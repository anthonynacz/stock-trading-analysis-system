"""One clean implied-volatility measurement for every part of Vela.

IV30 is a constant-maturity 30-day at-the-money IV: for each expiry, the
average of the call and put IV at the strike nearest spot, then interpolated
in total variance to exactly 30 days. Only two-sided quotes count, so a
chain read outside market hours gives no reading instead of a junk number
(the old `options_snapshots.atm_iv` mixed 0.00 and 5.88 readings from the
front-week call). Pure functions: chains are plain dicts, as returned by
`DataSourceClient.get_options_chain` or Yahoo's optionChain JSON.

Units: IVs and realised vols are annualised fractions (0.32 = 32%).
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Iterable

MIN_IV = 0.05
MAX_IV = 3.0
MAX_SPREAD = 0.25          # (ask - bid) / mid
MIN_DTE = 7                # nearer expiries are dominated by pin / event noise
TARGET_DTE = 30
MAX_FLAT_GAP = 15          # one-sided IV30: use the nearest expiry only within this many days of 30


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def quoted_iv(contract: dict) -> float | None:
    """The contract's IV when it has a tight two-sided quote and a sane IV."""
    bid, ask, iv = _num(contract.get("bid")), _num(contract.get("ask")), _num(contract.get("impliedVolatility"))
    if not bid or not ask or bid <= 0 or ask < bid or iv is None:
        return None
    if (ask - bid) / ((ask + bid) / 2.0) > MAX_SPREAD:
        return None
    return iv if MIN_IV <= iv <= MAX_IV else None


def atm_expiry_iv(calls: Iterable[dict], puts: Iterable[dict], spot: float) -> float | None:
    """Average call/put IV at the strike nearest spot; None when neither side is cleanly quoted."""
    by_strike: dict[float, list[float]] = {}
    for c in list(calls or []) + list(puts or []):
        k = _num(c.get("strike"))
        if k is not None:
            by_strike.setdefault(k, [])
    if not by_strike or not spot:
        return None
    strike = min(by_strike, key=lambda k: (abs(k - spot), -k))
    ivs = [iv for c in list(calls or []) + list(puts or [])
           if _num(c.get("strike")) == strike and (iv := quoted_iv(c)) is not None]
    return sum(ivs) / len(ivs) if ivs else None


def constant_maturity_iv(points: list[tuple[int, float]], target: int = TARGET_DTE) -> float | None:
    """IV at exactly `target` days from (dte, iv) points, interpolated in total variance.

    Uses the expiries on either side of the target; with only one side, the
    nearest expiry when it lies within MAX_FLAT_GAP days of the target.
    """
    pts = sorted((d, iv) for d, iv in points if d >= MIN_DTE and iv)
    if not pts:
        return None
    below = [p for p in pts if p[0] <= target]
    above = [p for p in pts if p[0] >= target]
    if below and above:
        (d1, v1), (d2, v2) = below[-1], above[0]
        if d1 == d2:
            return v1
        w = (d2 - target) / (d2 - d1)
        var = w * v1 * v1 * d1 + (1 - w) * v2 * v2 * d2
        return math.sqrt(var / target) if var > 0 else None
    d, v = min(pts, key=lambda p: abs(p[0] - target))
    return v if abs(d - target) <= MAX_FLAT_GAP else None


def iv_from_chains(chains: dict[str, dict], spot: float | None, today: date) -> dict:
    """{iv30, iv_front, front_dte, term_ratio} from {"YYYY-MM-DD": {"calls": [...], "puts": [...]}}."""
    out: dict = {"iv30": None, "iv_front": None, "front_dte": None, "term_ratio": None}
    if not chains or not spot:
        return out
    points: list[tuple[int, float]] = []
    for exp_str, sides in chains.items():
        try:
            dte = (datetime.strptime(exp_str, "%Y-%m-%d").date() - today).days
        except (TypeError, ValueError):
            continue
        if dte < MIN_DTE:
            continue
        iv = atm_expiry_iv(sides.get("calls") or [], sides.get("puts") or [], spot)
        if iv is not None:
            points.append((dte, iv))
    if not points:
        return out
    points.sort()
    out["front_dte"], out["iv_front"] = points[0]
    out["iv30"] = constant_maturity_iv(points)
    if out["iv30"]:
        out["term_ratio"] = out["iv_front"] / out["iv30"]
    return out


def pick_iv_expiries(expirations: Iterable[str], today: date) -> list[str]:
    """The expiries IV30 needs: the first one at least MIN_DTE out and the two around 30 days."""
    dated = []
    for e in expirations or []:
        try:
            dated.append(((datetime.strptime(e, "%Y-%m-%d").date() - today).days, e))
        except (TypeError, ValueError):
            continue
    usable = sorted(p for p in dated if p[0] >= MIN_DTE)
    if not usable:
        return []
    picked = {usable[0][1]}
    below = [e for d, e in usable if d <= TARGET_DTE]
    above = [e for d, e in usable if d >= TARGET_DTE]
    if below:
        picked.add(below[-1])
    if above:
        picked.add(above[0])
    return sorted(picked)


def realised_vol(closes: list[float], n: int) -> float | None:
    """Annualised close-to-close volatility of the last `n` daily log returns (needs n + 1 closes)."""
    cs = [c for c in closes if c and c > 0]
    if len(cs) < n + 1:
        return None
    rets = [math.log(b / a) for a, b in zip(cs[-n - 1:-1], cs[-n:])]
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(252)
