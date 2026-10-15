"""Market context for radar names from Financial Modeling Prep: why the stock is moving (news, press
releases, 8-K filings, analyst actions), exact earnings dates (with before/after-market timing) and the
free float. Merged into each ticker's option-metrics document by radar.options.refresh; display only.

Needs FMP_API_KEY in the environment (empty: nothing is added). The key is never logged: httpx request
logging is turned down and every error text is masked.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import calendar_nyse as cal

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)      # httpx INFO lines carry the full URL with ?apikey=
logging.getLogger("httpcore").setLevel(logging.WARNING)

BASE = "https://financialmodelingprep.com"
CONTEXT = {
    "news_hours": 24,            # news and press releases this recent explain today's move
    "filings_days": 2,           # 8-K filings
    "grades_days": 3,            # analyst upgrades / downgrades
    "earnings_back_days": 5,     # earnings calendar window
    "earnings_ahead_days": 60,
    "low_float_shares": 30_000_000,
    "max_catalysts": 3,
    "workers": 6,
    "timeout_s": 10,
}
# Company-issued and analyst items explain a move better than third-party articles.
KIND_RANK = {"8-K": 0, "press": 1, "analyst": 2, "news": 3}
# FMP grade actions as words ("hold" means the rating was maintained, not a Hold rating).
ANALYST_VERB = {"upgrade": "upgrades", "downgrade": "downgrades", "hold": "maintains", "maintain": "maintains",
                "init": "starts coverage at", "initialise": "starts coverage at", "initialize": "starts coverage at",
                "reiterate": "reiterates"}

Get = Callable[[str, dict], Any]


def _key() -> str:
    return os.environ.get("FMP_API_KEY", "")


def _mask(text: Any) -> str:
    key = _key()
    return str(text).replace(key, "***") if key else str(text)


def fmp_get(path: str, params: dict | None = None) -> Any:
    """GET an FMP endpoint (JSON), None on any failure. Errors are logged with the key masked."""
    import httpx

    try:
        r = httpx.get(BASE + path, params={**(params or {}), "apikey": _key()}, timeout=CONTEXT["timeout_s"])
        if r.status_code != 200:
            logger.info("fmp %s: HTTP %s", path, r.status_code)
            return None
        body = r.json()
        if isinstance(body, dict) and ("Error Message" in body or "message" in body):
            logger.info("fmp %s: %s", path, _mask(body.get("Error Message") or body.get("message"))[:120])
            return None
        return body
    except Exception as e:  # noqa: BLE001 - context is optional
        logger.info("fmp %s: %s: %s", path, type(e).__name__, _mask(e)[:120])
        return None


# ---------------------------------------------------------------- parsing (pure)

def et_to_utc(text: str | None) -> datetime | None:
    """FMP's 'YYYY-MM-DD HH:MM:SS' timestamps (US/Eastern), ISO strings with Z, or bare dates."""
    if not text:
        return None
    t = str(text).strip()
    try:
        if t.endswith("Z") or "+" in t[10:]:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc)
        if len(t) == 10:
            return datetime.fromisoformat(t).replace(tzinfo=cal.ET).astimezone(timezone.utc)
        return datetime.fromisoformat(t.replace("T", " ")).replace(tzinfo=cal.ET).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def earnings_by_symbol(rows: list[dict] | None, today: date) -> dict[str, dict]:
    """{symbol: {"next": {"date", "time"}, "last": {...}}} from FMP's earnings calendar (time: bmo / amc / '')."""
    out: dict[str, dict] = {}
    for r in rows or []:
        sym, d = r.get("symbol"), r.get("date")
        try:
            day = date.fromisoformat(str(d)[:10])
        except (TypeError, ValueError):
            continue
        if not sym:
            continue
        item = {"date": day.isoformat(), "time": (r.get("time") or "").lower()}
        slot = out.setdefault(sym, {})
        # A report before today's open has already happened; one after today's close is still ahead.
        if day > today or (day == today and item["time"] != "bmo"):
            if "next" not in slot or day.isoformat() < slot["next"]["date"]:
                slot["next"] = item
        elif "last" not in slot or day.isoformat() > slot["last"]["date"]:
            slot["last"] = item
    return out


def earnings_before_expiry(earn_date: str | None, earn_time: str, expiry: str | None) -> bool | None:
    """Whether the report lands before the option stops trading: any day before expiry, or the expiry day
    itself when reported before the open."""
    if not earn_date or not expiry:
        return None
    return earn_date < expiry or (earn_date == expiry and earn_time == "bmo")


