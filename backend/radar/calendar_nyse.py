"""NYSE trading calendar, stdlib only.

Source: NYSE holiday / early-close schedule. Cross-checked on 2026-09-27 against
exchange_calendars 4.13.2 (XNYS): identical holidays and early closes for 2026-2028.
Add the next year when NYSE publishes it, before COVERED_THROUGH: past it every weekday
counts as a session (see is_trading_day).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
COVERED_THROUGH = date(2028, 12, 31)

HOLIDAYS = frozenset(date.fromisoformat(d) for d in (
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027 (Juneteenth, Independence Day and Christmas observed on the nearest weekday)
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    # 2028 (New Year's Day falls on a Saturday: NYSE does not observe it)
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19",
    "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
))
EARLY_CLOSES = frozenset(date.fromisoformat(d) for d in (   # 13:00 ET close, 17:00 ET end of after-hours
    "2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24",
))
# Unscheduled closures (national day of mourning, weather). Edit by hand when announced.
EXTRA_CLOSURES: frozenset[date] = frozenset()

PRE_OPEN, REGULAR_OPEN = time(4, 0), time(9, 30)
CLOSE, EARLY_CLOSE = time(16, 0), time(13, 0)
POST_CLOSE, EARLY_POST_CLOSE = time(20, 0), time(17, 0)


@dataclass(frozen=True)
class Session:
    day: date
    pre_open: datetime      # aware UTC datetimes
    open: datetime
    close: datetime
    post_close: datetime
    early_close: bool

    def to_json(self) -> dict:
        iso = lambda t: t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"date": self.day.isoformat(), "pre_open": iso(self.pre_open), "open": iso(self.open),
                "close": iso(self.close), "post_close": iso(self.post_close), "early_close": self.early_close}

    # Regular-session 5-minute slot grid: slot j is the bar starting at open + 5*j minutes.
    @property
    def open_epoch(self) -> int:
        return int(self.open.timestamp())

    @property
    def close_epoch(self) -> int:
        return int(self.close.timestamp())

    @property
    def n_slots(self) -> int:
        return (self.close_epoch - self.open_epoch) // 300      # 78 normal day, 42 half day

    def slot_start(self, j: int) -> int:
        return self.open_epoch + 300 * j

    def slot_of(self, ts_epoch: int) -> int | None:
        """Slot index of a bar starting at ts_epoch, or None if it is not a regular-session grid bar."""
        off = ts_epoch - self.open_epoch
        if off < 0 or off % 300 or off // 300 >= self.n_slots:
            return None
        return off // 300


def _at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=ET).astimezone(UTC)


def calendar_known(d: date) -> bool:
    return d <= COVERED_THROUGH


def is_trading_day(d: date) -> bool:
    # Beyond COVERED_THROUGH we fail open (weekdays count as sessions): a scan on an
    # unknown holiday only finds no fresh bars, while a skipped session loses a day.
    return d.weekday() < 5 and d not in HOLIDAYS and d not in EXTRA_CLOSURES


def session_for(d: date) -> Session | None:
    if not is_trading_day(d):
        return None
    early = d in EARLY_CLOSES
    return Session(d, _at(d, PRE_OPEN), _at(d, REGULAR_OPEN),
                   _at(d, EARLY_CLOSE if early else CLOSE),
                   _at(d, EARLY_POST_CLOSE if early else POST_CLOSE), early)


def phase_at(now: datetime) -> tuple[str, Session | None]:
    """'pre' | 'regular' | 'post' | 'closed' for an aware datetime."""
    s = session_for(now.astimezone(ET).date())
    if s is None or now < s.pre_open or now >= s.post_close:
        return "closed", s
    if now < s.open:
        return "pre", s
    if now < s.close:
        return "regular", s
    return "post", s


def next_sessions(start: date, n: int) -> list[Session]:
    out, d = [], start
    while len(out) < n:
        s = session_for(d)
        if s:
            out.append(s)
        d += timedelta(days=1)
    return out


def previous_sessions(before: date, n: int) -> list[Session]:
    """The n trading sessions strictly before `before`, oldest first."""
    out, d = [], before - timedelta(days=1)
    while len(out) < n:
        s = session_for(d)
        if s:
            out.append(s)
        d -= timedelta(days=1)
    return out[::-1]


def scan_window(s: Session, scan_start_et: time) -> tuple[datetime, datetime]:
    """Scan window for one session: [max(pre_open, scan_start_et), post_close]."""
    return max(s.pre_open, _at(s.day, scan_start_et)), s.post_close


def parse_hhmm(v: str) -> time:
    h, m = v.split(":")
    return time(int(h), int(m))
