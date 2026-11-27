"""The har-gk-3 cleaning rules (quant/cleaning.py, config.ESTIMATOR): each defect planted in synthetic bars, and the
controls that must not be touched."""
from __future__ import annotations

import math
from datetime import date

import numpy as np

from quant import audit, cleaning, evaluate, har
from quant.config import ESTIMATOR, EVAL, HAR
from tests.quant.conftest import synth_ohlc, weekdays

SPLITS, SHELL = ESTIMATOR["splits"], ESTIMATOR["shell"]
VOL = 1_000_000


def _segments(segs: list[tuple[int, float, float]], seed: int = 1, s0: float = 10.0) -> dict:
    """Concatenated synthetic bars: each (sessions, annual vol, level). A level > 0 pins the segment to that price
    (a trust / deal: closes mean-revert to it), 0 lets the random walk run from the last close."""
    rng = np.random.default_rng(seed)
    parts, prev = [], s0
    for n, vol, level in segs:
        o = synth_ohlc(np.full(n, vol / math.sqrt(252)), rng, steps=40, s0=level or prev)
        if level:                                       # pinned: remove the walk, keep the daily noise
            drift = o["close"] / level
            for k in ("open", "high", "low", "close", "adj_close"):
                o[k] = o[k] / np.concatenate([[1.0], drift[:-1]])
        parts.append(o)
        prev = float(o["close"][-1])
    n = sum(p["close"].size for p in parts)
    r = {k: list(np.concatenate([p[k] for p in parts])) for k in ("open", "high", "low", "close", "adj_close")}
    r["date"] = [d.isoformat() for d in weekdays(date(2017, 1, 2), n)]
    r["volume"] = [VOL] * n
    return r


def _v(r, est=ESTIMATOR):
    return cleaning.variance_series(r["open"], r["high"], r["low"], r["close"], r["adj_close"], r["volume"], est)


# ---------------------------------------------------------------- shell history

def test_shell_is_cut_through_the_end_of_the_trust():
    r = _segments([(300, 0.03, 10.0), (500, 0.9, 0)])           # trust at $10, then a volatile operating company
    v, info = _v(r)
    L = info["shell_end"]
    assert 290 <= L < 300 and info["shell_bars"] > 250
    assert np.isnan(v[:L + 1]).all() and np.isfinite(v[L + 1:]).mean() > 0.99
    assert not info["pinned_now"]
    # The first HAR features after the cut need 21 real sessions (the burn-in).
    X = har.features(v)
    assert not np.isfinite(X[L + 1:L + 17]).all(axis=1).any() and np.isfinite(X[L + 25]).all()


def test_shell_at_normal_trust_noise_is_still_a_shell():
    # A trust's price sits within +/- 1% but bid-ask bounce gives the estimator ~15% vol (Yahoo NAMS / QBTS 2021).
    r = _segments([(300, 0.15, 10.0), (400, 0.9, 0)], seed=4)
    _, info = _v(r)
    assert 280 <= info["shell_end"] < 300


def test_cash_fund_and_waiting_shell_are_kept_and_flagged_live():
    r = _segments([(800, 0.004, 90.0)])                          # a T-bill fund: pinned for its whole life
    v, info = _v(r)
    assert info["shell_end"] == -1 and np.isfinite(v).mean() > 0.99 and info["pinned_now"]
    r = _segments([(300, 0.03, 10.0)])                           # a shell still waiting for its merger
    _, info = _v(r)
    assert info["shell_end"] == -1 and info["pinned_now"]


def test_recent_exit_is_not_cut_until_min_after():
    r = _segments([(300, 0.03, 10.0), (SHELL["min_after"] - 10, 0.9, 0)])
    _, info = _v(r)
    assert info["shell_end"] == -1 and not info["pinned_now"]


def test_bond_fund_drifting_in_and_out_is_not_cut():
    # calm at the start, then years of moderate vol with calm spells, calm again at the end
    segs = [(150, 0.03, 0), (250, 0.12, 0), (100, 0.03, 0), (400, 0.12, 0), (150, 0.03, 0), (400, 0.12, 0)]
    r = _segments(segs, s0=100.0)
    _, info = _v(r)
    assert info["shell_end"] == -1


def test_pinned_regime_later_in_life_is_not_cut():
    # A cash takeover: 600 normal sessions, 200 pinned at the deal price, the deal breaks (genuine volatility).
    r = _segments([(600, 0.30, 0), (200, 0.03, 40.0), (300, 0.70, 0)], s0=35.0)
    v, info = _v(r)
    assert info["shell_end"] == -1 and np.isfinite(v[600:800]).mean() > 0.99
    # While the deal was pending the live flag would have been set.
    r2 = {k: x[:800] for k, x in r.items()}
    assert _v(r2)[1]["pinned_now"]


