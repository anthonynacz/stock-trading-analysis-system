"""The display reading "options rich / fair / cheap" (pure functions, no DB). Not advice.

Implied vol normally sits above the volatility that follows (the variance risk premium), so IV > forecast is
the usual state, not a signal. The reading places the ticker's IV / forecast ratio in a reference distribution
of the same ratio:
1. the ticker's own earlier readings (READING.own_min_obs or more, earnings-free), else
2. today's cross-section of comparable names (READING.xs_min_names or more), else
3. "insufficient history".
Earnings inside the horizon put the earnings jump into IV: the ratio is not comparable, the reading says so.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from .config import READING

RICH, FAIR, CHEAP = "rich", "fair", "cheap"
INSUFFICIENT = "insufficient history"
NOT_COMPARABLE = "not comparable"
NO_IV = "no implied vol"

BASIS_OWN = "own_history"
BASIS_XS = "cross_section"
BASIS_EARNINGS = "earnings_in_horizon"
BASIS_NONE = "none"


def percentile_rank(x: float, ref: Sequence[float]) -> float | None:
    """Share of the reference below x, ties counted half (0..1); None for an empty reference."""
    vals = [v for v in ref if v is not None and math.isfinite(v)]
    if not vals:
        return None
    below = sum(1 for v in vals if v < x)
    equal = sum(1 for v in vals if v == x)
    return (below + 0.5 * equal) / len(vals)


def label(pctl: float, cfg: dict = READING) -> str:
    if pctl >= cfg["rich_pctl"]:
        return RICH
    if pctl <= cfg["cheap_pctl"]:
        return CHEAP
    return FAIR


def classify(ratio: float | None, *, own_ref: Sequence[float], xs_ref: Sequence[float],
             earnings_in_horizon: bool, cfg: dict = READING) -> tuple[str, str, float | None]:
    """(reading, basis, percentile). `own_ref`: the ticker's earlier earnings-free ratios (not today's);
    `xs_ref`: today's comparable ratios across names (this one included)."""
    if ratio is None or not math.isfinite(ratio):
        return NO_IV, BASIS_NONE, None
    if earnings_in_horizon:
        return NOT_COMPARABLE, BASIS_EARNINGS, None
    own = [v for v in own_ref if v is not None and math.isfinite(v)]
    if len(own) >= cfg["own_min_obs"]:
        p = percentile_rank(ratio, own)
        return label(p, cfg), BASIS_OWN, p
    xs = [v for v in xs_ref if v is not None and math.isfinite(v)]
    if len(xs) >= cfg["xs_min_names"]:
        p = percentile_rank(ratio, xs)
        return label(p, cfg), BASIS_XS, p
    return INSUFFICIENT, BASIS_NONE, None
