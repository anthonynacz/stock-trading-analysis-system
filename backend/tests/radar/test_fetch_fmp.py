"""radar.fetch with Financial Modeling Prep as the primary source: mapping onto Quote/Bars/DailyBars, the per-call
fallback to Yahoo (and Nasdaq), FMP's own breaker, and the key never leaking (logs, notes, health, URLs).

Fixtures fmp_*.json are trimmed live FMP responses captured on 2026-10-15 with the production key (the responses
never contain it); the charts and daily rows end with the session of 2026-10-14.
yahoo_chart_aapl_1d_20261014.json is Yahoo's chart for that session.
"""
from __future__ import annotations

import copy
import json
import logging
import random
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest

from radar import fetch
from radar.calendar_nyse import ET, session_for
from radar.config import FMP, PARAMS, US_EXCHANGES

_DEFAULTS = (FMP["bars_today"], FMP["bars_history"])     # read at import, before any test patches them
from radar.fetch import Fetcher
from radar.universe import dynamic_candidates, scan_symbols

FIXTURES = Path(__file__).parent / "fixtures"
DAY = session_for(date(2026, 10, 14))
AFTER = float(DAY.close_epoch + 3600)          # 17:00 ET, the session over: every bar is final
KEY = "TESTKEY-0123456789abcdefSECRET"


@pytest.fixture(autouse=True)
def _fmp_bars_today(monkeypatch):
    """These tests exercise the FMP 1-minute path for today's bars; production keeps it off (see below)."""
    monkeypatch.setitem(FMP, "bars_today", "fmp")


def test_production_keeps_todays_bars_on_yahoo():
    """FMP's intraday volume is off the Yahoo-built baselines on ~18% of symbol-days (2026-10-15): the scan's
    bars stay on Yahoo, FMP only fills symbols Yahoo misses, until the baselines move to FMP as well."""
    assert _DEFAULTS == ("yahoo", "yahoo")


def fx(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def et(day: str, hhmm: str) -> int:
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=ET).timestamp())


class FakeTransport:
    """Yahoo, Nasdaq and FMP answering from handlers `(key, params) -> (status, body)` (a handler may raise).
    FMP's key is the request path."""

    def __init__(self, fmp=None, chart=None, quote=None, screen=None, nasdaq=None):
        self.handlers = {"fmp": fmp, "chart": chart, "quote": quote, "screen": screen, "nasdaq": nasdaq}
        self.calls: list[tuple[str, object, object]] = []

    def _answer(self, kind, key, params):
        self.calls.append((kind, key, params))
        handler = self.handlers[kind]
        if handler is None:
            raise AssertionError(f"unexpected {kind} call for {key}")
        return handler(key, params)

    def count(self, kind: str) -> int:
        return sum(1 for k, *_ in self.calls if k == kind)

    def keys(self, kind: str) -> list:
        return [key for k, key, _ in self.calls if k == kind]

    def fmp(self, path, params, timeout):
        return self._answer("fmp", path, params)

    def yahoo_chart(self, symbol, params, timeout):
        return self._answer("chart", symbol, params)

    def yahoo_quote(self, params, timeout):
        return self._answer("quote", params["symbols"], params)

    def yahoo_screen(self, name, count, timeout):
        return self._answer("screen", name, {"count": count})

    def nasdaq(self, url, params, timeout):
        return self._answer("nasdaq", url, params)


def make(transport, clock=AFTER, key=KEY, **kw):
    sleeps: list[float] = []
    f = Fetcher(transport=transport, fmp_key=key, sleep=sleeps.append, clock=lambda: clock,
                rng=random.Random(7), **kw)
    return f, sleeps


# ---------------------------------------------------------------------- canned FMP answers
def fmp_quote_rows(symbols, **over):
    base = {r["symbol"]: r for r in fx("fmp_screener_quotes.json") + fx("fmp_quote.json")}
    rows = []
    for s in symbols:
        row = copy.deepcopy(base.get(s) or {**base["AAPL"], "symbol": s, "name": f"{s} Inc."})
        rows.append({**row, **over})
    return rows


def fmp_ok(path, params):
    """A working FMP: quotes for any symbol, the saved 1-minute and 5-minute AAPL charts for any symbol,
    the saved daily history, profiles and the screener."""
    if path.startswith("/quote/"):
        return 200, fmp_quote_rows(path.removeprefix("/quote/").split(","))
    if path.startswith("/historical-chart/1min/"):
        return 200, fx("fmp_chart_1min_aapl.json")
    if path.startswith("/historical-chart/5min/"):
        rows = fx("fmp_chart_5min_aapl.json")
        return 200, [r for r in rows if params["from"] <= r["date"][:10] <= params["to"]]
    if path.startswith("/historical-price-full/"):
        syms = path.removeprefix("/historical-price-full/").split(",")
        hist = fx("fmp_daily_aapl.json")["historical"]
        items = [{"symbol": s, "historical": hist} for s in syms]
        return 200, ({"historicalStockList": items} if len(syms) > 1 else items[0])
    if path.startswith("/profile/"):
        return 200, [{"symbol": s, "isEtf": s.endswith("X"), "sector": "Financial Services",
                      "exchangeShortName": "NYSE", "companyName": f"{s} Corp"}
                     for s in path.removeprefix("/profile/").split(",")]
    if path == fetch.FMP_SCREENER:
        return 200, fx("fmp_screener.json")
    raise AssertionError(path)


