"""Extended-hours movers: plain percentage moves in pre-market and after-hours trading.

    python -m radar.extended

The radar itself does nothing outside 09:30-16:00 ET (signal-model.md 1.5). This is a separate, display-only
list for the Movers view of the radar page: the last extended-hours trade of each stock against its last
regular close. The worker runs it every EXTENDED.interval_s while the market is in pre-market or after hours
and stores the result in radar_runtime.extended (GET /api/radar returns it as `extended`).

Data (FMP, measured on the production key 2026-11-11):
- /api/v4/batch-pre-post-market-trade/{symbols}: the latest trade of every symbol in one request (542
  symbols in 0.12 s): price and timestamp, no volume.
- /api/v3/quote/{symbols}: the last regular price (the reference close), name, market cap, average volume.
- /api/v3/stock-screener: the US stocks of cap >= $2B and price >= $5, so names outside the universe are seen.
- /api/v3/historical-chart/1min/{symbol}?extended=true: 1-minute bars from 04:00 ET with volume, read only
  for the largest movers to show how much actually traded (liquid names have every minute, thin names few).

Limits the page states: extended-hours volume is thin, so a move can be a handful of shares; options do not
trade in these hours, so option metrics shown next to a name are the last regular-session reading.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from . import calendar_nyse as cal
from .config import EXTENDED, REFERENCE_SYMBOLS

logger = logging.getLogger("radar.extended")

BASE = "https://financialmodelingprep.com"
QUOTE = "/api/v3/quote/{}"
TRADES = "/api/v4/batch-pre-post-market-trade/{}"
SCREENER = "/api/v3/stock-screener"
MINUTES = "/api/v3/historical-chart/1min/{}"
Get = Callable[[str, dict], Any]


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pos(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def fmp_get(path: str, params: dict | None = None) -> Any:
    """GET an FMP endpoint (JSON), None on any failure. The key travels in a header, never in the URL."""
    import httpx

    key = os.environ.get("FMP_API_KEY", "")
    try:
        r = httpx.get(BASE + path, params=params or {}, headers={"apikey": key}, timeout=EXTENDED["fetch_timeout_s"])
        if r.status_code != 200:
            logger.info("fmp %s: HTTP %s", path.split("/")[3], r.status_code)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001 - the caller reports what is missing
        logger.info("fmp %s: %s", path.split("/")[3], type(e).__name__)
        return None


# ---------------------------------------------------------------- the window

def window(now: float) -> dict | None:
    """The extended-hours window `now` is in, or None (regular session, night, weekend, holiday).

    pre:  from 04:00 ET to the open of a session; the reference is the previous session's close.
    post: from the close to 20:00 ET; the reference is that session's close.
    `since` is the reference close: only trades after it are extended-hours trades of this window (in
    pre-market that includes the evening before, so an after-hours earnings move is listed from 04:00)."""
    dt = datetime.fromtimestamp(now, cal.UTC)
    phase, s = cal.phase_at(dt)
    if s is None or phase not in ("pre", "post"):
        return None
    ref = cal.previous_sessions(s.day, 1)[0] if phase == "pre" else s
    return {"phase": phase, "session": s.day.isoformat(), "ref_day": ref.day.isoformat(), "since": ref.close_epoch}


# ---------------------------------------------------------------- pure parts

def reference_close(quote: dict, since: int) -> float | None:
    """The last regular close from a /quote row. Outside the regular session `price` is that close (its
    timestamp is at or before the close). Should the quote ever follow extended-hours trades (a timestamp
    after the close), `previousClose` is the close instead."""
    ts = _pos(quote.get("timestamp"))
    if ts is None or ts <= since + EXTENDED["close_grace_s"]:
        return _pos(quote.get("price"))
    return _pos(quote.get("previousClose"))


def mover_rows(quotes: list[dict], trades: list[dict], meta: dict[str, dict], win: dict, now: float,
               cfg: dict = EXTENDED) -> tuple[list[dict], int]:
    """(rows, names that traded in the window). A row is a stock whose last extended-hours trade is at
    least `min_abs_pct` percent away from its last regular close: the largest `max_rows_per_side` gainers
    and as many losers, largest move first. `meta[symbol]` gives name / sector / market cap."""
    last = {}
    for t in trades:
        sym, px, ts = t.get("symbol"), _pos(t.get("price")), _pos(t.get("timestamp"))
        if isinstance(sym, str) and px and ts:
            ts = ts / 1000.0 if ts > 1e11 else ts            # the endpoint answers in milliseconds
            if win["since"] < ts <= now + 120:
                last[sym] = (px, ts)
    rows = []
    for q in quotes:
        sym = q.get("symbol")
        if not isinstance(sym, str) or sym in REFERENCE_SYMBOLS or sym not in last or sym not in meta:
            continue
        ref = reference_close(q, win["since"])
        px, ts = last[sym]
        if ref is None or ref < cfg["price_min"]:
            continue
        pct = (px / ref - 1.0) * 100.0
        if abs(pct) < cfg["min_abs_pct"] or abs(pct) > cfg["max_abs_pct"]:
            continue                                          # beyond max_abs_pct: a bad print or a split, not a move
        m = meta[sym]
        avg = _pos(q.get("avgVolume"))
        rows.append({
            "ticker": sym, "name": m.get("name") or q.get("name"), "sector": m.get("sector") or "Unknown",
            "direction": "up" if pct > 0 else "down", "price": round(px, 4), "prev_close": round(ref, 4),
            "change_pct": round(pct, 2), "traded_at": iso(ts),
            # Filled by add_volume for the largest movers: shares and dollars traded in the window.
            "day_volume": None, "dollar_volume": None, "rvol": None,
            "adv_usd": round(avg * ref) if avg else None,
            "market_cap": round(c) if (c := _pos(q.get("marketCap")) or _pos(m.get("market_cap"))) else None,
        })
    rows.sort(key=lambda r: (-abs(r["change_pct"]), r["ticker"]))
    left = {"up": int(cfg["max_rows_per_side"]), "down": int(cfg["max_rows_per_side"])}
    kept = []
    for r in rows:
        if left[r["direction"]] > 0:
            left[r["direction"]] -= 1
            kept.append(r)
    return kept, len(last)


def window_volume(bars: Any, since: int) -> tuple[int, int] | None:
    """(shares, dollars) traded after `since` from FMP 1-minute rows ("date" is US Eastern), None when
    unreadable."""
    if not isinstance(bars, list):
        return None
    shares = dollars = 0.0
    for b in bars:
        try:
            ts = datetime.strptime(b["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=cal.ET).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        v, c = _pos(b.get("volume")), _pos(b.get("close"))
        if ts >= since and v and c:
            shares += v
            dollars += v * c
    return int(shares), int(round(dollars))


def volume_picks(rows: list[dict], per_side: int) -> list[dict]:
    """The largest movers of each direction: the ones whose traded volume is worth a request."""
    left = {"up": per_side, "down": per_side}
    out = []
    for r in rows:
        if left[r["direction"]] > 0:
            left[r["direction"]] -= 1
            out.append(r)
    return out


# ---------------------------------------------------------------- the scan

def load_meta(get: Get, cfg: dict = EXTENDED) -> dict[str, dict]:
    """{symbol: name, sector, market cap} of the stocks to scan: the radar universe (GICS sectors from
    universe.csv) plus the screener's US stocks above the cap and price floors."""
    from .universe import gics_sector, load_universe

    meta = {r["symbol"]: {"name": r.get("name"), "sector": r.get("sector") or "Unknown", "market_cap": None}
            for r in load_universe() if not r.get("is_etf")}
    screen = get(SCREENER, {"marketCapMoreThan": int(cfg["cap_min"]), "priceMoreThan": cfg["price_min"],
                            "isEtf": "false", "isFund": "false", "isActivelyTrading": "true",
                            "exchange": "NYSE,NASDAQ,AMEX", "limit": 10_000})
    for row in screen if isinstance(screen, list) else []:
        sym = row.get("symbol")
        if isinstance(sym, str) and sym and sym not in meta and "." not in sym and "-" not in sym:
            meta[sym] = {"name": row.get("companyName"), "sector": gics_sector(row.get("sector") or None) or "Unknown",
                         "market_cap": row.get("marketCap")}
    return meta


