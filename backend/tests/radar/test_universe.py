"""radar.universe and radar.tools.refresh_universe (no network)."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest

from radar.config import PARAMS, REFERENCE_SYMBOLS
from radar.tools import refresh_universe as ru
from radar.types import Quote
from radar.universe import COLUMNS, UNIVERSE_CSV, dynamic_candidates, gics_sector, load_universe, scan_symbols

FIXTURES = Path(__file__).parent / "fixtures"
GICS = {"Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
        "Health Care", "Industrials", "Information Technology", "Materials", "Real Estate", "Utilities"}


# ---------------------------------------------------------------------- universe.csv
def test_universe_csv_shape():
    with UNIVERSE_CSV.open(encoding="utf-8", newline="") as f:
        assert tuple(next(csv.reader(f))) == COLUMNS
    rows = load_universe()
    symbols = [r["symbol"] for r in rows]
    assert len(rows) == 542 and len(set(symbols)) == 542 and symbols == sorted(symbols)
    assert sum(r["in_sp500"] for r in rows) == 503 and sum(r["in_ndx"] for r in rows) == 101
    assert sum(r["is_etf"] for r in rows) == 24
    assert not any(c in s for s in symbols for c in "./ ") and {"BRK-B", "BF-B"} <= set(symbols)
    assert set(REFERENCE_SYMBOLS) <= {r["symbol"] for r in rows if r["is_etf"]}


def test_every_row_has_a_name_and_a_gics_sector():
    for r in load_universe():
        assert r["name"], r["symbol"]
        assert (r["sector"] == "ETF") if r["is_etf"] else (r["sector"] in GICS), r


def test_load_universe_types_and_blank_cells(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("symbol,name,sector,industry,in_sp500,in_ndx,is_etf\nABC, ,Energy,,True,False,False\n",
                 encoding="utf-8")
    assert load_universe(p) == [{"symbol": "ABC", "name": None, "sector": "Energy", "industry": None,
                                 "in_sp500": True, "in_ndx": False, "is_etf": False}]


def test_scan_symbols_are_stocks_plus_reference_etfs():
    symbols = scan_symbols()
    etfs = {r["symbol"] for r in load_universe() if r["is_etf"]}
    assert len(symbols) == 518 + len(REFERENCE_SYMBOLS) and len(set(symbols)) == len(symbols)
    assert symbols[-len(REFERENCE_SYMBOLS):] == list(REFERENCE_SYMBOLS)
    assert not (set(symbols) & etfs) - set(REFERENCE_SYMBOLS)


def test_gics_sector_names():
    assert gics_sector("Technology") == "Information Technology"
    assert gics_sector("Financial Services") == gics_sector("Finance") == "Financials"
    assert gics_sector("Consumer Cyclical") == "Consumer Discretionary"
    assert gics_sector("Health Care") == "Health Care" and gics_sector("") is None and gics_sector(None) is None


# ---------------------------------------------------------------------- dynamic candidates
def q(symbol, *, price=20.0, change=4.0, cap=5e9, exchange="NMS", kind="EQUITY") -> Quote:
    return Quote(symbol=symbol, price=price, change_pct=change, market_cap=cap, exchange=exchange, quote_type=kind)


def test_dynamic_candidates_filters_and_order():
    movers = [
        q("OK1", change=3.5),
        q("OK2", change=-9.0),
        q("EDGE", price=5.0, change=-3.0, cap=2e9),        # every threshold is inclusive
        q("KNOWN", change=8.0),
        q("OTC", exchange="PNK"),
        q("NOEXCH", exchange=None),
        q("FUND", kind="ETF", exchange="PCX"),
        q("SMALL", cap=1.99e9),
        q("CHEAP", price=4.99),
        q("CALM", change=2.99),
        q("NOCAP", cap=None),
        Quote(symbol="NOPRICE", change_pct=5.0, market_cap=5e9, exchange="NYQ", quote_type="EQUITY"),
        q("OK1", change=6.0),                               # duplicate from another screen: first one wins
    ]
    picked = dynamic_candidates(movers, {"KNOWN"}, PARAMS)
    assert [p.symbol for p in picked] == ["OK2", "OK1", "EDGE"]
    assert picked[1].change_pct == 3.5


def test_dynamic_candidates_respect_the_enabled_switch():
    params = copy.deepcopy(PARAMS)
    params["dynamic"]["enabled"] = False
    assert dynamic_candidates([q("OK1")], set(), params) == []


# ---------------------------------------------------------------------- refresh tool
def _inputs():
    sp500 = ru.parse_sp500((FIXTURES / "sp500_constituents.csv").read_text(encoding="utf-8"))
    ndx = ru.parse_ndx(json.loads((FIXTURES / "nasdaq_ndx_list.json").read_text(encoding="utf-8")))
    profiles = {"ALAB": {"symbol": "ALAB", "longName": "Astera Labs, Inc.", "sector": "Technology",
                         "industry": "Semiconductors"},
                "ASML": {"symbol": "ASML", "shortName": "ASML Holding", "sector": "Technology",
                         "industry": "Semiconductor Equipment & Materials"}}
    return sp500, ndx, profiles


def test_refresh_parses_sources_into_yahoo_symbols():
    sp500, ndx, _ = _inputs()
    assert [r["symbol"] for r in sp500] == ["MMM", "AAPL", "BRK-B", "BF-B", "MSFT"]
    assert sp500[1] == {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Information Technology",
                        "industry": "Technology Hardware, Storage & Peripherals"}
    assert [r["symbol"] for r in ndx] == ["AAPL", "MSFT", "ASML", "PDD", "ALAB"]


def test_refresh_merges_lists_flags_and_etfs():
    rows = {r["symbol"]: r for r in ru.build_rows(*_inputs())}
    assert rows["AAPL"]["in_sp500"] and rows["AAPL"]["in_ndx"] and not rows["MMM"]["in_ndx"]
    assert rows["ALAB"] == {"symbol": "ALAB", "name": "Astera Labs, Inc.", "sector": "Information Technology",
                            "industry": "Semiconductors", "in_sp500": False, "in_ndx": True, "is_etf": False}
    assert rows["ASML"]["name"] == "ASML Holding"
    assert rows["PDD"]["sector"] is None and rows["PDD"]["name"] == "PDD Holdings Inc. American Depositary Shares"
    assert rows["SPY"] == {"symbol": "SPY", "name": ru.ETFS["SPY"], "sector": "ETF", "industry": "ETF",
                           "in_sp500": False, "in_ndx": False, "is_etf": True}
    assert len(rows) == 5 + 3 + len(ru.ETFS)


def test_refresh_refuses_truncated_lists():
    with pytest.raises(ValueError, match="truncated"):
        ru.check_counts(ru.build_rows(*_inputs()))
    ru.check_counts(load_universe())


def test_refresh_diff_and_csv_round_trip(tmp_path):
    rows = ru.build_rows(*_inputs())
    out = tmp_path / "universe.csv"
    ru.write_csv(rows, out)
    assert out.read_bytes().count(b"\r") == 0
    reloaded = load_universe(out)
    assert reloaded == rows and ru.diff(reloaded, rows) == []
    changed = copy.deepcopy(rows[1:])
    changed[0]["sector"] = "Energy"
    lines = ru.diff(rows, changed)
    assert lines[0].startswith("- " + rows[0]["symbol"])
    assert lines[1] == f"~ {rows[1]['symbol']} sector: {rows[1]['sector']!r} -> 'Energy'"


def test_checked_in_csv_matches_its_writer(tmp_path):
    out = tmp_path / "universe.csv"
    ru.write_csv(load_universe(), out)
    assert out.read_bytes() == UNIVERSE_CSV.read_bytes().replace(b"\r\n", b"\n")
