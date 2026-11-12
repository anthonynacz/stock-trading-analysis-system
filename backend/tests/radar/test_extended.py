"""Extended-hours movers (radar/extended.py): the windows, the rows, the scan, its storage and the worker's
schedule."""
from __future__ import annotations

from datetime import datetime, timezone

from radar import extended as X
from radar.config import EXTENDED
from tests.radar.test_worker import Options, at, iso, make, run_until

UTC = timezone.utc
CLOSE = int(datetime(2026, 11, 11, 21, 0, tzinfo=UTC).timestamp())           # Wed 2026-11-11 16:00 ET


def ts(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


CFG = {**EXTENDED, "max_rows_per_side": 2, "volume_rows_per_side": 1}
WIN = {"phase": "post", "session": "2026-11-11", "ref_day": "2026-11-11", "since": CLOSE}
META = {s: {"name": f"{s} Inc", "sector": "Health Care", "market_cap": 5e9} for s in ("UP", "DN", "BIG", "MID", "OLD", "PENNY", "WILD")}


def quote(sym: str, price: float, stamp: int = CLOSE, **kw) -> dict:
    return {"symbol": sym, "price": price, "previousClose": price * 0.5, "timestamp": stamp, "avgVolume": 1_000_000,
            "marketCap": 8e9, "name": f"{sym} quote name", **kw}


def trade(sym: str, price: float, after_close_s: int = 1800) -> dict:
    return {"symbol": sym, "price": price, "size": 100, "timestamp": (CLOSE + after_close_s) * 1000}


# ---------------------------------------------------------------- windows

def test_windows_pre_post_and_closed():
    assert X.window(ts("2026-11-11T21:30:00Z")) == WIN                                    # 16:30 ET
    pre = X.window(ts("2026-11-12T12:00:00Z"))                                            # Thu 07:00 ET
    assert pre == {"phase": "pre", "session": "2026-11-12", "ref_day": "2026-11-11", "since": CLOSE}
    mon = X.window(ts("2026-11-16T10:00:00Z"))                                            # Mon 05:00 ET
    assert mon["phase"] == "pre" and mon["ref_day"] == "2026-11-13"                       # Friday's close
    for closed in ("2026-11-11T18:00:00Z",         # regular session
                   "2026-11-12T03:00:00Z",         # night
                   "2026-11-14T15:00:00Z"):        # Saturday
        assert X.window(ts(closed)) is None


def test_reference_close_is_the_quote_price_unless_the_quote_follows_extended_trades():
    assert X.reference_close(quote("A", 100.0), CLOSE) == 100.0
    assert X.reference_close(quote("A", 100.0, CLOSE + 600), CLOSE) == 100.0              # the closing auction print
    assert X.reference_close(quote("A", 100.0, CLOSE + 3600, previousClose=90.0), CLOSE) == 90.0
    assert X.reference_close({"symbol": "A", "price": None, "timestamp": CLOSE}, CLOSE) is None


# ---------------------------------------------------------------- rows

def test_rows_are_moves_of_the_last_extended_trade_against_the_close():
    now = CLOSE + 3600
    quotes = [quote("UP", 100.0), quote("DN", 50.0), quote("BIG", 20.0), quote("MID", 10.0), quote("OLD", 100.0),
              quote("PENNY", 3.0), quote("WILD", 10.0), quote("SPY", 600.0), quote("NOMETA", 10.0)]
    trades = [trade("UP", 104.0), trade("DN", 46.0), trade("BIG", 26.0), trade("MID", 10.3),
              {"symbol": "OLD", "price": 120.0, "timestamp": (CLOSE - 60) * 1000},        # a regular-session trade
              trade("PENNY", 3.9), trade("WILD", 25.0), trade("SPY", 660.0), trade("NOMETA", 12.0),
              {"symbol": "UP", "price": None, "timestamp": 1}, "junk"]
    rows, traded = X.mover_rows(quotes, [t for t in trades if isinstance(t, dict)], META, WIN, now, CFG)
    # BIG +30%, MID +3%, UP +4% -> the two largest gainers; DN -8%. OLD did not trade after the close, PENNY is
    # under $5, WILD's +150% is a bad print, SPY is a reference ETF and NOMETA is not a scanned stock.
    assert [r["ticker"] for r in rows] == ["BIG", "DN", "UP"] and traded == 8
    up = rows[2]
    assert up == {"ticker": "UP", "name": "UP Inc", "sector": "Health Care", "direction": "up", "price": 104.0,
                  "prev_close": 100.0, "change_pct": 4.0, "traded_at": "2026-11-11T21:30:00Z", "day_volume": None,
                  "dollar_volume": None, "rvol": None, "adv_usd": 100_000_000, "market_cap": 8_000_000_000}
    assert rows[1]["direction"] == "down" and rows[1]["change_pct"] == -8.0


def test_window_volume_sums_the_bars_after_the_close():
    bars = [{"date": "2026-11-11 15:59:00", "close": 10.0, "volume": 5000},               # regular session
            {"date": "2026-11-11 16:00:00", "close": 10.0, "volume": 100},
            {"date": "2026-11-11 17:30:00", "close": 11.0, "volume": 200},
            {"date": "bad", "close": 11.0, "volume": 1}, {"date": "2026-11-11 18:00:00", "close": 11.0, "volume": None}]
    assert X.window_volume(bars, CLOSE) == (300, 3200)
    assert X.window_volume(None, CLOSE) is None and X.window_volume([], CLOSE) == (0, 0)


def test_option_tickers_take_unread_then_oldest_and_skip_fresh_readings():
    now = CLOSE + 7200
    doc = {"movers": [{"ticker": t} for t in ("A", "B", "C", "D")]}
    rows = {"A": {"as_of": X.iso(now - 600)},            # read 10 minutes ago: fresh
            "B": {"as_of": X.iso(now - 4 * 3600)}, "D": {"as_of": X.iso(now - 2 * 3600)}}
    assert X.option_tickers(doc, rows, now, {**EXTENDED, "options_per_scan": 5}) == ["C", "B", "D"]
    assert X.option_tickers(doc, rows, now, {**EXTENDED, "options_per_scan": 1}) == ["C"]
    assert X.option_tickers({}, {}, now) == []


# ---------------------------------------------------------------- the scan

class Fmp:
    def __init__(self, fail: tuple[str, ...] = ()):
        self.fail, self.calls = fail, []

    def __call__(self, path: str, params: dict):
        kind = path.split("/")[3]
        self.calls.append((kind, path, dict(params)))
        if kind in self.fail:
            return None
        if kind == "stock-screener":
            return [{"symbol": "NEWCO", "companyName": "Newco Corp", "sector": "Technology", "marketCap": 9e9},
                    {"symbol": "BRK-B", "companyName": "Class shares are skipped", "sector": "Financial Services"}]
        syms = path.rsplit("/", 1)[1].split(",")
        if kind == "quote":
            return [quote(s, 100.0) for s in syms]
        if kind == "batch-pre-post-market-trade":
            px = {"NEWCO": 112.0, "AAPL": 97.0}
            return [trade(s, px.get(s, 100.2)) for s in syms]
        if kind == "historical-chart":
            return [{"date": "2026-11-11 16:30:00", "close": 110.0, "volume": 1000}]
        raise AssertionError(path)


def test_scan_lists_universe_and_screener_names_with_their_window_volume():
    now = CLOSE + 3600
    fmp = Fmp()
    doc = X.scan(now, fmp, CFG)
    assert doc["status"] == "ok" and doc["phase"] == "post" and doc["session"] == "2026-11-11"
    assert doc["as_of"] == "2026-11-11T22:00:00Z" and doc["since"] == "2026-11-11T21:00:00Z"
    assert [(r["ticker"], r["change_pct"]) for r in doc["movers"]] == [("NEWCO", 12.0), ("AAPL", -3.0)]
    newco, aapl = doc["movers"]
    assert newco["name"] == "Newco Corp" and newco["sector"] == "Information Technology"          # mapped to GICS
    assert (newco["day_volume"], newco["dollar_volume"]) == (1000, 110_000)
    assert aapl["sector"] == "Information Technology" and aapl["dollar_volume"] == 110_000
    assert doc["counts"]["scanned"] > 400 and doc["counts"]["with_volume"] == 2
    kinds = [c[0] for c in fmp.calls]
    assert kinds.count("stock-screener") == 1 and kinds.count("historical-chart") == 2
    assert kinds.count("quote") == kinds.count("batch-pre-post-market-trade") >= 1
    bars = next(c for c in fmp.calls if c[0] == "historical-chart")
    assert bars[2] == {"from": "2026-11-11", "to": "2026-11-11", "extended": "true"}
    assert all("apikey" not in c[2] for c in fmp.calls)                                    # the key is a header


def test_scan_outside_the_windows_is_none_and_failures_are_reported():
    assert X.scan(ts("2026-11-11T18:00:00Z"), Fmp()) is None
    now = CLOSE + 3600
    down = X.scan(now, Fmp(fail=("quote", "batch-pre-post-market-trade")), CFG)
    assert down["status"] == "error" and down["movers"] == []
    # Without the screener the universe is still scanned; without bars the rows have no volume.
    partial = X.scan(now, Fmp(fail=("stock-screener", "historical-chart")), CFG)
    assert partial["status"] == "ok" and [r["ticker"] for r in partial["movers"]] == ["AAPL"]
    assert partial["movers"][0]["dollar_volume"] is None and partial["counts"]["with_volume"] == 0


# ---------------------------------------------------------------- storage and schedule

def test_store_keeps_the_latest_scan(radar_store):
    assert radar_store.get_extended() is None
    radar_store.put_extended({"schema": 1, "phase": "post", "movers": [{"ticker": "A"}]})
    radar_store.put_settings({"levels": {}})                       # another runtime column: the scan survives
    assert radar_store.get_extended() == {"schema": 1, "phase": "post", "movers": [{"ticker": "A"}]}


def test_worker_scans_every_ten_minutes_after_hours_and_never_in_the_session(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T19:52:00Z")     # Mon 15:52 ET
    w.scan_extended = Options(clock)
    run_until(w, clock, "2026-09-28T20:35:00Z")                    # 16:35 ET
    calls = [at(c) for c in w.scan_extended.calls]
    assert calls and all(c >= at("2026-09-28T20:00:00Z") for c in calls)                    # none before the close
    assert len(calls) == 4 and all(b - a >= EXTENDED["interval_s"] for a, b in zip(calls, calls[1:]))   # 16:01 .. 16:31
    assert fake.ids[-1] == "2026-09-28T20:00:00Z"                  # the session's ticks ran as planned


def test_worker_leaves_room_for_the_next_job_and_survives_a_failing_scan(radar_store):
    # 09:08 ET: the warmup is due at 09:10, so no scan starts; after it, scans resume until the first tick nears.
    w, clock, fake = make(radar_store, "2026-09-28T13:08:00Z")
    w.scan_extended = Options(clock, fail=True)
    run_until(w, clock, "2026-09-28T13:36:30Z")
    calls = [iso(at(c)) for c in w.scan_extended.calls]
    assert calls and calls[0] >= "2026-09-28T13:10:00Z" and calls[-1] < "2026-09-28T13:30:00Z"
    assert "2026-09-28T13:35:00Z" in fake.ids                      # the first tick of the session still ran
