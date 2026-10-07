"""One Momentum Radar scan, run by radar.worker as a subprocess (PORT_SPEC section 5, github-spec 4.8).

    python -m radar.tick --tick-id 2026-09-28T13:35:00Z [--warmup] [--final] [--ignore-calendar]
    python -m radar.tick --probe        fetch-only diagnostics; writes nothing (the stored pack, if any,
                                        picks the thin names for the finality check)

This is the GitHub version's tick with its storage swapped. It reads the previous snapshot, the engine
document and the session's baseline packs from Postgres through radar.store, and publishes a scan with
one RadarStore.commit_tick call (one transaction) instead of writing a delta for the loop to push. Every
github-spec section 12 rule is kept: the fetch deadline, the live source health (on_health ->
write_live_health), quotes_ok / degraded_volume, pack acceptance and gap retries, dynamic adds, the
no-cross-session-carry rule and the probe.

Its last stdout line is a JSON result, and the exit code is 0 unless there is a bug. The worker passes
its bookkeeping (ticks today, the next tick, recent DB write times, ...) in RADAR_TICK_CONTEXT and its run
id in RADAR_RUN_ID; a tick run by hand falls back to the bookkeeping stored by the previous tick and the
run id "local". Importing this module needs only the standard library: the fetch, baseline, engine and
store modules load when a tick runs, so the worker reuses the helpers below without loading numpy.

Two scan_log fields changed meaning with the storage (the row shape is unchanged):
- `ms.write` is the DB write (commit_tick) time of the worker's previous scan. A row is written by that
  commit, so it cannot hold its own duration; this is how git_prev carried the previous push on GitHub,
  and radar.housekeeping takes its write p95 from it. The result line's `ms.write` is this tick's own.
- `hot_bytes` holds the JSON bytes this scan wrote (member rows, events, state). Table sizes are measured
  by radar.housekeeping (pg_total_relation_size), not per tick.
`git_prev` stays NO_GIT and `health.published_late` stays 0: nothing is pushed late any more.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import json
import logging
import math
import os
import sys
import time
import traceback
import zlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from . import calendar_nyse as cal
from .config import PARAMS, REFERENCE_SYMBOLS, RUNTIME

if TYPE_CHECKING:
    from .types import FetchReport, Quote

logger = logging.getLogger(__name__)

DISCLAIMER = "Educational analysis of what is moving now, not a forecast and not financial advice."
HELD = "Stocks already on the radar are held, not dropped."
# The tick asks the worker for an extra housekeeping run when its DB writes get slow (storage.md 5: a
# p95 over 1.5 s across at least 10 recent writes). Sizes are housekeeping's own nightly measurement.
HK_WRITE_P95_MS = 1500
HK_MIN_SAMPLES = 10
PACK_MIN_COVERAGE = 0.8          # below this share of symbols with 5m (or daily) history, retry the pack next tick
PACK_GAP_RETRIES = 3             # later ticks that may refetch universe names the accepted pack lacks, per day
PACK_GAP_REASONS = ("no prior close", "no daily history")   # ineligible only because the daily fetch failed
PACK_NAMES = {"main": "baselines", "extra": "baselines_extra"}   # radar_baselines.kind -> the github-spec name
# The fetcher stops this long before the worker's tick timeout (SPEC 12.3). It must cover a request still
# in flight at the deadline (up to 2 x fetch_timeout_s on the crumb path) plus the engine step and the
# commit; test_tick pins that. The deadline counts from the start of main(), before the heavy imports,
# because the worker's timer starts at Popen.
DEADLINE_MARGIN_S = 40
CONTEXT_ENV = "RADAR_TICK_CONTEXT"   # the worker's bookkeeping for this tick: {"loop": {...}, "ops": {...}}
RUN_ID_ENV = "RADAR_RUN_ID"          # the worker's run id, the scan_log run_id of its ticks
HEALTH_REF = "tick_ref"              # stamps the live source health with "<run id>/<tick id>" (see health_ref)
BARS_DEGRADED_SHARE = 0.2
MS_KEYS = ("fetch_quotes", "fetch_movers", "fetch_bars", "baselines", "compute", "write", "total")
HOT_KEYS = ("member_ticks", "events", "scan_log", "state")
SOURCE_STATUSES = ("ok", "degraded", "down")
DEFAULT_MARKET = {"mode": "normal", "dir": None, "spy_chg_day_pct": 0.0, "spy_z30": 0.0, "breadth30": 0.0}
NO_GIT = {"stage": 0, "commit": 0, "push": 0, "attempts": 0, "status": "none"}
EMPTY_COUNTS = {"universe": 0, "stage_b": 0, "members": 0, "heating": 0, "entered_today": 0, "exited_today": 0}


class PackUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers (also used by radar.worker)

def iso(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_tick_id(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def run_id() -> str:
    """The scan_log run id: the worker's (it sets RADAR_RUN_ID for its ticks), else "local"."""
    return os.environ.get(RUN_ID_ENV) or "local"


def health_ref(run: str, tick_id: str) -> str:
    """The stamp on a live source health. A tick killed before its first fetch call leaves the previous
    tick's copy in place; the worker merges only a copy stamped with the tick that failed (SPEC 12.6)."""
    return f"{run}/{tick_id}"


def read_context(environ: Mapping[str, str] = os.environ) -> dict | None:
    """The worker's bookkeeping for this tick, or None when the tick runs by hand (or it is unreadable)."""
    raw = environ.get(CONTEXT_ENV)
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        logger.warning("%s is not JSON; using the stored loop bookkeeping", CONTEXT_ENV)
        return None
    return doc if isinstance(doc, dict) else None


def gunzip_json(blob: bytes | None) -> dict | None:
    if not blob:
        return None
    try:
        doc = json.loads(gzip.decompress(blob))
    except (OSError, EOFError, ValueError, zlib.error):
        return None
    return doc if isinstance(doc, dict) else None


def gz_json(doc: dict) -> bytes:
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return gzip.compress(raw.encode("utf-8"), compresslevel=6, mtime=0)


