"""radar.options: option-chain metrics of the radar names from a synthetic Yahoo v7 optionChain payload.

The pure helpers are checked on a fixed "now"; refresh() runs on the real RadarStore (SQLite) with a fake
fetch, and on the real clock, because the store prunes rows against the wall clock.
"""
from __future__ import annotations

import gzip
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime

import pytest

from radar import options
from radar.calendar_nyse import ET
from radar.config import OPTIONS

UTC = timezone.utc
NOW = datetime(2026, 10, 7, 15, 0, tzinfo=UTC).timestamp()        # Wednesday 11:00 ET
SIGMA_D = 0.02
HV = SIGMA_D * math.sqrt(252) * 100.0                              # 31.749...


# ---------------------------------------------------------------- payload builders

def today_of(now: float) -> date:
    return options.et_date(now)


def exp_epoch(now: float, days: int) -> int:
    """Yahoo's expiry stamp: 00:00 UTC of the expiry date, `days` calendar days after today (ET)."""
    d = today_of(now) + timedelta(days=days)
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def et_noon(now: float, days: int) -> int:
    d = today_of(now) + timedelta(days=days)
    return int(datetime.combine(d, dtime(12, 0), tzinfo=ET).timestamp())


def call(strike: float, *, bid: float = 1.0, ask: float = 1.1, volume: int = 100, oi: int = 1000,
         iv: float = 0.5) -> dict:
    return {"strike": strike, "bid": bid, "ask": ask, "volume": volume, "openInterest": oi,
            "impliedVolatility": iv, "contractSymbol": f"XYZ{int(strike * 1000):08d}C"}


def calls_good() -> list[dict]:
    """Strikes around a 100 spot: 85 and 120 lie outside the ±10% band, 95/100/105 inside."""
    return [call(85, volume=900, oi=9000), call(95, volume=200, oi=1000),
            call(100, bid=2.0, ask=2.2, volume=300, oi=1500, iv=0.5), call(105, volume=100, oi=800),
            call(120, volume=50, oi=5000)]


def chain(exp: int, calls: list[dict] | None = None, puts: list[dict] | None = None) -> dict:
    return {"expirationDate": exp, "calls": calls_good() if calls is None else calls,
            "puts": [{"volume": 400}, {"volume": 300}, {"volume": None}] if puts is None else puts}


def quote(price: float = 100.0, **kw) -> dict:
    return {"regularMarketPrice": price, "averageDailyVolume3Month": 2_000_000, **kw}


def result(now: float, *, days=(2, 9, 16, 23, 30, 44), first: int | None = None, q: dict | None = None,
           calls: list[dict] | None = None) -> dict:
    """optionChain.result[0] of the first request: the expiry list, the quote and the nearest expiry's chain
    (or the chain of `first` days out)."""
    exps = [exp_epoch(now, d) for d in days]
    head = exp_epoch(now, first) if first is not None else (exps[0] if exps else None)
    return {"expirationDates": exps, "quote": q if q is not None else quote(),
            "options": [chain(head, calls)] if head is not None else []}


def body(res: dict) -> dict:
    return {"optionChain": {"result": [res], "error": None}}


class Yahoo:
    """The fetch seam: answers by (symbol, date param) and records every call."""

    def __init__(self, now: float, *, by_symbol: dict | None = None, statuses: dict | None = None,
                 raise_first: int = 0):
        self.now = now
        self.by_symbol = by_symbol or {}
        self.statuses = statuses or {}        # symbol -> list of statuses to return first
        self.raise_first = raise_first
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, params: dict, timeout: float):
        self.calls.append((url, dict(params), timeout))
        sym = url.rsplit("/", 1)[-1]
        if self.raise_first:
            self.raise_first -= 1
            raise ConnectionError("reset by peer")
        queue = self.statuses.get(sym)
        if queue:
            st = queue.pop(0)
            if st != 200:
                return st, None
        res = self.by_symbol.get(sym) or result(self.now)
        if "date" in params:
            res = {**res, "options": [chain(int(params["date"]), res.get("_calls"))],
                   "quote": res.get("quote")}
        return 200, body({k: v for k, v in res.items() if k != "_calls"})


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(options.time, "sleep", lambda s: None)


