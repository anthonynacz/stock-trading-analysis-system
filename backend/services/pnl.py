"""Portfolio P&L snapshot worker.

Runs nightly via APScheduler (post-market-close). For each active user
the worker:
  1. Refreshes current_price on all OPEN positions via yfinance.
  2. Computes unrealized P&L (sum across OPEN positions).
  3. Computes realized P&L for positions closed today (closed_at::date
     == snapshot_date).
  4. UPSERTs one row into portfolio_pnl_snapshot keyed by (user_id, date).

Re-running the same day is idempotent: the unique constraint
(user_id, snapshot_date) drives an ON CONFLICT update.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import async_session
from db.models import PortfolioPnlSnapshot, Position, User
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)


async def _refresh_user_positions(
    session: AsyncSession, user_id: int, client: DataSourceClient
) -> None:
    """Mutate OPEN positions' current_price for user_id in-place using yfinance.
    Mirrors the logic in /api/positions/refresh-all but is callable from a
    worker. Failures per-ticker are swallowed silently."""
    pos_q = await session.execute(
        select(Position).where(
            Position.user_id == user_id,
            Position.status == "OPEN",
        )
    )
    positions = list(pos_q.scalars())
    if not positions:
        return

    stock_tickers = sorted({p.ticker for p in positions if p.position_type == "STOCK"})
    option_tickers = sorted({p.ticker for p in positions if p.position_type in ("CALL", "PUT")})

    loop = asyncio.get_event_loop()
    stock_prices: dict[str, float] = {}
    chains: dict[str, dict] = {}

    for tk in stock_tickers:
        try:
            data = await loop.run_in_executor(None, client.get_stock_data, tk)
            p = (data or {}).get("price")
            if p is not None:
                stock_prices[tk] = float(p)
        except Exception:
            logger.exception("pnl: stock fetch failed for %s", tk)

    for tk in option_tickers:
        try:
            ch = await loop.run_in_executor(
                None, lambda t=tk: client.get_options_chain(t, mode="spread"),
            )
            chains[tk] = ch or {}
        except Exception:
            logger.exception("pnl: chain fetch failed for %s", tk)

    for pos in positions:
        if pos.position_type == "STOCK":
            price = stock_prices.get(pos.ticker)
            if price is not None:
                pos.current_price = Decimal(str(round(price, 2)))
            continue
        # CALL / PUT — spread chain first, per-expiry fallback.
        if not pos.expiry or not pos.strike_price:
            continue
        expiry_str = pos.expiry.isoformat()
        side = "calls" if pos.position_type == "CALL" else "puts"
        target = float(pos.strike_price)
        last_price: float | None = None

        ch = chains.get(pos.ticker, {})
        chain_map = ch.get("chains", {}) if isinstance(ch, dict) else {}
        exp_chain = chain_map.get(expiry_str) if chain_map else None
        if exp_chain:
            for c in exp_chain.get(side, []):
                strike = c.get("strike")
                if strike is None:
                    continue
                if abs(float(strike) - target) < 0.01:
                    lp = c.get("lastPrice")
                    if lp is not None and float(lp) > 0:
                        last_price = float(lp)
                    break

        if last_price is None:
            try:
                last_price = await loop.run_in_executor(
                    None,
                    client.get_option_contract_price,
                    pos.ticker, expiry_str, target, side,
                )
            except Exception:
                logger.exception("pnl: contract lookup failed for %s %s %s", pos.ticker, expiry_str, target)

        if last_price is not None and last_price > 0:
            pos.current_price = Decimal(str(round(last_price, 2)))


def _pnl_for_position(pos: Position) -> Decimal:
    """Unrealized P&L for a single OPEN position. Mirrors the on-the-fly
    computation in api/routes._compute_position_fields."""
    if pos.current_price is None:
        return Decimal("0")
    current = pos.current_price
    if pos.position_type == "STOCK":
        return (current - pos.entry_price) * pos.quantity
    # CALL / PUT: P&L vs premium paid (or entry if no premium captured)
    basis = pos.premium_paid if pos.premium_paid is not None else pos.entry_price
    return (current - basis) * pos.quantity * 100


async def _snapshot_user(session: AsyncSession, user: User, today: date) -> dict[str, Any]:
    """Compute today's P&L row for one user and UPSERT it. Returns a summary
    dict for logging."""
    open_q = await session.execute(
        select(Position).where(
            Position.user_id == user.id,
            Position.status == "OPEN",
        )
    )
    open_positions = list(open_q.scalars())
    unrealized = sum((_pnl_for_position(p) for p in open_positions), Decimal("0"))

    # Realized today: positions whose closed_at falls on `today`
    today_start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    today_end = datetime.combine(today, datetime.max.time(), tzinfo=timezone.utc)
    closed_today_q = await session.execute(
        select(Position).where(
            Position.user_id == user.id,
            Position.status == "CLOSED",
            Position.closed_at.is_not(None),
            Position.closed_at.between(today_start, today_end),
        )
    )
    closed_today = list(closed_today_q.scalars())
    realized_today = sum(
        (p.realized_pnl for p in closed_today if p.realized_pnl is not None),
        Decimal("0"),
    )

    # Cumulative realized — sum over ALL closed positions for this user
    cum_q = await session.execute(
        select(Position.realized_pnl).where(
            Position.user_id == user.id,
            Position.status == "CLOSED",
            Position.realized_pnl.is_not(None),
        )
    )
    realized_cum = sum((row[0] for row in cum_q.all()), Decimal("0"))

    stmt = pg_insert(PortfolioPnlSnapshot).values(
        user_id=user.id,
        snapshot_date=today,
        unrealized_pnl=unrealized.quantize(Decimal("0.01")),
        realized_pnl_today=realized_today.quantize(Decimal("0.01")),
        realized_pnl_cumulative=realized_cum.quantize(Decimal("0.01")),
        open_count=len(open_positions),
        closed_count_today=len(closed_today),
    ).on_conflict_do_update(
        constraint="uq_pnl_snapshot_user_date",
        set_=dict(
            unrealized_pnl=unrealized.quantize(Decimal("0.01")),
            realized_pnl_today=realized_today.quantize(Decimal("0.01")),
            realized_pnl_cumulative=realized_cum.quantize(Decimal("0.01")),
            open_count=len(open_positions),
            closed_count_today=len(closed_today),
        ),
    )
    await session.execute(stmt)

    return {
        "user_id": user.id,
        "unrealized": float(unrealized),
        "realized_today": float(realized_today),
        "open_count": len(open_positions),
        "closed_today": len(closed_today),
    }


async def run_pnl_snapshot() -> dict[str, Any]:
    """APScheduler entry. Refresh prices + write today's P&L row per user."""
    from services.run_log import (
        STATUS_FAILED,
        STATUS_SUCCESS,
        record_run_finish,
        record_run_start,
    )

    global_run_id = await record_run_start(phase="pnl_snapshot", user_id=None)
    started = datetime.now(tz=timezone.utc)
    today = started.date()
    summary: dict[str, Any] = {
        "started_at": started.isoformat(),
        "users_processed": 0,
        "errors": 0,
    }
    session = async_session()
    client = DataSourceClient()
    try:
        users_q = await session.execute(select(User).where(User.is_active.is_(True)))
        users = list(users_q.scalars())

        for user in users:
            try:
                await _refresh_user_positions(session, user.id, client)
                row_summary = await _snapshot_user(session, user, today)
                await session.commit()
                summary["users_processed"] += 1
                # Per-user log row (lets a user see "my snapshot ran at X")
                u_run = await record_run_start(phase="pnl_snapshot", user_id=user.id)
                await record_run_finish(u_run, status=STATUS_SUCCESS, meta=row_summary)
            except Exception as exc:
                logger.exception("pnl_snapshot failed for user %s", user.id)
                summary["errors"] += 1
                await session.rollback()
                u_run = await record_run_start(phase="pnl_snapshot", user_id=user.id)
                await record_run_finish(
                    u_run,
                    status=STATUS_FAILED,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
    finally:
        client.close()
        await session.close()

    summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    logger.info("P&L snapshot done: %s", summary)
    await record_run_finish(
        global_run_id,
        status=STATUS_SUCCESS if summary["errors"] == 0 else STATUS_FAILED,
        meta={k: v for k, v in summary.items() if k not in {"started_at", "finished_at"}},
    )
    return summary