def json_bytes(doc: dict) -> bytes:
    return (json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def jsonl_bytes(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


def jsonable(obj: Any, bad: list[int]) -> Any:
    """Plain JSON types; numpy values become Python values and non-finite floats become null."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        if math.isfinite(obj):
            return float(obj)
        bad[0] += 1
        return None
    if isinstance(obj, dict):
        return {str(k): jsonable(v, bad) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [jsonable(v, bad) for v in obj]
    if hasattr(obj, "tolist"):
        return jsonable(obj.tolist(), bad)
    raise TypeError(f"not JSON-serialisable: {type(obj).__name__}")


def scan_log_row(tick_id: str, written_at: str, **fields: Any) -> dict:
    """One scan_log row (github-spec section 6) with zero defaults for what a run did not measure."""
    row = {"v": 1, "tick": tick_id, "run_id": run_id(), "written_at": written_at, "session": None, "phase": "",
           "status": "error", "lag_s": 0, "universe": 0, "stage_b": 0, "quotes_ok": 0, "quotes_err": 0,
           "bars_ok": 0, "bars_err": 0, "members": 0, "heating": 0, "entered": [], "exited": [],
           "processed_slots": [], "source": "yahoo", "ms": dict.fromkeys(MS_KEYS, 0), "git_prev": dict(NO_GIT),
           "hot_bytes": dict.fromkeys(HOT_KEYS, 0), "errors": []}
    unknown = fields.keys() - row.keys()
    if unknown:
        raise KeyError(f"not scan_log fields: {sorted(unknown)}")
    row.update(fields)
    row["errors"] = [str(e)[:200] for e in row["errors"][:5]]
    return row


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def hk_request(write_ms: list[float]) -> str | None:
    """Why the worker should run housekeeping now (DB writes got slow), or None."""
    if len(write_ms) >= HK_MIN_SAMPLES and p95(write_ms) > HK_WRITE_P95_MS:
        return f"DB write p95 {p95(write_ms):.0f} ms over {HK_WRITE_P95_MS} ms"
    return None


def source_block(h: Any) -> dict:
    """state.json "source" from the fetcher's health record (the engine document's source_health)."""
    h = h if isinstance(h, dict) else {}
    last_ok = h.get("last_ok_at")
    try:
        failures = int(h.get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        failures = 0
    return {"name": h["name"] if h.get("name") in ("yahoo", "nasdaq") else "yahoo",
            "status": h["status"] if h.get("status") in SOURCE_STATUSES else "ok",
            "consecutive_failures": failures, "last_ok_at": last_ok if isinstance(last_ok, str) else None}


def session_json(s: cal.Session, phase: str) -> dict:
    return {"date": s.day.isoformat(), "phase": phase, "open": iso(s.open_epoch), "close": iso(s.close_epoch),
            "half_day": s.early_close}


def same_session_scan(state: Any, session_date: str | None) -> bool:
    """Whether a previous state.json is a scan of session `session_date`: same session.date, phase regular or
    post (the final tick runs after the close) and status not closed. Only then may an in-session state carry
    its recent_exits, day counts and last_bar (SPEC 12.6). The pre-open heartbeat is dated today but still
    shows the previous session's exits."""
    state = state if isinstance(state, dict) else {}
    s = state.get("session") if isinstance(state.get("session"), dict) else {}
    return (session_date is not None and s.get("date") == session_date and s.get("phase") in ("regular", "post")
            and state.get("status") != "closed")


def first_tick_et(s: cal.Session) -> str:
    return (s.open + timedelta(minutes=5)).astimezone(cal.ET).strftime("%H:%M")


def resolve_session(tick_epoch: int, now_epoch: float, *, ignore_calendar: bool, final: bool) -> tuple[cal.Session | None, bool]:
    """(session, run_engine). The engine runs for boundaries in (open, close]; manual runs replay the
    current session once it has opened, otherwise the previous one."""
    day = datetime.fromtimestamp(tick_epoch, cal.UTC).astimezone(cal.ET).date()
    s = cal.session_for(day)
    if s and s.open_epoch < tick_epoch <= s.close_epoch:
        return s, True
    if (ignore_calendar or final) and s and now_epoch >= s.open_epoch:
        return s, True
    if ignore_calendar:
        return cal.previous_sessions(day, 1)[0], True
    return s, False


# ---------------------------------------------------------------- dependencies (real ones are imported lazily)

@dataclass
class Deps:
    fetcher: Callable[..., Any]
    engine: Callable[..., Any]
    build_pack: Callable[..., Any]
    extend_pack: Callable[..., Any]
    pack_from_json: Callable[[dict], Any]
    load_universe: Callable[[], list[dict]]
    scan_symbols: Callable[[], list[str]]
    dynamic_candidates: Callable[..., list]


def real_deps() -> Deps:
    from . import baselines, engine, fetch, universe
    return Deps(fetch.Fetcher, engine.Engine, baselines.build_pack, baselines.extend_pack,
                baselines.BaselinePack.from_json, universe.load_universe, universe.scan_symbols,
                universe.dynamic_candidates)


def open_store() -> Any:
    """The production store (Postgres from settings.DATABASE_URL); a seam for the CLI tests."""
    from .store import RadarStore
    return RadarStore()


@dataclass
class TickArgs:
    tick_id: str
    warmup: bool = False
    final: bool = False
    ignore_calendar: bool = False


# ---------------------------------------------------------------- the tick

class Tick:
    """One scan. `store` is a radar.store.RadarStore (or anything with its pinned API); `context` is the
    worker's {"loop": ..., "ops": ...} bookkeeping for this tick (None when run by hand)."""

    def __init__(self, args: TickArgs, deps: Deps, store: Any, *, clock: Callable[[], float] = time.time,
                 started: float | None = None, context: dict | None = None):
        self.a, self.deps, self.store, self.clock = args, deps, store, clock
        self.tick_epoch = parse_tick_id(args.tick_id)
        # main() passes the time it started (on `clock`), before real_deps() imported numpy and the HTTP stack.
        self.started = clock() if started is None else started
        # The fetcher stops starting requests here, so a slow or blocked source still lets the tick reach
        # _finish (and save the breaker progress) before the worker's timeout kills it.
        self.deadline = self.started + RUNTIME["tick_timeout_s"] - DEADLINE_MARGIN_S
        self.t0 = time.perf_counter()
        self.ms = dict.fromkeys(MS_KEYS, 0)
        self.errors: list[str] = []
        self.engine_doc = store.load_engine_doc() or {}
        self.prev_state = store.load_state() or {}
        ctx = context if isinstance(context, dict) else {}
        self.loop = ctx["loop"] if isinstance(ctx.get("loop"), dict) else (self.engine_doc.get("loop") or {})
        stored_ops = self.engine_doc.get("ops")
        self.ops = {**(stored_ops if isinstance(stored_ops, dict) else {"hk_dispatched_at": None}),
                    **(ctx["ops"] if isinstance(ctx.get("ops"), dict) else {})}
        self.pack_gaps = self.engine_doc.get("pack_gaps")
        self.packs: dict[str, tuple[str, str, bytes]] = {}   # {kind: (session_date, params_version, gz)} to store
        self._fetcher: Any = None
        self._pack_built = False                  # the main pack was built by this tick
        self._extra: set[str] = set()             # symbols that live in the extra pack (dynamic adds, gap fills)
        self._extra_dirty = False
        self._live_health_ok = True

    @contextmanager
    def timed(self, key: str) -> Iterator[None]:
        t = time.perf_counter()
        try:
            yield
        finally:
            self.ms[key] += int((time.perf_counter() - t) * 1000)

    def run(self) -> dict:
        session, run_engine = resolve_session(self.tick_epoch, self.clock(), ignore_calendar=self.a.ignore_calendar,
                                              final=self.a.final)
        if self.a.warmup and session is not None:
            return self._warmup(session)
        if run_engine:
            return self._scan(session)
        return self._heartbeat()

    # -- data access

    def fetcher(self) -> Any:
        if self._fetcher is None:
            self._fetcher = self.deps.fetcher(health=self.engine_doc.get("source_health"),
                                              workers=RUNTIME["fetch_workers"], timeout_s=RUNTIME["fetch_timeout_s"],
                                              deadline=self.deadline, on_health=self._save_health)
        return self._fetcher

    def _save_health(self, health: dict) -> None:
        """Called by the fetcher after every call: if the worker kills this tick, it merges this live copy
        into the engine document, so the breaker still advances (SPEC 12.3). One failed write stops the
        others: with the database unreachable each would wait for the connect timeout."""
        if not self._live_health_ok:
            return
        try:
            self.store.write_live_health({**health, HEALTH_REF: health_ref(run_id(), self.a.tick_id)})
        except Exception as e:  # noqa: BLE001 - losing the live copy only weakens the timeout bookkeeping
            self._live_health_ok = False
            self.errors.append(f"source health: {type(e).__name__}: {e}")

    def _fetch(self, what: str, fn: Callable[..., tuple[Any, FetchReport]], symbols: list[str] | None,
               **kw: Any) -> tuple[Any, FetchReport]:
        """The data sources are unofficial and flaky: any failure becomes an empty result so the
        engine holds its members instead of the tick dying."""
        try:
            return fn(symbols, **kw) if symbols is not None else fn(**kw)
        except Exception as e:  # noqa: BLE001 - network boundary, reported in scan_log
            from .types import FetchReport
            self.errors.append(f"{what}: {type(e).__name__}: {e}")
            return ([] if symbols is None else {}), FetchReport(
                source="none", status="down", requested=len(symbols or ()), failed=list(symbols or ()),
                notes=[type(e).__name__])

    def _load_pack(self, kind: str, session_date: str) -> Any:
        """The session's stored pack, or None when it is missing, from other params or unreadable (it is
        then rebuilt)."""
        version, blob = self.store.load_pack(session_date, kind)
        doc = gunzip_json(blob)
        if doc is None or version != PARAMS["params_version"] or doc.get("asof") != session_date \
                or doc.get("params_version") != PARAMS["params_version"]:
            return None
        try:
            return self.deps.pack_from_json(doc)
        except (KeyError, TypeError, ValueError) as e:
            self.errors.append(f"{PACK_NAMES[kind]}: unreadable, rebuilding ({type(e).__name__}: {e})")
            return None

    def _universe_meta(self) -> dict[str, dict]:
        return {r["symbol"]: {"name": r.get("name"), "sector": r.get("sector") or "Unknown"}
                for r in self.deps.load_universe()}

    def _build_pack(self, session: cal.Session) -> Any:
        """Today's pack, accepted only when SPY has 5m and daily bars and both cover PACK_MIN_COVERAGE of
        the symbols (SPEC 12.3). Otherwise nothing is stored and the next tick retries."""
        symbols = self.deps.scan_symbols()
        bars5, r5 = self._fetch("baseline bars", self.fetcher().bars_5m, symbols, range_="1mo")
        daily, rd = self._fetch("baseline daily", self.fetcher().daily, symbols, range_="3mo")
        cov5 = sum(1 for s in symbols if s in bars5) / max(1, len(symbols))
        covd = sum(1 for s in symbols if s in daily) / max(1, len(symbols))
        missing_spy = [what for what, got in (("5-minute", bars5), ("daily", daily)) if "SPY" not in got]
        if missing_spy or cov5 < PACK_MIN_COVERAGE or covd < PACK_MIN_COVERAGE:
            spy = f"no SPY {' or '.join(missing_spy)} bars; " if missing_spy else ""
            raise PackUnavailable(f"{spy}5-minute history for {cov5:.0%} and daily history for {covd:.0%} of "
                                  f"symbols ({r5.status}/{rd.status})")
        try:
            return self.deps.build_pack(session, bars5, daily, self._universe_meta(), PARAMS)
        except ValueError as e:                     # e.g. no SPY bar of a base session, or no SPY prior close
            raise PackUnavailable(str(e)) from e

    def _pack_for(self, session: cal.Session) -> Any:
        date = session.day.isoformat()
        with self.timed("baselines"):
            pack = self._load_pack("main", date)
            if pack is None:
                pack = self._build_pack(session)
                self._pack_built = True
                self.packs["main"] = (date, PARAMS["params_version"], gz_json(pack.to_json()))
            extra = self._load_pack("extra", date)
        if extra is not None:
            self._extra = set(extra.symbols)
            pack = dataclasses.replace(pack, symbols={**pack.symbols, **extra.symbols})
        return pack

    def _extend(self, what: str, pack: Any, session: cal.Session, syms: list[str],
                meta: Callable[[list[str]], dict[str, dict]], *, replace_existing: bool = False) -> tuple[Any, list[str]]:
        """Fetch 1mo 5m and 3mo daily bars for syms and add the ones that have both to the pack, as
        extra-pack symbols. Returns (pack, symbols added). Nothing is fetched while the chart breaker is
        open (SPEC 12.6): these ranges have no Nasdaq fallback, so the calls would go to Yahoo as unscheduled
        probes. The callers retry on a later tick."""
        if self._chart_breaker_open():
            self.errors.append(f"{what}: {', '.join(syms)} deferred while the chart breaker is open")
            return pack, []
        with self.timed("baselines"):
            bars5, _ = self._fetch(f"{what} bars", self.fetcher().bars_5m, syms, range_="1mo")
            daily, _ = self._fetch(f"{what} daily", self.fetcher().daily, syms, range_="3mo")
            ready = [s for s in syms if s in bars5 and s in daily]      # a name without daily would be ineligible
            if not ready:
                return pack, []
            base = dataclasses.replace(pack, symbols={s: b for s, b in pack.symbols.items() if s not in ready}) \
                if replace_existing else pack
            try:
                out = self.deps.extend_pack(base, session, {s: bars5[s] for s in ready}, {s: daily[s] for s in ready},
                                            meta(ready), PARAMS)
            except ValueError as e:                 # auxiliary: the scan goes on without them
                self.errors.append(f"{what}: {e}")
                return pack, []
        added = [s for s in ready if s in out.symbols]
        self._extra.update(added)
        self._extra_dirty |= bool(added)
        return out, added

    def _reextend_dynamic(self, pack: Any, session: cal.Session, dyn: list[str], quotes: dict[str, Quote]) -> Any:
        """Dynamic adds whose extra pack was missing or rejected (e.g. a params bump rebuilt the packs
        mid-session) get their baselines again before the engine is built (SPEC 12.3)."""
        missing = [s for s in dyn if s not in pack.symbols]
        if not missing:
            return pack

        def meta(syms: list[str]) -> dict[str, dict]:
            return {s: {"name": getattr(quotes.get(s), "name", None),
                        "sector": getattr(quotes.get(s), "sector", None) or "Unknown"} for s in syms}

        pack, added = self._extend("dynamic adds", pack, session, missing, meta)
        still = [s for s in missing if s not in added]
        if still:
            self.errors.append(f"dynamic adds: no baselines yet for {', '.join(still)}; retrying next scan")
        return pack

    def _pack_gaps(self, pack: Any) -> list[str]:
        """Universe names the pack lacks, or holds as ineligible only because their daily bars were missing."""
        return [s for s in self.deps.scan_symbols()
                if s not in pack.symbols or pack.symbols[s].ineligible_reason in PACK_GAP_REASONS]

    def _source_ok(self) -> bool:
        h = self._source_health()
        return isinstance(h, dict) and h.get("status") == "ok" and h.get("name", "yahoo") == "yahoo"

    def _chart_breaker_open(self) -> bool:
        """The chart family's breaker, read as fetch.Fetcher restores it (a flat record covers both families)."""
        h = self._source_health()
        h = h if isinstance(h, dict) else {}
        fams = h.get("families")
        rec = fams.get("chart") if isinstance(fams, dict) else h
        breaker = rec.get("breaker") if isinstance(rec, dict) else None
        return isinstance(breaker, dict) and breaker.get("open") is True

    def _fill_gaps(self, pack: Any, session: cal.Session) -> Any:
        """Retry the pack's gaps on later ticks, at most PACK_GAP_RETRIES times a day and only while the
        source is ok and the chart breaker closed; the names go to the extra pack without using the dynamic
        budget. Names still missing are listed in scan_log errors."""
        gaps = self._pack_gaps(pack)
        date = session.day.isoformat()
        pg = self.pack_gaps if isinstance(self.pack_gaps, dict) and self.pack_gaps.get("session") == date else {}
        retries = int(pg.get("retries") or 0)
        if gaps and not self._pack_built and retries < PACK_GAP_RETRIES and self._source_ok() \
                and not self._chart_breaker_open():
            retries += 1
            meta = self._universe_meta()
            pack, _ = self._extend("pack gaps", pack, session, gaps, lambda syms: {s: meta.get(s, {}) for s in syms},
                                   replace_existing=True)
            gaps = self._pack_gaps(pack)
        self.pack_gaps = {"session": date, "retries": retries}
        self._note_gaps(gaps)
        return pack

    def _note_gaps(self, gaps: list[str]) -> None:
        if gaps:
            self.errors.append(f"pack gaps: {len(gaps)} universe name(s) without usable baselines: {', '.join(gaps)}")

    def _store_extra(self, pack: Any, session: cal.Session) -> None:
        if self._extra_dirty:
            extra = dataclasses.replace(pack, symbols={s: pack.symbols[s] for s in sorted(self._extra) if s in pack.symbols})
            self.packs["extra"] = (session.day.isoformat(), PARAMS["params_version"], gz_json(extra.to_json()))

    def _dynamic_adds(self, pack: Any, session: cal.Session, dyn: list[str], known: set[str],
                      quotes: dict[str, Quote], movers: list[Quote]) -> Any:
        """Movers outside the universe get baselines on first sight and stay for the session. They need
        5m and daily bars; their sector comes from one batched quote call (screens carry none)."""
        cfg = PARAMS["dynamic"]
        room = cfg["max_adds_per_day"] - len(dyn)
        if not cfg["enabled"] or room <= 0 or not movers:
            return pack
        cands = {q.symbol: q for q in self.deps.dynamic_candidates(movers, known, PARAMS)[:room]}
        if not cands:
            return pack
        profiles: dict[str, Quote] = {}

        def meta(syms: list[str]) -> dict[str, dict]:
            got, _ = self._fetch("dynamic profiles", self.fetcher().quotes, syms)
            profiles.update(got)
            return {s: {"name": getattr(got.get(s), "name", None) or cands[s].name,
                        "sector": getattr(got.get(s), "sector", None) or cands[s].sector or "Unknown"} for s in syms}

        pack, added = self._extend("dynamic adds", pack, session, list(cands), meta)
        dyn.extend(added)
        quotes.update({s: profiles.get(s) or cands[s] for s in added})
        return pack

    # -- the three kinds of run

    def _scan(self, session: cal.Session) -> dict:
        date = session.day.isoformat()
        dyn = list(self.engine_doc.get("dynamic_adds") or []) if self.engine_doc.get("session") == date else []
        try:
            pack = self._pack_for(session)
        except PackUnavailable as e:
            self.errors.append(f"baselines: {e}")
            return self._carry(session, dyn, "no_data",
                               f"Today's baselines could not be built yet; retrying next scan. {HELD}")
        symbols = self.deps.scan_symbols()
        universe = list(dict.fromkeys(symbols + dyn))
        fetcher = self.fetcher()
        with self.timed("fetch_quotes"):
            quotes, qrep = self._fetch("quotes", fetcher.quotes, universe)
        with self.timed("fetch_movers"):
            movers, mrep = self._fetch("movers", fetcher.movers, None)
        if mrep.status != "ok":
            self.errors.append(f"movers: {mrep.status}")
        pack = self._reextend_dynamic(pack, session, dyn, quotes)
        pack = self._fill_gaps(pack, session)
        pack = self._dynamic_adds(pack, session, dyn, set(universe), quotes, movers)
        self._store_extra(pack, session)
        universe = list(dict.fromkeys(universe + dyn))

        now = int(self.clock())                       # before the bar fetch: bars are at least this fresh
        with self.timed("compute"):
            engine = self.deps.engine(PARAMS, pack, session, state=self.engine_doc.get("engine"))
            # A failed quote call must not widen Stage B to the whole universe (SPEC 12.3).
            stage_b = list(engine.stage_a(quotes, now, quotes_ok=qrep.status != "down"))
        with self.timed("fetch_bars"):
            bars, brep = self._fetch("bars", fetcher.bars_5m, stage_b, range_="1d")
        with self.timed("compute"):
            # Nasdaq fallback volume is not on the Yahoo basis of the baselines (SPEC 12.3, DATA-6).
            out = engine.step(bars, now, degraded_volume=brep.source == "nasdaq")
            engine_state = engine.state_dict()

        status, message = self._scan_status(qrep, brep, bars)
        snap = out.snapshot
        events, rows = list(out.events), list(out.member_rows)
        processed = [int(j) for j in out.processed_slots]
        members, heating = snap.get("members") or [], snap.get("heating") or []
        counts = {**EMPTY_COUNTS, "universe": len(universe), "stage_b": len(stage_b), "members": len(members),
                  "heating": len(heating), **(snap.get("counts") or {})}
        last_bar = iso(session.slot_start(max(processed)) + 300) if processed else self._prev_same(date, "last_bar")
        source = self._source()
        state = self._state(session_json(session, cal.phase_at(self._now_dt())[0]), status, message, source,
                            snap, counts, last_bar)
        engine_doc = self._engine_doc(date, engine_state, dyn)
        return self._finish(
            session_date=date, phase=state["session"]["phase"], status=status, message=message,
            state=state, engine_doc=engine_doc, member_rows=rows, events=events,
            scan={"universe": len(universe), "stage_b": len(stage_b), "quotes_ok": qrep.ok,
                  "quotes_err": len(qrep.failed), "bars_ok": brep.ok, "bars_err": len(brep.failed),
                  "entered": [e["ticker"] for e in events if e.get("type") == "ENTER"],
                  "exited": [e["ticker"] for e in events if e.get("type") == "EXIT"],
                  "processed_slots": processed})

    def _warmup(self, session: cal.Session) -> dict:
        date = session.day.isoformat()
        status, message = "ok", f"Baselines ready; the radar starts at {first_tick_et(session)} ET."
        try:
            self._note_gaps(self._pack_gaps(self._pack_for(session)))
        except PackUnavailable as e:
            self.errors.append(f"baselines: {e}")
            status, message = "no_data", "Baselines could not be built; the first scan will retry."
        dyn = list(self.engine_doc.get("dynamic_adds") or []) if self.engine_doc.get("session") == date else []
        state = self._closed_state()
        engine_doc = self._engine_doc(date, self.engine_doc.get("engine"), dyn)
        return self._finish(session_date=date, phase=state["session"]["phase"], status=status,
                            message=message, state=state, engine_doc=engine_doc)

    def _heartbeat(self) -> dict:
        state = self._closed_state()
        engine_doc = self._engine_doc(self.engine_doc.get("session"), self.engine_doc.get("engine"),
                                      list(self.engine_doc.get("dynamic_adds") or []))
        return self._finish(session_date=None, phase=state["session"]["phase"], status="closed",
                            message=state["message"], state=state, engine_doc=engine_doc)

    def _carry(self, session: cal.Session, dyn: list[str], status: str, message: str) -> dict:
        """No engine step this tick: republish the previous snapshot if it is a scan of this session, else
        start empty (SPEC 12.6)."""
        date = session.day.isoformat()
        same = same_session_scan(self.prev_state, date)
        snap = {k: self.prev_state.get(k) for k in ("members", "heating", "recent_exits", "sector_banners", "market")} if same else {}
        counts = {**EMPTY_COUNTS, **(self.prev_state.get("counts") or {})} if same else dict(EMPTY_COUNTS)
        source = self._source()
        state = self._state(session_json(session, cal.phase_at(self._now_dt())[0]), status, message, source, snap,
                            counts, self._prev_same(date, "last_bar"))
        engine_doc = self._engine_doc(date, self.engine_doc.get("engine"), dyn)
        return self._finish(session_date=date, phase=state["session"]["phase"], status=status,
                            message=message, state=state, engine_doc=engine_doc)

    # -- composition

    def _now_dt(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), cal.UTC)

    def _prev_same(self, date: str, key: str) -> Any:
        return self.prev_state.get(key) if same_session_scan(self.prev_state, date) else None

    def _scan_status(self, qrep: FetchReport, brep: FetchReport, bars: dict) -> tuple[str, str]:
        if not bars:
            return "no_data", f"No fresh market data in this scan. {HELD}"
        backup = "nasdaq" in (qrep.source, brep.source)
        thin = brep.requested and len(brep.failed) > BARS_DEGRADED_SHARE * brep.requested
        if backup or thin or qrep.status != "ok" or brep.status != "ok":
            lead = "Using the backup data source (Nasdaq)." if backup else \
                f"Data came back incomplete ({brep.ok} of {brep.requested} charts)."
            return "degraded", f"{lead} {HELD}"
        return "ok", ""

    def _source_health(self) -> Any:
        return self._fetcher.health_state() if self._fetcher is not None else self.engine_doc.get("source_health")

    def _source(self) -> dict:
        """state.json "source" is the fetcher's own health (breaker, failures in a row), carried
        between ticks in the engine document; a tick that fetched nothing repeats the stored value."""
        return source_block(self._source_health())

    def _next_tick_at(self) -> str | None:
        nxt = self.loop.get("next_tick_at")
        try:
            return nxt if nxt and not self.a.final and parse_tick_id(nxt) > self.clock() else None
        except (TypeError, ValueError):
            return None

    def _health(self) -> dict:
        # published_late counted scans delivered after failed git pushes; a commit here is never late.
        return {"ticks_today": int(self.loop.get("ticks_today", 0)), "ticks_skipped": int(self.loop.get("ticks_skipped", 0)),
                "last_tick_ms": 0, "loop_run_id": self.loop.get("run_id") or "",
                "loop_started_at": self.loop.get("started_at"), "published_late": 0}

    def _state(self, session: dict, status: str, message: str, source: dict, snap: dict, counts: dict,
               last_bar: str | None) -> dict:
        return {"schema": 1, "generated_at": iso(self.clock()), "tick_id": self.a.tick_id, "last_bar": last_bar,
                "status": status, "message": message, "session": session, "next_tick_at": self._next_tick_at(),
                "params_version": PARAMS["params_version"], "source": source,
                "market": snap.get("market") or dict(DEFAULT_MARKET), "counts": counts,
                "members": snap.get("members") or [], "heating": snap.get("heating") or [],
                "recent_exits": snap.get("recent_exits") or [], "sector_banners": snap.get("sector_banners") or [],
                "health": self._health(), "disclaimer": DISCLAIMER}

    def _closed_state(self) -> dict:
        """Heartbeat outside the regular session: no members, and the latest session's exits are kept
        until the next session starts."""
        dt = datetime.fromtimestamp(self.tick_epoch, cal.UTC)
        phase, today = cal.phase_at(dt)
        day = dt.astimezone(cal.ET).date()
        pre = today is not None and dt < today.open
        upcoming = cal.next_sessions(day + timedelta(days=1), 1)[0]
        show = today if today is not None and dt < today.post_close else upcoming
        latest = today if today is not None and dt >= today.open else cal.previous_sessions(day, 1)[0]
        prev_session = self.prev_state.get("session") or {}
        # A heartbeat already labelled with the session it waits for carries `latest`'s exits (SPEC 6).
        keep = prev_session.get("date") == latest.day.isoformat() or (
            self.prev_state.get("status") == "closed" and prev_session.get("date") == show.day.isoformat())
        prev_counts = self.prev_state.get("counts") or {}
        counts = {**EMPTY_COUNTS, **({k: prev_counts.get(k, 0) for k in ("entered_today", "exited_today")} if keep else {})}
        if pre:
            message = f"The radar starts at {first_tick_et(today)} ET."
        else:
            message = f"Market closed. The radar starts again {upcoming.open.astimezone(cal.ET):%a} at {first_tick_et(upcoming)} ET."
        snap = {"recent_exits": self.prev_state.get("recent_exits") if keep else []}
        return self._state(session_json(show, phase), "closed", message, self._source(), snap, counts,
                           self.prev_state.get("last_bar") if keep else None)

    def _engine_doc(self, session_date: str | None, engine_state: Any, dyn: list[str]) -> dict:
        return {"schema": 1, "session": session_date, "engine": engine_state, "source_health": self._source_health(),
                "dynamic_adds": dyn, "pack_gaps": self.pack_gaps, "ops": dict(self.ops), "loop": self.loop}

    def _finish(self, *, session_date: str | None, phase: str, status: str, message: str, state: dict,
                engine_doc: dict, member_rows: list[dict] | None = None, events: list[dict] | None = None,
                scan: dict | None = None) -> dict:
        """Publish the scan in one transaction: snapshot, engine document, rows, scan_log row and any pack
        this tick built (the store also clears the live source health, which the engine document now holds)."""
        t_write = time.perf_counter()
        bad = [0]
        rows = jsonable(list(member_rows or []), bad)
        evs = jsonable(list(events or []), bad)
        engine_doc = jsonable(engine_doc, bad)
        state["health"]["last_tick_ms"] = int((time.perf_counter() - self.t0) * 1000)
        state = jsonable(state, bad)
        if bad[0]:
            self.errors.append(f"replaced {bad[0]} non-finite value(s) with null")
        hot = {"member_ticks": len(jsonl_bytes(rows)), "events": len(jsonl_bytes(evs)), "scan_log": 0,
               "state": len(json_bytes(state))}
        write_ms = [float(v) for v in self.loop.get("write_ms") or [] if isinstance(v, (int, float))]
        request = hk_request(write_ms)
        engine_doc["ops"]["hk_request"] = {"reason": request, "at": iso(self.clock())} if request else None
        prep_ms = int((time.perf_counter() - t_write) * 1000)
        self.ms["total"] = int((time.perf_counter() - self.t0) * 1000)
        row = scan_log_row(self.a.tick_id, iso(self.clock()), session=session_date, phase=phase, status=status,
                           lag_s=max(0, int(self.started - self.tick_epoch)), members=len(state["members"]),
                           heating=len(state["heating"]), source=state["source"]["name"],
                           ms={**self.ms, "write": int(write_ms[-1]) if write_ms else 0}, git_prev=dict(NO_GIT),
                           hot_bytes=hot, errors=self.errors, **(scan or {}))
        t_commit = time.perf_counter()
        self.store.commit_tick(state=state, engine_doc=engine_doc, member_rows=rows, events=evs, scan_row=row,
                               packs=dict(self.packs) or None)
        self.ms["write"] = prep_ms + int((time.perf_counter() - t_commit) * 1000)
        self.ms["total"] = int((time.perf_counter() - self.t0) * 1000)
        return {"status": status, "message": message, "members": row["members"], "entered": row["entered"],
                "exited": row["exited"], "processed_slots": row["processed_slots"], "source": state["source"],
                "ms": dict(self.ms), "hot_bytes": hot, "hk_request": request}


