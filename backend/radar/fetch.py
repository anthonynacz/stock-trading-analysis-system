"""Keyless market data for the radar (SPEC 4.2): Yahoo first, Nasdaq behind a circuit breaker.

Transport rules come from measurements (radar/docs/data-sources.md):
- Yahoo v8 chart goes through curl_cffi with Chrome impersonation: plain requests gets 429 at once.
- The v7 quote endpoint and the predefined movers screens need a cookie and crumb, which yfinance's
  YfData manages. Nothing else from yfinance is used (yf.download takes 45-53 s for the universe).
- Nasdaq is the fallback. Its 1-minute chart stamps the ET wall clock as if it were UTC.

The breaker is kept per endpoint family (SPEC 12.1): "chart" (v8 chart: bars_5m, daily) and "crumb"
(v7 quote and screener: quotes, movers) fail independently, e.g. a 429 on the chart while quotes work.
"""
from __future__ import annotations

import math
import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as dtime
from functools import cached_property, partial, wraps
from typing import Any, Protocol

import numpy as np
from curl_cffi import requests as creq
from yfinance.data import YfData
from yfinance.exceptions import YFRateLimitError

from radar.calendar_nyse import ET, session_for
from radar.config import REFERENCE_SYMBOLS, US_EXCHANGES
from radar.types import Bars, DailyBars, FetchReport, Quote
from radar.universe import gics_sector, load_universe

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

Reply = tuple[int, Any]


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


class HttpTransport:
    """curl_cffi Chrome sessions (one per worker thread) plus yfinance for the crumb-guarded endpoints."""

    def __init__(self) -> None:
        self._local = threading.local()

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
        return result, report
    return call


class Fetcher:
    """One tick's data access. Pass `health_state()` back as `health=` on the next tick.

    `deadline` is an absolute time on `clock` (time.time by default), e.g. tick start + tick_timeout_s - 40:
    past it no request starts and no retry is made, so a hung or blocked source cannot outlive the tick.
    `on_health` receives `health_state()` after every public call.
    `transport`, `sleep`, `clock` and `rng` exist for tests.
    """

    def __init__(self, *, health: dict | None = None, workers: int = 16, timeout_s: float = 12.0,
                 deadline: float | None = None, on_health: Callable[[dict], None] | None = None,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, rng: random.Random | None = None) -> None:
        self._max_workers = max(1, workers)
        self._min_workers = min(MIN_WORKERS, self._max_workers)
        self._timeout = timeout_s
        self._tx = transport or HttpTransport()
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
        return self._serve("crumb", partial(self._yahoo_quotes, symbols), partial(self._nasdaq_quotes, symbols))

    @_reports_health
    def bars_5m(self, symbols: list[str], *, range_: str = "1d",
                include_prepost: bool = False) -> tuple[dict[str, Bars], FetchReport]:
        symbols = _unique(symbols)
        if not symbols:
            return {}, FetchReport(source="none", status="ok")
        params = {"range": range_, "interval": "5m", "includePrePost": "true" if include_prepost else "false"}
        yahoo = partial(self._yahoo_charts, symbols, params, _parse_bars)
        # Nasdaq's chart covers one session only, so baseline ranges have no fallback.
        nasdaq = partial(self._nasdaq_bars, symbols, include_prepost) if range_ == "1d" else None
        return self._serve("chart", yahoo, nasdaq)

    @_reports_health
    def daily(self, symbols: list[str], *, range_: str = "3mo") -> tuple[dict[str, DailyBars], FetchReport]:
        symbols = _unique(symbols)
        if not symbols:
            return {}, FetchReport(source="none", status="ok")
        params = {"range": range_, "interval": "1d"}
        return self._serve("chart", partial(self._yahoo_charts, symbols, params, _parse_daily), None)

    @_reports_health
    def movers(self) -> tuple[list[Quote], FetchReport]:
        return self._serve("crumb", self._yahoo_movers, self._nasdaq_movers)

    def health_state(self) -> dict:
        """Per-family breakers under `families`; the top level keeps the flat fields state.json shows,
        each the worst of the two families (last_ok_at: the older one)."""
        fams = {name: _family_json(h) for name, h in self._fam.items()}
        worst = max(FAMILIES, key=lambda name: _badness(self._fam[name]))
        last_ok = [h.last_ok_at for h in self._fam.values() if h.last_ok_at]
        return {"schema": 1, "name": "nasdaq" if any(h.breaker_open for h in self._fam.values()) else "yahoo",
                "status": max((h.status for h in self._fam.values()), key=STATUSES.index),
                "consecutive_failures": max(h.consecutive_failures for h in self._fam.values()),
                "last_ok_at": min(last_ok) if last_ok else None,
                "workers": self._workers, "breaker": fams[worst]["breaker"], "families": fams}

    # ------------------------------------------------------------------ breaker and degraded mode
    def _restore(self, d: dict) -> tuple[int, dict[str, _Health]]:
        """Health read back from engine.json; anything malformed falls back to a fresh, closed breaker.
        A flat record from before the per-family breakers applies to both families."""
        d = d if isinstance(d, dict) else {}
        workers = _int(d.get("workers")) or self._max_workers
        families = d.get("families")
        return (min(self._max_workers, max(self._min_workers, workers)),
                {name: _family_from(families.get(name) if isinstance(families, dict) else d) for name in FAMILIES})

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
                self._record_yahoo(family, report)
                if report.status == "ok":
                    report.notes.append("yahoo probe ok")
                else:
                    result, report = self._fall_back(nasdaq, report, "yahoo probe")
        else:
            result, report = self._run(yahoo, TRIES, fail_fast=True)
            self._record_yahoo(family, report)
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

    def _record_yahoo(self, family: str, report: FetchReport) -> None:
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
    big = [q for q in map(_nasdaq_screener_quote, rows)
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
