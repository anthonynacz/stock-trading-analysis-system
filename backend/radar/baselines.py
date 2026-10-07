"""Daily baseline pack (signal-model.md sections 1.3 and 3): built from complete sessions strictly before
the target session (half days excluded), so a pack never looks ahead."""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np

from radar.calendar_nyse import Session, previous_sessions, session_for
from radar.config import REFERENCE_SYMBOLS
from radar.features import EPS, NS, fill_slots
from radar.types import Bars, DailyBars

PACK_SCHEMA = 1


def _num(x) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


def _nan(x) -> float:
    return math.nan if x is None else float(x)


@dataclass
class SymbolBaseline:
    symbol: str
    name: str | None
    sector: str | None
    sigma5: float
    beta: float
    vm: np.ndarray
    cvm: np.ndarray
    sigma_d: float
    adv20_usd: float
    medbar_usd: float
    missing_share: float
    n_base: int
    prev_close: float | None
    eligible: bool
    ineligible_reason: str | None

    def to_json(self) -> dict:
        return {"name": self.name, "sector": self.sector, "sigma5": _num(self.sigma5), "beta": _num(self.beta),
                "vm": [int(x) for x in self.vm], "cvm": [int(x) for x in self.cvm],
                "sigma_d": _num(self.sigma_d), "adv20_usd": _num(self.adv20_usd),
                "medbar_usd": _num(self.medbar_usd), "missing_share": float(self.missing_share),
                "n_base": int(self.n_base), "prev_close": _num(self.prev_close), "eligible": bool(self.eligible),
                "ineligible_reason": self.ineligible_reason}

    @classmethod
    def from_json(cls, symbol: str, d: dict) -> SymbolBaseline:
        return cls(symbol, d["name"], d["sector"], _nan(d["sigma5"]), _nan(d["beta"]),
                   np.asarray(d["vm"], dtype=np.float64), np.asarray(d["cvm"], dtype=np.float64),
                   _nan(d["sigma_d"]), _nan(d["adv20_usd"]), _nan(d["medbar_usd"]), float(d["missing_share"]),
                   int(d["n_base"]), d["prev_close"], bool(d["eligible"]), d["ineligible_reason"])


@dataclass
class BaselinePack:
    asof: str                    # session date the pack is for (YYYY-MM-DD)
    params_version: str
    tod_m: np.ndarray            # time-of-day multiplier, 78 slots
    sigma5_spy: float
    symbols: dict[str, SymbolBaseline]
    spy_lr: dict[str, list[float]] = field(default_factory=dict)   # base session -> SPY slot 1..77 log returns (for extend_pack's beta)

    def to_json(self) -> dict:
        return {"schema": PACK_SCHEMA, "asof": self.asof, "params_version": self.params_version,
                "tod_m": [float(x) for x in self.tod_m], "sigma5_spy": float(self.sigma5_spy),
                "spy_lr": self.spy_lr, "symbols": {s: b.to_json() for s, b in sorted(self.symbols.items())}}

    @classmethod
    def from_json(cls, d: dict) -> BaselinePack:
        return cls(d["asof"], d["params_version"], np.asarray(d["tod_m"], dtype=np.float64), float(d["sigma5_spy"]),
                   {s: SymbolBaseline.from_json(s, b) for s, b in d["symbols"].items()},
                   {k: list(v) for k, v in (d.get("spy_lr") or {}).items()})


def base_sessions(session: Session, n: int) -> list[Session]:
    """The n complete (non half-day) sessions before `session`, oldest first."""
    out: list[Session] = []
    before = session.day
    while len(out) < n:
        batch = previous_sessions(before, n)
        out = [s for s in batch if not s.early_close] + out
        before = batch[0].day
    return out[-n:]


def _slot_grid(bars5: dict[str, Bars], symbols: list[str], sessions: list[Session]):
    """C, V, present as [symbol, session, slot]; missing slots filled flat, sessions without data NaN."""
    shape = (len(symbols), len(sessions), NS)
    c = np.full(shape, np.nan)
    v = np.zeros(shape)
    opens = np.array([s.open_epoch for s in sessions], dtype=np.int64)
    for i, sym in enumerate(symbols):
        b = bars5.get(sym)
        if b is None or len(b) == 0:
            continue
        ts = np.asarray(b.ts, dtype=np.int64)
        di = np.searchsorted(opens, ts, side="right") - 1
        off = ts - opens[np.maximum(di, 0)]
        j = off // 300
        ok = (di >= 0) & (off >= 0) & (off % 300 == 0) & (j < NS) & np.isfinite(b.c) & (b.c > 0)
        c[i, di[ok], j[ok]] = b.c[ok]
        v[i, di[ok], j[ok]] = np.nan_to_num(np.asarray(b.v, dtype=np.float64)[ok])
    present = ~np.isnan(c)
    c = fill_slots(c, present)
    v[~present.any(axis=2)] = np.nan
    return c, v, present


