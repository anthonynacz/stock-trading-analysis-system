"""Daily bars (quant/bars.py): parsing, the final-bar cutoff, backfill / extend / restatement, key masking."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from quant import bars


def _fmp_body(days: list[date], close=lambda i: 100.0 + i, factor=1.0):
    return {"symbol": "X", "historical": [
        {"date": d.isoformat(), "open": close(i), "high": close(i) + 1, "low": close(i) - 1, "close": close(i),
         "adjClose": close(i) * factor, "volume": 1000} for i, d in reversed(list(enumerate(days)))]}


def _weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_parse_fmp_sorts_filters_and_sanitises():
    body = {"historical": [
        {"date": "2026-11-13", "open": 10, "high": 11, "low": 9, "close": 10.5, "adjClose": 10.4, "volume": 5},
        {"date": "2026-11-12", "open": "nan", "high": 11, "low": 9, "close": 10.0},
        {"date": "2026-11-15", "open": 10, "high": 11, "low": 9, "close": 10.5},          # Sunday
        {"date": "2026-11-11", "open": 10, "high": 11, "low": 9, "close": 10.0, "adjClose": None, "volume": None},
        {"date": "2026-11-16", "open": 10, "high": 11, "low": 9, "close": 10.0}]}         # beyond `last`
    rows = bars.parse_fmp("X", body, date(2026, 11, 1), date(2026, 11, 13))
    assert [r["bar_date"] for r in rows] == [date(2026, 11, 11), date(2026, 11, 13)]
    assert rows[0]["adj_close"] is None and rows[0]["volume"] is None and rows[1]["source"] == "fmp"
    assert bars.parse_fmp("X", None, date(2026, 1, 1), date(2026, 2, 1)) == []


def test_parse_yahoo_uses_et_session_dates():
    ts = [int(datetime(2026, 11, 13, 14, 30, tzinfo=timezone.utc).timestamp())]       # 09:30 ET
    body = {"chart": {"result": [{"timestamp": ts, "indicators": {
        "quote": [{"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10]}],
        "adjclose": [{"adjclose": [1.4]}]}}]}}
    rows = bars.parse_yahoo("X", body, date(2026, 11, 1), date(2026, 11, 13))
    assert rows[0]["bar_date"] == date(2026, 11, 13) and rows[0]["adj_close"] == 1.4
    assert bars.parse_yahoo("X", {"chart": {"result": None}}, date(2026, 1, 1), date(2026, 2, 1)) == []


def test_last_complete_day():
    et = lambda y, m, d, h, mi: datetime(y, m, d, h + 5, mi, tzinfo=timezone.utc)   # EST (UTC-5) in November
    assert bars.last_complete_day(et(2026, 11, 16, 17, 5)) == date(2026, 11, 16)
    assert bars.last_complete_day(et(2026, 11, 16, 15, 0)) == date(2026, 11, 13)    # live bar: Friday's
    assert bars.last_complete_day(et(2026, 11, 26, 18, 0)) == date(2026, 11, 25)    # Thanksgiving


class FakeSource:
    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    def fetch(self, ticker, first, last):
        self.calls.append((ticker, first, last))
        rows = bars.parse_fmp(ticker, self.bodies.get(ticker), first, last)
        return rows, ("fmp" if rows else "none")


NOW = datetime(2026, 11, 16, 22, 5, tzinfo=timezone.utc)       # 17:05 ET


def test_backfill_then_extend_then_restate(store):
    days = _weekdays(date(2026, 1, 5), date(2026, 11, 16))
    src = FakeSource({"AAA": _fmp_body(days)})
    res = bars.update(store, ["AAA", "BBB"], source=src, now=NOW)
    assert res["backfilled"] == ["AAA"] and res["failed"] == ["BBB"] and res["status"] == "degraded"
    assert store.bar_last_dates()["AAA"] == date(2026, 11, 16)
    # Up to date: no request.
    src.calls.clear()
    assert bars.update(store, ["AAA"], source=src, now=NOW)["up_to_date"] == 1 and not src.calls
    # Next day, same history: extended from the overlap window only.
    days2 = days + [date(2026, 11, 17)]
    src = FakeSource({"AAA": _fmp_body(days2)})
    res = bars.update(store, ["AAA"], source=src, now=NOW + timedelta(days=1))
    assert res["extended"] == 1 and len(src.calls) == 1 and src.calls[0][1] == date(2026, 11, 6)
    # A dividend re-scales adj_close of the history: the ticker is fetched whole and replaced.
    days3 = days2 + [date(2026, 11, 18)]
    src = FakeSource({"AAA": _fmp_body(days3, factor=0.99)})
    res = bars.update(store, ["AAA"], source=src, now=NOW + timedelta(days=2))
    assert res["restated"] == ["AAA"] and len(src.calls) == 2
    loaded = store.load_bars(["AAA"])["AAA"]
    assert len(loaded["date"]) == len(days3)
    assert abs(loaded["adj_close"][0] / loaded["close"][0] - 0.99) < 1e-9


def test_restated_detects_split_and_ignores_unchanged():
    s = {date(2026, 1, 2): {"close": 100.0, "adj_close": 99.0}}
    assert not bars.restated(s, [{"bar_date": date(2026, 1, 2), "close": 100.0, "adj_close": 99.0}])
    assert bars.restated(s, [{"bar_date": date(2026, 1, 2), "close": 50.0, "adj_close": 49.5}])
    assert not bars.restated(s, [{"bar_date": date(2026, 1, 5), "close": 50.0, "adj_close": 49.5}])


def test_fmp_key_is_masked_and_never_in_the_request_url(monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setenv("FMP_API_KEY", "sekret123")
    assert bars.mask("GET ...?apikey=sekret123 failed") == "GET ...?apikey=*** failed"

    class Tx:
        def __init__(self):
            self.seen = []

        def fmp(self, path, params, timeout):
            self.seen.append((path, params))
            raise RuntimeError("boom sekret123")

        def yahoo_chart(self, sym, params, timeout):
            self.seen.append((sym, params))
            return 500, None

    tx = Tx()
    src = bars.BarSource(transport=tx, fmp_key="sekret123")
    src.fmp_pacer.gap = src.yahoo_pacer.gap = 0
    rows, how = src.fetch("BRK.B", date(2026, 1, 1), date(2026, 2, 1))
    assert rows == [] and how == "none"
    assert tx.seen[0][0] == "/historical-price-full/BRK-B"
    assert all("sekret123" not in str(x) for x in tx.seen)
    assert "boom ***" in caplog.text and "sekret123" not in caplog.text


def test_pacer_spaces_calls():
    t = [0.0]
    slept = []
    p = bars.Pacer(2.0, clock=lambda: t[0], sleep=lambda s: slept.append(s))
    p.wait()
    p.wait()
    assert slept == [0.5]
