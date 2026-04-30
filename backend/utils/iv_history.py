"""IV rank / percentile computation.

Proper IV rank uses 52-week (or whatever history is available) ATM IV.
When we don't yet have enough snapshots, fall back to a *stabilized*
cross-sectional proxy that only looks at near-ATM, liquid strikes on
the front 3 expirations — the full-chain min/max approach yields wildly
unstable numbers because wingy strikes have garbage IVs.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import OptionsSnapshot

MIN_HISTORY_POINTS = 20  # below this, use cross-sectional fallback
HISTORY_WINDOW_DAYS = 365


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


async def fetch_atm_iv_history(
    session: AsyncSession, ticker: str, window_days: int = HISTORY_WINDOW_DAYS
) -> list[float]:
    """Return atm_iv values from the last `window_days` of snapshots (oldest-first)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    result = await session.execute(
        select(OptionsSnapshot.atm_iv)
        .where(OptionsSnapshot.ticker == ticker)
        .where(OptionsSnapshot.snapshot_time >= cutoff)
        .where(OptionsSnapshot.atm_iv.is_not(None))
        .order_by(OptionsSnapshot.snapshot_time.asc())
    )
    values: list[float] = []
    for (v,) in result.all():
        f = _safe_float(v)
        if f is not None and 0.05 <= f <= 3.0:
            values.append(f)
    return values


def _stable_cross_sectional_rank(
    chains: dict[str, dict[str, list[dict]]],
    current_atm_iv: float,
    spot: float | None,
) -> tuple[float | None, float | None]:
    """Fallback: use IVs from liquid, near-ATM contracts on the front 3 expirations.

    This still has the fundamental limitation that min/max across one day's
    chain isn't a 52-week high/low, but restricting to near-ATM liquid strikes
    drops the wingy-IV noise that caused 14 → 5 → 12 swings.
    """
    if spot is None or spot <= 0:
        return None, None

    # Sort expirations chronologically; take front 3.
    sorted_exp: list[str] = []
    for exp_str in chains.keys():
        try:
            datetime.strptime(exp_str, "%Y-%m-%d")
        except ValueError:
            continue
        sorted_exp.append(exp_str)
    sorted_exp.sort()
    front = sorted_exp[:3]

    band = spot * 0.15
    ivs: list[float] = []
    for exp in front:
        sides = chains.get(exp, {})
        for side in ("calls", "puts"):
            for c in sides.get(side, []) or []:
                strike = _safe_float(c.get("strike"))
                iv = _safe_float(c.get("impliedVolatility"))
                oi = c.get("openInterest")
                try:
                    oi_int = int(oi) if oi is not None else 0
                except (TypeError, ValueError):
                    oi_int = 0
                if strike is None or iv is None:
                    continue
                if abs(strike - spot) > band:
                    continue
                if oi_int < 10:
                    continue
                if not (0.05 <= iv <= 3.0):
                    continue
                ivs.append(iv)

    if len(ivs) < 4:
        return None, None

    lo, hi = min(ivs), max(ivs)
    rank = ((current_atm_iv - lo) / (hi - lo) * 100) if hi > lo else 50.0
    rank = max(0.0, min(100.0, rank))
    pct = sum(1 for iv in ivs if iv <= current_atm_iv) / len(ivs) * 100
    return round(rank, 2), round(pct, 2)


async def compute_iv_rank_and_percentile(
    session: AsyncSession,
    ticker: str,
    current_atm_iv: float | None,
    *,
    chains: dict | None = None,
    spot: float | None = None,
) -> tuple[float | None, float | None, str]:
    """Return (iv_rank, iv_percentile, source).

    source is one of "historical", "cross_sectional", "unavailable".
    Prefers historical IV rank from `options_snapshots` when ≥ MIN_HISTORY_POINTS
    data points exist; otherwise falls back to a stabilized cross-sectional proxy.
    """
    if current_atm_iv is None or not (0.05 <= current_atm_iv <= 3.0):
        return None, None, "unavailable"

    history = await fetch_atm_iv_history(session, ticker)

    if len(history) >= MIN_HISTORY_POINTS:
        lo, hi = min(history), max(history)
        rank = ((current_atm_iv - lo) / (hi - lo) * 100) if hi > lo else 50.0
        rank = max(0.0, min(100.0, rank))
        pct = sum(1 for iv in history if iv <= current_atm_iv) / len(history) * 100
        return round(rank, 2), round(pct, 2), "historical"

    if chains is not None:
        rank, pct = _stable_cross_sectional_rank(chains, current_atm_iv, spot)
        if rank is not None:
            return rank, pct, "cross_sectional"

    return None, None, "unavailable"
