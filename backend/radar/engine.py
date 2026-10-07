"""Momentum Radar state machine (signal-model.md sections 1-10 with radar/docs/github-spec.md
sections 4.5, 5, 6 and 12.2).

The engine runs on bar time: every call processes each final, not yet processed regular-session slot in
order, so live ticks, catch-up after an outage and the replay all go through the same per-slot step.
All state lives in plain JSON types (`state_dict`) so a new process resumes exactly where the last left off.
The output describes what is moving now. It is educational analysis, not advice and not a forecast.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from radar.baselines import BaselinePack
from radar.calendar_nyse import Session
from radar.config import REFERENCE_SYMBOLS
from radar.features import (NS, Grid, SessionFeatures, build_grid, direction, entry_gates, extreme,
                            halt_flags, score, split_flags)
from radar.types import Bars, Quote

STATE_SCHEMA = 1
RECENT_EXITS_MAX = 30
SPARK_PRE_SLOTS = 6
BANNER_TTL_SLOTS = 6            # a sector banner lists names blocked by the sector cap in the last 30 minutes
STAGE_A_MIN_COVERAGE = 0.5      # below this share of eligible names with quotes, Stage B scans everything
BREADTH_MIN_COVERAGE = 0.5      # breadth needs 30-minute prices for at least this share of eligible names
FAST_PATH_RVOL3 = 4.0           # single-tick entry also needs this burst volume (signal-model.md 4.3)
SIGNAL_KEYS = ("z3", "z6", "zday", "rvol3", "rvolc", "dvwap", "er6", "acc")

EXIT_TEXT = {
    "FADE": "Momentum faded: {z30:+.1f}σ vs market over 30 min, {z15:+.1f}σ over 15 min",
    "DRY": "Volume dried up to {rvol:.1f}× normal for this time",
    "STALL": "No new {side} for {mins} min and the 30-min thrust is down to {z30:+.1f}σ",
    "REVERSAL": "Sharp move the other way: {z15:+.1f}σ vs market in 15 min",
    "GIVEBACK": "Gave back {gb:.0f}% of the move that put it on the radar",
    "VWAP_CROSS": "Crossed back {vside} session VWAP",
    "SESSION_END": "The radar clears before the closing auction",
    "HALT_LONG": "Trading halted for {mins} min",
    "DATA_STALE": "No fresh 5-minute bars for {n} scans",
    "DISPLACED": "Replaced by {by}, a stronger mover, while the radar was full",
}


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(x) -> float | None:
    """Plain float for state/output; NaN and inf become None."""
    x = float(x)
    return x if math.isfinite(x) else None


def _r(x, nd: int) -> float | None:
    x = _f(x) if x is not None else None
    return None if x is None else round(x, nd)


def _pct(log_ret) -> float | None:
    x = _f(log_ret) if log_ret is not None else None
    return None if x is None else round(math.expm1(x) * 100.0, 2)


def _dir_word(d: int) -> str:
    return "up" if d > 0 else "down"


@dataclass
class TickOutput:
    processed_slots: list[int]
    events: list[dict] = field(default_factory=list)
    member_rows: list[dict] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)


@dataclass
class _Ctx:
    """One step's view of the Stage-B symbols: grid, features and halt flags, aligned by row."""
    symbols: list[str]
    row: dict[str, int]
    pix: np.ndarray
    grid: Grid
    feats: SessionFeatures
    eligible: np.ndarray
    halts: object
    spy: int
    spy_last: int
    prov: np.ndarray                 # slot of a newest bar that may still be revised (Bars.provisional_last), else -1
    degraded: bool = False           # bars came from the fallback source: volume is not on the baseline's basis


