"""Daily clean IV capture (`iv_daily`) and the IV cost score built on it.

`run_iv_capture` (cron, 15:00 ET on trading days) measures IV30 / front IV /
HV20 / HV60 for the active watchlist, open positions, recent research
tickers and the Radar universe, one row per ticker per day. Quotes are live
then, so the quality gate in utils/iv_measure.py keeps junk out. A scheduled
run needs the regular session open (holidays, weekends and early-close days
skip it) and stops at the close. The pipeline and Research read the latest
row through `score_for_ticker`; a ticker with no recent row is measured on
demand, during the regular session only (otherwise the score is "IV unknown").
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import async_session
from db.models import EarningsCalendar, IvDaily, Position, ResearchResult, Watchlist
from radar import calendar_nyse as cal
from utils.data_sources import DataSourceClient
from utils.iv_measure import iv_from_chains, realised_vol
from utils.iv_score import EVENT_HORIZON_DAYS, iv_score

logger = logging.getLogger(__name__)

HISTORY_DAYS = 400          # calendar days loaded for the 252-trading-day window
MAX_AGE_DAYS = 7            # an older reading is not "today's IV"
RESEARCH_LOOKBACK_DAYS = 30


def et_today() -> date:
    return datetime.now(cal.ET).date()


def capture_skip_reason(now: datetime) -> str | None:
    """Why a capture at `now` would read stale quotes, else None (the regular session is open)."""
    if not cal.is_trading_day(now.astimezone(cal.ET).date()):
        return "not a trading day"
    phase, _ = cal.phase_at(now)
    return None if phase == "regular" else "regular session closed"


def measure(client: DataSourceClient, ticker: str, today: date) -> dict | None:
    """One clean reading for `ticker` (blocking yfinance calls), or None without a valid IV30."""
    chain = client.get_options_chain(ticker, mode="iv30")
    closes = [c for _, c in client.get_daily_closes(ticker, "4mo")]
    spot = chain.get("current_price") or (closes[-1] if closes else None)
    iv = iv_from_chains(chain.get("chains") or {}, spot, today)
    if not iv["iv30"]:
        return None
    return {
        "ticker": ticker, "day": today, "iv30": iv["iv30"], "iv_front": iv["iv_front"],
        "front_dte": iv["front_dte"], "hv20": realised_vol(closes, 20), "hv60": realised_vol(closes, 60),
        "spot": spot,
    }


def _dec(v: float | None, nd: int) -> Decimal | None:
    return None if v is None else Decimal(str(round(v, nd)))


def reading_values(row: dict) -> dict:
    """iv_daily column values of one reading (fractions rounded to the column scale)."""
    return {
        "ticker": row["ticker"], "day": row["day"], "iv30": _dec(row["iv30"], 4),
        "iv_front": _dec(row["iv_front"], 4), "front_dte": row["front_dte"],
        "hv20": _dec(row["hv20"], 4), "hv60": _dec(row["hv60"], 4), "spot": _dec(row["spot"], 4),
        "source": row.get("source", "yahoo"), "captured_at": datetime.now(timezone.utc),
    }


async def upsert_reading(session: AsyncSession, row: dict) -> None:
    values = reading_values(row)
    stmt = pg_insert(IvDaily).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "day"], set_={k: v for k, v in values.items() if k not in ("ticker", "day")},
    )
    await session.execute(stmt)


async def iv_universe(session: AsyncSession) -> list[str]:
    """Watchlist, open positions, recent research tickers and the Radar universe."""
    from radar.universe import scan_symbols

    cutoff = datetime.now(timezone.utc) - timedelta(days=RESEARCH_LOOKBACK_DAYS)
    tickers = set(scan_symbols())
    for q in (
        select(Watchlist.ticker).where(Watchlist.is_active.is_(True)),
        select(Position.ticker).where(Position.status == "OPEN"),
        select(ResearchResult.ticker).where(ResearchResult.analyzed_at >= cutoff),
    ):
        tickers.update(t.upper() for t in (await session.execute(q)).scalars() if t)
    return sorted(tickers)


async def run_iv_capture(tickers: list[str] | None = None, *, scheduled: bool = True) -> dict[str, Any]:
    """Cron entry point: one iv_daily row per ticker with a clean reading today."""
    from services.run_log import STATUS_FAILED, STATUS_SUCCESS, record_run_finish, record_run_start

    today = et_today()
    reason = capture_skip_reason(datetime.now(timezone.utc))
    if reason and scheduled:
        logger.info("iv capture skipped: %s", reason)
        return {"skipped": reason}
    run_id = await record_run_start(phase="iv_capture", user_id=None, meta={"scheduled": scheduled})
    summary: dict[str, Any] = {"tickers": 0, "stored": 0, "no_reading": 0, "errors": 0, "after_close": 0}
    client = DataSourceClient()
    loop = asyncio.get_running_loop()
    error: str | None = None
    session = async_session()
    try:
        names = tickers or await iv_universe(session)
        summary["tickers"] = len(names)
        for i, ticker in enumerate(names):
            if scheduled and capture_skip_reason(datetime.now(timezone.utc)):
                summary["after_close"] = len(names) - i     # quotes go stale at the close: stop
                break
            try:
                row = await loop.run_in_executor(None, measure, client, ticker, today)
            except Exception:
                summary["errors"] += 1
                logger.debug("iv capture failed for %s", ticker, exc_info=True)
                continue
            if row is None:
                summary["no_reading"] += 1
                continue
            await upsert_reading(session, row)
            await session.commit()
            summary["stored"] += 1
    except Exception as exc:  # noqa: BLE001 - logged and recorded on the run row
        error = str(exc)
        logger.exception("iv capture failed")
        await session.rollback()
    finally:
        await session.close()
    await record_run_finish(run_id, status=STATUS_FAILED if error else STATUS_SUCCESS,
                            error_message=error, meta=summary)
    logger.info("iv capture: %s", summary)
    return summary


async def load_histories(session: AsyncSession, tickers: list[str], before: date) -> dict[str, list[float]]:
    """{ticker: clean IV30 series before `before`, oldest first}."""
    if not tickers:
        return {}
    rows = await session.execute(
        select(IvDaily.ticker, IvDaily.iv30)
        .where(IvDaily.ticker.in_(tickers), IvDaily.day < before,
               IvDaily.day >= before - timedelta(days=HISTORY_DAYS), IvDaily.iv30.isnot(None))
        .order_by(IvDaily.ticker, IvDaily.day)
    )
    out: dict[str, list[float]] = {t: [] for t in tickers}
    for t, v in rows.all():
        out[t].append(float(v))
    return out


async def iv30_change(session: AsyncSession, ticker: str, days_back: tuple[int, int]) -> float | None:
    """Relative change of IV30 between the latest reading and one `days_back` (min, max) days earlier."""
    rows = (await session.execute(
        select(IvDaily.day, IvDaily.iv30).where(IvDaily.ticker == ticker, IvDaily.iv30.isnot(None))
        .order_by(IvDaily.day.desc()).limit(15)
    )).all()
    if not rows:
        return None
    last_day, last = rows[0]
    lo, hi = days_back
    older = [v for d, v in rows[1:] if lo <= (last_day - d).days <= hi]
    if not older or not older[0]:
        return None
    return float(last) / float(older[0]) - 1.0


async def next_earnings(session: AsyncSession, ticker: str, after: date, horizon_days: int) -> date | None:
    row = await session.execute(
        select(EarningsCalendar.earnings_date)
        .where(EarningsCalendar.ticker == ticker, EarningsCalendar.earnings_date > after,
               EarningsCalendar.earnings_date <= after + timedelta(days=horizon_days))
        .order_by(EarningsCalendar.earnings_date).limit(1)
    )
    return row.scalar_one_or_none()


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


async def score_for_ticker(
    session: AsyncSession, client: DataSourceClient, ticker: str, *, measure_if_missing: bool = True,
) -> dict | None:
    """The IV cost score from the latest clean reading (measured now when none is recent)."""
    today = et_today()
    row = (await session.execute(
        select(IvDaily).where(IvDaily.ticker == ticker, IvDaily.iv30.isnot(None),
                              IvDaily.day >= today - timedelta(days=MAX_AGE_DAYS))
        .order_by(IvDaily.day.desc()).limit(1)
    )).scalar_one_or_none()
    if row is None and measure_if_missing and capture_skip_reason(datetime.now(timezone.utc)) is None:
        loop = asyncio.get_running_loop()
        try:
            fresh = await loop.run_in_executor(None, measure, client, ticker, today)
        except Exception:
            logger.debug("on-demand iv measure failed for %s", ticker, exc_info=True)
            fresh = None
        if fresh:
            await upsert_reading(session, fresh)
            await session.flush()
            row = (await session.execute(
                select(IvDaily).where(IvDaily.ticker == ticker, IvDaily.day == today)
            )).scalar_one_or_none()
    if row is None:
        return None
    history = (await load_histories(session, [ticker], row.day))[ticker]
    earnings = await next_earnings(session, ticker, row.day, EVENT_HORIZON_DAYS)
    return iv_score(
        _f(row.iv30), _f(row.hv20), history, iv_front=_f(row.iv_front), hv60=_f(row.hv60),
        earnings_date=earnings, as_of=row.day.isoformat(),
    )