# ---------------------------------------------------------------- probe (fetch-only diagnostics)

PROBE_FINALITY_SYMBOLS = ("SPY", "QQQ", "AAPL")
# Thin but eligible S&P names whose newest bar was seen to change after +50 s (DATA-4); used when no
# stored pack ranks the universe by median 5-minute dollar volume.
PROBE_THIN_FALLBACK = ("AIZ", "ALLE", "PNW", "GL", "DVA", "HII", "TPL", "NI", "ERIE", "FRT")
PROBE_THIN_COUNT = 10
PROBE_FINALITY_OFFSETS = (15, 30, 45, 60, 90, 120, 180, 240, 300)


def stored_main_pack(store: Any, now_epoch: float) -> dict | None:
    """The newest stored universe pack: today's session, else the previous one (the probe often runs
    before the warmup or on a weekend). None without a store or when the database cannot be read."""
    if store is None:
        return None
    day = datetime.fromtimestamp(now_epoch, cal.UTC).astimezone(cal.ET).date()
    today = cal.session_for(day)
    for s in ([today] if today is not None else []) + cal.previous_sessions(day, 1):
        try:
            _, blob = store.load_pack(s.day.isoformat(), "main")
        except Exception as e:  # noqa: BLE001 - a diagnostic run goes on with the fixed list
            logger.warning("probe: stored pack unreadable (%s: %s)", type(e).__name__, e)
            return None
        doc = gunzip_json(blob)
        if doc is not None:
            return doc
    return None


