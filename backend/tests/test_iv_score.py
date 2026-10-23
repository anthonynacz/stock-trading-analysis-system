"""IV cost score and the clean IV measurement it rests on (docs/iv-score-design.md)."""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from utils.iv_measure import (
    atm_expiry_iv, constant_maturity_iv, iv_from_chains, pick_iv_expiries, quoted_iv, realised_vol,
)
from utils.iv_score import iv_score, label_for, ratio_part

TODAY = date(2026, 10, 23)


def _c(strike, iv, bid=1.0, ask=1.1):
    return {"strike": strike, "bid": bid, "ask": ask, "impliedVolatility": iv}


# ---------------------------------------------------------------- measurement

def test_quality_gate_rejects_junk_quotes():
    assert quoted_iv(_c(100, 0.30)) == 0.30
    assert quoted_iv(_c(100, 0.30, bid=0.0, ask=0.0)) is None        # after-hours blank quote
    assert quoted_iv(_c(100, 0.30, bid=0.5, ask=1.0)) is None        # spread 67% of mid
    assert quoted_iv(_c(100, 5.88)) is None                          # NOK's 588% reading
    assert quoted_iv(_c(100, 0.00001)) is None


def test_atm_expiry_iv_averages_call_and_put_at_the_nearest_strike():
    calls = [_c(95, 0.40), _c(100, 0.30), _c(105, 0.25)]
    puts = [_c(95, 0.45), _c(100, 0.34), _c(105, 0.28)]
    assert atm_expiry_iv(calls, puts, 101.0) == pytest.approx(0.32)
    # Unquoted put: the call alone.
    assert atm_expiry_iv(calls, [_c(100, 0.34, bid=0, ask=0)], 100.0) == pytest.approx(0.30)
    assert atm_expiry_iv([_c(100, 0.3, bid=0, ask=0)], [], 100.0) is None


def test_constant_maturity_interpolates_in_total_variance():
    iv = constant_maturity_iv([(20, 0.30), (40, 0.40)])
    expected = math.sqrt((0.5 * 0.09 * 20 + 0.5 * 0.16 * 40) / 30)
    assert iv == pytest.approx(expected)
    assert constant_maturity_iv([(30, 0.33)]) == 0.33
    assert constant_maturity_iv([(40, 0.35)]) == 0.35                 # one side, within 15 days
    assert constant_maturity_iv([(80, 0.35)]) is None                 # too far to stand for 30 days
    assert constant_maturity_iv([(3, 0.9), (40, 0.35)]) == 0.35       # 0-6 DTE ignored


def test_iv_from_chains_and_expiry_pick():
    exps = [(TODAY + timedelta(days=d)).isoformat() for d in (1, 8, 15, 22, 36, 64)]
    assert pick_iv_expiries(exps, TODAY) == [exps[1], exps[3], exps[4]]
    chains = {
        exps[0]: {"calls": [_c(100, 1.5)], "puts": []},
        exps[1]: {"calls": [_c(100, 0.50)], "puts": [_c(100, 0.50)]},
        exps[3]: {"calls": [_c(100, 0.30)], "puts": [_c(100, 0.30)]},
        exps[4]: {"calls": [_c(100, 0.30)], "puts": [_c(100, 0.30)]},
    }
    out = iv_from_chains(chains, 100.0, TODAY)
    assert out["front_dte"] == 8 and out["iv_front"] == pytest.approx(0.50)
    assert out["iv30"] == pytest.approx(0.30)
    assert out["term_ratio"] == pytest.approx(0.50 / 0.30)
    assert iv_from_chains({exps[1]: {"calls": [_c(100, 0.3, 0, 0)], "puts": []}}, 100.0, TODAY)["iv30"] is None


def test_realised_vol():
    closes = [100 * math.exp(0.01 * (i % 2)) for i in range(22)]     # +1%, -1% alternating
    hv = realised_vol(closes, 20)
    assert hv == pytest.approx(0.01 * math.sqrt(20 / 19) * math.sqrt(252), rel=1e-6)
    assert realised_vol(closes[:10], 20) is None


# ---------------------------------------------------------------- score

def test_ratio_part_reproduces_the_old_radar_cut_offs():
    assert ratio_part(1.2) == pytest.approx(35)
    assert ratio_part(1.5) == pytest.approx(60)
    assert ratio_part(2.0) == pytest.approx(80)
    assert ratio_part(0.5) == 0 and ratio_part(5.0) == 100
    assert [label_for(s) for s in (34.9, 35, 59.9, 60, 79.9, 80)] == [
        "cheap", "fair", "fair", "pricey", "pricey", "expensive"]


