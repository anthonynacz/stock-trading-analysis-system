"""Unified data-source client for EdgeFlow.

Wraps yfinance, Financial Modeling Prep (FMP), Finnhub, and NewsAPI with
per-source rate limiting, TTL caching, and fallback chains.
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
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
TTL_DISCOVERY = 30 * 60

# ---------------------------------------------------------------------------
# Rate-limit definitions: source -> (max_calls, period_seconds)
# ---------------------------------------------------------------------------
_RATE_LIMITS: dict[str, tuple[int, float]] = {
    "fmp": (300, 60),            # 300 calls / min (Starter plan)
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

        Returns a flat dict with price, fundamentals, moving averages,
        short interest, volume, 52-week range, and analyst targets.
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
                "price": info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"),
                "previous_close": info.get("previousClose") or info.get("regularMarketPreviousClose"),
                "market_cap": info.get("marketCap"),
                "avg_volume": info.get("averageDailyVolume10Day") or info.get("averageVolume"),
                "beta": info.get("beta"),
                "pe_ratio": info.get("trailingPE"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "company_name": info.get("shortName") or info.get("longName"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "analyst_count": info.get("numberOfAnalystOpinions"),
                "avg_analyst_target": info.get("targetMeanPrice"),
                # Technical / moving averages
                "sma_50": info.get("fiftyDayAverage"),
                "sma_200": info.get("twoHundredDayAverage"),
                # Short interest
                "short_pct_float": info.get("shortPercentOfFloat"),
                "short_ratio": info.get("shortRatio"),
                # Today's volume (for volume confirmation signal)
                "volume": info.get("volume") or info.get("regularMarketVolume"),
            }

            self._set_cached(cache_key, result, TTL_STOCK_DATA)
            return result

        except Exception:
            logger.exception("get_stock_data failed for %s", ticker)
            return {"ticker": ticker}

    # ------------------------------------------------------------------ #

    def get_stock_age_months(self, ticker: str) -> float | None:
        """Return months since the ticker's first trade date.

        Primary source: yfinance monthly history over max period — the
        first bar's date is the listing date. Fallback:
        ``firstTradeDateEpochUtc`` from ``.info`` (missing for most large
        tickers). Used by the multi-bagger scanner to flag recent IPOs /
        spinoffs (< 24 months = structural-event signal). Cached 24h.
        """
        cache_key = f"stock_age:{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        try:
            self._wait_for_rate_limit("yfinance")
            hist = yf.Ticker(ticker).history(period="max", interval="1mo", auto_adjust=True)
            first_ts = None
            if hist is not None and len(hist) > 0:
                first_ts = hist.index[0].to_pydatetime()
                if first_ts.tzinfo is None:
                    first_ts = first_ts.replace(tzinfo=timezone.utc)
            if first_ts is None:
                # Fallback: firstTradeDateEpochUtc from .info (rarely populated)
                self._wait_for_rate_limit("yfinance")
                info = yf.Ticker(ticker).info or {}
                epoch = info.get("firstTradeDateEpochUtc")
                if not epoch:
                    return None
                first_ts = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
            months = (datetime.now(tz=timezone.utc) - first_ts).days / 30.4375
            self._set_cached(cache_key, months, 24 * 60 * 60)
            return months
        except Exception:
            logger.debug("get_stock_age_months failed for %s", ticker, exc_info=True)
            return None

    # ------------------------------------------------------------------ #

    def get_long_horizon_returns(self, ticker: str) -> dict:
        """Compute 3m / 6m / 12m total returns from yfinance price history.

        Returns a dict ``{return_3m, return_6m, return_12m}`` in decimal
        (e.g. 0.25 = +25%). Uses 14 months of data so the 12m lookback
        doesn't fall off the edge of a 1-year history (which averages
        ~251 trading days vs. the 252 we want to step back). Newly-listed
        tickers with < 12m of history return None for the longer windows.
        """
        cache_key = f"long_returns:{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        out: dict[str, float | None] = {
            "return_3m": None, "return_6m": None, "return_12m": None,
        }
        try:
            self._wait_for_rate_limit("yfinance")
            hist = yf.Ticker(ticker).history(period="14mo", auto_adjust=True)
            if hist is None or len(hist) < 10:
                return out
            closes = hist["Close"].dropna()
            if len(closes) < 10:
                return out
            latest = float(closes.iloc[-1])
            for key, approx_days in (("return_3m", 63), ("return_6m", 126), ("return_12m", 252)):
                # Need at least (approx_days + 1) bars to reach back that far;
                # for shorter histories, fall back to the earliest available bar
                # when the lookback exceeds ~80% of the window (IPOs with <1y data).
                if len(closes) > approx_days:
                    prior = float(closes.iloc[-(approx_days + 1)])
                elif key == "return_12m" and len(closes) >= int(approx_days * 0.8):
                    prior = float(closes.iloc[0])
                else:
                    continue
                if prior > 0:
                    out[key] = (latest - prior) / prior
            self._set_cached(cache_key, out, 6 * 60 * 60)
            return out
        except Exception:
            logger.debug("get_long_horizon_returns failed for %s", ticker, exc_info=True)
            return out

    # ------------------------------------------------------------------ #

    def get_daily_closes(self, ticker: str, period: str = "9mo") -> list[tuple[date, float]]:
        """Daily close series as [(date, close)], ascending. Used by the
        outcome-scoring worker to mark recommendations at trading-day
        horizons. Cached 6h (one fetch covers all pending recs per ticker)."""
        cache_key = f"daily_closes:{ticker}:{period}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        try:
            self._wait_for_rate_limit("yfinance")
            hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            if hist is None or len(hist) == 0:
                return []
            closes = hist["Close"].dropna()
            out = [(idx.date(), float(v)) for idx, v in closes.items()]
            self._set_cached(cache_key, out, 6 * 60 * 60)
            return out
        except Exception:
            logger.debug("get_daily_closes failed for %s", ticker, exc_info=True)
            return []

    # ------------------------------------------------------------------ #

    def get_income_statement_quarterly(
        self, ticker: str, limit: int = 5,
    ) -> list[dict]:
        """Fetch last N quarterly income statements from FMP.

        Returns a list sorted most-recent-first with keys at minimum:
        ``date``, ``revenue``, ``grossProfit``, ``operatingIncome``,
        ``netIncome``. Empty list on failure. Used for YoY growth and
        margin-expansion signals in the multi-bagger scanner.
        """
        if not settings.FMP_API_KEY:
            return []
        cache_key = f"income_q:{ticker}:{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        try:
            data = self._fmp_get(
                f"/api/v3/income-statement/{ticker}",
                params={"period": "quarter", "limit": limit},
            )
            if not isinstance(data, list):
                return []
            self._set_cached(cache_key, data, 6 * 60 * 60)
            return data
        except Exception:
            logger.debug("get_income_statement_quarterly failed for %s", ticker, exc_info=True)
            return []

    # ------------------------------------------------------------------ #

    def get_technical_indicators(self, ticker: str) -> dict:
        """Compute technical indicators from yfinance price history.

        Returns dict with rsi_14, momentum_5d, momentum_20d, volume_ratio,
        drawdown metrics (drawdown_1d, drawdown_2d, drawdown_3d,
        consecutive_down_days), and volume trend on down vs up days
        (down_day_volume_ratio).
        """
        cache_key = f"technicals:{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        result: dict[str, Any] = {}
        try:
            self._wait_for_rate_limit("yfinance")
            hist = yf.Ticker(ticker).history(period="2mo")
            if hist.empty or len(hist) < 15:
                return result

            closes = hist["Close"].dropna()
            volumes = hist["Volume"].dropna()

            # ── OHLC extraction (fallback-safe) ────────────────────
            ohlc_available = (
                "Open" in hist.columns and "High" in hist.columns
                and "Low" in hist.columns
                and not hist[["Open", "High", "Low"]].dropna().empty
            )
            if ohlc_available:
                opens = hist["Open"].dropna()
                highs = hist["High"].dropna()
                lows = hist["Low"].dropna()
            else:
                opens = highs = lows = None

            # ── RSI-14 (Wilder's smoothed) ──────────────────────────
            if len(closes) >= 15:
                deltas = closes.diff()
                gains = deltas.clip(lower=0)
                losses = (-deltas.clip(upper=0))
                avg_gain = gains.iloc[1:15].mean()
                avg_loss = losses.iloc[1:15].mean()
                for i in range(14, len(deltas)):
                    avg_gain = (avg_gain * 13 + gains.iloc[i]) / 14
                    avg_loss = (avg_loss * 13 + losses.iloc[i]) / 14
                if avg_loss > 0:
                    rs = avg_gain / avg_loss
                    result["rsi_14"] = 100 - (100 / (1 + rs))
                elif avg_gain > 0:
                    result["rsi_14"] = 100.0
                else:
                    result["rsi_14"] = 50.0

            # ── Momentum (percent change over N days) ───────────────
            if len(closes) >= 6:
                result["momentum_5d"] = (
                    (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6]
                ) * 100
            if len(closes) >= 21:
                result["momentum_20d"] = (
                    (closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21]
                ) * 100

            # ── Volume ratio (today vs 20-day avg) ──────────────────
            if len(volumes) >= 20 and volumes.iloc[-20:].mean() > 0:
                result["volume_ratio"] = (
                    float(volumes.iloc[-1]) / float(volumes.iloc[-20:].mean())
                )

            # ── Drawdown metrics ────────────────────────────────────
            if len(closes) >= 4:
                # 1-day, 2-day, 3-day cumulative returns (negative = drop)
                result["drawdown_1d"] = (
                    (closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2]
                ) * 100
                if len(closes) >= 3:
                    result["drawdown_2d"] = (
                        (closes.iloc[-1] - closes.iloc[-3]) / closes.iloc[-3]
                    ) * 100
                if len(closes) >= 4:
                    result["drawdown_3d"] = (
                        (closes.iloc[-1] - closes.iloc[-4]) / closes.iloc[-4]
                    ) * 100

                # Consecutive down days (counting back from today)
                daily_returns = closes.pct_change().dropna()
                consec = 0
                for ret in reversed(daily_returns.values):
                    if ret < 0:
                        consec += 1
                    else:
                        break
                result["consecutive_down_days"] = consec

            # ── Volume trend on down vs up days (last 10 days) ──────
            if len(closes) >= 11 and len(volumes) >= 11:
                daily_rets = closes.pct_change().iloc[-10:]
                daily_vols = volumes.iloc[-10:]
                down_vols = [float(v) for r, v in zip(daily_rets, daily_vols) if r < 0]
                up_vols = [float(v) for r, v in zip(daily_rets, daily_vols) if r > 0]
                if up_vols and down_vols:
                    avg_down_vol = sum(down_vols) / len(down_vols)
                    avg_up_vol = sum(up_vols) / len(up_vols)
                    if avg_up_vol > 0:
                        # >1 = heavier volume on down days (distribution)
                        # <1 = lighter volume on down days (exhaustion)
                        result["down_day_volume_ratio"] = avg_down_vol / avg_up_vol

            # ── OHLC: Upper Wick Rejection ──────────────────────────
            if ohlc_available and len(closes) >= 2 and len(highs) >= 1 and len(lows) >= 1:
                today_close = float(closes.iloc[-1])
                prior_close = float(closes.iloc[-2])
                today_high = float(highs.iloc[-1])
                today_low = float(lows.iloc[-1])
                day_range = today_high - today_low
                if (today_close > prior_close and day_range > 0
                        and not any(math.isnan(v) for v in (today_close, today_high, today_low))):
                    wick_pct = (today_high - today_close) / day_range
                    if wick_pct > 0.60:
                        result["ohlc_upper_wick_rejection"] = round(wick_pct * 100, 1)

            # ── OHLC: Gap-Down After Up Day ─────────────────────────
            if ohlc_available and len(closes) >= 3 and len(opens) >= 1:
                yesterday_close = float(closes.iloc[-2])
                prior_close_2d = float(closes.iloc[-3])
                today_open = float(opens.iloc[-1])
                if (prior_close_2d > 0 and yesterday_close > 0
                        and not any(math.isnan(v) for v in (yesterday_close, prior_close_2d, today_open))):
                    yesterday_return = ((yesterday_close - prior_close_2d) / prior_close_2d) * 100
                    gap_pct = ((today_open - yesterday_close) / yesterday_close) * 100
                    if yesterday_return > 1.0 and gap_pct < -0.5:
                        result["ohlc_gap_down_after_up"] = round(gap_pct, 2)

            # ── OHLC: Close Position in Range ───────────────────────
            if ohlc_available and len(closes) >= 1 and len(highs) >= 1 and len(lows) >= 1:
                today_close = float(closes.iloc[-1])
                today_high = float(highs.iloc[-1])
                today_low = float(lows.iloc[-1])
                day_range = today_high - today_low
                if (day_range > 0
                        and not any(math.isnan(v) for v in (today_close, today_high, today_low))):
                    result["ohlc_close_position_in_range"] = round(
                        (today_close - today_low) / day_range, 4
                    )

            self._set_cached(cache_key, result, TTL_STOCK_DATA)
        except Exception:
            logger.exception("get_technical_indicators failed for %s", ticker)

        return result

    # ------------------------------------------------------------------ #

    def get_market_benchmark(self) -> dict:
        """Fetch SPY 5d momentum for relative strength comparison.

        Cached for the session since SPY is the same for all tickers.
        """
        cache_key = "benchmark:SPY"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        result: dict[str, Any] = {}
        try:
            self._wait_for_rate_limit("yfinance")
            hist = yf.Ticker("SPY").history(period="1mo")
            if hist.empty or len(hist) < 6:
                return result

            closes = hist["Close"].dropna()
            if len(closes) >= 6:
                result["spy_momentum_5d"] = (
                    (closes.iloc[-1] - closes.iloc[-6]) / closes.iloc[-6]
                ) * 100

            self._set_cached(cache_key, result, TTL_STOCK_DATA)
        except Exception:
            logger.exception("get_market_benchmark failed")

        return result

    # ------------------------------------------------------------------ #

    def get_analyst_ratings(
        self, ticker: str | None = None, since_hours: int = 24
    ) -> list[dict]:
        """Fetch recent analyst rating changes.

        Primary: FMP ``/api/v3/upgrades-downgrades``.
        Fallback 1: Finnhub ``/stock/upgrade-downgrade``.
        Fallback 2: yfinance ``Ticker.recommendations``.
        """
        cache_key = f"analyst_ratings:{ticker}:{since_hours}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        results = self._get_ratings_fmp(ticker)

        if not results:
            results = self._get_ratings_finnhub(ticker)

        if not results and ticker:
            results = self._get_ratings_yfinance(ticker)

        self._set_cached(cache_key, results, TTL_ANALYST_RATINGS)
        return results

    def _get_ratings_fmp(self, ticker: str | None) -> list[dict]:
        """Fetch analyst grade history from Financial Modeling Prep."""
        if not ticker or not settings.FMP_API_KEY:
            return []
        try:
            raw = self._fmp_get(f"/api/v3/grade/{ticker}")
            if not isinstance(raw, list):
                return []

            results: list[dict] = []
            for item in raw:
                results.append(
                    {
                        "ticker": item.get("symbol", ticker),
                        "firm": item.get("gradingCompany", ""),
                        "analyst_name": "",
                        "action": item.get("action", ""),
                        "previous_rating": item.get("previousGrade", ""),
                        "new_rating": item.get("newGrade", ""),
                        "previous_pt": None,
                        "new_pt": None,
                        "published_at": item.get("date", ""),
                    }
                )
            return results

        except Exception:
            logger.exception("FMP ratings fetch failed for %s", ticker)
            return []

    def _get_ratings_finnhub(self, ticker: str | None) -> list[dict]:
        """Fetch upgrade/downgrade data from Finnhub."""
        if not ticker:
            return []
        try:
            raw = self._finnhub_get(
                "/api/v1/stock/upgrade-downgrade",
                params={"symbol": ticker},
            )
            if not isinstance(raw, list):
                return []

            results: list[dict] = []
            for item in raw:
                results.append(
                    {
                        "ticker": item.get("symbol", ticker),
                        "firm": item.get("company", ""),
                        "analyst_name": "",
                        "action": item.get("action", ""),
                        "previous_rating": item.get("fromGrade", ""),
                        "new_rating": item.get("toGrade", ""),
                        "previous_pt": None,
                        "new_pt": None,
                        "published_at": item.get("gradeTime", ""),
                    }
                )
            return results

        except Exception:
            logger.exception("Finnhub ratings fetch failed for %s", ticker)
            return []

    def _get_ratings_yfinance(self, ticker: str) -> list[dict]:
        """Fallback: fetch recommendations from yfinance."""
        try:
            self._wait_for_rate_limit("yfinance")
            recs = yf.Ticker(ticker).recommendations
            if recs is None or recs.empty:
                return []

            results: list[dict] = []
            for _, row in recs.tail(20).iterrows():
                results.append(
                    {
                        "ticker": ticker,
                        "firm": row.get("Firm", ""),
                        "analyst_name": "",
                        "action": row.get("Action", row.get("To Grade", "")),
                        "previous_rating": row.get("From Grade", ""),
                        "new_rating": row.get("To Grade", ""),
                        "previous_pt": None,
                        "new_pt": None,
                        "published_at": str(row.name) if hasattr(row, "name") else "",
                    }
                )
            return results

        except Exception:
            logger.exception("yfinance ratings fetch failed for %s", ticker)
            return []

    # ------------------------------------------------------------------ #

    def get_options_chain(
        self, ticker: str, *, mode: str = "default"
    ) -> dict:
        """Fetch options chain expirations via yfinance.

        Modes:
        - "default": next 4 expirations (cached short-dated view)
        - "spread": up to 8 expirations sampled across DTE buckets (0-7, 7-21,
          21-60, 60-180) — used by the deep-options analyzer for term structure
          and LEAPS-aware strategy selection.
        """
        cache_key = f"options_chain:{ticker}:{mode}"
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

            if mode == "spread":
                selected = self._spread_expirations(expirations)
            else:
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

    def get_option_contract_price(
        self, ticker: str, expiry: str, strike: float, side: str,
    ) -> float | None:
        """Fetch the last-traded price of a single specific option contract.

        Used by the positions refresh path where we need the price for an
        exact (ticker, expiry, strike, side) tuple — get_options_chain only
        returns a curated subset of expirations, so a user's contract may
        not be in the spread.

        Returns the lastPrice if found and > 0, otherwise None. side must
        be "calls" or "puts" (lowercase, matches yfinance .calls / .puts).
        """
        if side not in ("calls", "puts"):
            return None
        try:
            self._wait_for_rate_limit("yfinance")
            yticker = yf.Ticker(ticker)
            expirations = yticker.options or ()
            if expiry not in expirations:
                return None
            self._wait_for_rate_limit("yfinance")
            chain = yticker.option_chain(expiry)
            df = chain.calls if side == "calls" else chain.puts
            for _, row in df.iterrows():
                row_strike = row.get("strike")
                if row_strike is None:
                    continue
                if abs(float(row_strike) - float(strike)) < 0.01:
                    last = row.get("lastPrice")
                    if last is None:
                        return None
                    val = float(last)
                    return val if val > 0 else None
            return None
        except Exception:
            logger.exception(
                "get_option_contract_price failed for %s %s %s %s",
                ticker, expiry, strike, side,
            )
            return None

    @staticmethod
    def _spread_expirations(expirations: tuple[str, ...]) -> list[str]:
        """Pick up to 8 expirations spread across DTE buckets.

        Buckets (days-to-expiry): 0-7, 7-21, 21-45, 45-90, 90-180, 180+.
        For each bucket, take the first available expiration. Falls back to
        filling from the front if a bucket is empty.
        """
        today = datetime.now(timezone.utc).date()
        dated = []
        for exp in expirations:
            try:
                d = datetime.strptime(exp, "%Y-%m-%d").date()
            except ValueError:
                continue
            dte = (d - today).days
            if dte < 0:
                continue
            dated.append((exp, dte))

        buckets = [(0, 7), (7, 21), (21, 45), (45, 90), (90, 180), (180, 10_000)]
        picked: list[str] = []
        used = set()
        for lo, hi in buckets:
            for exp, dte in dated:
                if exp in used:
                    continue
                if lo <= dte < hi:
                    picked.append(exp)
                    used.add(exp)
                    break
        # If fewer than 6, pad with the next nearest unused
        for exp, _ in dated:
            if len(picked) >= 8:
                break
            if exp not in used:
                picked.append(exp)
                used.add(exp)
        picked.sort()
        return picked[:8]

    # ------------------------------------------------------------------ #

    def get_news(
        self, ticker: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Fetch news from Finnhub (primary), FMP, then NewsAPI fallback."""
        cache_key = f"news:{ticker}:{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        results = self._get_news_finnhub(ticker, limit)

        if not results and ticker:
            results = self._get_news_fmp(ticker, limit)

        if not results and ticker:
            results = self._get_news_newsapi(ticker, limit)

        self._set_cached(cache_key, results, TTL_NEWS)
        return results

    def _get_news_finnhub(
        self, ticker: str | None, limit: int
    ) -> list[dict]:
        try:
            if ticker:
                # 3-day window so Monday's premarket pulls Sat/Sun articles, and
                # Tuesday-after-Monday-holiday still catches the holiday. URL
                # dedup in scan_news() absorbs the overlap with prior runs.
                now = datetime.now(tz=timezone.utc)
                to_date = now.strftime("%Y-%m-%d")
                from_date = (now - timedelta(days=3)).strftime("%Y-%m-%d")
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

    def _get_news_fmp(self, ticker: str, limit: int) -> list[dict]:
        """Fetch ticker-specific news from Financial Modeling Prep."""
        if not settings.FMP_API_KEY:
            return []
        try:
            raw = self._fmp_get(
                "/api/v3/stock_news",
                params={"tickers": ticker, "limit": limit},
            )
            if not isinstance(raw, list):
                return []

            results: list[dict] = []
            for item in raw[:limit]:
                results.append(
                    {
                        "headline": item.get("title", ""),
                        "summary": item.get("text", ""),
                        "source": item.get("site", ""),
                        "url": item.get("url", ""),
                        "published_at": item.get("publishedDate", ""),
                        "category": "company",
                        "related_tickers": [item.get("symbol", ticker)],
                    }
                )
            return results

        except Exception:
            logger.exception("FMP news fetch failed (ticker=%s)", ticker)
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

        Primary: FMP ``/api/v3/earning_calendar`` (provides EPS estimates).
        Fallback: yfinance ``Ticker.calendar``.
        """
        cache_key = f"earnings:{','.join(tickers) if tickers else 'all'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        results = self._get_earnings_fmp(tickers)

        if not results:
            results = self._get_earnings_yfinance(tickers)

        self._set_cached(cache_key, results, TTL_EARNINGS)
        return results

    def _get_earnings_fmp(
        self, tickers: list[str] | None
    ) -> list[dict]:
        """Fetch earnings calendar from FMP (includes EPS estimates)."""
        if not tickers or not settings.FMP_API_KEY:
            return []
        results: list[dict] = []
        for t in tickers:
            try:
                raw = self._fmp_get(f"/api/v3/historical/earning_calendar/{t}")
                if not isinstance(raw, list):
                    continue

                for item in raw:
                    raw_date = item.get("date")
                    if not raw_date:
                        continue

                    # Derive fiscal quarter from fiscalDateEnding (e.g. "2026-03-31" → "Q1 2026")
                    fiscal_quarter = None
                    fde = item.get("fiscalDateEnding")
                    if fde and isinstance(fde, str) and len(fde) >= 7:
                        try:
                            month = int(fde[5:7])
                            year = fde[:4]
                            q = (month - 1) // 3 + 1
                            fiscal_quarter = f"Q{q} {year}"
                        except (ValueError, IndexError):
                            pass

                    # FMP returns "bmo"/"amc" for time — normalize to uppercase
                    raw_time = item.get("time", "")
                    earnings_time = raw_time.upper() if raw_time in ("bmo", "amc") else None

                    results.append(
                        {
                            "ticker": t,
                            "earnings_date": raw_date,
                            "earnings_time": earnings_time,
                            "fiscal_quarter": fiscal_quarter,
                            "consensus_eps": item.get("epsEstimated"),
                            "actual_eps": item.get("eps"),
                            "consensus_revenue": item.get("revenueEstimated"),
                            "actual_revenue": item.get("revenue"),
                        }
                    )

            except Exception:
                logger.exception("FMP earnings fetch failed for %s", t)
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

    # ------------------------------------------------------------------ #

    def get_insider_trades(self, ticker: str) -> list[dict]:
        """Fetch insider transactions.

        Primary: FMP ``/api/v4/insider-trading``.
        Fallback: Finnhub ``/stock/insider-transactions``.
        """
        cache_key = f"insider_trades:{ticker}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        results = self._get_insider_trades_fmp(ticker)

        if not results:
            results = self._get_insider_trades_finnhub(ticker)

        self._set_cached(cache_key, results, TTL_STOCK_DATA)
        return results

    def _get_insider_trades_fmp(self, ticker: str) -> list[dict]:
        """Fetch insider trades from Financial Modeling Prep."""
        if not settings.FMP_API_KEY:
            return []
        try:
            raw = self._fmp_get(
                "/api/v4/insider-trading",
                params={"symbol": ticker, "limit": 50},
            )
            if not isinstance(raw, list):
                return []

            results: list[dict] = []
            for item in raw:
                shares = item.get("securitiesTransacted")
                price = item.get("price")
                # Compute total value = shares * unit price
                value = None
                if shares is not None and price is not None:
                    try:
                        value = float(shares) * float(price)
                    except (TypeError, ValueError):
                        pass

                results.append(
                    {
                        "name": item.get("reportingName", ""),
                        "title": item.get("typeOfOwner", ""),
                        "transaction_type": item.get("transactionType", ""),
                        "shares": shares,
                        "value": value,
                        "date": item.get("transactionDate", ""),
                    }
                )
            return results

        except Exception:
            logger.exception("FMP insider trades fetch failed for %s", ticker)
            return []

    def _get_insider_trades_finnhub(self, ticker: str) -> list[dict]:
        """Fallback: fetch insider transactions from Finnhub."""
        try:
            raw = self._finnhub_get(
                "/api/v1/stock/insider-transactions",
                params={"symbol": ticker},
            )
            data = raw.get("data", []) if isinstance(raw, dict) else raw

            results: list[dict] = []
            for item in data:
                shares = item.get("share")
                price = item.get("transactionPrice")
                # Compute total value = shares * unit price
                value = None
                if shares is not None and price is not None:
                    try:
                        value = float(shares) * float(price)
                    except (TypeError, ValueError):
                        pass

                results.append(
                    {
                        "name": item.get("name", ""),
                        "title": item.get("filingDate", ""),
                        "transaction_type": item.get("transactionCode", ""),
                        "shares": shares,
                        "value": value,
                        "date": item.get("transactionDate", ""),
                    }
                )
            return results

        except Exception:
            logger.exception("Finnhub insider trades fetch failed for %s", ticker)
            return []

    # ------------------------------------------------------------------
    # Discovery (trending / active stocks)
    # ------------------------------------------------------------------

    def get_most_active(self) -> list[dict]:
        """Fetch most actively traded stocks from FMP."""
        cache_key = "discovery:most_active"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        result = self._get_fmp_market_list("/api/v3/stock/actives")
        self._set_cached(cache_key, result, TTL_DISCOVERY)
        return result

    def get_biggest_gainers(self) -> list[dict]:
        """Fetch biggest gainers from FMP."""
        cache_key = "discovery:gainers"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        result = self._get_fmp_market_list("/api/v3/stock/gainers")
        self._set_cached(cache_key, result, TTL_DISCOVERY)
        return result

    def get_biggest_losers(self) -> list[dict]:
        """Fetch biggest losers from FMP."""
        cache_key = "discovery:losers"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        result = self._get_fmp_market_list("/api/v3/stock/losers")
        self._set_cached(cache_key, result, TTL_DISCOVERY)
        return result

    def _get_fmp_market_list(self, endpoint: str) -> list[dict]:
        """Shared helper for FMP market list endpoints (actives/gainers/losers)."""
        if not settings.FMP_API_KEY:
            return []
        try:
            raw = self._fmp_get(endpoint)
            # FMP wraps results in a dict (e.g. {"mostActiveStock": [...]})
            if isinstance(raw, dict):
                # Extract the first (and only) value which is the list
                lists = [v for v in raw.values() if isinstance(v, list)]
                raw = lists[0] if lists else []
            if not isinstance(raw, list):
                return []
            return [
                {
                    "symbol": item.get("ticker") or item.get("symbol", ""),
                    "name": item.get("companyName") or item.get("name", ""),
                    "price": item.get("price"),
                    "change": item.get("changes") or item.get("change"),
                    "change_pct": item.get("changesPercentage"),
                }
                for item in raw
                if item.get("ticker") or item.get("symbol")
            ]
        except Exception:
            logger.exception("FMP market list fetch failed for %s", endpoint)
            return []

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()
