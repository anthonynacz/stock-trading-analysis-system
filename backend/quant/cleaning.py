"""The model's daily variance series with the data-cleaning rules of config.ESTIMATOR (pure numpy, no DB).

`variance_series` is the one place the rules act: the evaluation (quant/evaluate.make_series), the forecast job
(through the same panel) and the audit all call it, so every model and the live forecast see the same input.
Rules (each justified in config.ESTIMATOR, counted in the scorecard's `cleaning` block):
- no_trade_missing: no-trade bars are missing, not zero variance (estimators.no_trade_bars).
- gaps: each ticker on the panel's session calendar (`session_reference` / `align_sessions`); the bar after a
  missing or invalid session has no known overnight return and is missing too.
- splits: one overnight return that is a split the vendor did not adjust is repaired or, when the evidence is
  not unambiguous, the day is missing (`split_days`).
- shell: a ticker's start-of-life pinned regime (a blank-check trust before a merger) is cut (`shell_end`).
"""
from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np

from .estimators import close_to_close_returns, garman_klass, no_trade_bars, overnight_returns, valid_bars

ROW_KEYS = ("open", "high", "low", "close", "adj_close", "volume", "source")


def _a(x) -> np.ndarray:
    return np.asarray([np.nan if v is None else v for v in x], dtype=np.float64)


# ---------------------------------------------------------------- missing sessions

def session_reference(rows: dict[str, dict[str, Any]], ref_share: float = 0.5) -> np.ndarray:
    """The panel's session calendar: dates on which at least `ref_share` of the tickers whose bars span that date
    have a bar (no holiday table needed, and a foreign or one-off date of one ticker does not become a session
    for the others)."""
    spans, sets = [], []
    for r in rows.values():
        d = np.unique(np.asarray(r["date"], dtype="datetime64[D]"))
        if len(d):
            spans.append((d[0], d[-1]))
            sets.append(d)
    if not sets:
        return np.zeros(0, dtype="datetime64[D]")
    allv = np.unique(np.concatenate(sets))
    have = np.zeros(len(allv), dtype=np.int64)
    live = np.zeros(len(allv) + 1, dtype=np.int64)
    for d, (a, b) in zip(sets, spans):
        have[np.searchsorted(allv, d)] += 1
        live[np.searchsorted(allv, a)] += 1
        live[np.searchsorted(allv, b, side="right")] -= 1
    live = np.cumsum(live)[:-1]
    return allv[have >= np.maximum(1, np.ceil(ref_share * live))]


def align_sessions(r: dict[str, Any], ref: np.ndarray) -> tuple[dict[str, Any], int]:
    """One ticker's rows with a NaN row for each reference session inside its span that it has no bar for.
    Returns (rows, sessions inserted). Its own extra dates are kept."""
    d = np.asarray(r["date"], dtype="datetime64[D]")
    if not len(d) or not len(ref):
        return r, 0
    span = ref[(ref >= d.min()) & (ref <= d.max())]
    miss = np.setdiff1d(span, d)
    if not len(miss):
        return r, 0
    alld = np.concatenate([d, miss])
    order = np.argsort(alld, kind="stable")
    out: dict[str, Any] = {"date": [str(x) for x in alld[order]]}
    for k in ROW_KEYS:
        if k not in r or r[k] is None:
            continue
        fill = None if k in ("volume", "source") else float("nan")
        vals = list(r[k]) + [fill] * len(miss)
        out[k] = [vals[i] for i in order]
    out["_inserted"] = np.concatenate([np.zeros(len(d), bool), np.ones(len(miss), bool)])[order]
    return out, int(len(miss))


# ---------------------------------------------------------------- split defects

def _split_ratio(x: float, ratios, tol: float) -> float | None:
    for k in ratios:
        if abs(abs(x) - math.log(k)) <= tol:
            return float(k)
    return None


def _median(x: np.ndarray) -> float:
    x = x[np.isfinite(x) & (x > 0)]
    return float(np.median(x)) if len(x) else float("nan")


