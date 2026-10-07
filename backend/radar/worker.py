"""The Momentum Radar worker: the long-running process of the `radar` container (PORT_SPEC section 5).

    python -m radar.worker

A port of the GitHub version's radar.loop scheduling, without git, retiring or handover (the container
restarts on its own), running every day instead of one job per session:

- **Single instance:** it takes the Postgres advisory lock LOCK_KEY and, while another worker holds it,
  retries every 60 s. It then waits for the radar tables, which the backend container's Alembic
  migrations create, polling every 10 s.
- **Ticks:** each NYSE session (half days included) gets the warmup at RUNTIME.warmup_et, a tick at every
  5-minute boundary + tick_offset_s from open + 5 min through the close, and the final tick at close +
  last_tick_after_close_s. Each tick is a `python -m radar.tick` subprocess with a hard timeout. After an
  overrun the next boundary is taken from now, the skipped ones are counted and the engine catches up.
- **Failures:** a tick that failed or timed out without committing gets the failure record
  (Loop._write_failure): the live source health it reached is merged, a timeout counts as one more failed
  call (on every closed endpoint family when it left no live health), and the republished snapshot never
  carries another session's exits, counts or last bar (SPEC 12.3, 12.6).
- **Alerts:** after each successful scan, the ENTER events after the alert cursor go to
  services.alerts.dispatch_radar_entries, then the cursor moves on. Alert failures never stop the radar.
- **Housekeeping:** radar.housekeeping runs nightly at HOUSEKEEPING.schedule_et ET every day, and when a
  tick asks for it (hk_request), at most every 6 h. It records its own radar_housekeeping_runs and
  pipeline_run_log rows.
- **Scan now:** while it waits for the next job it polls radar_runtime.scan_request (written by POST
  /api/radar/scan) every NAP_S and runs a pending request as an extra tick of the latest 5-minute boundary,
  under its own scan_log run id. It leaves the schedule and the session counters alone: the scheduled tick
  of that boundary still runs (a re-run tick is idempotent) and picks up a bar that was not final yet.
- **Option metrics:** after every successful scan (scheduled or requested) `python -m radar.options` refreshes
  the option-chain metrics of the members and warming-up names (subprocess, OPTIONS.timeout_s); a failure
  is logged and never affects the radar.
- **Signals:** SIGTERM / SIGINT stop it within about 5 s and terminate a running tick (terminate, then
  kill after 3 s).
- **Logging:** stdout, plus a pipeline_run_log row per tick (phase radar_tick) for the /schedule page.
  Logging never breaks the loop.

The worker passes its bookkeeping to each tick in RADAR_TICK_CONTEXT (radar.tick stores it in the engine
document, which is where a restarted worker resumes from) and its run id in RADAR_RUN_ID.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, TypeVar

from . import calendar_nyse as cal
from .config import HOUSEKEEPING, OPTIONS, RUNTIME
from .tick import (
    CONTEXT_ENV,
    DEFAULT_MARKET,
    EMPTY_COUNTS,
    HEALTH_REF,
    HELD,
    MS_KEYS,
    RUN_ID_ENV,
    health_ref,
    iso,
    parse_tick_id,
    resolve_session,
    same_session_scan,
    scan_log_row,
    session_json,
    source_block,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")

LOCK_KEY = 771234            # pg advisory lock "radar.worker": one worker per database
LOCK_RETRY_S = 60
SCHEMA_POLL_S = 10
NAP_S = 5.0                  # longest sleep between stop checks: a SIGTERM is noticed within this
POLL_S = 1.0                 # a running tick is checked this often for a stop request
KILL_AFTER_S = 3.0           # a terminated tick that is still alive after this is killed
WRITE_MS_KEEP = 50           # recent DB write times passed to the tick for its hk_request rule
COMMIT_CHECK_ROWS = 10       # scan_log rows searched for a failed tick's own commit
HK_REQUEST_MIN_INTERVAL_S = 6 * 3600
NIGHTLY_GRACE_S = 3600       # a worker (re)started this soon after the nightly time still runs it
ALERT_MAX_AGE_S = 30 * 60    # an entry older than this is not pushed: "entered the radar" must be news
ALERT_LOOKBACK_S = 24 * 3600 # without a cursor, events this recent are considered
ALERT_BATCH = 500
ALERT_TIMEOUT_S = 60.0
RUN_LOG_TIMEOUT_S = 10.0
BREAKER_TRIP = 3             # = fetch.BREAKER_TRIP (importing fetch would load numpy and curl_cffi into the worker)
TICK_CMD = (sys.executable, "-m", "radar.tick")
OPTIONS_CMD = (sys.executable, "-m", "radar.options")
SCAN_REQUEST_TTL_S = 180     # a pending "Scan now" request older than this is dropped as expired


# ---------------------------------------------------------------- the plan (ported from radar.loop)

@dataclass(frozen=True)
class Slot:
    tick_epoch: int          # the 5-minute boundary, passed as --tick-id
    run_at: int              # when the tick starts
    final: bool = False
    warmup: bool = False


@dataclass(frozen=True)
class Job:
    """What the worker does next. No slot means the nightly housekeeping."""
    at: float
    slot: Slot | None = None
    session: cal.Session | None = None
    following: int | None = None     # when the session's next tick starts (state.next_tick_at)
    skipped: int = 0                 # boundaries missed since the last tick (overruns, restarts)


def at_et(day: date, hhmm: str) -> float:
    return datetime.combine(day, cal.parse_hhmm(hhmm), tzinfo=cal.ET).timestamp()


def plan_session(s: cal.Session) -> tuple[Slot, list[Slot]]:
    warm = int(at_et(s.day, RUNTIME["warmup_et"]))
    off = RUNTIME["tick_offset_s"]
    ticks = [Slot(b, b + off) for b in range(s.open_epoch + 300, s.close_epoch, 300)]
    ticks.append(Slot(s.close_epoch, s.close_epoch + RUNTIME["last_tick_after_close_s"], final=True))
    return Slot(warm, warm, warmup=True), ticks


def next_slot(ticks: list[Slot], now: float, last_tick: int | None) -> tuple[Slot | None, int]:
    """The next tick to run and how many boundaries were skipped since last_tick. After an overrun
    the next boundary is taken from now; the final tick always runs, late if it has to."""
    remaining = [t for t in ticks if last_tick is None or t.tick_epoch > last_tick]
    if not remaining:
        return None, 0
    upcoming = [t for t in remaining if t.run_at >= now]
    slot = upcoming[0] if upcoming else remaining[-1]
    skipped = 0 if last_tick is None else sum(1 for t in remaining if t.tick_epoch < slot.tick_epoch)
    return slot, skipped


def next_nightly(after: float) -> float:
    """The first nightly housekeeping time (HOUSEKEEPING.schedule_et, ET) at or after `after`."""
    day = datetime.fromtimestamp(after, cal.UTC).astimezone(cal.ET).date()
    t = at_et(day, HOUSEKEEPING["schedule_et"])
    return t if t >= after else at_et(day + timedelta(days=1), HOUSEKEEPING["schedule_et"])


def event_time(event_id: Any) -> float | None:
    """The bar close an event id starts with (<YYYYMMDDTHHMMZ>-<TICKER>-...), or None."""
    try:
        return datetime.strptime(str(event_id)[:14], "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _epoch(value: Any) -> int | None:
    try:
        return parse_tick_id(value) if isinstance(value, str) and value else None
    except ValueError:
        return None


# ---------------------------------------------------------------- the tick subprocess

def _halt(p: subprocess.Popen) -> None:
    p.terminate()
    try:
        p.communicate(timeout=KILL_AFTER_S)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()


def run_tick_subprocess(cmd: list[str], timeout_s: float, env: dict[str, str],
                        stop: Callable[[], bool] = lambda: False) -> dict:
    """Run one tick; its stderr goes to the container log, its last stdout line is the JSON result. It is
    polled every POLL_S so a stop request (SIGTERM/SIGINT to the worker) terminates it at once."""
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=None, text=True, encoding="utf-8",
                         errors="replace", env=env)
    while True:
        left = timeout_s - (time.monotonic() - t0)
        try:
            out, _ = p.communicate(timeout=max(0.01, min(POLL_S, left)))   # output read so far is kept
            break
        except subprocess.TimeoutExpired:
            if stop():
                _halt(p)
                return {"status": "stopped", "message": "a stop was requested; the scan was terminated",
                        "duration_ms": int((time.monotonic() - t0) * 1000)}
            if time.monotonic() - t0 >= timeout_s:
                _halt(p)
                return {"status": "timeout", "message": f"the scan took longer than {timeout_s:.0f} s and was stopped",
                        "duration_ms": int((time.monotonic() - t0) * 1000)}
    res: dict = {}
    for line in reversed(out.strip().splitlines()):
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            res = doc
            break
    if p.returncode != 0:
        res = {**res, "status": "error", "message": res.get("message") or f"the scan exited with code {p.returncode}"}
    res.setdefault("status", "error")
    res.setdefault("message", "the scan printed no result")
    res["duration_ms"] = int((time.monotonic() - t0) * 1000)
    return res


# ---------------------------------------------------------------- source health of a tick killed on timeout

def _count(v: object) -> int:
    try:
        return max(0, int(v))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _breaker_open(rec: dict) -> bool:
    return isinstance(rec.get("breaker"), dict) and rec["breaker"].get("open") is True


def _fail_once(rec: dict, now: str) -> None:
    """One more failed call on a health record (flat, or one endpoint family); the breaker opens at the trip."""
    rec["consecutive_failures"] = _count(rec.get("consecutive_failures")) + 1
    rec["status"] = "down"
    breaker = dict(rec["breaker"]) if isinstance(rec.get("breaker"), dict) else {}
    if breaker.get("open") is not True and rec["consecutive_failures"] >= BREAKER_TRIP:
        breaker.update(open=True, opened_at=now, calls=0, good_probes=0)
    rec["breaker"] = breaker


def health_after_timeout(health: object, now: str, *, blind: bool = False) -> dict:
    """A tick killed by the timeout counts as one more failed call (SPEC 12.3): the worst endpoint family
    (most failures in a row, among those whose breaker is still closed) is raised by 1 and its breaker
    opens at the trip count, so repeated timeouts reach the Nasdaq fallback even if a hang escapes the
    tick's deadline. `blind`: the tick left no live health, so no call finished and nothing tells which
    family hangs; every family whose breaker is still closed is raised by 1 instead (SPEC 12.6).
    The flat (pre-family) record is handled the same way."""
    h = dict(health) if isinstance(health, dict) else {}
    families = h.get("families")
    if isinstance(families, dict) and families:
        fams = {k: dict(v) if isinstance(v, dict) else {} for k, v in families.items()}
        closed = [k for k, f in fams.items() if not _breaker_open(f)] or list(fams)
        worst = max(closed, key=lambda k: _count(fams[k].get("consecutive_failures")))
        for k in closed if blind else [worst]:
            _fail_once(fams[k], now)
        # the flat top level is derived as in fetch.Fetcher.health_state(): the worst family's values
        rank = ("ok", "degraded", "down")
        top = max(fams.values(), key=lambda f: (_breaker_open(f), rank.index(f["status"]) if f.get("status") in rank
                                                else 0, _count(f.get("consecutive_failures"))))
        h.update(families=fams, status="down", breaker=top.get("breaker"),
                 consecutive_failures=max(_count(f.get("consecutive_failures")) for f in fams.values()))
        if any(_breaker_open(f) for f in fams.values()):
            h["name"] = "nasdaq"
    else:
        _fail_once(h, now)
        if _breaker_open(h):
            h["name"] = "nasdaq"
    return h


# ---------------------------------------------------------------- the backend's async services, from a sync process

def run_async(make: Callable[[], Awaitable[T]], timeout_s: float) -> T:
    """Run one of the backend's async calls (alerts, run log) from this synchronous process.

    Each call gets its own event loop and a fresh pool on the backend's shared async engine: asyncpg
    connections are bound to the loop that opened them, so one pooled by an earlier loop would fail on
    this one. The pool is dropped first (without closing what another loop opened) and closed after."""
    async def isolated() -> T:
        from db.connection import engine
        await engine.dispose(close=False)
        try:
            return await asyncio.wait_for(make(), timeout_s)
        finally:
            await engine.dispose()
    return asyncio.run(isolated())


class PipelineRunLog:
    """pipeline_run_log rows through services.run_log, so the /schedule page lists the radar's ticks."""

    def start(self, phase: str, meta: dict) -> int | None:
        from services import run_log
        return run_async(lambda: run_log.record_run_start(phase, meta=meta), RUN_LOG_TIMEOUT_S)

    def finish(self, row_id: int | None, *, ok: bool, error: str | None, meta: dict) -> None:
        if row_id is None:
            return
        from services import run_log
        status = run_log.STATUS_SUCCESS if ok else run_log.STATUS_FAILED
        run_async(lambda: run_log.record_run_finish(row_id, status=status, error_message=error, meta=meta),
                  RUN_LOG_TIMEOUT_S)