def thin_names(deps: Deps, store: Any, now_epoch: float, n: int = PROBE_THIN_COUNT) -> tuple[list[str], str]:
    """(the n eligible names with the lowest medbar_usd in the stored pack, where they came from);
    the fixed thin list when there is no readable pack."""
    doc = stored_main_pack(store, now_epoch)
    if doc is not None:
        try:
            pack = deps.pack_from_json(doc)
            ranked = sorted((float(b.medbar_usd), s) for s, b in pack.symbols.items()
                            if b.eligible and s not in PROBE_FINALITY_SYMBOLS and math.isfinite(float(b.medbar_usd)))
        except (AttributeError, KeyError, TypeError, ValueError):
            ranked = []
        if ranked:
            return [s for _, s in ranked[:n]], f"stored pack of {doc.get('asof')}, lowest median 5-minute dollar volume"
    return list(PROBE_THIN_FALLBACK[:n]), "fixed thin list (no stored pack)"


def _bar_at(bars: Any, ts: int) -> tuple | None:
    if bars is None:
        return None
    stamps = bars.ts.tolist()
    if ts not in stamps:
        return None
    i = stamps.index(ts)
    return (float(bars.o[i]), float(bars.h[i]), float(bars.l[i]), float(bars.c[i]), int(bars.v[i]))


