"""Unified data-source client for EdgeFlow.

Wraps yfinance, Financial Modeling Prep (FMP), Finnhub, and NewsAPI with
per-source rate limiting, TTL caching, and fallback chains.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx
import yfinance as yf

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache TTLs (seconds)
# ---------------------------------------------------------------------------
TTL_STOCK_DATA = 5 * 60
TTL_ANALYST_RATINGS = 30 * 60
TTL_NEWS = 15 * 60
TTL_OPTIONS_CHAIN = 5 * 60
TTL_EARNINGS = 60 * 60

# ---------------------------------------------------------------------------
# Rate-limit definitions: source -> (max_calls, period_seconds)
# ---------------------------------------------------------------------------
_RATE_LIMITS: dict[str, tuple[int, float]] = {
    "fmp": (250, 86_400),       # 250 calls / day
    "finnhub": (60, 60),        # 60 calls / min
    "newsapi": (100, 86_400),   # 100 calls / day
    "yfinance": (1, 0.5),       # 1 call per 0.5 s  (min delay)
}


class DataSourceClient:
    """Synchronous client that fetches market data from multiple providers."""

    def __init__(self) -> None:
        self._http = httpx.Client(timeout=20.0)

        # Rate-limiter state: {source: deque[timestamp]}
        self._rate_buckets: dict[str, deque[float]] = {
            src: deque() for src in _RATE_LIMITS
        }

        # TTL cache: {key: (value, expiry_timestamp)}
        self._cache: dict[str, tuple[Any, float]] = {}

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------
    def _get_cached(self, key: str) -> Any | None:
        """Return cached value if present and not expired, else ``None``."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._cache[key]
            return None
        return value

    def _set_cached(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store *value* in the cache with a TTL."""
        self._cache[key] = (value, time.monotonic() + ttl_seconds)

    # ------------------------------------------------------------------
    # Rate-limiter
    # ------------------------------------------------------------------
    def _wait_for_rate_limit(self, source: str) -> None:
        """Block until a rate-limit slot is available for *source*."""
        max_calls, period = _RATE_LIMITS[source]
        bucket = self._rate_buckets[source]

        now = time.monotonic()
        # Evict timestamps older than the window
        while bucket and (now - bucket[0]) >= period:
            bucket.popleft()

        if len(bucket) >= max_calls:
            wait = period - (now - bucket[0])
            if wait > 0:
                logger.debug("Rate-limited on %s — sleeping %.2fs", source, wait)
                time.sleep(wait)
            # Evict again after sleep
            now = time.monotonic()
            while bucket and (now - bucket[0]) >= period:
                bucket.popleft()

        bucket.append(time.monotonic())

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------
    def _fmp_get(self, path: str, params: dict | None = None) -> Any:
        """GET from Financial Modeling Prep API."""
        self._wait_for_rate_limit("fmp")
        params = params or {}
        params["apikey"] = settings.FMP_API_KEY
        url = f"https://financialmodelingprep.com{path}"
        resp = self._http.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _finnhub_get(self, path: str, params: dict | None = None) -> Any:
        """GET from Finnhub API."""
        self._wait_for_rate_limit("finnhub")
        params = params or {}
        params["token"] = settings.FINNHUB_API_KEY
        url = f"https://finnhub.io{path}"
        resp = self._http.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _newsapi_get(self, path: str, params: dict | None = None) -> Any:
        """GET from NewsAPI."""
        self._wait_for_rate_limit("newsapi")
        params = params or {}
        params["apiKey"] = settings.NEWSAPI_KEY
        url = f"https://newsapi.org{path}"
        resp = self._http.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_stock_data(self, ticker: str) -> dict:
        """Fetch core stock data via yfinance.

        Returns a flat dict with price, market_cap, beta, pe_ratio, sector,
        company_name, 52-week high/low, analyst count, avg analyst target, and
        average volume.
        """
        cache_key = f"stock_data:{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            self._wait_for_rate_limit("yfinance")
            info = yf.Ticker(ticker).info or {}

            result: dict[str, Any] = {
                "ticker": ticker,
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "market_cap": info.get("marketCap"),
                "avg_volume": info.get("averageDailyVolume10Day") or info.get("averageVolume"),
                "beta": info.get("beta"),
                "pe_ratio": info.get("trailingPE"),
                "sector": info.get("sector"),
                "company_name": info.get("shortName") or info.get("longName"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "analyst_count": info.get("numberOfAnalystOpinions"),
                "avg_analyst_target": info.get("targetMeanPrice"),
            }

            self._set_cached(cache_key, result, TTL_STOCK_DATA)
            return result

        except Exception:
            logger.exception("get_stock_data failed for %s", ticker)
            return {"ticker": ticker}

    # ------------------------------------------------------------------ #

    def get_analyst_ratings(
        self, ticker: str | None = None, since_hours: int = 24
    ) -> list[dict]:
        """Fetch recent analyst rating changes from FMP.

        If *ticker* is provided, returns ratings for that symbol only.
        Otherwise returns the global RSS feed of upgrades/downgrades.
        """
        cache_key = f"analyst_ratings:{ticker}:{since_hours}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            if ticker:
                raw = self._fmp_get(
                    "/api/v3/upgrades-downgrades",
                    params={"company": ticker},
                )
            else:
                raw = self._fmp_get("/api/v3/upgrades-downgrades-rss-feed")

            if not isinstance(raw, list):
                raw = []

            results: list[dict] = []
            for item in raw:
                published = item.get("publishedDate") or item.get("date", "")
                results.append(
                    {
                        "ticker": item.get("symbol", ticker or ""),
                        "firm": item.get("gradingCompany") or item.get("company", ""),
                        "analyst_name": item.get("analystName", ""),
                        "action": item.get("action") or item.get("newGrade", ""),
                        "previous_rating": item.get("previousGrade", ""),
                        "new_rating": item.get("newGrade", ""),
                        "previous_pt": item.get("previousPrice"),
                        "new_pt": item.get("newPrice") or item.get("priceTarget"),
                        "published_at": published,
                    }
                )

            self._set_cached(cache_key, results, TTL_ANALYST_RATINGS)
            return results

        except Exception:
            logger.exception("get_analyst_ratings failed (ticker=%s)", ticker)
            return []

    # ------------------------------------------------------------------ #

    def get_options_chain(self, ticker: str) -> dict:
        """Fetch options chain for the next 4 expirations via yfinance."""
        cache_key = f"options_chain:{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            self._wait_for_rate_limit("yfinance")
            yticker = yf.Ticker(ticker)
            expirations: tuple[str, ...] = yticker.options or ()
            price = (yticker.info or {}).get("currentPrice") or (
                yticker.info or {}
            ).get("regularMarketPrice")

            selected = list(expirations[:4])
            chains: dict[str, dict[str, list[dict]]] = {}

            for exp in selected:
                self._wait_for_rate_limit("yfinance")
                chain = yticker.option_chain(exp)

                def _rows(df) -> list[dict]:  # noqa: ANN001
                    cols = [
                        "strike", "bid", "ask", "lastPrice",
                        "volume", "openInterest", "impliedVolatility",
                    ]
                    out: list[dict] = []
                    for _, row in df.iterrows():
                        entry = {}
                        for c in cols:
                            val = row.get(c)
                            # Convert numpy types to native Python
                            if hasattr(val, "item"):
                                val = val.item()
                            entry[c] = val
                        out.append(entry)
                    return out

                chains[exp] = {
                    "calls": _rows(chain.calls),
                    "puts": _rows(chain.puts),
                }

            result: dict[str, Any] = {
                "ticker": ticker,
                "current_price": price,
                "expirations": list(expirations),
                "chains": chains,
            }

            self._set_cached(cache_key, result, TTL_OPTIONS_CHAIN)
            return result

        except Exception:
            logger.exception("get_options_chain failed for %s", ticker)
            return {"ticker": ticker, "expirations": [], "chains": {}}

    # ------------------------------------------------------------------ #

    def get_news(
        self, ticker: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Fetch news from Finnhub (primary) with NewsAPI fallback."""
        cache_key = f"news:{ticker}:{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        results = self._get_news_finnhub(ticker, limit)

        if not results and ticker:
            results = self._get_news_newsapi(ticker, limit)

        self._set_cached(cache_key, results, TTL_NEWS)
        return results

    def _get_news_finnhub(
        self, ticker: str | None, limit: int
    ) -> list[dict]:
        try:
            if ticker:
                now = datetime.now(tz=timezone.utc)
                from_date = now.strftime("%Y-%m-%d")
                to_date = from_date
                raw = self._finnhub_get(
                    "/api/v1/company-news",
                    params={"symbol": ticker, "from": from_date, "to": to_date},
                )
            else:
                raw = self._finnhub_get(
                    "/api/v1/news",
                    params={"category": "general"},
                )

            if not isinstance(raw, list):
                return []

            results: list[dict] = []
            for item in raw[:limit]:
                results.append(
                    {
                        "headline": item.get("headline", ""),
                        "summary": item.get("summary", ""),
                        "source": item.get("source", ""),
                        "url": item.get("url", ""),
                        "published_at": item.get("datetime", ""),
                        "category": item.get("category", ""),
                        "related_tickers": item.get("related", "").split(",")
                        if item.get("related")
                        else [ticker] if ticker else [],
                    }
                )
            return results

        except Exception:
            logger.exception("Finnhub news fetch failed (ticker=%s)", ticker)
            return []

    def _get_news_newsapi(self, ticker: str, limit: int) -> list[dict]:
        try:
            raw = self._newsapi_get(
                "/v2/everything",
                params={"q": ticker, "pageSize": limit, "sortBy": "publishedAt"},
            )
            articles = raw.get("articles", [])
            results: list[dict] = []
            for item in articles[:limit]:
                results.append(
                    {
                        "headline": item.get("title", ""),
                        "summary": item.get("description", ""),
                        "source": (item.get("source") or {}).get("name", ""),
                        "url": item.get("url", ""),
                        "published_at": item.get("publishedAt", ""),
                        "category": "company",
                        "related_tickers": [ticker],
                    }
                )
            return results

        except Exception:
            logger.exception("NewsAPI fetch failed (ticker=%s)", ticker)
            return []

    # ------------------------------------------------------------------ #

    def get_earnings_calendar(
        self, tickers: list[str] | None = None
    ) -> list[dict]:
        """Fetch upcoming earnings dates.

        Primary: yfinance ``Ticker.calendar``.
        Fallback: FMP ``/api/v3/earning_calendar``.
        """
        cache_key = f"earnings:{','.join(tickers) if tickers else 'all'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        results = self._get_earnings_yfinance(tickers)

        if not results:
            results = self._get_earnings_fmp(tickers)

        self._set_cached(cache_key, results, TTL_EARNINGS)
        return results

    def _get_earnings_yfinance(
        self, tickers: list[str] | None
    ) -> list[dict]:
        if not tickers:
            return []
        results: list[dict] = []
        for t in tickers:
            try:
                self._wait_for_rate_limit("yfinance")
                cal = yf.Ticker(t).calendar
                if cal is None:
                    continue

                # yfinance calendar can be a dict or DataFrame
                if isinstance(cal, dict):
                    earnings_date = cal.get("Earnings Date")
                    if isinstance(earnings_date, list) and earnings_date:
                        earnings_date = earnings_date[0]
                    results.append(
                        {
                            "ticker": t,
                            "earnings_date": str(earnings_date) if earnings_date else None,
                            "earnings_time": cal.get("Earnings Average", None),
                            "fiscal_quarter": cal.get("Revenue Average", None),
                        }
                    )
                else:
                    # DataFrame path
                    if hasattr(cal, "to_dict"):
                        d = cal.to_dict()
                        earnings_date = d.get("Earnings Date")
                        if isinstance(earnings_date, dict):
                            earnings_date = next(iter(earnings_date.values()), None)
                        results.append(
                            {
                                "ticker": t,
                                "earnings_date": str(earnings_date) if earnings_date else None,
                                "earnings_time": None,
                                "fiscal_quarter": None,
                            }
                        )

            except Exception:
                logger.exception("yfinance earnings fetch failed for %s", t)
        return results

    def _get_earnings_fmp(
        self, tickers: list[str] | None
    ) -> list[dict]:
        try:
            raw = self._fmp_get("/api/v3/earning_calendar")
            if not isinstance(raw, list):
                return []

            ticker_set = set(tickers) if tickers else None
            results: list[dict] = []
            for item in raw:
                sym = item.get("symbol", "")
                if ticker_set and sym not in ticker_set:
                    continue
                results.append(
                    {
                        "ticker": sym,
                        "earnings_date": item.get("date"),
                        "earnings_time": item.get("time"),  # BMO / AMC
                        "fiscal_quarter": item.get("fiscalDateEnding"),
                    }
                )
            return results

        except Exception:
            logger.exception("FMP earnings calendar fetch failed")
            return []

    # ------------------------------------------------------------------ #

    def get_insider_trades(self, ticker: str) -> list[dict]:
        """Fetch insider transactions from Finnhub."""
        cache_key = f"insider_trades:{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            raw = self._finnhub_get(
                "/api/v1/stock/insider-transactions",
                params={"symbol": ticker},
            )
            data = raw.get("data", []) if isinstance(raw, dict) else raw

            results: list[dict] = []
            for item in data:
                results.append(
                    {
                        "name": item.get("name", ""),
                        "title": item.get("filingDate", ""),
                        "transaction_type": item.get("transactionCode", ""),
                        "shares": item.get("share"),
                        "value": item.get("transactionPrice"),
                        "date": item.get("transactionDate", ""),
                    }
                )

            self._set_cached(cache_key, results, TTL_STOCK_DATA)
            return results

        except Exception:
            logger.exception("get_insider_trades failed for %s", ticker)
            return []

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()
