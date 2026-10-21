"""Trend-day detector for the Momentum Radar ("Trending today", display only).

A trend day is a stock that grinds steadily one way from the open, usually on news, without the 15/30-minute
bursts the radar's entry engine waits for. At slot k (bar k is the last final bar, slot 0 = 09:30-09:35 ET)
the detector reads bars 0..k only, so it can run on every live tick and in the replay without lookahead.

Inputs are the arrays the radar already holds: the session grid (o, h, l, c, v as [symbol, slot]), the SPY row,
and per-symbol baselines from the pack (beta, sigma_d = daily log-return volatility, prior close, cumulative
time-of-day volume profile cvm[:, k]).

Everything is market-relative: the stock's log move minus beta times SPY's over the same span, in units of
the stock's own daily volatility. Plain-words meaning of each condition (direction d = +1 up, -1 down):

  big day      the move since the open is >= zopen sigma_d and the move since yesterday's close >= zday sigma_d
               (both against the market), so a gap that is merely holding does not count
  above VWAP   at least `vwap_share` of the bars since the opening bar closed on the trend side of the
               session VWAP, and the last bar did too
  steady       path efficiency since the open (net move / sum of 5-minute moves) >= `eff`
  still going  the market-relative regression slope over the last `slope_bars` bars points the same way, and
               the session high (low) was set within the last `ext_bars` bars
  volume       session volume >= `rvolc` x normal for this time of day

New flags from 09:50 to 13:30 ET with the live "earlier" set (slots 3..47; 10:00 for the other sets). Exit (the stock leaves the list and does not re-enter that day):
four consecutive 5-minute closes on the wrong side of the session VWAP. An optional giveback exit (the
market-relative move since the open falls below (1 - giveback) of its best) is off by default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

EPS = 1e-12

# Threshold sets compared in the backtests (2026-10-15: 40 sessions 2026-07-31..09-25; 2026-10-21: 54 sessions
# 2026-07-31..10-15; see radar/docs/trend-day.md); RECOMMENDED is the live set.
_COMMON = {"first_slot": 5, "last_slot": 47, "slope_bars": 9, "slope_z": 0.0, "ext_bars": 6}
THRESHOLDS: dict[str, dict] = {
    # Lists grinders ~15 minutes sooner (median 10:20 vs 10:25; 29% vs 24% of big grinders by 10:30) at the same
    # quality per flag, with ~15 names a day instead of ~10. The prior-close condition (zday) was what held
    # names back: CRL 2026-10-21 is listed at 10:15 instead of 11:15. Display only, like every set.
    "earlier":  {**_COMMON, "first_slot": 3, "zopen": 1.5, "zday": 1.5, "vwap_share": 0.90, "eff": 0.45, "rvolc": 1.5},
    "early":    {**_COMMON, "zopen": 1.5, "zday": 2.0, "vwap_share": 0.90, "eff": 0.45, "rvolc": 1.5},
    "balanced": {**_COMMON, "zopen": 2.0, "zday": 2.0, "vwap_share": 0.90, "eff": 0.35, "rvolc": 1.5},
    "strict":   {**_COMMON, "zopen": 2.5, "zday": 2.5, "vwap_share": 0.90, "eff": 0.45, "rvolc": 1.5},
}
RECOMMENDED = "earlier"
# Exit: 4 consecutive closes (20 min) on the wrong side of VWAP. A giveback rule (fraction of the best move since
# the open) is supported but off: in the backtest every giveback level exited names that then kept trending.
EXIT = {"vwap_bars": 4, "giveback": None}


def trend_features(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray, *, k: int,
                   spy: int, beta: np.ndarray, sigd: np.ndarray, pc: np.ndarray, cvm_k: np.ndarray,
                   slope_bars: int = 9) -> dict[str, np.ndarray]:
    """Per-symbol trend features at slot k from bars 0..k (arrays [S, >= k+1]; filled grid, no NaN closes).

    Returns arrays of length S: dir (+1/-1), zopen, zday, vwap_share, on_vwap, eff, slope_z, since_ext,
    rvolc, vwap, best (best market-relative log move since the open in direction dir, at bar closes),
    rel_open (market-relative log move since the open, signed), vside (sign of close - VWAP)."""
    C, H, L, V = c[:, :k + 1], h[:, :k + 1], l[:, :k + 1], v[:, :k + 1]
    o0 = o[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        lc, lspy = np.log(C), np.log(C[spy])
        x = lc - beta[:, None] * lspy[None, :]                 # market-relative log price path
        x_open = np.log(o0) - beta * math.log(o[spy, 0])
        zopen = (x[:, k] - x_open) / (sigd + EPS)
        rday = lc[:, k] - np.log(pc)
        zday = (rday - beta * rday[spy]) / (sigd + EPS)
        d = np.where(zopen >= 0, 1, -1)

        tp = (H + L + C) / 3.0
        cv = np.cumsum(V, axis=1)
        vwap = np.cumsum(tp * V, axis=1) / cv
        vwap = np.where(cv > 0, vwap, C)                         # no volume yet: VWAP undefined, use the close
        side = d[:, None] * (C - vwap) > 0
        vwap_share = side[:, 1:].mean(axis=1) if k >= 1 else side[:, 0].astype(float)
        on_vwap = side[:, k]

        steps = np.abs(np.diff(C, axis=1)).sum(axis=1) + np.abs(C[:, 0] - o0)
        eff = np.abs(C[:, k] - o0) / (steps + EPS)

        w = min(slope_bars, k + 1)
        if w >= 3:
            t = np.arange(w, dtype=float) - (w - 1) / 2.0
            seg = x[:, k - w + 1:k + 1]
            slope = (seg * t[None, :]).sum(axis=1) / (t * t).sum()
            slope_z = d * slope * (w - 1) / (sigd + EPS)        # fitted move over the window, sigma_d units
        else:
            slope_z = np.zeros(len(C))

        ext = np.where(d[:, None] > 0, H, -L)                  # session extreme in the trend direction
        since_ext = np.argmax(ext[:, ::-1] >= ext.max(axis=1, keepdims=True) - EPS, axis=1)   # bars since the last one

        rvolc = cv[:, k] / cvm_k
        best = np.max(d[:, None] * (x - x_open[:, None]), axis=1)
    return {"dir": d, "zopen": zopen, "zday": zday, "vwap_share": vwap_share, "on_vwap": on_vwap, "eff": eff,
            "slope_z": slope_z, "since_ext": since_ext, "rvolc": rvolc, "vwap": vwap[:, k], "best": best,
            "rel_open": x[:, k] - x_open, "vside": np.sign(C[:, k] - vwap[:, k])}


def trend_flags(f: dict[str, np.ndarray], k: int, th: dict) -> np.ndarray:
    """Symbols meeting every trend condition at slot k (bool [S]). Eligibility and fresh-data checks are the
    caller's (the radar's `ctx.eligible & f["fresh"]`)."""
    if not th["first_slot"] <= k <= th["last_slot"]:
        return np.zeros(len(f["dir"]), dtype=bool)
    d = f["dir"]
    with np.errstate(invalid="ignore"):
        return ((d * f["zopen"] >= th["zopen"]) & (d * f["zday"] >= th["zday"])
                & (f["vwap_share"] >= th["vwap_share"]) & f["on_vwap"] & (f["eff"] >= th["eff"])
                & (f["slope_z"] > th["slope_z"]) & (f["since_ext"] < th["ext_bars"]) & (f["rvolc"] >= th["rvolc"]))


@dataclass
class TrendEntry:
    """One symbol on the "Trending today" list (state is plain types, so it serialises to JSON)."""
    dir: int
    flag_slot: int
    flag_price: float
    best: float                      # best market-relative move since the open in `dir`, log units
    wrong_side_run: int = 0
    exit_slot: int | None = None
    exit_reason: str | None = None


def trend_exit_reason(e: TrendEntry, rel_open: float, vside: float, ex: dict = EXIT) -> str | None:
    """Exit check for a listed symbol at the current slot (updates e.best and e.wrong_side_run).

    rel_open: market-relative log move since the open (signed); vside: sign of close - session VWAP.
    VWAP: `vwap_bars` consecutive closes not on the trend side of VWAP.
    GIVEBACK (only when ex["giveback"] is not None): the move since the open is down to (1 - giveback) of its best."""
    move = e.dir * rel_open
    e.best = max(e.best, move)
    e.wrong_side_run = 0 if e.dir * vside > 0 else e.wrong_side_run + 1
    if e.wrong_side_run >= ex["vwap_bars"]:
        return "VWAP"
    if ex.get("giveback") is not None and e.best > 0 and move < (1.0 - ex["giveback"]) * e.best:
        return "GIVEBACK"
    return None


def update_trend_list(listed: dict[str, TrendEntry], symbols: list[str], f: dict[str, np.ndarray], k: int,
                      flags: np.ndarray, close_k: np.ndarray, ok: np.ndarray, ex: dict = EXIT) -> list[str]:
    """One slot of the list: exits for listed names, then new flags (a name enters at most once a day).

    `ok` masks rows whose data is usable this slot (eligible, fresh); `listed` holds every name flagged today,
    exited ones included. Returns the symbols that entered at this slot."""
    for i, s in enumerate(symbols):
        e = listed.get(s)
        if e is not None and e.exit_slot is None and ok[i] and e.flag_slot < k:
            why = trend_exit_reason(e, float(f["rel_open"][i]), float(f["vside"][i]), ex)
            if why:
                e.exit_slot, e.exit_reason = k, why
    new = []
    for i in np.flatnonzero(flags & ok):
        s = symbols[i]
        if s not in listed:
            listed[s] = TrendEntry(int(f["dir"][i]), k, float(close_k[i]), float(f["best"][i]))
            new.append(s)
    return new