def _tod_multiplier(lr: np.ndarray, clamp: list[float]) -> np.ndarray:
    absr = np.abs(lr)
    rel = absr / (np.nanmedian(absr, axis=2, keepdims=True) + EPS)
    p = np.nanmedian(np.nanmedian(rel, axis=0), axis=0)          # symbols, then sessions -> slots 1..77
    m = np.empty(NS)
    m[1:] = np.clip(p / np.nanmedian(p), clamp[0], clamp[1])
    m[0] = m[1]
    return m


def _intraday_stats(c: np.ndarray, v: np.ndarray, present: np.ndarray, lr: np.ndarray, spy_lr: np.ndarray,
                    m: np.ndarray, bp: dict, spy_row: int | None):
    """Per-symbol beta, sigma5, vm, cvm, medbar$, missing share and n_base over the base sessions."""
    mm = m[1:]
    x = lr / mm
    y = (spy_lr / mm)[None]
    ok = np.isfinite(x) & np.isfinite(y)
    x0, y0 = np.where(ok, x, 0.0), np.where(ok, y, 0.0)
    braw = (x0 * y0).sum(axis=(1, 2)) / ((y0 ** 2).sum(axis=(1, 2)) + EPS)
    shrink = bp["beta_shrink"]
    beta = np.clip(shrink * braw + (1.0 - shrink), *bp["beta_clamp"])
    res = np.abs(x - beta[:, None, None] * y).reshape(len(c), -1)
    sig5 = 1.4826 * np.nanmedian(res, axis=1)
    if spy_row is not None:
        beta[spy_row] = 1.0
        sig5[spy_row] = 1.4826 * np.nanmedian(np.abs(x[spy_row]))
    sig5 = np.maximum(sig5, bp["sigma5_floor"])
    vm = np.nanmedian(v, axis=1)
    vm_s = vm.copy()
    vm_s[:, 1:-1] = (vm[:, :-2] + vm[:, 1:-1] + vm[:, 2:]) / 3.0    # auction slots 0 and 77 are not smoothed
    vm_s = np.round(np.nan_to_num(np.maximum(vm_s, 1.0), nan=1.0))
    cvm = np.round(np.nan_to_num(np.maximum(np.nanmedian(np.cumsum(v, axis=2), axis=1), 1.0), nan=1.0))
    medbar = np.nanmedian((v[:, :, 1:-1] * c[:, :, 1:-1]).reshape(len(c), -1), axis=1)
    missing = 1.0 - present.mean(axis=(1, 2))
    n_base = present.any(axis=2).sum(axis=1)
    return beta, sig5, vm_s, cvm, medbar, missing, n_base


def _daily_stats(d: DailyBars | None, session: Session, prev_day: date, bp: dict):
    """(prev_close, sigma_d, adv20$) from daily bars strictly before the session.

    prev_close is None unless the last bar is the previous session's (a stale close would fake a gap)."""
    if d is None or len(d.day) == 0:
        return None, math.nan, math.nan
    days = np.asarray(d.day, dtype="datetime64[D]")
    keep = days < np.datetime64(session.day)
    close, vol, days = np.asarray(d.c, dtype=np.float64)[keep], np.asarray(d.v, dtype=np.float64)[keep], days[keep]
    if len(close) == 0:
        return None, math.nan, math.nan
    prev_close = float(close[-1]) if days[-1] == np.datetime64(prev_day) and close[-1] > 0 else None
    nd, n = bp["daily_sessions"], bp["sessions"]
    with np.errstate(divide="ignore", invalid="ignore"):
        sigd = float(np.std(np.diff(np.log(close[-nd:])), ddof=1)) if len(close) >= nd else math.nan
    adv = float(np.median(close[-n:] * vol[-n:])) if len(close) >= n else math.nan
    return prev_close, sigd, adv


