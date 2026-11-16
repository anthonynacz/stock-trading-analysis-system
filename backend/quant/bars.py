"""Daily bars into quant_daily_bars: backfill, then incremental after each close.

Source order per ticker: FMP /historical-price-full (key in the `apikey` header through radar.fetch's
HttpTransport, never in a URL), then Yahoo's v8 chart (same transport) when FMP has no key, fails or returns
nothing. Requests are paced at BARS.max_rps (production shares the FMP key). Errors are logged with the key
masked.

Rules:
- Only completed sessions are stored: today's bar only after BARS.final_after_et on a trading day.
- A ticker with stored bars is re-read from BARS.overlap_days before its last bar. When a re-read bar's close or
  adjustment factor (adj_close / close) differs from the stored one (a split or dividend re-scaled the vendor's
  history), the whole history is fetched again and replaces the stored one.
"""
from __future__ import annotations

import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from radar import calendar_nyse as cal

from .config import BARS

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

FMP_DAILY = "/historical-price-full/{}"
Reply = tuple[int, Any]


def mask(text: Any) -> str:
    key = os.environ.get("FMP_API_KEY", "")
    return str(text).replace(key, "***") if key else str(text)


def fetch_symbol(ticker: str) -> str:
    """Vendor spelling (BRK.B -> BRK-B; both FMP and Yahoo use the dash)."""
    return ticker.strip().upper().replace(".", "-").replace("/", "-")


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def last_complete_day(now: datetime, cfg: dict = BARS) -> date:
    """The latest session whose bar is final at `now` (aware)."""
    et = now.astimezone(cal.ET)
    hh, mm = (int(x) for x in cfg["final_after_et"].split(":"))
    today = et.date()
    if cal.is_trading_day(today) and (et.hour, et.minute) >= (hh, mm):
        return today
    return cal.previous_sessions(today, 1)[0].day


def _bar(ticker: str, d: date, o, h, l, c, adj, vol, source: str) -> dict | None:
    o, h, l, c, adj = _f(o), _f(h), _f(l), _f(c), _f(adj)
    if None in (o, h, l, c) or min(o, h, l, c) <= 0:
        return None
    v = _f(vol)
    return {"ticker": ticker, "bar_date": d, "open": o, "high": h, "low": l, "close": c,
            "adj_close": adj if adj and adj > 0 else None, "volume": int(v) if v is not None and v >= 0 else None,
            "source": source}


def parse_fmp(ticker: str, body: Any, first: date, last: date) -> list[dict]:
    """FMP historical-price-full body (one symbol) -> bar rows within [first, last], ascending, weekdays only."""
    rows = body.get("historical") if isinstance(body, dict) else None
    out: dict[date, dict] = {}
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        try:
            d = date.fromisoformat(str(r["date"])[:10])
        except ValueError:
            continue
        if not (first <= d <= last) or d.weekday() >= 5:
            continue
        b = _bar(ticker, d, r.get("open"), r.get("high"), r.get("low"), r.get("close"), r.get("adjClose"),
                 r.get("volume"), "fmp")
        if b:
            out[d] = b
    return [out[d] for d in sorted(out)]


