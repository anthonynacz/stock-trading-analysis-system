"""EODHD IV backfill: chain parsing and per-day readings with a fake API (services/iv_backfill.py)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from services.iv_backfill import day_chains, ticker_readings

D1, D2 = date(2026, 6, 15), date(2026, 6, 16)


def _rec(day: date, exp: date, typ: str, strike: float, iv: float, bid=4.0, ask=4.2) -> dict:
    return {"id": f"X{exp:%y%m%d}{typ[0].upper()}{int(strike * 1000):08d}-{day.isoformat()}",
            "attributes": {"exp_date": exp.isoformat(), "type": typ, "strike": strike, "bid": bid, "ask": ask,
                           "volatility": iv}}


class FakeApi:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, params: dict):
        self.calls.append((path, params))
        assert path == "/mp/unicornbay/options/eod"                          # options add-on only, no EODHD prices
        day = date.fromisoformat(params["filter[tradetime_eq]"])
        e1, e2 = day + timedelta(days=21), day + timedelta(days=42)
        recs = [_rec(day, e, t, 100.0, iv) for e, iv in ((e1, 0.30), (e2, 0.30)) for t in ("call", "put")]
        # The same contract's later snapshot (it last traded on `day`): must be ignored.
        recs.append(_rec(day + timedelta(days=3), e1, "call", 100.0, 0.99))
        return {"data": recs}


def test_day_chains_keeps_only_the_snapshot_of_that_day_and_filters_near_the_money():
    api = FakeApi()
    chains = day_chains(api, "X", D1, 100.0)
    assert sorted(chains) == [(D1 + timedelta(days=21)).isoformat(), (D1 + timedelta(days=42)).isoformat()]
    assert all(r["impliedVolatility"] == 0.30 for c in chains.values() for side in c.values() for r in side)
    params = api.calls[0][1]
    assert params["filter[strike_from]"] == 93.0 and params["filter[strike_to"] if False else True
    assert params["filter[exp_date_from]"] == (D1 + timedelta(days=7)).isoformat()


CLOSES = [{"date": (D1 - timedelta(days=90 - i)).isoformat(), "close": 100.0 + (i % 3),
           "adjusted_close": 100.0 + (i % 3)} for i in range(90)] + [
    {"date": D1.isoformat(), "close": 100.0, "adjusted_close": 100.0},
    {"date": D2.isoformat(), "close": 101.0, "adjusted_close": 101.0}]


def test_ticker_readings_build_rows_and_skip_days_already_stored():
    api = FakeApi()
    rows = ticker_readings(api, "X", D1, D2, skip={D2}, closes=CLOSES, pause_s=0)
    assert [r["day"] for r in rows] == [D1]
    r = rows[0]
    assert r["iv30"] == pytest.approx(0.30) and r["source"] == "eodhd" and r["spot"] == 100.0
    assert r["hv20"] is not None and r["hv60"] is not None
    assert len(api.calls) == 1                                            # one chain request per new day
