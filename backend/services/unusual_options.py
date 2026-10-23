"""Unusual options activity used to protect watchlist names from rotation.

One shared helper for the daily rotation (services/watchlist_manager.py), the
rotate-out gate and the watchlist protection badges (api/routes.py).

Rule: in the last COMPLETED session, the ticker's end-of-session options
snapshot shows call volume >= UNUSUAL_OPTIONS_MULTIPLE x the average
end-of-session call volume of the UNUSUAL_OPTIONS_LOOKBACK_SESSIONS sessions
before it. Only snapshots taken after the session close count ("end of
session"): the 09:35 ET options run sees a few minutes of volume and would
never compare fairly with a full day. At the 05:30 ET rotation the last
completed session is the previous trading day.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    UNUSUAL_OPTIONS_LOOKBACK_SESSIONS,
    UNUSUAL_OPTIONS_MIN_BASELINE,
    UNUSUAL_OPTIONS_MULTIPLE,
)
from db.models import OptionsSnapshot
from radar.calendar_nyse import ET, previous_sessions, session_for

logger = logging.getLogger(__name__)


def last_completed_session_date(now: datetime) -> date:
    """ET date of the latest NYSE session whose regular close has passed."""
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    today_et = now.astimezone(ET).date()
    today = session_for(today_et)
    if today is not None and now >= today.close:
        return today_et
    return previous_sessions(today_et, 1)[0].day


def _as_utc(ts: datetime) -> datetime:
    # SQLite (tests) hands TIMESTAMPTZ back naive; Postgres aware. Stored as UTC.
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def get_unusual_options_tickers(
    session: AsyncSession, now: datetime | None = None
) -> set[str]:
    """Tickers with unusual call volume in the last completed session.

    Never raises: a failure logs and returns an empty set (protection off).
    """
    try:
        now = now or datetime.now(tz=timezone.utc)
        ref_date = last_completed_session_date(now)
        baseline = previous_sessions(ref_date, UNUSUAL_OPTIONS_LOOKBACK_SESSIONS)
        sessions = {s.day: s for s in baseline}
        ref = session_for(ref_date)
        sessions[ref_date] = ref

        start = datetime.combine(baseline[0].day, time(0), tzinfo=ET).astimezone(timezone.utc)
        end = datetime.combine(ref_date + timedelta(days=1), time(0), tzinfo=ET).astimezone(timezone.utc)
        rows = (await session.execute(
            select(
                OptionsSnapshot.ticker,
                OptionsSnapshot.snapshot_time,
                OptionsSnapshot.total_call_volume,
            ).where(
                OptionsSnapshot.snapshot_time >= start,
                OptionsSnapshot.snapshot_time < end,
                OptionsSnapshot.total_call_volume.is_not(None),
            )
        )).all()

        # Last after-close snapshot per (ticker, session date).
        eod: dict[tuple[str, date], tuple[datetime, int]] = {}
        for ticker, ts, call_vol in rows:
            ts = _as_utc(ts)
            d = ts.astimezone(ET).date()
            s = sessions.get(d)
            if s is None or ts < s.close:
                continue
            key = (ticker, d)
            if key not in eod or ts > eod[key][0]:
                eod[key] = (ts, int(call_vol))

        by_ticker: dict[str, dict[date, int]] = {}
        for (ticker, d), (_, vol) in eod.items():
            by_ticker.setdefault(ticker, {})[d] = vol

        unusual: set[str] = set()
        for ticker, vols in by_ticker.items():
            last = vols.get(ref_date)
            if last is None:
                continue
            base = [v for d, v in vols.items() if d != ref_date]
            if len(base) < UNUSUAL_OPTIONS_MIN_BASELINE:
                continue
            avg = sum(base) / len(base)
            if avg > 0 and last >= UNUSUAL_OPTIONS_MULTIPLE * avg:
                unusual.add(ticker)

        if unusual:
            logger.info(
                "Unusual options activity on %s (>=%.0fx avg): %s",
                ref_date, UNUSUAL_OPTIONS_MULTIPLE, ", ".join(sorted(unusual)),
            )
        return unusual
    except Exception:
        logger.exception("Failed to compute unusual options tickers")
        return set()