def fmp_down(path, params):
    return 503, None


def yahoo_chart_ok(key, params):
    return 200, fx("yahoo_chart_aapl_1d.json")


def yahoo_quote_ok(key, params):
    rows = [{"symbol": s, "regularMarketPrice": 10.0, "quoteType": "EQUITY", "exchange": "NMS"}
            for s in params["symbols"].split(",")]
    return 200, {"quoteResponse": {"result": rows, "error": None}}


# ---------------------------------------------------------------------- quotes
def test_fmp_quotes_map_onto_the_yahoo_quote_contract():
    t = FakeTransport(fmp=fmp_ok)
    f, _ = make(t, clock=float(DAY.open_epoch + 3600))
    quotes, report = f.quotes(["AAPL", "SPY", "BRK-B", "BF-B", "GLD"])
    assert (report.source, report.status, report.ok) == ("fmp", "ok", 5) and t.count("fmp") == 1
    assert t.keys("fmp") == ["/quote/AAPL,SPY,BRK-B,BF-B,GLD"]
    raw = {r["symbol"]: r for r in fx("fmp_quote.json")}
    aapl = quotes["AAPL"]
    assert (aapl.price, aapl.prev_close, aapl.change_pct) == (raw["AAPL"]["price"], raw["AAPL"]["previousClose"],
                                                             raw["AAPL"]["changesPercentage"])
    assert aapl.day_volume == raw["AAPL"]["volume"] and isinstance(aapl.day_volume, int)
    assert aapl.market_time == raw["AAPL"]["timestamp"] and aapl.avg_volume_3m == raw["AAPL"]["avgVolume"]
    assert (aapl.exchange, aapl.quote_type, aapl.market_state) == ("NMS", "EQUITY", "REGULAR")
    assert aapl.sector == "Information Technology" and aapl.market_cap == raw["AAPL"]["marketCap"]
    assert (quotes["SPY"].exchange, quotes["SPY"].quote_type, quotes["SPY"].sector) == ("PCX", "ETF", None)
    assert quotes["GLD"].quote_type == "ETF" and quotes["BRK-B"].exchange == "NYQ"
    assert quotes["BF-B"].sector == "Consumer Staples"
    assert all(q.exchange in US_EXCHANGES for q in quotes.values())


@pytest.mark.parametrize("clock, state", [
    (DAY.pre_open.timestamp() + 60, "PRE"), (DAY.open_epoch + 60, "REGULAR"), (DAY.close_epoch + 60, "POST"),
    (DAY.post_close.timestamp() + 60, "CLOSED"), (et("2026-10-17", "12:00"), "CLOSED")])
def test_market_state_comes_from_the_calendar(clock, state):
    f, _ = make(FakeTransport(fmp=fmp_ok), clock=float(clock))
    assert f.quotes(["AAPL"])[0]["AAPL"].market_state == state


def test_exchange_codes_map_to_the_yahoo_codes_the_universe_filter_expects():
    assert [fetch._fmp_exchange(c, False) for c in ("NASDAQ", "NYSE", "AMEX", "CBOE", "BATS", "nyse")] == \
        ["NMS", "NYQ", "ASE", "BTS", "BTS", "NYQ"]
    assert fetch._fmp_exchange("AMEX", True) == "PCX"                     # NYSE Arca ETFs, as Yahoo labels them
    assert fetch._fmp_exchange("OTC", False) == "OTC" and "OTC" not in US_EXCHANGES
    assert fetch._fmp_exchange(None, False) is None and fetch._fmp_exchange("", False) is None


def test_quotes_outside_the_universe_take_type_and_sector_from_profiles():
    t = FakeTransport(fmp=fmp_ok)
    f, _ = make(t)
    quotes, report = f.quotes(["AAPL", "ZZAB", "ZZEX"])
    assert report.source == "fmp" and t.keys("fmp") == ["/quote/AAPL,ZZAB,ZZEX", "/profile/ZZAB,ZZEX"]
    zz = quotes["ZZAB"]
    assert (zz.quote_type, zz.sector, zz.exchange) == ("EQUITY", "Financials", "NMS")
    assert quotes["ZZEX"].quote_type == "ETF" and quotes["ZZEX"].sector is None
    no_profile = FakeTransport(fmp=lambda p, q: (500, None) if p.startswith("/profile/") else fmp_ok(p, q))
    quotes, report = make(no_profile)[0].quotes(["ZZAB"])
    assert report.status == "ok" and quotes["ZZAB"].quote_type is None     # unknown, never guessed


