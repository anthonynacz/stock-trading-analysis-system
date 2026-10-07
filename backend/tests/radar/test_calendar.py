"""The pinned NYSE calendar: holidays, half days, DST and the regular-session slot grid."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from radar import calendar_nyse as cal

UTC = timezone.utc


def sessions_between(start: date, end: date) -> list[cal.Session]:
    out, d = [], start
    while d <= end:
        s = cal.session_for(d)
        if s:
            out.append(s)
        d += timedelta(days=1)
    return out


@pytest.mark.parametrize("day", [
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
    "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24",
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19", "2028-07-04", "2028-09-04",
    "2028-11-23", "2028-12-25",
])
def test_holidays_are_closed(day):
    assert cal.session_for(date.fromisoformat(day)) is None


@pytest.mark.parametrize("day", ["2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24"])
def test_half_days_close_at_13_et_with_42_slots(day):
    s = cal.session_for(date.fromisoformat(day))
    assert s.early_close and s.close.astimezone(cal.ET).hour == 13 and s.n_slots == 42
    assert s.post_close.astimezone(cal.ET).hour == 17


def test_every_year_has_251_sessions_and_the_design_window_569():
    for year in (2026, 2027, 2028):
        assert len(sessions_between(date(year, 1, 1), date(year, 12, 31))) == 251, year
    # runtime.md: 569 sessions from 2026-09-28 to 2028-12-29, cross-checked with exchange_calendars
    assert len(sessions_between(date(2026, 9, 28), date(2028, 12, 29))) == 569


def test_open_days_next_to_observed_holidays():
    assert cal.session_for(date(2027, 12, 31)) is not None        # 2028-01-01 is a Saturday: not observed
    assert cal.session_for(date(2027, 7, 2)) is not None
    assert cal.session_for(date(2026, 9, 26)) is None               # Saturday


def test_open_and_close_follow_dst():
    assert cal.session_for(date(2026, 9, 28)).open == datetime(2026, 9, 28, 13, 30, tzinfo=UTC)   # EDT
    assert cal.session_for(date(2026, 11, 2)).open == datetime(2026, 11, 2, 14, 30, tzinfo=UTC)   # EST
    assert cal.session_for(date(2027, 3, 12)).close == datetime(2027, 3, 12, 21, 0, tzinfo=UTC)   # EST
    assert cal.session_for(date(2027, 3, 15)).close == datetime(2027, 3, 15, 20, 0, tzinfo=UTC)   # EDT
    assert cal.session_for(date(2026, 11, 27)).close == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize("when, phase", [
    ("2026-09-28T07:59:00Z", "closed"),     # 03:59 EDT
    ("2026-09-28T08:00:00Z", "pre"),        # 04:00 EDT
    ("2026-09-28T13:29:59Z", "pre"),
    ("2026-09-28T13:30:00Z", "regular"),
    ("2026-09-28T19:59:59Z", "regular"),
    ("2026-09-28T20:00:00Z", "post"),
    ("2026-09-29T00:00:00Z", "closed"),     # 20:00 EDT
    ("2026-11-02T14:29:00Z", "pre"),        # 09:29 EST
    ("2026-11-02T14:30:00Z", "regular"),
    ("2026-11-27T18:00:00Z", "post"),       # half day
    ("2026-11-27T22:00:00Z", "closed"),     # 17:00 EST, end of the half day's after-hours
    ("2026-11-26T15:00:00Z", "closed"),     # Thanksgiving
])
def test_phase_at(when, phase):
    assert cal.phase_at(datetime.fromisoformat(when.replace("Z", "+00:00")))[0] == phase


def test_slot_grid():
    s = cal.session_for(date(2026, 9, 28))
    assert s.n_slots == 78 and s.slot_start(0) == s.open_epoch and s.slot_start(77) == s.close_epoch - 300
    assert s.slot_of(s.open_epoch) == 0 and s.slot_of(s.open_epoch + 300 * 77) == 77
    assert s.slot_of(s.open_epoch + 300 * 78) is None
    assert s.slot_of(s.open_epoch - 300) is None and s.slot_of(s.open_epoch + 150) is None
    half = cal.session_for(date(2026, 11, 27))
    assert half.slot_of(half.open_epoch + 300 * 41) == 41 and half.slot_of(half.open_epoch + 300 * 42) is None


def test_previous_and_next_sessions_skip_holidays_and_keep_order():
    assert [s.day.isoformat() for s in cal.previous_sessions(date(2026, 9, 8), 3)] == ["2026-09-02", "2026-09-03", "2026-09-04"]
    assert [s.day.isoformat() for s in cal.next_sessions(date(2026, 11, 26), 2)] == ["2026-11-27", "2026-11-30"]
    assert cal.previous_sessions(date(2026, 9, 28), 1)[0].day == date(2026, 9, 25)
    assert cal.next_sessions(date(2026, 9, 28), 1)[0].day == date(2026, 9, 28)


def test_to_json():
    assert cal.session_for(date(2026, 11, 27)).to_json() == {
        "date": "2026-11-27", "pre_open": "2026-11-27T09:00:00Z", "open": "2026-11-27T14:30:00Z",
        "close": "2026-11-27T18:00:00Z", "post_close": "2026-11-27T22:00:00Z", "early_close": True}


def test_beyond_coverage_weekdays_count_as_sessions():
    assert not cal.calendar_known(date(2029, 1, 2)) and cal.calendar_known(cal.COVERED_THROUGH)
    assert cal.session_for(date(2029, 1, 1)) is not None       # fail open: a weekday past coverage
    assert cal.session_for(date(2029, 1, 6)) is None           # Saturday
