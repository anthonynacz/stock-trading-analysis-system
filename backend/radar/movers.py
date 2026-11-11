"""Plain movers: the day's largest percentage moves among the scanned stocks.

The radar proper measures a move against the market and against the stock's own volatility; this list does
neither. It is the plain change since yesterday's close, for the "Movers" view of the radar page, which
sorts and filters it by option liquidity, dollar volume, price and sector. Display only: the engine never
reads it, and a name here says nothing about whether the move will continue.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from .config import MOVERS, REFERENCE_SYMBOLS

if TYPE_CHECKING:        # radar.tick imports this module and must stay light (no numpy at import time)
    from .types import Quote


def _pos(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def plain_movers(quotes: dict[str, Quote], pack: Any, slot: int, cfg: dict = MOVERS) -> list[dict]:
    """The eligible names that moved at least `min_abs_pct` percent since the prior close, largest move first:
    at most `max_rows_per_side` gainers and as many losers (a one-way day must not crowd the other side out).

    `slot` is the last final 5-minute slot (-1 before the first bar closes): relative volume is today's volume
    against the stock's normal cumulative volume by that slot. The prior close is the pack's (the one every
    other radar number uses); the quote's own is the fallback."""
    rows = []
    for sym, base in pack.symbols.items():
        q = quotes.get(sym)
        if q is None or sym in REFERENCE_SYMBOLS or not base.eligible:
            continue
        price, prev = _pos(q.price), _pos(base.prev_close) or _pos(q.prev_close)
        if price is None or prev is None:
            continue
        pct = (price / prev - 1.0) * 100.0
        if abs(pct) < cfg["min_abs_pct"]:
            continue
        volume = _pos(q.day_volume)
        normal = _pos(base.cvm[slot]) if 0 <= slot < len(base.cvm) else None
        rows.append({
            "ticker": sym, "name": base.name or q.name, "sector": base.sector or q.sector or "Unknown",
            "direction": "up" if pct > 0 else "down", "price": round(price, 4), "prev_close": round(prev, 4),
            "change_pct": round(pct, 2),
            "day_volume": int(volume) if volume else None,
            "dollar_volume": round(price * volume) if volume else None,
            "rvol": round(volume / normal, 2) if volume and normal else None,
            "adv_usd": round(a) if (a := _pos(base.adv20_usd)) else None,
            "market_cap": round(m) if (m := _pos(q.market_cap)) else None,
        })
    rows.sort(key=lambda r: (-abs(r["change_pct"]), r["ticker"]))
    left = {"up": int(cfg["max_rows_per_side"]), "down": int(cfg["max_rows_per_side"])}
    kept = []
    for r in rows:
        if left[r["direction"]] > 0:
            left[r["direction"]] -= 1
            kept.append(r)
    return kept