def split_days(o, h, l, c, adj_c, volume, cfg: dict) -> dict[str, Any]:
    """Split defects in one ticker's bars (rules in config.ESTIMATOR["splits"]).

    Returns {"on": overnight return to use where finite (NaN: no change), "cc": the same for the close-to-close
    return, "missing": days whose variance is missing (ambiguous), "events": [{i, kind, ratio, m, df}]}; kinds:
    repaired (both series moved by a split ratio, evidence unambiguous), adj_only (adj_close jumped, prices
    did not: raw return used), adj_confirmed (the dividend correction already takes the jump out; unchanged),
    ambiguous (variance missing)."""
    o, h, l, c = map(_a, (o, h, l, c))
    n = len(c)
    out_on, out_cc = np.full(n, np.nan), np.full(n, np.nan)
    missing = np.zeros(n, dtype=bool)
    events: list[dict] = []
    if n < 2:
        return {"on": out_on, "cc": out_cc, "missing": missing, "events": events}
    a = _a(adj_c) if adj_c is not None else np.full(n, np.nan)
    vol = _a(volume) if volume is not None else np.full(n, np.nan)
    ok = valid_bars(o, h, l, c)
    with np.errstate(invalid="ignore", divide="ignore"):
        f = np.where(np.isfinite(a) & (a > 0) & ok, a / c, np.nan)
        m = np.full(n, np.nan)
        dc = np.full(n, np.nan)
        df = np.full(n, np.nan)
        m[1:] = np.log(o[1:] / c[:-1])
        dc[1:] = np.log(c[1:] / c[:-1])
        df[1:] = np.log(f[1:] / f[:-1])
        rng = np.log(np.maximum.reduce([h, o, c]) / np.minimum.reduce([l, o, c]))
    ratios, rtol, fj, tol = cfg["ratios"], float(cfg["ratio_tol"]), float(cfg["factor_jump"]), float(cfg["tol"])
    w, ptol, calm = int(cfg["window"]), float(cfg["persist_tol"]), float(cfg["calm_range"])
    for i in range(1, n):
        if not (ok[i] and ok[i - 1]) or not np.isfinite(m[i]):
            continue
        dfi = df[i]
        if np.isfinite(dfi) and abs(dfi) > fj:
            if abs(m[i]) <= tol and abs(dc[i]) <= tol:
                kind = "adj_only"
                out_on[i], out_cc[i] = m[i], dc[i]
            elif abs(m[i] + dfi) <= tol and rng[i] < abs(dfi) - tol:
                kind = "adj_confirmed"
            else:
                kind = "ambiguous"
                missing[i] = True
            events.append({"i": i, "kind": kind, "ratio": _split_ratio(dfi, ratios, rtol),
                           "m": round(float(m[i]), 4), "df": round(float(dfi), 4)})
            continue
        k = _split_ratio(m[i], ratios, rtol)
        if k is None:
            continue
        s = math.copysign(math.log(k), m[i])
        lo, hi = max(0, i - w), min(n, i + w)
        before, after = _median(c[lo:i]), _median(c[i:hi])
        persist = (i - lo == w and hi - i == w and np.isfinite(before) and np.isfinite(after)
                   and abs(math.log(after / before) - m[i]) <= ptol)
        quiet = np.isfinite(rng[i]) and np.isfinite(rng[i - 1]) and rng[i] < calm and rng[i - 1] < calm
        vb, va = _median(vol[lo:i]), _median(vol[i:hi])
        shares = (np.isfinite(vb) and np.isfinite(va) and -math.copysign(1.0, m[i]) * math.log(va / vb)
                  >= 0.5 * math.log(k))
        corr = dfi if np.isfinite(dfi) else 0.0
        if persist and quiet and shares:
            kind = "repaired"
            out_on[i] = m[i] - s + corr
            if np.isfinite(dc[i]):
                out_cc[i] = dc[i] - s + corr
        else:
            kind = "ambiguous"
            missing[i] = True
        events.append({"i": i, "kind": kind, "ratio": k, "m": round(float(m[i]), 4),
                       "df": None if not np.isfinite(dfi) else round(float(dfi), 4),
                       "evidence": {"persist": bool(persist), "quiet": bool(quiet), "shares": bool(shares)}})
    return {"on": out_on, "cc": out_cc, "missing": missing, "events": events}


# ---------------------------------------------------------------- shell history

def _rolling_count(ok: np.ndarray, w: int) -> np.ndarray:
    cs = np.concatenate([[0], np.cumsum(ok.astype(np.int64))])
    out = np.zeros(len(ok), dtype=np.int64)
    if len(ok) >= w:
        out[w - 1:] = cs[w:] - cs[:-w]
    return out


