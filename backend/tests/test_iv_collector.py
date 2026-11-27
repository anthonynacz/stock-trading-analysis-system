"""The daily IV capture runs only while the regular session is open (live two-sided quotes)."""
from __future__ import annotations

from datetime import datetime

from radar import calendar_nyse as cal
from services.iv_collector import capture_skip_reason


def _et(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=cal.ET)


def test_regular_session_afternoon_captures() -> None:
    assert capture_skip_reason(_et("2026-11-25T15:00")) is None


def test_holiday_and_weekend_skip() -> None:
    assert capture_skip_reason(_et("2026-11-26T15:00")) == "not a trading day"    # Thanksgiving
    assert capture_skip_reason(_et("2026-11-28T15:00")) == "not a trading day"    # Saturday


def test_early_close_day_skips_the_afternoon_capture() -> None:
    assert capture_skip_reason(_et("2026-11-27T12:30")) is None                   # still open
    assert capture_skip_reason(_et("2026-11-27T15:00")) == "regular session closed"


def test_capture_stops_at_the_close() -> None:
    assert capture_skip_reason(_et("2026-11-25T15:59")) is None
    assert capture_skip_reason(_et("2026-11-25T16:00")) == "regular session closed"
