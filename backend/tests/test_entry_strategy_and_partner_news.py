"""Direction-aware entry strategy (REACTIVE vs PULLBACK) and third-party partner news.

REACTIVE used to fire on any > 3% move, so a BUY on a stock down 3.9% (MRVL, 2026-10-12) read
"catalyst already in motion". A third party's press release naming the ticker as a partner
("Aviz Networks Delivers ... for the Marvell ...") was rated HIGH impact and triggered that rescore.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import pytest

from services.intraday_news import RELEVANCE_THRESHOLD, filter_material_news
from services.recommendation_engine import RecommendationEngine
from utils.scoring import assign_impact_level, compute_ticker_relevance, is_third_party_mention


def _engine() -> RecommendationEngine:
    eng = RecommendationEngine.__new__(RecommendationEngine)

    class _Earnings:
        async def get_catalyst_window(self, ticker):
            return None

    eng._earnings_service = _Earnings()
    return eng


# ---------------------------------------------------------------- entry strategy

@pytest.mark.parametrize("conviction, moved, expected", [
    (49, 3.9, "REACTIVE"),       # bullish call, stock already up: chasing
    (49, -3.9, "PULLBACK"),      # bullish call, stock falling: not "in motion"
    (49, 3.0, "WAIT"),           # the 3% bound is exclusive both ways
    (49, -3.0, "WAIT"),
    (49, 1.2, "WAIT"),
    (20, -6.0, "WAIT"),          # conviction below 30 never reaches REACTIVE / PULLBACK
    (10, 5.0, "HOLD"),
    (-40, -6.0, "HOLD"),         # bearish convictions are unchanged
])
def test_entry_strategy_follows_the_direction_of_the_move(conviction, moved, expected):
    assert _engine()._determine_entry_strategy(conviction, None, moved, []) == expected


def test_pre_position_still_wins_over_a_big_move():
    window = {"window_status": "ACTIVE"}
    assert _engine()._determine_entry_strategy(49, window, -6.0, []) == "PRE_POSITION"
    assert _engine()._determine_entry_strategy(49, window, 6.0, []) == "PRE_POSITION"


def _rationale(entry: str, price: float, prev: float) -> str:
    stock = {"price": price, "previous_close": prev, "avg_analyst_target": 337.99}
    signals = [{"signal": "Above 50d SMA", "points": 10, "detail": "Price above 50d SMA"}]
    return asyncio.run(_engine()._generate_rationale("MRVL", "BUY", 49.0, signals, stock, entry_strategy=entry))


def test_pullback_rationale_names_the_actual_move():
    text = _rationale("PULLBACK", 264.53, 275.28)
    assert "Entry: PULLBACK" in text and "down 3.9% today against the call" in text
    assert "catalyst already in motion" not in text


def test_reactive_rationale_names_the_actual_move():
    text = _rationale("REACTIVE", 286.0, 275.28)
    assert "Entry: REACTIVE" in text and "up 3.9% today in the call's direction" in text


def test_stock_already_moved_signal_no_longer_claims_a_catalyst():
    import inspect
    src = inspect.getsource(RecommendationEngine)
    assert "on catalyst\"" not in src and "Stock moved {stock_moved_pct:+.1f}% today" in src


# ---------------------------------------------------------------- third-party partner news

MARVELL = "Marvell Technology, Inc."


@pytest.mark.parametrize("ticker, company, headline, expected", [
    ("MRVL", MARVELL, "Aviz Networks Delivers Production-Ready SONiC for the Marvell Ethernet Switching Portfolio", True),
    ("MRVL", MARVELL, "Credo Launches 1.6T Retimer Compatible With Marvell Switches", True),
    ("NVDA", "NVIDIA Corporation", "Dell expands AI Factory lineup built on NVIDIA Blackwell", True),
    ("NVDA", "NVIDIA Corporation", "Supermicro partners with NVIDIA on liquid-cooled racks", True),
    ("AMD", "Advanced Micro Devices", "Oracle to deploy 50,000 GPUs from AMD in new cluster", True),
    # The ticker is the subject, a comparison, commentary or a customer win named up front: not third party.
    ("MRVL", MARVELL, "Marvell Unveils Second-generation Structera X, Industry-first CXL Memory Expanders", False),
    ("MRVL", MARVELL, "Marvell Technology (MRVL) To Showcase AI Data Center Solutions At OCP 2026", False),
    ("MRVL", MARVELL, "Marvell's Outlook Says Revenue and Earnings Could 10x in 5 Years", False),
    ("MRVL", MARVELL, "Better Artificial Intelligence Stock: Arm vs. Marvell Technology", False),
    ("MRVL", MARVELL, "Marvell price target raised by Bank of America on $80 billion sales outlook", False),
    ("MSFT", "Microsoft Corporation", "The AI Spending Boom Could Be Just Getting Started for Microsoft", False),
    ("AMD", "Advanced Micro Devices", "Oracle Selects AMD Instinct GPUs for New Cloud Cluster", False),
    ("TSLA", "Tesla, Inc.", "Wall Street Is Too Bearish on Tesla", False),
    ("AAPL", "Apple Inc.", "Jim Cramer Says Buy Apple", False),
    ("AAPL", "Apple Inc.", "", False),
])
def test_is_third_party_mention(ticker, company, headline, expected):
    assert is_third_party_mention(ticker, company, headline) is expected


def test_third_party_relevance_is_below_the_intraday_bar():
    score, source = compute_ticker_relevance(
        "MRVL", MARVELL, "Aviz Networks Delivers Production-Ready SONiC for the Marvell Ethernet Switching Portfolio",
        None, [], was_searched_ticker=True)
    assert (score, source) == (0.6, "THIRD_PARTY") and score < RELEVANCE_THRESHOLD
    assert compute_ticker_relevance("MRVL", MARVELL, "Marvell Unveils Structera X", None, [], True) == (1.0, "HEADLINE")


def test_third_party_impact_is_never_high():
    assert assign_impact_level("PRODUCT", 0.88) == "HIGH"
    assert assign_impact_level("PRODUCT", 0.88, third_party=True) == "MEDIUM"
    assert assign_impact_level("PRODUCT", -0.95, third_party=True) == "MEDIUM"
    assert assign_impact_level("PRODUCT", 0.15, third_party=True) == "LOW"
    assert assign_impact_level("EARNINGS", 0.9, third_party=True) == "MEDIUM"


@dataclass
class _News:
    id: int
    headline: str
    sentiment_score: Decimal | None
    impact_level: str | None
    category: str | None


@dataclass
class _Rel:
    news_id: int
    ticker: str
    relevance_score: Decimal


def test_a_third_party_press_release_no_longer_triggers_a_rescore():
    headline = "Aviz Networks Delivers Production-Ready SONiC for the Marvell Ethernet Switching Portfolio"
    score, _ = compute_ticker_relevance("MRVL", MARVELL, headline, None, [], True)
    news = _News(1, headline, Decimal("0.88"), assign_impact_level("PRODUCT", 0.88, third_party=True), "PRODUCT")
    assert filter_material_news([news], [_Rel(1, "MRVL", Decimal(str(score)))]) == []
    # The same story with Marvell as the subject still triggers.
    own = _News(2, "Marvell Delivers Production-Ready SONiC", Decimal("0.88"), assign_impact_level("PRODUCT", 0.88), "PRODUCT")
    assert len(filter_material_news([own], [_Rel(2, "MRVL", Decimal("1.0"))])) == 1
