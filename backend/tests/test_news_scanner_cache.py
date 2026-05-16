"""Integration tests for the FinBERT cache short-circuit in NewsScanner.scan_news.

We mock the data client (so no network), the session (so no DB), and the
FinBERT batch scorer (so no 500MB model load). What we're checking is the
*orchestration*: that score_batch is called only for uncached candidates,
and that cached scores are reused for hash-matching candidates.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.news_scanner import NewsScanner, _content_hash


class _FakeScalars:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows
    def unique(self): return self
    def first(self): return self._rows[0] if self._rows else None


class _FakeResult:
    """SQLAlchemy result-shape stand-in for our specific call sites."""
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows
    def scalars(self): return _FakeScalars([r[0] if isinstance(r, tuple) else r for r in self._rows])


class _FakeSession:
    """Minimal AsyncSession stand-in. Queues result-sets in order of execute() calls.

    scan_news() makes these executes in order:
      1. SELECT MarketNews.id, source_url WHERE source_url IN (...)
      2. (only if url_to_news_id non-empty) SELECT NewsTickerRelevance rows
      3. SELECT content_hash, sentiment_score WHERE content_hash IN (...)
    """
    def __init__(self, results):
        self._queue = list(results)
        self.added: list = []
        self.committed = False

    async def execute(self, _stmt):
        if not self._queue:
            return _FakeResult([])
        return self._queue.pop(0)

    def add_all(self, rows): self.added.extend(rows)
    def add(self, row): self.added.append(row)
    async def commit(self): self.committed = True


def _make_news_item(headline, summary="", url="http://x/1"):
    return {
        "headline": headline,
        "summary": summary,
        "source": "TestWire",
        "url": url,
        "published_at": None,
        "category": "company",
        "related_tickers": ["AAPL"],
    }


@pytest.mark.asyncio
async def test_cache_hit_skips_finbert():
    """When DB already has a row with matching content_hash + sentiment_score,
    FinBERT must NOT be called for that candidate."""
    headline = "Apple beats earnings"
    summary = "Strong iPhone sales"
    expected_hash = _content_hash(headline, summary)

    data_client = MagicMock()
    data_client.get_news = MagicMock(return_value=[_make_news_item(headline, summary)])

    session = _FakeSession(results=[
        _FakeResult([]),                                    # 1. URL dedup query — empty
        _FakeResult([(expected_hash, Decimal("0.42"))]),    # 3. Hash cache lookup — HIT
    ])
    # Note: when URL dedup returns empty, the relevance-pair lookup is skipped
    # (url_to_news_id is empty), so the 2nd execute is the hash lookup.

    scanner = NewsScanner(session, data_client)
    with patch("services.news_scanner.score_batch") as mock_score:
        await scanner.scan_news(["AAPL"], {"AAPL": "Apple Inc"})

    mock_score.assert_not_called(), "FinBERT should not run for a cache hit"
    assert len(session.added) == 1
    assert session.added[0].content_hash == expected_hash
    assert float(session.added[0].sentiment_score) == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_cache_miss_scores_via_finbert():
    """No matching hash in DB → FinBERT IS called."""
    data_client = MagicMock()
    data_client.get_news = MagicMock(return_value=[
        _make_news_item("Completely novel headline", "Unique body")
    ])

    session = _FakeSession(results=[
        _FakeResult([]),  # URL dedup empty
        _FakeResult([]),  # hash cache empty
    ])

    scanner = NewsScanner(session, data_client)
    with patch("services.news_scanner.score_batch", return_value=[0.7]) as mock_score:
        await scanner.scan_news(["AAPL"], {"AAPL": "Apple Inc"})

    mock_score.assert_called_once()
    assert len(session.added) == 1
    assert float(session.added[0].sentiment_score) == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_within_batch_dedup_scores_once():
    """Two candidates in the same scan with identical text → FinBERT runs ONCE,
    both rows get the same score."""
    data_client = MagicMock()
    # Same headline+summary, different URLs (the case URL dedup can't catch)
    data_client.get_news = MagicMock(return_value=[
        _make_news_item("Same headline", "Same body", url="http://a/1"),
        _make_news_item("Same headline", "Same body", url="http://b/2"),
    ])

    session = _FakeSession(results=[
        _FakeResult([]),  # URL dedup empty
        _FakeResult([]),  # hash cache empty
    ])

    scanner = NewsScanner(session, data_client)
    with patch("services.news_scanner.score_batch", return_value=[0.5]) as mock_score:
        await scanner.scan_news(["AAPL"], {"AAPL": "Apple Inc"})

    # Critical: only ONE text sent to FinBERT, both rows reused the score
    mock_score.assert_called_once()
    call_args = mock_score.call_args.args[0]
    assert len(call_args) == 1, f"Expected 1 text for FinBERT, got {len(call_args)}"

    assert len(session.added) == 2
    assert float(session.added[0].sentiment_score) == pytest.approx(0.5)
    assert float(session.added[1].sentiment_score) == pytest.approx(0.5)
    # And the same content_hash on both
    assert session.added[0].content_hash == session.added[1].content_hash


@pytest.mark.asyncio
async def test_mixed_cache_hit_and_miss():
    """Some candidates have cached scores, some don't — FinBERT only scores the
    missing ones, all rows get correct sentiment."""
    cached_h = _content_hash("Cached headline", "Cached body")

    data_client = MagicMock()
    data_client.get_news = MagicMock(return_value=[
        _make_news_item("Cached headline", "Cached body", url="http://a/1"),
        _make_news_item("Fresh headline", "Fresh body", url="http://b/2"),
    ])

    session = _FakeSession(results=[
        _FakeResult([]),                              # URL dedup empty
        _FakeResult([(cached_h, Decimal("0.9"))]),    # Cache: HIT for the first one
    ])

    scanner = NewsScanner(session, data_client)
    with patch("services.news_scanner.score_batch", return_value=[-0.2]) as mock_score:
        await scanner.scan_news(["AAPL"], {"AAPL": "Apple Inc"})

    # FinBERT called once, with only the uncached text
    mock_score.assert_called_once()
    scored_texts = mock_score.call_args.args[0]
    assert len(scored_texts) == 1
    assert "Fresh headline" in scored_texts[0]

    assert len(session.added) == 2
    # Order in session.added matches input order
    by_hash = {r.content_hash: r for r in session.added}
    assert float(by_hash[cached_h].sentiment_score) == pytest.approx(0.9)
    # The uncached one has the FinBERT score
    fresh_h = _content_hash("Fresh headline", "Fresh body")
    assert float(by_hash[fresh_h].sentiment_score) == pytest.approx(-0.2)
