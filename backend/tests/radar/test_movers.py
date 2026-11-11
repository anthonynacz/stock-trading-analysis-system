"""Plain percentage movers (radar/movers.py) and their ride-along option metrics (radar/options.py)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from radar import options
from radar.movers import plain_movers
from radar.types import Quote
from tests.radar.test_options import Yahoo, today_of

CFG = {"min_abs_pct": 2.0, "max_rows_per_side": 3}


@dataclass
class Base:
    prev_close: float | None = 100.0
    eligible: bool = True
    name: str | None = "Name"
    sector: str | None = "Technology"
    adv20_usd: float = 50e6
    cvm: np.ndarray = field(default_factory=lambda: np.arange(1, 79) * 1000.0)


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
                  "rvol": 2.0, "adv_usd": 50_000_000, "market_cap": None}
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