# ---------------------------------------------------------------- pure helpers

def test_pick_expiry_skips_expiries_nearer_than_min_dte():
    exps = [exp_epoch(NOW, d) for d in (9, 2, 0, 16)]             # unsorted on purpose
    assert options.pick_expiry(exps, today_of(NOW), 7) == exp_epoch(NOW, 9)
    assert options.pick_expiry(exps, today_of(NOW), 0) == exp_epoch(NOW, 0)
    assert options.pick_expiry([exp_epoch(NOW, 7)], today_of(NOW), 7) == exp_epoch(NOW, 7)   # inclusive


def test_pick_expiry_takes_the_farthest_when_all_are_near_and_none_when_empty():
    exps = [exp_epoch(NOW, d) for d in (1, 3, 5)]
    assert options.pick_expiry(exps, today_of(NOW), 7) == exp_epoch(NOW, 5)
    assert options.pick_expiry([], today_of(NOW), 7) is None
    assert options.pick_expiry(None, today_of(NOW), 7) is None


def test_exp_date_is_the_utc_date_of_the_stamp():
    assert options.exp_date(exp_epoch(NOW, 9)) == date(2026, 10, 16)
    assert today_of(NOW) == date(2026, 10, 7)
    # 01:00 UTC on Oct 8 is still Oct 7 in New York
    assert options.et_date(datetime(2026, 10, 8, 1, 0, tzinfo=UTC).timestamp()) == date(2026, 10, 7)


def test_next_earnings_takes_the_nearest_future_date_and_the_estimate_flag():
    q = {"earningsTimestampStart": et_noon(NOW, 7), "earningsTimestampEnd": et_noon(NOW, 9),
         "earningsTimestamp": et_noon(NOW, -30), "isEarningsDateEstimate": True}
    assert options.next_earnings(q, NOW) == (date(2026, 10, 14), True)
    assert options.next_earnings({"earningsTimestamp": et_noon(NOW, 0)}, NOW) == (date(2026, 10, 7), False)
    assert options.next_earnings({"earningsTimestamp": et_noon(NOW, -1)}, NOW) == (None, False)
    assert options.next_earnings({"earningsTimestamp": "n/a", "earningsTimestampStart": 0}, NOW) == (None, False)


@pytest.mark.parametrize("bid, ask, expected", [
    (2.0, 2.2, 0.2 / 2.1 * 100.0), (1.0, 1.0, 0.0), (0.0, 1.0, None), (None, 1.0, None), (1.0, None, None),
    (1.2, 1.0, None), (-1.0, 1.0, None),
])
def test_spread_pct(bid, ask, expected):
    got = options.spread_pct(bid, ask)
    assert got == pytest.approx(expected) if expected is not None else got is None


@pytest.mark.parametrize("oi, spread, ntm_volume, expected", [
    (2000, 10.0, 500, "good"),            # every "good" limit, inclusive
    (1999, 10.0, 500, "fair"),
    (5000, 10.1, 900, "fair"),
    (5000, 5.0, 499, "fair"),
    (300, 25.0, 0, "fair"),               # fair needs no volume
    (299, 5.0, 900, "thin"),
    (5000, 25.1, 900, "thin"),
    (5000, None, 900, "thin"),            # no quotable spread is never better than thin
])
def test_grade(oi, spread, ntm_volume, expected):
    assert options.grade(oi, spread, ntm_volume, OPTIONS["grade"]) == expected


# ---------------------------------------------------------------- chain_metrics

def metrics(**kw) -> dict:
    res = result(NOW, q=kw.pop("q", None))
    ch = kw.pop("chain", chain(exp_epoch(NOW, 9)))
    return options.chain_metrics(res, ch, NOW, baseline=kw.pop("baseline", {"sigma_d": SIGMA_D, "adv20_usd": 5e7}),
                                 **kw)


