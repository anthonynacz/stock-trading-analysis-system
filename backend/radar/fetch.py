"""Market data for the radar (SPEC 4.2): FMP first when FMP_API_KEY is set, then Yahoo, Nasdaq behind a breaker.

Financial Modeling Prep (paid, keyed) serves quotes, 5-minute bars, daily bars and movers when the key is in the
environment. Whatever it does not serve in a call (a failed request, a symbol it lacks, its breaker open) goes to
the keyless path below for that call, so an FMP outage costs a fallback, never a stalled tick. FMP has its own
breaker (family "fmp"). The key is sent in a header, never in a URL, and never appears in notes, health or logs.

FMP, measured on the production key on 2026-10-15 (v3 API; the "stable" batch quote is not in the plan, 402):
- /quote: the 542-symbol universe in one request (0.2-1 s). Against Yahoo's v7 quote for 520 names in session:
  volume ratio median 1.0000 (p5 0.9994), previous close identical, price within 2 bp (median 0.5). No quote type,
  market state or sector; exchanges are NASDAQ/NYSE/AMEX/CBOE (Yahoo: NMS or NGM/NYQ/PCX for Arca ETFs/BTS).
- 5-minute endpoint: at most 10 calendar days per request; regular session only unless extended=true. It serves
  a bar once it has ended, but the newest bar can hold one minute's volume until the next bar appears 3-4 minutes
  later. Its history counts the 09:30 minute twice in the first bar on some days and drops volume from some bars.
- 1-minute endpoint: at most 3 calendar days per request. A bar's last minute arrives 15-45 s after the bar ends
  (32 bar ends, 16 names) and did not change after that (one exception in 3 more samples). Its 5-minute sums
  match Yahoo's bars better than FMP's own 5-minute history (22 names x 5 sessions, per symbol-day volume vs
  Yahoo: 1-minute sums median 0.998, p5-p95 0.85-1.08; 5-minute history median 0.974, p5 0.83). So today's bars
  are 1-minute sums and the baselines' multi-day ranges stay on Yahoo by default (FMP["bars_history"]).
- Daily /historical-price-full: at most 5 symbols per request; close and volume equal to Yahoo's (1,560 rows).
- Movers: /stock_market/gainers|losers|actives return 50 rows of mostly micro caps; the stock screener plus a batch
  quote rebuilds Yahoo's screens instead (~2,100 stocks with cap >= $2B and price >= $5, ~3 s).

Transport rules come from measurements (radar/docs/data-sources.md):
- Yahoo v8 chart goes through curl_cffi with Chrome impersonation: plain requests gets 429 at once.
- The v7 quote endpoint and the predefined movers screens need a cookie and crumb, which yfinance's
  YfData manages. Nothing else from yfinance is used (yf.download takes 45-53 s for the universe).
- Nasdaq is the fallback. Its 1-minute chart stamps the ET wall clock as if it were UTC.

The breaker is kept per endpoint family (SPEC 12.1): "chart" (v8 chart: bars_5m, daily) and "crumb"
(v7 quote and screener: quotes, movers) fail independently, e.g. a 429 on the chart while quotes work.
"""
from __future__ import annotations

import calendar
import logging
import math
import os
import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from functools import cached_property, partial, wraps
from typing import Any, Protocol

import numpy as np
from curl_cffi import requests as creq
from yfinance.data import YfData
from yfinance.exceptions import YFRateLimitError

from radar.calendar_nyse import ET, phase_at, previous_sessions, session_for
from radar.config import FMP, REFERENCE_SYMBOLS, US_EXCHANGES
from radar.types import Bars, DailyBars, FetchReport, Quote
from radar.universe import gics_sector, load_universe

# httpx logs every request line, URL and query string included, at INFO. The radar's FMP key travels in a header,
# but nothing that runs in the radar's process may log a keyed URL (security requirement): request lines stay out.
for _logger in ("httpx", "httpcore"):
    logging.getLogger(_logger).setLevel(logging.WARNING)

YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{}"
YAHOO_QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_SCREENER = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
NASDAQ_CHART = "https://api.nasdaq.com/api/quote/{}/chart"
NASDAQ_WATCHLIST = "https://api.nasdaq.com/api/quote/watchlist"
NASDAQ_SCREENER = "https://api.nasdaq.com/api/screener/stocks"
NASDAQ_HEADERS = {"Accept": "application/json, text/plain, */*",
                  "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
QUOTE_FIELDS = ",".join((
    "symbol", "shortName", "longName", "quoteType", "exchange", "marketState", "regularMarketPrice",
    "regularMarketChangePercent", "regularMarketPreviousClose", "regularMarketVolume", "regularMarketTime",
    "averageDailyVolume3Month", "marketCap", "sector"))
MOVER_SCREENS = ("day_gainers", "day_losers", "most_actives")

QUOTE_BATCH = 250           # symbols per v7 request
WATCHLIST_BATCH = 20        # Nasdaq's watchlist silently drops anything past 20 symbols
SCREEN_COUNT = 250          # Yahoo's per-screen maximum
TRIES = 3
BACKOFF_S = 1.0             # sleep before retry k (k >= 1): BACKOFF_S * 2**(k-1) + U(0, JITTER_S)
JITTER_S = 0.5
MIN_WORKERS = 4
NASDAQ_WORKERS = 8
DEGRADED_FAIL_SHARE = 0.2   # a call with more failed items than this (or any 429) is degraded
BREAKER_TRIP = 3            # degraded Yahoo calls of one family in a row that open its breaker
PROBE_EVERY = 3             # while open, every 3rd fallback-capable call of the family tries Yahoo
PROBES_TO_CLOSE = 2         # good Yahoo results in a row that close it
FAMILIES = ("chart", "crumb")   # Yahoo endpoint families, each with its own breaker
FAIL_FAST = 20              # Yahoo replies in a row that are 429, 5xx or network errors (no 200 between) that
                            # end a call, graded down: a blocked runner learns it in seconds, not minutes
NETWORK_ERROR = 0           # status recorded when a request raised (or was never sent)
STATUSES = ("ok", "degraded", "down")   # best to worst
# Nasdaq's chart answers BRK.B with "Something went wrong" every time (measured), so the fallback skips it.
NASDAQ_NO_CHART = frozenset({"BRK-B"})
# Nasdaq screener filters that mirror Yahoo's predefined movers screens.
NQ_MOVER_CAP_MIN = 2e9
NQ_MOVER_PRICE_MIN = 5.0
NQ_GAINER_PCT = 3.0
NQ_LOSER_PCT = -2.5
NQ_ACTIVE_VOLUME = 5_000_000
_NQ_NUMBER_JUNK = re.compile(r"[$,%+\s]")

FMP_BASE = "https://financialmodelingprep.com/api/v3"
FMP_QUOTE = "/quote/{}"
FMP_CHART = "/historical-chart/5min/{}"
FMP_DAILY = "/historical-price-full/{}"
FMP_PROFILE = "/profile/{}"
FMP_SCREENER = "/stock-screener"
FMP_FAMILY = "fmp"
FMP_PROFILE_BATCH = 100
# FMP exchange short names -> the Yahoo codes the universe filter knows (US_EXCHANGES). FMP says AMEX for NYSE
# American and for NYSE Arca alike; Yahoo calls Arca's ETFs PCX and NYSE American's stocks ASE.
FMP_EXCHANGES = {"NASDAQ": "NMS", "NYSE": "NYQ", "AMEX": "ASE", "CBOE": "BTS", "BATS": "BTS",
                 "NYSEARCA": "PCX", "ARCA": "PCX"}
FMP_PHASES = {"pre": "PRE", "regular": "REGULAR", "post": "POST", "closed": "CLOSED"}
_FMP_RANGE = re.compile(r"(\d+)(d|wk|mo|y)")

Reply = tuple[int, Any]
# A source's attempt, run under one _Budget, and a builder of attempts for a symbol subset (None for movers).
type Attempt[T] = Callable[[_Budget], tuple[T, FetchReport]]
type Attempts[T] = Callable[[Any], Attempt[T]]


def to_nasdaq(symbol: str, sep: str = ".") -> str:
    """Yahoo BRK-B -> Nasdaq BRK.B (chart endpoint) or, with sep="/", BRK/B (watchlist).

    Measured: the chart answers BF.B and 404s on BF/B; the watchlist answers bf/b but returns an
    empty N/A row for bf.b.
    """
    return symbol.replace("-", sep)


def from_nasdaq(symbol: str) -> str:
    """Nasdaq BRK.B or BRK/B (either appears in chart, watchlist and screener rows) -> Yahoo BRK-B."""
    return symbol.strip().upper().replace("/", "-").replace(".", "-")


class Transport(Protocol):
    """HTTP behind the Fetcher. Each method returns (status, JSON body or None) within `timeout` seconds and
    may raise on network errors (a hung request raises once `timeout` has passed)."""

    def yahoo_chart(self, symbol: str, params: dict, timeout: float) -> Reply: ...

    def yahoo_quote(self, params: dict, timeout: float) -> Reply: ...

    def yahoo_screen(self, name: str, count: int, timeout: float) -> Reply: ...

    def nasdaq(self, url: str, params: dict | list, timeout: float) -> Reply: ...

    # Optional: a transport without `fmp` (or a Fetcher without a key) never uses FMP.
    def fmp(self, path: str, params: dict, timeout: float) -> Reply: ...


class HttpTransport:
    """curl_cffi Chrome sessions (one per worker thread) plus yfinance for the crumb-guarded endpoints."""

    def __init__(self, fmp_key: str = "") -> None:
        self._local = threading.local()
        self._fmp_key = fmp_key

    def __repr__(self) -> str:
        return "HttpTransport()"    # never the key

    def _session(self) -> creq.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = creq.Session(impersonate="chrome")
        return session

    def yahoo_chart(self, symbol: str, params: dict, timeout: float) -> Reply:
        r = self._session().get(YAHOO_CHART.format(symbol), params=params, timeout=timeout)
        return r.status_code, (r.json() if r.status_code == 200 else None)

    def yahoo_quote(self, params: dict, timeout: float) -> Reply:
        return self._crumbed(YAHOO_QUOTE, params, timeout)

    def yahoo_screen(self, name: str, count: int, timeout: float) -> Reply:
        # The request yf.screen(name, count=count) makes, sent here because yf.screen fixes a 30 s timeout.
        params = {"scrIds": name, "count": count, "formatted": "false", "lang": "en-US", "region": "US",
                  "corsDomain": "finance.yahoo.com"}
        status, body = self._crumbed(YAHOO_SCREENER, params, timeout)
        result = ((body or {}).get("finance") or {}).get("result")
        if status == 200 and not result:
            return NETWORK_ERROR, None      # a 200 without a result is a glitch: retry it
        return status, (result[0] if status == 200 else None)

    @staticmethod
    def _crumbed(url: str, params: dict, timeout: float) -> Reply:
        """YfData supplies the cookie and crumb; the request and its one retry with YfData's other crumb
        strategy after a 4xx (a stale cookie or crumb) are sent here, as yfinance 1.2's YfData.get sent them.
        Since 1.7, get() swallows a failed or rate-limited crumb fetch and sends the request without a crumb,
        which turns a retryable 429 or network error into a final 401. Here a rate-limited crumb is a 429 and
        a failed or missing one a network error with any yfinance (tests pin this on the installed version).
        YfData's cookie and crumb requests ignore the caller's timeout (they use 30 s), so the crumb is fetched
        with `timeout`, and the crumb plus the request run under one hard cap of `timeout` (SPEC 12.1): like
        every Transport call, a crumbed one returns within `timeout`, which the tick's deadline relies on."""
        yd = YfData()

        def send(crumb: str) -> Any:
            return yd._session.get(url, params={**params, "crumb": crumb}, timeout=timeout)

        def crumbed_get() -> Any:
            crumb, strategy = _yahoo_crumb(yd, timeout)
            r = send(crumb)
            if r.status_code >= 400:
                yd._set_cookie_strategy("csrf" if strategy == "basic" else "basic")
                r = send(_yahoo_crumb(yd, timeout)[0])
            # As YfData.get: a redirect to Yahoo's EU cookie-consent page is accepted, giving the real answer.
            return yd._accept_consent_form(r, timeout) if yd._is_this_consent_url(r.url) else r
        try:
            r = _bounded(crumbed_get, timeout)
        except YFRateLimitError:
            return 429, None
        return r.status_code, (r.json() if r.status_code == 200 else None)

    def nasdaq(self, url: str, params: dict | list, timeout: float) -> Reply:
        r = self._session().get(url, params=params, headers=NASDAQ_HEADERS, timeout=timeout)
        if r.status_code != 200:
            return r.status_code, None
        body = r.json()
        status = body.get("status") or {}
        code = status.get("rCode") or 200   # unknown symbols: HTTP 200, rCode 400
        if code != 200:
            return code, None
        if body.get("data") is None and status.get("bCodeMessage"):
            return 503, None    # HTTP 200 with "Something went wrong. Please try again later.": retry it
        return 200, body

    def fmp(self, path: str, params: dict, timeout: float) -> Reply:
        """FMP v3 GET. The key goes in the `apikey` header (FMP accepts it there), never in the query string, so
        no URL that a library logs or puts in an error message carries it. Unknown symbols are HTTP 200 with an
        empty body; a bad key is 401 (final); a refusal sent as HTTP 200 with an "Error Message" is mapped to 429
        when it is a rate limit (retried) and to 403 otherwise."""
        r = self._session().get(FMP_BASE + path, params=params, headers={"apikey": self._fmp_key}, timeout=timeout)
        if r.status_code != 200:
            return r.status_code, None
        body = r.json()
        if isinstance(body, dict) and "Error Message" in body:
            return (429 if "limit" in str(body["Error Message"]).lower() else 403), None
        return 200, body


@dataclass
class _Reply:
    status: int
    body: Any
    http_429: int


@dataclass
class _Health:
    """Breaker state of one Yahoo endpoint family."""
    status: str = "ok"
    consecutive_failures: int = 0
    last_ok_at: str | None = None
    breaker_open: bool = False
    opened_at: str | None = None
    calls_open: int = 0
    good_probes: int = 0


class _Budget:
    """What one call may still send: `tries` per request, nothing new past the fetcher's deadline and,
    for Yahoo, nothing more after FAIL_FAST failed replies in a row. Shared by the call's worker threads."""

    def __init__(self, tries: int, deadline: float | None, clock: Callable[[], float], fail_fast: int) -> None:
        self.tries = tries
        self._deadline, self._clock, self._fail_fast = deadline, clock, fail_fast
        self._lock = threading.Lock()
        self._streak = 0
        self.failed_fast = False    # FAIL_FAST failed replies in a row ended the call
        self.timed_out = False      # the deadline refused a request or a retry
        self.skipped = 0            # requests never sent

    def may_send(self, wait: float = 0.0, *, first: bool = False) -> bool:
        """Whether a request may start after `wait` seconds of backoff."""
        with self._lock:
            refused = self.failed_fast
            if not refused and self._deadline is not None and self._clock() + wait >= self._deadline:
                refused = self.timed_out = True
            if refused and first:
                self.skipped += 1
            return not refused

    def replied(self, status: int) -> None:
        with self._lock:
            if status == 200:
                self._streak = 0
            elif _retryable(status):
                self._streak += 1
                if self._fail_fast and self._streak >= self._fail_fast:
                    self.failed_fast = True


def _reports_health(method: Callable[..., tuple[Any, FetchReport]]) -> Callable[..., tuple[Any, FetchReport]]:
    """After every public call, hand the health to `on_health` (SPEC 12.1): the tick keeps it in a side
    file, so the breaker still advances when the tick is killed later. A failing callback costs a note,
    never the data just fetched."""
    @wraps(method)
    def call(self: Fetcher, *args: Any, **kwargs: Any) -> tuple[Any, FetchReport]:
        result, report = method(self, *args, **kwargs)
        if self._on_health is not None:
            try:
                self._on_health(self.health_state())
            except Exception as e:  # noqa: BLE001 - the side file is best effort
                report.notes.append(f"on_health failed: {type(e).__name__}")
        if self._fmp_key:   # defence in depth: no note ever carries the key (none is built from a URL)
            report.notes = [n.replace(self._fmp_key, "***") for n in report.notes]
        return result, report
    return call


class Fetcher:
    """One tick's data access. Pass `health_state()` back as `health=` on the next tick.

    `deadline` is an absolute time on `clock` (time.time by default), e.g. tick start + tick_timeout_s - 40:
    past it no request starts and no retry is made, so a hung or blocked source cannot outlive the tick.
    `on_health` receives `health_state()` after every public call.
    `fmp_key`: None reads FMP_API_KEY from the environment; empty (or a transport without `fmp`) means no FMP.
    `transport`, `sleep`, `clock` and `rng` exist for tests.
    """

    def __init__(self, *, health: dict | None = None, workers: int = 16, timeout_s: float = 12.0,
                 deadline: float | None = None, on_health: Callable[[dict], None] | None = None,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, rng: random.Random | None = None,
                 fmp_key: str | None = None) -> None:
        self._max_workers = max(1, workers)
        self._min_workers = min(MIN_WORKERS, self._max_workers)
        self._timeout = timeout_s
        self._fmp_key = (os.environ.get("FMP_API_KEY", "") if fmp_key is None else fmp_key).strip()
        self._tx = transport or HttpTransport(fmp_key=self._fmp_key)
        self._fmp_on = bool(self._fmp_key) and bool(FMP["enabled"]) and callable(getattr(self._tx, "fmp", None))
        self._fmp_timeout = min(timeout_s, float(FMP["timeout_s"]))
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        if deadline is not None and deadline < clock() - 86_400:
            raise ValueError(f"deadline {deadline} is not on the fetcher's clock ({clock()}): pass e.g. "
                             "tick start (time.time) + budget, not a time.monotonic() value")
        self._deadline = deadline
        self._on_health = on_health
        self._workers, self._fam = self._restore(health or {})

    # ------------------------------------------------------------------ public API (SPEC 4.2)
    @_reports_health
    def quotes(self, symbols: list[str]) -> tuple[dict[str, Quote], FetchReport]:
        symbols = _unique(symbols)
        if not symbols:
            return {}, FetchReport(source="none", status="ok")
        return self._serve_primary("crumb", symbols, lambda rest: partial(self._fmp_quotes, rest),
                                   lambda rest: partial(self._yahoo_quotes, rest),
                                   lambda rest: partial(self._nasdaq_quotes, rest))

    @_reports_health
    def bars_5m(self, symbols: list[str], *, range_: str = "1d",
                include_prepost: bool = False) -> tuple[dict[str, Bars], FetchReport]:
        """FMP serves one session ("1d") first unless FMP["bars_today"] is "yahoo". Multi-day ranges (the
        baselines) go to Yahoo first unless FMP["bars_history"] is "fmp": FMP's 5-minute history is not on the
        basis of its own 1-minute bars nor of Yahoo's (see the module docstring), so it only fills symbols Yahoo
        did not serve."""
        symbols = _unique(symbols)
        if not symbols:
            return {}, FetchReport(source="none", status="ok")
        params = {"range": range_, "interval": "5m", "includePrePost": "true" if include_prepost else "false"}
        span = self._fmp_span(range_)
        fmp = (lambda rest: partial(self._fmp_bars, rest, span, include_prepost)) if span else None
        history = span is not None and span[0] != span[1]
        # Nasdaq's chart covers one session only, so baseline ranges have no Nasdaq fallback.
        return self._serve_primary("chart", symbols, fmp,
                                   lambda rest: partial(self._yahoo_charts, rest, params, _parse_bars),
                                   lambda rest: partial(self._nasdaq_bars, rest, include_prepost)
                                   if range_ == "1d" else None,
                                   fmp_first=FMP["bars_history" if history else "bars_today"] == "fmp")

    @_reports_health
    def daily(self, symbols: list[str], *, range_: str = "3mo") -> tuple[dict[str, DailyBars], FetchReport]:
        symbols = _unique(symbols)
        if not symbols:
            return {}, FetchReport(source="none", status="ok")
        params = {"range": range_, "interval": "1d"}
        span = self._fmp_span(range_)
        return self._serve_primary("chart", symbols,
                                   (lambda rest: partial(self._fmp_daily, rest, span)) if span else None,
                                   lambda rest: partial(self._yahoo_charts, rest, params, _parse_daily),
                                   lambda rest: None)

    @_reports_health
    def movers(self) -> tuple[list[Quote], FetchReport]:
        return self._serve_primary("crumb", None, lambda _: self._fmp_movers, lambda _: self._yahoo_movers,
                                   lambda _: self._nasdaq_movers)

    def health_state(self) -> dict:
        """Per-family breakers under `families`; the top level keeps the flat fields state.json shows.

        Without FMP the top level is the worst of the two Yahoo families (last_ok_at: the older one) and
        `name` says nasdaq while either Yahoo breaker is open, exactly as before FMP. With FMP on, `families`
        also holds "fmp" and `primary` says which source leads: while FMP's breaker is closed it serves, so the
        top level is FMP's own record and `name` stays "yahoo" (the label state.json and tick._source_ok know;
        a Yahoo breaker FMP makes irrelevant does not flag the scan as on backup data). While FMP's breaker is
        open the Yahoo families drive the top level as without FMP."""
        fams = {name: _family_json(self._fam[name]) for name in FAMILIES}
        yahoo = [self._fam[name] for name in FAMILIES]
        worst = max(FAMILIES, key=lambda name: _badness(self._fam[name]))
        last_ok = [h.last_ok_at for h in yahoo if h.last_ok_at]
        state = {"schema": 1, "name": "nasdaq" if any(h.breaker_open for h in yahoo) else "yahoo",
                 "status": max((h.status for h in yahoo), key=STATUSES.index),
                 "consecutive_failures": max(h.consecutive_failures for h in yahoo),
                 "last_ok_at": min(last_ok) if last_ok else None,
                 "workers": self._workers, "breaker": fams[worst]["breaker"], "families": fams}
        if self._fmp_on:
            f = self._fam[FMP_FAMILY]
            fams[FMP_FAMILY] = _family_json(f)
            state["primary"] = "yahoo" if f.breaker_open else "fmp"
            if not f.breaker_open:
                state.update(name="yahoo", status=f.status, consecutive_failures=f.consecutive_failures,
                             last_ok_at=f.last_ok_at, breaker=fams[FMP_FAMILY]["breaker"])
        return state

    # ------------------------------------------------------------------ breaker and degraded mode
    def _restore(self, d: dict) -> tuple[int, dict[str, _Health]]:
        """Health read back from engine.json; anything malformed falls back to a fresh, closed breaker.
        A flat record from before the per-family breakers applies to both Yahoo families (not to FMP)."""
        d = d if isinstance(d, dict) else {}
        workers = _int(d.get("workers")) or self._max_workers
        families = d.get("families")
        fams = {name: _family_from(families.get(name) if isinstance(families, dict) else d) for name in FAMILIES}
        fams[FMP_FAMILY] = _family_from(families.get(FMP_FAMILY) if isinstance(families, dict) else None)
        return min(self._max_workers, max(self._min_workers, workers)), fams

    def _serve_primary[T](self, family: str, symbols: list[str] | None, fmp: Attempts[T] | None,
                          yahoo: Attempts[T], nasdaq: Callable[[Any], Attempt[T] | None],
                          *, fmp_first: bool = True) -> tuple[T, FetchReport]:
        """FMP and the Yahoo family's own routing (_serve: Yahoo, Nasdaq behind its breaker), one after the other.

        Each argument builds a source's attempt for a symbol subset (None for movers; nasdaq may give None: no
        fallback). FMP first (the default): the Yahoo family fetches only the symbols FMP did not serve and the
        results are merged; for movers a non-ok FMP answer is replaced as a whole. fmp_first=False: the Yahoo
        family first and FMP fills what it left out. Without FMP (no key, an unsupported range), past the deadline,
        or while FMP's breaker is open between probes, the call is exactly the Yahoo-first call it was before FMP."""
        start = time.perf_counter()
        expired = self._deadline is not None and self._clock() >= self._deadline
        if not self._fmp_on or fmp is None or expired:
            return self._serve(family, yahoo(symbols), nasdaq(symbols))
        if not fmp_first:
            result, report = self._serve(family, yahoo(symbols), nasdaq(symbols))
            if symbols is None or not report.failed or self._clock() >= (self._deadline or math.inf):
                return result, report
            rest = list(report.failed)
            tried = self._try_fmp(fmp(rest))
            if tried is None:
                return result, report
            result = {**tried[0], **result}
            report = _merged_report(symbols, result, report, tried[1])
        else:
            tried = self._try_fmp(fmp(symbols))
            if tried is None:                           # FMP's breaker is open and this call is not a probe
                result, report = self._serve(family, yahoo(symbols), nasdaq(symbols))
                report.notes.insert(0, "fmp breaker open")
                return result, report
            result, report = tried
            if symbols is None:
                if report.status != "ok":
                    fb, fb_report = self._serve(family, yahoo(None), nasdaq(None))
                    fb_report.http_429 += report.http_429
                    fb_report.notes.insert(0, f"fmp {report.status}: {report.ok}/{report.requested} ok")
                    result, report = fb, fb_report
            elif report.failed:
                rest = list(report.failed)
                fb, fb_report = self._serve(family, yahoo(rest), nasdaq(rest))
                result = {**fb, **result}
                report = _merged_report(symbols, result, report, fb_report)
        report.ms = int((time.perf_counter() - start) * 1000)
        return result, report

    def _try_fmp[T](self, fn: Callable[[_Budget], tuple[T, FetchReport]]) -> tuple[T, FetchReport] | None:
        """One FMP attempt under the breaker: closed, a full attempt; open, every PROBE_EVERY-th call is a probe
        (one try per request) and the others return None (not tried)."""
        h = self._fam[FMP_FAMILY]
        if h.breaker_open:
            h.calls_open += 1
            if h.calls_open % PROBE_EVERY:
                return None
            result, report = self._run(fn, 1, fail_fast=True)
            if report.status == "ok":
                report.notes.append("fmp probe ok")
        else:
            result, report = self._run(fn, TRIES, fail_fast=True)
        self._record(FMP_FAMILY, report)
        h.status = report.status
        return result, report

    def _serve[T](self, family: str, yahoo: Callable[[_Budget], tuple[T, FetchReport]],
               nasdaq: Callable[[_Budget], tuple[T, FetchReport]] | None) -> tuple[T, FetchReport]:
        """Route one call of an endpoint family: Yahoo while its breaker is closed; Nasdaq while open,
        probing Yahoo every 3rd call. Past the deadline nothing is sent and the breaker is left alone."""
        start = time.perf_counter()
        h = self._fam[family]
        expired = self._deadline is not None and self._clock() >= self._deadline
        if expired:     # every request is refused: an empty, down report
            result, report = self._run(nasdaq if h.breaker_open and nasdaq is not None else yahoo, TRIES)
        elif h.breaker_open and nasdaq is not None:
            h.calls_open += 1
            if h.calls_open % PROBE_EVERY:
                result, report = self._run(nasdaq, TRIES)
            else:
                result, report = self._run(yahoo, 1, fail_fast=True)   # the probe: one try per request
                self._record(family, report)
                if report.status == "ok":
                    report.notes.append("yahoo probe ok")
                else:
                    result, report = self._fall_back(nasdaq, report, "yahoo probe")
        else:
            result, report = self._run(yahoo, TRIES, fail_fast=True)
            self._record(family, report)
            if h.breaker_open and nasdaq is not None and report.status == "down":
                result, report = self._fall_back(nasdaq, report, "yahoo")   # this call opened the breaker
        report.ms = int((time.perf_counter() - start) * 1000)
        if not expired:
            h.status = report.status
        return result, report

    def _run[T](self, fn: Callable[[_Budget], tuple[T, FetchReport]], tries: int, *,
                fail_fast: bool = False) -> tuple[T, FetchReport]:
        """One source attempt under a fresh budget. A Yahoo call cut by fail-fast is down (SPEC 12.1);
        one cut by the deadline is at best degraded."""
        budget = _Budget(tries, self._deadline, self._clock, FAIL_FAST if fail_fast else 0)
        result, report = fn(budget)
        if budget.failed_fast:
            report.status = "down"
            report.notes.append(f"stopped after {FAIL_FAST} failed replies in a row, {budget.skipped} not requested")
        elif budget.timed_out:
            report.status = "degraded" if report.status == "ok" else report.status
            report.notes.append(f"deadline reached, {budget.skipped} not requested")
        return result, report

    def _fall_back[T](self, nasdaq: Callable[[_Budget], tuple[T, FetchReport]], yahoo_report: FetchReport,
                   label: str) -> tuple[T, FetchReport]:
        result, report = self._run(nasdaq, TRIES)
        report.http_429 += yahoo_report.http_429
        report.notes.insert(0, f"{label} {yahoo_report.status}: {yahoo_report.ok}/{yahoo_report.requested} ok")
        return result, report

    def _record(self, family: str, report: FetchReport) -> None:
        h = self._fam[family]
        ok = report.status == "ok"
        if family == "chart":   # the chart is the only Yahoo endpoint fetched by the worker pool
            self._workers = (min(self._max_workers, self._workers * 2) if ok
                             else max(self._min_workers, self._workers // 2))
        if ok:
            h.consecutive_failures = 0
            h.last_ok_at = self._stamp()
        else:
            h.consecutive_failures += 1
        if h.breaker_open:
            h.good_probes = h.good_probes + 1 if ok else 0
            if h.good_probes >= PROBES_TO_CLOSE:
                h.breaker_open, h.opened_at, h.calls_open, h.good_probes = False, None, 0, 0
        elif h.consecutive_failures >= BREAKER_TRIP:
            h.breaker_open, h.opened_at, h.calls_open, h.good_probes = True, self._stamp(), 0, 0

    def _stamp(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------------ requests
    def _request(self, call: Callable[[], Reply], budget: _Budget) -> _Reply:
        """Retry 429, 5xx and network errors with exponential backoff and jitter; other statuses are final.
        The budget refuses requests past the deadline or after fail-fast (the reply is then NETWORK_ERROR)."""
        status, body, http_429 = NETWORK_ERROR, None, 0
        for attempt in range(budget.tries):
            if attempt:
                wait = BACKOFF_S * 2 ** (attempt - 1) + self._rng.uniform(0, JITTER_S)
                if not budget.may_send(wait):
                    break
                self._sleep(wait)
            if not budget.may_send(first=not attempt):
                break
            try:
                status, body = call()
            except Exception:   # curl_cffi and yfinance raise many types for resets, timeouts and bad bodies
                status, body = NETWORK_ERROR, None
            budget.replied(status)
            http_429 += status == 429
            if status == 200 or not _retryable(status):
                break
        return _Reply(status, body if status == 200 else None, http_429)

    @cached_property
    def _etfs(self) -> frozenset[str]:
        return frozenset({r["symbol"] for r in load_universe() if r["is_etf"]} | set(REFERENCE_SYMBOLS))

    def _asset_class(self, symbol: str) -> str:
        return "etf" if symbol in self._etfs else "stocks"

    # ------------------------------------------------------------------ Yahoo
    def _yahoo_quotes(self, symbols: list[str], budget: _Budget) -> tuple[dict[str, Quote], FetchReport]:
        wanted, out, http_429 = set(symbols), {}, 0
        for batch in _chunks(symbols, QUOTE_BATCH):
            params = {"symbols": ",".join(batch), "formatted": "false", "fields": QUOTE_FIELDS,
                      "lang": "en-US", "region": "US"}
            reply = self._request(partial(self._tx.yahoo_quote, params, self._timeout), budget)
            http_429 += reply.http_429
            for row in ((reply.body or {}).get("quoteResponse") or {}).get("result") or []:
                q = _yahoo_quote(row)
                if q.symbol in wanted:
                    out[q.symbol] = q
        return out, _report("yahoo", symbols, out, http_429)

    def _yahoo_charts[T](self, symbols: list[str], params: dict, parse: Callable[[str, Any], T | None],
                      budget: _Budget) -> tuple[dict[str, T], FetchReport]:
        def fetch(symbol: str) -> _Reply:
            return self._request(partial(self._tx.yahoo_chart, symbol, params, self._timeout), budget)

        replies = _pool_map(fetch, symbols, self._workers)
        out = {}
        for symbol, reply in zip(symbols, replies, strict=True):
            parsed = _parse_or_none(parse, symbol, reply.body) if reply.status == 200 else None
            if parsed is not None:
                out[symbol] = parsed
        return out, _report("yahoo", symbols, out, sum(r.http_429 for r in replies))

    def _yahoo_movers(self, budget: _Budget) -> tuple[list[Quote], FetchReport]:
        seen: set[str] = set()
        movers: list[Quote] = []
        answered: set[str] = set()
        http_429 = 0
        for name in MOVER_SCREENS:
            reply = self._request(partial(self._tx.yahoo_screen, name, SCREEN_COUNT, self._timeout), budget)
            http_429 += reply.http_429
            if reply.status != 200:
                continue
            answered.add(name)
            for row in (reply.body or {}).get("quotes") or []:
                q = _yahoo_quote(row)
                if q.exchange in US_EXCHANGES and q.symbol not in seen:
                    seen.add(q.symbol)
                    movers.append(q)
        return movers, _report("yahoo", list(MOVER_SCREENS), answered, http_429)

    # ------------------------------------------------------------------ Nasdaq fallback
    def _nasdaq_quotes(self, symbols: list[str], budget: _Budget) -> tuple[dict[str, Quote], FetchReport]:
        """Watchlist quotes, 20 symbols per request. The screener would be one request, but it lagged a full
        session when measured (Thursday's closes on Sunday) and has no ETFs, so Stage A would lose SPY."""
        replies = self._nasdaq_gets([
            (NASDAQ_WATCHLIST, [("symbol", f"{to_nasdaq(s, '/').lower()}|{self._asset_class(s)}") for s in batch])
            for batch in _chunks(symbols, WATCHLIST_BATCH)], budget)
        wanted, out = set(symbols), {}
        for reply in replies:
            for row in (reply.body or {}).get("data") or []:
                q = _nasdaq_watch_quote(row)
                if q.symbol in wanted and q.price is not None:
                    out[q.symbol] = q
        return out, _report("nasdaq", symbols, out, sum(r.http_429 for r in replies), fallback=True)

    def _nasdaq_bars(self, symbols: list[str], include_prepost: bool,
                     budget: _Budget) -> tuple[dict[str, Bars], FetchReport]:
        """Symbols in NASDAQ_NO_CHART are not requested: they stay missing (failed) while the breaker is open."""
        charted = [s for s in symbols if s not in NASDAQ_NO_CHART]
        replies = self._nasdaq_gets([
            (NASDAQ_CHART.format(to_nasdaq(s)), {"assetclass": self._asset_class(s), "charttype": "rs"})
            for s in charted], budget)
        out = {}
        for symbol, reply in zip(charted, replies, strict=True):
            bars = (_parse_or_none(_parse_nasdaq_bars, symbol, reply.body, include_prepost)
                    if reply.status == 200 else None)
            if bars is not None:
                out[symbol] = bars
        report = _report("nasdaq", symbols, out, sum(r.http_429 for r in replies), fallback=True)
        if len(charted) < len(symbols):
            report.notes.append("no Nasdaq chart: " + ", ".join(s for s in symbols if s in NASDAQ_NO_CHART))
        return out, report

    def _nasdaq_movers(self, budget: _Budget) -> tuple[list[Quote], FetchReport]:
        [reply] = self._nasdaq_gets([(NASDAQ_SCREENER, {"tableonly": "true", "download": "true"})], budget)
        movers = _nasdaq_movers(_screener_rows(reply.body))
        answered = {"screener"} if reply.status == 200 else set()
        return movers, _report("nasdaq", ["screener"], answered, reply.http_429, fallback=True)

    def _nasdaq_gets(self, requests: list[tuple[str, dict | list]], budget: _Budget) -> list[_Reply]:
        def fetch(request: tuple[str, dict | list]) -> _Reply:
            return self._request(partial(self._tx.nasdaq, *request, self._timeout), budget)
        return _pool_map(fetch, requests, NASDAQ_WORKERS)

    # ------------------------------------------------------------------ FMP (primary when keyed)
    @cached_property
    def _universe(self) -> dict[str, dict]:
        return {r["symbol"]: r for r in load_universe()}

    def _fmp_gets(self, requests: list[tuple[str, dict]], budget: _Budget) -> list[_Reply]:
        def fetch(request: tuple[str, dict]) -> _Reply:
            return self._request(partial(self._tx.fmp, *request, self._fmp_timeout), budget)
        return _pool_map(fetch, requests, int(FMP["workers"])) if requests else []

    def _fmp_span(self, range_: str) -> tuple[date, date] | None:
        """(first, last) ET dates a Yahoo-style range covers on FMP, or None for a range FMP is not asked for.
        `last` is today once today's session has reached its pre-open, else the latest session before it;
        Nd counts sessions back from it, Nwk/Nmo/Ny calendar time back from today (as Yahoo's ranges do)."""
        m = _FMP_RANGE.fullmatch(range_)
        if not m or int(m[1]) <= 0:
            return None
        now = self._clock()
        today = datetime.fromtimestamp(now, ET).date()
        s = session_for(today)
        last = today if s is not None and now >= s.pre_open.timestamp() else previous_sessions(today, 1)[0].day
        n, unit = int(m[1]), m[2]
        if unit == "d":
            first = last if n == 1 else previous_sessions(last, n - 1)[0].day
        elif unit == "wk":
            first = today - timedelta(weeks=n)
        else:
            first = _months_back(today, n if unit == "mo" else 12 * n)
        return min(first, last), last

    def _market_state(self) -> str:
        return FMP_PHASES[phase_at(datetime.fromtimestamp(self._clock(), UTC))[0]]

    def _fmp_profiles(self, symbols: list[str], budget: _Budget) -> dict[str, dict]:
        """Profile rows (isEtf, sector, exchangeShortName, companyName) for symbols outside the universe, whose
        ETF flag and sector universe.csv cannot give. Best effort: a failed request leaves them unknown."""
        replies = self._fmp_gets([(FMP_PROFILE.format(",".join(b)), {}) for b in _chunks(symbols, FMP_PROFILE_BATCH)],
                                 budget)
        return {str(row.get("symbol")): row for r in replies for row in _rows(r.body)}

    def _fmp_quotes(self, symbols: list[str], budget: _Budget) -> tuple[dict[str, Quote], FetchReport]:
        batches = _chunks(symbols, int(FMP["quote_batch"]))
        replies = self._fmp_gets([(FMP_QUOTE.format(",".join(b)), {}) for b in batches], budget)
        wanted = set(symbols)
        rows = [row for r in replies for row in _rows(r.body) if _text(row.get("symbol")) in wanted]
        outside = [row["symbol"] for row in rows if row["symbol"] not in self._universe]
        profiles = self._fmp_profiles(outside, budget) if outside else {}
        state, out = self._market_state(), {}
        for row in rows:
            q = _fmp_quote(row, self._universe.get(row["symbol"]), profiles.get(row["symbol"]), state)
            if q.price is not None:
                out[q.symbol] = q
        return out, _report("fmp", symbols, out, sum(r.http_429 for r in replies))

    def _fmp_movers(self, budget: _Budget) -> tuple[list[Quote], FetchReport]:
        """Yahoo's three predefined screens rebuilt on FMP: the stock screener lists the US-listed stocks (no
        ETFs or funds) with cap >= $2B and price >= $5, a batch quote prices them, and the screens' thresholds
        pick the movers (as the Nasdaq fallback does). FMP's own gainers/losers/actives lists are not used: their
        50 rows are almost all micro caps, so names the radar could add (cap >= $2B) rarely appear in them."""
        params = {"marketCapMoreThan": int(NQ_MOVER_CAP_MIN), "priceMoreThan": NQ_MOVER_PRICE_MIN,
                  "isEtf": "false", "isFund": "false", "isActivelyTrading": "true",
                  "exchange": "NYSE,NASDAQ,AMEX", "limit": int(FMP["movers_screen_limit"])}
        [screen] = self._fmp_gets([(FMP_SCREENER, params)], budget)
        listed = {row["symbol"]: row for row in _rows(screen.body) if _text(row.get("symbol"))}
        replies = self._fmp_gets([(FMP_QUOTE.format(",".join(b)), {})
                                  for b in _chunks(list(listed), int(FMP["quote_batch"]))], budget)
        state = self._market_state()
        quotes = [_fmp_quote(row, None, listed[row["symbol"]], state)
                  for r in replies for row in _rows(r.body) if _text(row.get("symbol")) in listed]
        answered = {"screener"} if listed else set()
        if replies and all(r.status == 200 for r in replies):
            answered.add("quotes")
        movers = [q for q in _threshold_movers(quotes) if q.exchange in US_EXCHANGES]
        return movers, _report("fmp", ["screener", "quotes"], answered,
                               screen.http_429 + sum(r.http_429 for r in replies))

    def _fmp_bars(self, symbols: list[str], span: tuple[date, date], include_prepost: bool,
                  budget: _Budget) -> tuple[dict[str, Bars], FetchReport]:
        """One session ("1d"): FMP's 1-minute bars summed into 5-minute bars, because its 5-minute endpoint
        serves the newest bar long before it is complete (measured: a 1-minute share of the volume at +50 s,
        still incomplete at +120 s), while a bar's last 1-minute row arrives 15-45 s after its end. Longer
        ranges: the 5-minute endpoint, in windows of FMP's per-request limit. A symbol fails when any of its
        requests failed, or when it has no regular-session bar although a session in the range has been open
        for 10 minutes (an unknown symbol is an empty 200)."""
        first, last = span
        minute = first == last
        windows = [(last, last)] if minute else _fmp_windows(first, last, int(FMP["intraday_window_days"]))
        path = "/historical-chart/1min/{}" if minute else FMP_CHART
        extra = {"extended": "true"} if include_prepost else {}
        jobs = [(s, a, b) for s in symbols for a, b in windows]
        replies = self._fmp_gets([(path.format(s), {"from": a.isoformat(), "to": b.isoformat(), **extra})
                                  for s, a, b in jobs], budget)
        rows: dict[str, list] = {s: [] for s in symbols}
        bad: set[str] = set()
        for (s, _, _), reply in zip(jobs, replies, strict=True):
            if reply.status != 200 or not isinstance(reply.body, list):
                bad.add(s)
            else:
                rows[s].extend(reply.body)
        now = self._clock()
        expect = any(s.open_epoch + 600 <= now for s in _sessions_between(first, last))
        out = {}
        for s in symbols:
            if s in bad:
                continue
            bars = _parse_or_none(_parse_fmp_bars, s, rows[s], include_prepost, minute, now)
            if bars is not None and (len(bars) or not expect):
                out[s] = bars
        return out, _report("fmp", symbols, out, sum(r.http_429 for r in replies))

    def _fmp_daily(self, symbols: list[str], span: tuple[date, date],
                   budget: _Budget) -> tuple[dict[str, DailyBars], FetchReport]:
        first, _ = span
        today = datetime.fromtimestamp(self._clock(), ET).date()     # today's live row, as Yahoo's daily has it
        batches = list(_chunks(symbols, int(FMP["daily_batch"])))
        replies = self._fmp_gets([(FMP_DAILY.format(",".join(b)), {"from": first.isoformat(), "to": today.isoformat()})
                                  for b in batches], budget)
        wanted, out = set(symbols), {}
        for reply in replies:
            body = reply.body if reply.status == 200 and isinstance(reply.body, dict) else {}
            items = body.get("historicalStockList")     # several symbols; one symbol is the item itself
            for item in _rows(items) if isinstance(items, list) else ([body] if "historical" in body else []):
                sym = str(item.get("symbol", ""))
                daily = _parse_or_none(_parse_fmp_daily, sym, item.get("historical"))
                if sym in wanted and daily is not None and len(daily.day):
                    out[sym] = daily
        return out, _report("fmp", symbols, out, sum(r.http_429 for r in replies))


# ---------------------------------------------------------------------- reports
def _grade(requested: int, ok: int, http_429: int) -> str:
    if requested and not ok:
        return "down"
    if http_429 or requested - ok > DEGRADED_FAIL_SHARE * requested:
        return "degraded"
    return "ok"


def _report(source: str, requested: list[str], got: dict | set, http_429: int, *,
            fallback: bool = False) -> FetchReport:
    failed = [s for s in requested if s not in got]
    ok = len(requested) - len(failed)
    status = _grade(len(requested), ok, http_429)
    if fallback and status == "ok":
        status = "degraded"     # the fallback source is never full quality (no auctions, 1-minute H/L)
    return FetchReport(source=source, status=status, requested=len(requested), ok=ok, failed=failed,
                       http_429=http_429, notes=["nasdaq fallback"] if fallback else [])


def _merged_report(symbols: list[str], got: dict, first: FetchReport, second: FetchReport) -> FetchReport:
    """One report for a call one source served in part and the other completed (FMP and the Yahoo family, in
    either order). The source is the first one's when it served any symbol, but "nasdaq" whenever Nasdaq served
    any: the tick reads that as volume off the baselines' basis (degraded_volume). Graded on the merged coverage
    plus the Yahoo family's 429s; a call the deadline cut, or one Nasdaq helped, is at best degraded."""
    failed = [s for s in symbols if s not in got]
    ok = len(symbols) - len(failed)
    nasdaq = any(r.source == "nasdaq" and r.ok > 0 for r in (first, second))
    status = _grade(len(symbols), ok, sum(r.http_429 for r in (first, second) if r.source != "fmp"))
    if status == "ok" and (nasdaq or any(n.startswith("deadline reached") for n in first.notes + second.notes)):
        status = "degraded"
    return FetchReport(source="nasdaq" if nasdaq else (first.source if first.ok else second.source), status=status,
                       requested=len(symbols), ok=ok, failed=failed, http_429=first.http_429 + second.http_429,
                       notes=[(f"{first.source} {first.status}: {first.ok}/{first.requested} ok; "
                               f"{second.source} served {second.ok}/{second.requested}")] + first.notes + second.notes)


def _retryable(status: int) -> bool:
    return status in (NETWORK_ERROR, 429) or status >= 500


# ---------------------------------------------------------------------- health records
def _family_from(d: Any) -> _Health:
    """One family's breaker from engine.json (or a whole flat pre-family record); junk gives a fresh one."""
    d = d if isinstance(d, dict) else {}
    breaker = d.get("breaker") if isinstance(d.get("breaker"), dict) else {}
    return _Health(
        status=d.get("status") if d.get("status") in STATUSES else "ok",
        consecutive_failures=_int(d.get("consecutive_failures")) or 0,
        last_ok_at=_text(d.get("last_ok_at")),
        breaker_open=breaker.get("open") is True,
        opened_at=_text(breaker.get("opened_at")),
        calls_open=_int(breaker.get("calls")) or 0,
        good_probes=_int(breaker.get("good_probes")) or 0)


def _family_json(h: _Health) -> dict:
    return {"status": h.status, "consecutive_failures": h.consecutive_failures, "last_ok_at": h.last_ok_at,
            "breaker": {"open": h.breaker_open, "opened_at": h.opened_at, "calls": h.calls_open,
                        "good_probes": h.good_probes}}


def _badness(h: _Health) -> tuple[bool, int, int]:
    return h.breaker_open, STATUSES.index(h.status), h.consecutive_failures


def _bounded[T](fn: Callable[[], T], cap_s: float) -> T:
    """fn() on a daemon thread, given up after cap_s with TimeoutError. A thread stuck on a hung socket is
    left behind; as a daemon it never blocks the tick's exit."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller below
            box["error"] = e
    worker = threading.Thread(target=run, name="radar-yahoo-crumbed", daemon=True)
    worker.start()
    worker.join(cap_s)
    if worker.is_alive():
        raise TimeoutError(f"no answer within {cap_s:g} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _yahoo_crumb(yd: Any, timeout: float) -> tuple[str, str]:
    """(crumb, cookie strategy) from YfData, its requests bounded by `timeout`. A rate-limited getcrumb raises
    YFRateLimitError in every yfinance version. yfinance 1.7 answers a failed cookie request with no crumb
    where 1.2 raised, so no crumb raises ConnectionError: the caller then sees a network error either way."""
    crumb, strategy = yd._get_cookie_and_crumb(timeout)
    if not crumb:
        raise ConnectionError("Yahoo gave no cookie and crumb")
    return crumb, strategy


def _parse_or_none[T](parse: Callable[..., T | None], *args: Any) -> T | None:
    """Parse one symbol's body; a body in an unexpected shape fails that symbol, not the whole call."""
    try:
        return parse(*args)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError):
        return None


# ---------------------------------------------------------------------- Yahoo parsing (numpy, no pandas)
def _chart_arrays(body: Any) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    """Chart rows sorted by ts, rows without a close dropped, missing O/H/L set to the close."""
    result = ((body or {}).get("chart") or {}).get("result")
    if not result:
        return None
    res = result[0]
    ts = np.asarray(res.get("timestamp") or [], dtype=np.int64)
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    cols = {k: _column(quote.get(k), len(ts)) for k in ("open", "high", "low", "close", "volume")}
    keep = ~np.isnan(cols["close"])
    order = np.argsort(ts[keep], kind="stable")
    ts = ts[keep][order]
    c = cols["close"][keep][order]
    arrays = {"c": c, "v": np.nan_to_num(cols["volume"][keep][order]).astype(np.int64)}
    for name, short in (("open", "o"), ("high", "h"), ("low", "l")):
        x = cols[name][keep][order]
        arrays[short] = np.where(np.isnan(x), c, x)
    return ts, arrays


def _column(values: list | None, n: int) -> np.ndarray:
    if values is None or len(values) != n:
        return np.full(n, np.nan)
    return np.array(values, dtype=np.float64)   # JSON nulls become NaN


def _last_of_runs(keys: np.ndarray) -> np.ndarray:
    """Mask keeping the last row of each run of equal sorted keys."""
    return np.r_[keys[1:] != keys[:-1], True] if len(keys) else np.zeros(0, dtype=bool)


def _parse_bars(symbol: str, body: Any) -> Bars | None:
    parsed = _chart_arrays(body)
    if parsed is None:
        return None
    ts, a = parsed
    on_grid = ts % 300 == 0
    last_ts = last_px = None
    if not on_grid.all():    # Yahoo appends an off-grid "last trade" row
        i = np.flatnonzero(~on_grid)[-1]
        last_ts, last_px = int(ts[i]), float(a["c"][i])
    ts, a = ts[on_grid], {k: x[on_grid] for k, x in a.items()}
    keep = _last_of_runs(ts)
    ts = ts[keep]
    # DATA-4. Yahoo closes the newest row when the first trade after its end arrives: it folds that trade
    # into the row, and later trades go to the next row. Until then the row can still change (thin names,
    # measured live). A last trade already past the row's end means the row is closed, so it is not
    # provisional (the SPEC 12.1 wording has this backwards). Without a last-trade row nothing is known.
    provisional = last_ts is not None and len(ts) > 0 and last_ts < int(ts[-1]) + 300
    return Bars(symbol, ts, a["o"][keep], a["h"][keep], a["l"][keep], a["c"][keep], a["v"][keep],
                last_trade_ts=last_ts, last_trade_px=last_px, provisional_last=provisional)


def _parse_daily(symbol: str, body: Any) -> DailyBars | None:
    parsed = _chart_arrays(body)
    if parsed is None:
        return None
    ts, a = parsed
    if not len(ts):
        return DailyBars(symbol, np.zeros(0, dtype="datetime64[D]"), a["o"], a["h"], a["l"], a["c"], a["v"])
    # Daily rows are stamped at the open (or the last trade for today), so one ET offset dates them all.
    offset = int(datetime.fromtimestamp(int(ts[-1]), ET).utcoffset().total_seconds())
    days = (ts + offset) // 86400
    keep = _last_of_runs(days)
    return DailyBars(symbol, days[keep].astype("datetime64[D]"), a["o"][keep], a["h"][keep], a["l"][keep],
                     a["c"][keep], a["v"][keep])


def _yahoo_quote(row: dict) -> Quote:
    return Quote(
        symbol=str(row.get("symbol", "")),
        price=_float(row.get("regularMarketPrice")),
        prev_close=_float(row.get("regularMarketPreviousClose")),
        change_pct=_float(row.get("regularMarketChangePercent")),
        day_volume=_int(row.get("regularMarketVolume")),
        market_time=_int(row.get("regularMarketTime")),
        market_state=row.get("marketState"),
        avg_volume_3m=_int(row.get("averageDailyVolume3Month")),
        market_cap=_float(row.get("marketCap")),
        exchange=row.get("exchange"),
        quote_type=row.get("quoteType"),
        name=row.get("longName") or row.get("shortName"),
        sector=gics_sector(row.get("sector")))


def _float(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _int(v: Any) -> int | None:
    f = _float(v)
    return None if f is None else int(f)


def _text(v: Any) -> str | None:
    return v if isinstance(v, str) else None


# ---------------------------------------------------------------------- Nasdaq parsing
def _nq_float(v: Any) -> float | None:
    """Nasdaq numbers arrive as strings like "$1,234.50", "+1.53%" or "N/A"."""
    if not isinstance(v, str):
        return _float(v)
    try:
        f = float(_NQ_NUMBER_JUNK.sub("", v))
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _nq_int(v: Any) -> int | None:
    f = _nq_float(v)
    return None if f is None else int(f)


def _nasdaq_watch_quote(row: dict) -> Quote:
    price, prev_close = _nq_float(row.get("lastSalePrice")), _nq_float(row.get("previousClosePrice"))
    change_pct = _nq_float(row.get("percentageChange"))
    if change_pct is None and price is not None and prev_close:
        change_pct = round((price / prev_close - 1) * 100, 4)   # blank when it rounds to 0.00%
    return Quote(
        symbol=from_nasdaq(str(row.get("symbol", ""))),
        price=price,
        prev_close=prev_close,
        change_pct=change_pct,
        day_volume=_nq_int(row.get("volume")),
        quote_type="ETF" if str(row.get("assetClass", "")).upper() == "ETF" else "EQUITY",
        name=row.get("companyName"))


def _screener_rows(body: Any) -> list[dict]:
    data = (body or {}).get("data") or {}
    return data.get("rows") or (data.get("table") or {}).get("rows") or []


def _nasdaq_screener_quote(row: dict) -> Quote:
    # The screener has no exchange column. Its rows are US-listed, but exchange stays None, so
    # dynamic_candidates skips them: a dynamic add needs Yahoo baselines anyway.
    price, change = _nq_float(row.get("lastsale")), _nq_float(row.get("netchange"))
    return Quote(
        symbol=from_nasdaq(str(row.get("symbol", ""))),
        price=price,
        prev_close=None if price is None or change is None else round(price - change, 4),
        change_pct=_nq_float(row.get("pctchange")),
        day_volume=_nq_int(row.get("volume")),
        market_cap=_nq_float(row.get("marketCap")),
        quote_type="EQUITY",
        name=row.get("name"),
        sector=gics_sector(row.get("sector")))


def _nasdaq_movers(rows: list[dict]) -> list[Quote]:
    """Gainers, losers and most actives from the screener, with Yahoo's predefined-screen thresholds."""
    return _threshold_movers(list(map(_nasdaq_screener_quote, rows)))


def _threshold_movers(quotes: list[Quote]) -> list[Quote]:
    """Yahoo's predefined screens rebuilt from quotes: day_gainers (> +3%), day_losers (< -2.5%), most_actives
    (> 5M shares), each cap >= $2B and price >= $5, at most SCREEN_COUNT per screen, deduplicated in that order."""
    big = [q for q in quotes
           if (q.market_cap or 0) >= NQ_MOVER_CAP_MIN and (q.price or 0) >= NQ_MOVER_PRICE_MIN
           and q.change_pct is not None]
    gainers = sorted((q for q in big if q.change_pct > NQ_GAINER_PCT), key=lambda q: -q.change_pct)
    losers = sorted((q for q in big if q.change_pct < NQ_LOSER_PCT), key=lambda q: q.change_pct)
    actives = sorted((q for q in big if (q.day_volume or 0) > NQ_ACTIVE_VOLUME), key=lambda q: -(q.day_volume or 0))
    seen: set[str] = set()
    movers = []
    for q in gainers[:SCREEN_COUNT] + losers[:SCREEN_COUNT] + actives[:SCREEN_COUNT]:
        if q.symbol not in seen:
            seen.add(q.symbol)
            movers.append(q)
    return movers


def _parse_nasdaq_bars(symbol: str, body: Any, include_prepost: bool) -> Bars | None:
    """Resample Nasdaq's 1-minute last-price-and-shares points (charttype=rs) to 5-minute bars."""
    data = (body or {}).get("data")
    if not isinstance(data, dict):
        return None
    points = data.get("chart") or []
    x = np.array([p.get("x") for p in points], dtype=np.float64)
    y = np.array([p.get("y") for p in points], dtype=np.float64)
    w = np.array([p.get("w") for p in points], dtype=np.float64)
    keep = ~(np.isnan(x) | np.isnan(y))
    wall, y, w = (x[keep] // 1000).astype(np.int64), y[keep], np.nan_to_num(w[keep])
    if not len(wall):
        return _empty_bars(symbol)
    # x is the ET wall clock written as UTC ms; the chart spans 04:00-20:00, clear of the 02:00 DST switch.
    day = datetime.fromtimestamp(int(wall[0]), UTC).date()
    offset = int(datetime.combine(day, dtime(12), ET).utcoffset().total_seconds())
    ts = wall - offset
    order = np.argsort(ts, kind="stable")
    ts, y, w = ts[order], y[order], w[order]
    if not include_prepost:
        session = session_for(day)
        inside = ((ts >= session.open_epoch) & (ts < session.close_epoch) if session
                  else np.zeros(len(ts), dtype=bool))
        ts, y, w = ts[inside], y[inside], w[inside]
    if not len(ts):
        return _empty_bars(symbol)
    bucket = ts - ts % 300
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(bucket)] - 1
    return Bars(symbol, bucket[starts], y[starts], np.maximum.reduceat(y, starts), np.minimum.reduceat(y, starts),
                y[ends], np.rint(np.add.reduceat(w, starts)).astype(np.int64))


# ---------------------------------------------------------------------- FMP parsing
def _rows(body: Any) -> list[dict]:
    return [r for r in body if isinstance(r, dict)] if isinstance(body, list) else []


def _months_back(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _sessions_between(first: date, last: date) -> Iterator[Any]:
    d = first
    while d <= last:
        s = session_for(d)
        if s is not None:
            yield s
        d += timedelta(days=1)


def _fmp_windows(first: date, last: date, size: int) -> list[tuple[date, date]]:
    """[first, last] cut into request windows of at most `size` calendar days, newest first. Each window ends on a
    session day, and stretches holding no session are not requested."""
    out, end = [], last
    while end >= first:
        while end >= first and session_for(end) is None:
            end -= timedelta(days=1)
        if end < first:
            break
        start = max(first, end - timedelta(days=size - 1))
        out.append((start, end))
        end = start - timedelta(days=1)
    return out


def _fmp_epochs(stamps: list[str]) -> np.ndarray:
    """FMP's "YYYY-MM-DD HH:MM:SS" (US/Eastern wall clock, bar start) -> UTC epoch seconds. The ET offset is taken
    at noon of each date: bars run 04:00-20:00, clear of the 02:00 DST switch."""
    base: dict[str, int] = {}
    out = np.empty(len(stamps), dtype=np.int64)
    for i, s in enumerate(stamps):
        b = base.get(s[:10])
        if b is None:
            day = date.fromisoformat(s[:10])
            b = base[s[:10]] = (int(datetime.combine(day, dtime(0), UTC).timestamp())
                                - int(datetime.combine(day, dtime(12), ET).utcoffset().total_seconds()))
        if len(s) != 19 or s[10] != " ":
            raise ValueError(f"unexpected FMP date {s!r}")
        out[i] = b + int(s[11:13]) * 3600 + int(s[14:16]) * 60 + int(s[17:19])
    return out


def _parse_fmp_bars(symbol: str, rows: list[dict], include_prepost: bool, minute: bool, now: float) -> Bars:
    """FMP intraday rows (newest first, ET bar starts, float volumes) -> Bars on the Yahoo contract: ascending
    unique UTC bar starts on the 5-minute grid, regular session only unless include_prepost, int64 volume.
    1-minute rows are summed into 5-minute bars (open first, high max, low min, close last, volume sum).

    provisional_last (FMP has no last-trade row): a 1-minute aggregate whose last minute has not arrived is
    provisional until FMP["settle_s"] after its end. Measured live (32 bar ends, 16 names): the last minute
    arrived 15-45 s after the bar's end and the bar did not change after that (in 3 earlier samples one arrived
    minute was revised within 50 s). A thin name's last minute may never come (no trade): the clock settles it.
    Not covered: the session's last bar gains the closing auction in its last minute 30-130 s after the close,
    after that minute has arrived. FMP's 5-minute endpoint rebuilds its newest bar only when the next one appears
    (3-4 minutes after the end; until then it can hold one minute's volume), so that bar stays provisional until
    5 minutes + settle_s after its end."""
    rows = [r for r in rows if r.get("close") is not None and r.get("date")]
    if not rows:
        return _empty_bars(symbol)
    ts = _fmp_epochs([str(r["date"]) for r in rows])
    cols = {k: np.array([_nan_float(r.get(k)) for r in rows], dtype=np.float64)
            for k in ("open", "high", "low", "close", "volume")}
    order = np.argsort(ts, kind="stable")
    ts = ts[order]
    c = cols["close"][order]
    keep = np.isfinite(c)
    if not include_prepost:
        sessions: dict[int, Any] = {}
        inside = np.zeros(len(ts), dtype=bool)
        for i, t in enumerate(ts):
            day = (int(t) - 4 * 3600) // 86400       # any instant 04:00-20:00 ET lies on its ET date in UTC-4h
            if day not in sessions:
                sessions[day] = session_for(date.fromordinal(day + 719163))
            s = sessions[day]
            inside[i] = s is not None and s.open_epoch <= t < s.close_epoch
        keep &= inside
    ts, c = ts[keep], c[keep]
    a = {}
    for k in ("open", "high", "low"):
        x = cols[k][order][keep]
        a[k] = np.where(np.isfinite(x), x, c)       # a missing open/high/low is the close, as for Yahoo
    v = np.nan_to_num(cols["volume"][order][keep])
    last = _last_of_runs(ts)                        # a repeated stamp: the later row wins
    ts, c, v, a = ts[last], c[last], v[last], {k: x[last] for k, x in a.items()}
    if not len(ts):
        return _empty_bars(symbol)
    settle = float(FMP["settle_s"])
    if minute:
        bucket = ts - ts % 300
        starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
        ends = np.r_[starts[1:], len(bucket)] - 1
        complete = int(ts[-1]) >= int(bucket[-1]) + 240         # the newest bar's last minute is in
        ts, o, h, lo, c = (bucket[starts], a["open"][starts], np.maximum.reduceat(a["high"], starts),
                           np.minimum.reduceat(a["low"], starts), c[ends])
        v = np.add.reduceat(v, starts)
        provisional = not complete and now < int(ts[-1]) + 300 + settle
    else:
        on_grid = ts % 300 == 0
        ts, c, v = ts[on_grid], c[on_grid], v[on_grid]
        o, h, lo = a["open"][on_grid], a["high"][on_grid], a["low"][on_grid]
        provisional = bool(len(ts)) and now < int(ts[-1]) + 600 + settle
    return Bars(symbol, ts, o, h, lo, c, np.rint(v).astype(np.int64), provisional_last=provisional)


def _parse_fmp_daily(symbol: str, rows: Any) -> DailyBars | None:
    """FMP daily rows (newest first; close is split-adjusted like Yahoo's, adjClose also has dividends) ->
    DailyBars ascending by session date, one row per date (the later row wins), today's live row included."""
    if not isinstance(rows, list):
        return None
    rows = [r for r in rows if isinstance(r, dict) and r.get("date") and _float(r.get("close")) is not None]
    rows.sort(key=lambda r: str(r["date"]))
    day = np.array([str(r["date"])[:10] for r in rows], dtype="datetime64[D]")
    c = np.array([r["close"] for r in rows], dtype=np.float64)
    keep = _last_of_runs(day.astype(np.int64))
    cols = {k: np.array([_nan_float(r.get(k)) for r in rows], dtype=np.float64) for k in ("open", "high", "low")}
    ohl = {k: np.where(np.isfinite(x), x, c) for k, x in cols.items()}
    v = np.rint(np.nan_to_num(np.array([_nan_float(r.get("volume")) for r in rows], dtype=np.float64)))
    return DailyBars(symbol, day[keep], ohl["open"][keep], ohl["high"][keep], ohl["low"][keep], c[keep],
                     v[keep].astype(np.int64))


def _fmp_exchange(code: Any, etf: bool) -> str | None:
    """FMP exchange short name -> Yahoo code (unknown codes, e.g. OTC, pass through and fail the US filter)."""
    if not isinstance(code, str) or not code.strip():
        return None
    c = code.strip().upper()
    return "PCX" if c == "AMEX" and etf else FMP_EXCHANGES.get(c, c)


def _fmp_quote(row: dict, meta: dict | None, profile: dict | None, market_state: str) -> Quote:
    """FMP /quote row -> Quote. FMP has no quote type, market state or sector in /quote: universe names take
    their ETF flag and sector from universe.csv (as GICS), others from a profile or screener row (isEtf, sector);
    without either the type stays unknown. The market state comes from the NYSE calendar at the fetcher's clock.
    volume is the consolidated regular-session volume (equal to Yahoo's regularMarketVolume, median ratio 1.000
    over 520 names in session, measured)."""
    symbol = str(row.get("symbol", ""))
    if meta is not None:
        etf: bool | None = bool(meta.get("is_etf")) or symbol in REFERENCE_SYMBOLS
    elif profile is not None and isinstance(profile.get("isEtf"), bool):
        etf = profile["isEtf"]
    else:
        etf = None
    sector = (meta or {}).get("sector") or gics_sector((profile or {}).get("sector") or None)
    return Quote(
        symbol=symbol,
        price=_float(row.get("price")),
        prev_close=_float(row.get("previousClose")),
        change_pct=_float(row.get("changesPercentage")),
        day_volume=_int(row.get("volume")),
        market_time=_int(row.get("timestamp")),
        market_state=market_state,
        avg_volume_3m=_int(row.get("avgVolume")),
        market_cap=_float(row.get("marketCap")),
        exchange=_fmp_exchange(row.get("exchange") or (profile or {}).get("exchangeShortName"), bool(etf)),
        quote_type=None if etf is None else ("ETF" if etf else "EQUITY"),
        name=row.get("name") or (meta or {}).get("name") or (profile or {}).get("companyName"),
        sector=None if etf else sector)


def _nan_float(v: Any) -> float:
    f = _float(v)
    return math.nan if f is None else f


def _empty_bars(symbol: str) -> Bars:
    f = np.zeros(0, dtype=np.float64)
    return Bars(symbol, np.zeros(0, dtype=np.int64), f, f.copy(), f.copy(), f.copy(), np.zeros(0, dtype=np.int64))


# ---------------------------------------------------------------------- helpers
def _unique(symbols: list[str]) -> list[str]:
    return list(dict.fromkeys(symbols))


def _chunks(items: list[str], n: int) -> Iterator[list[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _pool_map[T](fn: Callable[[Any], T], items: list, workers: int) -> list[T]:
    with ThreadPoolExecutor(max(1, min(workers, len(items)))) as pool:
        return list(pool.map(fn, items))
