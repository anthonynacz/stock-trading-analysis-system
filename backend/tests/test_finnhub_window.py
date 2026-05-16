"""Verify _get_news_finnhub uses a 3-day rolling window per-ticker.

The bug we're guarding against: the prior code used from=today,to=today,
which silently dropped Sat/Sun headlines on Monday's premarket scan.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from utils.data_sources import DataSourceClient


def test_finnhub_company_news_3_day_window():
    client = DataSourceClient.__new__(DataSourceClient)  # skip __init__ (avoids httpx setup)
    client._finnhub_get = MagicMock(return_value=[])

    # Freeze "now" to a Monday so we can read the from/to strings deterministically
    fixed_now = datetime(2026, 5, 18, 6, 30, tzinfo=timezone.utc)  # Mon 06:30 UTC

    with patch("utils.data_sources.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        # Important: the production code also imports timedelta from datetime,
        # so timedelta arithmetic needs to still work. Wrap it through.
        from datetime import timedelta as _real_timedelta
        from datetime import datetime as _real_dt
        mock_dt.side_effect = lambda *a, **kw: _real_dt(*a, **kw)
        # Make 'now - timedelta(days=3)' work
        fixed_now_minus_3 = fixed_now - _real_timedelta(days=3)

        client._get_news_finnhub("AAPL", limit=20)

    # Inspect the params Finnhub was actually called with
    assert client._finnhub_get.called
    call_kwargs = client._finnhub_get.call_args.kwargs
    params = call_kwargs["params"]

    assert params["symbol"] == "AAPL"
    assert params["to"] == "2026-05-18", f"to_date should be today (Mon), got {params['to']}"
    assert params["from"] == "2026-05-15", \
        f"from_date should be Friday (Mon - 3d), got {params['from']}"


def test_finnhub_general_news_no_date_filter():
    """No-ticker path uses /api/v1/news?category=general, no from/to params."""
    client = DataSourceClient.__new__(DataSourceClient)
    client._finnhub_get = MagicMock(return_value=[])

    client._get_news_finnhub(None, limit=20)

    call_args = client._finnhub_get.call_args
    assert call_args.args[0] == "/api/v1/news"
    assert call_args.kwargs["params"] == {"category": "general"}
    assert "from" not in call_args.kwargs["params"]
    assert "to" not in call_args.kwargs["params"]