def add_volume(rows: list[dict], win: dict, now: float, get: Get, cfg: dict = EXTENDED) -> int:
    """Shares and dollars traded in the window for the largest movers (one request each)."""
    picks = volume_picks(rows, int(cfg["volume_rows_per_side"]))
    if not picks:
        return 0
    first = datetime.fromtimestamp(win["since"], cal.ET).date().isoformat()
    last = datetime.fromtimestamp(now, cal.ET).date().isoformat()

    def one(r: dict) -> tuple[int, int] | None:
        return window_volume(get(MINUTES.format(r["ticker"]), {"from": first, "to": last, "extended": "true"}),
                             win["since"])

    with ThreadPoolExecutor(max_workers=int(cfg["workers"])) as pool:
        got = list(pool.map(one, picks))
    done = 0
    for r, v in zip(picks, got):
        if v is not None:
            r["day_volume"], r["dollar_volume"] = v
            done += 1
    return done


def scan(now: float, get: Get = fmp_get, cfg: dict = EXTENDED) -> dict | None:
    """One extended-hours scan, or None outside the pre-market and after-hours windows."""
    win = window(now)
    if win is None:
        return None
    t0 = time.monotonic()
    meta = load_meta(get, cfg)
    syms = sorted(meta)
    batches = [syms[i:i + int(cfg["batch"])] for i in range(0, len(syms), int(cfg["batch"]))]
    with ThreadPoolExecutor(max_workers=int(cfg["workers"])) as pool:
        quotes = list(pool.map(lambda b: get(QUOTE.format(",".join(b)), {}), batches))
        trades = list(pool.map(lambda b: get(TRADES.format(",".join(b)), {}), batches))
    failed = sum(not isinstance(x, list) for x in quotes + trades)
    doc = {"schema": 1, **win, "as_of": iso(now), "since": iso(win["since"]), "movers": [],
           "counts": {"scanned": len(syms), "traded": 0, "with_volume": 0}}
    if failed == len(quotes) + len(trades):
        return {**doc, "status": "error", "message": "The extended-hours prices could not be read."}
    flat = lambda parts: [row for p in parts if isinstance(p, list) for row in p if isinstance(row, dict)]  # noqa: E731
    rows, traded = mover_rows(flat(quotes), flat(trades), meta, win, now, cfg)
    with_volume = add_volume(rows, win, now, get, cfg)
    doc.update(movers=rows, counts={"scanned": len(syms), "traded": traded, "with_volume": with_volume},
               status="ok" if not failed else "degraded",
               message="" if not failed else "Some prices could not be read: the list may be incomplete.",
               duration_ms=int((time.monotonic() - t0) * 1000))
    return doc