def test_chain_metrics_of_a_liquid_chain():
    doc = metrics(q=quote(earningsTimestampStart=et_noon(NOW, 7), isEarningsDateEstimate=True))
    assert doc["as_of"] == "2026-10-07T15:00:00Z" and doc["price"] == 100.0
    assert doc["has_options"] is True and doc["weeklies"] is True
    assert doc["expiry"] == "2026-10-16" and doc["dte"] == 9
    assert doc["earnings_date"] == "2026-10-14" and doc["earnings_estimate"] is True
    assert doc["days_to_earnings"] == 7 and doc["earnings_before_expiry"] is True
    atm = doc["atm"]
    assert atm["strike"] == 100.0 and atm["bid"] == 2.0 and atm["ask"] == 2.2 and atm["mid"] == 2.1
    assert atm["spread_pct"] == round(0.2 / 2.1 * 100, 1) == 9.5
    assert atm["breakeven_pct"] == 2.2                            # (strike + ask) / spot - 1
    assert atm["volume"] == 300 and atm["oi"] == 1500 and atm["iv_pct"] == 50.0
    assert atm["contract"] == "XYZ00100000C"
    # near the money: strikes 95, 100, 105 (85 and 120 lie outside the ±10% band)
    assert doc["ntm_call_oi"] == 1000 + 1500 + 800 and doc["ntm_call_volume"] == 200 + 300 + 100
    assert doc["call_volume"] == 900 + 200 + 300 + 100 + 50 and doc["put_volume"] == 700
    assert doc["put_call_volume"] == round(700 / 1550, 2)
    assert doc["iv_pct"] == 50.0 and doc["hv_pct"] == round(HV, 1) == 31.7
    assert doc["iv_hv"] == round(50.0 / HV, 2) == 1.57
    assert doc["expected_move_pct"] == round(50.0 * math.sqrt(9 / 365), 1) == 7.9
    assert doc["adv_usd"] == 5e7                                  # the baseline's adv wins over Yahoo's
    assert doc["liquidity"] == "good"
    json.dumps(doc, allow_nan=False)


def test_atm_strike_is_the_nearest_and_the_higher_one_on_a_tie():
    assert metrics(q=quote(price=101.0))["atm"]["strike"] == 100.0
    assert metrics(q=quote(price=103.0))["atm"]["strike"] == 105.0
    assert metrics(q=quote(price=102.5))["atm"]["strike"] == 105.0       # tie: the higher strike


def test_earnings_after_expiry_and_no_earnings():
    doc = metrics(q=quote(earningsTimestampStart=et_noon(NOW, 12)))
    assert doc["earnings_before_expiry"] is False and doc["days_to_earnings"] == 12
    assert doc["earnings_estimate"] is False
    doc = metrics()
    assert doc["earnings_date"] is None and doc["earnings_estimate"] is None
    assert doc["days_to_earnings"] is None and doc["earnings_before_expiry"] is False
    # earnings on the expiry day itself counts as before expiry
    assert metrics(q=quote(earningsTimestamp=et_noon(NOW, 9)))["earnings_before_expiry"] is True


def test_weeklies_need_four_expiries_within_the_window():
    monthly = result(NOW, days=(9, 44, 72))
    doc = options.chain_metrics(monthly, chain(exp_epoch(NOW, 9)), NOW)
    assert doc["weeklies"] is False and doc["has_options"] is True
    four = result(NOW, days=(2, 9, 16, 35, 60))                      # 35 days is inside the window
    assert options.chain_metrics(four, chain(exp_epoch(NOW, 9)), NOW)["weeklies"] is True
    three = result(NOW, days=(2, 9, 36, 60))
    assert options.chain_metrics(three, chain(exp_epoch(NOW, 9)), NOW)["weeklies"] is False


def test_hv_and_adv_without_a_baseline():
    doc = metrics(baseline=None)
    assert doc["hv_pct"] is None and doc["iv_hv"] is None and doc["iv_pct"] == 50.0
    assert doc["adv_usd"] == 100.0 * 2_000_000                       # spot x Yahoo's 3-month volume
    assert metrics(baseline={"sigma_d": 0, "adv20_usd": None})["hv_pct"] is None