def pinned_sessions(v: np.ndarray, c: np.ndarray, cfg: dict, trading_days: int = 252) -> np.ndarray:
    """Sessions whose trailing `window` closes all sit within +/- band of their median with a realised vol under
    vol_line (or fewer than a quarter of the window's sessions trade at all)."""
    v, c = np.asarray(v, dtype=np.float64), _a(c)
    n, w = len(c), int(cfg["window"])
    out = np.zeros(n, dtype=bool)
    if n < w:
        return out
    okv = np.isfinite(v)
    nv = _rolling_count(okv, w)
    sv = np.concatenate([[0.0], np.cumsum(np.where(okv, v, 0.0))])
    mean_v = np.full(n, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_v[w - 1:] = (sv[w:] - sv[:-w]) / nv[w - 1:]
    low_vol = (nv < w / 4.0) | (np.sqrt(mean_v * trading_days) < float(cfg["vol_line"]))
    okc = np.isfinite(c) & (c > 0)
    nc = _rolling_count(okc, w)
    win = np.lib.stride_tricks.sliding_window_view(np.where(okc, c, np.nan), w)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)     # all-NaN windows (inserted sessions)
        med = np.nanmedian(win, axis=1)
        mx, mn = np.nanmax(win, axis=1), np.nanmin(win, axis=1)
    band = float(cfg["band"])
    with np.errstate(invalid="ignore", divide="ignore"):
        in_band = (mx / med - 1.0 <= band) & (1.0 - mn / med <= band)
    out[w - 1:] = (nc[w - 1:] >= w / 2.0) & in_band & low_vol[w - 1:]
    return out


def shell_end(pinned: np.ndarray, cfg: dict) -> int:
    """Index of the last session of a start-of-life pinned regime the ticker has left for good, else -1."""
    idx = np.flatnonzero(pinned)
    if not len(idx):
        return -1
    p0, last = int(idx[0]), int(idx[-1])
    if p0 - int(cfg["window"]) + 1 > int(cfg["start_within"]):
        return -1
    if len(pinned) - 1 - last < int(cfg["min_after"]):
        return -1
    if float(np.mean(pinned[p0:last + 1])) < float(cfg["pinned_share"]):
        return -1
    return last


# ---------------------------------------------------------------- the series

def variance_series(o, h, l, c, adj_c=None, volume=None, est: dict | None = None,
                    trading_days: int = 252) -> tuple[np.ndarray, dict[str, Any]]:
    """(daily variance, info): overnight^2 + Garman-Klass floored at est["floor"], NaN where a rule says missing.
    info: counts per rule, split events, the shell end index (-1: none), `pinned` (bool per row) and `cc` (the
    dividend-adjusted close-to-close returns with the same repairs, for the scale diagnostic)."""
    est = est or {}
    o, h, l, c = map(_a, (o, h, l, c))
    n = len(c)
    floor = float(est.get("floor", 1e-8))
    gk = garman_klass(o, h, l, c)
    on = overnight_returns(o, c, adj_c)
    cc = close_to_close_returns(c, adj_c)
    info: dict[str, Any] = {"no_trade": 0, "post_gap": 0, "split_events": [], "split_missing": 0, "shell_end": -1,
                            "shell_bars": 0, "pinned": np.zeros(n, dtype=bool)}
    sp = None
    if est.get("splits") and n > 1:
        sp = split_days(o, h, l, c, adj_c, volume, est["splits"])
        on = np.where(np.isfinite(sp["on"]), sp["on"], on)
        cc = np.where(np.isfinite(sp["cc"]), sp["cc"], cc)
        info["split_events"] = sp["events"]
    v = gk + np.where(np.isfinite(on), on * on, 0.0)
    v = np.where(np.isfinite(gk), np.maximum(v, floor), np.nan)
    if est.get("gaps") and n > 1:
        ok = valid_bars(o, h, l, c)
        after = np.zeros(n, dtype=bool)
        after[1:] = ~ok[:-1] & np.isfinite(v[1:])
        info["post_gap"] = int(after.sum())
        v = np.where(after, np.nan, v)
    if est.get("no_trade_missing"):
        nt = no_trade_bars(o, h, l, c, volume)
        info["no_trade"] = int((nt & np.isfinite(v)).sum())
        v = np.where(nt, np.nan, v)
    if sp is not None:
        info["split_missing"] = int((sp["missing"] & np.isfinite(v)).sum())
        v = np.where(sp["missing"], np.nan, v)
        cc = np.where(sp["missing"], np.nan, cc)
    if est.get("shell"):
        pin = pinned_sessions(v, c, est["shell"], trading_days)
        info["pinned"] = pin
        L = shell_end(pin, est["shell"])
        if L >= 0:
            info["shell_end"] = L
            info["shell_bars"] = int(np.isfinite(v[:L + 1]).sum())
            v = v.copy()
            v[:L + 1] = np.nan
    info["cc"] = cc
    return v, info