def _provisional_at(bars: Any, ts: int) -> tuple[bool, bool]:
    """(the bar starting at ts was provisional, its next row exists). Provisional is the fetcher's own flag
    (fetch._parse_bars, SPEC 12.1) on the newest row: the last trade is still inside it, so Yahoo has not
    closed it yet. The engine holds decisions on such a bar (SPEC 12.2)."""
    if bars is None or not len(bars.ts):
        return False, False
    stamps = bars.ts.tolist()
    return stamps[-1] == ts and getattr(bars, "provisional_last", False) is True, ts + 300 in stamps


def bar_finality(fetcher: Any, clock: Callable[[], float], sleep: Callable[[float], None],
                 symbols: tuple[str, ...] = PROBE_FINALITY_SYMBOLS,
                 offsets: tuple[int, ...] = PROBE_FINALITY_OFFSETS) -> dict:
    """Refetch the bar that closes at the next boundary at several offsets and report, per name, when it
    stopped changing, at which offsets it was provisional (the fetcher's rule), after which offsets it still
    changed, and when the next row appeared. A change after an offset where the bar was not provisional is a
    miss of the rule (`changed_unflagged`). Only meaningful during the regular session."""
    now = clock()
    phase, s = cal.phase_at(datetime.fromtimestamp(now, cal.UTC))
    boundary = (int(now) // 300 + 1) * 300
    if phase != "regular" or s is None or boundary > s.close_epoch:
        return {"measured": False, "note": "market closed: bar finality needs a live regular session"}
    start = boundary - 300
    samples = []
    for off in offsets:
        sleep(max(0.0, boundary + off - clock()))
        bars, rep = fetcher.bars_5m(list(symbols), range_="1d")
        samples.append({sym: (_bar_at(bars.get(sym), start), *_provisional_at(bars.get(sym), start))
                        for sym in symbols})
    result = {}
    for sym in symbols:
        final = samples[-1][sym][0]
        settled = None
        for off, snap in reversed(list(zip(offsets, samples))):
            if final is None or snap[sym][0] != final:
                break
            settled = off
        seen = [(off, sn[sym][0], sn[sym][1]) for off, sn in zip(offsets, samples) if sn[sym][0]]
        result[sym] = {"first_seen_s": seen[0][0] if seen else None,
                       "stable_from_s": settled, "final_close": final[3] if final else None,
                       "final_volume": final[4] if final else None,
                       "provisional_s": [off for off, _, prov in seen if prov],
                       "changed_after_s": [off for i, (off, bar, _) in enumerate(seen)
                                           if any(later != bar for _, later, _ in seen[i + 1:])],
                       "next_row_s": next((o for o, sn in zip(offsets, samples) if sn[sym][2]), None)}
    late = sorted(sym for sym, v in result.items()
                  if v["stable_from_s"] is None or v["stable_from_s"] > RUNTIME["tick_offset_s"])
    missed = sorted(sym for sym, v in result.items() if set(v["changed_after_s"]) - set(v["provisional_s"]))
    return {"measured": True, "bar_start": iso(start), "offsets_s": list(offsets), "symbols": result,
            "unstable_at_tick": late, "changed_unflagged": missed}


def _offsets(values: list[int]) -> str:
    return ", ".join(map(str, values)) or "none"


def probe(deps: Deps, *, store: Any = None, clock: Callable[[], float] = time.time,
          sleep: Callable[[float], None] = time.sleep) -> dict:
    """Fetch-only diagnostics, printed as markdown to stdout. `store` (optional) is only read, for the
    thin names of the stored pack; nothing is written."""
    fetcher = deps.fetcher(workers=RUNTIME["fetch_workers"], timeout_s=RUNTIME["fetch_timeout_s"])
    symbols = deps.scan_symbols()
    sample = list(REFERENCE_SYMBOLS) + [s for s in symbols if s not in REFERENCE_SYMBOLS][:60]
    calls = [("quotes (universe)", fetcher.quotes, (symbols,), {}),
             ("movers", fetcher.movers, (), {}),
             ("5m bars 1d (63 symbols)", fetcher.bars_5m, (sample,), {"range_": "1d"}),
             ("5m bars 1mo (20 symbols)", fetcher.bars_5m, (sample[:20],), {"range_": "1mo"}),
             ("daily 3mo (20 symbols)", fetcher.daily, (sample[:20],), {"range_": "3mo"})]
    rows = []
    for label, fn, args, kw in calls:
        t = time.perf_counter()
        try:
            _, rep = fn(*args, **kw)
            rows.append((label, rep.source, rep.status, rep.requested, rep.ok, len(rep.failed), rep.http_429,
                         int((time.perf_counter() - t) * 1000)))
        except Exception as e:  # noqa: BLE001 - a diagnostic run reports every failure
            rows.append((label, "none", f"error: {type(e).__name__}", 0, 0, 0, 0, int((time.perf_counter() - t) * 1000)))
    thin, thin_source = thin_names(deps, store, clock())
    # Liquid names settle within seconds; thin ones are where a provisional (not yet closed) bar shows up.
    finality = bar_finality(fetcher, clock, sleep,
                            symbols=PROBE_FINALITY_SYMBOLS + tuple(s for s in thin if s not in PROBE_FINALITY_SYMBOLS))
    finality.update(thin=thin, thin_source=thin_source)
    md = [f"## Momentum Radar probe, {iso(clock())}", "", "Fetch only: nothing was written to the database.", "",
          "| Call | Source | Status | Requested | OK | Failed | HTTP 429 | Wall ms |", "|---|---|---|---|---|---|---|---|"]
    md += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    md += ["", "### Bar finality", "", f"Thin names: {', '.join(thin)} ({thin_source}).", ""]
    if finality["measured"]:
        md += [f"Bar starting {finality['bar_start']}, refetched at +{', +'.join(map(str, finality['offsets_s']))} s "
               f"after its close. Compare `stable_from_s` with the {PARAMS['session']['bar_final_grace_s']} s grace and "
               f"the +{RUNTIME['tick_offset_s']} s tick. Provisional uses the fetcher's rule (SPEC 12.1): the bar was "
               "the newest row and the last trade was still inside it, so the source had not closed it. "
               "'Changed after' lists the offsets after which the bar still changed; a change after an offset "
               "where it was not provisional is a miss of the rule.", "",
               f"Not stable by the tick: {', '.join(finality['unstable_at_tick']) or 'none'}.", "",
               f"Changed while not provisional: {', '.join(finality['changed_unflagged']) or 'none'}.", "",
               "| Symbol | Kind | First seen (s) | Stable from (s) | Provisional at (s) | Changed after (s) "
               "| Next row from (s) | Close | Volume |",
               "|---|---|---|---|---|---|---|---|---|"]
        md += [f"| {sym} | {'thin' if sym in thin else 'liquid'} | {v['first_seen_s']} | {v['stable_from_s']} | "
               f"{_offsets(v['provisional_s'])} | {_offsets(v['changed_after_s'])} | {v['next_row_s']} | "
               f"{v['final_close']} | {v['final_volume']} |"
               for sym, v in finality["symbols"].items()]
    else:
        md.append(finality["note"])
    md += ["", "### Source health", "", "```json", json.dumps(fetcher.health_state(), indent=1, sort_keys=True), "```", ""]
    print("\n".join(md), flush=True)
    failed = any(r[2] != "ok" for r in rows)
    return {"status": "degraded" if failed else "ok", "message": "probe finished; nothing written",
            "calls": [dict(zip(("call", "source", "status", "requested", "ok", "failed", "http_429", "ms"), r)) for r in rows],
            "finality": finality}


def _probe_store() -> Any:
    try:
        return open_store()
    except Exception as e:  # noqa: BLE001 - the probe works without a database
        logger.warning("probe: no store (%s: %s); using the fixed thin list", type(e).__name__, e)
        return None


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    started = time.time()     # the worker's timeout runs from Popen: the deadline counts the imports in real_deps() too
    ap = argparse.ArgumentParser(description="Run one Momentum Radar scan and store it.")
    ap.add_argument("--tick-id", help="the 5-minute boundary, YYYY-MM-DDTHH:MM:SSZ")
    ap.add_argument("--warmup", action="store_true", help="build today's baseline pack and write a pre-open heartbeat")
    ap.add_argument("--final", action="store_true", help="last tick of the session (close + 50 s)")
    ap.add_argument("--ignore-calendar", action="store_true", help="run the engine even when the market is closed")
    ap.add_argument("--probe", action="store_true", help="fetch-only diagnostics; writes nothing")
    a = ap.parse_args(argv)
    if not a.probe and not a.tick_id:
        ap.error("--tick-id is required")
    if a.tick_id:
        try:
            parse_tick_id(a.tick_id)
        except ValueError:
            ap.error(f"--tick-id must look like 2026-09-28T13:35:00Z, got {a.tick_id!r}")
    try:
        if a.probe:
            result = probe(real_deps(), store=_probe_store())
        else:
            deps = real_deps()
            result = Tick(TickArgs(a.tick_id, a.warmup, a.final, a.ignore_calendar), deps, open_store(),
                          started=started, context=read_context()).run()
    except Exception as e:  # noqa: BLE001 - a bug: report it on the result line, exit non-zero
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"status": "error", "message": f"tick failed: {type(e).__name__}: {e}"[:300]}), flush=True)
        return 1
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    # stdout carries the result line; logs go to stderr, which the worker's container log inherits.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(main())