@pytest.mark.parametrize("calls, expected", [
    (calls_good(), "good"),
    # wide spread on the ATM call and little volume: fair
    ([call(95, oi=200), call(100, bid=2.0, ask=2.4, oi=200, volume=10), call(105, oi=200, volume=5)], "fair"),
    # too little open interest near the money: thin
    ([call(95, oi=50), call(100, bid=2.0, ask=2.2, oi=50), call(130, oi=99_999, volume=9999)], "thin"),
    # no bid on the ATM call: no spread, thin
    ([call(95, oi=5000, volume=900), call(100, bid=0.0, ask=0.5, oi=5000, volume=900)], "thin"),
])
def test_liquidity_grades(calls, expected):
    doc = metrics(chain=chain(exp_epoch(NOW, 9), calls))
    assert doc["liquidity"] == expected


def test_liquidity_none_without_options_quote_or_calls():
    no_exps = options.chain_metrics({"expirationDates": [], "quote": quote()}, None, NOW)
    assert no_exps["has_options"] is False and no_exps["liquidity"] == "none" and no_exps["expiry"] is None
    no_chain = options.chain_metrics(result(NOW), None, NOW)
    assert no_chain["has_options"] is True and no_chain["liquidity"] == "none" and no_chain["atm"] is None
    no_spot = options.chain_metrics(result(NOW, q={"regularMarketPrice": None}), chain(exp_epoch(NOW, 9)), NOW)
    assert no_spot["liquidity"] == "none" and no_spot["price"] is None
    no_calls = metrics(chain=chain(exp_epoch(NOW, 9), calls=[{"strike": None}]))
    assert no_calls["liquidity"] == "none" and no_calls["expiry"] == "2026-10-16" and no_calls["atm"] is None


def test_unquoted_iv_and_ask_are_left_out():
    calls = [call(100, bid=0.0, ask=0.0, iv=0.00001, volume=0, oi=0)]
    doc = metrics(chain=chain(exp_epoch(NOW, 9), calls, puts=[]))
    assert doc["iv_pct"] is None and doc["expected_move_pct"] is None and doc["iv_hv"] is None
    assert doc["atm"]["breakeven_pct"] is None and doc["atm"]["mid"] is None and doc["atm"]["spread_pct"] is None
    assert doc["put_call_volume"] is None and doc["liquidity"] == "thin"


# ---------------------------------------------------------------- ticker_metrics and the fetch seam

def test_ticker_metrics_requests_the_chosen_expiry_second():
    y = Yahoo(NOW)
    doc = options.ticker_metrics(y, "XYZ", NOW, {"sigma_d": SIGMA_D})
    assert [c[1] for c in y.calls] == [{}, {"date": exp_epoch(NOW, 9)}]     # 2 DTE skipped
    assert all(c[0] == options.YAHOO_OPTIONS.format("XYZ") for c in y.calls)
    assert all(c[2] == float(OPTIONS["fetch_timeout_s"]) for c in y.calls)
    assert doc["expiry"] == "2026-10-16" and doc["dte"] == 9 and doc["liquidity"] == "good"


def test_ticker_metrics_uses_the_first_chain_when_it_is_the_chosen_expiry():
    y = Yahoo(NOW, by_symbol={"XYZ": result(NOW, days=(9, 16, 23))})
    doc = options.ticker_metrics(y, "XYZ", NOW, None)
    assert [c[1] for c in y.calls] == [{}] and doc["expiry"] == "2026-10-16"


def test_ticker_metrics_without_expiries():
    y = Yahoo(NOW, by_symbol={"XYZ": {"expirationDates": [], "quote": quote(), "options": []}})
    doc = options.ticker_metrics(y, "XYZ", NOW, None)
    assert len(y.calls) == 1 and doc["has_options"] is False and doc["liquidity"] == "none"


