"""Unit tests for `services.breaking_news.build_breaking_items`.

The Dashboard breaking strip and the immediate Discord push both depend on
this assembly step: material headlines only, newest first, revision diff
attached only when that exact headline produced the rescore.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from services.breaking_news import build_breaking_items, rec_summary


@dataclass
class _News:
    id: int
    headline: str
    sentiment_score: Decimal | None
    impact_level: str | None
    category: str | None
    published_at: datetime
    source: str | None = "Reuters"
    source_url: str | None = "https://example.com/a"
    ticker_relevances: list = field(default_factory=list)


@dataclass
class _Rel:
    news_id: int
    ticker: str
    relevance_score: Decimal


@dataclass
class _Rec:
    id: int
    ticker: str
    action: str
    conviction_score: Decimal | None
    prior_action: str | None = None
    prior_conviction_score: Decimal | None = None
    revised_at: datetime | None = None
    revision_reason: str | None = None


def _ts(h: int) -> datetime:
    return datetime(2026, 9, 9, h, 0, tzinfo=timezone.utc)


def test_only_material_headlines_survive_and_newest_first():
    loud = _News(1, "Guidance slashed", Decimal("-0.8"), "MEDIUM", "EARNINGS", _ts(9))
    quiet = _News(2, "Minor blog post", Decimal("0.1"), "LOW", "PRODUCT", _ts(10))
    later = _News(3, "Upgrade to Buy", Decimal("0.6"), "HIGH", "ANALYST", _ts(11))
    rels = [_Rel(1, "AAPL", Decimal("0.9")), _Rel(2, "AAPL", Decimal("0.9")), _Rel(3, "AAPL", Decimal("0.9"))]
    items = build_breaking_items([loud, quiet, later], rels, {}, set(), limit=10)
    assert [i["id"] for i in items] == [3, 1]
    assert items[0]["tickers"][0]["ticker"] == "AAPL"
    assert items[0]["tickers"][0]["recommendation"] is None


def test_revision_diff_attached_only_for_triggering_headline():
    headline = "Chip demand surges on hyperscaler orders"
    news = _News(7, headline, Decimal("0.7"), "HIGH", "SECTOR", _ts(12))
    other = _News(8, "Unrelated but material", Decimal("0.7"), "HIGH", "MACRO", _ts(13))
    rels = [_Rel(7, "NVDA", Decimal("1.0")), _Rel(8, "NVDA", Decimal("0.8"))]
    rec = _Rec(
        id=5, ticker="NVDA", action="STRONG_BUY", conviction_score=Decimal("71"),
        prior_action="BUY", prior_conviction_score=Decimal("55"), revised_at=_ts(12),
        revision_reason=f"intraday_news[SECTOR/HIGH]: {headline}",
    )
    items = build_breaking_items([news, other], rels, {"NVDA": rec}, {"NVDA"}, limit=10)
    by_id = {i["id"]: i for i in items}

    trig = by_id[7]["tickers"][0]
    assert trig["held"] is True
    assert trig["recommendation"]["triggered_revision"] is True
    assert trig["recommendation"]["prior_action"] == "BUY"
    assert trig["recommendation"]["prior_conviction_score"] == 55.0

    untrig = by_id[8]["tickers"][0]["recommendation"]
    assert untrig["triggered_revision"] is False
    assert untrig["prior_action"] is None  # diff hidden when not caused by this story
    assert untrig["action"] == "STRONG_BUY"


def test_rec_summary_handles_missing_rec_and_limit():
    assert rec_summary(None, "x") is None
    news = [_News(i, f"h{i}", Decimal("0.9"), "HIGH", "ANALYST", _ts(i)) for i in range(1, 6)]
    rels = [_Rel(n.id, "MSFT", Decimal("0.9")) for n in news]
    assert len(build_breaking_items(news, rels, {}, set(), limit=2)) == 2