def option_tickers(doc: dict, rows: dict[str, dict], now: float, cfg: dict = EXTENDED) -> list[str]:
    """Movers whose option metrics are worth reading now: never read first, then the oldest reading, skipping
    the ones read in the last `options_fresh_min` minutes. At most `options_per_scan`."""
    names = [m["ticker"] for m in doc.get("movers") or []]
    cutoff = iso(now - 60 * float(cfg["options_fresh_min"]))
    age = lambda t: str((rows.get(t) or {}).get("as_of") or "")  # noqa: E731
    stale = [t for t in names if age(t) < cutoff]
    order = sorted(range(len(stale)), key=lambda i: (age(stale[i]), i))
    return [stale[i] for i in order[:int(cfg["options_per_scan"])]]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="python -m radar.extended", description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print the scan instead of storing it")
    args = ap.parse_args(argv)
    now = time.time()
    try:
        doc = scan(now)
    except Exception as e:  # noqa: BLE001 - reported on stdout for the worker
        logger.exception("extended-hours scan failed")
        print(json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"[:200]}), flush=True)
        return 1
    if doc is None:
        print(json.dumps({"status": "closed", "message": "not in pre-market or after hours"}), flush=True)
        return 0
    if args.dry_run:
        print(json.dumps(doc), flush=True)
        return 0
    from .store import RadarStore
    store = RadarStore()
    res = {"status": doc["status"], "phase": doc["phase"], "movers": len(doc["movers"]), **doc["counts"],
           "duration_ms": doc.get("duration_ms")}
    try:
        if doc["status"] != "error":          # a failed scan keeps the previous list on the page
            store.put_extended(doc)
            # Option metrics of a few movers (Yahoo): closing quotes after hours, mostly empty in pre-market
            # (an empty read keeps the previous row). Never fails the scan.
            try:
                from . import options
                picks = option_tickers(doc, store.load_option_metrics([m["ticker"] for m in doc["movers"]]), now)
                if picks:
                    o = options.refresh(store, picks)
                    res["options"] = {"tickers": o.get("tickers"), "ok": o.get("ok")}
            except Exception:  # noqa: BLE001
                logger.warning("extended: option metrics failed", exc_info=True)
    finally:
        store.close()
    print(json.dumps(res), flush=True)
    return 0 if doc["status"] != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
