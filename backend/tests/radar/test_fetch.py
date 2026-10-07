"""radar.fetch: parsing of saved live responses, retry/backoff, degraded mode, circuit breaker, health.

Fixtures in tests/radar/fixtures/ are trimmed live responses captured on 2026-09-27 (Friday 2026-09-25 data).
"""
from __future__ import annotations

import copy
import json
import random
import re
import threading
import time
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from radar import fetch
from radar.calendar_nyse import session_for
from radar.config import PARAMS, RUNTIME
from radar.fetch import Fetcher, from_nasdaq, to_nasdaq
from radar.universe import dynamic_candidates, scan_symbols

FIXTURES = Path(__file__).parent / "fixtures"
FRIDAY = session_for(date(2026, 9, 25))
T0 = 1790343000.0           # 2026-09-25 13:30:00Z, the fake clock of make()


def fx(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    """Answers from handlers `(key, params) -> (status, body)`; a handler may raise to simulate a network error."""

    def __init__(self, chart=None, quote=None, screen=None, nasdaq=None):
        self.handlers = {"chart": chart, "quote": quote, "screen": screen, "nasdaq": nasdaq}
        self.calls: list[tuple[str, object]] = []
        self._lock = threading.Lock()

    def _answer(self, kind, key, params):
        with self._lock:
            self.calls.append((kind, key))
        handler = self.handlers[kind]
        if handler is None:
            raise AssertionError(f"unexpected {kind} call for {key}")
        return handler(key, params)

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)

    def yahoo_chart(self, symbol, params, timeout):
        return self._answer("chart", symbol, params)

    def yahoo_quote(self, params, timeout):
        return self._answer("quote", params["symbols"], params)

    def yahoo_screen(self, name, count, timeout):
        return self._answer("screen", name, {"count": count})

    def nasdaq(self, url, params, timeout):
        return self._answer("nasdaq", url, params)


def scripted(*replies):
    """Handler returning the scripted replies in order, then repeating the last one; exceptions are raised."""
    queue, lock = list(replies), threading.Lock()

    def handler(key, params):
        with lock:
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return reply
    return handler


def chart_ok(key, params):
    return 200, fx("yahoo_chart_aapl_1d.json")


def chart_fail(key, params):
    return 500, None


def quote_ok(key, params):
    rows = [{"symbol": s, "regularMarketPrice": 10.0, "quoteType": "EQUITY", "exchange": "NMS"}
            for s in params["symbols"].split(",")]
    return 200, {"quoteResponse": {"result": rows, "error": None}}


def watchlist_ok(url, params):
    rows = [{"symbol": to_nasdaq(sym_class.split("|")[0].upper()), "lastSalePrice": "$10.00",
             "percentageChange": "+1.00%", "volume": "1,000", "assetClass": sym_class.split("|")[1].upper()}
            for _, sym_class in params]
    return 200, {"data": rows}


def nasdaq_ok(url, params):
    if url == fetch.NASDAQ_WATCHLIST:
        return watchlist_ok(url, params)
    if url == fetch.NASDAQ_SCREENER:
        return 200, fx("nasdaq_screener.json")
    return 200, fx("nasdaq_chart_rs_aapl.json")


def make(transport, health=None, workers=16, **kw):
    sleeps: list[float] = []
    f = Fetcher(health=health, workers=workers, transport=transport,
                **{"sleep": sleeps.append, "clock": lambda: T0, "rng": random.Random(7), **kw})
    return f, sleeps


FRESH_FAMILY = {"status": "ok", "consecutive_failures": 0, "last_ok_at": None,
                "breaker": {"open": False, "opened_at": None, "calls": 0, "good_probes": 0}}


def open_health(*families, **breaker):
    """Health as health_state() writes it, with the breaker of each named family (default: both) open."""
    opened = {"status": "degraded", "consecutive_failures": 3, "last_ok_at": None,
              "breaker": {"open": True, "opened_at": "2026-09-25T15:00:00Z", "calls": 0, "good_probes": 0, **breaker}}
    return {"schema": 1, "workers": 4,
            "families": {name: copy.deepcopy(opened if name in (families or fetch.FAMILIES) else FRESH_FAMILY)
                         for name in fetch.FAMILIES}}


def family(health, name):
    """(consecutive_failures, breaker open) of one family."""
    f = health["families"][name]
    return f["consecutive_failures"], f["breaker"]["open"]


# ---------------------------------------------------------------------- Yahoo chart parsing
def test_bars_regular_session_from_saved_chart():
    b = fetch._parse_bars("AAPL", fx("yahoo_chart_aapl_1d.json"))
    assert len(b) == 79                      # 78 session bars + Yahoo's 16:00 volume-0 row (slot_of says None)
    assert b.ts[0] == FRIDAY.open_epoch and FRIDAY.slot_of(int(b.ts[-1])) is None
    assert b.ts.dtype == np.int64 and b.v.dtype == np.int64 and b.c.dtype == np.float64
    assert np.all(b.ts % 300 == 0) and np.all(np.diff(b.ts) > 0)
    assert b.v[0] == 1133666 and b.c[77] == pytest.approx(341.04, abs=1e-4)
    assert b.last_trade_ts is None and b.last_trade_px is None


def test_bars_off_grid_last_trade_row_is_split_out():
    b = fetch._parse_bars("AAPL", fx("yahoo_chart_aapl_1d_prepost.json"))
    assert np.all(b.ts % 300 == 0)
    assert b.last_trade_ts == 1790380798 and b.last_trade_px == pytest.approx(341.4603)
    assert b.ts[-1] == 1790380500 and b.ts[0] == FRIDAY.pre_open.timestamp()