def test_symbols_fmp_lacks_are_filled_from_yahoo_in_the_same_call():
    t = FakeTransport(fmp=lambda p, q: (200, [r for r in fmp_ok(p, q)[1] if r["symbol"] != "MSFT"]),
                      quote=yahoo_quote_ok)
    f, _ = make(t)
    symbols = ["AAPL", "MSFT", "NVDA", "JPM", "SPY", "GLD"]
    quotes, report = f.quotes(symbols)
    assert set(quotes) == set(symbols) and t.keys("quote") == ["MSFT"]
    assert quotes["MSFT"].price == 10.0 and quotes["AAPL"].exchange == "NMS"
    assert (report.source, report.status, report.ok, report.failed) == ("fmp", "ok", 6, [])
    assert report.notes[0] == "fmp ok: 5/6 ok; yahoo served 1/1"        # 1 of 6 missing: still an ok FMP call
    h = f.health_state()
    assert h["families"]["fmp"]["consecutive_failures"] == 0 and h["families"]["crumb"]["last_ok_at"]


# ---------------------------------------------------------------------- 5-minute bars
def test_one_session_is_built_from_1_minute_bars_on_the_yahoo_contract():
    t = FakeTransport(fmp=fmp_ok)
    f, _ = make(t)
    bars, report = f.bars_5m(["AAPL"])
    assert report.source == "fmp" and t.calls[0][1:] == ("/historical-chart/1min/AAPL",
                                                         {"from": "2026-10-14", "to": "2026-10-14"})
    b = bars["AAPL"]
    assert b.ts.dtype == np.int64 and b.v.dtype == np.int64 and b.c.dtype == np.float64
    assert np.all(b.ts % 300 == 0) and np.all(np.diff(b.ts) > 0)
    assert b.ts[0] == DAY.open_epoch and b.ts[-1] == DAY.slot_start(77) and len(b) == 78
    assert all(DAY.slot_of(int(x)) is not None for x in b.ts)
    assert b.last_trade_ts is None and not b.provisional_last
    # bucket 0 by hand from the 1-minute rows 09:30..09:34
    mins = sorted((r for r in fx("fmp_chart_1min_aapl.json") if "09:30:00" <= r["date"][11:] < "09:35:00"),
                  key=lambda r: r["date"])
    assert (b.o[0], b.c[0]) == (mins[0]["open"], mins[-1]["close"])
    assert (b.h[0], b.l[0]) == (max(r["high"] for r in mins), min(r["low"] for r in mins))
    assert b.v[0] == round(sum(r["volume"] for r in mins))


def test_1_minute_sums_track_yahoo_while_fmps_5_minute_history_does_not():
    """Why today's bars are FMP 1-minute sums and the baselines stay on Yahoo by default (FMP["bars_history"]):
    on the saved session FMP's 1-minute sums match Yahoo's bars (opening bar included, day total within 0.1%),
    while FMP's own 5-minute bar for 09:30 counts the opening minute twice and another bar lacks 90% of its
    volume. (Live over 22 names x 5 sessions: radar/fetch.py docstring.)"""
    one = fetch._parse_fmp_bars("AAPL", fx("fmp_chart_1min_aapl.json"), False, True, AFTER)
    rows5 = [r for r in fx("fmp_chart_5min_aapl.json") if r["date"].startswith("2026-10-14")]
    five = fetch._parse_fmp_bars("AAPL", rows5, False, False, AFTER)
    y = fetch._parse_bars("AAPL", fx("yahoo_chart_aapl_1d_20261014.json"))
    assert np.array_equal(one.ts, y.ts) and np.array_equal(five.ts, y.ts) and len(y) == 78
    assert np.median(np.abs(one.c / y.c - 1)) < 1e-4
    assert one.v[0] == pytest.approx(y.v[0], rel=1e-3) and one.v.sum() == pytest.approx(y.v.sum(), rel=1e-3)
    first_minute = min(fx("fmp_chart_1min_aapl.json"), key=lambda r: r["date"])["volume"]
    assert five.v[0] == pytest.approx(one.v[0] + first_minute, rel=1e-3)      # the opening minute twice
    assert (five.v < 0.2 * y.v).sum() == 1                                      # a bar missing most volume


def test_regular_session_filter_and_extended_hours():
    ext = fx("fmp_chart_1min_aapl_ext.json")
    regular = fetch._parse_fmp_bars("AAPL", ext, False, True, AFTER)
    assert regular.ts[0] == DAY.open_epoch and regular.ts[-1] == DAY.slot_start(77)
    full = fetch._parse_fmp_bars("AAPL", ext, True, True, AFTER)
    assert full.ts[0] == int(DAY.pre_open.timestamp()) and full.ts[-1] > DAY.close_epoch
    assert len(full) > len(regular) and full.v[full.ts < DAY.open_epoch].sum() > 0     # FMP has pre-market volume
    t = FakeTransport(fmp=lambda p, q: (200, ext))
    make(t)[0].bars_5m(["AAPL"], include_prepost=True)
    assert t.calls[0][2] == {"from": "2026-10-14", "to": "2026-10-14", "extended": "true"}


