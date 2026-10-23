"""IV cost score: is the volatility a buyer pays right now cheap or expensive?

One 0-100 score from two parts (design: docs/iv-score-design.md):
- history: where today's IV30 sits in its own clean daily history
  (`iv_daily`), as a percentile damped by how far it is from the median;
- realised: IV30 against the movement the stock delivers (HV20).
Each part is 50 when normal and higher when dearer. The history part only
counts once 20 clean days exist and reaches full weight at 120. An event
priced into the option (earnings before expiry, or a front IV well above
IV30) is tagged and floors the label at "pricey". Holder-side warnings (IV
crushing today, earnings just reported) are not part of this score.

Pure function; IVs are annualised fractions in, percents out for display.
"""

from __future__ import annotations

import math
from datetime import date
from statistics import median

LABELS = (("cheap", 35), ("fair", 60), ("pricey", 80), ("expensive", 101))
FLOOR_LABEL = "pricey"
MIN_HISTORY = 20            # clean days before the history part counts
FULL_HISTORY = 120          # ... and before it carries its full weight
WINDOW = 252                # trading days of history compared against
RECENT = 60                 # regime window
REGIME_LOW, REGIME_HIGH = 0.7, 1.4   # recent median / window median outside this: regime changed
SIZE_SCALE = 0.20           # the percentile counts in full once IV is 20% away from its median
SPIKE_GAP = 25              # percentile - rank: one spike stretches the range
EVENT_HORIZON_DAYS = 30     # IV30 spans earnings within this many days
TERM_EVENT = 1.2            # front IV / IV30 at or above this: an event is priced into the front expiry
PART_WEIGHT = 0.5
# IV/HV -> sub-score knots: reproduce the Radar's old cut-offs (cheap <= 1.2, pricey >= 1.5, expensive >= 2.0).
RATIO_KNOTS = ((0.8, 10.0), (1.0, 22.0), (1.2, 35.0), (1.5, 60.0), (2.0, 80.0), (3.0, 100.0))


def label_for(score: float) -> str:
    return next(name for name, upper in LABELS if score < upper)


