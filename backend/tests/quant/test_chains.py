"""Chain snapshots (quant/chains.py): expiry selection, contract filtering / sanitising, rows, idempotent store."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from db.models import QuantChainSnapshot
from quant import chains, iv

NOW = datetime(2026, 11, 16, 20, 40, tzinfo=timezone.utc)      # Monday 15:40 ET
TODAY = date(2026, 11, 16)


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def test_select_expiries_brackets_30_days_and_caps():
    weeklies = [_epoch(date.fromordinal(TODAY.toordinal() + k)) for k in range(4, 200, 7)]
    got = chains.select_expiries(weeklies, TODAY)
    dtes = [(chains.ropt.exp_date(e) - TODAY).days for e in got]
    assert 25 in dtes and 32 in dtes                 # the pair around 30 days
    assert len(got) <= 8 and max(dtes) <= 120 and dtes == sorted(dtes)
    assert chains.select_expiries([], TODAY) == []
    # Only a 1-day expiry below 30: the bracket skips it (min_dte), the nearest-to-7 target may still take it.
    few = [_epoch(date(2026, 11, 17)), _epoch(date(2026, 12, 18))]
    assert _epoch(date(2026, 12, 18)) in chains.select_expiries(few, TODAY)


def _c(strike, bid, ask, iv_=0.3, vol=5, oi=100, last=1.0):
    return {"strike": strike, "bid": bid, "ask": ask, "impliedVolatility": iv_, "volume": vol, "openInterest": oi,
            "lastPrice": last}


def test_filter_contracts_sanitises_and_bands():
    chain = {"calls": [_c(100, 2.0, 2.2), _c(60, 40.0, 41.0),             # 60: outside +/-30% of 100
                       _c(105, float("nan"), 1.0, iv_=float("nan"), vol=float("nan")),
                       _c(110, 0.0, 0.0),                                  # no offer: not a quote
                       _c(115, 0.6, 0.5),                                  # crossed
                       _c(120, 0.0, 0.05, iv_=0.00001)],                   # placeholder IV
             "puts": [_c(95, 1.5, 1.6, oi=None)]}
    cols = chains.filter_contracts(chain, 100.0)
    assert cols["strike"] == [100, 105, 120, 95]
    assert cols["side"] == ["C", "C", "C", "P"]
    assert cols["bid"][1] is None and cols["volume"][1] is None and cols["iv"][1] is None
    assert cols["iv"][2] is None and cols["oi"][3] is None
    assert set(cols) == set(chains.FIELDS) and all(len(v) == 4 for v in cols.values())


def test_no_bids_means_no_reading():
    pre_open = {"calls": [_c(100, 0.0, 2.2), _c(105, None, 1.0)], "puts": [_c(95, 0.0, 1.0)]}
    assert chains.filter_contracts(pre_open, 100.0) is None
    assert chains.filter_contracts({"calls": [], "puts": []}, 100.0) is None
    assert chains.filter_contracts(pre_open, 0) is None


def _priced_chain(F, T, sigma, strikes):
    calls, puts = [], []
    for k in strikes:
        c, p = iv.black76(F, k, T, 0.045, sigma, True), iv.black76(F, k, T, 0.045, sigma, False)
        calls.append(_c(k, round(c - 0.01, 4), round(c + 0.01, 4)))
        puts.append(_c(k, max(round(p - 0.01, 4), 0.01), round(p + 0.01, 4)))
    return {"calls": calls, "puts": puts}


def _fake_fetch(expiries: list[date], spot=100.0, sigma=0.3):
    calls = []

    def fetch(url, params, timeout):
        calls.append(params)
        exps = [_epoch(d) for d in expiries]
        e = int(params.get("date", exps[0]))
        T = iv.year_fraction(NOW, chains.ropt.exp_date(e))
        chain = {"expirationDate": e, **_priced_chain(spot, T, sigma, range(80, 121, 5))}
        quote = {"regularMarketPrice": spot, "earningsTimestamp": _epoch(date(2026, 12, 1)) + 72000}
        return 200, {"optionChain": {"result": [{"expirationDates": exps, "quote": quote, "options": [chain]}]}}
    return fetch, calls


def test_ticker_snapshot_rows_and_atm_iv():
    exps = [date(2026, 11, 20), date(2026, 12, 4), date(2026, 12, 18), date(2027, 1, 15), date(2027, 6, 18)]
    fetch, calls = _fake_fetch(exps)
    rows = chains.ticker_snapshot(fetch, "BRK.B", NOW, lambda: None)
    assert {r["expiry"] for r in rows} == set(exps[:4])          # 2027-06 is beyond 120 days
    assert len(calls) == 4                                       # first read reused for the nearest expiry
    r = next(r for r in rows if r["expiry"] == date(2026, 12, 18))
    assert r["ticker"] == "BRK.B" and r["snapshot_date"] == TODAY and r["dte"] == 32
    assert r["iv_method"].startswith("parity_b76") and r["atm_iv"] == pytest.approx(0.30, abs=0.01)
    assert r["forward"] == pytest.approx(100.0, abs=0.05)
    assert r["earnings_date"] == date(2026, 12, 1)


def test_collect_is_idempotent(store):
    fetch, _ = _fake_fetch([date(2026, 12, 4), date(2026, 12, 18)])
    for _ in range(2):
        res = chains.collect(store, ["AAA", "BBB"], fetch=fetch, now=NOW)
        assert res["stored"] == 2 and res["status"] == "ok"
    with store.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(QuantChainSnapshot)).scalar() == 4
    summ = store.load_chain_summaries(TODAY)
    assert set(summ) == {"AAA", "BBB"} and all(r["atm_iv"] for r in summ["AAA"])


def test_collect_reports_failures_and_empty_quotes(store):
    def broken(url, params, timeout):
        return 404, None

    res = chains.collect(store, ["ZZZ"], fetch=broken, now=NOW)
    assert res["failed"] == ["ZZZ"] and res["status"] == "error"
