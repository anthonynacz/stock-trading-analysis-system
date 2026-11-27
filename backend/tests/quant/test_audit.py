"""Bars audit (quant/audit.py): each defect planted in synthetic bars is found, clean bars stay unflagged."""
from __future__ import annotations

from datetime import date

import numpy as np
from quant import audit
from tests.quant.conftest import panel_rows, synth_ohlc, weekdays

N = 300


def _clean(seed: int = 3, sigma: float = 0.02) -> tuple[list[date], dict]:
    rng = np.random.default_rng(seed)
    dates = weekdays(date(2021, 1, 4), N)
    ohlc = synth_ohlc(np.full(N, sigma), rng, steps=60)
    r = panel_rows(dates, ohlc)
    r["volume"] = [1_000_000] * N
    r["source"] = ["fmp"] * N
    return dates, r


def _ref(dates):
    return np.asarray([d.isoformat() for d in dates], dtype="datetime64[D]")


def test_clean_bars_are_not_flagged():
    dates, r = _clean()
    rep = audit.audit_ticker("OK", r, _ref(dates))
    assert rep["flags"] == [] and rep["severity"] == 0
    assert rep["missing_sessions"] == 0 and rep["extra_dates"] == 0
    assert 0.6 < rep["scale"] < 1.6


def test_invalid_ohlc_and_outside_range():
    dates, r = _clean()
    r["high"][10], r["low"][10] = r["low"][10], r["high"][10]          # high < low
    r["open"][20] = r["high"][20] * 1.05                               # open 5% above the high
    rep = audit.audit_ticker("BAD", r, _ref(dates))
    assert rep["high_lt_low"] == 1 and "ohlc_invalid" in rep["flags"]
    assert rep["price_outside_gross"] >= 1 and "price_outside_range" in rep["flags"]


def test_stale_run_and_no_trade_bars_and_near_zero_trailing():
    dates, r = _clean()
    c = r["close"][99]
    for i in range(100, 130):                                          # 30 sessions of the same printed price
        r["open"][i] = r["high"][i] = r["low"][i] = r["close"][i] = r["adj_close"][i] = c
        r["volume"][i] = 0
    rep = audit.audit_ticker("STALE", r, _ref(dates))
    assert rep["zero_range"] == 30 and rep["zero_volume"] == 30 and rep["no_trade_bars"] == 30
    assert rep["longest_flat_run"] >= 30 and "stale_run" in rep["flags"]
    # The model treats no-trade bars as missing: the trailing window has no data, not a zero variance.
    assert rep["floor_days"] == 0 and rep["near_zero_trailing_days"] == 0


def test_genuinely_quiet_bars_show_near_zero_trailing():
    dates, r = _clean()
    for i in range(100, 130):                                          # trading, but a 0.01% range
        base = r["close"][99]
        r["open"][i], r["close"][i], r["adj_close"][i] = base, base * 1.00005, base * 1.00005
        r["high"][i], r["low"][i] = base * 1.0001, base * 0.9999
    rep = audit.audit_ticker("QUIET", r, _ref(dates))
    assert rep["near_zero_trailing_days"] > 0 and "near_zero_trailing" in rep["flags"]


def test_split_only_in_adj_close_is_the_prime_suspect():
    dates, r = _clean()
    for i in range(150):                                               # adj_close halves before day 150, prices do not
        r["adj_close"][i] *= 0.5
    rep = audit.audit_ticker("ADJ", r, _ref(dates))
    kinds = [j["kind"] for j in rep["factor_jumps"]]
    assert kinds == ["adj_only"] and rep["factor_jumps"][0]["date"] == dates[150].isoformat()
    assert "adj_only_jump" in rep["flags"] and rep["severity"] >= audit.WEIGHTS["adj_only_jump"]


def test_split_in_ohlc_consistent_vs_inconsistent():
    dates, r = _clean()
    for k in ("open", "high", "low", "close"):                         # unadjusted 2:1 split in OHLC, adj_close adjusted
        for i in range(150):
            r[k][i] *= 2.0
    rep = audit.audit_ticker("SPLIT", r, _ref(dates))
    assert [j["kind"] for j in rep["factor_jumps"]] == ["in_ohlc"]
    assert "split_inconsistent" not in rep["flags"]

    r["open"][150] *= 2.0                                              # the split day's open still on the old basis
    rep = audit.audit_ticker("SPLIT2", r, _ref(dates))
    assert [j["kind"] for j in rep["factor_jumps"]] == ["inconsistent"] and "split_inconsistent" in rep["flags"]


def test_unadjusted_split_in_both_series():
    dates, r = _clean(sigma=0.004)                                     # quiet enough that the split day is ~ln 3
    for k in ("open", "high", "low", "close", "adj_close"):            # history before day 150 not split-adjusted at all
        for i in range(150):
            r[k][i] *= 3.0
    rep = audit.audit_ticker("UNADJ", r, _ref(dates))
    assert rep["unadjusted_splits"] and rep["unadjusted_splits"][0]["ratio"] == 3.0
    assert "unadjusted_split" in rep["flags"] and rep["big_returns"] >= 1


def test_mixed_sources_gaps_extra_dates_and_missing_adj():
    dates, r = _clean()
    r["source"] = ["fmp"] * 200 + ["yahoo"] * (N - 200)
    r["adj_close"][50] = float("nan")
    ref = _ref(dates)
    keep = [i for i in range(N) if not (60 <= i < 80)]                 # 20 sessions missing (6.7% > 2%)
    r = {k: [v[i] for i in keep] for k, v in r.items()}
    ref = np.delete(ref, 250)                                          # a date the reference calendar does not have
    rep = audit.audit_ticker("MIX", r, ref)
    assert rep["sources"] == {"fmp": 180, "yahoo": N - 200} and rep["source_switches"] == 1
    assert rep["missing_sessions"] == 20 and "gaps" in rep["flags"]
    assert rep["extra_dates"] == 1 and "extra_dates" in rep["flags"]
    assert rep["adj_missing"] == 1 and "adj_missing" in rep["flags"]
    assert {"mixed_sources", "gaps", "extra_dates", "adj_missing"} <= set(rep["flags"])


def test_audit_report_sorts_by_severity_and_formats():
    dates, good = _clean(1)
    _, bad = _clean(2)
    for i in range(150):
        bad["adj_close"][i] *= 0.5
    spy = {**good}
    rep = audit.audit({"SPY": spy, "GOOD": good, "BAD": bad})
    assert rep["tickers"] == 3 and rep["calendar_reference"] == "SPY"
    assert rep["tickers_detail"][0]["ticker"] == "BAD" and rep["flagged"] == 1
    assert rep["rows_by_source"] == {"fmp": 3 * N}
    text = audit.format_audit(rep)
    assert "BAD" in text and "adj_only" in text and len(text.splitlines()) < 50
    assert "GOOD " not in text.split("flags\n", 1)[1]


def test_audit_main_reads_a_csv(tmp_path, capsys):
    dates, r = _clean()
    p = tmp_path / "bars.csv"
    with open(p, "w") as fh:
        fh.write("ticker,date,open,high,low,close,adj_close,volume,source\n")
        for i, d in enumerate(r["date"]):
            fh.write(f"SPY,{d},{r['open'][i]},{r['high'][i]},{r['low'][i]},{r['close'][i]},{r['adj_close'][i]},"
                     f"{r['volume'][i]},yahoo\n")
    assert audit.main(["--csv", str(p), "--json"]) == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["tickers"] == 1 and out["rows_by_source"] == {"yahoo": N}