def test_half_days_and_holidays_are_cut_by_the_calendar():
    """2026-11-27 closes at 13:00 ET; 2026-11-26 is Thanksgiving: rows FMP might send there are not session bars."""
    rows = [{"date": f"2026-11-27 {hm}:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 10}
            for hm in ("09:25", "09:30", "12:55", "13:00", "15:55")]
    rows += [{"date": "2026-11-26 10:00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 10}]
    b = fetch._parse_fmp_bars("X", rows, False, False, AFTER)
    assert [datetime.fromtimestamp(int(x), ET).strftime("%H:%M") for x in b.ts] == ["09:30", "12:55"]


def test_eastern_stamps_become_utc_bar_starts_across_dst():
    assert fetch._fmp_epochs(["2026-10-30 09:30:00", "2026-11-02 09:30:00"]).tolist() == \
        [int(datetime(2026, 10, 30, 13, 30, tzinfo=UTC).timestamp()),
         int(datetime(2026, 11, 2, 14, 30, tzinfo=UTC).timestamp())]
    with pytest.raises(ValueError):
        fetch._fmp_epochs(["2026-10-30T09:30:00Z"])


def test_volumes_are_rounded_rows_cleaned_and_order_restored():
    rows = [{"date": "2026-10-14 09:35:00", "open": 2.0, "high": 2.5, "low": 1.9, "close": 2.2, "volume": 11072.2627},
            {"date": "2026-10-14 09:30:00", "open": None, "high": 2.1, "low": 1.8, "close": 2.0, "volume": 99.5},
            {"date": "2026-10-14 09:40:00", "open": 2.2, "high": 2.3, "low": 2.1, "close": None, "volume": 5},
            {"date": "2026-10-14 09:35:00", "open": 2.0, "high": 2.6, "low": 1.9, "close": 2.3, "volume": 12000.0}]
    b = fetch._parse_fmp_bars("X", rows, False, False, AFTER)
    assert b.ts.tolist() == [DAY.slot_start(0), DAY.slot_start(1)] and b.v.tolist() == [100, 12000]
    assert b.o[0] == 2.0 and b.c[1] == 2.3                  # missing open -> close; the later duplicate wins


@pytest.mark.parametrize("offset", [-60, 0, 30, 89])
def test_a_bar_with_its_last_minute_is_final_one_without_it_provisional_until_it_settles(offset):
    """FMP has no last-trade row. A 1-minute aggregate is final once its last minute is in (it arrived 15-45 s
    after the bar's end live and the bar did not change after that); without it the bar is provisional until
    FMP["settle_s"] after its end (a thin name may not trade in that minute)."""
    rows = [r for r in fx("fmp_chart_1min_aapl.json") if r["date"] < "2026-10-14 11:00:00"]
    end = DAY.slot_start(17) + 300                                      # newest bar 10:55-11:00
    whole = fetch._parse_fmp_bars("AAPL", rows, False, True, end + offset)
    assert whole.ts[-1] == DAY.slot_start(17) and not whole.provisional_last
    partial_ = fetch._parse_fmp_bars("AAPL", [r for r in rows if r["date"] != "2026-10-14 10:59:00"], False, True,
                                     end + offset)
    assert partial_.ts[-1] == DAY.slot_start(17) and partial_.provisional_last is (offset < FMP["settle_s"])
    assert not fetch._parse_fmp_bars("AAPL", rows[:-1] if rows[-1]["date"].endswith("10:59:00") else rows,
                                     False, True, end + FMP["settle_s"]).provisional_last


def test_fmp_5_minute_endpoint_newest_bar_stays_provisional_until_rebuilt():
    rows = [r for r in fx("fmp_chart_5min_aapl.json") if "2026-10-14" in r["date"] and r["date"][11:] < "11:00:00"]
    end = DAY.slot_start(17) + 300
    assert fetch._parse_fmp_bars("AAPL", rows, False, False, end + 299 + FMP["settle_s"]).provisional_last
    assert not fetch._parse_fmp_bars("AAPL", rows, False, False, end + 300 + FMP["settle_s"]).provisional_last


def test_baseline_ranges_stay_on_yahoo_and_fmp_fills_what_yahoo_missed():
    t = FakeTransport(fmp=fmp_ok, chart=lambda k, p: (404, None) if k == "MSFT" else yahoo_chart_ok(k, p))
    f, _ = make(t)
    bars, report = f.bars_5m(["AAPL", "MSFT"], range_="1mo")
    assert t.keys("chart") == ["AAPL", "MSFT"]
    assert [path for k, path, _ in t.calls if k == "fmp"] == ["/historical-chart/5min/MSFT"] * 3
    assert set(bars) == {"AAPL", "MSFT"} and report.source == "yahoo" and report.status == "ok"
    assert report.notes[0] == "yahoo degraded: 1/2 ok; fmp served 1/1"
    t = FakeTransport(fmp=fmp_ok, chart=yahoo_chart_ok)
    make(t)[0].bars_5m(["AAPL", "MSFT"], range_="1mo")
    assert t.count("fmp") == 0


def test_todays_bars_can_be_kept_on_yahoo(monkeypatch):
    monkeypatch.setitem(FMP, "bars_today", "yahoo")
    t = FakeTransport(fmp=fmp_ok, chart=lambda k, p: (404, None) if k == "MSFT" else yahoo_chart_ok(k, p))
    bars, report = make(t)[0].bars_5m(["AAPL", "MSFT"])
    assert t.keys("chart") == ["AAPL", "MSFT"] and t.keys("fmp") == ["/historical-chart/1min/MSFT"]
    assert set(bars) == {"AAPL", "MSFT"} and report.source == "yahoo"


def test_baseline_range_uses_5_minute_windows_within_fmps_limit(monkeypatch):
    monkeypatch.setitem(FMP, "bars_history", "fmp")
    t = FakeTransport(fmp=fmp_ok)
    f, _ = make(t)
    bars, report = f.bars_5m(["AAPL", "MSFT"], range_="1mo")
    windows = [(p["from"], p["to"]) for k, path, p in t.calls if path == "/historical-chart/5min/AAPL"]
    assert windows == [("2026-10-05", "2026-10-14"), ("2026-09-23", "2026-10-02"), ("2026-09-14", "2026-09-22")]
    assert all((date.fromisoformat(b) - date.fromisoformat(a)).days < FMP["intraday_window_days"] for a, b in windows)
    assert report.source == "fmp" and set(bars) == {"AAPL", "MSFT"} and t.count("fmp") == 6
    b = bars["AAPL"]
    assert np.all(np.diff(b.ts) > 0) and len({int(x) // 86400 for x in b.ts}) == 23


def test_windows_skip_stretches_without_sessions():
    assert fetch._fmp_windows(date(2026, 11, 21), date(2026, 11, 30), 3) == [
        (date(2026, 11, 28), date(2026, 11, 30)), (date(2026, 11, 25), date(2026, 11, 27)),
        (date(2026, 11, 22), date(2026, 11, 24))]           # 11-21 is a Saturday, 11-26 Thanksgiving
    assert fetch._fmp_windows(date(2026, 10, 17), date(2026, 10, 18), 10) == []


def test_ranges_fmp_is_not_asked_for_go_straight_to_yahoo():
    t = FakeTransport(fmp=fmp_ok, chart=yahoo_chart_ok)
    bars, report = make(t)[0].bars_5m(["AAPL"], range_="max")
    assert report.source == "yahoo" and t.count("fmp") == 0 and "AAPL" in bars


def test_unknown_symbol_fails_over_to_yahoo_but_an_empty_pre_open_session_does_not():
    t = FakeTransport(fmp=lambda p, q: (200, []) if "ZZZZ" in p else fmp_ok(p, q), chart=yahoo_chart_ok)
    f, _ = make(t)
    bars, report = f.bars_5m(["AAPL", "ZZZZ"])
    assert t.keys("chart") == ["ZZZZ"] and report.source == "fmp" and set(bars) == {"AAPL", "ZZZZ"}
    pre = FakeTransport(fmp=lambda p, q: (200, []))
    bars, report = make(pre, clock=DAY.open_epoch - 600.0)[0].bars_5m(["AAPL"])
    assert report.status == "ok" and len(bars["AAPL"]) == 0 and pre.count("fmp") == 1


def test_malformed_fmp_bodies_fail_only_their_symbols():
    good = fx("fmp_chart_1min_aapl.json")
    bad = {"/historical-chart/1min/BADDATE": [{**good[0], "date": "2026-10-14T15:59:00Z"}],
           "/historical-chart/1min/NOTLIST": {"historical": []}}
    t = FakeTransport(fmp=lambda p, q: (200, bad.get(p, good)), chart=yahoo_chart_ok)
    bars, report = make(t)[0].bars_5m(["AAPL", "BADDATE", "NOTLIST", "MSFT"])
    assert report.source == "fmp" and report.ok == 4
    assert set(bars) == {"AAPL", "BADDATE", "NOTLIST", "MSFT"} and t.keys("chart") == ["BADDATE", "NOTLIST"]
    junk = FakeTransport(fmp=lambda p, q: (200, [{"symbol": ["AAPL"]}, "x", {"symbol": "AAPL", "price": "n/a"},
                                                 *fmp_quote_rows(["MSFT"])]), quote=yahoo_quote_ok)
    quotes, _ = make(junk)[0].quotes(["AAPL", "MSFT"])
    assert quotes["MSFT"].exchange == "NMS" and quotes["AAPL"].price == 10.0     # AAPL from Yahoo


# ---------------------------------------------------------------------- daily bars
def test_daily_bars_from_fmp_batches_of_five():
    t = FakeTransport(fmp=fmp_ok)
    f, _ = make(t)
    symbols = ["AAPL", "MSFT", "NVDA", "SPY", "QQQ", "BRK-B", "JPM"]
    daily, report = f.daily(symbols)
    assert report.source == "fmp" and set(daily) == set(symbols)
    assert [path for _, path, _ in t.calls] == ["/historical-price-full/AAPL,MSFT,NVDA,SPY,QQQ",
                                                "/historical-price-full/BRK-B,JPM"]
    assert t.calls[0][2] == {"from": "2026-07-14", "to": "2026-10-14"}
    d = daily["AAPL"]
    hist = fx("fmp_daily_aapl.json")["historical"]
    assert d.day.dtype == np.dtype("datetime64[D]") and np.all(np.diff(d.day.astype(np.int64)) > 0)
    assert d.day[-1] == np.datetime64("2026-10-14") and d.c[-1] == hist[0]["close"] and d.v[-1] == hist[0]["volume"]
    assert d.v.dtype == np.int64 and len(d.day) == len(hist)


def test_single_symbol_daily_body_and_unknown_symbols():
    t = FakeTransport(fmp=lambda p, q: (200, {}) if "ZZZZ" in p else fmp_ok(p, q), chart=lambda k, p: (404, None))
    daily, report = make(t)[0].daily(["AAPL"])
    assert report.source == "fmp" and len(daily["AAPL"].day) > 60
    daily, report = make(t)[0].daily(["ZZZZ"])
    assert daily == {} and report.failed == ["ZZZZ"] and t.keys("chart") == ["ZZZZ"]


# ---------------------------------------------------------------------- movers
def test_movers_are_yahoos_screens_rebuilt_from_the_fmp_screener():
    t = FakeTransport(fmp=fmp_ok)
    f, _ = make(t)
    movers, report = f.movers()
    assert (report.source, report.status, report.requested, report.ok) == ("fmp", "ok", 2, 2)
    screen = {r["symbol"]: r for r in fx("fmp_screener.json")}
    assert t.calls[0][1] == fetch.FMP_SCREENER and t.calls[0][2]["marketCapMoreThan"] == 2_000_000_000
    assert t.calls[1][1] == "/quote/" + ",".join(screen)
    syms = [q.symbol for q in movers]
    assert len(syms) == len(set(syms)) and set(syms) <= set(screen)
    for q in movers:
        assert q.exchange in US_EXCHANGES and q.quote_type == "EQUITY" and q.market_cap >= 2e9
        assert q.change_pct > 3 or q.change_pct < -2.5 or q.day_volume > 5_000_000
    assert dynamic_candidates(movers, set(scan_symbols()), PARAMS)        # the radar can add from them


def test_failed_fmp_movers_are_replaced_by_yahoos_screens():
    screens = fx("yahoo_screens.json")
    t = FakeTransport(fmp=lambda p, q: (500, None) if p != fetch.FMP_SCREENER else fmp_ok(p, q),
                      screen=lambda name, p: (200, screens[name]))
    movers, report = make(t)[0].movers()
    assert report.source == "yahoo" and report.notes[0] == "fmp degraded: 1/2 ok" and len(movers) == 11


# ---------------------------------------------------------------------- breaker, fallback, deadline
def test_no_key_or_no_fmp_transport_means_the_yahoo_path_unchanged(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    t = FakeTransport(fmp=fmp_ok, quote=yahoo_quote_ok)
    f = Fetcher(transport=t, clock=lambda: AFTER)
    _, report = f.quotes(["AAPL"])
    assert report.source == "yahoo" and t.count("fmp") == 0
    h = f.health_state()
    assert set(h["families"]) == {"chart", "crumb"} and "primary" not in h
    monkeypatch.setenv("FMP_API_KEY", KEY)
    assert Fetcher(transport=t, clock=lambda: AFTER).quotes(["AAPL"])[1].source == "fmp"     # the env key is read
    bare = FakeTransport(quote=yahoo_quote_ok)
    bare.fmp = None
    assert make(bare)[0].quotes(["AAPL"])[1].source == "yahoo"
    monkeypatch.setitem(FMP, "enabled", False)
    assert make(FakeTransport(fmp=fmp_ok, quote=yahoo_quote_ok))[0].quotes(["AAPL"])[1].source == "yahoo"


def test_fmp_outage_falls_back_to_yahoo_and_opens_fmps_own_breaker():
    t = FakeTransport(fmp=fmp_down, chart=yahoo_chart_ok, quote=yahoo_quote_ok)
    health, sources = None, []
    for _ in range(3):                                  # three ticks, health carried through engine.json
        f, _ = make(t, health=health)
        quotes, qrep = f.quotes(["AAPL", "SPY"])
        bars, brep = f.bars_5m(["AAPL", "SPY"])
        health = json.loads(json.dumps(f.health_state()))
        sources.append((qrep.source, qrep.status, brep.source, brep.status))
        assert set(quotes) == set(bars) == {"AAPL", "SPY"}
    assert sources == [("yahoo", "ok", "yahoo", "ok")] * 3
    fam = health["families"]["fmp"]
    # tick 2's quote call is the 3rd strike; then FMP is skipped except tick 3's bars call, a failed probe
    assert fam["breaker"]["open"] and fam["consecutive_failures"] == 4 and health["primary"] == "yahoo"
    assert health["families"]["chart"]["breaker"]["open"] is False and health["name"] == "yahoo"
    assert health["status"] == "ok"                    # the top level follows the Yahoo families now
    before = t.count("fmp")
    f, _ = make(t, health=health)
    _, report = f.quotes(["AAPL"])
    assert t.count("fmp") == before and report.source == "yahoo" and report.notes[0] == "fmp breaker open"


def test_open_fmp_breaker_probes_every_third_call_and_closes_after_two_good_probes():
    t = FakeTransport(fmp=fmp_ok, quote=yahoo_quote_ok)
    health = {"families": {"fmp": {"status": "down", "consecutive_failures": 3,
                                   "breaker": {"open": True, "opened_at": "2026-10-14T15:00:00Z", "calls": 0}}}}
    f, _ = make(t, health=health)
    sources = [f.quotes(["AAPL"])[1].source for _ in range(7)]
    assert sources == ["yahoo", "yahoo", "fmp", "yahoo", "yahoo", "fmp", "fmp"]
    h = f.health_state()
    assert not h["families"]["fmp"]["breaker"]["open"] and h["primary"] == "fmp"
    assert t.count("fmp") == 3


def test_failed_probe_is_one_try_per_request_and_falls_back():
    t = FakeTransport(fmp=fmp_down, quote=yahoo_quote_ok)
    health = {"families": {"fmp": {"breaker": {"open": True, "calls": 2, "good_probes": 1}}}}
    f, sleeps = make(t, health=health)
    quotes, report = f.quotes(["AAPL"])
    assert t.count("fmp") == 1 and sleeps == [] and report.source == "yahoo" and "AAPL" in quotes
    assert f.health_state()["families"]["fmp"]["breaker"]["good_probes"] == 0


def test_fmp_block_fails_fast_and_yahoo_serves_the_rest():
    t = FakeTransport(fmp=lambda p, q: (429, None), chart=yahoo_chart_ok)
    f, _ = make(t, workers=16)
    symbols = [f"S{i:03d}" for i in range(200)]
    bars, report = f.bars_5m(symbols)
    assert fetch.FAIL_FAST <= t.count("fmp") < fetch.FAIL_FAST + FMP["workers"]
    assert set(bars) == set(symbols) and report.source == "yahoo" and report.status == "ok"
    assert report.http_429 == t.count("fmp") and report.notes[0].startswith("fmp down: 0/200 ok")
    assert any(n.startswith(f"stopped after {fetch.FAIL_FAST}") for n in report.notes)


def test_partial_fmp_plus_nasdaq_is_reported_as_nasdaq():
    """The tick reads source "nasdaq" as volume off the baselines' basis (degraded_volume): a call Nasdaq helped
    must say so even when FMP served most of it."""
    chart_open = {"families": {"chart": {"status": "degraded", "consecutive_failures": 3,
                                         "breaker": {"open": True, "opened_at": "2026-10-14T15:00:00Z"}}}}
    t = FakeTransport(fmp=lambda p, q: (500, None) if "MSFT" in p else fmp_ok(p, q),
                      nasdaq=lambda url, p: (200, fx("nasdaq_chart_rs_aapl.json")))
    bars, report = make(t, health=chart_open)[0].bars_5m(["AAPL", "MSFT"])
    assert set(bars) == {"AAPL", "MSFT"} and report.source == "nasdaq" and report.status == "degraded"
    assert t.count("chart") == 0 and t.count("nasdaq") == 1


def test_past_the_deadline_fmp_is_not_called_either():
    t = FakeTransport()
    f, _ = make(t, deadline=AFTER - 1)
    for result, report in (f.quotes(["AAPL"]), f.movers(), f.bars_5m(["AAPL"]), f.daily(["AAPL"])):
        assert not result and (report.source, report.status) == ("yahoo", "down")
    assert t.calls == [] and f.health_state()["families"]["fmp"]["consecutive_failures"] == 0


def test_deadline_cut_inside_fmp_leaves_the_rest_unrequested_and_degraded():
    now = [AFTER]

    def slow(path, params):
        now[0] += 1.0
        return fmp_ok(path, params)
    t = FakeTransport(fmp=slow)
    f = Fetcher(transport=t, fmp_key=KEY, clock=lambda: now[0], deadline=AFTER + 8.5, sleep=lambda s: None)
    f._fmp_gets = lambda reqs, budget: [f._request(lambda r=r: t.fmp(*r, 1), budget) for r in reqs]   # in order
    bars, report = f.bars_5m([f"S{i}" for i in range(10)])
    assert len(bars) == 9 and report.status == "degraded" and t.count("chart") == 0


# ---------------------------------------------------------------------- health
def test_health_with_fmp_round_trips_and_leads_the_top_level():
    t = FakeTransport(fmp=fmp_ok)
    f, _ = make(t)
    f.quotes(["AAPL"])
    h = f.health_state()
    assert h["primary"] == "fmp" and set(h["families"]) == {"chart", "crumb", "fmp"}
    assert (h["name"], h["status"], h["last_ok_at"]) == ("yahoo", "ok", "2026-10-14T21:00:00Z")
    assert json.loads(json.dumps(h)) == h
    assert make(FakeTransport(fmp=fmp_ok), health=copy.deepcopy(h))[0].health_state() == h
    yahoo_open = copy.deepcopy(h)
    yahoo_open["families"]["chart"]["breaker"]["open"] = True
    g = make(FakeTransport(fmp=fmp_ok), health=yahoo_open)[0].health_state()
    assert g["name"] == "yahoo" and g["families"]["chart"]["breaker"]["open"]     # FMP leads: not on backup data
    flat = {"name": "nasdaq", "status": "down", "consecutive_failures": 3, "breaker": {"open": True}}
    g = make(FakeTransport(fmp=fmp_ok), health=flat)[0].health_state()
    assert g["families"]["fmp"]["breaker"]["open"] is False and g["families"]["chart"]["breaker"]["open"] is True


def test_the_worker_timeout_record_restores_with_fmp():
    """worker.health_after_timeout raises the worst closed family; the record it writes must restore."""
    from radar import worker as worker_mod
    f, _ = make(FakeTransport(fmp=fmp_down, quote=yahoo_quote_ok))
    f.quotes(["AAPL"])
    h = worker_mod.health_after_timeout(f.health_state(), "2026-10-14T20:00:00Z")
    assert h["families"]["fmp"]["consecutive_failures"] == 2
    g = make(FakeTransport(fmp=fmp_ok), health=json.loads(json.dumps(h)))[0].health_state()
    assert g["families"]["fmp"]["consecutive_failures"] == 2 and g["primary"] == "fmp"


# ---------------------------------------------------------------------- the key never leaks (security)
class _Resp:
    def __init__(self, status_code, body=None):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


class _Session:
    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.seen.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_http_transport_sends_the_key_in_a_header_and_maps_statuses(monkeypatch):
    session = _Session(_Resp(200, [{"symbol": "AAPL"}]), _Resp(401, {"Error Message": "Invalid API KEY."}),
                       _Resp(200, {"Error Message": "Limit Reach . Please upgrade your plan"}),
                       _Resp(200, {"Error Message": "Exclusive Endpoint"}), _Resp(200, {}))
    t = fetch.HttpTransport(fmp_key=KEY)
    monkeypatch.setattr(t, "_session", lambda: session)
    assert t.fmp("/quote/AAPL", {}, 5.0) == (200, [{"symbol": "AAPL"}])
    assert t.fmp("/quote/AAPL", {}, 5.0) == (401, None)
    assert t.fmp("/quote/AAPL", {}, 5.0) == (429, None)          # a plan limit is retried
    assert t.fmp("/quote/AAPL", {}, 5.0) == (403, None)
    assert t.fmp("/historical-price-full/ZZZZ", {"from": "2026-07-15"}, 5.0) == (200, {})
    for s in session.seen:
        assert s["headers"] == {"apikey": KEY} and KEY not in s["url"] and KEY not in json.dumps(s["params"])
        assert s["url"].startswith(fetch.FMP_BASE) and s["timeout"] == 5.0
    assert KEY not in repr(t) and KEY not in str(t)


def test_the_key_never_reaches_logs_notes_or_health(caplog):
    """Security requirement: errors carrying the key (as a URL with ?apikey= would) never surface. The Fetcher
    keeps no exception text, scrubs notes, and keeps httpx's request lines (which print full URLs) out of the logs."""
    caplog.set_level(logging.DEBUG)

    def leaky(path, params):
        raise ConnectionError(f"GET {fetch.FMP_BASE}{path}?apikey={KEY} failed")
    t = FakeTransport(fmp=leaky, chart=yahoo_chart_ok, quote=yahoo_quote_ok)
    f, _ = make(t, on_health=lambda h: logging.getLogger("radar.test").info("health %s", h))
    out = [f.quotes(["AAPL"]), f.bars_5m(["AAPL"]), f.daily(["AAPL"])]
    assert all(r.source == "yahoo" for _, r in out)
    text = caplog.text + json.dumps(f.health_state()) + json.dumps([r.to_json() for _, r in out])
    assert KEY not in text and "apikey=" not in text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_httpx_request_lines_are_not_logged(caplog):
    httpx = pytest.importorskip("httpx")
    caplog.set_level(logging.DEBUG)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
    client.get(f"{fetch.FMP_BASE}/quote/AAPL", params={"apikey": KEY})
    assert KEY not in caplog.text


def test_notes_are_scrubbed_of_the_key(monkeypatch):
    t = FakeTransport(fmp=fmp_ok)
    f, _ = make(t)
    real = fetch._report

    def noisy(*a, **kw):
        r = real(*a, **kw)
        r.notes.append(f"echo {KEY}")
        return r
    monkeypatch.setattr(fetch, "_report", noisy)
    _, report = f.quotes(["AAPL"])
    assert report.notes == ["echo ***"]


# ---------------------------------------------------------------------- live (RADAR_LIVE=1 and FMP_API_KEY)
@pytest.mark.live
def test_live_fmp_quotes_bars_daily_and_movers():
    import os
    if not os.environ.get("FMP_API_KEY"):
        pytest.skip("FMP_API_KEY not set")
    f = Fetcher()
    symbols = ["AAPL", "MSFT", "NVDA", "BRK-B", "BF-B", "SPY", "QQQ"]
    quotes, qr = f.quotes(symbols)
    bars, br = f.bars_5m(symbols)
    daily, dr = f.daily(symbols)
    _, mr = f.movers()
    assert {qr.source, br.source, dr.source, mr.source} == {"fmp"}
    assert quotes["SPY"].quote_type == "ETF" and quotes["AAPL"].exchange == "NMS"
    assert all(np.all(b.ts % 300 == 0) for b in bars.values()) and len(daily["AAPL"].day) > 50
