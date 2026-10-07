"""Momentum Radar features (signal-model.md section 2), vectorised over symbols with numpy.

Grid convention: regular session only; slot j is the bar starting at open + 5*j minutes. "At slot k" means
bar k is the last final bar. Returns never reach back before C[0] (the close of the opening bar), so the
opening print is an anchor, never part of a thrust. Everything at slot k reads bars 0..k only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from radar.types import Bars

NS = 78                      # slots on a normal day; half days use slots 0..41 of the same baseline arrays
EPS = 1e-12
SPLIT_SIGMA = 8.0            # split guard (signal-model.md section 3)
SPLIT_TOL = 0.03
SPLIT_RATIOS = np.array([1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2.0, 3.0, 4.0, 5.0, 10.0])


@dataclass
class Grid:
    """Regular-session bars on the slot grid, [symbol, slot]. Missing slots are filled flat at the previous
    close with zero volume (leading gaps take the first present close); `present` marks real bars."""
    symbols: list[str]
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    present: np.ndarray
    first: np.ndarray        # first present slot per symbol, n_slots when the symbol has no bar


def fill_slots(c: np.ndarray, present: np.ndarray) -> np.ndarray:
    """Close carried into missing slots along the last axis (first present close for leading gaps).

    Rows without any present slot stay NaN."""
    n = c.shape[-1]
    ar = np.arange(n)
    idx = np.where(present, ar, -1)
    np.maximum.accumulate(idx, axis=-1, out=idx)
    first = np.where(present.any(axis=-1), present.argmax(axis=-1), n - 1)
    idx = np.where(idx < 0, first[..., None], idx)
    return np.take_along_axis(c, idx, axis=-1)


def build_grid(bars: dict[str, Bars], symbols: list[str], open_epoch: int, n_slots: int) -> Grid:
    shape = (len(symbols), n_slots)
    o, h, l, c = (np.full(shape, np.nan) for _ in range(4))
    v = np.zeros(shape)
    have = [(i, bars[s]) for i, s in enumerate(symbols) if s in bars and len(bars[s])]
    if have:
        cat = lambda attr: np.concatenate([np.asarray(getattr(b, attr), dtype=np.float64) for _, b in have])
        rows = np.concatenate([np.full(len(b), i) for i, b in have])
        off = np.concatenate([np.asarray(b.ts, dtype=np.int64) for _, b in have]) - open_epoch
        j = off // 300
        close = cat("c")
        ok = (off >= 0) & (off % 300 == 0) & (j < n_slots) & np.isfinite(close) & (close > 0)
        r, j = rows[ok], j[ok]
        o[r, j], h[r, j], l[r, j], c[r, j] = cat("o")[ok], cat("h")[ok], cat("l")[ok], close[ok]
        v[r, j] = np.nan_to_num(cat("v")[ok])
    present = ~np.isnan(c)
    filled = fill_slots(c, present)
    for arr in (o, h, l):
        np.copyto(arr, filled, where=~present)
        np.copyto(arr, filled, where=np.isnan(arr))
    first = np.where(present.any(axis=1), present.argmax(axis=1), n_slots)
    return Grid(list(symbols), o, h, l, filled, v, present, first)


def lin(x, a: float, b: float):
    return np.clip((x - a) / (b - a), 0.0, 1.0)


class SessionFeatures:
    """Section 2.1 features for every grid symbol at any slot k, using bars 0..k only.

    Baseline arrays are aligned with grid.symbols; m is the time-of-day multiplier for this session's slots
    and sigday_scale = sqrt(sum_{j=1..77} m_j^2) from the full-day profile.
    """

    def __init__(self, grid: Grid, *, spy: int, m: np.ndarray, beta: np.ndarray, sig5: np.ndarray,
                 sig5_spy: float, vm: np.ndarray, cvm: np.ndarray, sigd: np.ndarray, pc: np.ndarray,
                 sigday_scale: float):
        self.g, self.spy, self.m = grid, spy, m
        self.beta, self.sig, self.sig_spy = beta, sig5, sig5_spy
        self.vm, self.cvm, self.sigd, self.pc = vm, cvm, sigd, pc
        self.sig_day = sig5 * sigday_scale
        self.tp = (grid.h + grid.l + grid.c) / 3.0

    def _scale(self, a: int, k: int) -> float:
        return float(np.sqrt(np.sum(self.m[a + 1:k + 1] ** 2))) if k > a else np.nan

    def at(self, k: int) -> dict[str, np.ndarray]:
        g, spy, beta, sig = self.g, self.spy, self.beta, self.sig
        C, H, L, V = g.c, g.h, g.l, g.v
        out: dict[str, np.ndarray] = {}
        with np.errstate(divide="ignore", invalid="ignore"):
            for n in (1, 3, 6):
                a = max(k - n, 0)
                r = np.log(C[:, k] / C[:, a])
                scale = self._scale(a, k)
                out[f"r{n}"] = r
                if n > 1:
                    out[f"z{n}"] = (r - beta * r[spy]) / (sig * scale + EPS)
                if n == 6:
                    out["zS6"] = r[spy] / (self.sig_spy * scale + EPS)
            if k >= 6:
                r_a = np.log(C[:, k] / C[:, k - 3]) - beta * np.log(C[spy, k] / C[spy, k - 3])
                r_b = np.log(C[:, k - 3] / C[:, k - 6]) - beta * np.log(C[spy, k - 3] / C[spy, k - 6])
                out["acc"] = (r_a - r_b) / (sig * np.sqrt(np.sum(self.m[k - 5:k + 1] ** 2)) + EPS)
            else:
                out["acc"] = np.zeros(C.shape[0])
            a3 = max(k - 2, 0)
            out["rvol3"] = V[:, a3:k + 1].sum(axis=1) / self.vm[:, a3:k + 1].sum(axis=1)
            vol = V[:, :k + 1].sum(axis=1)
            out["rvolc"] = vol / self.cvm[:, k]
            vwap = (self.tp[:, :k + 1] * V[:, :k + 1]).sum(axis=1) / vol
            out["vwap"] = np.where(vol > 0, vwap, np.nan)
            out["dvwap"] = np.log(C[:, k] / out["vwap"]) / (self.sig_day + EPS)
            if k >= 3:
                out["newhi"] = H[:, k - 2:k + 1].max(axis=1) > H[:, :k - 2].max(axis=1)
                out["newlo"] = L[:, k - 2:k + 1].min(axis=1) < L[:, :k - 2].min(axis=1)
            else:
                out["newhi"] = out["newlo"] = np.zeros(C.shape[0], dtype=bool)
            a6 = max(k - 6, 0)
            path = np.abs(np.diff(C[:, a6:k + 1], axis=1)).sum(axis=1)
            out["er6"] = np.abs(C[:, k] - C[:, a6]) / (path + EPS)
            # Today's realised volatility against the baseline: the RMS of the market-adjusted 5-minute returns
            # since the opening bar, each in units of the expected sigma5 * m_j (1.0 = a normal day). `rv` covers
            # the session so far, `rv12` the last hour. The adaptive reversal exit divides z3 by it.
            if k >= 1:
                lr = np.log(C[:, 1:k + 1] / C[:, :k])
                res = (lr - beta[:, None] * lr[spy][None, :]) / (sig[:, None] * self.m[1:k + 1][None, :] + EPS)
                out["rv"] = np.sqrt(np.mean(res ** 2, axis=1))
                out["rv12"] = np.sqrt(np.mean(res[:, -12:] ** 2, axis=1))
            else:
                out["rv"] = out["rv12"] = np.ones(C.shape[0])
            out["rday"] = np.log(C[:, k] / self.pc)
            out["zday"] = (out["rday"] - beta * out["rday"][spy]) / (self.sigd + EPS)
        out["fresh"] = g.present[:, a3:k + 1].all(axis=1) & (V[:, a3:k + 1] > 0).all(axis=1)
        out["dollar3"] = (V[:, a3:k + 1] * C[:, a3:k + 1]).sum(axis=1)
        out["C"], out["H"], out["L"], out["V"] = C[:, k], H[:, k], L[:, k], V[:, k]
        return out


def direction(z3: np.ndarray, z6: np.ndarray, entry: dict) -> np.ndarray:
    """Section 4.1: sign(z6) when the 30-minute thrust is relatively stronger, else sign(z3); 0 -> +1."""
    use6 = np.abs(z6) / entry["z6"] >= np.abs(z3) / entry["z3"]
    d = np.where(use6, np.sign(z6), np.sign(z3))
    return np.where(np.isfinite(d) & (d != 0), d, 1.0).astype(np.int64)


def extreme(f: dict[str, np.ndarray], d) -> np.ndarray:
    return np.where(np.asarray(d) > 0, f["newhi"], f["newlo"])


def entry_gates(f: dict[str, np.ndarray], d: np.ndarray, entry: dict, bump: np.ndarray | float) -> np.ndarray:
    """E1-E5 of section 4.2 (E6 eligibility and re-entry rules are applied by the engine)."""
    z3, z6 = d * f["z3"], d * f["z6"]
    thrust = ((z3 >= entry["z3"] + bump) | (z6 >= entry["z6"] + bump)) & (z3 > 0) & (z6 > 0)
    floor = d * f["r6"] >= entry["abs_r6"]
    part = (f["rvol3"] >= entry["rvol3"]) & (f["rvolc"] >= entry["rvolc"])
    struct = extreme(f, d) & (d * f["dvwap"] > 0) & (f["er6"] >= entry["er6"])
    inplay = (d * f["zday"] >= entry["inplay_zday"]) & (f["rvolc"] >= entry["inplay_rvolc"])
    return thrust & floor & part & struct & inplay


def score(f: dict[str, np.ndarray], d, entry: dict, weights: dict) -> np.ndarray:
    """Section 4.4 intensity (0-100). It ranks and displays; it is not a probability."""
    with np.errstate(divide="ignore", invalid="ignore"):
        s1 = lin(np.maximum(d * f["z3"] / entry["z3"], d * f["z6"] / entry["z6"]), 0.5, 2.0)
        s2 = lin(np.log2(np.maximum(f["rvol3"], 1e-3)), 0.0, 3.0)
        s3 = (0.4 * extreme(f, d).astype(float) + 0.3 * lin(f["er6"], 0.3, 0.8)
              + 0.3 * lin(d * f["dvwap"], 0.0, 1.0))
        s4 = lin(d * f["zday"], 0.5, 3.0)
        s5 = lin(d * f["acc"], 0.0, 2.0)
    return 100.0 * (weights["thrust"] * s1 + weights["burst_vol"] * s2 + weights["structure"] * s3
                    + weights["day"] * s4 + weights["accel"] * s5)


def split_flags(grid: Grid, pc: np.ndarray, sigd: np.ndarray) -> np.ndarray:
    """Section 3 corporate-action guard from the first regular bar's open against the prior close."""
    n = grid.c.shape[1]
    rows = np.arange(len(grid.symbols))
    o0 = np.where(grid.first < n, grid.o[rows, np.minimum(grid.first, n - 1)], np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = o0 / pc
        big = np.abs(np.log(ratio)) >= SPLIT_SIGMA * sigd
        near = (np.abs(ratio[:, None] / SPLIT_RATIOS[None, :] - 1.0) <= SPLIT_TOL).any(axis=1)
    return big & near


@dataclass
class HaltFlags:
    halted: np.ndarray       # [S, n] halt evidence at the slot
    reopen: np.ndarray       # [S, n] slot is inside the reopen window after a halt
    run: np.ndarray          # [S, n] consecutive halted slots ending at the slot


def halt_flags(grid: Grid, *, spy: int, sig5: np.ndarray, m: np.ndarray, ms_ok: np.ndarray,
               move_sigma: float, reopen_bars: int, external: np.ndarray | None = None) -> HaltFlags:
    """Section 2.3 heuristic (b), plus optional external evidence.

    Slot k is halted when it has no real print (absent, or V=0 with H=L), SPY printed, the name's baseline
    is not gappy, and either the previous printed bar moved >= move_sigma * sigma5 * m_k or slot k-1 was
    also missing. A single missing slot followed by a bar that moves that much is recognised when that bar
    prints: it then opens the reopen window.
    """
    S, n = grid.c.shape
    rows = np.arange(S)[:, None]
    miss = ~grid.present | ((grid.v == 0) & (grid.h == grid.l))
    spy_p = grid.present[spy]
    with np.errstate(divide="ignore", invalid="ignore"):
        lr = np.zeros((S, n))
        lr[:, 1:] = np.log(grid.c[:, 1:] / grid.c[:, :-1])
        thr = move_sigma * sig5[:, None] * m[None, :n]
        last = np.where(~miss, np.arange(n), -1)
        np.maximum.accumulate(last, axis=1, out=last)
        prev = np.full((S, n), -1)
        prev[:, 1:] = last[:, :-1]
        lr_prev = np.where(prev >= 1, np.abs(lr[rows, np.maximum(prev, 0)]), 0.0)
        miss_prev = np.zeros((S, n), dtype=bool)
        miss_prev[:, 1:] = miss[:, :-1]
        gate = spy_p[None, :] & ms_ok[:, None]
        halted = miss & gate & ((lr_prev >= thr) | miss_prev)
        if external is not None:
            halted |= external
        retro = np.zeros((S, n), dtype=bool)
        retro[:, 1:] = miss[:, :-1] & gate[:, :-1] & (np.abs(lr[:, 1:]) >= thr[:, :-1])
    resumed = grid.present & (grid.v > 0) & ~halted
    halted_prev = np.zeros((S, n), dtype=bool)
    halted_prev[:, 1:] = halted[:, :-1]
    ended = resumed & (halted_prev | retro)
    reopen = np.zeros((S, n), dtype=bool)
    for i in range(reopen_bars):
        reopen[:, i:] |= ended[:, :n - i]
    reopen &= ~halted
    run = np.zeros((S, n), dtype=np.int64)
    for j in range(n):
        run[:, j] = np.where(halted[:, j], (run[:, j - 1] if j else 0) + 1, 0)
    return HaltFlags(halted, reopen, run)
