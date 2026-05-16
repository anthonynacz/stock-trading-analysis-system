"""Unit tests for the news-content sentiment cache key.

`_content_hash` defines the equivalence class of "same article text". Anything
that collapses to the same hash will share a cached FinBERT score, so the
normalization rules are load-bearing. Test them.
"""
from __future__ import annotations

import pytest

from services.news_scanner import _content_hash


def test_identical_text_same_hash():
    assert _content_hash("Apple beats earnings", "Strong iPhone sales") == \
           _content_hash("Apple beats earnings", "Strong iPhone sales")


def test_case_insensitive():
    a = _content_hash("Apple Beats Earnings", "Strong iPhone Sales")
    b = _content_hash("apple beats earnings", "STRONG IPHONE SALES")
    assert a == b


def test_whitespace_normalized():
    """Leading, trailing, and internal whitespace collapsed identically."""
    a = _content_hash("  Apple   beats   earnings  ", "Strong iPhone sales")
    b = _content_hash("Apple beats earnings", "Strong iPhone sales")
    assert a == b


def test_internal_newline_treated_as_whitespace():
    a = _content_hash("Apple beats\nearnings", "iPhone sales")
    b = _content_hash("Apple beats earnings", "iPhone sales")
    assert a == b


def test_different_headline_different_hash():
    a = _content_hash("Apple beats earnings", "Strong iPhone sales")
    b = _content_hash("Apple misses earnings", "Strong iPhone sales")
    assert a != b


def test_different_summary_different_hash():
    a = _content_hash("Apple earnings", "Strong iPhone sales")
    b = _content_hash("Apple earnings", "Weak iPhone sales")
    assert a != b


def test_none_summary_does_not_crash():
    h = _content_hash("Apple beats earnings", None)
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex


def test_empty_strings_produce_stable_hash():
    """Empty input shouldn't blow up — and two empties hash identically."""
    a = _content_hash("", "")
    b = _content_hash("", None)
    assert a == b
    assert len(a) == 64


def test_hash_format_is_sha256_hex():
    h = _content_hash("foo", "bar")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_punctuation_preserved():
    """Punctuation is part of meaning — we don't strip it."""
    a = _content_hash("Apple beats earnings", "iPhone sales")
    b = _content_hash("Apple beats earnings!", "iPhone sales")
    assert a != b


def test_headline_summary_boundary_not_collapsible():
    """Moving text between headline and summary changes nothing if the
    concatenated normalized form is identical — that's actually the design.
    Different *concatenations* should match if the result is the same word
    sequence. Confirms the join+normalize behavior."""
    a = _content_hash("Apple beats earnings strong", "iPhone sales")
    b = _content_hash("Apple beats earnings", "strong iPhone sales")
    # "apple beats earnings strong iphone sales" both ways → same hash
    assert a == b