def test_calm_stocks_are_not_flagged_live():
    r = _segments([(800, 0.12, 0)], s0=60.0)                     # a utility in calm years
    _, info = _v(r)
    assert info["shell_end"] == -1 and not info["pinned_now"]


def test_one_quiet_month_at_listing_is_not_a_shell():
    r = _segments([(40, 0.03, 10.0), (700, 0.6, 0)])
    assert _v(r)[1]["shell_end"] == -1


# ---------------------------------------------------------------- split defects

def _split(r: dict, t: int, k: float, *, adj: bool = True, prices: bool = True, volume: bool = True) -> None:
    """History before t carries a k:1 split the vendor did not adjust (prices k times higher)."""
    for i in range(t):
        if prices:
            for f in ("open", "high", "low", "close"):
                r[f][i] *= k
        if adj:
            r["adj_close"][i] *= k
        if volume:
            r["volume"][i] = int(r["volume"][i] / k)


def _kind(info, t):
    return [e["kind"] for e in info["split_events"] if e["i"] == t]


def test_unadjusted_split_with_raw_volume_is_repaired():
    r = _segments([(400, 0.25, 0)], s0=50.0)
    _split(r, 200, 2.0)
    v, info = _v(r)
    assert _kind(info, 200) == ["repaired"]
    assert v[200] < 0.003 and info["split_missing"] == 0           # the -ln 2 gap is gone, the day is kept
    assert abs(info["on"][200]) < 0.05 and abs(info["cc"][200]) < 0.1
    raw, _ = _v(r, {**ESTIMATOR, "splits": None})
    assert raw[200] > 0.4                                          # without the rule: (ln 2)^2 in one day


def test_reverse_split_is_repaired_too():
    r = _segments([(400, 0.25, 0)], s0=50.0)
    _split(r, 200, 1 / 10)                                         # 1:10 reverse: old prices ten times lower
    assert _kind(_v(r)[1], 200) == ["repaired"]


def test_split_without_share_evidence_is_ambiguous_and_missing():
    r = _segments([(400, 0.25, 0)], s0=50.0)
    _split(r, 200, 3.0, volume=False)                              # volume already adjusted, prices not
    v, info = _v(r)
    assert _kind(info, 200) == ["ambiguous"] and np.isnan(v[200]) and info["split_missing"] == 1


def test_split_in_adj_close_only_uses_the_raw_return():
    r = _segments([(400, 0.25, 0)], s0=50.0)
    _split(r, 200, 0.5, prices=False, volume=False)               # adj_close halves before t, prices continuous
    v, info = _v(r)
    assert _kind(info, 200) == ["adj_only"] and v[200] < 0.003
    raw, _ = _v(r, {**ESTIMATOR, "splits": None})
    assert raw[200] > 0.4


def test_split_adjusted_in_adj_close_is_already_right():
    r = _segments([(400, 0.25, 0)], s0=50.0)
    _split(r, 200, 2.0, adj=False, volume=False)                   # prices unadjusted, adj_close continuous
    v, info = _v(r)
    assert _kind(info, 200) == ["adj_confirmed"] and v[200] < 0.003


def test_special_dividend_is_left_to_the_dividend_correction():
    r = _segments([(400, 0.25, 0)], s0=50.0)
    for f in ("open", "high", "low", "close", "adj_close"):        # a 15% special dividend paid at the open of t
        for i in range(200, 400):
            r[f][i] *= 0.85
    for i in range(200, 400):
        r["adj_close"][i] /= 0.85
    v, info = _v(r)
    assert info["split_events"] == [] and info["special_dividends"] == 1 and v[200] < 0.003


def test_genuine_ratio_sized_move_on_a_news_day_is_left():
    r = _segments([(400, 0.6, 0)], s0=5.0, seed=8)
    for f in ("open", "high", "low", "close", "adj_close"):        # opens at 2x (a squeeze) on a wide-range day
        r[f][200] = r["close"][199] * 2.0
    r["high"][200], r["low"][200] = r["close"][199] * 2.3, r["close"][199] * 1.4
    r["close"][200] = r["adj_close"][200] = r["close"][199] * 1.6
    v, info = _v(r)
    assert _kind(info, 200) == ["left"] and v[200] > 0.4           # real volatility is not deleted


def test_one_day_bad_print_is_ambiguous_on_both_days():
    r = _segments([(400, 0.2, 0)], s0=20.0)
    for f in ("open", "high", "low", "close", "adj_close"):        # day 200 printed at 3x, back the next day
        r[f][200] *= 3.0
    v, info = _v(r)
    assert _kind(info, 200) == ["ambiguous"] and _kind(info, 201) == ["ambiguous"]
    assert np.isnan(v[200]) and np.isnan(v[201])