def test_bars_one_month_range_spans_sessions():
    b = fetch._parse_bars("AAPL", fx("yahoo_chart_aapl_1mo.json"))
    days = {int(t) // 86400 for t in b.ts}
    assert len(b) == 157 and len(days) == 2 and np.all(np.diff(b.ts) > 0)


def test_bars_nulls_duplicates_and_order_are_cleaned():
    body = fx("yahoo_chart_aapl_1d.json")
    res = body["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    q["close"][5] = None                        # dropped
    q["open"][6] = None                         # filled from close
    q["volume"][7] = None                       # 0 shares
    res["timestamp"][1], res["timestamp"][2] = res["timestamp"][2], res["timestamp"][1]
    res["timestamp"].append(res["timestamp"][10])   # duplicate slot: the later row wins
    for k in q:
        q[k].append(q[k][10] if k != "close" else 999.0)
    b = fetch._parse_bars("AAPL", body)
    assert len(b) == 78 and np.all(np.diff(b.ts) > 0)
    assert FRIDAY.slot_start(5) not in b.ts
    i6 = int(np.flatnonzero(b.ts == FRIDAY.slot_start(6))[0])
    assert b.o[i6] == b.c[i6]
    assert b.v[int(np.flatnonzero(b.ts == FRIDAY.slot_start(7))[0])] == 0
    assert b.c[int(np.flatnonzero(b.ts == FRIDAY.slot_start(10))[0])] == 999.0


def test_chart_error_or_empty_bodies():
    assert fetch._parse_bars("ZZZZ", fx("yahoo_chart_not_found.json")) is None
    body = fx("yahoo_chart_aapl_1d.json")
    del body["chart"]["result"][0]["timestamp"]
    empty = fetch._parse_bars("AAPL", body)
    assert empty is not None and len(empty) == 0


def chart_until(slot, *extra_ts):
    """The saved 1d chart cut after `slot`, plus rows at `extra_ts` repeating the last row's values."""
    body = fx("yahoo_chart_aapl_1d.json")
    res = body["chart"]["result"][0]
    n = res["timestamp"].index(FRIDAY.slot_start(slot)) + 1
    res["timestamp"] = res["timestamp"][:n] + list(extra_ts)
    quote = res["indicators"]["quote"][0]
    for k, v in quote.items():
        quote[k] = v[:n] + [v[n - 1]] * len(extra_ts)
    return body


def test_newest_bar_is_provisional_until_a_later_trade_or_row_closes_it():
    """DATA-4: Yahoo folds the first trade after the newest row's end into that row and only then closes it, so
    the row may still change while the last trade is inside it. (Updated: the first version of this test flagged
    the opposite state, last trade past the end, as SPEC 12.1 words it; the live sample below shows that state
    is the settled one.)"""
    t = FRIDAY.slot_start(20)
    inside = fetch._parse_bars("AAPL", chart_until(20, t + 150))            # last trade inside bar 20, no row 21
    assert inside.provisional_last and inside.ts[-1] == t and inside.last_trade_ts == t + 150
    assert not fetch._parse_bars("AAPL", chart_until(20, t + 303)).provisional_last   # folded in: bar 20 closed
    nxt = fetch._parse_bars("AAPL", chart_until(20, t + 300, t + 303))      # row 21 exists, so bar 20 is closed;
    assert nxt.ts[-1] == t + 300 and nxt.provisional_last                   # the flag is about row 21, still open
    assert not fetch._parse_bars("AAPL", chart_until(20)).provisional_last            # no last-trade row
    assert not fetch._parse_bars("AAPL", fx("yahoo_chart_aapl_1d.json")).provisional_last
    # Saved after the session: no trade ever closed the 19:55 post-market row. The engine reads the flag only
    # for the regular slot it is finalizing.
    assert fetch._parse_bars("AAPL", fx("yahoo_chart_aapl_1d_prepost.json")).provisional_last
    assert not fetch._parse_nasdaq_bars("AAPL", fx("nasdaq_chart_rs_aapl.json"), False).provisional_last


# The review's live finality sample (DATA-4): the 18:50-18:55 UTC bar of 24 names, refetched at +20, +50, +90 and
# +150 s after its end. A cell is the last trade's offset from the bar's end in seconds, then "n" when the next
# row existed and "*" when the bar still changed after that fetch. (ERIE returned no row for the bar.)
LIVE_FINALITY = """
    SPY    +19   +49   +89n  +144n
    AAPL   +20   +50   +89n  +148n
    AIZ    -158* -158* +80   +80
    ALLE   -83*  -83*  -83*  +132
    FOX    -134  -134  -134  -134
    NWS    +17   +40   +58   +58
    BF-B   +6    +41   +79n  +145n
    HII    -46*  +44   +75n  +126n
    MKTX   -43*  -43*  -43*  -43*
    PNW    -12*  -12*  +56   +109n
    TPL    -67*  +40   +88n  +146n
    AOS    +14   +46   +79n  +144n
    CPB    +18   +47   +85n  +146n
    FRT    +21   +48   +85n  +111n
    MHK    +20   +47   +47   +47
    IVZ    +6    +46   +80n  +128n
    BEN    +5    +50   +79n  +143n
    GL     -10*  -10*  +88   +134n
    HSIC   -21*  +28   +84n  +109n
    DVA    -31*  -31*  -31*  +95
    EMN    +16   +16   +90n  +139n
    CHRW   +12   +42   +80n  +128n
    NI     -5*   +30   +83n  +147n
    PODD   +19   +48   +54   +128n
"""


def test_provisional_flag_matches_the_live_finality_sample():
    """DATA-4 regression (fix-verify round 2): the 96 live states, rebuilt as Yahoo served them, are flagged
    exactly when the bar still changed afterwards. The only exception is FOX, which did not trade again during
    the sample. The SPEC 12.1 wording (last trade past the end) flagged none of the 20 changes and 41 settled
    bars, SPY and AAPL included."""
    t = FRIDAY.slot_start(20)
    wrong, changes = [], 0
    for line in LIVE_FINALITY.strip().splitlines():
        sym, *cells = line.split()
        for offset, cell in zip((20, 50, 90, 150), cells, strict=True):
            trade, nxt, changed = re.fullmatch(r"([+-]\d+)(n?)(\*?)", cell).groups()
            b = fetch._parse_bars(sym, chart_until(20, *([t + 300] if nxt else []), t + 300 + int(trade)))
            provisional_k = b.provisional_last and b.ts[-1] == t     # how the engine reads it for slot k
            changes += bool(changed)
            if provisional_k != bool(changed):
                wrong.append((sym, offset))
    assert changes == 20 and wrong == [("FOX", 20), ("FOX", 50), ("FOX", 90), ("FOX", 150)]


def test_malformed_body_fails_only_that_symbol():
    bad = fx("yahoo_chart_aapl_1d.json")
    bad["chart"]["result"][0]["indicators"]["quote"][0]["close"][3] = "n/a"
    t = FakeTransport(chart=lambda k, p: (200, bad) if k == "BAD" else chart_ok(k, p))
    f, _ = make(t)
    bars, report = f.bars_5m(["AAPL", "BAD", "MSFT", "NVDA", "AMD", "JPM"])
    assert set(bars) == {"AAPL", "MSFT", "NVDA", "AMD", "JPM"} and report.failed == ["BAD"]


def test_daily_bars_dated_in_new_york():
    d = fetch._parse_daily("AAPL", fx("yahoo_chart_aapl_daily_3mo.json"))
    assert d.day.dtype == np.dtype("datetime64[D]") and len(d.day) == 64
    assert d.day[-1] == np.datetime64("2026-09-25") and d.c[-1] == pytest.approx(341.07)
    assert np.all(np.diff(d.day.astype(np.int64)) > 0) and d.v.dtype == np.int64


def test_daily_live_row_after_utc_midnight_keeps_the_session_date():
    body = fx("yahoo_chart_aapl_daily_3mo.json")
    res = body["chart"]["result"][0]
    res["timestamp"].append(1790380799)          # 2026-09-25 19:59:59 ET = 2026-09-26 00:00 UTC (a live row)
    for k, v in res["indicators"]["quote"][0].items():
        v.append(342.0 if k == "close" else v[-1])
    d = fetch._parse_daily("AAPL", body)
    assert len(d.day) == 64 and d.day[-1] == np.datetime64("2026-09-25") and d.c[-1] == 342.0


# ---------------------------------------------------------------------- Yahoo quotes and movers
def test_quotes_parse_the_saved_v7_response():
    t = FakeTransport(quote=lambda k, p: (200, fx("yahoo_quote.json")))
    f, _ = make(t)
    quotes, report = f.quotes(["AAPL", "MSFT", "BRK-B", "SPY", "NVDA", "ZZZZNOTREAL"])
    aapl = quotes["AAPL"]
    assert (aapl.price, aapl.prev_close, aapl.day_volume) == (341.07, 335.92, 30002507)
    assert aapl.change_pct == pytest.approx(1.533, abs=1e-3) and aapl.market_time == 1790366401
    assert (aapl.exchange, aapl.quote_type, aapl.name, aapl.market_state) == ("NMS", "EQUITY", "Apple Inc.", "CLOSED")
    assert aapl.sector == "Information Technology" and quotes["BRK-B"].sector == "Financials"
    assert quotes["SPY"].quote_type == "ETF" and quotes["SPY"].sector is None
    assert quotes["AAPL"].market_cap > 4e12 and quotes["AAPL"].avg_volume_3m > 0
    assert report.failed == ["ZZZZNOTREAL"] and (report.ok, report.status, report.source) == (5, "ok", "yahoo")
    assert "sector" in fetch.QUOTE_FIELDS.split(",")


def test_quotes_batch_at_most_250_symbols():
    t = FakeTransport(quote=quote_ok)
    f, _ = make(t)
    symbols = [f"S{i:03d}" for i in range(600)]
    quotes, report = f.quotes(symbols + symbols[:10])       # duplicates are fetched once
    assert [len(k.split(",")) for _, k in t.calls] == [250, 250, 100]
    assert len(quotes) == 600 and report.status == "ok" and report.requested == 600


def test_movers_keep_us_exchanges_and_dedupe():
    screens = fx("yahoo_screens.json")
    t = FakeTransport(screen=lambda name, p: (200, screens[name]))
    f, _ = make(t)
    movers, report = f.movers()
    symbols = [q.symbol for q in movers]
    assert "SCGLY" not in symbols                           # OTC (OID) row dropped
    assert symbols.count("AAL") == 1 and symbols.count("CRWD") == 1
    assert len(symbols) == 11 and report.status == "ok" and report.requested == 3
    assert all(q.exchange in fetch.US_EXCHANGES for q in movers)
    assert [n for _, n in t.calls] == list(fetch.MOVER_SCREENS)


def test_real_movers_feed_dynamic_candidates():
    screens = fx("yahoo_screens.json")
    f, _ = make(FakeTransport(screen=lambda name, p: (200, screens[name])))
    movers, _ = f.movers()
    picked = [q.symbol for q in dynamic_candidates(movers, set(scan_symbols()), PARAMS)]
    # BKNG, CRWD, CRWV, INTC, NVDA and SPCX are universe names; SCGLY trades OTC; CRWD also moved < 3%.
    assert picked == ["PPLI", "ALM", "ZS", "TWLO", "AAL"]


# ---------------------------------------------------------------------- Nasdaq parsing
def test_symbol_mapping_both_ways():
    assert to_nasdaq("BRK-B") == "BRK.B" and to_nasdaq("BRK-B", "/") == "BRK/B" and to_nasdaq("AAPL") == "AAPL"
    assert from_nasdaq("BRK.B") == "BRK-B" and from_nasdaq("BRK/B") == "BRK-B" and from_nasdaq(" bf/b ") == "BF-B"
    assert from_nasdaq(to_nasdaq("BF-B")) == "BF-B"


def test_nasdaq_numbers():
    assert fetch._nq_float("$1,234.50") == 1234.5 and fetch._nq_float("+1.53%") == 1.53
    assert fetch._nq_float("-0.412%") == -0.412 and fetch._nq_float(767.18) == 767.18
    assert fetch._nq_float("N/A") is None and fetch._nq_float("") is None and fetch._nq_float("nan") is None
    assert fetch._nq_int("30,002,768") == 30002768


def test_nasdaq_chart_resamples_to_yahoo_like_5m_bars():
    body = fx("nasdaq_chart_rs_aapl.json")
    b = fetch._parse_nasdaq_bars("AAPL", body, include_prepost=False)
    assert b.ts[0] == FRIDAY.open_epoch and np.all(b.ts % 300 == 0)
    assert all(FRIDAY.slot_of(int(t)) is not None for t in b.ts)
    # bucket 0 by hand: points 09:30..09:34 ET (pseudo-UTC x = wall clock)
    pts = [p for p in body["data"]["chart"] if 1790328600000 <= p["x"] < 1790328900000]
    assert (b.o[0], b.c[0]) == (pts[0]["y"], pts[-1]["y"])
    assert (b.h[0], b.l[0]) == (max(p["y"] for p in pts), min(p["y"] for p in pts))
    assert b.v[0] == round(sum(p["w"] for p in pts))
    # cross-check against Yahoo's own 5m bars for the same Friday: closes match, volume within 1%
    y = fetch._parse_bars("AAPL", fx("yahoo_chart_aapl_1d.json"))
    for j in range(1, 7):
        yi, ni = np.flatnonzero(y.ts == FRIDAY.slot_start(j))[0], np.flatnonzero(b.ts == FRIDAY.slot_start(j))[0]
        assert b.c[ni] == pytest.approx(y.c[yi], abs=0.03)
        assert b.v[ni] == pytest.approx(y.v[yi], rel=0.01)


def test_nasdaq_chart_with_prepost_and_bad_bodies():
    b = fetch._parse_nasdaq_bars("AAPL", fx("nasdaq_chart_rs_aapl.json"), include_prepost=True)
    assert b.ts[0] == int(FRIDAY.pre_open.timestamp()) and b.ts[-1] > FRIDAY.close_epoch
    assert fetch._parse_nasdaq_bars("SPY", fx("nasdaq_chart_bad_asset.json"), False) is None
    empty = fetch._parse_nasdaq_bars("AAPL", {"data": {"chart": []}}, False)
    assert empty is not None and len(empty) == 0


def test_nasdaq_screener_movers_mirror_yahoo_thresholds():
    movers = fetch._nasdaq_movers(fetch._screener_rows(fx("nasdaq_screener.json")))
    symbols = [q.symbol for q in movers]
    assert symbols[:4] == ["ADPT", "A", "GME", "ALMR"]         # gainers > 3%, cap >= $2B, price >= $5
    assert {"ACAD", "ACN"} <= set(symbols) and {"AAL", "SPCX", "AAPL"} <= set(symbols)
    assert "ABR^D" not in symbols and "BRK-B" not in symbols and len(symbols) == len(set(symbols))
    gme = movers[symbols.index("GME")]
    assert gme.prev_close == pytest.approx(24.2) and gme.market_cap == pytest.approx(12622614770.0)
    assert gme.exchange is None and gme.sector == "Consumer Discretionary"
    assert dynamic_candidates(movers, set(), PARAMS) == []    # no exchange: never a dynamic add


# ---------------------------------------------------------------------- retry and backoff
def test_retry_429_then_ok_backs_off_with_jitter():
    t = FakeTransport(chart=scripted((429, None), (429, None), (200, fx("yahoo_chart_aapl_1d.json"))))
    f, sleeps = make(t)
    bars, report = f.bars_5m(["AAPL"])
    assert "AAPL" in bars and t.count("chart") == 3
    assert 1.0 <= sleeps[0] <= 1.5 and 2.0 <= sleeps[1] <= 2.5
    assert report.http_429 == 2 and report.status == "degraded"    # any 429 degrades the call


def test_retry_5xx_and_network_errors():
    t = FakeTransport(chart=scripted((503, None), ConnectionError("reset"), (200, fx("yahoo_chart_aapl_1d.json"))))
    f, sleeps = make(t)
    bars, report = f.bars_5m(["AAPL"])
    assert "AAPL" in bars and t.count("chart") == 3 and len(sleeps) == 2 and report.status == "ok"


def test_other_4xx_is_not_retried():
    t = FakeTransport(chart=lambda k, p: (404, fx("yahoo_chart_not_found.json")))
    f, sleeps = make(t)
    bars, report = f.bars_5m(["ZZZZ"])
    assert bars == {} and t.count("chart") == 1 and sleeps == []
    assert report.failed == ["ZZZZ"] and report.status == "down"


def test_gives_up_after_three_tries():
    t = FakeTransport(chart=chart_fail)
    f, sleeps = make(t)
    _, report = f.bars_5m(["AAPL"])
    assert t.count("chart") == 3 and len(sleeps) == 2 and report.status == "down"


# ---------------------------------------------------------------------- degraded mode
def test_degraded_calls_halve_workers_and_ok_calls_restore_them():
    symbols = [f"S{i}" for i in range(10)]
    failing = {"S0", "S1", "S2"}                                # 30% > 20%: degraded
    t = FakeTransport(chart=lambda k, p: (404, None) if k in failing else chart_ok(k, p))
    f, _ = make(t, workers=16)
    workers = []
    for _ in range(2):
        _, report = f.bars_5m(symbols)
        assert report.status == "degraded" and report.ok == 7
        workers.append(f.health_state()["workers"])
    failing.clear()
    for _ in range(2):
        f.bars_5m(symbols)
        workers.append(f.health_state()["workers"])
    assert workers == [8, 4, 8, 16]


def test_small_failure_share_stays_ok():
    t = FakeTransport(chart=lambda k, p: (404, None) if k == "S0" else chart_ok(k, p))
    f, _ = make(t)
    _, report = f.bars_5m([f"S{i}" for i in range(10)])
    assert report.status == "ok" and report.failed == ["S0"]


# ---------------------------------------------------------------------- circuit breaker
def test_breaker_opens_after_three_degraded_calls_and_serves_nasdaq():
    t = FakeTransport(quote=lambda k, p: (503, None), nasdaq=nasdaq_ok)
    f, _ = make(t)
    sources = []
    for _ in range(3):
        quotes, report = f.quotes(["AAPL", "SPY", "BRK-B"])
        sources.append(report.source)
    assert sources == ["yahoo", "yahoo", "nasdaq"]              # the tripping call is already served by Nasdaq
    assert set(quotes) == {"AAPL", "SPY", "BRK-B"} and report.status == "degraded"
    assert quotes["SPY"].quote_type == "ETF" and quotes["BRK-B"].price == 10.0
    h = f.health_state()
    assert h["breaker"]["open"] and h["name"] == "nasdaq" and h["consecutive_failures"] == 3
    assert family(h, "crumb") == (3, True) and family(h, "chart") == (0, False)
    assert [url for kind, url in t.calls if kind == "nasdaq"] == [fetch.NASDAQ_WATCHLIST]


def test_nasdaq_quotes_use_watchlist_batches_with_asset_classes():
    seen = []

    def nasdaq(url, params):
        seen.append(params)
        return watchlist_ok(url, params)
    f, _ = make(FakeTransport(nasdaq=nasdaq), health=open_health("crumb"))
    symbols = ["SPY", "BRK-B"] + [f"S{i}" for i in range(43)]
    quotes, report = f.quotes(symbols)
    assert sorted(len(p) for p in seen) == [5, 20, 20] and len(quotes) == 45
    flat = {v for p in seen for _, v in p}
    assert "spy|etf" in flat and "brk/b|stocks" in flat          # the watchlist answers bf.b with an N/A row
    assert report.source == "nasdaq" and "nasdaq fallback" in report.notes


def test_nasdaq_watchlist_quotes_from_saved_response():
    f, _ = make(FakeTransport(nasdaq=lambda url, p: (200, fx("nasdaq_watchlist.json"))), health=open_health("crumb"))
    quotes, report = f.quotes(["SPY", "QQQ", "IWM", "BRK-B", "BF-B", "ETN", "AAPL", "ZZZZ"])
    assert report.failed == ["ZZZZ"] and report.status == "degraded"
    assert quotes["BF-B"].price == 26.16 and quotes["BF-B"].change_pct == 0.62     # not the N/A "BF.B" row
    assert quotes["BRK-B"].prev_close == 505.18 and quotes["SPY"].quote_type == "ETF"
    assert quotes["ETN"].change_pct == pytest.approx(-0.0045, abs=1e-4)             # blank percent: computed
    aapl = quotes["AAPL"]
    assert (aapl.price, aapl.prev_close, aapl.change_pct, aapl.day_volume) == (341.07, 335.92, 1.53, 30002768)


def test_breaker_probes_every_third_call_and_closes_after_two_good_probes():
    t = FakeTransport(quote=quote_ok, nasdaq=nasdaq_ok)
    f, _ = make(t, health=open_health("crumb"))
    sources = [f.quotes(["AAPL"])[1].source for _ in range(7)]
    assert sources == ["nasdaq", "nasdaq", "yahoo", "nasdaq", "nasdaq", "yahoo", "yahoo"]
    h = f.health_state()
    assert not h["breaker"]["open"] and h["name"] == "yahoo" and h["consecutive_failures"] == 0


def test_failed_probe_resets_good_probes_and_falls_back():
    t = FakeTransport(chart=chart_fail, nasdaq=nasdaq_ok)
    f, sleeps = make(t, health=open_health("chart", good_probes=1, calls=2))
    bars, report = f.bars_5m(["AAPL", "MSFT"])
    assert t.count("chart") == 2 and sleeps == []              # a probe is one try per symbol
    assert report.source == "nasdaq" and set(bars) == {"AAPL", "MSFT"}
    assert report.notes == ["yahoo probe down: 0/2 ok", "nasdaq fallback"]
    breaker = f.health_state()["families"]["chart"]["breaker"]
    assert breaker["open"] and breaker["good_probes"] == 0 and breaker["calls"] == 3


def test_baseline_ranges_and_daily_stay_on_yahoo_and_count_as_probes():
    t = FakeTransport(chart=lambda k, p: (200, fx("yahoo_chart_aapl_daily_3mo.json" if p["interval"] == "1d"
                                                  else "yahoo_chart_aapl_1mo.json")))
    f, _ = make(t, health=open_health("chart"))
    bars, report = f.bars_5m(["AAPL"], range_="1mo")
    assert report.source == "yahoo" and len(bars["AAPL"]) == 157
    assert f.health_state()["families"]["chart"]["breaker"] == {"open": True, "opened_at": "2026-09-25T15:00:00Z",
                                                                "calls": 0, "good_probes": 1}
    daily, report = f.daily(["AAPL"])
    assert report.source == "yahoo" and len(daily["AAPL"].day) == 64
    assert not f.health_state()["breaker"]["open"] and t.count("nasdaq") == 0


def test_nasdaq_bars_and_movers_while_open():
    t = FakeTransport(nasdaq=nasdaq_ok)
    f, _ = make(t, health=open_health())
    bars, report = f.bars_5m(["AAPL", "SPY"])
    assert report.source == "nasdaq" and bars["AAPL"].ts[0] == FRIDAY.open_epoch
    urls = {url for kind, url in t.calls if kind == "nasdaq"}
    assert urls == {fetch.NASDAQ_CHART.format("AAPL"), fetch.NASDAQ_CHART.format("SPY")}
    movers, report = f.movers()
    assert report.source == "nasdaq" and movers and all(q.exchange is None for q in movers)


def test_chart_block_opens_the_chart_breaker_while_quotes_stay_on_yahoo():
    """DATA-1 case A: the v8 chart answers 429 while the v7 quote works. The good quote calls must not
    reset the chart count: the 3rd bars call opens the chart breaker and Nasdaq serves it."""
    screens = fx("yahoo_screens.json")
    t = FakeTransport(chart=lambda k, p: (429, None), quote=quote_ok, screen=lambda name, p: (200, screens[name]),
                      nasdaq=nasdaq_ok)
    health, sources = None, []
    for _ in range(3):                                          # three ticks, health carried through engine.json
        f, _ = make(t, health=health)
        _, qrep = f.quotes(["AAPL", "SPY"])
        _, mrep = f.movers()
        bars, brep = f.bars_5m(["AAPL", "SPY"])
        health = json.loads(json.dumps(f.health_state()))
        assert (qrep.source, qrep.status, mrep.source, mrep.status) == ("yahoo", "ok", "yahoo", "ok")
        sources.append(brep.source)
    assert sources == ["yahoo", "yahoo", "nasdaq"] and set(bars) == {"AAPL", "SPY"}
    assert family(health, "chart") == (3, True) and family(health, "crumb") == (0, False)
    assert health["name"] == "nasdaq" and health["families"]["crumb"]["status"] == "ok"


def test_crumb_block_moves_quotes_and_movers_to_nasdaq_while_bars_stay_on_yahoo():
    """DATA-1 case B: the crumb breaks (401) while charts work. Quotes and movers switch to Nasdaq on the
    3rd crumb call; bars stay on Yahoo with the full worker pool."""
    t = FakeTransport(chart=chart_ok, quote=lambda k, p: (401, None), screen=lambda name, p: (401, None),
                      nasdaq=nasdaq_ok)
    health, ticks = None, []
    for _ in range(2):
        f, _ = make(t, health=health)
        reports = [f.quotes(["AAPL", "SPY"])[1], f.movers()[1], f.bars_5m(["AAPL", "SPY"])[1]]
        health = json.loads(json.dumps(f.health_state()))
        ticks.append([(r.source, r.status) for r in reports])
    assert ticks == [[("yahoo", "down"), ("yahoo", "down"), ("yahoo", "ok")],
                     [("nasdaq", "degraded"), ("nasdaq", "degraded"), ("yahoo", "ok")]]
    assert family(health, "crumb") == (3, True) and family(health, "chart") == (0, False)
    assert health["workers"] == 16                              # crumb failures do not shrink the chart pool


# ---------------------------------------------------------------------- health state
def test_health_state_round_trip_and_defaults():
    t = FakeTransport(quote=lambda k, p: (503, None), nasdaq=nasdaq_ok)
    f, _ = make(t)
    for _ in range(4):
        f.quotes(["AAPL"])
    state = f.health_state()
    assert json.loads(json.dumps(state)) == state
    assert state["breaker"]["open"] and state["breaker"]["opened_at"] == "2026-09-25T13:30:00Z"
    restored, _ = make(FakeTransport(), health=copy.deepcopy(state))
    assert restored.health_state() == state
    assert family(state, "crumb") == (3, True) and family(state, "chart") == (0, False)
    default = {"schema": 1, "name": "yahoo", "status": "ok", "consecutive_failures": 0, "last_ok_at": None,
               "workers": 16, "breaker": FRESH_FAMILY["breaker"],
               "families": {"chart": FRESH_FAMILY, "crumb": FRESH_FAMILY}}
    for junk in ({"workers": "x", "breaker": None, "status": "weird"},
                 {"breaker": ["open"], "last_ok_at": 5, "consecutive_failures": "3"},
                 {"breaker": {"open": "yes", "opened_at": 1, "calls": None}},
                 {"families": {"chart": "x", "crumb": {"breaker": ["open"], "status": None}}},
                 {"families": ["chart"]},
                 ["not", "a", "dict"]):
        fresh, _ = make(FakeTransport(), health=junk)
        assert fresh.health_state() == default, junk
    capped, _ = make(FakeTransport(), health={"workers": 64}, workers=8)
    assert capped.health_state()["workers"] == 8


def test_flat_health_from_before_the_family_split_applies_to_both_families():
    flat = {"schema": 1, "name": "nasdaq", "status": "degraded", "consecutive_failures": 3,
            "last_ok_at": "2026-09-25T14:00:00Z", "workers": 4,
            "breaker": {"open": True, "opened_at": "2026-09-25T15:00:00Z", "calls": 2, "good_probes": 1}}
    f, _ = make(FakeTransport(), health=copy.deepcopy(flat))
    fam = {k: flat[k] for k in ("status", "consecutive_failures", "last_ok_at", "breaker")}
    assert f.health_state() == {**flat, "families": {"chart": fam, "crumb": fam}}
    # a closed flat record with two strikes: the next failed quote call opens the crumb breaker only
    t = FakeTransport(quote=lambda k, p: (503, None), nasdaq=nasdaq_ok)
    f, _ = make(t, health={"name": "yahoo", "status": "down", "consecutive_failures": 2, "last_ok_at": None,
                           "breaker": {"open": False}})
    _, report = f.quotes(["AAPL"])
    h = f.health_state()
    assert report.source == "nasdaq" and family(h, "crumb") == (3, True) and family(h, "chart") == (2, False)


def test_top_level_health_is_the_worst_family():
    h = open_health("chart")
    h["families"]["crumb"].update(status="down", consecutive_failures=2, last_ok_at="2026-09-25T13:00:00Z")
    h["families"]["chart"]["last_ok_at"] = "2026-09-25T12:00:00Z"
    f, _ = make(FakeTransport(), health=h)
    top = f.health_state()
    assert (top["name"], top["status"], top["consecutive_failures"]) == ("nasdaq", "down", 3)
    assert top["last_ok_at"] == "2026-09-25T12:00:00Z" and top["breaker"] == h["families"]["chart"]["breaker"]


def test_ok_call_records_last_ok_at():
    f, _ = make(FakeTransport(quote=quote_ok))
    f.quotes(["AAPL"])
    assert f.health_state()["last_ok_at"] == "2026-09-25T13:30:00Z"


def test_empty_requests_make_no_calls():
    t = FakeTransport()
    f, _ = make(t)
    for result, report in (f.quotes([]), f.bars_5m([]), f.daily([])):
        assert not result and report.status == "ok" and report.source == "none"
    assert t.calls == []


def test_on_health_gets_the_health_after_every_public_call():
    seen = []
    screens = fx("yahoo_screens.json")
    t = FakeTransport(chart=chart_ok, quote=lambda k, p: (503, None), screen=lambda name, p: (200, screens[name]))
    f, _ = make(t, on_health=seen.append)
    f.quotes(["AAPL"])
    f.movers()
    f.bars_5m(["AAPL"])
    f.daily([])
    f.quotes([])
    assert len(seen) == 5 and seen[-1] == f.health_state()
    assert family(seen[0], "crumb") == (1, False) and family(seen[1], "crumb") == (0, False)


def test_failing_on_health_costs_a_note_not_the_data():
    def boom(health):
        raise OSError("disk full")
    f, _ = make(FakeTransport(quote=quote_ok), on_health=boom)
    quotes, report = f.quotes(["AAPL"])
    assert "AAPL" in quotes and report.status == "ok" and report.notes == ["on_health failed: OSError"]


# ---------------------------------------------------------------------- deadline and fail-fast (SPEC 12.1)
def test_past_the_deadline_nothing_is_sent_and_the_breaker_is_left_alone():
    t = FakeTransport()                                         # every request would be recorded in t.calls
    f, _ = make(t, deadline=T0 - 1)
    for result, report in (f.quotes(["AAPL"]), f.movers(), f.bars_5m(["AAPL", "MSFT"]), f.daily(["AAPL"])):
        assert not result and (report.source, report.status) == ("yahoo", "down")
        assert report.notes[-1] == f"deadline reached, {report.requested} not requested"
    assert t.calls == [] and f.health_state()["families"] == {"chart": FRESH_FAMILY, "crumb": FRESH_FAMILY}
    f, _ = make(t, health=open_health("chart"), deadline=T0)   # open: the fallback is not called either
    bars, report = f.bars_5m(["AAPL"])
    assert bars == {} and (report.source, report.status) == ("nasdaq", "down") and t.calls == []
    assert f.health_state()["families"]["chart"] == open_health("chart")["families"]["chart"]


def test_no_retry_once_the_backoff_would_cross_the_deadline():
    now = [T0]

    def slow_503(key, params):
        now[0] += 5.0
        return 503, None
    t = FakeTransport(chart=slow_503)
    f, sleeps = make(t, clock=lambda: now[0], deadline=T0 + 5.5)
    _, report = f.bars_5m(["AAPL"])
    assert t.count("chart") == 1 and sleeps == [] and report.status == "down"
    assert report.notes == ["deadline reached, 0 not requested"]
    assert family(f.health_state(), "chart") == (1, False)     # the reply that came back still counts


def test_deadline_on_another_clock_is_refused():
    with pytest.raises(ValueError, match="not on the fetcher's clock"):
        make(FakeTransport(), deadline=12_345.0)                # a time.monotonic() value, not time.time()


def test_blocked_chart_call_stops_after_twenty_failed_replies():
    t = FakeTransport(chart=lambda k, p: (429, None))
    f, _ = make(t, workers=16)
    bars, report = f.bars_5m([f"S{i:03d}" for i in range(500)])
    # each of the 16 worker threads may have one request in flight when the 20th failure lands
    assert bars == {} and fetch.FAIL_FAST <= t.count("chart") < fetch.FAIL_FAST + 16
    assert report.status == "down" and report.http_429 == t.count("chart")
    assert report.notes[-1].startswith(f"stopped after {fetch.FAIL_FAST} failed replies in a row")


def test_a_200_between_failures_restarts_the_fail_fast_count():
    t = FakeTransport(chart=lambda k, p: chart_ok(k, p) if k[-1] in "49" else (429, None))
    f, _ = make(t, workers=1)                                   # one thread: replies arrive in symbol order
    bars, report = f.bars_5m([f"S{i:02d}" for i in range(50)])   # 4 blocked symbols (12 replies) between 200s
    assert len(bars) == 10 and t.count("chart") == 40 * fetch.TRIES + 10
    assert report.status == "degraded" and not any(n.startswith("stopped") for n in report.notes)


def test_fail_fast_after_partial_success_is_still_down():
    """E2E-1 (fix-verify coverage): 30 symbols answer, then Yahoo blocks. The call keeps the 30 bars it has but
    is graded down (SPEC 12.1), not degraded by its failure share: the tick reads quotes_ok from the grade, and
    as the 3rd strike it is the call that opens the breaker, so Nasdaq serves it at once."""
    symbols = [f"S{i:02d}" for i in range(60)]
    ok = chart_ok("AAPL", {})
    t = FakeTransport(chart=scripted(*[ok] * 30, (429, None)))
    f, _ = make(t, workers=1)                                   # one thread: replies arrive in symbol order
    bars, report = f.bars_5m(symbols)
    # S30..S35 fail 3 times each and S36 twice: the 20th failure in a row ends the call before S37
    assert len(bars) == report.ok == 30 and t.count("chart") == 30 + fetch.FAIL_FAST
    assert report.status == "down" and report.http_429 == fetch.FAIL_FAST
    assert report.notes == [f"stopped after {fetch.FAIL_FAST} failed replies in a row, 23 not requested"]
    t = FakeTransport(chart=scripted(*[ok] * 30, (429, None)), nasdaq=nasdaq_ok)
    f, _ = make(t, workers=1, health={"families": {"chart": {"consecutive_failures": 2}}})
    bars, report = f.bars_5m(symbols)
    assert report.source == "nasdaq" and set(bars) == set(symbols)
    assert report.notes[0] == "yahoo down: 30/60 ok" and family(f.health_state(), "chart") == (3, True)


def test_deadline_cut_of_an_otherwise_good_call_is_degraded():
    """DATA-2 (fix-verify coverage): the deadline stops a call after 9 of 10 symbols. The 9 answers alone would
    grade it ok (10% missing, no 429), but a call the deadline cut is at best degraded."""
    now = [T0]

    def chart_1s(key, params):
        now[0] += 1.0
        return chart_ok(key, params)
    t = FakeTransport(chart=chart_1s)
    f, _ = make(t, workers=1, clock=lambda: now[0], deadline=T0 + 8.5)
    bars, report = f.bars_5m([f"S{i}" for i in range(10)])
    assert len(bars) == report.ok == 9 and report.failed == ["S9"] and report.http_429 == 0
    assert report.status == "degraded" and report.notes == ["deadline reached, 1 not requested"]


@pytest.mark.parametrize("path", ["breaker open", "failed probe", "tripping call"])
def test_fail_fast_never_cuts_the_nasdaq_fallback(monkeypatch, path):
    """E2E-1 (fix-verify coverage): fail-fast is for Yahoo calls (SPEC 12.1). Nasdaq is the last source, so a
    run of failed Nasdaq replies (7 symbols x 3 tries = 21 in a row) does not end the call, on any of the three
    ways a call reaches it: the remaining symbols are still requested and served."""
    monkeypatch.setattr(fetch, "_pool_map", lambda fn, items, workers: [fn(x) for x in items])   # replies in order
    symbols = [f"S{i:02d}" for i in range(30)]
    broken = {fetch.NASDAQ_CHART.format(s) for s in symbols[:7]}
    t = FakeTransport(chart=chart_fail, nasdaq=lambda url, p: (503, None) if url in broken else nasdaq_ok(url, p))
    health = {"breaker open": open_health("chart"), "failed probe": open_health("chart", calls=2),
              "tripping call": {"families": {"chart": {"consecutive_failures": 2}}}}[path]
    f, _ = make(t, health=health)
    bars, report = f.bars_5m(symbols)
    assert t.count("nasdaq") == 7 * fetch.TRIES + 23 and 7 * fetch.TRIES > fetch.FAIL_FAST
    assert report.source == "nasdaq" and set(bars) == set(symbols[7:]) and report.failed == symbols[:7]
    assert report.status == "degraded" and not any(n.startswith("stopped") for n in report.notes)
    assert (t.count("chart") > 0) == (path != "breaker open")


class SimClock:
    """Virtual seconds for the tick-budget tests. `pool_map` stands in for fetch._pool_map: items run one at a
    time, each on the simulated worker that is free first (as a thread pool hands them out), so time advances
    as `workers` threads would in parallel. Requests add their latency, sleeps their length."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s

    def pool_map(self, fn, items, workers):
        free = [self.now] * max(1, min(workers, len(items)))
        out = []
        for item in items:
            k = free.index(min(free))
            self.now = free[k]
            out.append(fn(item))
            free[k] = self.now
        self.now = max(free)
        return out


def scan_ticks(monkeypatch, yahoo, ticks):
    """`ticks` scans 5 minutes apart on the virtual clock, as the tick runs them: quotes for the universe,
    movers, 5m bars for the universe, under the tick's deadline, health carried through engine.json. Yahoo
    answers with `yahoo(sim)`; Nasdaq works (0.3 s a request). Per tick: (elapsed s, reports, health,
    Yahoo chart requests)."""
    sim = SimClock(T0)
    monkeypatch.setattr(fetch, "_pool_map", sim.pool_map)

    def nasdaq(url, params):
        sim.now += 0.3
        return nasdaq_ok(url, params)
    t = FakeTransport(chart=yahoo(sim), quote=yahoo(sim), screen=yahoo(sim), nasdaq=nasdaq)
    universe = scan_symbols()
    health, out = None, []
    for i in range(ticks):
        start = sim.now = T0 + 300 * i
        charts = t.count("chart")
        f = Fetcher(health=health, workers=RUNTIME["fetch_workers"], timeout_s=RUNTIME["fetch_timeout_s"],
                    deadline=start + RUNTIME["tick_timeout_s"] - 40, transport=t, sleep=sim.sleep, clock=sim.time,
                    rng=random.Random(i))
        reports = {"quotes": f.quotes(universe)[1], "movers": f.movers()[1], "bars": f.bars_5m(universe)[1]}
        health = json.loads(json.dumps(f.health_state()))
        out.append((sim.now - start, reports, health, t.count("chart") - charts))
    return out


def test_yahoo_429_block_keeps_ticks_short_and_opens_each_family_breaker(monkeypatch):
    """E2E-1: Yahoo answers 429 to everything. Before, quotes and movers halved the pool to 4 workers and
    bars made 3 tries for ~520 symbols (~470 s), so the tick was killed at 270 s and no breaker ever opened.
    Now a tick takes about half a minute of virtual time, and each family trips on its own calls: the crumb
    breaker on tick 2's quote call, the chart breaker on tick 3's bars call."""
    def yahoo(sim):
        def reply(key, params):
            sim.now += 0.3
            return 429, None
        return reply
    ticks = scan_ticks(monkeypatch, yahoo, 3)
    assert max(elapsed for elapsed, *_ in ticks) < RUNTIME["tick_timeout_s"] / 4
    (_, r1, h1, charts1), (_, r2, h2, _), (_, r3, h3, _) = ticks
    assert {(r.source, r.status) for r in r1.values()} == {("yahoo", "down")} and charts1 == fetch.FAIL_FAST
    assert family(h1, "crumb") == (2, False) and family(h1, "chart") == (1, False)
    assert [r2[k].source for k in ("quotes", "movers", "bars")] == ["nasdaq", "nasdaq", "yahoo"]
    assert family(h2, "crumb") == (3, True) and family(h2, "chart") == (2, False)
    assert [r3[k].source for k in ("quotes", "movers", "bars")] == ["nasdaq"] * 3
    assert family(h3, "chart") == (3, True) and h3["name"] == "nasdaq"
    assert r3["bars"].failed == ["BRK-B"] and r3["bars"].ok == len(scan_symbols()) - 1     # no Nasdaq chart


def test_hung_yahoo_never_outlives_the_tick(monkeypatch):
    """DATA-2: every Yahoo request hangs until the 12 s timeout. Quotes and movers alone used to run past
    270 s. With the deadline (tick start + tick_timeout_s - 40 s) every tick ends before the kill, so its
    health is kept, and the breakers still open: crumb on tick 2, chart by tick 4."""
    def yahoo(sim):
        def hang(key, params):
            sim.now += RUNTIME["fetch_timeout_s"]
            raise TimeoutError("hung")
        return hang
    ticks = scan_ticks(monkeypatch, yahoo, 4)
    # A request started just before the deadline ends at most fetch_timeout_s later: every Transport call,
    # crumbed ones included, returns within its timeout (test_crumb_and_request_share_one_timeout).
    limit = RUNTIME["tick_timeout_s"] - 40 + RUNTIME["fetch_timeout_s"]
    assert limit < RUNTIME["tick_timeout_s"] and all(elapsed <= limit for elapsed, *_ in ticks)
    _, r1, h1, _ = ticks[0]
    assert all(r.status == "down" for r in r1.values()) and family(h1, "crumb") == (2, False)
    assert any(n.startswith("deadline reached") for n in r1["movers"].notes + r1["bars"].notes)
    assert family(ticks[1][2], "crumb") == (3, True) and ticks[1][1]["quotes"].source == "nasdaq"
    _, r4, h4, _ = ticks[-1]
    assert family(h4, "chart") == (3, True) and r4["bars"].source == "nasdaq"


# ---------------------------------------------------------------------- HttpTransport glue (no network)
class FakeResponse:
    def __init__(self, status_code, body=None, url="https://query1.finance.yahoo.com/"):
        self.status_code, self._body, self.url = status_code, body, url

    def json(self):
        return self._body


class FakeGetter:
    """Stands in for YfData() (and its `_session`) and for a curl_cffi session: `get` answers from a queue and
    records kwargs; YfData's `_get_cookie_and_crumb` answers from `crumbs` (default "crumb") and records its
    timeout; cookie-strategy switches are recorded."""

    def __init__(self, *answers, crumbs=()):
        self.answers, self.seen, self.crumb_timeouts = list(answers), [], []
        self.crumbs, self.strategies = list(crumbs), []

    def __call__(self):
        return self

    @property
    def _session(self):
        return self

    def _get_cookie_and_crumb(self, timeout=30):
        self.crumb_timeouts.append(timeout)
        crumb = self.crumbs.pop(0) if self.crumbs else "crumb"
        if isinstance(crumb, Exception):
            raise crumb
        return crumb, "basic"

    def _set_cookie_strategy(self, strategy, have_lock=False):
        self.strategies.append(strategy)

    def _is_this_consent_url(self, url):
        return "consent.yahoo.com" in url

    def _accept_consent_form(self, response, timeout):
        self.seen.append({"url": "consent accepted", "timeout": timeout})
        return self.answers.pop(0)

    def get(self, url, params=None, timeout=None, headers=None):
        self.seen.append({"url": url, "params": params, "timeout": timeout, "headers": headers})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_http_transport_yahoo_glue(monkeypatch):
    screen = {"quotes": [{"symbol": "PPLI"}]}
    getter = FakeGetter(FakeResponse(200, {"finance": {"result": [screen]}}),
                        FakeResponse(200, {"finance": {"result": None, "error": "glitch"}}),
                        FakeResponse(401, {"error": "Unauthorized"}), FakeResponse(401, {"error": "Unauthorized"}),
                        FakeResponse(200, fx("yahoo_quote.json")),
                        crumbs=["crumb", "crumb", fetch.YFRateLimitError()])
    monkeypatch.setattr(fetch, "YfData", getter)
    t = fetch.HttpTransport()
    assert t.yahoo_screen("day_gainers", 250, 7.0) == (200, screen)
    assert getter.seen[0]["timeout"] == 7.0 and getter.seen[0]["params"]["scrIds"] == "day_gainers"
    assert getter.seen[0]["params"]["crumb"] == "crumb"
    assert t.yahoo_screen("day_gainers", 250, 7.0) == (fetch.NETWORK_ERROR, None)
    assert t.yahoo_quote({"symbols": "AAPL"}, 7.0) == (429, None)      # a rate-limited crumb is a 429
    assert t.yahoo_quote({"symbols": "AAPL"}, 7.0) == (401, None)      # still refused after one crumb renewal
    assert getter.strategies == ["csrf"]
    status, body = t.yahoo_quote({"symbols": "AAPL"}, 7.0)
    assert status == 200 and body["quoteResponse"]["result"][0]["symbol"] == "AAPL"
    assert getter.crumb_timeouts == [7.0] * 6                   # the crumb is fetched with the caller's timeout


@pytest.mark.parametrize("renewal, expected", [
    ("crumb2", (200, {"ok": 1})),
    (fetch.YFRateLimitError(), (429, None)),
    (TimeoutError("getcrumb"), TimeoutError),
    (None, ConnectionError),
])
def test_stale_crumb_is_renewed_once_and_its_failures_keep_their_status(monkeypatch, renewal, expected):
    """yfinance 1.7's YfData.get() swallows a failed crumb renewal and sends the request without a crumb, which
    Yahoo refuses with a final 401. The transport renews the crumb itself, so a rate-limited renewal stays a
    429 and a failed or empty one a network error (retried by the Fetcher), as with yfinance 1.2."""
    getter = FakeGetter(FakeResponse(401), FakeResponse(200, {"ok": 1}), crumbs=["crumb1", renewal])
    monkeypatch.setattr(fetch, "YfData", getter)
    if isinstance(expected, type):
        with pytest.raises(expected):
            fetch.HttpTransport().yahoo_quote({"symbols": "AAPL"}, 7.0)
    else:
        assert fetch.HttpTransport().yahoo_quote({"symbols": "AAPL"}, 7.0) == expected
    assert getter.strategies == ["csrf"]
    sent = [s["params"]["crumb"] for s in getter.seen]
    assert sent == (["crumb1", "crumb2"] if renewal == "crumb2" else ["crumb1"])     # never sent without a crumb


def test_no_crumb_is_a_network_error_and_nothing_is_sent(monkeypatch):
    getter = FakeGetter(crumbs=[None])
    monkeypatch.setattr(fetch, "YfData", getter)
    with pytest.raises(ConnectionError):
        fetch.HttpTransport().yahoo_screen("day_gainers", 250, 7.0)
    assert getter.seen == []


def test_consent_redirect_is_accepted_like_yfdata_get(monkeypatch):
    getter = FakeGetter(FakeResponse(200, None, url="https://consent.yahoo.com/v2/collectConsent?sessionId=1"),
                        FakeResponse(200, {"quoteResponse": {"result": []}}))
    monkeypatch.setattr(fetch, "YfData", getter)
    assert fetch.HttpTransport().yahoo_quote({"symbols": "AAPL"}, 7.0) == (200, {"quoteResponse": {"result": []}})
    assert getter.seen[-1] == {"url": "consent accepted", "timeout": 7.0}


# The installed yfinance's real YfData, driven offline through a curl_cffi session that answers from a script.
# These pin the statuses the Fetcher relies on to whatever yfinance is installed (1.2 in the GitHub build, 1.7 in
# Vela's image): yfinance 1.7 changed how YfData.get handles failed crumbs (see HttpTransport._crumbed).
_YAHOO_ROUTES = (("fc", "fc.yahoo.com"), ("getcrumb", "/v1/test/getcrumb"), ("guce", "guce.yahoo.com/consent"),
                 ("quote", "/v7/finance/quote"))


class _YahooReply(FakeResponse):
    """A curl_cffi response as YfData reads it: status, text/content (cookie and crumb pages) and json()."""

    def __init__(self, status_code, text="", body=None):
        super().__init__(status_code, body)
        self.text, self.content = text, text.encode()


@pytest.fixture
def real_yfdata(monkeypatch):
    """Builds the real YfData singleton on a scripted session; the singleton and the cookie cache are restored."""
    from curl_cffi import requests as creq
    from yfinance import cache as yf_cache
    from yfinance.data import SingletonMeta, YfData

    class ScriptedSession(creq.Session):
        def __init__(self, script):
            super().__init__(impersonate="chrome")
            self.script, self.log = script, []

        def get(self, url, **kwargs):
            return self._answer(url, kwargs)

        def post(self, url, **kwargs):
            return self._answer(url, kwargs)

        def _answer(self, url, kwargs):
            key = next((k for k, frag in _YAHOO_ROUTES if frag in url), None)
            assert key is not None, f"unexpected request {url}"
            self.log.append(f"quote:{(kwargs.get('params') or {}).get('crumb')}" if key == "quote" else key)
            queue = self.script[key]
            answer = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(answer, BaseException):
                raise answer
            return answer

    monkeypatch.setattr(yf_cache._CookieCacheManager, "_Cookie_cache", yf_cache._CookieCacheDummy())
    monkeypatch.setattr(fetch, "YfData", YfData)
    saved = SingletonMeta._instances.pop(YfData, None)

    def build(script: dict) -> ScriptedSession:
        session = ScriptedSession(script)
        SingletonMeta._instances.pop(YfData, None)
        YfData(session=session)
        return session
    yield build
    SingletonMeta._instances.pop(YfData, None)
    if saved is not None:
        SingletonMeta._instances[YfData] = saved


def _scripted(key: str, step: object) -> object:
    """One scripted answer: an int status, a crumb string, or the name of a curl_cffi exception."""
    from curl_cffi.requests import exceptions
    if step in ("Timeout", "ConnectionError", "DNSError"):
        return getattr(exceptions, step)(step)
    if key == "getcrumb":
        return _YahooReply(429, "Too Many Requests") if step == 429 else _YahooReply(200, step)
    if key == "guce":
        return _YahooReply(200, "<html><body>no consent form</body></html>")
    body = {"quoteResponse": {"result": [{"symbol": "AAPL"}]}} if key == "quote" and step == 200 else None
    return _YahooReply(step, body=body)


@pytest.mark.parametrize("script, expected, sent", [
    pytest.param({"getcrumb": ["c1"], "quote": [200]}, 200, ["c1"], id="ok"),
    pytest.param({"getcrumb": [429], "quote": [200]}, 429, [], id="crumb rate-limited"),
    pytest.param({"getcrumb": ["c1", "c2"], "quote": [401, 200]}, 200, ["c1", "c2"], id="stale crumb renewed"),
    pytest.param({"getcrumb": ["c1", "c2"], "quote": [429, 429]}, 429, ["c1", "c2"], id="quote rate-limited"),
    pytest.param({"getcrumb": ["c1", 429], "quote": [401, 401]}, 429, ["c1"], id="renewal rate-limited"),
    pytest.param({"getcrumb": ["c1", "Timeout"], "quote": [401, 401]}, fetch.NETWORK_ERROR, ["c1"],
                 id="renewal timed out"),
    pytest.param({"fc": ["ConnectionError"], "getcrumb": ["c1"], "quote": [401]}, fetch.NETWORK_ERROR, [],
                 id="cookie host reset"),
    pytest.param({"fc": ["DNSError"], "getcrumb": ["c1"], "quote": [401]}, fetch.NETWORK_ERROR, [],
                 id="cookie host blocked"),
])
def test_crumbed_statuses_with_the_installed_yfinance(real_yfdata, script, expected, sent):
    session = real_yfdata({key: [_scripted(key, step) for step in steps]
                           for key, steps in {"fc": [404], "guce": ["form"], **script}.items()})
    try:
        status = fetch.HttpTransport().yahoo_quote({"symbols": "AAPL"}, 5.0)[0]
    except Exception:  # noqa: BLE001 - the Fetcher records a raised request as NETWORK_ERROR and retries it
        status = fetch.NETWORK_ERROR
    assert status == expected, session.log
    assert [entry.removeprefix("quote:") for entry in session.log if entry.startswith("quote:")] == sent


@pytest.mark.parametrize("step", ["crumb", "request"])
def test_hung_crumbed_call_is_cut_at_the_timeout(monkeypatch, step):
    """DATA-2: yfinance's cookie and crumb requests use their own 30 s timeout (and a 4xx retry fetches a new
    crumb the same way), so the transport caps each step at the caller's timeout."""
    release = threading.Event()

    class HungYfData(FakeGetter):
        def _get_cookie_and_crumb(self, timeout=30):
            if step == "crumb":
                release.wait(10)                                # a hung fc.yahoo.com or getcrumb request
            return "crumb", "basic"

        def get(self, url, params=None, timeout=None, headers=None):
            self.seen.append(url)
            release.wait(10)
            return FakeResponse(200, {})
    getter = HungYfData()
    monkeypatch.setattr(fetch, "YfData", getter)
    started = time.perf_counter()
    try:
        with pytest.raises(TimeoutError):
            fetch.HttpTransport().yahoo_quote({"symbols": "AAPL"}, 0.05)
        assert time.perf_counter() - started < 2
        assert getter.seen == ([] if step == "crumb" else [fetch.YAHOO_QUOTE])
    finally:
        release.set()


def test_crumb_and_request_share_one_timeout(monkeypatch):
    """Fix-verify note on DATA-2: with one cap per step, a slow crumb followed by a slow request could take
    twice the timeout (24 s), while the tick's deadline assumes a request ends within fetch_timeout_s. Here
    each step takes 0.75 x the timeout, so only a cap shared by both steps cuts the call: at the timeout,
    instead of answering after 1.5 x. (Updated: the work used to end 0.06 s after the cap, so a join() that
    returned late on a loaded runner let the call complete. It now ends 0.5 s after the cap, and each step
    still ends 0.25 s inside it, so a cap per step would let the call answer.)"""
    cap = 1.0

    class SlowYfData(FakeGetter):
        def _get_cookie_and_crumb(self, timeout=30):
            time.sleep(0.75 * cap)
            return "crumb", "basic"

        def get(self, url, params=None, timeout=None, headers=None):
            time.sleep(0.75 * cap)
            return FakeResponse(200, {"quoteResponse": {"result": []}})
    monkeypatch.setattr(fetch, "YfData", SlowYfData())
    with pytest.raises(TimeoutError):
        fetch.HttpTransport().yahoo_quote({"symbols": "AAPL"}, cap)


# Hand-written in the shape the verifier saw live for BRK.B: HTTP 200, rCode 200, data null.
NASDAQ_WENT_WRONG = {"data": None, "message": None, "status": {"rCode": 200, "bCodeMessage": [
    {"code": 1001, "errorMessage": "Something went wrong. Please try again later."}], "developerMessage": None}}


def test_http_transport_nasdaq_maps_rcode(monkeypatch):
    session = FakeGetter(FakeResponse(200, fx("nasdaq_chart_bad_asset.json")),
                         FakeResponse(200, fx("nasdaq_watchlist.json")),
                         FakeResponse(403),
                         FakeResponse(200, NASDAQ_WENT_WRONG))
    t = fetch.HttpTransport()
    monkeypatch.setattr(t, "_session", session)
    assert t.nasdaq(fetch.NASDAQ_CHART.format("SPY"), {"assetclass": "stocks"}, 5.0) == (400, None)
    assert t.nasdaq(fetch.NASDAQ_WATCHLIST, [("symbol", "spy|etf")], 5.0)[0] == 200
    assert t.nasdaq(fetch.NASDAQ_SCREENER, {}, 5.0) == (403, None)
    assert t.nasdaq(fetch.NASDAQ_CHART.format("AAPL"), {}, 5.0) == (503, None)    # "went wrong": retryable
    assert session.seen[0]["headers"]["Origin"] == "https://www.nasdaq.com"


def test_nasdaq_went_wrong_is_retried_and_brk_b_is_not_requested(monkeypatch):
    """DATA-7: a transient "Something went wrong" reply is retried; BRK.B has no Nasdaq chart at all."""
    session = FakeGetter(FakeResponse(200, NASDAQ_WENT_WRONG), FakeResponse(200, NASDAQ_WENT_WRONG),
                         FakeResponse(200, fx("nasdaq_chart_rs_aapl.json")))
    t = fetch.HttpTransport()
    monkeypatch.setattr(t, "_session", session)
    f, sleeps = make(t, health=open_health("chart"))
    bars, report = f.bars_5m(["AAPL", "BRK-B"])
    assert [s["url"] for s in session.seen] == [fetch.NASDAQ_CHART.format("AAPL")] * 3 and len(sleeps) == 2
    assert set(bars) == {"AAPL"} and report.source == "nasdaq" and report.failed == ["BRK-B"]
    assert report.notes == ["nasdaq fallback", "no Nasdaq chart: BRK-B"]


# ---------------------------------------------------------------------- live smoke test
LIVE_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V", "XOM", "UNH",
                "LLY", "AVGO", "COST", "BF-B", "AMD", "NFLX", "SPY", "QQQ"]


@pytest.mark.live      # skipped unless RADAR_LIVE=1 (tests/radar/conftest.py)
def test_live_quotes_and_bars_for_20_symbols():
    f = Fetcher()
    quotes, qr = f.quotes(LIVE_SYMBOLS)
    bars, br = f.bars_5m(LIVE_SYMBOLS)
    assert qr.source == "yahoo" and qr.ok >= 18 and br.ok >= 18
    assert quotes["BRK-B"].price and quotes["SPY"].quote_type == "ETF"
    for b in bars.values():
        assert np.all(b.ts % 300 == 0) and np.all(np.diff(b.ts) > 0) and len(b) > 0
    assert br.ms < 10_000 and qr.ms < 10_000
