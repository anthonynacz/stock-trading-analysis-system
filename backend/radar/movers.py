"""Plain movers: the largest percentage moves among the scanned stocks, today and over the last sessions.

The radar proper measures a move against the market and against the stock's own volatility; this list does
neither. It is the plain change since yesterday's close, plus the plain change over the last 3, 5, 10 and
20 sessions (today included) and where the price stands against its 20- and 50-day averages, for the
"Movers" view of the radar page, which sorts and filters it by option liquidity, dollar volume, price and
sector. Display only: the engine never reads it, and a name here says nothing about whether the move will
continue.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from .config import MOVERS, REFERENCE_SYMBOLS

if TYPE_CHECKING:        # radar.tick imports this module and must stay light (no numpy at import time)
    from .types import Quote

SETUPS = ("dip_uptrend", "falling", "stretched")


def _pos(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def _pct(a: float, b: float) -> float:
    return round((a / b - 1.0) * 100.0, 2)


def history_stats(price: float, closes: list[float], cfg: dict = MOVERS) -> dict:
    """Multi-day context of `price` (now) from the prior daily closes (oldest first, the last is yesterday's).

    chg_{n}d: the move over the last n sessions with today as the last one (price against the close n sessions
    back). vs_sma20 / vs_sma50: distance from the 20- / 50-day average, today's price included.
    off_high_20d: distance from the highest of the last 20 sessions (0 at a new high). A value is None when
    the history is too short for it."""
    out: dict[str, float | None] = {f"chg_{n}d": None for n in cfg["windows"]}
    out.update(vs_sma20=None, vs_sma50=None, off_high_20d=None)
    c = [float(x) for x in closes or []]
    if not c or price <= 0 or any(not (math.isfinite(x) and x > 0) for x in c):
        return out
    for n in cfg["windows"]:
        if len(c) >= n:
            out[f"chg_{n}d"] = _pct(price, c[-n])
    for n in (20, 50):
        if len(c) >= n - 1:
            window = c[len(c) - (n - 1):] + [price]
            out[f"vs_sma{n}"] = _pct(price, sum(window) / n)
    if len(c) >= 19:
        out["off_high_20d"] = _pct(price, max(c[-19:] + [price]))
    return out


def setup_of(stats: dict, cfg: dict = MOVERS) -> str | None:
    """The multi-day pattern, a label and not a forecast (config.MOVERS has the study behind the limits):
    dip_uptrend  down `dip_chg_5d_max` or more over 5 sessions while above the 50-day average
    falling      the same drop at or below the 50-day average
    stretched    up `stretched_chg_5d_min` or more over 5 sessions"""
    chg5, vs50 = stats.get("chg_5d"), stats.get("vs_sma50")
    if chg5 is None:
        return None
    if chg5 >= cfg["stretched_chg_5d_min"]:
        return "stretched"
    if chg5 <= cfg["dip_chg_5d_max"] and vs50 is not None:
        return "dip_uptrend" if vs50 > 0 else "falling"
    return None


def _top(rows: list[dict], key: str, floor: float, per_side: int) -> set[str]:
    """Tickers of the `per_side` largest gainers and losers by `key`, among moves of at least `floor` percent."""
    keep: set[str] = set()
    for sign in (1, -1):
        side = [r for r in rows if r.get(key) is not None and sign * r[key] >= floor]
        side.sort(key=lambda r: (-abs(r[key]), r["ticker"]))
        keep.update(r["ticker"] for r in side[:per_side])
    return keep


def plain_movers(quotes: dict[str, Quote], pack: Any, slot: int, cfg: dict = MOVERS) -> list[dict]:
    """The eligible names worth listing, largest move today first:

    - today: moved at least `min_abs_pct` percent since the prior close, at most `max_rows_per_side` gainers
      and as many losers (a one-way day must not crowd the other side out);
    - each window of `windows`: moved at least `window_min_abs_pct` percent over it, at most
      `window_rows_per_side` per side;
    - the `setup_rows` deepest dips in an uptrend (setup_of), whatever they did today.

    `slot` is the last final 5-minute slot (-1 before the first bar closes): relative volume is today's volume
    against the stock's normal cumulative volume by that slot. The prior close is the pack's (the one every
    other radar number uses); the quote's own is the fallback. The multi-day fields come from the pack's daily
    closes and are None on a pack without them."""
    rows = []
    for sym, base in pack.symbols.items():
        q = quotes.get(sym)
        if q is None or sym in REFERENCE_SYMBOLS or not base.eligible:
            continue
        price, pack_prev = _pos(q.price), _pos(base.prev_close)
        prev = pack_prev or _pos(q.prev_close)
        if price is None or prev is None:
            continue
        pct = (price / prev - 1.0) * 100.0
        volume = _pos(q.day_volume)
        normal = _pos(base.cvm[slot]) if 0 <= slot < len(base.cvm) else None
        # The closes count sessions back from the pack's prior close: without it they are not aligned.
        stats = history_stats(price, list(getattr(base, "closes", None) or []) if pack_prev else [], cfg)
        rows.append({
            "ticker": sym, "name": base.name or q.name, "sector": base.sector or q.sector or "Unknown",
            "direction": "up" if pct > 0 else "down", "price": round(price, 4), "prev_close": round(prev, 4),
            "change_pct": round(pct, 2),
            "day_volume": int(volume) if volume else None,
            "dollar_volume": round(price * volume) if volume else None,
            "rvol": round(volume / normal, 2) if volume and normal else None,
            "adv_usd": round(a) if (a := _pos(base.adv20_usd)) else None,
            "market_cap": round(m) if (m := _pos(q.market_cap)) else None,
            **stats, "setup": setup_of(stats, cfg),
        })
    keep = _top(rows, "change_pct", cfg["min_abs_pct"], int(cfg["max_rows_per_side"]))
    for n in cfg["windows"]:
        keep |= _top(rows, f"chg_{n}d", cfg["window_min_abs_pct"], int(cfg["window_rows_per_side"]))
    dips = sorted((r for r in rows if r["setup"] == "dip_uptrend"), key=lambda r: (r["chg_5d"], r["ticker"]))
    keep.update(r["ticker"] for r in dips[: int(cfg["setup_rows"])])
    kept = [r for r in rows if r["ticker"] in keep]
    kept.sort(key=lambda r: (-abs(r["change_pct"]), r["ticker"]))
    return kept
