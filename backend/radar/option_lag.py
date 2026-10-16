"""How stale are Yahoo's delayed option quotes? Compares each logged reading with the same name's reading
~15 minutes later.

Yahoo's option quotes run about 15 minutes behind. The reading taken at t+15 shows the market as it was at
t, so it is roughly what a live feed would have shown when the radar displayed the t reading. Counting how
often the option part of the entry verdict (frontend verdict.ts: spread and liquidity, IV price) differs
between the two tells whether a live options feed would change what the radar says.

    python -m radar.option_lag [--days 10] [--json]

Reads radar_option_history (written by radar/options.py refresh); read-only.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Mirrors frontend/src/components/radar/verdict.ts and ivPrice.ts.
SPREAD_OK, SPREAD_MAX = 10.0, 15.0
IV_CHEAP_MAX, IV_RICH, IV_EXPENSIVE, TERM_INVERTED = 1.2, 1.5, 2.0, 1.2
# The pair window around the ~15-minute delay (readings come every 5 minutes).
PAIR_MIN, PAIR_MAX, PAIR_TARGET = 12.0, 20.0, 15.0
CLASS_RANK = {"ok": 0, "caution": 1, "avoid": 2}


def side_of(m: dict, direction: str | None) -> dict | None:
    """The traded side as {liquidity, spread_pct, iv...}: calls for up moves, puts for down moves; older rows
    without `sides` fall back to the call fields."""
    kind = "put" if direction == "down" else "call"
    s = (m.get("sides") or {}).get(kind)
    if s:
        return {"liquidity": s.get("liquidity"), "spread_pct": (s.get("atm") or {}).get("spread_pct"),
                "strike": (s.get("atm") or {}).get("strike"), "mid": (s.get("atm") or {}).get("mid")}
    if kind == "put":
        return None
    a = m.get("atm") or {}
    return {"liquidity": m.get("liquidity"), "spread_pct": a.get("spread_pct"), "strike": a.get("strike"),
            "mid": a.get("mid")}


def option_class(side: dict | None) -> str | None:
    """avoid / caution / ok from liquidity and spread, as the verdict's first block."""
    if side is None:
        return None
    sp = side.get("spread_pct")
    if side.get("liquidity") in ("none", "thin") or sp is None or sp > SPREAD_MAX:
        return "avoid"
    return "caution" if sp > SPREAD_OK else "ok"


def iv_class(m: dict) -> str | None:
    if not m.get("iv_pct"):
        return None
    r = m.get("iv_hv")
    if m.get("earnings_before_expiry") or (r is not None and r >= IV_EXPENSIVE):
        return "expensive"
    if (r or 0) >= IV_RICH or (m.get("term_ratio") or 0) >= TERM_INVERTED:
        return "rich"
    if r is None or r > IV_CHEAP_MAX:
        return "fair"
    return "cheap"


def pairs(rows: list[dict]) -> list[tuple[dict, dict]]:
    """(reading at t, the same ticker's reading closest to t+15 min within 12-20 min), same session day."""
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    out = []
    for rs in by_ticker.values():
        rs.sort(key=lambda r: r["as_of"])
        for i, a in enumerate(rs):
            best, best_gap = None, None
            for b in rs[i + 1:]:
                gap = (b["as_of"] - a["as_of"]).total_seconds() / 60
                if gap > PAIR_MAX:
                    break
                if gap >= PAIR_MIN and (best_gap is None or abs(gap - PAIR_TARGET) < abs(best_gap - PAIR_TARGET)):
                    best, best_gap = b, gap
            if best is not None and best["as_of"].astimezone(ET).date() == a["as_of"].astimezone(ET).date():
                out.append((a, best))
    return out


def _pct(n: int, d: int) -> float | None:
    return round(100.0 * n / d, 1) if d else None


def _q(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 2)