def catalysts_for(sym: str, now: datetime, news: list[dict], press: list[dict] | None, filings: list[dict] | None,
                  grades: list[dict] | None, params: dict = CONTEXT) -> list[dict]:
    """The items that can explain today's move, best first: 8-K, press release, analyst action, news."""
    items: list[dict] = []
    news_cut = now - timedelta(hours=params["news_hours"])
    for n in news:
        if n.get("symbol") == sym and (t := et_to_utc(n.get("publishedDate"))) and t >= news_cut:
            items.append({"kind": "news", "title": n.get("title") or "", "at": _iso(t), "source": n.get("site") or "",
                          "url": n.get("url")})
    for p in press or []:
        if (t := et_to_utc(p.get("date"))) and t >= news_cut:
            items.append({"kind": "press", "title": p.get("title") or "", "at": _iso(t), "source": "press release",
                          "url": None})
    file_cut = now - timedelta(days=params["filings_days"])
    for f in filings or []:
        if (t := et_to_utc(f.get("fillingDate") or f.get("acceptedDate"))) and t >= file_cut:
            items.append({"kind": "8-K", "title": "8-K filed with the SEC (material event)", "at": _iso(t),
                          "source": "SEC", "url": f.get("finalLink") or f.get("link")})
    grade_cut = now - timedelta(days=params["grades_days"])
    for g in grades or []:
        if (t := et_to_utc(g.get("publishedDate"))) and t >= grade_cut:
            firm, action = g.get("gradingCompany") or "Analyst", (g.get("action") or "").lower()
            prev, new = g.get("previousGrade"), g.get("newGrade")
            verb = ANALYST_VERB.get(action, action or "rates")
            change = f"{prev} → {new}" if action in ("upgrade", "downgrade") and prev and new and prev != new else (new or "")
            items.append({"kind": "analyst", "title": f"{firm} {verb} {change}".strip(), "at": _iso(t),
                          "source": firm, "url": g.get("newsURL")})
    seen: set[str] = set()
    unique = []
    for it in sorted(items, key=lambda x: (KIND_RANK[x["kind"]], -datetime.strptime(x["at"], "%Y-%m-%dT%H:%M:%SZ").timestamp())):
        key = it["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(it)
    return unique[: params["max_catalysts"]]


def float_doc(rows: list[dict] | None, params: dict = CONTEXT) -> dict | None:
    row = (rows or [None])[0]
    if not isinstance(row, dict):
        return None
    shares = row.get("floatShares")
    try:
        shares = float(shares) if shares is not None else None
    except (TypeError, ValueError):
        shares = None
    free = row.get("freeFloat")
    return {"shares": shares, "free_pct": round(float(free), 1) if isinstance(free, (int, float)) else None,
            "low": bool(shares is not None and shares < params["low_float_shares"])}


# ---------------------------------------------------------------- merge into the option-metrics documents

def add_context(docs: dict[str, dict], now_epoch: float, *, get: Get | None = None,
                params: dict = CONTEXT) -> int:
    """Add catalysts, FMP earnings dates and float to `docs` ({ticker: metrics}) in place. Returns how
    many tickers got context; 0 without an FMP key."""
    if not docs or (get is None and not _key()):
        return 0
    get = get or fmp_get
    now = datetime.fromtimestamp(now_epoch, timezone.utc)
    today = now.astimezone(cal.ET).date()
    syms = list(docs)
    cal_rows = get("/api/v3/earning_calendar", {
        "from": (today - timedelta(days=params["earnings_back_days"])).isoformat(),
        "to": (today + timedelta(days=params["earnings_ahead_days"])).isoformat()})
    earnings = earnings_by_symbol(cal_rows if isinstance(cal_rows, list) else [], today)
    news = get("/api/v3/stock_news", {"tickers": ",".join(syms), "limit": 200})
    news = news if isinstance(news, list) else []

    def per_ticker(sym: str) -> tuple[Any, Any, Any, Any]:
        return (get(f"/api/v3/press-releases/{sym}", {"limit": 5}),
                get(f"/api/v3/sec_filings/{sym}", {"type": "8-K", "limit": 3}),
                get("/api/v4/upgrades-downgrades", {"symbol": sym}),
                get("/api/v4/shares_float", {"symbol": sym}))

    with ThreadPoolExecutor(max_workers=int(params["workers"])) as pool:
        per = dict(zip(syms, pool.map(per_ticker, syms)))
    done = 0
    for sym, doc in docs.items():
        press, filings, grades, flt = per.get(sym) or (None, None, None, None)
        lst = lambda x: x if isinstance(x, list) else None  # noqa: E731
        doc["catalysts"] = catalysts_for(sym, now, news, lst(press), lst(filings), lst(grades)[:20] if lst(grades) else None,
                                         params)
        doc["float"] = float_doc(lst(flt), params)
        e = earnings.get(sym) or {}
        if e.get("next"):
            doc["earnings_date"], doc["earnings_time"] = e["next"]["date"], e["next"]["time"]
            doc["earnings_estimate"] = False
            doc["days_to_earnings"] = (date.fromisoformat(e["next"]["date"]) - today).days
            doc["earnings_before_expiry"] = earnings_before_expiry(e["next"]["date"], e["next"]["time"], doc.get("expiry"))
        if e.get("last"):
            doc["last_earnings_date"], doc["last_earnings_time"] = e["last"]["date"], e["last"]["time"]
        doc["context_source"] = "fmp"
        done += 1
    return done