def ratio_part(iv_hv: float) -> float:
    """Sub-score for IV / HV, piecewise linear in log(ratio), clamped to 0-100."""
    x = math.log(iv_hv)
    pts = [(math.log(r), s) for r, s in RATIO_KNOTS]
    if x <= pts[0][0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
    elif x >= pts[-1][0]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
    else:
        (x0, y0), (x1, y1) = next((a, b) for a, b in zip(pts, pts[1:]) if x <= b[0])
    return max(0.0, min(100.0, y0 + (x - x0) / (x1 - x0) * (y1 - y0)))


def history_part(iv30: float, history: list[float]) -> dict:
    """Percentile / rank / median of today's IV30 against its window, with the regime check."""
    window = history[-WINDOW:]
    regime = None
    if len(history) >= RECENT + 40:
        recent_med, window_med = median(history[-RECENT:]), median(window)
        if not REGIME_LOW <= recent_med / window_med <= REGIME_HIGH:
            regime = {"recent_median_pct": round(recent_med * 100, 1), "year_median_pct": round(window_med * 100, 1)}
            window = history[-RECENT:]
    med = median(window)
    lo, hi = min(window), max(window)
    pct = 100.0 * sum(1 for v in window if v <= iv30) / len(window)
    rank = 50.0 if hi == lo else max(0.0, min(100.0, (iv30 - lo) / (hi - lo) * 100.0))
    size = iv30 / med - 1.0
    sub = 50.0 + (pct - 50.0) * min(1.0, abs(size) / SIZE_SCALE)
    return {
        "days": len(history), "window_days": len(window),
        "percentile": round(pct, 1), "rank": round(rank, 1),
        "median_pct": round(med * 100, 1), "size_pct": round(size * 100, 1),
        "low_pct": round(lo * 100, 1), "high_pct": round(hi * 100, 1),
        "spike": pct - rank >= SPIKE_GAP, "regime": regime, "sub_score": sub,
    }


def iv_score(
    iv30: float | None,
    hv20: float | None,
    history: list[float] | None = None,
    *,
    iv_front: float | None = None,
    hv60: float | None = None,
    earnings_date: date | None = None,
    as_of: str | None = None,
) -> dict | None:
    """The score object, or None without an IV30 reading.

    `history` is the clean daily IV30 series before today, oldest first.
    `earnings_date` is set only when earnings fall before the expiry being judged.
    """
    if not iv30 or iv30 <= 0:
        return None
    parts: list[dict] = []
    notes: list[str] = []
    hist = None
    clean = [v for v in (history or []) if v and v > 0]
    if len(clean) >= MIN_HISTORY:
        hist = history_part(iv30, clean)
        weight = PART_WEIGHT * min(1.0, (len(clean) - MIN_HISTORY) / (FULL_HISTORY - MIN_HISTORY))
        if weight > 0:
            parts.append({"key": "history", "label": "Versus its own history",
                          "sub_score": round(hist["sub_score"]), "weight": round(weight, 2)})
        if hist["regime"]:
            r = hist["regime"]
            notes.append(f"IV regime changed: the last {RECENT} days ran at a median of {r['recent_median_pct']}% "
                         f"against {r['year_median_pct']}% before, so today is compared with the last {RECENT} days.")
        if hist["spike"]:
            notes.append(f"One spike stretches the range: IV rank {hist['rank']:.0f} looks low, but "
                         f"{hist['percentile']:.0f}% of days were at or below today's IV.")
        if abs(hist["size_pct"]) < SIZE_SCALE * 100 and abs(hist["percentile"] - 50) >= 25:
            notes.append(f"IV is only {abs(hist['size_pct']):.0f}% {'above' if hist['size_pct'] > 0 else 'below'} "
                         f"its median of {hist['median_pct']}%, so the percentile counts only in part.")
    else:
        notes.append(f"Only {len(clean)} days of clean IV history (needs {MIN_HISTORY}): judged on realised "
                     "volatility alone.")
    iv_hv = iv30 / hv20 if hv20 and hv20 > 0 else None
    if iv_hv:
        parts.append({"key": "realised", "label": "Versus the stock's own movement",
                      "sub_score": round(ratio_part(iv_hv)), "weight": PART_WEIGHT})
    if not parts:
        return None
    total = sum(p["weight"] for p in parts)
    score = sum(p["sub_score"] * p["weight"] for p in parts) / total
    for p in parts:
        p["share"] = round(p["weight"] / total, 2)

    event = None
    term_ratio = iv_front / iv30 if iv_front else None
    if earnings_date:
        event = {"kind": "earnings", "date": earnings_date.isoformat(),
                 "note": f"Earnings {earnings_date:%b} {earnings_date.day} are priced in: IV usually drops right after."}
    elif term_ratio and term_ratio >= TERM_EVENT:
        event = {"kind": "term", "date": None,
                 "note": f"The front expiry's IV is {term_ratio:.2f}x the 30-day IV: an event is priced into it."}
    label = label_for(score)
    floored = bool(event) and [n for n, _ in LABELS].index(label) < [n for n, _ in LABELS].index(FLOOR_LABEL)
    if floored:
        label = FLOOR_LABEL

    has_hist = any(p["key"] == "history" for p in parts)
    if has_hist and iv_hv and len(clean) >= FULL_HISTORY:
        confidence = "high"
    elif has_hist and iv_hv:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "score": round(score), "label": label, "floored": floored, "confidence": confidence,
        "iv30_pct": round(iv30 * 100, 1),
        "iv_front_pct": round(iv_front * 100, 1) if iv_front else None,
        "term_ratio": round(term_ratio, 2) if term_ratio else None,
        "hv20_pct": round(hv20 * 100, 1) if hv20 else None,
        "hv60_pct": round(hv60 * 100, 1) if hv60 else None,
        "iv_hv": round(iv_hv, 2) if iv_hv else None,
        "history": {k: v for k, v in hist.items() if k != "sub_score"} if hist else None,
        "parts": parts, "event": event, "notes": notes, "as_of": as_of,
    }