def summarize(ps: list[tuple[dict, dict]]) -> dict:
    n = len(ps)
    opt_n = opt_changed = to_avoid = from_avoid = 0
    iv_n = iv_changed = 0
    d_spread: list[float] = []
    d_iv: list[float] = []
    examples = []
    for a, b in ps:
        direction = a.get("direction")
        sa, sb = side_of(a["metrics"] or {}, direction), side_of(b["metrics"] or {}, direction)
        ca, cb = option_class(sa), option_class(sb)
        if ca and cb:
            opt_n += 1
            if ca != cb:
                opt_changed += 1
                to_avoid += cb == "avoid"
                from_avoid += ca == "avoid"
            spa, spb = (sa or {}).get("spread_pct"), (sb or {}).get("spread_pct")
            if spa is not None and spb is not None:
                d_spread.append(abs(spb - spa))
                examples.append((abs(spb - spa), a, b, ca, cb, spa, spb))
        ia, ib = iv_class(a["metrics"] or {}), iv_class(b["metrics"] or {})
        if ia and ib:
            iv_n += 1
            iv_changed += ia != ib
            va, vb = (a["metrics"] or {}).get("iv_pct"), (b["metrics"] or {}).get("iv_pct")
            if va and vb:
                d_iv.append(abs(vb - va))
    examples.sort(key=lambda e: -e[0])
    return {
        "pairs": n,
        "option_verdict": {"compared": opt_n, "changed_pct": _pct(opt_changed, opt_n),
                           "became_avoid": to_avoid, "left_avoid": from_avoid},
        "spread_change_pts": {"median": _q(d_spread, 0.5), "p90": _q(d_spread, 0.9)},
        "iv_price": {"compared": iv_n, "changed_pct": _pct(iv_changed, iv_n)},
        "iv_change_pts": {"median": _q(d_iv, 0.5), "p90": _q(d_iv, 0.9)},
        "examples": [
            {"ticker": a["ticker"], "direction": a.get("direction"),
             "at_et": a["as_of"].astimezone(ET).strftime("%Y-%m-%d %H:%M"),
             "shown": f"{ca} (spread {spa:.1f}%)", "live_15m_later": f"{cb} (spread {spb:.1f}%)"}
            for _, a, b, ca, cb, spa, spb in examples[:10]
        ],
    }


def report(rows: list[dict]) -> dict:
    ps = pairs(rows)
    def early(p: tuple[dict, dict]) -> bool:
        t = p[0]["as_of"].astimezone(ET)
        return t.hour * 60 + t.minute < 10 * 60 + 30
    first_hour = [p for p in ps if early(p)]
    later = [p for p in ps if not early(p)]
    days = sorted({r["as_of"].astimezone(ET).date().isoformat() for r in rows})
    return {
        "readings": len(rows), "tickers": len({r["ticker"] for r in rows}), "sessions": days,
        "all": summarize(ps), "before_10_30_et": summarize(first_hour), "after_10_30_et": summarize(later),
    }


def _text(rep: dict) -> str:
    lines = [f"{rep['readings']} readings, {rep['tickers']} tickers, sessions: {', '.join(rep['sessions']) or '-'}"]
    for key, title in (("all", "All day"), ("before_10_30_et", "Before 10:30 ET"), ("after_10_30_et", "After 10:30 ET")):
        s = rep[key]
        ov, iv = s["option_verdict"], s["iv_price"]
        lines.append(
            f"{title}: {s['pairs']} pairs · option verdict (spread/liquidity) changed {ov['changed_pct']}% of "
            f"{ov['compared']} (became Avoid {ov['became_avoid']}, left Avoid {ov['left_avoid']}) · spread change "
            f"median {s['spread_change_pts']['median']} / p90 {s['spread_change_pts']['p90']} pts · IV price "
            f"changed {iv['changed_pct']}% · IV change median {s['iv_change_pts']['median']} / p90 "
            f"{s['iv_change_pts']['p90']} pts")
    for e in rep["all"]["examples"]:
        lines.append(f"  {e['at_et']} {e['ticker']:6} {e['direction'] or '?':4} shown {e['shown']} -> {e['live_15m_later']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m radar.option_lag", description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=10, help="look back this many days (default 10)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    from .store import RadarStore
    store = RadarStore()
    try:
        rows = store.load_option_history(datetime.now(timezone.utc) - timedelta(days=args.days))
    finally:
        store.close()
    rep = report(rows)
    print(json.dumps(rep, indent=2, default=str) if args.json else _text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