# ---------------------------------------------------------------- missing sessions

def test_session_reference_and_alignment():
    days = [d.isoformat() for d in weekdays(date(2020, 1, 1), 300)]
    full = {"date": days, **{k: [1.0] * 300 for k in ("open", "high", "low", "close", "adj_close")},
            "volume": [1] * 300}
    holey = {k: v[:100] + v[190:] for k, v in full.items()}       # 90 sessions missing
    extra = {k: list(v) for k, v in full.items()}
    rows = {"A": full, "B": {k: list(v) for k, v in full.items()}, "C": holey}
    ref = cleaning.session_reference(rows)
    assert len(ref) == 300
    al, ins = cleaning.align_sessions(holey, ref)
    assert ins == 90 and len(al["date"]) == 300 and np.isnan(al["close"][150]) and al["volume"][150] is None
    # a date only one ticker has does not become a session for the others
    extra["date"][10] = "2020-01-04"                               # a Saturday
    rows = {"A": full, "B": rows["B"], "X": extra}
    assert np.datetime64("2020-01-04") not in cleaning.session_reference(rows)


def test_gap_cannot_make_one_overnight_return_or_a_window_span_months():
    r = _segments([(600, 0.3, 0)], s0=30.0)
    r["date"] = [d.isoformat() for d in weekdays(date(2019, 1, 1), 600)]
    jump = 1.5                                                     # the price moved 50% while no bars were stored
    for f in ("open", "high", "low", "close", "adj_close"):
        for i in range(400, 600):
            r[f][i] *= jump
    holey = {k: v[:300] + v[400:] for k, v in r.items()}           # 100 sessions without bars
    other = {k: list(v) for k, v in r.items()}
    rows = {"FULL": other, "FULL2": {k: list(v) for k, v in r.items()}, "HOLE": holey}
    on = evaluate.panel_from_rows(rows, HAR, ESTIMATOR)["HOLE"]
    off = evaluate.panel_from_rows(rows, HAR, {**ESTIMATOR, "gaps": None})["HOLE"]
    assert len(on.dates) == 600 and len(off.dates) == 500 and on.clean["inserted"] == 100
    # without the rule the bar after the hole carries the whole 100-session move as one "overnight" return
    span = math.log(holey["open"][300] / holey["close"][299]) ** 2
    assert span > 0.05 and off.v[300] >= span
    assert np.isnan(on.v[300:401]).all() and on.clean["post_gap"] == 1
    # a window may miss up to 4 of its 21 sessions (har.MIN_VALID_SHARE), none spans the hole
    assert np.isnan(on.trailing[304:417]).all() and np.isfinite(on.trailing[[303, 417]]).all()
    assert np.isnan(on.rv[283:396]).all() and np.isfinite(on.rv[[282, 396]]).all()
    assert np.isfinite(off.trailing[300:310]).all()


# ---------------------------------------------------------------- scorecard and audit report the rules

def _panel_rows() -> dict:
    rows = {f"T{i}": _segments([(900, 0.3, 0)], seed=20 + i, s0=40.0) for i in range(3)}
    rows["SH"] = _segments([(250, 0.03, 10.0), (650, 0.8, 0)], seed=7)
    _split(rows["T1"], 600, 2.0)
    return rows


def test_scorecard_cleaning_block_counts_each_rule():
    ev = {**EVAL, "min_train_days": 300}
    card = evaluate.run(_panel_rows(), HAR, ev)
    cl = card["cleaning"]
    assert cl["rules"] == ["no_trade_missing", "gaps", "splits", "shell"]
    br = cl["by_rule"]
    assert br["shell"]["tickers"] == 1 and br["shell"]["bars_removed"] > 200
    fu = br["shell"]["first_usable"]["SH"]
    assert "2017-12-01" < fu["first_usable"] < "2018-01-31" and fu["shell_last"] < fu["first_usable"]
    assert br["shell"]["oos_ticker_days_removed"] >= 0 and br["splits"]["repaired"] == 1
    assert br["splits"]["events"]["T1"][0]["kind"] == "repaired"
    text = evaluate.format_scorecard(card)
    assert "shell history:" in text and "SH: pinned through" in text and "split defects: 1 repaired" in text


def test_audit_reports_what_the_rules_do():
    rows = _panel_rows()
    rows["SPY"] = rows.pop("T0")
    rep = audit.audit(rows)
    by = {x["ticker"]: x for x in rep["tickers_detail"]}
    assert "shell_history" in by["SH"]["flags"] and by["SH"]["shell"]["first_usable"]
    assert "split_repaired" in by["T1"]["flags"] and by["T1"]["split_rule"][0]["kind"] == "repaired"
    assert set(rep["shells"]) == {"SH"} and "T1" in rep["split_rule"]
    assert "shell " in audit.format_audit(rep)