def dispatch_alerts(events: list[dict]) -> int:
    """Discord alerts for new radar entries (services.alerts.dispatch_radar_entries); the number sent."""
    from services.alerts import dispatch_radar_entries
    return int(run_async(lambda: dispatch_radar_entries(events), ALERT_TIMEOUT_S) or 0)


# ---------------------------------------------------------------- the worker

class Worker:
    """The radar's scheduler. `store` is a radar.store.RadarStore; the other arguments are seams for tests:
    `run_tick(cmd, timeout_s, env, stop=...) -> result`, `dispatch_alerts(events) -> int`,
    `housekeeping(trigger) -> report` and `run_log` (start/finish of pipeline_run_log rows)."""

    def __init__(self, store: Any, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 run_tick: Callable[..., dict] = run_tick_subprocess,
                 dispatch_alerts: Callable[[list[dict]], int] = dispatch_alerts,
                 housekeeping: Callable[[str], Any] | None = None,
                 run_log: Any = None, env: Mapping[str, str] = os.environ,
                 tick_cmd: tuple[str, ...] = TICK_CMD,
                 refresh_options: Callable[[], dict] | None = None):
        self.store, self.clock, self.sleep, self.run_tick = store, clock, sleep, run_tick
        self.dispatch_alerts = dispatch_alerts
        self.housekeeping = housekeeping or self._run_housekeeping
        self.run_log = run_log if run_log is not None else PipelineRunLog()
        self.env, self.tick_cmd = env, tick_cmd
        self.refresh_options = refresh_options     # None: no option metrics (tests); main() sets the subprocess
        self.handled_request: str | None = None    # id of the last "Scan now" request handled (runs at most once)
        self.stop = False
        self.started = 0.0
        self.run_id = ""
        self.resumed: dict = {}                    # the loop bookkeeping the last tick stored (restart)
        self.session_date: str | None = None
        self.ticks_today = self.ticks_skipped = 0
        self.last_tick: int | None = None
        self.warmed: str | None = None             # the session whose warmup ran
        self.write_ms: list[int] = []
        self.ops: dict = {}                        # the on-request housekeeping record, kept in the engine document
        self.hk_requested_at: float | None = None
        self.nightly_at = 0.0

    def request_stop(self, signum: int, _frame: object) -> None:
        self.stop = True
        logger.info("signal %s: stopping", signum)

    def sleep_until(self, t: float) -> None:
        while not self.stop:
            left = t - self.clock()
            if left <= 0:
                return
            self.sleep(min(left, NAP_S))

    # -- main flow

    def run(self) -> int:
        self.started = self.clock()
        self.run_id = "worker-" + datetime.fromtimestamp(self.started, cal.UTC).strftime("%Y%m%dT%H%M%SZ")
        logger.info("radar worker %s starting", self.run_id)
        if not self._acquire_lock() or not self._wait_for_schema():
            return 0
        self._resume()
        self.nightly_at = next_nightly(self.started - NIGHTLY_GRACE_S)
        while not self.stop:
            job = self._next_job(self.clock())
            self._wait(job.at)
            if self.stop:
                break
            if job.slot is None:
                self._nightly()
            else:
                self._run(job)
        logger.info("radar worker %s stopped", self.run_id)
        return 0

    def _acquire_lock(self) -> bool:
        while not self.stop:
            try:
                if self.store.try_advisory_lock(LOCK_KEY):
                    logger.info("holding advisory lock %d (radar.worker)", LOCK_KEY)
                    return True
                logger.info("another radar worker holds advisory lock %d; retrying in %d s", LOCK_KEY, LOCK_RETRY_S)
            except Exception as e:  # noqa: BLE001 - the database may still be starting
                logger.warning("advisory lock %d failed (%s: %s); retrying in %d s", LOCK_KEY, type(e).__name__, e,
                               LOCK_RETRY_S)
            self.sleep_until(self.clock() + LOCK_RETRY_S)
        return False

    def _wait_for_schema(self) -> bool:
        waited = False
        while not self.stop:
            if self._schema_ready():
                if waited:
                    logger.info("radar tables found")
                return True
            if not waited:
                logger.info("waiting for the radar tables (the backend container runs the migrations)")
                waited = True
            self.sleep_until(self.clock() + SCHEMA_POLL_S)
        return False

    def _schema_ready(self) -> bool:
        try:
            check = getattr(self.store, "schema_ready", None)
            if check is not None:
                return bool(check())
            self.store.load_engine_doc()            # raises while the tables are missing
            return True
        except Exception as e:  # noqa: BLE001 - not ready yet
            logger.debug("radar schema not ready: %s: %s", type(e).__name__, e)
            return False

    def _resume(self) -> None:
        """Carry what the last tick stored: the session counters (a restart mid-session continues them),
        the recent DB write times and the last on-request housekeeping."""
        try:
            doc = self.store.load_engine_doc() or {}
        except Exception as e:  # noqa: BLE001 - start fresh; the next tick stores the bookkeeping again
            logger.warning("could not read the engine document (%s: %s); starting fresh", type(e).__name__, e)
            doc = {}
        loop = doc.get("loop") if isinstance(doc.get("loop"), dict) else {}
        self.resumed = loop
        self.write_ms = [int(v) for v in loop.get("write_ms") or [] if isinstance(v, (int, float))][-WRITE_MS_KEEP:]
        ops = doc.get("ops") if isinstance(doc.get("ops"), dict) else {}
        self.ops = {k: ops[k] for k in ("hk_dispatched_at", "hk_dispatch_result") if k in ops}
        self.hk_requested_at = _epoch(ops.get("hk_dispatched_at"))

    # -- what next

    def _next_job(self, now: float) -> Job:
        tick = self._tick_job(now)
        return Job(self.nightly_at) if self.nightly_at <= tick.at else tick

    def _tick_job(self, now: float) -> Job:
        """Today's next tick, else the next session's warmup."""
        day = datetime.fromtimestamp(now, cal.UTC).astimezone(cal.ET).date()
        today = cal.session_for(day)
        job = self._session_job(today, now) if today is not None else None
        if job is None:
            job = self._session_job(cal.next_sessions(day + timedelta(days=1), 1)[0], now)
        assert job is not None, "a future session always has its warmup ahead"
        return job

    def _session_job(self, s: cal.Session, now: float) -> Job | None:
        date_ = s.day.isoformat()
        warmup, ticks = plan_session(s)
        if self.warmed != date_ and now < ticks[0].run_at:
            return Job(warmup.run_at, warmup, s, following=ticks[0].run_at)
        slot, skipped = next_slot(ticks, now, self._last_tick_of(date_))
        # A final tick later than the end of after-hours is dropped: the next warmup takes over.
        if slot is None or (slot.final and now >= s.post_close.timestamp()):
            return None
        following = None if slot.final else ticks[ticks.index(slot) + 1].run_at
        return Job(slot.run_at, slot, s, following=following, skipped=skipped)

    def _last_tick_of(self, session_date: str) -> int | None:
        if self.session_date == session_date:
            return self.last_tick
        return _epoch(self.resumed.get("last_tick")) if self.resumed.get("session") == session_date else None

    def _begin_session(self, s: cal.Session) -> None:
        """Reset the counters for a new session, or resume them when the stored loop is of this session."""
        session_date = s.day.isoformat()
        if self.session_date == session_date:
            return
        self.session_date, self.ticks_today, self.ticks_skipped, self.last_tick = session_date, 0, 0, None
        if self.resumed.get("session") == session_date:
            self.ticks_today = _count(self.resumed.get("ticks_today"))
            self.ticks_skipped = _count(self.resumed.get("ticks_skipped"))
            self.last_tick = _epoch(self.resumed.get("last_tick"))
        _, ticks = plan_session(s)
        logger.info("session %s%s: %d ticks %s..%s%s", session_date, " (half day)" if s.early_close else "", len(ticks),
                    iso(ticks[0].run_at), iso(ticks[-1].run_at),
                    f", resuming after {iso(self.last_tick)}" if self.last_tick else "")

    # -- one tick

    def _context(self, following: int | None) -> dict:
        """The bookkeeping a tick publishes (state.health, next_tick_at) and stores in the engine document."""
        return {"loop": {"run_id": self.run_id, "started_at": iso(self.started), "session": self.session_date,
                         "ticks_today": self.ticks_today, "ticks_skipped": self.ticks_skipped,
                         "last_tick": iso(self.last_tick) if self.last_tick else None,
                         "write_ms": self.write_ms[-WRITE_MS_KEEP:],
                         "next_tick_at": iso(following) if following else None},
                "ops": dict(self.ops)}

    def _run(self, job: Job) -> None:
        slot, s = job.slot, job.session
        assert slot is not None and s is not None
        self._begin_session(s)
        if slot.warmup:
            self.warmed = s.day.isoformat()
        else:
            self.ticks_skipped += job.skipped
            self.ticks_today += 1
            self.last_tick = slot.tick_epoch
        tick_id = iso(slot.tick_epoch)
        kind = "warmup" if slot.warmup else "final" if slot.final else "tick"
        cmd = [*self.tick_cmd, "--tick-id", tick_id] + ["--warmup"] * slot.warmup + ["--final"] * slot.final
        env = {**self.env, CONTEXT_ENV: json.dumps(self._context(job.following)), RUN_ID_ENV: self.run_id}
        row_id = self._log_start(tick_id, kind)
        res = self.run_tick(cmd, RUNTIME["tick_timeout_s"], env, stop=lambda: self.stop)
        failed = res.get("status") in ("timeout", "error")
        if self.stop:
            self._log_finish(row_id, tick_id, kind, res, ok=False)
            logger.info("%s %s stopped", kind, tick_id)
            return
        if failed and not self._committed(tick_id):
            self._write_failure(job, res)
        elif not failed:
            self._note_write(res)
        self._log_finish(row_id, tick_id, kind, res, ok=not failed)
        logger.info("%s %s %s · %s on radar (+%d/-%d) · %s ms%s", kind, tick_id[11:16] + "Z", res.get("status"),
                    res.get("members", "?"), len(res.get("entered") or []), len(res.get("exited") or []),
                    res.get("duration_ms", 0),
                    f" · {res.get('message')}" if res.get("status") not in ("ok", "closed") else "")
        if not failed and not slot.warmup:
            self._dispatch_alerts()
            self._refresh_options()
        if res.get("hk_request"):
            self._on_hk_request(str(res["hk_request"]))

    # -- "Scan now" requests and option metrics

    def _wait(self, t: float) -> None:
        """sleep_until(t), running "Scan now" requests that arrive meanwhile."""
        while not self.stop:
            self._poll_scan_request()
            left = t - self.clock()
            if left <= 0:
                return
            self.sleep(min(left, NAP_S))

    def _poll_scan_request(self) -> None:
        getter = getattr(self.store, "get_scan_request", None)
        if getter is None:
            return
        try:
            req = getter()
        except Exception as e:  # noqa: BLE001 - no request is the safe reading; polled again in NAP_S
            logger.debug("scan request unreadable: %s: %s", type(e).__name__, e)
            return
        if not isinstance(req, dict) or req.get("status") != "pending":
            return
        # Handled once, even when its outcome could not be stored (it would otherwise re-run every poll).
        if req.get("id") is not None and req.get("id") == self.handled_request:
            return
        self.handled_request = req.get("id")
        now = self.clock()
        asked = _epoch(req.get("requested_at"))
        if asked is None or now - asked > SCAN_REQUEST_TTL_S:
            self._finish_request(req, "error", "The request expired before the scanner picked it up.")
            return
        s = cal.session_for(datetime.fromtimestamp(now, cal.UTC).astimezone(cal.ET).date())
        if s is None or not (s.open_epoch + 300 <= now < s.close_epoch):
            self._finish_request(req, "refused", "The market is closed: the radar scans only during regular "
                                                 "hours, from 5 minutes after the open to the close.")
            return
        self._manual_scan(req, s, now)

    def _finish_request(self, req: dict, status: str, message: str, result: dict | None = None) -> None:
        doc = {**req, "status": status, "message": message, "finished_at": iso(self.clock())}
        if result is not None:
            doc["result"] = result
        try:
            self.store.put_scan_request(doc)
        except Exception:  # noqa: BLE001 - the page shows the request as running until it expires
            logger.warning("could not record the scan request outcome", exc_info=True)

    def _manual_scan(self, req: dict, s: cal.Session, now: float) -> None:
        """One extra tick of the latest 5-minute boundary for a "Scan now" request. The tick's bookkeeping
        (loop context) is this worker's, unchanged: the scheduled ticks and counters go on as planned."""
        tick_epoch = min(int(now) // 300 * 300, s.close_epoch)
        tick_id = iso(tick_epoch)
        req = {**req, "status": "running", "started_at": iso(now), "tick_id": tick_id}
        try:
            self.store.put_scan_request(req)
        except Exception:  # noqa: BLE001 - run it anyway; the outcome is recorded below
            logger.warning("could not mark the scan request running", exc_info=True)
        self._begin_session(s)
        following = self._tick_job(now)
        stamp = datetime.fromtimestamp(now, cal.UTC).strftime("%H%M%S")
        run_id = f"{self.run_id}-manual-{stamp}"
        cmd = [*self.tick_cmd, "--tick-id", tick_id]
        context = self._context(int(following.at) if following.slot and not following.slot.warmup else None)
        env = {**self.env, CONTEXT_ENV: json.dumps(context), RUN_ID_ENV: run_id}
        row_id = self._log_start(tick_id, "manual")
        logger.info("scan now (requested by %s): tick %s", req.get("requested_by") or "?", tick_id)
        res = self.run_tick(cmd, RUNTIME["tick_timeout_s"], env, stop=lambda: self.stop)
        failed = res.get("status") in ("timeout", "error", "stopped")
        self._log_finish(row_id, tick_id, "manual", res, ok=not failed)
        result = {k: res.get(k) for k in ("status", "members", "entered", "exited", "duration_ms")}
        if failed:
            logger.info("scan now %s %s: %s", tick_id, res.get("status"), res.get("message"))
            self._finish_request(req, "error", str(res.get("message") or "The scan failed."), result)
            return
        self._note_write(res)
        self._dispatch_alerts()
        self._refresh_options()
        logger.info("scan now %s %s, %s on radar (+%d/-%d), %s ms", tick_id, res.get("status"),
                    res.get("members", "?"), len(res.get("entered") or []), len(res.get("exited") or []),
                    res.get("duration_ms", 0))
        ok = res.get("status") in ("ok", "closed")
        self._finish_request(req, "done", "" if ok else str(res.get("message") or ""), result)

    def _refresh_options(self) -> None:
        if self.refresh_options is None:
            return
        try:
            res = self.refresh_options() or {}
            failed = res.get("failed") or []
            logger.info("option metrics %s: %s/%s tickers in %s ms%s", res.get("status"), res.get("ok", "?"),
                        res.get("tickers", "?"), res.get("duration_ms", "?"),
                        (" (failed: " + ", ".join(failed) + ")") if failed else "")
        except Exception:  # noqa: BLE001 - option metrics never stop the radar
            logger.warning("option metrics refresh failed", exc_info=True)

    def _note_write(self, res: dict) -> None:
        ms = res.get("ms")
        write = ms.get("write") if isinstance(ms, dict) else None
        if isinstance(write, (int, float)):
            self.write_ms = (self.write_ms + [int(write)])[-WRITE_MS_KEEP:]

    def _committed(self, tick_id: str) -> bool:
        """Whether a tick reported as failed had committed anyway (killed between its commit and its exit):
        its scan_log row is there, and recording a failure would overwrite a good snapshot."""
        try:
            rows = self.store.scan_log_tail(COMMIT_CHECK_ROWS)
        except Exception as e:  # noqa: BLE001 - assume not: the failure record is the safe side
            logger.warning("could not read scan_log (%s: %s)", type(e).__name__, e)
            return False
        return any(isinstance(r, dict) and r.get("tick") == tick_id and r.get("run_id") == self.run_id for r in rows)

    def _reached_health(self, tick_id: str) -> dict | None:
        """The source health the failed tick reached (written by its fetcher after every call), or None
        when it finished no call. A copy stamped with another tick is an earlier one, never merged."""
        try:
            live = self.store.load_live_health()
        except Exception as e:  # noqa: BLE001 - treated as no evidence
            logger.warning("could not read the live source health (%s: %s)", type(e).__name__, e)
            return None
        if not isinstance(live, dict) or live.get(HEALTH_REF) != health_ref(self.run_id, tick_id):
            return None
        return {k: v for k, v in live.items() if k != HEALTH_REF}

    def _write_failure(self, job: Job, res: dict) -> None:
        try:
            self._record_failure(job, res)
        except Exception:  # noqa: BLE001 - the next tick tries again; the worker goes on
            logger.exception("could not record the failed tick %s", iso(job.slot.tick_epoch) if job.slot else "?")

    def _record_failure(self, job: Job, res: dict) -> None:
        """The tick wrote nothing: log the run, keep this worker's bookkeeping and the source health the
        tick reached (SPEC 12.3) and, during the session, republish the previous snapshot flagged as an
        error. Only a scan of the same session is republished; otherwise (the pre-open heartbeat, another
        session) the lists, day counts and last_bar start empty (SPEC 12.6)."""
        slot = job.slot
        assert slot is not None
        now = self.clock()
        engine = self.store.load_engine_doc() or {"schema": 1}
        reached = self._reached_health(iso(slot.tick_epoch))
        if reached is not None:
            stored = engine.get("source_health")
            engine["source_health"] = {**(stored if isinstance(stored, dict) else {}), **reached}
        if res.get("status") == "timeout":
            engine["source_health"] = health_after_timeout(engine.get("source_health"), iso(now),
                                                           blind=reached is None)
        ctx = self._context(job.following)
        stored_ops = engine.get("ops")
        engine.update(loop=ctx["loop"], ops={**(stored_ops if isinstance(stored_ops, dict) else
                                                {"hk_dispatched_at": None}), **ctx["ops"]})
        state = self.store.load_state() or {}
        if state and not slot.warmup:
            scanned, _ = resolve_session(slot.tick_epoch, now, ignore_calendar=False, final=slot.final)
            if not same_session_scan(state, scanned.day.isoformat() if scanned else None):
                state.update(last_bar=None, market=dict(DEFAULT_MARKET), counts=dict(EMPTY_COUNTS),
                             **{k: [] for k in ("members", "heating", "recent_exits", "sector_banners")})
            if scanned is not None:
                state["session"] = session_json(scanned, cal.phase_at(datetime.fromtimestamp(now, cal.UTC))[0])
            what = "timed out" if res.get("status") == "timeout" else "failed"
            state.update(generated_at=iso(now), status="error", message=f"The last scan {what}. {HELD}",
                         next_tick_at=iso(job.following) if job.following else None,
                         health={"ticks_today": self.ticks_today, "ticks_skipped": self.ticks_skipped,
                                 "last_tick_ms": int(res.get("duration_ms", 0)), "loop_run_id": self.run_id,
                                 "loop_started_at": iso(self.started), "published_late": 0})
            if "source_health" in engine:
                state["source"] = source_block(engine["source_health"])
            published = state
        else:
            # The warmup (or a tick before any snapshot exists) leaves the snapshot as it is: the store
            # keeps the document when it is passed empty.
            published, state = state, {}
        phase, session = cal.phase_at(datetime.fromtimestamp(slot.tick_epoch, cal.UTC))
        row = scan_log_row(
            iso(slot.tick_epoch), iso(now), run_id=self.run_id, session=session.day.isoformat() if session else None,
            phase=phase, status="error", lag_s=max(0, int(slot.run_at - slot.tick_epoch)),
            members=len(published.get("members") or []), heating=len(published.get("heating") or []),
            source=(published.get("source") or {}).get("name", "yahoo"),
            ms={**dict.fromkeys(MS_KEYS, 0), "total": int(res.get("duration_ms", 0))},
            errors=[f"{res.get('status')}: {res.get('message', '')}"])
        self.store.write_failure(state=state, engine_doc=engine, scan_row=row)

    # -- alerts

    def _dispatch_alerts(self) -> None:
        """Push the ENTER events committed since the alert cursor, then move the cursor past every newer
        event. Only fresh entries go out (ALERT_MAX_AGE_S): after an outage, "entered the radar" would no
        longer be true. The cursor moves only once the dispatch returned; a retry is safe because each
        alert is deduplicated on radar_entry:<event id>."""
        try:
            cursor = self.store.get_alert_cursor()
            now = self.clock()
            since = event_time(cursor) if cursor else None
            since = now - ALERT_LOOKBACK_S if since is None else since
            events = self.store.recent_events(since=datetime.fromtimestamp(since, cal.UTC), limit=ALERT_BATCH)
            newer = sorted((e for e in events if isinstance(e, dict) and isinstance(e.get("id"), str)
                            and (cursor is None or e["id"] > cursor)), key=lambda e: e["id"])
            if not newer:
                return
            fresh = [e for e in newer if e.get("type") == "ENTER"
                     and (t := event_time(e["id"])) is not None and now - t <= ALERT_MAX_AGE_S]
            if fresh:
                sent = self.dispatch_alerts(fresh)
                logger.info("radar alerts: %d new entr%s (%s), %s alert(s) sent", len(fresh),
                            "y" if len(fresh) == 1 else "ies", ", ".join(e.get("ticker", "?") for e in fresh), sent)
            self.store.set_alert_cursor(newer[-1]["id"])
        except Exception:  # noqa: BLE001 - alerts never stop the radar
            logger.warning("radar alerts failed; the radar goes on", exc_info=True)

    # -- housekeeping

    def _nightly(self) -> None:
        self.nightly_at = next_nightly(self.nightly_at + 1)
        self._housekeeping("schedule")

    def _on_hk_request(self, reason: str) -> None:
        now = self.clock()
        if self.hk_requested_at is not None and now - self.hk_requested_at < HK_REQUEST_MIN_INTERVAL_S:
            return
        self.hk_requested_at = now
        logger.info("housekeeping requested by the tick: %s", reason)
        # Kept in the engine document (the next tick stores it), so a restart does not run it again at once.
        self.ops = {"hk_dispatched_at": iso(now), "hk_dispatch_result": self._housekeeping("worker_request")}

    def _housekeeping(self, trigger: str) -> str:
        """One housekeeping run in this process; ticks that fall due meanwhile are skipped and caught up,
        as after any overrun. radar.housekeeping records its own run rows. Returns the run's status (ok,
        error, busy), or "failed" when it raised."""
        t0 = time.monotonic()
        logger.info("housekeeping (%s) starting", trigger)
        try:
            report = self.housekeeping(trigger)
        except Exception:  # noqa: BLE001 - housekeeping never stops the radar
            logger.exception("housekeeping (%s) failed", trigger)
            return "failed"
        status = str(report.get("status") or "done") if isinstance(report, dict) else "done"
        logger.info("housekeeping (%s) %s in %.0f s", trigger, status, time.monotonic() - t0)
        return status

    def _run_housekeeping(self, trigger: str) -> dict:
        from . import housekeeping
        return housekeeping.run(mode="apply", trigger=trigger, store=self.store)

    # -- pipeline_run_log

    def _log_start(self, tick_id: str, kind: str) -> int | None:
        try:
            return self.run_log.start("radar_tick", {"tick": tick_id, "kind": kind, "worker": self.run_id})
        except Exception:  # noqa: BLE001 - logging never breaks the loop
            logger.warning("pipeline_run_log start failed for %s", tick_id, exc_info=True)
            return None

    def _log_finish(self, row_id: int | None, tick_id: str, kind: str, res: dict, *, ok: bool) -> None:
        meta = {"tick": tick_id, "kind": kind, "worker": self.run_id, "status": res.get("status"),
                "members": res.get("members"), "entered": res.get("entered") or [], "exited": res.get("exited") or [],
                "duration_ms": res.get("duration_ms")}
        try:
            self.run_log.finish(row_id, ok=ok, error=None if ok else f"{res.get('status')}: {res.get('message', '')}"[:500],
                                meta=meta)
        except Exception:  # noqa: BLE001 - logging never breaks the loop
            logger.warning("pipeline_run_log finish failed for %s", tick_id, exc_info=True)


def install_signal_handlers(worker: Worker) -> None:
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Run the Momentum Radar worker (scans every NYSE session, nightly "
                                        "housekeeping).").parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .store import RadarStore
    store = RadarStore()
    worker = Worker(store, refresh_options=lambda: run_tick_subprocess(
        list(OPTIONS_CMD), float(OPTIONS["timeout_s"]), dict(os.environ)))
    install_signal_handlers(worker)
    try:
        return worker.run()
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
