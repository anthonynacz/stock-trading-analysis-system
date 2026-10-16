"""radar.options: Black-Scholes greeks and the per-side (calls / puts) metrics used for the entry verdict."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from radar import options

UTC = timezone.utc
NOW = datetime(2026, 10, 7, 15, 0, tzinfo=UTC).timestamp()        # Wednesday 11:00 ET


def opt(strike: float, kind: str = "C", *, bid: float = 1.0, ask: float = 1.1, volume: int = 100, oi: int = 1000,
        iv: float = 0.5) -> dict:
    return {"strike": strike, "bid": bid, "ask": ask, "volume": volume, "openInterest": oi,
            "impliedVolatility": iv, "contractSymbol": f"XYZ{int(strike * 1000):08d}{kind}"}


# ---------------------------------------------------------------- greeks

def test_greeks_of_an_at_the_money_pair():
    c = options.greeks(100.0, 100.0, 40.0, 8, "call")
    p = options.greeks(100.0, 100.0, 40.0, 8, "put")
    assert 0.5 < c["delta"] < 0.55 and -0.5 < p["delta"] < -0.45
    assert c["delta"] - p["delta"] == pytest.approx(1.0, abs=0.002)        # put-call parity of deltas
    # Long options lose value every day; the ATM call decays a little faster than the put (rate > 0).
    assert c["theta_day_usd"] < p["theta_day_usd"] < 0
    # Rule of thumb: ATM theta ~ S * sigma / (2 * sqrt(2 * pi * T)) per share and year; / 365 per day, x 100 shares.
    t = 8 / 365
    approx = 100.0 * 0.40 / (2 * math.sqrt(2 * math.pi * t)) / 365 * 100.0       # about $14.8
    assert abs(c["theta_day_usd"]) == pytest.approx(approx, rel=0.15)


def test_greeks_decay_speeds_up_near_expiry():
    far = options.greeks(100.0, 100.0, 40.0, 30, "call")["theta_day_usd"]
    near = options.greeks(100.0, 100.0, 40.0, 3, "call")["theta_day_usd"]
    assert near < far < 0 and abs(near) > 2.5 * abs(far)


@pytest.mark.parametrize("args", [
    (None, 100.0, 40.0, 8), (100.0, None, 40.0, 8), (100.0, 100.0, None, 8), (100.0, 100.0, 0.0, 8), (100.0, 100.0, 40.0, None),
])
def test_greeks_without_inputs_are_none(args):
    assert options.greeks(*args, "call") == {"delta": None, "theta_day_usd": None}


def test_greeks_on_expiry_day_do_not_divide_by_zero():
    g = options.greeks(100.0, 100.0, 40.0, 0, "put")
    assert g["delta"] is not None and g["theta_day_usd"] < 0


# ---------------------------------------------------------------- side_metrics

def puts_chain() -> list[dict]:
    return [opt(85, "P", oi=9000), opt(95, "P", volume=200, oi=1000),
            opt(100, "P", bid=2.4, ask=2.6, volume=300, oi=1500, iv=0.5), opt(105, "P", volume=100, oi=800),
            opt(120, "P", oi=5000)]


def test_side_metrics_of_the_puts():
    s = options.side_metrics(puts_chain(), 100.0, 9, "put")
    a = s["atm"]
    assert a["strike"] == 100.0 and a["bid"] == 2.4 and a["ask"] == 2.6 and a["mid"] == 2.5
    assert a["spread_pct"] == 8.0 and a["iv_pct"] == 50.0 and a["contract"] == "XYZ00100000P"
    assert a["breakeven_pct"] == -2.6                              # (strike - ask) / spot - 1: the stock must fall
    assert -0.5 < a["delta"] < -0.4 and a["theta_day_usd"] < 0
    assert a["theta_pct"] == round(abs(a["theta_day_usd"]) / 250.0 * 100.0, 1)
    # Spread $0.20 over |delta| x $100: the stock move that only pays the spread.
    assert a["spread_move_pct"] == round(0.2 / (abs(a["delta"]) * 100.0) * 100.0, 2)
    assert s["ntm_oi"] == 1000 + 1500 + 800 and s["ntm_volume"] == 200 + 300 + 100
    assert s["liquidity"] == "good"
    json.dumps(s, allow_nan=False)


def test_side_metrics_tie_breaks_calls_up_and_puts_down():
    rows = [opt(100), opt(105)]
    assert options.side_metrics(rows, 102.5, 9, "call")["atm"]["strike"] == 105.0
    assert options.side_metrics(rows, 102.5, 9, "put")["atm"]["strike"] == 100.0


def test_side_metrics_without_quotes_or_rows():
    assert options.side_metrics([], 100.0, 9, "put") is None
    assert options.side_metrics([opt(100)], 0.0, 9, "put") is None
    s = options.side_metrics([opt(100, "P", bid=0.0, ask=0.0, oi=0, volume=0, iv=0.0)], 100.0, 9, "put")
    a = s["atm"]
    assert a["mid"] is None and a["iv_pct"] is None and a["delta"] is None
    assert a["theta_pct"] is None and a["spread_move_pct"] is None and a["breakeven_pct"] is None
    assert s["liquidity"] == "thin"


def test_chain_metrics_carries_both_sides():
    d = options.et_date(NOW) + timedelta(days=9)
    exp = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
    calls = [opt(95, oi=1000), opt(100, bid=2.0, ask=2.2, volume=600, oi=1500), opt(105, oi=800)]
    res = {"expirationDates": [exp], "quote": {"regularMarketPrice": 100.0}, "options": []}
    doc = options.chain_metrics(res, {"expirationDate": exp, "calls": calls, "puts": puts_chain()}, NOW)
    assert doc["sides"]["call"]["atm"]["ask"] == 2.2 and doc["sides"]["put"]["atm"]["ask"] == 2.6
    # The legacy call fields stay as they were.
    assert doc["atm"]["ask"] == 2.2 and doc["ntm_call_oi"] == doc["sides"]["call"]["ntm_oi"] == 3300
    empty = options.chain_metrics({"expirationDates": [], "quote": {"regularMarketPrice": 100.0}}, None, NOW)
    assert empty["sides"] == {"call": None, "put": None}
