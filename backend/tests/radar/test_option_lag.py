"""radar_option_history (written by radar.options.refresh) and the staleness report radar/option_lag.py."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from radar import option_lag, options
from tests.radar.test_options import Yahoo, today_of

UTC = timezone.utc
T0 = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)          # 10:00 ET


def side(spread: float | None, liq: str = "good") -> dict:
    return {"atm": {"spread_pct": spread, "strike": 100.0, "mid": 2.0}, "liquidity": liq, "ntm_oi": 1, "ntm_volume": 1}


def doc(call: float | None = 8.0, put: float | None = 8.0, *, iv: float = 40.0, ivhv: float = 1.1,
        earnings: bool = False, put_liq: str = "good") -> dict:
    return {"iv_pct": iv, "iv_hv": ivhv, "term_ratio": 1.0, "earnings_before_expiry": earnings,
            "sides": {"call": side(call), "put": side(put, put_liq)}}


def row(ticker: str, minutes: float, metrics: dict, direction: str | None = "up") -> dict:
    return {"ticker": ticker, "as_of": T0 + timedelta(minutes=minutes), "direction": direction, "metrics": metrics}


# ---------------------------------------------------------------- refresh writes the history

def test_refresh_appends_a_slim_reading_per_name_with_its_direction(radar_store):
    now = time.time()
    day = today_of(now).isoformat()
    state = {"schema": 1, "session": {"date": day},
             "members": [{"ticker": "AAA", "intensity": 90, "direction": "down"}],
             "heating": [{"ticker": "BBB", "direction": "up"}]}
    radar_store.commit_tick(state=state, engine_doc={"schema": 1}, member_rows=[], events=[],
                            scan_row={"v": 1, "tick": "2026-10-07T14:00:00Z", "run_id": "local"})
    options.refresh(radar_store, fetch=Yahoo(now), clock=lambda: now)
    options.refresh(radar_store, fetch=Yahoo(now + 300), clock=lambda: now + 300)
    hist = radar_store.load_option_history()
    assert [(h["ticker"], h["direction"]) for h in hist] == [("AAA", "down"), ("BBB", "up")] * 2
    assert hist[0]["as_of"] < hist[2]["as_of"]
    m = hist[0]["metrics"]
    assert set(m) == set(options.HISTORY_KEYS) | {"iv_risk"}
    # The fixture chain quotes calls only: the put side is None, the call side is kept.
    assert m["sides"]["call"]["atm"]["strike"] == 100.0 and m["sides"]["put"] is None
    assert m["iv_risk"] in ("normal", "elevated", "high", "crushing")
    # The latest-row table is unchanged: one row per ticker.
    assert set(radar_store.load_option_metrics()) == {"AAA", "BBB"}


def test_option_history_is_pruned_on_write_and_a_rerun_adds_nothing(radar_store):
    now = datetime.now(UTC)
    radar_store.put_option_history([("OLD", "up", {"x": 1})], now - timedelta(days=30), keep_days=21)
    radar_store.put_option_history([("NEW", None, {"x": 2})], now, keep_days=21)
    radar_store.put_option_history([("NEW", None, {"x": 3})], now, keep_days=21)      # same reading again
    hist = radar_store.load_option_history()
    assert [(h["ticker"], h["metrics"]) for h in hist] == [("NEW", {"x": 2})]
    assert radar_store.load_option_history(now + timedelta(seconds=1)) == []


def test_history_failure_never_fails_the_refresh(radar_store, monkeypatch):
    now = time.time()
    monkeypatch.setattr(radar_store, "put_option_history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    res = options.refresh(radar_store, ["QQQ"], fetch=Yahoo(now), clock=lambda: now)
    assert res["ok"] == 1 and set(radar_store.load_option_metrics()) == {"QQQ"}


# ---------------------------------------------------------------- option_lag

def test_option_class_follows_the_verdict_rules():
    def flat(spread, liq="good"):
        return option_lag.side_of({"sides": {"call": side(spread, liq)}}, "up")
    assert option_lag.option_class(flat(8.0)) == "ok"
    assert option_lag.option_class(flat(12.0)) == "caution"
    assert option_lag.option_class(flat(16.0)) == "avoid"
    assert option_lag.option_class(flat(None)) == "avoid"
    assert option_lag.option_class(flat(5.0, "thin")) == "avoid"
    assert option_lag.option_class(None) is None


def test_iv_class():
    assert option_lag.iv_class(doc(ivhv=1.1)) == "cheap"
    assert option_lag.iv_class(doc(ivhv=1.3)) == "fair"
    assert option_lag.iv_class(doc(ivhv=1.6)) == "rich"
    assert option_lag.iv_class(doc(ivhv=2.1)) == "expensive"
    assert option_lag.iv_class(doc(ivhv=1.1, earnings=True)) == "expensive"
    assert option_lag.iv_class({"iv_pct": None}) is None


def test_side_of_uses_puts_for_down_moves_and_old_rows_fall_back_to_calls():
    d = doc(call=5.0, put=20.0)
    assert option_lag.side_of(d, "down")["spread_pct"] == 20.0
    assert option_lag.side_of(d, "up")["spread_pct"] == 5.0
    legacy = {"liquidity": "fair", "atm": {"spread_pct": 9.0}}
    assert option_lag.side_of(legacy, "up")["spread_pct"] == 9.0 and option_lag.side_of(legacy, "down") is None


def test_pairs_take_the_reading_closest_to_15_minutes_later_on_the_same_day():
    rows = [row("A", 0, doc()), row("A", 5, doc()), row("A", 10, doc()), row("A", 15, doc()), row("A", 20, doc()),
            row("B", 0, doc()), row("B", 25, doc())]                     # B: nothing 12-20 minutes later
    ps = option_lag.pairs(rows)
    assert [(a["as_of"] - T0, b["as_of"] - a["as_of"]) for a, b in ps] == [
        (timedelta(0), timedelta(minutes=15)), (timedelta(minutes=5), timedelta(minutes=15))]


def test_report_counts_verdict_changes_and_splits_the_first_hour():
    rows = [
        row("A", 0, doc(call=8.0)), row("A", 15, doc(call=18.0)),                 # ok -> avoid, 10:00 ET
        row("D", 0, doc(put=20.0), "down"), row("D", 15, doc(put=9.0), "down"),   # avoid -> ok on the put side
        row("C", 60, doc(call=8.0, ivhv=1.1)), row("C", 75, doc(call=9.0, ivhv=1.6)),  # 11:00 ET, same class
    ]
    rep = option_lag.report(rows)
    s = rep["all"]
    assert s["pairs"] == 3
    assert s["option_verdict"] == {"compared": 3, "changed_pct": 66.7, "became_avoid": 1, "left_avoid": 1}
    assert s["iv_price"] == {"compared": 3, "changed_pct": 33.3}
    assert s["spread_change_pts"]["median"] == 10.0
    assert rep["before_10_30_et"]["pairs"] == 2 and rep["after_10_30_et"]["pairs"] == 1
    assert s["examples"][0]["ticker"] == "D" and s["examples"][0]["shown"].startswith("avoid")
    assert option_lag.report([])["all"]["option_verdict"]["changed_pct"] is None
    json.dumps(rep, default=str)
    assert "pairs" in option_lag._text(rep)
