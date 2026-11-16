"""Implied volatility from stored chain snapshots (pure functions, no DB).

Per expiry, the at-the-money IV is computed from the quotes themselves rather than Yahoo's per-contract IV:
1. The forward comes from put-call parity, F = K + e^{rT} (C - P), at the strikes where call and put mids are
   closest. It carries dividends and borrow, and it is consistent with the (15-minute delayed) option quotes,
   so a stock price read at another moment does not bias the result.
2. The ATM IV is the Black-76 IV of the out-of-the-money mid at the two strikes bracketing F, interpolated
   linearly in strike to F. European pricing of American options: the early-exercise premium of near-the-money,
   short-dated contracts is small; noted, not corrected.
Without a usable parity pair the expiry falls back to Yahoo's IV at the strike nearest the stock price
(method "yahoo_iv").

The ~30-day constant-maturity IV interpolates total variance (sigma^2 T) linearly in T between the expiries
bracketing 30 calendar days.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Any

from radar.calendar_nyse import ET

from .config import CHAINS, IV

YEAR_DAYS = 365.0


def _ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def black76(F: float, K: float, T: float, r: float, sigma: float, call: bool) -> float:
    """Discounted Black-76 price of a European option on a forward."""
    if T <= 0 or sigma <= 0:
        return math.exp(-r * T) * max((F - K) if call else (K - F), 0.0)
    sd = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    df = math.exp(-r * T)
    if call:
        return df * (F * _ncdf(d1) - K * _ncdf(d2))
    return df * (K * _ncdf(-d2) - F * _ncdf(-d1))


def implied_vol(price: float, F: float, K: float, T: float, r: float, call: bool,
                lo: float = 1e-4, hi: float = 5.0) -> float | None:
    """Black-76 IV by bisection; None when the price is outside the no-arbitrage bounds."""
    if not (price > 0 and F > 0 and K > 0 and T > 0):
        return None
    if price <= black76(F, K, T, r, lo, call) or price >= black76(F, K, T, r, hi, call):
        return None
    a, b = lo, hi
    for _ in range(80):
        m = 0.5 * (a + b)
        if black76(F, K, T, r, m, call) < price:
            a = m
        else:
            b = m
        if b - a < 1e-7:
            break
    return 0.5 * (a + b)


def year_fraction(snapshot_at: datetime, expiry: date) -> float:
    """Years from the snapshot to 16:00 ET on the expiry date (ACT/365)."""
    end = datetime.combine(expiry, time(16, 0), tzinfo=ET)
    return max((end - snapshot_at).total_seconds(), 0.0) / (YEAR_DAYS * 86400.0)


def _mid(bid: Any, ask: Any, max_rel_spread: float) -> float | None:
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(b) and math.isfinite(a)) or b <= 0 or a <= 0 or a < b:
        return None
    m = 0.5 * (a + b)
    return m if (a - b) / m <= max_rel_spread else None


def contracts_to_rows(contracts: dict) -> list[dict]:
    """Columnar contracts document -> one dict per contract."""
    keys = [k for k in ("strike", "side", "bid", "ask", "last", "iv", "volume", "oi") if k in contracts]
    n = len(contracts.get("strike") or [])
    return [{k: contracts[k][i] for k in keys} for i in range(n)]


def expiry_atm_iv(contracts: dict, spot: float | None, T: float, r: float,
                  max_rel_spread: float = IV["max_rel_spread"]) -> dict | None:
    """{"iv", "forward", "method"} for one expiry's columnar contracts, None when nothing usable."""
    if T <= 0:
        return None
    rows = contracts_to_rows(contracts)
    calls = {float(c["strike"]): c for c in rows if c.get("side") == "C"}
    puts = {float(c["strike"]): c for c in rows if c.get("side") == "P"}
    pairs = []
    for k in sorted(set(calls) & set(puts)):
        cm = _mid(calls[k].get("bid"), calls[k].get("ask"), max_rel_spread)
        pm = _mid(puts[k].get("bid"), puts[k].get("ask"), max_rel_spread)
        if cm is not None and pm is not None:
            pairs.append((k, cm, pm))
    if pairs:
        closest = sorted(pairs, key=lambda p: abs(p[1] - p[2]))[:3]
        fwds = sorted(k + math.exp(r * T) * (cm - pm) for k, cm, pm in closest)
        F = fwds[len(fwds) // 2]
        if F > 0:
            below = [p for p in pairs if p[0] <= F]
            above = [p for p in pairs if p[0] >= F]
            pts = []
            for p in ([below[-1]] if below else []) + ([above[0]] if above else []):
                k, cm, pm = p
                call = k >= F           # the out-of-the-money side
                iv = implied_vol(cm if call else pm, F, k, T, r, call)
                if iv is not None:
                    pts.append((k, iv))
            pts = sorted(set(pts))
            if len(pts) == 2 and pts[1][0] > pts[0][0]:
                (k1, v1), (k2, v2) = pts
                w = (F - k1) / (k2 - k1)
                return {"iv": v1 + w * (v2 - v1), "forward": F, "method": "parity_b76"}
            if pts:
                return {"iv": pts[0][1], "forward": F, "method": "parity_b76_1k"}
    # Fallback: Yahoo's own IV at the strike nearest the stock price (average of call and put when both).
    if not spot:
        return None
    ivs = [c for c in rows if _iv_ok(c.get("iv"))]
    if not ivs:
        return None
    k0 = min({float(c["strike"]) for c in ivs}, key=lambda k: abs(k - spot))
    at = [float(c["iv"]) for c in ivs if float(c["strike"]) == k0]
    return {"iv": sum(at) / len(at), "forward": None, "method": "yahoo_iv"}


def _iv_ok(v: Any) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and CHAINS["min_iv"] <= f <= CHAINS["max_iv"]


def constant_maturity(points: list[tuple[float, float]], target_days: float = IV["target_days"],
                      min_dte: float = IV["min_dte"],
                      max_extrapolate_days: float = IV["max_extrapolate_days"]) -> dict | None:
    """~30-day IV from (T in years, iv) points: linear in total variance between the expiries bracketing the
    target; flat from the nearest expiry when none brackets it and it is within max_extrapolate_days.
    Returns {"iv", "method", "t_days": [used expiries' days]} or None."""
    pts = sorted((T, v) for T, v in points if T * YEAR_DAYS >= min_dte and v and v > 0 and math.isfinite(v))
    if not pts:
        return None
    Ts = target_days / YEAR_DAYS
    lower = [p for p in pts if p[0] <= Ts]
    upper = [p for p in pts if p[0] >= Ts]
    if lower and upper:
        (T1, v1), (T2, v2) = lower[-1], upper[0]
        if T2 == T1:
            return {"iv": v1, "method": "exact", "t_days": [round(T1 * YEAR_DAYS, 2)]}
        w1, w2 = v1 * v1 * T1, v2 * v2 * T2
        w = w1 + (w2 - w1) * (Ts - T1) / (T2 - T1)
        if w <= 0:
            return None
        return {"iv": math.sqrt(w / Ts), "method": "interpolated",
                "t_days": [round(T1 * YEAR_DAYS, 2), round(T2 * YEAR_DAYS, 2)]}
    T0, v0 = min(pts, key=lambda p: abs(p[0] - Ts))
    if abs(T0 - Ts) * YEAR_DAYS <= max_extrapolate_days:
        return {"iv": v0, "method": "nearest", "t_days": [round(T0 * YEAR_DAYS, 2)]}
    return None


def cm30_from_snapshots(rows: list[dict], r: float) -> dict | None:
    """Constant-maturity IV of one ticker's snapshot rows of one day (each with snapshot_at, expiry, atm_iv).
    Uses the stored per-expiry atm_iv (computed at write time from the same quotes)."""
    pts = []
    for row in rows:
        iv = row.get("atm_iv")
        if iv is None:
            continue
        pts.append((year_fraction(row["snapshot_at"], row["expiry"]), float(iv)))
    res = constant_maturity(pts)
    if res is not None:
        res["iv"] = round(res["iv"], 6)
    return res