def test_an_empty_result_is_a_ticker_without_options():
    y = lambda url, params, timeout: (200, {"optionChain": {"result": []}})   # noqa: E731
    doc = options.ticker_metrics(y, "NONE", NOW, None)
    assert doc is not None and doc["has_options"] is False and doc["price"] is None


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_rate_limit_or_server_error_is_retried_once(status, monkeypatch):
    naps = []
    monkeypatch.setattr(options.time, "sleep", naps.append)
    y = Yahoo(NOW, statuses={"XYZ": [status]})
    doc = options.ticker_metrics(y, "XYZ", NOW, None)
    assert doc is not None and doc["expiry"] == "2026-10-16"
    assert [c[1] for c in y.calls] == [{}, {}, {"date": exp_epoch(NOW, 9)}] and naps == [1.0]


def test_a_second_rate_limit_gives_up():
    y = Yahoo(NOW, statuses={"XYZ": [429, 429]})
    assert options.ticker_metrics(y, "XYZ", NOW, None) is None
    assert len(y.calls) == 2


def test_a_client_error_is_not_retried():
    y = Yahoo(NOW, statuses={"XYZ": [404]})
    assert options.ticker_metrics(y, "XYZ", NOW, None) is None and len(y.calls) == 1


def test_a_network_error_is_retried_once():
    y = Yahoo(NOW, raise_first=1)
    assert options.ticker_metrics(y, "XYZ", NOW, None)["expiry"] == "2026-10-16"
    y = Yahoo(NOW, raise_first=2)
    assert options.ticker_metrics(y, "XYZ", NOW, None) is None and len(y.calls) == 2


def test_a_failed_second_request_fails_the_ticker():
    y = Yahoo(NOW, statuses={"XYZ": [200, 429, 429]})
    assert options.ticker_metrics(y, "XYZ", NOW, None) is None
    assert [c[1] for c in y.calls] == [{}, {"date": exp_epoch(NOW, 9)}, {"date": exp_epoch(NOW, 9)}]


# ---------------------------------------------------------------- radar_tickers

def test_radar_tickers_members_by_intensity_then_heating_deduplicated_and_capped():
    state = {"members": [{"ticker": "LOW", "intensity": 40}, {"ticker": "HIGH", "intensity": 90},
                         {"ticker": "MID", "intensity": None}, {"ticker": ""}],
             "heating": [{"ticker": "WARM"}, {"ticker": "HIGH"}, {"ticker": None}]}
    assert options.radar_tickers(state, 30) == ["HIGH", "LOW", "MID", "WARM"]
    assert options.radar_tickers(state, 2) == ["HIGH", "LOW"]
    assert options.radar_tickers({}, 30) == []


# ---------------------------------------------------------------- refresh() on the store

def _seed(store, session_date: str, members: list[str], heating: list[str] = (),
          baselines: dict | None = None) -> None:
    state = {"schema": 1, "session": {"date": session_date},
             "members": [{"ticker": t, "intensity": 90 - i} for i, t in enumerate(members)],
             "heating": [{"ticker": t} for t in heating]}
    packs = None
    if baselines is not None:
        gz = gzip.compress(json.dumps({"asof": session_date, "symbols": baselines}).encode())
        packs = {"main": (session_date, "radar-sm-1", gz)}
    store.commit_tick(state=state, engine_doc={"schema": 1}, member_rows=[], events=[],
                      scan_row={"v": 1, "tick": "2026-10-07T14:00:00Z", "run_id": "local"}, packs=packs)


def test_refresh_writes_one_row_per_radar_name(radar_store):
    now = time.time()
    day = today_of(now).isoformat()
    _seed(radar_store, day, ["AAA", "BBB"], ["CCC"], baselines={"AAA": {"sigma_d": SIGMA_D, "adv20_usd": 9e7}})
    y = Yahoo(now)
    res = options.refresh(radar_store, fetch=y, clock=lambda: now)
    assert res["status"] == "ok" and res["tickers"] == 3 and res["ok"] == 3 and res["failed"] == []
    rows = radar_store.load_option_metrics()
    assert set(rows) == {"AAA", "BBB", "CCC"}
    assert rows["AAA"]["hv_pct"] == round(HV, 1) and rows["AAA"]["adv_usd"] == 9e7     # the pack's baseline
    assert rows["BBB"]["hv_pct"] is None and rows["BBB"]["adv_usd"] == 2e8
    assert rows["CCC"]["liquidity"] == "good" and rows["CCC"]["dte"] >= OPTIONS["min_dte"]
    assert radar_store.load_option_metrics(["AAA", "ZZZ"]) == {"AAA": rows["AAA"]}
    assert radar_store.load_option_metrics([]) == {}