def parse_yahoo(ticker: str, body: Any, first: date, last: date) -> list[dict]:
    """Yahoo v8 chart body (interval 1d) -> bar rows within [first, last], ascending. Rows are stamped at the
    session open; the ET date of the stamp is the session date."""
    try:
        res = body["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
        adj = ((res["indicators"].get("adjclose") or [{}])[0] or {}).get("adjclose") or [None] * len(ts)
    except (KeyError, IndexError, TypeError):
        return []
    out: dict[date, dict] = {}
    for i, t in enumerate(ts):
        d = datetime.fromtimestamp(int(t), timezone.utc).astimezone(cal.ET).date()
        if not (first <= d <= last) or d.weekday() >= 5:
            continue
        b = _bar(ticker, d, q["open"][i], q["high"][i], q["low"][i], q["close"][i],
                 adj[i] if i < len(adj) else None, q["volume"][i], "yahoo")
        if b:
            out[d] = b
    return [out[d] for d in sorted(out)]


def restated(stored: dict[date, dict], fresh: list[dict], tol: float = BARS["restate_tol"]) -> bool:
    """True when a re-read bar no longer matches the stored one (close, or adjustment factor adj/close)."""
    for b in fresh:
        s = stored.get(b["bar_date"])
        if not s:
            continue
        if abs(b["close"] / s["close"] - 1.0) > tol:
            return True
        fa = (b["adj_close"] / b["close"]) if b.get("adj_close") else None
        sa = (s["adj_close"] / s["close"]) if s.get("adj_close") else None
        if fa is not None and sa is not None and abs(fa / sa - 1.0) > tol:
            return True
    return False


class Pacer:
    """At most `rps` calls per second (sequential callers)."""

    def __init__(self, rps: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.gap = 1.0 / rps if rps > 0 else 0.0
        self.clock, self.sleep, self.next_at = clock, sleep, 0.0

    def wait(self) -> None:
        now = self.clock()
        if now < self.next_at:
            self.sleep(self.next_at - now)
            now = self.next_at
        self.next_at = now + self.gap


class BarSource:
    """FMP first, Yahoo fallback, one symbol per request."""

    def __init__(self, transport: Any = None, fmp_key: str | None = None, cfg: dict = BARS) -> None:
        key = (os.environ.get("FMP_API_KEY", "") if fmp_key is None else fmp_key).strip()
        if transport is None:
            from radar.fetch import HttpTransport
            transport = HttpTransport(fmp_key=key)
        self.tx, self.fmp_on, self.cfg = transport, bool(key), cfg
        self.fmp_pacer = Pacer(float(cfg["max_rps"]))
        self.yahoo_pacer = Pacer(1.0)
        self.fmp_failures = 0

    def fetch(self, ticker: str, first: date, last: date) -> tuple[list[dict], str]:
        sym = fetch_symbol(ticker)
        timeout = float(self.cfg["timeout_s"])
        if self.fmp_on and self.fmp_failures < 5:       # five FMP failures in a row: Yahoo for the rest of the run
            self.fmp_pacer.wait()
            try:
                status, body = self.tx.fmp(FMP_DAILY.format(sym), {"from": first.isoformat(), "to": last.isoformat()},
                                           timeout)
            except Exception as e:  # noqa: BLE001 - one ticker's failure falls back to Yahoo
                status, body = 0, None
                logger.info("bars %s: fmp %s: %s", ticker, type(e).__name__, mask(e)[:160])
            rows = parse_fmp(ticker, body, first, last) if status == 200 else []
            if rows:
                self.fmp_failures = 0
                return rows, "fmp"
            if status != 200:
                self.fmp_failures += 1
                logger.info("bars %s: fmp HTTP %s", ticker, status)
        self.yahoo_pacer.wait()
        p1 = int(datetime.combine(first, datetime.min.time(), tzinfo=cal.ET).timestamp())
        p2 = int(datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=cal.ET).timestamp())
        try:
            status, body = self.tx.yahoo_chart(sym, {"period1": p1, "period2": p2, "interval": "1d",
                                                     "events": "div,split"}, timeout)
        except Exception as e:  # noqa: BLE001
            logger.info("bars %s: yahoo %s: %s", ticker, type(e).__name__, mask(e)[:160])
            return [], "none"
        rows = parse_yahoo(ticker, body, first, last) if status == 200 else []
        return rows, ("yahoo" if rows else "none")


def update(store: Any, tickers: list[str], *, source: BarSource | None = None, now: datetime | None = None,
           backfill: bool = False, cfg: dict = BARS) -> dict:
    """Backfill new tickers, extend the others; returns a summary for the run log."""
    t0 = time.monotonic()
    now = now or datetime.now(timezone.utc)
    last = last_complete_day(now, cfg)
    first_full = last - timedelta(days=int(365.25 * float(cfg["backfill_years"])))
    source = source or BarSource(cfg=cfg)
    have = store.bar_last_dates()
    stats = {"tickers": len(tickers), "backfilled": [], "extended": 0, "restated": [], "up_to_date": 0,
             "failed": [], "rows": 0, "sources": {}}
    for t in tickers:
        prev = have.get(t)
        if prev is not None and prev >= last and not backfill:
            stats["up_to_date"] += 1
            continue
        try:
            if prev is None or backfill:
                rows, src = source.fetch(t, first_full, last)
                if rows:
                    stats["rows"] += store.upsert_bars(rows, replace_ticker=t)
                    stats["backfilled"].append(t)
            else:
                since = prev - timedelta(days=int(cfg["overlap_days"]))
                rows, src = source.fetch(t, since, last)
                if rows and restated(store.load_bar_window(t, since), rows, float(cfg["restate_tol"])):
                    rows, src = source.fetch(t, first_full, last)
                    if rows:
                        stats["rows"] += store.upsert_bars(rows, replace_ticker=t)
                        stats["restated"].append(t)
                elif rows:
                    stats["rows"] += store.upsert_bars(rows)
                    stats["extended"] += 1
        except Exception as e:  # noqa: BLE001 - one ticker never stops the run
            logger.warning("bars %s: %s: %s", t, type(e).__name__, mask(e)[:200])
            rows, src = [], "none"
        if not rows:
            stats["failed"].append(t)
        stats["sources"][src] = stats["sources"].get(src, 0) + 1
    stats["last_complete_day"] = last.isoformat()
    stats["duration_ms"] = int((time.monotonic() - t0) * 1000)
    ok = stats["tickers"] - len(stats["failed"])
    stats["status"] = "ok" if not stats["failed"] else ("degraded" if ok else "error")
    return stats