def _eligibility(sym: str, meta: dict, prev_close, sigd, adv, medbar, missing, n_base, u: dict):
    checks = [
        (sym in REFERENCE_SYMBOLS, "reference index"),
        (bool(meta.get("is_etf")), "ETF"),
        (n_base < u["min_baseline_sessions"], "short 5-minute history"),
        (not missing <= u["missing_share_max"], "gappy 5-minute data"),
        (prev_close is None, "no prior close"),
        (prev_close is not None and prev_close < u["price_min"], "price under $5"),
        (not math.isfinite(sigd), "no daily history"),
        (not adv >= u["adv20_usd_min"], "low dollar volume"),
        (not medbar >= u["median_bar_usd_min"], "thin 5-minute bars"),
    ]
    reason = next((why for failed, why in checks if failed), None)
    return reason is None, reason


def _log_returns(c: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.log(c[:, :, 1:] / c[:, :, :-1])


def _symbol_baselines(symbols: list[str], grid: tuple, lr: np.ndarray, daily: dict[str, DailyBars],
                      meta: dict[str, dict], session: Session, spy_lr: np.ndarray, m: np.ndarray,
                      params: dict) -> dict[str, SymbolBaseline]:
    c, v, present = grid
    spy_row = symbols.index("SPY") if "SPY" in symbols else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(divide="ignore", invalid="ignore"):
            beta, sig5, vm, cvm, medbar, missing, n_base = _intraday_stats(
                c, v, present, lr, spy_lr, m, params["baseline"], spy_row)
    prev_day = previous_sessions(session.day, 1)[0].day
    out = {}
    for i, sym in enumerate(symbols):
        info = meta.get(sym, {})
        pc, sigd, adv = _daily_stats(daily.get(sym), session, prev_day, params["baseline"])
        ok, why = _eligibility(sym, info, pc, sigd, adv, medbar[i], missing[i], int(n_base[i]), params["universe"])
        out[sym] = SymbolBaseline(sym, info.get("name"), info.get("sector"), float(sig5[i]), float(beta[i]),
                                  vm[i], cvm[i], sigd, adv, float(medbar[i]), float(missing[i]), int(n_base[i]),
                                  pc, ok, why)
    return out


def build_pack(session: Session, bars5: dict[str, Bars], daily: dict[str, DailyBars],
               meta: dict[str, dict], params: dict) -> BaselinePack:
    """Bars may span any range: only the base sessions before `session` are read.

    Sessions without any SPY bar (outside the fetched range) are left out, so missing_share and n_base
    count only sessions the source covers. A pack without SPY's prior-session close is refused (ValueError):
    every day move is measured against SPY's, so such a pack would block every entry all session."""
    if "SPY" not in bars5:
        raise ValueError("baseline pack needs SPY 5-minute bars")
    symbols = sorted(bars5)
    sessions = base_sessions(session, params["baseline"]["sessions"])
    c, v, present = _slot_grid(bars5, symbols, sessions)
    spy = symbols.index("SPY")
    have = present[spy].any(axis=1)
    if not have.any():
        raise ValueError("no SPY bars in the baseline window")
    sessions = [s for s, h in zip(sessions, have) if h]
    grid = (c[:, have], v[:, have], present[:, have])
    lr = _log_returns(grid[0])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = _tod_multiplier(lr, params["baseline"]["tod_clamp"])
    syms = _symbol_baselines(symbols, grid, lr, daily, meta, session, lr[spy], m, params)
    if syms["SPY"].prev_close is None:
        raise ValueError("baseline pack needs SPY's prior-session close (daily bars missing or stale)")
    return BaselinePack(session.day.isoformat(), params["params_version"], m, syms["SPY"].sigma5, syms,
                        {s.day.isoformat(): [float(x) for x in row] for s, row in zip(sessions, lr[spy])})


def extend_pack(pack: BaselinePack, session: Session, bars5: dict[str, Bars],
                daily: dict[str, DailyBars], meta: dict[str, dict], params: dict) -> BaselinePack:
    """Adds baselines for symbols not yet in the pack, over the pack's base sessions, profile and SPY series.

    bars5 needs only the new symbols; symbols already in the pack are left unchanged."""
    new = sorted(s for s in bars5 if s not in pack.symbols)
    if not new:
        return pack
    if not pack.spy_lr:
        raise ValueError("pack has no SPY base returns; rebuild it with build_pack")
    days = sorted(pack.spy_lr)
    sessions = [session_for(date.fromisoformat(d)) for d in days]
    grid = _slot_grid(bars5, new, sessions)
    spy_lr = np.array([pack.spy_lr[d] for d in days], dtype=np.float64)
    syms = _symbol_baselines(new, grid, _log_returns(grid[0]), daily, meta, session, spy_lr, pack.tod_m, params)
    return replace(pack, symbols={**pack.symbols, **syms})
