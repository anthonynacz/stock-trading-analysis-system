"""Plain percentage movers (radar/movers.py) and their ride-along option metrics (radar/options.py)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from radar import options
from radar.movers import history_stats, plain_movers, setup_of
from radar.types import Quote
from tests.radar.test_options import Yahoo, today_of

CFG = {"min_abs_pct": 2.0, "max_rows_per_side": 3, "windows": [3, 5, 10, 20], "window_min_abs_pct": 5.0,
       "window_rows_per_side": 2, "dip_chg_5d_max": -8.0, "stretched_chg_5d_min": 15.0, "setup_rows": 1}
NO_HISTORY = {"chg_3d": None, "chg_5d": None, "chg_10d": None, "chg_20d": None, "vs_sma20": None, "vs_sma50": None,
              "off_high_20d": None, "setup": None}


@dataclass
class Base:
    prev_close: float | None = 100.0
    eligible: bool = True
    name: str | None = "Name"
    sector: str | None = "Technology"
    adv20_usd: float = 50e6
    cvm: np.ndarray = field(default_factory=lambda: np.arange(1, 79) * 1000.0)
    closes: list = field(default_factory=list)


@dataclass
class Pack:
    symbols: dict


def q(sym: str, price: float, vol: int | None = 20_000, **kw) -> Quote:
    return Quote(sym, price=price, day_volume=vol, **kw)


def test_movers_are_plain_percent_changes_largest_first():
    pack = Pack({"UP": Base(), "DOWN": Base(), "FLAT": Base(), "SPY": Base(), "PENNY": Base(eligible=False),
                 "NOPC": Base(prev_close=None), "BIG": Base(sector=None, name=None)})
    quotes = {"UP": q("UP", 105.0), "DOWN": q("DOWN", 92.0, market_cap=3e9), "FLAT": q("FLAT", 101.0),
              "SPY": q("SPY", 110.0), "PENNY": q("PENNY", 130.0), "NOPC": q("NOPC", 50.0),
              "BIG": q("BIG", 120.0, vol=None, name="Big Co", sector="Energy")}
    rows = plain_movers(quotes, pack, 9, CFG)
    assert [r["ticker"] for r in rows] == ["BIG", "DOWN", "UP"]          # |20%|, |8%|, |5%|; FLAT under 2%
    up = rows[2]
    assert up == {"ticker": "UP", "name": "Name", "sector": "Technology", "direction": "up", "price": 105.0,
                  "prev_close": 100.0, "change_pct": 5.0, "day_volume": 20_000, "dollar_volume": 2_100_000,
                  "rvol": 2.0, "adv_usd": 50_000_000, "market_cap": None, **NO_HISTORY}
    assert rows[1]["direction"] == "down" and rows[1]["change_pct"] == -8.0 and rows[1]["market_cap"] == 3_000_000_000
    big = rows[0]
    assert (big["name"], big["sector"]) == ("Big Co", "Energy")           # the quote's, when the pack has none
    assert big["day_volume"] is None and big["dollar_volume"] is None and big["rvol"] is None


def test_movers_cap_each_side_use_the_quote_close_as_fallback_and_skip_rvol_before_the_first_bar():
    pack = Pack({f"S{i}": Base() for i in range(6)} | {"Q": Base(prev_close=None), "DN": Base()})
    quotes = {f"S{i}": q(f"S{i}", 103.0 + i) for i in range(6)} | {"Q": q("Q", 50.0, prev_close=40.0),
                                                                    "DN": q("DN", 97.5)}
    rows = plain_movers(quotes, pack, -1, CFG)
    # Three gainers at most; the one loser stays although six gainers moved more.
    assert [r["ticker"] for r in rows] == ["Q", "S5", "S4", "DN"] and rows[0]["change_pct"] == 25.0
    assert all(r["rvol"] is None for r in rows)
    assert plain_movers({}, pack, 5, CFG) == []


# ---------------------------------------------------------------- multi-day moves and setups

def test_history_stats_count_sessions_back_with_today_as_the_last_one():
    closes = [100.0] * 40 + [110.0, 120.0, 130.0, 125.0, 120.0]      # 45 closes, yesterday 120
    st = history_stats(108.0, closes, CFG)
    assert st["chg_3d"] == -16.92          # 108 against the close 3 sessions back (130)
    assert st["chg_5d"] == -1.82           # against 110
    assert st["chg_10d"] == 8.0 and st["chg_20d"] == 8.0
    assert st["vs_sma20"] == round((108 / ((100 * 14 + 110 + 120 + 130 + 125 + 120 + 108) / 20) - 1) * 100, 2)
    assert st["vs_sma50"] is None          # 45 closes: one short of 49 + today
    assert st["off_high_20d"] == round((108 / 130 - 1) * 100, 2)
    assert history_stats(131.0, closes, CFG)["off_high_20d"] == 0.0      # today is the 20-day high
    short = history_stats(108.0, closes[-4:], CFG)
    assert short["chg_3d"] == -16.92 and short["chg_5d"] is None and short["vs_sma20"] is None
    assert history_stats(108.0, [], CFG) == {k: v for k, v in NO_HISTORY.items() if k != "setup"}
    assert history_stats(108.0, [100.0, float("nan"), 0.0], CFG)["chg_3d"] is None


def test_setups_are_named_from_the_5_day_move_and_the_50_day_average():
    assert setup_of({"chg_5d": -9.0, "vs_sma50": 4.0}, CFG) == "dip_uptrend"
    assert setup_of({"chg_5d": -9.0, "vs_sma50": -0.5}, CFG) == "falling"
    assert setup_of({"chg_5d": -9.0, "vs_sma50": None}, CFG) is None       # no 50-day average: no call
    assert setup_of({"chg_5d": -7.9, "vs_sma50": 4.0}, CFG) is None
    assert setup_of({"chg_5d": 15.0, "vs_sma50": None}, CFG) == "stretched"
    assert setup_of({"chg_5d": None, "vs_sma50": 4.0}, CFG) is None


def test_multi_day_movers_and_dips_are_listed_even_when_flat_today():
    up_run = [100.0] * 55 + [101.0, 102.0, 103.0, 104.0, 105.0]            # yesterday 105
    dip = [50.0] * 30 + [80.0] * 25 + [100.0, 104.0, 108.0, 112.0, 110.0]   # ran to 112, 50-day average ~70
    pack = Pack({"FLAT": Base(closes=[100.0] * 60), "WEEK": Base(prev_close=105.0, closes=up_run),
                 "DIP": Base(prev_close=110.0, closes=dip), "OLD": Base(prev_close=None, closes=up_run),
                 "DAY": Base(closes=[100.0] * 60)})
    quotes = {"FLAT": q("FLAT", 100.5), "WEEK": q("WEEK", 106.0), "DIP": q("DIP", 91.0),
              "OLD": q("OLD", 106.0, prev_close=105.0), "DAY": q("DAY", 103.0)}
    rows = {r["ticker"]: r for r in plain_movers(quotes, pack, 9, CFG)}
    # FLAT moved nowhere; OLD has no pack close, so its closes are not aligned and it has no multi-day move.
    assert set(rows) == {"DIP", "DAY", "WEEK"}
    assert rows["DAY"]["change_pct"] == 3.0 and rows["DAY"]["chg_5d"] == 3.0 and rows["DAY"]["setup"] is None
    week = rows["WEEK"]                                                    # +0.95% today, +6% over 10 sessions
    assert week["change_pct"] == 0.95 and week["chg_5d"] == 4.95 and week["chg_10d"] == 6.0
    d = rows["DIP"]
    assert d["chg_5d"] == -9.0 and d["vs_sma50"] > 0 and d["setup"] == "dip_uptrend" and d["direction"] == "down"
    assert d["off_high_20d"] == round((91 / 112 - 1) * 100, 2)
    assert list(rows) == ["DIP", "DAY", "WEEK"]                            # largest move today first


# ---------------------------------------------------------------- options ride-along

def test_mover_tickers_take_unread_names_first_then_the_oldest_reading():
    state = {"movers": [{"ticker": t} for t in ("A", "B", "C", "D", "E")] + [{"ticker": "A"}, "junk"]}
    rows = {"A": {"as_of": "2026-11-11T15:00:00Z"}, "B": {"as_of": "2026-11-11T14:00:00Z"}, "E": {}}
    assert options.mover_tickers(state, ["D"], rows, 3) == ["C", "E", "B"]
    assert options.mover_tickers(state, [], rows, 10) == ["C", "D", "E", "B", "A"]
    assert options.mover_tickers({}, [], {}, 5) == [] and options.mover_tickers(state, [], rows, 0) == []


def test_context_is_carried_while_fresh():
    now = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc).timestamp()
    assert options.context_fresh({"context_at": "2026-11-11T14:40:00Z"}, now, 30)
    assert not options.context_fresh({"context_at": "2026-11-11T14:20:00Z"}, now, 30)
    assert not options.context_fresh({"context_at": "garbage"}, now, 30)
    assert not options.context_fresh({}, now, 30) and not options.context_fresh(None, now, 30)
    doc = {"iv_pct": 40.0, "earnings_date": "yahoo"}
    options.carry_context(doc, {"catalysts": [1], "earnings_date": "2026-11-20", "context_at": "x", "iv_pct": 1.0})
    assert doc == {"iv_pct": 40.0, "earnings_date": "2026-11-20", "catalysts": [1], "context_at": "x"}


def test_refresh_adds_movers_after_the_radar_names_and_keeps_them_out_of_the_history(radar_store):
    now = time.time()
    day = today_of(now).isoformat()
    state = {"schema": 1, "session": {"date": day},
             "members": [{"ticker": "AAA", "intensity": 90, "direction": "up"}],
             "movers": [{"ticker": t, "direction": "up", "change_pct": 9.0 - i} for i, t in enumerate(("AAA", "MV1", "MV2", "MV3"))]}
    radar_store.commit_tick(state=state, engine_doc={"schema": 1}, member_rows=[], events=[],
                            scan_row={"v": 1, "tick": "2026-10-07T14:00:00Z", "run_id": "local"})
    limit = {"options_per_refresh": 2, "context_ttl_min": 30}
    res = options.refresh(radar_store, fetch=Yahoo(now), clock=lambda: now, movers=limit)
    assert res["tickers"] == 3 and set(radar_store.load_option_metrics()) == {"AAA", "MV1", "MV2"}
    res = options.refresh(radar_store, fetch=Yahoo(now + 300), clock=lambda: now + 300, movers=limit)
    assert res["tickers"] == 3 and set(radar_store.load_option_metrics()) == {"AAA", "MV1", "MV2", "MV3"}
    assert {h["ticker"] for h in radar_store.load_option_history()} == {"AAA"}
    # Explicit tickers (the CLI) never pull the movers in.
    assert options.refresh(radar_store, ["QQQ"], fetch=Yahoo(now), clock=lambda: now)["tickers"] == 1