class Engine:
    def __init__(self, params: dict, pack: BaselinePack, session: Session, state: dict | None = None):
        self.p = params
        self.pack = pack
        self.session = session
        self.day = session.day.isoformat()
        self.n = session.n_slots
        s = params["session"]
        shift = NS - self.n                          # half day: the same windows, measured from the close
        self.first_entry = s["first_entry_slot"]
        self.last_entry = s["last_entry_slot"] - shift
        self.last_confirm = s["last_confirm_slot"] - shift
        self.session_end = s["session_end_slot"] - shift
        self.grace = s["bar_final_grace_s"]
        self._index_pack()
        same = bool(state) and state.get("schema") == STATE_SCHEMA and state.get("session") == self.day
        self.st = copy.deepcopy(state) if same else self._fresh_state()
        # A pack rebuilt mid-session (new params_version, lost extra pack) can miss names the state holds:
        # their heating is dropped; members stay frozen on their last view until SESSION_END.
        for sym in [s for s in self.st["heating"] if s not in self.idx]:
            del self.st["heating"][sym]

    # ------------------------------------------------------------------ setup
    def _index_pack(self) -> None:
        if "SPY" not in self.pack.symbols:
            raise ValueError("baseline pack has no SPY")
        self.syms = sorted(self.pack.symbols)
        self.idx = {s: i for i, s in enumerate(self.syms)}
        b = [self.pack.symbols[s] for s in self.syms]
        arr = lambda key: np.array([np.nan if getattr(x, key) is None else getattr(x, key) for x in b], dtype=float)
        self.beta, self.sig5, self.sigd = arr("beta"), arr("sigma5"), arr("sigma_d")
        self.pc, self.missing = arr("prev_close"), arr("missing_share")
        self.elig = np.array([x.eligible for x in b], dtype=bool)
        self.vm = np.vstack([x.vm for x in b])
        self.cvm = np.vstack([x.cvm for x in b])
        self.names = [x.name for x in b]
        self.sectors = [x.sector or "Unknown" for x in b]
        self.m = np.asarray(self.pack.tod_m, dtype=float)
        self.sigday_scale = float(np.sqrt(np.sum(self.m[1:] ** 2)))
        self.ms_ok = self.missing <= self.p["universe"]["missing_share_max"]

    def _fresh_state(self) -> dict:
        return {"schema": STATE_SCHEMA, "session": self.day, "params_version": self.p["params_version"],
                "last_slot": -1, "members": {}, "heating": {}, "memory": {}, "exits": [],
                "market": {"mode": "normal", "dir": 0, "off_count": 0, "zS6": None, "breadth": None,
                           "spy_chg_day_pct": None},
                "banners": {}, "ext_halts": {}, "ring": {"syms": [], "px": {}},
                "counts": {"entered": 0, "exited": 0, "stage_b": 0}}

    def state_dict(self) -> dict:
        return copy.deepcopy(self.st)

    def _label(self, sym: str, x: dict) -> tuple[str | None, str]:
        """Name and sector of a member or heating entry: stored at entry, so they never need the pack.

        State written before they were stored falls back to the pack, then to (None, "Unknown")."""
        if "sector" in x:
            return x.get("name"), x["sector"]
        i = self.idx.get(sym)
        return (self.names[i], self.sectors[i]) if i is not None else (None, "Unknown")

    def _final_slot(self, now_epoch: int) -> int:
        """Last slot whose bar is final at now (bar close + grace), capped at the session's last slot."""
        k = (now_epoch - self.session.open_epoch - 300 - self.grace) // 300
        return int(min(max(k, -1), self.n - 1))

    # ------------------------------------------------------------------ stage A
    def stage_a(self, quotes: dict[str, Quote], now_epoch: int, *, quotes_ok: bool = True) -> list[str]:
        """Symbols needing 5-minute bars this tick (signal-model.md 1.4).

        `quotes_ok=False` (the quote call is down): members, heating names and the references only. The scan
        never widens to the whole universe during an outage, and missing data never evicts a member."""
        st = self.st
        must = set(st["members"]) | set(st["heating"]) | set(REFERENCE_SYMBOLS)
        k = self._final_slot(now_epoch)
        if k < 0 or not quotes_ok:
            return sorted(must)
        px = np.full(len(self.syms), np.nan)
        vol = np.full(len(self.syms), np.nan)
        for i, sym in enumerate(self.syms):
            q = quotes.get(sym)
            if q is not None and q.price is not None and q.price > 0:
                px[i] = q.price
                if q.day_volume is not None:
                    vol[i] = q.day_volume
        self._ring_put(k, px)
        usable = self.elig & np.isfinite(px) & np.isfinite(vol)
        if usable.sum() < STAGE_A_MIN_COVERAGE * self.elig.sum():
            passers = self.elig
        else:
            pf = self.p["prefilter"]
            spy = self.idx["SPY"]
            with np.errstate(divide="ignore", invalid="ignore"):
                rday = np.log(px / self.pc)
                zraw = rday / self.sigd
                zrel = (rday - self.beta * rday[spy]) / self.sigd
                rvolc = vol / self.cvm[:, k]
            passers = usable & ((np.abs(zraw) >= pf["zday_abs"]) | (np.abs(zrel) >= pf["zday_abs"])) \
                & (rvolc >= pf["rvolc"])
        return sorted(must | {self.syms[i] for i in np.flatnonzero(passers)})

    def _ring_put(self, k: int, px: np.ndarray) -> None:
        """Quote prices by slot for breadth over names that have no bars this tick."""
        ring = self.st["ring"]
        if ring["syms"] != self.syms:
            ring["px"] = {slot: [_f(x) for x in self._ring_get(int(slot))] for slot in ring["px"]}
            ring["syms"] = list(self.syms)
        ring["px"][str(k)] = [_r(x, 4) for x in px]          # quote precision; keeps engine.json small

    def _ring_get(self, k: int) -> np.ndarray:
        ring = self.st["ring"]
        vals = ring["px"].get(str(k))
        if vals is None:
            return np.full(len(self.syms), np.nan)
        if ring["syms"] == self.syms:
            return np.array([np.nan if x is None else x for x in vals], dtype=float)
        by = dict(zip(ring["syms"], vals))
        return np.array([np.nan if by.get(s) is None else by[s] for s in self.syms], dtype=float)

    def _ring_prune(self) -> None:
        keep = self.st["last_slot"] - 5          # the next slot's 30-minute anchor is last_slot - 5
        self.st["ring"]["px"] = {s: v for s, v in self.st["ring"]["px"].items() if int(s) >= keep}

    # ------------------------------------------------------------------ step
    def step(self, bars: dict[str, Bars], now_epoch: int, *, halted: set[str] | frozenset[str] = frozenset(),
             degraded_volume: bool = False) -> TickOutput:
        """`halted`: symbols an external feed reports halted now; applied to the slots this call processes.

        `degraded_volume`: the bars come from the fallback source, whose volume is not on the baseline's basis.
        No new heating or entries then, and no DRY soft-fails; members are refreshed and exit on price rules."""
        st = self.st
        k_now = self._final_slot(now_epoch)
        due = list(range(st["last_slot"] + 1, k_now + 1))
        out = TickOutput(processed_slots=[])
        if due:
            for sym in sorted(halted):
                slots = st["ext_halts"].setdefault(sym, [])
                slots.extend(k for k in due if k not in slots)
            ctx = self.context(bars, degraded_volume=degraded_volume)
            st["counts"]["stage_b"] = len(ctx.symbols) if ctx else 0
            end_due = k_now >= self.session_end
            for k in due:
                live = ctx is not None and ctx.spy_last >= k
                if not live and not end_due:
                    break                                # SPY has not printed this slot yet: wait
                self._process(k, ctx if live else None, k_now, out)
                st["last_slot"] = k
                out.processed_slots.append(k)
            self._ring_prune()
        out.snapshot = self.snapshot()
        return out

    def context(self, bars: dict[str, Bars], *, degraded_volume: bool = False) -> _Ctx | None:
        """Grid, features, halt flags and eligibility of the pack symbols in `bars` (None without SPY bars)."""
        symbols = sorted(s for s in bars if s in self.idx)
        if "SPY" not in symbols:
            return None
        grid = build_grid(bars, symbols, self.session.open_epoch, self.n)
        spy = symbols.index("SPY")
        if grid.first[spy] >= self.n:
            return None
        pix = np.array([self.idx[s] for s in symbols])
        m = self.m[:self.n]
        feats = SessionFeatures(grid, spy=spy, m=m, beta=self.beta[pix], sig5=self.sig5[pix],
                                sig5_spy=self.pack.sigma5_spy, vm=self.vm[pix, :self.n], cvm=self.cvm[pix, :self.n],
                                sigd=self.sigd[pix], pc=self.pc[pix], sigday_scale=self.sigday_scale)
        row = {s: i for i, s in enumerate(symbols)}
        ext = np.zeros(grid.c.shape, dtype=bool)
        for sym, slots in self.st["ext_halts"].items():
            if sym in row:
                ext[row[sym], [j for j in slots if j < self.n]] = True
        halts = halt_flags(grid, spy=spy, sig5=self.sig5[pix], m=m, ms_ok=self.ms_ok[pix],
                           move_sigma=self.p["halt"]["move_sigma"], reopen_bars=self.p["halt"]["reopen_block_bars"],
                           external=ext)
        eligible = self.elig[pix] & ~split_flags(grid, self.pc[pix], self.sigd[pix])
        spy_last = int(np.flatnonzero(grid.present[spy])[-1])
        prov = np.full(len(symbols), -1)
        for i, sym in enumerate(symbols):
            b = bars[sym]
            if getattr(b, "provisional_last", False) and len(b):
                off = int(b.ts[-1]) - self.session.open_epoch
                if off >= 0 and off % 300 == 0:
                    prov[i] = off // 300
        return _Ctx(symbols, row, pix, grid, feats, eligible, halts, spy, spy_last, prov, degraded_volume)

    # ------------------------------------------------------------------ one slot
    def _process(self, k: int, ctx: _Ctx | None, k_now: int, out: TickOutput) -> None:
        if ctx is None:
            self._process_frozen(k, k_now, out)
            return
        p, st = self.p, self.st
        f = ctx.feats.at(k)
        has = ctx.grid.first <= k
        halted, reopen = ctx.halts.halted[:, k], ctx.halts.reopen[:, k]
        # New heating, confirmation and entry need settled bars on the baseline's volume basis.
        ok = (ctx.eligible & has & f["fresh"] & (f["dollar3"] >= p["universe"]["tick_dollar3_min"])
              & ~halted & ~reopen & (ctx.prov != k) & (not ctx.degraded))
        self._update_market(k, ctx, f)
        sc = {1: score(f, 1, p["entry"], p["score_weights"]), -1: score(f, -1, p["entry"], p["score_weights"])}

        for sym in sorted(st["members"]):
            i = ctx.row.get(sym)
            if i is None or not has[i]:
                self._member_frozen(sym, k, k_now, out)
            else:
                self._member_step(sym, i, k, k_now, f, sc, ctx, out)

        cands = []
        for sym in sorted(st["heating"]):
            h = st["heating"].pop(sym)
            i = ctx.row.get(sym)
            if i is not None and k <= self.last_confirm and ok[i] and self._confirms(h, i, f):
                cands.append((float(sc[h["dir"]][i]), sym, h["dir"], i))

        if self.first_entry <= k <= self.last_entry:
            self._new_heating(k, f, sc, ok, ctx, cands)
        self._admit(sorted(cands, key=lambda c: (-c[0], c[1])), k, k_now, f, sc, ctx, out)
        self._rows(k, out)

    def _process_frozen(self, k: int, k_now: int, out: TickOutput) -> None:
        """A slot with no usable market data (only reached when the session end is already due)."""
        self.st["heating"].clear()
        for sym in sorted(self.st["members"]):
            self._member_frozen(sym, k, k_now, out)
        self._rows(k, out)

    def _update_market(self, k: int, ctx: _Ctx, f: dict) -> None:
        pm, mk = self.p["market"], self.st["market"]
        zs6 = _f(f["zS6"])
        net = self._breadth(k, ctx, f)
        breadth = None if net is None else abs(net)
        z, b = (abs(zs6) if zs6 is not None else 0.0), (breadth if breadth is not None else 0.0)
        if mk["mode"] == "normal":
            if z >= pm["on_spy_z6"] or b >= pm["on_breadth"]:
                mk.update(mode="market", off_count=0)
        elif z < pm["off_spy_z6"] and b < pm["off_breadth"]:
            mk["off_count"] += 1
            if mk["off_count"] >= pm["off_bars"]:
                mk.update(mode="normal", off_count=0)
        else:
            mk["off_count"] = 0
        if mk["mode"] == "market":
            sign = np.sign(zs6) if zs6 else np.sign(net or 0.0)
            mk["dir"] = int(sign) if sign else (mk["dir"] or 1)
        else:
            mk["dir"] = 0
        mk.update(zS6=zs6, breadth=breadth, spy_chg_day_pct=_pct(f["rday"][ctx.spy]))

    def _breadth(self, k: int, ctx: _Ctx, f: dict) -> float | None:
        """Net share of eligible names up over the last 30 minutes (signed mean of sign(r6))."""
        a = max(k - 6, 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            r6 = np.log(self._ring_get(k) / self._ring_get(a))
        has = ctx.grid.first <= k
        r6[ctx.pix[has]] = f["r6"][has]
        valid = self.elig & np.isfinite(r6)
        if valid.sum() == 0 or valid.sum() < BREADTH_MIN_COVERAGE * self.elig.sum():
            return None
        return float(np.mean(np.sign(r6[valid])))

    def _confirms(self, h: dict, i: int, f: dict) -> bool:
        """Section 4.3 C1-C4 (C5 eligibility, freshness and halts are in `ok`)."""
        pc, d = self.p["confirm"], h["dir"]
        return bool(d * (f["C"][i] - h["heat_px"]) >= 0 and d * f["z6"][i] >= pc["z6"]
                    and f["rvol3"][i] >= pc["rvol3"] and d * f["dvwap"][i] > 0 and d * f["z3"][i] > 0)

    def _new_heating(self, k: int, f: dict, sc: dict, ok: np.ndarray, ctx: _Ctx, cands: list) -> None:
        p, st, mk = self.p, self.st, self.st["market"]
        d_pos = direction(f["z3"], f["z6"], p["entry"])
        bump = np.where((mk["mode"] == "market") & (d_pos == mk["dir"]), p["market"]["bump"], 0.0)
        with np.errstate(invalid="ignore"):
            gates = entry_gates(f, d_pos, p["entry"], bump) & ok
        taken = {c[1] for c in cands}
        fast_score = p["confirm"]["fast_path_score"]
        fast_rvol3 = p["confirm"].get("fast_path_rvol3", FAST_PATH_RVOL3)
        for i in np.flatnonzero(gates):
            sym, d = ctx.symbols[i], int(d_pos[i])
            if sym in st["members"] or sym in taken or self._reentry_blocked(sym, d, k, float(f["C"][i])):
                continue
            s = float(sc[d][i])
            if fast_score is not None and s >= fast_score and f["rvol3"][i] >= fast_rvol3:
                cands.append((s, sym, d, i))
            else:
                st["heating"][sym] = {"dir": d, "heat_px": float(f["C"][i]), "heat_slot": k,
                                      "name": self.names[ctx.pix[i]], "sector": self.sectors[ctx.pix[i]],
                                      "view": self._view(i, d, f, sc)}

    def _reentry_blocked(self, sym: str, d: int, k: int, px: float) -> bool:
        """Section 6.4 anti-flap rules."""
        mem = self.st["memory"].get(sym)
        if mem is None:
            return False
        r = self.p["reentry"]
        if mem["episodes"] >= r["max_episodes"]:
            return True
        if mem["last_exit_slot"] is None:
            return False
        since = k - mem["last_exit_slot"]
        if mem["last_exit_dir"] == d:
            if since < r["cool_same_bars"]:
                return True
            if r["require_new_peak"] and mem["last_peak"] is not None and d * (px - mem["last_peak"]) <= 0:
                return True
        elif mem["last_exit_dir"] == -d and since < r["cool_opp_bars"]:
            return True
        return False

    # ------------------------------------------------------------------ members
    def _member_step(self, sym: str, i: int, k: int, k_now: int, f: dict, sc: dict, ctx: _Ctx,
                     out: TickOutput) -> None:
        p, m = self.p, self.st["members"][sym]
        d, hx = m["dir"], p["hard_exit"]
        px = float(f["C"][i])
        m["spark"]["p"].append(px)
        if ctx.halts.halted[i, k]:
            m["state"] = "halted"
            m["last_ext"] += 1                   # the stall clock is frozen too (signal-model.md 2.3)
            run = int(ctx.halts.run[i, k])
            if k >= self.session_end:
                self._exit(sym, k, k_now, px, "SESSION_END", {}, out)
            elif run >= hx["halt_long_bars"]:
                self._exit(sym, k, k_now, px, "HALT_LONG", {"mins": run * 5}, out)
            return
        reopen = bool(ctx.halts.reopen[i, k])
        prov = bool(ctx.prov[i] == k)
        m["dwell"] += 1
        m["peak"] = max(m["peak"], float(f["H"][i])) if d > 0 else min(m["peak"], float(f["L"][i]))
        if not reopen:
            # DATA_STALE counts consecutive bars without a fresh print, so one missing or zero-volume bar is never
            # three stale scans. Any fresh print ends the run; a bad bar counts only while the source is healthy.
            bar_ok = bool(ctx.grid.present[i, k] and ctx.grid.v[i, k] > 0)
            m["stale"] = 0 if bar_ok else m["stale"] + (0 if ctx.degraded else 1)
        if extreme(f, d)[i]:
            m["last_ext"] = k
        m["view"] = self._view(i, d, f, sc)
        m["last_bar_slot"] = self._last_bar(ctx, i, k)
        reason, info = self._exit_reason(m, i, k, px, f, reopen, no_dry=ctx.degraded or prov, no_stall=prov)
        if reason:
            self._exit(sym, k, k_now, px, reason, info, out)
        else:
            m["state"] = "cooling" if m["soft"] else "racing"

    def _exit_reason(self, m: dict, i: int, k: int, px: float, f: dict, reopen: bool, *, no_dry: bool = False,
                     no_stall: bool = False) -> tuple[str | None, dict]:
        """Section 6: the first matching rule wins. On reopen bars only REVERSAL and GIVEBACK apply.

        `no_dry` / `no_stall`: that soft rule reads data that cannot be trusted at this slot (fallback volume,
        a bar still being revised). It neither counts a soft-fail nor clears the ones already counted."""
        hx, hold, d = self.p["hard_exit"], self.p["hold"], m["dir"]
        z3, z6 = d * float(f["z3"][i]), d * float(f["z6"][i])
        if k >= self.session_end:
            return "SESSION_END", {}
        if m["stale"] >= hx["stale_bars"]:
            return "DATA_STALE", {"n": m["stale"]}
        move = d * (m["peak"] - m["base"])
        gb = d * (m["peak"] - px) / move if move > 0 else 0.0
        if z3 <= -hx["reversal_z3"]:
            return "REVERSAL", {}
        if gb >= hx["giveback"]:
            return "GIVEBACK", {"gb": 100.0 * gb}
        if reopen:
            return None, {}
        if d * f["dvwap"][i] < -hx["vwap_cross"]:
            return "VWAP_CROSS", {}
        fade = z6 < hold["fade_z6"] and z3 < hold["fade_z3"]
        dry = bool(f["rvol3"][i] < hold["dry_rvol3"])
        stall = (k - m["last_ext"]) >= hold["stall_bars"] and z6 < hold["stall_z6"]
        unsure = (dry and no_dry) or (stall and no_stall)
        dry, stall = dry and not no_dry, stall and not no_stall
        if not (fade or dry or stall):
            if not unsure:
                m["soft"] = 0
            return None, {}
        m["soft"] += 1
        if m["dwell"] >= hold["min_dwell"] and m["soft"] >= hold["soft_fails"]:
            if fade:
                return "FADE", {}
            if dry:
                return "DRY", {}
            return "STALL", {"mins": 5 * (k - m["last_ext"])}
        return None, {}

    @staticmethod
    def _last_bar(ctx: _Ctx, i: int, k: int) -> int:
        printed = np.flatnonzero(ctx.grid.present[i, :k + 1])
        return int(printed[-1]) if printed.size else k

    def _member_frozen(self, sym: str, k: int, k_now: int, out: TickOutput) -> None:
        """No bars for a member this slot (fetch gap): nothing changes, and missing data never evicts."""
        m = self.st["members"][sym]
        if k >= self.session_end:
            self._exit(sym, k, k_now, m["view"]["price"], "SESSION_END", {}, out)
        else:
            m["spark"]["p"].append(m["view"]["price"])

    def _exit(self, sym: str, k: int, k_now: int, px: float, reason: str, info: dict, out: TickOutput) -> None:
        st = self.st
        m = st["members"].pop(sym)
        mem = st["memory"][sym]
        mem.update(last_exit_slot=k, last_exit_dir=m["dir"], last_peak=m["peak"])
        st["counts"]["exited"] += 1
        v = m["view"]
        detail = EXIT_TEXT[reason].format(
            z30=v["z30"] or 0.0, z15=v["z15"] or 0.0, rvol=v["rvol"] or 0.0,
            side="high" if m["dir"] > 0 else "low", vside="below" if m["dir"] > 0 else "above",
            **{"mins": 0, "gb": 0.0, "n": 0, "by": "", **info})
        move = _pct(math.log(px / m["entry_px"]))
        held = 5 * (k - m["entry_slot"])
        out.events.append(self._event(sym, k, k_now, "EXIT", m, px, v["intensity"], reason, detail,
                                      held, move, v["signals"]))
        st["exits"].insert(0, {"ticker": sym, "name": self._label(sym, m)[0], "direction": _dir_word(m["dir"]),
                               "entered_at": iso(self.session.slot_start(m["entry_slot"]) + 300),
                               "exited_at": iso(self.session.slot_start(k) + 300), "minutes_on_radar": held,
                               "move_since_entry_pct": move, "exit_reason": reason, "exit_detail": detail})
        del st["exits"][RECENT_EXITS_MAX:]

    # ------------------------------------------------------------------ admission (section 7)
    def _admit(self, cands: list, k: int, k_now: int, f: dict, sc: dict, ctx: _Ctx, out: TickOutput) -> None:
        p, st, mk = self.p, self.st, self.st["market"]
        caps = p["caps"]
        admitted = admitted_mkt = 0
        for s, sym, d, i in cands:
            with_market = mk["mode"] == "market" and d == mk["dir"]
            if admitted >= caps["max_new_per_tick"]:
                continue
            if with_market and admitted_mkt >= p["market"]["max_new_mkt_dir"]:
                continue
            sector = self.sectors[ctx.pix[i]]
            if sector != "Unknown" and sum(1 for x, m in st["members"].items() if m["dir"] == d
                                           and self._label(x, m)[1] == sector) >= caps["max_per_sector_dir"]:
                ban = st["banners"].setdefault(f"{sector}|{d}", {"sector": sector, "dir": d, "tickers": {}})
                ban["tickers"][sym] = k
                continue
            if len(st["members"]) >= caps["max_members"]:
                pool = [(m["view"]["score"], x) for x, m in st["members"].items()
                        if m["dwell"] >= p["hold"]["min_dwell"]]
                if not pool:
                    continue
                low, low_sym = min(pool)
                if s < low + caps["displace_margin"]:
                    continue
                j = ctx.row.get(low_sym)
                low_px = float(f["C"][j]) if j is not None and ctx.grid.first[j] <= k \
                    else st["members"][low_sym]["view"]["price"]
                self._exit(low_sym, k, k_now, low_px, "DISPLACED", {"by": sym}, out)
            self._enter(sym, i, d, s, k, k_now, f, sc, ctx, out)
            admitted += 1
            admitted_mkt += with_market
        for key in list(st["banners"]):                 # names held back beyond the cap, never current members
            ban = st["banners"][key]
            ban["tickers"] = {t: j for t, j in ban["tickers"].items()
                              if j > k - BANNER_TTL_SLOTS and t not in st["members"]}
            if not ban["tickers"]:
                del st["banners"][key]

    def _enter(self, sym: str, i: int, d: int, s: float, k: int, k_now: int, f: dict, sc: dict, ctx: _Ctx,
               out: TickOutput) -> None:
        st = self.st
        c = ctx.grid.c[i]
        a = max(k - SPARK_PRE_SLOTS, 0)
        window = c[a:k + 1]
        mem = st["memory"].setdefault(sym, {"episodes": 0, "last_exit_slot": None, "last_exit_dir": 0,
                                            "last_peak": None})
        mem["episodes"] += 1
        px = float(c[k])
        m = {"dir": d, "entry_slot": k, "entry_px": px, "name": self.names[ctx.pix[i]],
             "sector": self.sectors[ctx.pix[i]],
             "base": float(window.min() if d > 0 else window.max()), "peak": px, "last_ext": k,
             "dwell": 0, "soft": 0, "stale": 0, "episode": mem["episodes"], "late": k_now - k > 2,
             "state": "racing", "view": self._view(i, d, f, sc), "last_bar_slot": self._last_bar(ctx, i, k),
             "spark": {"t0_slot": a, "entry_i": k - a, "p": [float(x) for x in window]}}
        st["members"][sym] = m
        st["counts"]["entered"] += 1
        out.events.append(self._event(sym, k, k_now, "ENTER", m, px, s, "ENTRY", "; ".join(m["view"]["reasons"]),
                                      None, None, m["view"]["signals"]))

    # ------------------------------------------------------------------ views and rows
    def _view(self, i: int, d: int, f: dict, sc: dict) -> dict:
        """Display values of one symbol at the slot (never used for decisions, except `score`)."""
        s = _f(sc[d][i]) or 0.0
        vwap = _f(f["vwap"][i])
        return {"price": float(f["C"][i]), "chg_5m": _pct(f["r1"][i]), "chg_15m": _pct(f["r3"][i]),
                "chg_30m": _pct(f["r6"][i]), "chg_day": _pct(f["rday"][i]), "rvol": _r(f["rvol3"][i], 2),
                "rvol_day": _r(f["rvolc"][i], 2),
                "vwap_dist": round((float(f["C"][i]) / vwap - 1.0) * 100.0, 2) if vwap else None,
                "z15": _r(f["z3"][i], 2), "z30": _r(f["z6"][i], 2), "zday": _r(f["zday"][i], 2),
                "intensity": _r(s, 1) or 0.0, "score": s, "reasons": self._reasons(i, d, f),
                "signals": {key: _r(f[key][i], 3) for key in SIGNAL_KEYS}, "vol_5m": int(f["V"][i])}

    def _reasons(self, i: int, d: int, f: dict) -> list[str]:
        e = self.p["entry"]
        z3, z6 = float(f["z3"][i]), float(f["z6"][i])
        thrust = (f"{z3:+.1f}σ vs market in 15 min" if d * z3 / e["z3"] >= d * z6 / e["z6"]
                  else f"{z6:+.1f}σ vs market in 30 min")
        where = "above VWAP" if f["dvwap"][i] > 0 else "below VWAP"
        if extreme(f, d)[i]:
            where = ("new high of day, " if d > 0 else "new low of day, ") + where
        return [thrust, f"volume {float(f['rvol3'][i]):.1f}× normal for this time", where]

    def _event(self, sym: str, k: int, k_now: int, kind: str, m: dict, px: float, intensity: float, reason: str,
               detail: str, held: int | None, move: float | None, signals: dict) -> dict:
        ts = self.session.slot_start(k) + 300
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%dT%H%MZ")
        return {"v": 1, "id": f"{stamp}-{sym}-{kind}-{m['episode']}", "ts": iso(ts), "session": self.day, "slot": k,
                "ticker": sym, "type": kind, "dir": _dir_word(m["dir"]), "price": round(px, 4),
                "intensity": _r(intensity, 1), "reason": reason, "detail": detail[:160], "episode": m["episode"],
                "held_min": held, "move_since_entry_pct": move, "late": k_now - k > 2, "signals": signals,
                "params_version": self.p["params_version"]}

    def _rows(self, k: int, out: TickOutput) -> None:
        tick = iso(self.session.slot_start(k) + 300)
        st = self.st
        for role, table in (("member", st["members"]), ("heating", st["heating"])):
            for sym in sorted(table):
                x = table[sym]
                v = x["view"]
                move = _pct(math.log(v["price"] / x["entry_px"])) if role == "member" else None
                out.member_rows.append({
                    "v": 1, "tick": tick, "session": self.day, "slot": k, "ticker": sym, "role": role,
                    "dir": _dir_word(x["dir"]), "state": x["state"] if role == "member" else "heating",
                    "price": round(v["price"], 4), "chg_day_pct": v["chg_day"], "chg_5m_pct": v["chg_5m"],
                    "move_since_entry_pct": move, "vol_5m": v["vol_5m"], "intensity": v["intensity"],
                    "signals": v["signals"]})

    # ------------------------------------------------------------------ snapshot (state.json shapes)
    @staticmethod
    def _giveback_pct(m: dict, px: float) -> float:
        """The GIVEBACK rule's measure in percent, as _exit_reason computes it (0 before any progress)."""
        d = m["dir"]
        move = d * (m["peak"] - m["base"])
        return round(100.0 * d * (m["peak"] - px) / move, 1) if move > 0 else 0.0

    def snapshot(self) -> dict:
        st, ss = self.st, self.session
        last = st["last_slot"]
        members = []
        for sym, m in st["members"].items():
            v, sp = m["view"], m["spark"]
            name, sector = self._label(sym, m)
            members.append({
                "ticker": sym, "name": name, "sector": sector,
                "direction": _dir_word(m["dir"]), "state": m["state"], "late": m["late"],
                "entered_at": iso(ss.slot_start(m["entry_slot"]) + 300), "entry_price": round(m["entry_px"], 4),
                "last_price": round(v["price"], 4), "last_bar_at": iso(ss.slot_start(m["last_bar_slot"]) + 300),
                "minutes_on_radar": 5 * (last - m["entry_slot"]),
                "move_since_entry_pct": _pct(math.log(v["price"] / m["entry_px"])),
                "peak_since_entry_pct": _pct(math.log(m["peak"] / m["entry_px"])),
                "chg_5m_pct": v["chg_5m"], "chg_15m_pct": v["chg_15m"], "chg_30m_pct": v["chg_30m"],
                "chg_day_pct": v["chg_day"], "rvol": v["rvol"], "rvol_day": v["rvol_day"],
                "vwap_dist_pct": v["vwap_dist"], "z15": v["z15"], "z30": v["z30"], "zday": v["zday"],
                "intensity": round(v["intensity"]), "reasons": v["reasons"], "soft_fails": m["soft"],
                "episode": m["episode"],
                # Display only (the exit-watch gauges): the share of the move from its base that was given
                # back (the GIVEBACK rule, section 6) and the minutes since the last new high/low (STALL).
                "giveback_pct": self._giveback_pct(m, v["price"]),
                "mins_since_extreme": 5 * (last - m["last_ext"]),
                "spark": {"t0": iso(ss.slot_start(sp["t0_slot"]) + 300), "step_s": 300, "entry_i": sp["entry_i"],
                          "p": [round(x, 4) for x in sp["p"]]}})
        members.sort(key=lambda x: (-x["intensity"], x["ticker"]))
        heating = [{"ticker": sym, "name": self._label(sym, h)[0], "direction": _dir_word(h["dir"]),
                    "since": iso(ss.slot_start(h["heat_slot"]) + 300), "price": round(h["view"]["price"], 4),
                    "chg_day_pct": h["view"]["chg_day"], "intensity": round(h["view"]["intensity"]),
                    "reasons": h["view"]["reasons"]} for sym, h in sorted(st["heating"].items())]
        banners = [{"sector": b["sector"], "direction": _dir_word(b["dir"]), "count": len(b["tickers"]),
                    "tickers": sorted(b["tickers"])} for _, b in sorted(st["banners"].items())]
        mk = st["market"]
        market = {"mode": mk["mode"], "dir": _dir_word(mk["dir"]) if mk["dir"] else None,
                  "spy_chg_day_pct": mk["spy_chg_day_pct"], "spy_z30": _r(mk["zS6"], 2) if mk["zS6"] is not None
                  else None, "breadth30": _r(mk["breadth"], 3) if mk["breadth"] is not None else None}
        counts = {"universe": int(self.elig.sum()), "stage_b": st["counts"]["stage_b"], "members": len(members),
                  "heating": len(heating), "entered_today": st["counts"]["entered"],
                  "exited_today": st["counts"]["exited"]}
        return {"members": members, "heating": heating, "recent_exits": copy.deepcopy(st["exits"]),
                "market": market, "sector_banners": banners, "counts": counts}