def test_refresh_keeps_the_previous_row_of_a_failed_ticker(radar_store):
    now = time.time()
    _seed(radar_store, today_of(now).isoformat(), ["AAA", "BAD"])
    old = {"liquidity": "fair", "price": 12.0}
    radar_store.put_option_metrics({"BAD": old}, datetime.fromtimestamp(now - 3600, UTC), 7)
    y = Yahoo(now, statuses={"BAD": [500, 500]})
    res = options.refresh(radar_store, fetch=y, clock=lambda: now)
    assert res["status"] == "degraded" and res["ok"] == 1 and res["failed"] == ["BAD"]
    rows = radar_store.load_option_metrics()
    assert rows["BAD"] == old and rows["AAA"]["liquidity"] == "good"


def test_refresh_reports_error_when_every_ticker_fails_and_writes_nothing(radar_store):
    now = time.time()
    _seed(radar_store, today_of(now).isoformat(), ["AAA"])
    res = options.refresh(radar_store, fetch=Yahoo(now, statuses={"AAA": [404]}), clock=lambda: now)
    assert res["status"] == "error" and res["failed"] == ["AAA"]
    assert radar_store.load_option_metrics() == {}


def test_refresh_with_explicit_tickers_and_with_no_names(radar_store):
    now = time.time()
    res = options.refresh(radar_store, [], fetch=Yahoo(now), clock=lambda: now)        # empty store, no names
    assert res == {"status": "ok", "tickers": 0, "ok": 0, "failed": [], "duration_ms": 0}
    res = options.refresh(radar_store, ["QQQ"], fetch=Yahoo(now), clock=lambda: now)
    assert res["ok"] == 1 and set(radar_store.load_option_metrics()) == {"QQQ"}
    assert options.refresh(radar_store, fetch=Yahoo(now), clock=lambda: now)["tickers"] == 0   # no state


def test_baselines_for_survives_a_missing_or_corrupt_pack(radar_store):
    assert options.baselines_for(radar_store, None) == {}
    assert options.baselines_for(radar_store, "2026-10-07") == {}
    radar_store.commit_tick(state={"schema": 1}, engine_doc={"schema": 1}, member_rows=[], events=[],
                            scan_row={"v": 1, "tick": "2026-10-07T14:00:00Z", "run_id": "local"},
                            packs={"main": ("2026-10-07", "v", b"not gzip"),
                                   "extra": ("2026-10-07", "v", gzip.compress(b'{"symbols": {"X": {"sigma_d": 1}}}'))})
    assert options.baselines_for(radar_store, "2026-10-07") == {"X": {"sigma_d": 1}}


# ---------------------------------------------------------------- the option metrics table

def test_put_option_metrics_upserts_and_prunes_rows_older_than_keep_days(radar_store):
    now = datetime.now(UTC)
    radar_store.put_option_metrics({"AAA": {"v": 1}, "BBB": {"v": 1}}, now - timedelta(days=3), keep_days=7)
    radar_store.put_option_metrics({"AAA": {"v": 2}}, now, keep_days=7)
    assert radar_store.load_option_metrics() == {"AAA": {"v": 2}, "BBB": {"v": 1}}
    radar_store.put_option_metrics({"CCC": {"v": 1}}, now, keep_days=2)                # BBB is 3 days old
    assert radar_store.load_option_metrics() == {"AAA": {"v": 2}, "CCC": {"v": 1}}
    radar_store.put_option_metrics({"OLD": {"v": 1}}, now - timedelta(days=8), keep_days=7)
    assert "OLD" not in radar_store.load_option_metrics()                             # pruned on its own write
