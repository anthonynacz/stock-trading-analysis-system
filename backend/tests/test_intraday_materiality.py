"""Unit tests for `intraday_news.filter_material_news`.

The materiality filter decides whether a (ticker, headline) pair is impactful
enough to justify re-running the recommendation engine for that ticker. Wrong
thresholds either burn compute on noise or miss real catalysts — both bad.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from services.intraday_news import filter_material_news


@dataclass
class _News:
    """Minimal MarketNews stand-in for the filter (which only reads attrs)."""
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


def _make(news, ticker="AAPL", relevance=0.9):
    rel = _Rel(news.id, ticker, Decimal(str(relevance)))
    return [news], [rel]


def test_high_impact_always_passes_regardless_of_sentiment():
    """An article tagged HIGH-impact passes even with neutral sentiment."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.05"),
                 impact_level="HIGH", category="PRODUCT")
    items, rels = _make(news)
    triggers = filter_material_news(items, rels)
    assert len(triggers) == 1
    assert triggers[0].ticker == "AAPL"


def test_negative_sentiment_symmetric_with_positive():
    """|sentiment| ≥ 0.4 fires whether the news is bullish or bearish."""
    pos = _News(id=1, headline="up", sentiment_score=Decimal("0.5"),
                impact_level="MEDIUM", category="PRODUCT")
    neg = _News(id=2, headline="down", sentiment_score=Decimal("-0.5"),
                impact_level="MEDIUM", category="PRODUCT")
    pos_t = filter_material_news(*_make(pos))
    neg_t = filter_material_news(*_make(neg))
    assert len(pos_t) == 1
    assert len(neg_t) == 1


def test_below_default_threshold_filtered_out():
    """|sentiment| < 0.4 with a non-material category → filtered out."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.3"),
                 impact_level="MEDIUM", category="PRODUCT")
    triggers = filter_material_news(*_make(news))
    assert triggers == []


def test_category_branch_lower_threshold():
    """EARNINGS/ANALYST/GEOPOLITICAL use a 0.3 threshold, not 0.4."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.35"),
                 impact_level="MEDIUM", category="EARNINGS")
    triggers = filter_material_news(*_make(news))
    assert len(triggers) == 1


def test_category_branch_negative_symmetric():
    """Negative earnings news at 0.35 also fires — symmetric on |sentiment|."""
    news = _News(id=1, headline="miss", sentiment_score=Decimal("-0.35"),
                 impact_level="MEDIUM", category="EARNINGS")
    triggers = filter_material_news(*_make(news))
    assert len(triggers) == 1


def test_low_relevance_blocks_default_branch():
    """relevance < 0.7 means the default branch can't fire even at sentiment 0.5."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.5"),
                 impact_level="MEDIUM", category="PRODUCT")
    items, _ = _make(news, relevance=0.4)
    rels = [_Rel(news.id, "AAPL", Decimal("0.4"))]
    triggers = filter_material_news(items, rels)
    assert triggers == []


def test_low_relevance_does_not_block_high_impact():
    """HIGH impact bypasses the relevance gate (the news is news regardless)."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.0"),
                 impact_level="HIGH", category="PRODUCT")
    rels = [_Rel(news.id, "AAPL", Decimal("0.2"))]
    triggers = filter_material_news([news], rels)
    assert len(triggers) == 1


def test_low_relevance_does_not_block_category_branch():
    """ANALYST/EARNINGS/GEOPOLITICAL also bypass the relevance gate when
    |sentiment| ≥ 0.3 — the category is the strong signal."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.35"),
                 impact_level="MEDIUM", category="ANALYST")
    rels = [_Rel(news.id, "AAPL", Decimal("0.2"))]
    triggers = filter_material_news([news], rels)
    assert len(triggers) == 1


def test_dedup_per_ticker_news_pair():
    """Two relevance rows for the same (ticker, news_id) only emit once."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.5"),
                 impact_level="HIGH", category="PRODUCT")
    rels = [
        _Rel(news.id, "AAPL", Decimal("0.9")),
        _Rel(news.id, "AAPL", Decimal("0.7")),  # duplicate per-ticker
    ]
    triggers = filter_material_news([news], rels)
    assert len(triggers) == 1


def test_two_tickers_for_same_news_both_emitted():
    """One article relevant to AAPL and MSFT → two triggers, one per ticker."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.5"),
                 impact_level="HIGH", category="PRODUCT")
    rels = [
        _Rel(news.id, "AAPL", Decimal("0.9")),
        _Rel(news.id, "MSFT", Decimal("0.8")),
    ]
    triggers = filter_material_news([news], rels)
    tickers = {t.ticker for t in triggers}
    assert tickers == {"AAPL", "MSFT"}


def test_orphan_relevance_skipped():
    """Relevance row pointing at a news_id not in the items list → skipped, not crash."""
    news = _News(id=1, headline="x", sentiment_score=Decimal("0.5"),
                 impact_level="HIGH", category="PRODUCT")
    rels = [
        _Rel(news.id, "AAPL", Decimal("0.9")),
        _Rel(999, "MSFT", Decimal("0.8")),  # orphan: news id 999 not in items
    ]
    triggers = filter_material_news([news], rels)
    assert len(triggers) == 1
    assert triggers[0].ticker == "AAPL"


def test_null_sentiment_treated_as_zero():
    """Defensive: sentiment_score NULL shouldn't crash, just fail the threshold."""
    news = _News(id=1, headline="x", sentiment_score=None,
                 impact_level="MEDIUM", category="PRODUCT")
    triggers = filter_material_news(*_make(news))
    assert triggers == []