def test_spike_does_not_make_high_iv_look_cheap():
    random.seed(1)
    history = [random.uniform(0.25, 0.35) for _ in range(251)] + [0.90]
    s = iv_score(0.40, 0.30, history)
    h = s["history"]
    assert h["rank"] < 30 and h["percentile"] > 99 and h["spike"]
    assert s["label"] == "pricey" and s["confidence"] == "high"
    assert any("spike" in n for n in s["notes"])


def test_tight_range_does_not_overstate_a_small_rise():
    random.seed(2)
    history = [random.uniform(0.28, 0.33) for _ in range(252)]
    s = iv_score(0.325, 0.28, history)
    assert s["history"]["percentile"] > 80                         # looks extreme on percentile alone
    assert s["label"] == "fair"
    assert any("counts only in part" in n for n in s["notes"])


def test_regime_shift_compares_with_the_recent_window():
    random.seed(3)
    history = [random.gauss(0.60, 0.04) for _ in range(190)] + [random.gauss(0.30, 0.02) for _ in range(62)]
    s = iv_score(0.32, 0.27, history)
    assert s["history"]["regime"] is not None and s["history"]["window_days"] == 60
    assert s["label"] == "fair"                                     # the 1-year window alone would say cheap
    assert any("regime changed" in n for n in s["notes"])


def test_no_history_scores_on_realised_vol_with_low_confidence():
    s = iv_score(0.339, 0.184, [])
    assert s["score"] == round(ratio_part(0.339 / 0.184)) and s["label"] == "pricey"
    assert s["confidence"] == "low" and s["history"] is None
    assert [p["key"] for p in s["parts"]] == ["realised"]


def test_history_weight_ramps_from_20_to_120_days():
    hist = [0.30] * 70
    s = iv_score(0.30, 0.25, hist)
    w = {p["key"]: p["weight"] for p in s["parts"]}
    assert w["history"] == pytest.approx(0.25) and w["realised"] == 0.5
    assert s["confidence"] == "medium"
    assert iv_score(0.30, None, [0.30] * 10) is None              # nothing to judge on


def test_event_floors_the_label_at_pricey():
    cheap = iv_score(0.20, 0.25, [])
    assert cheap["label"] == "cheap" and cheap["event"] is None
    s = iv_score(0.20, 0.25, [], earnings_date=date(2026, 10, 29))
    assert s["label"] == "pricey" and s["floored"] and s["event"]["kind"] == "earnings"
    assert s["score"] == cheap["score"]                            # the score itself is the cost now
    t = iv_score(0.20, 0.25, [], iv_front=0.26)
    assert t["event"]["kind"] == "term" and t["label"] == "pricey"
    hot = iv_score(0.60, 0.25, [], earnings_date=date(2026, 10, 29))
    assert hot["label"] == "expensive" and not hot["floored"]      # never lowered by the floor


# ---------------------------------------------------------------- rules that read the score

def test_rules_read_the_score():
    from types import SimpleNamespace

    from services.deep_options_analyzer import iv_bucket_for
    from services.options_analyzer import OptionsAnalyzer
    from services.recommendation_engine import RecommendationEngine, _iv_cost

    assert _iv_cost(SimpleNamespace(iv_score={"score": 72})) == 72
    assert _iv_cost(SimpleNamespace(iv_score=None)) is None and _iv_cost(None) is None

    eng = RecommendationEngine.__new__(RecommendationEngine)
    assert eng._determine_risk_level(65, 40) == "LOW"
    assert eng._determine_risk_level(65, 60) == "MEDIUM"
    assert eng._determine_risk_level(65, None) == "MEDIUM"          # no clean IV: never LOW
    assert eng._determine_risk_level(10, 20) == "HIGH"

    assert [iv_bucket_for({"label": l}) for l in ("cheap", "fair", "pricey", "expensive")] == [
        "LOW", "MID", "HIGH", "HIGH"]
    assert iv_bucket_for(None) == "MID"

    assert "debit spread" in OptionsAnalyzer._build_rationale("CALL_DEBIT_SPREAD", 65, None, 30, "BULLISH")
    assert "unknown" in OptionsAnalyzer._build_rationale("NAKED_CALL", None, None, 30, "BULLISH")
