"""IV visibility (radar/options.py) and FMP market context (radar/context.py).

The EBAY case (2026-10-15): a takeover rumour lifted the front-week ATM IV to 1.7x the stock's normal
volatility; it bled from ~47% to ~38% while the stock drifted, and the radar did not say so.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from radar import context, options

NOW = datetime(2026, 10, 15, 19, 30, tzinfo=timezone.utc).timestamp()   # 15:30 ET


def doc(**kw):
    base = {"as_of": "2026-10-15T19:30:00Z", "price": 122.5, "expiry": "2026-10-23", "dte": 8, "iv_pct": 38.4,
            "hv_pct": 26.1, "iv_hv": 1.47, "iv_30d_pct": 43.1, "term_ratio": 0.89, "vega_per_pt_usd": 7.23,
            "earnings_date": "2026-10-28", "earnings_before_expiry": False}
    base.update(kw)
    return base


# ---------------------------------------------------------------- options: IV numbers

def test_vega_per_point_matches_black_scholes():
    # ATM, 8 days, 38% IV, $122.5: S * phi(d1) * sqrt(T) ~ $7.2 per contract per IV point
    assert options.vega_per_point(122.5, 123.0, 38.4, 8) == pytest.approx(7.2, abs=0.3)
    assert options.vega_per_point(None, 123.0, 38.4, 8) is None
    assert options.vega_per_point(122.5, 123.0, 0, 8) is None


def test_atm_iv_picks_the_nearest_strike_and_skips_unquoted():
    chain = {"calls": [{"strike": 120, "impliedVolatility": 0.5}, {"strike": 125, "impliedVolatility": 0.4}]}
    assert options.atm_iv(chain, 124.0) == pytest.approx(40.0)
    assert options.atm_iv({"calls": [{"strike": 125, "impliedVolatility": 0.00001}]}, 124.0) is None
    assert options.atm_iv(None, 124.0) is None
    assert options.atm_iv({"calls": [{"strike": 125, "impliedVolatility": 0.016}]}, 124.0) is None   # GTX-style noise


def test_the_first_reading_of_the_day_is_kept_and_the_change_measured():
    first = options.carry_iv_first(doc(as_of="2026-10-15T15:15:00Z", iv_pct=47.0), None, NOW)
    assert first["iv_first"] == {"day": "2026-10-15", "expiry": "2026-10-23", "pct": 47.0, "at": "2026-10-15T15:15:00Z"}
    later = options.carry_iv_first(doc(), first, NOW)
    assert later["iv_first"]["pct"] == 47.0 and later["iv_change_pts"] == pytest.approx(-8.6)
    assert later["iv_change_pct"] == pytest.approx(-18.3, abs=0.1)


def test_the_first_reading_resets_on_a_new_day_or_a_new_expiry():
    prev = {"iv_first": {"day": "2026-10-14", "expiry": "2026-10-23", "pct": 47.0, "at": "2026-10-14T15:00:00Z"}}
    assert options.carry_iv_first(doc(), prev, NOW)["iv_first"]["pct"] == 38.4
    prev = {"iv_first": {"day": "2026-10-15", "expiry": "2026-10-16", "pct": 47.0, "at": "2026-10-15T15:00:00Z"}}
    assert options.carry_iv_first(doc(), prev, NOW)["iv_change_pts"] == 0.0


def test_a_row_from_before_iv_first_existed_seeds_the_first_reading():
    prev = {"as_of": "2026-10-15T15:45:56Z", "expiry": "2026-10-23", "iv_pct": 43.7}
    d = options.carry_iv_first(doc(), prev, NOW)
    assert d["iv_first"]["pct"] == 43.7 and d["iv_change_pts"] == pytest.approx(-5.3)
    stale = {"as_of": "2026-10-14T15:45:56Z", "expiry": "2026-10-23", "iv_pct": 43.7}
    assert options.carry_iv_first(doc(), stale, NOW)["iv_first"]["pct"] == 38.4


def test_ebay_is_flagged_as_crushing_with_the_cost_per_contract():
    d = options.carry_iv_first(doc(), {"iv_first": {"day": "2026-10-15", "expiry": "2026-10-23", "pct": 47.0,
                                                    "at": "2026-10-15T15:15:00Z"}}, NOW)
    risk = options.iv_risk(d, NOW)
    assert risk["level"] == "crushing" and risk["label"] == "IV crushing"
    assert "47% → 38%" in risk["reasons"][0] and "$62 per contract" in risk["reasons"][0]


def test_a_drop_shorter_than_the_minimum_window_is_not_a_crush():
    d = options.carry_iv_first(doc(), {"iv_first": {"day": "2026-10-15", "expiry": "2026-10-23", "pct": 47.0,
                                                    "at": "2026-10-15T19:10:00Z"}}, NOW)
    assert options.iv_risk(d, NOW)["level"] != "crushing"


@pytest.mark.parametrize("kw, level, phrase", [
    ({"earnings_before_expiry": True}, "high", "before this expiry"),
    ({"last_earnings_date": "2026-10-14"}, "high", "in the last session"),
    ({"last_earnings_date": "2026-10-15"}, "high", "today"),
    ({"iv_hv": 1.7, "iv_pct": 44.0}, "elevated", "1.7× the stock's normal"),
    ({"term_ratio": 1.3, "iv_pct": 60.0, "iv_30d_pct": 46.0}, "elevated", "priced into this week"),
    ({}, "normal", None),
])
def test_iv_risk_levels(kw, level, phrase):
    risk = options.iv_risk(options.carry_iv_first(doc(**kw), None, NOW), NOW)
    assert risk["level"] == level
    if phrase:
        assert any(phrase in r for r in risk["reasons"])
    else:
        assert risk["reasons"] == []


def test_radar_tickers_include_todays_exits_after_members_and_heating():
    state = {"session": {"date": "2026-10-15"},
             "members": [{"ticker": "A", "intensity": 50}, {"ticker": "B", "intensity": 90}],
             "heating": [{"ticker": "C"}],
             "recent_exits": [{"ticker": "EBAY", "exited_at": "2026-10-15T15:50:00Z"},
                              {"ticker": "OLD", "exited_at": "2026-10-14T19:00:00Z"}, {"ticker": "A"}]}
    assert options.radar_tickers(state, 10) == ["B", "A", "C", "EBAY"]


# ---------------------------------------------------------------- context: parsing

def test_fmp_times_are_eastern_unless_marked_utc():
    assert context.et_to_utc("2026-10-15 13:01:42").strftime("%H:%M") == "17:01"
    assert context.et_to_utc("2026-10-15T19:11:38.000Z").strftime("%H:%M") == "19:11"
    assert context.et_to_utc("2026-10-15").strftime("%Y-%m-%d %H:%M") == "2026-10-15 04:00"
    assert context.et_to_utc("garbage") is None and context.et_to_utc(None) is None


def test_earnings_by_symbol_splits_reported_from_upcoming():
    from datetime import date
    rows = [{"symbol": "EBAY", "date": "2026-10-28", "time": "amc"},
            {"symbol": "EBAY", "date": "2026-08-05", "time": "amc"},
            {"symbol": "AAA", "date": "2026-10-15", "time": "bmo"},     # reported this morning
            {"symbol": "BBB", "date": "2026-10-15", "time": "amc"},     # still ahead tonight
            {"symbol": "CCC", "date": "bad"}]
    got = context.earnings_by_symbol(rows, date(2026, 10, 15))
    assert got["EBAY"] == {"next": {"date": "2026-10-28", "time": "amc"}, "last": {"date": "2026-08-05", "time": "amc"}}
    assert "next" not in got["AAA"] and got["AAA"]["last"]["date"] == "2026-10-15"
    assert got["BBB"]["next"]["date"] == "2026-10-15" and "CCC" not in got


@pytest.mark.parametrize("earn, time_, expiry, expected", [
    ("2026-10-20", "amc", "2026-10-23", True),
    ("2026-10-23", "bmo", "2026-10-23", True),      # before the open on expiry day: the option still trades
    ("2026-10-23", "amc", "2026-10-23", False),     # after the close on expiry day: already expired
    ("2026-10-28", "amc", "2026-10-23", False),
    (None, "", "2026-10-23", None),
])
def test_earnings_before_expiry(earn, time_, expiry, expected):
    assert context.earnings_before_expiry(earn, time_, expiry) is expected


def test_catalysts_rank_company_and_analyst_items_first_and_drop_stale_ones():
    now = datetime(2026, 10, 15, 19, 30, tzinfo=timezone.utc)
    news = [{"symbol": "EBAY", "title": "eBay Jumps 5% on Takeover Speculation", "publishedDate": "2026-10-15 13:01:42",
             "site": "Benzinga", "url": "u1"},
            {"symbol": "EBAY", "title": "Old story", "publishedDate": "2026-10-13 10:00:00", "site": "x"},
            {"symbol": "ETSY", "title": "Etsy story", "publishedDate": "2026-10-15 12:00:00", "site": "x"},
            {"symbol": "EBAY", "title": "eBay Jumps 5% on Takeover Speculation", "publishedDate": "2026-10-15 13:05:00", "site": "dup"}]
    press = [{"date": "2026-10-15 08:00:00", "title": "eBay announces live commerce partnership"}]
    filings = [{"fillingDate": "2026-10-15 00:00:00", "finalLink": "sec"}]
    grades = [{"publishedDate": "2026-10-15T12:00:00.000Z", "gradingCompany": "Morgan Stanley", "action": "upgrade",
               "previousGrade": "Equal-Weight", "newGrade": "Overweight", "newsURL": "g"}]
    got = context.catalysts_for("EBAY", now, news, press, filings, grades)
    assert [c["kind"] for c in got] == ["8-K", "press", "analyst"]
    assert got[2]["title"] == "Morgan Stanley upgrades Equal-Weight → Overweight"
    held = context.catalysts_for("X", now, [], None, None, [{"publishedDate": "2026-10-15T12:00:00.000Z",
                                  "gradingCompany": "Oppenheimer", "action": "hold", "newGrade": "Outperform"}])
    assert held[0]["title"] == "Oppenheimer maintains Outperform"
    only_news = context.catalysts_for("EBAY", now, news, None, None, None)
    assert [c["title"] for c in only_news] == ["eBay Jumps 5% on Takeover Speculation"]     # deduped, stale dropped
    assert only_news[0]["at"] == "2026-10-15T17:05:00Z"


def test_float_doc_flags_low_float():
    assert context.float_doc([{"floatShares": 12_000_000, "freeFloat": 60.4}]) == {"shares": 12e6, "free_pct": 60.4, "low": True}
    assert context.float_doc([{"floatShares": 399_805_800, "freeFloat": 89.8}])["low"] is False
    assert context.float_doc([]) is None and context.float_doc(None) is None


# ---------------------------------------------------------------- context: merge

def _fake_get(calls):
    def get(path, params):
        calls.append(path)
        if path == "/api/v3/earning_calendar":
            return [{"symbol": "EBAY", "date": "2026-10-28", "time": "amc"},
                    {"symbol": "EBAY", "date": "2026-08-05", "time": "amc"}]
        if path == "/api/v3/stock_news":
            return [{"symbol": "EBAY", "title": "eBay Jumps 5% on Takeover Speculation",
                     "publishedDate": "2026-10-15 13:01:42", "site": "Benzinga"}]
        if path.startswith("/api/v4/shares_float"):
            return [{"floatShares": 399_805_800, "freeFloat": 89.8}]
        return []
    return get


def test_add_context_merges_catalysts_earnings_and_float(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "k")
    calls: list[str] = []
    docs = {"EBAY": {"expiry": "2026-10-23", "earnings_date": "2026-10-29", "earnings_estimate": True}}
    assert context.add_context(docs, NOW, get=_fake_get(calls)) == 1
    d = docs["EBAY"]
    assert d["earnings_date"] == "2026-10-28" and d["earnings_time"] == "amc" and d["earnings_estimate"] is False
    assert d["days_to_earnings"] == 13 and d["earnings_before_expiry"] is False
    assert d["last_earnings_date"] == "2026-08-05" and d["float"]["low"] is False
    assert d["catalysts"][0]["title"].startswith("eBay Jumps 5%") and d["context_source"] == "fmp"
    assert calls.count("/api/v3/earning_calendar") == 1 and calls.count("/api/v3/stock_news") == 1


def test_no_key_no_context(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    docs = {"EBAY": {}}
    assert context.add_context(docs, NOW) == 0 and docs == {"EBAY": {}}


def test_the_api_key_never_reaches_the_logs(monkeypatch, caplog):
    monkeypatch.setenv("FMP_API_KEY", "SECRET123")

    class Boom:
        status_code = 200

        @staticmethod
        def json():
            return {"Error Message": "Invalid API KEY SECRET123"}

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Boom())
    with caplog.at_level(logging.DEBUG):
        assert context.fmp_get("/api/v3/quote/X") is None
        monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("url ...apikey=SECRET123")))
        assert context.fmp_get("/api/v3/quote/X") is None
    assert "SECRET123" not in caplog.text and "***" in caplog.text
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_a_friday_after_close_report_is_still_just_reported_on_monday():
    monday = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc).timestamp()
    d = options.carry_iv_first(doc(as_of="2026-10-19T15:00:00Z", last_earnings_date="2026-10-16"), None, monday)
    assert options.iv_risk(d, monday)["level"] == "high"
    d = options.carry_iv_first(doc(as_of="2026-10-19T15:00:00Z", last_earnings_date="2026-10-15"), None, monday)
    assert options.iv_risk(d, monday)["level"] == "normal"          # two sessions ago: no longer "just"


def test_empty_off_hours_quotes_never_replace_a_good_row():
    empty = {"has_options": True, "atm": {"bid": 0.0, "ask": 0.0}, "ntm_call_oi": 0}
    assert options.empty_quotes(empty) is True
    assert options.empty_quotes({"has_options": True, "atm": {"bid": 1.2, "ask": 1.3}, "ntm_call_oi": 0}) is False
    assert options.empty_quotes({"has_options": False, "atm": None}) is False     # truly no options: store it

    class Store:
        def __init__(self):
            self.written = None

        def load_state(self):
            return {"members": [{"ticker": "GTX", "intensity": 50}], "session": {"date": "2026-10-15"}}

        def load_pack(self, *_):
            return None, None

        def load_option_metrics(self, tickers):
            return {}

        def put_option_metrics(self, docs, as_of, keep_days):
            self.written = docs

    import radar.options as o
    st = Store()
    orig = o.ticker_metrics
    try:
        o.ticker_metrics = lambda fetch, s, now, base, params=o.OPTIONS: {"has_options": True, "atm": {"bid": 0, "ask": 0},
                                                                          "ntm_call_oi": 0, "as_of": "x"}
        res = o.refresh(st, fetch=lambda *a: (200, {}), clock=lambda: NOW)
    finally:
        o.ticker_metrics = orig
    assert st.written is None and res["kept_previous"] == ["GTX"]
