"""Recommendation engine — stacks signals from all services into scored recommendations."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    AnalystRating,
    DeepOptionsAnalysis,
    EarningsCalendar,
    MarketNews,
    OptionsSnapshot,
    Recommendation,
    Sector,
    SuggestedOption,
    UniverseStock,
)
from services.analyst_tracker import AnalystTracker
from services.earnings_calendar import EarningsCalendarService
from services.news_scanner import NewsScanner
from services.options_analyzer import OptionsAnalyzer
from utils.data_sources import DataSourceClient
from utils.geopolitical import detect_and_score

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """Aggregates signals from all services and produces daily recommendations."""

    def __init__(
        self,
        session: AsyncSession,
        data_client: DataSourceClient,
        analyst_tracker: AnalystTracker,
        earnings_service: EarningsCalendarService,
        news_scanner: NewsScanner,
        options_analyzer: OptionsAnalyzer,
    ) -> None:
        self._session = session
        self._data_client = data_client
        self._analyst_tracker = analyst_tracker
        self._earnings_service = earnings_service
        self._news_scanner = news_scanner
        self._options_analyzer = options_analyzer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_recommendations(
        self, tickers: list[str]
    ) -> list[Recommendation]:
        """Generate and persist recommendations for *tickers*.

        For each ticker the engine:
        1. Gathers today's signals from all services (DB queries).
        2. Stacks signals into a conviction score.
        3. Classifies an action and builds a rationale.
        4. If actionable, selects options contracts.
        5. Upserts the recommendation (delete existing for same date+ticker).

        Returns recommendations sorted by conviction_score DESC.
        """
        today = date.today()
        results: list[Recommendation] = []

        for ticker in tickers:
            try:
                rec = await self._generate_single(ticker, today)
                if rec is not None:
                    results.append(rec)
            except Exception:
                logger.exception(
                    "Failed to generate recommendation for %s", ticker
                )

        # Sort by conviction descending
        results.sort(
            key=lambda r: float(r.conviction_score or 0), reverse=True
        )
        return results

    async def get_todays_recommendations(self) -> list[Recommendation]:
        """Query today's recommendations sorted by conviction_score DESC."""
        today = date.today()
        stmt = (
            select(Recommendation)
            .where(Recommendation.recommendation_date == today)
            .options(selectinload(Recommendation.suggested_options))
            .order_by(Recommendation.conviction_score.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # On-demand research (no persistence to recommendations table)
    # ------------------------------------------------------------------

    async def analyze_single(self, ticker: str) -> dict[str, Any]:
        """Run full analysis pipeline for one ticker.

        Returns a data dict with all scored fields. Does NOT persist to the
        ``recommendations`` table — the caller is responsible for storage.
        """
        today = date.today()

        # ── 1. Gather signals data ──────────────────────────────────────
        loop = asyncio.get_event_loop()
        stock_data: dict = await loop.run_in_executor(
            None, self._data_client.get_stock_data, ticker
        )
        technicals: dict = await loop.run_in_executor(
            None, self._data_client.get_technical_indicators, ticker
        )
        benchmark: dict = await loop.run_in_executor(
            None, self._data_client.get_market_benchmark
        )

        ratings = await self._analyst_tracker.get_recent_ratings(
            ticker=ticker, hours=48
        )
        catalyst_window = await self._earnings_service.get_catalyst_window(ticker)
        news_sentiment = await self._news_scanner.get_sentiment_summary(
            ticker=ticker, hours=48
        )
        options_snapshot = await self._options_analyzer.get_latest_snapshot(ticker)
        greeks_summary = await self._options_analyzer.get_chain_greeks_summary(
            ticker, float(stock_data.get("price", 0))
        )

        signals_data: dict[str, Any] = {
            "ratings": ratings,
            "catalyst_window": catalyst_window,
            "news_sentiment": news_sentiment,
            "options_snapshot": options_snapshot,
            "greeks_summary": greeks_summary,
            "stock_data": stock_data,
            "technicals": technicals,
            "benchmark": benchmark,
        }

        # ── 2. Stack signals ────────────────────────────────────────────
        conviction_score, signal_details = await self._stack_signals(
            ticker, signals_data
        )

        # ── 3. Classify action ──────────────────────────────────────────
        action = self._classify_action(conviction_score)

        # ── 4. Entry strategy ───────────────────────────────────────────
        current_price = stock_data.get("price")
        stock_moved_pct = self._calc_stock_move_pct(stock_data)
        action, stale_move_gated = self._apply_stale_move_gate(
            action, stock_moved_pct, signal_details
        )
        entry_strategy = self._determine_entry_strategy(
            conviction_score, catalyst_window, stock_moved_pct, signal_details
        )
        if stale_move_gated:
            entry_strategy = "WAIT"

        # ── 5. Rationale ───────────────────────────────────────────────
        rationale = await self._generate_rationale(
            ticker, action, conviction_score, signal_details, stock_data,
            entry_strategy=entry_strategy,
        )

        # ── 6. Risk level ──────────────────────────────────────────────
        iv_rank = float(options_snapshot.iv_rank or 0) if options_snapshot else 0.0
        risk_level = self._determine_risk_level(conviction_score, iv_rank)

        # ── 7. Target / stop-loss ──────────────────────────────────────
        target_price = stock_data.get("avg_analyst_target")
        stop_loss_price = self._calc_stop_loss(current_price, action)
        catalyst_type = self._infer_catalyst_type(signal_details)

        # ── 8. Select options contracts if actionable ──────────────────
        suggested_contracts: list[dict] = []
        if action in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL"):
            direction = "BULLISH" if action in ("BUY", "STRONG_BUY") else "BEARISH"
            earnings_date = (
                catalyst_window.get("earnings_date") if catalyst_window else None
            )
            suggested_contracts = await self._options_analyzer.select_optimal_contracts(
                ticker=ticker,
                direction=direction,
                iv_rank=iv_rank,
                current_price=float(current_price) if current_price else 0.0,
                catalyst_type=catalyst_type,
                earnings_date=earnings_date,
            )

        # ── 9. Build options snapshot dict ─────────────────────────────
        options_dict = None
        if options_snapshot:
            options_dict = {
                "stock_price": float(options_snapshot.stock_price) if options_snapshot.stock_price else None,
                "iv_rank": float(options_snapshot.iv_rank) if options_snapshot.iv_rank else None,
                "iv_percentile": float(options_snapshot.iv_percentile) if options_snapshot.iv_percentile else None,
                "put_call_ratio": float(options_snapshot.put_call_ratio) if options_snapshot.put_call_ratio else None,
                "total_call_volume": options_snapshot.total_call_volume,
                "total_put_volume": options_snapshot.total_put_volume,
                "unusual_activity": options_snapshot.unusual_activity,
                "unusual_activity_detail": options_snapshot.unusual_activity_detail,
            }

        logger.info(
            "Research analysis complete: %s %s (conviction %.1f, %d signals)",
            ticker, action, conviction_score, len(signal_details),
        )

        return {
            "ticker": ticker,
            "action": action,
            "conviction_score": round(conviction_score, 2),
            "signal_count": len(signal_details),
            "signals": signal_details,
            "rationale": rationale,
            "catalyst_type": catalyst_type,
            "entry_strategy": entry_strategy,
            "exit_rules": None,
            "risk_level": risk_level,
            "current_price": current_price,
            "target_price": target_price,
            "stop_loss_price": stop_loss_price,
            "options_data": options_dict,
            "suggested_contracts": suggested_contracts,
        }

    # ------------------------------------------------------------------
    # Single-ticker pipeline
    # ------------------------------------------------------------------

    async def _generate_single(
        self, ticker: str, today: date
    ) -> Recommendation | None:
        """Run the full pipeline for one ticker."""

        # ── 1. Gather signals data ──────────────────────────────────────
        loop = asyncio.get_event_loop()
        stock_data: dict = await loop.run_in_executor(
            None, self._data_client.get_stock_data, ticker
        )
        technicals: dict = await loop.run_in_executor(
            None, self._data_client.get_technical_indicators, ticker
        )
        benchmark: dict = await loop.run_in_executor(
            None, self._data_client.get_market_benchmark
        )

        ratings = await self._analyst_tracker.get_recent_ratings(
            ticker=ticker, hours=48
        )
        catalyst_window = await self._earnings_service.get_catalyst_window(ticker)
        news_sentiment = await self._news_scanner.get_sentiment_summary(
            ticker=ticker, hours=48
        )
        options_snapshot = await self._options_analyzer.get_latest_snapshot(ticker)
        greeks_summary = await self._options_analyzer.get_chain_greeks_summary(
            ticker, float(stock_data.get("price", 0))
        )

        signals_data: dict[str, Any] = {
            "ratings": ratings,
            "catalyst_window": catalyst_window,
            "news_sentiment": news_sentiment,
            "options_snapshot": options_snapshot,
            "greeks_summary": greeks_summary,
            "stock_data": stock_data,
            "technicals": technicals,
            "benchmark": benchmark,
        }

        # ── 2. Stack signals ────────────────────────────────────────────
        conviction_score, signal_details = await self._stack_signals(
            ticker, signals_data
        )

        # ── 3. Classify action ──────────────────────────────────────────
        action = self._classify_action(conviction_score)

        # ── 4. Entry strategy ───────────────────────────────────────────
        current_price = stock_data.get("price")
        stock_moved_pct = self._calc_stock_move_pct(stock_data)
        action, stale_move_gated = self._apply_stale_move_gate(
            action, stock_moved_pct, signal_details
        )
        entry_strategy = self._determine_entry_strategy(
            conviction_score, catalyst_window, stock_moved_pct, signal_details
        )
        if stale_move_gated:
            entry_strategy = "WAIT"

        # ── 5. Rationale ───────────────────────────────────────────────
        rationale = await self._generate_rationale(
            ticker, action, conviction_score, signal_details, stock_data,
            entry_strategy=entry_strategy,
        )

        # ── 6. Risk level ──────────────────────────────────────────────
        iv_rank = float(options_snapshot.iv_rank or 0) if options_snapshot else 0.0
        risk_level = self._determine_risk_level(conviction_score, iv_rank)

        # ── 7. Target / stop-loss ──────────────────────────────────────
        target_price = stock_data.get("avg_analyst_target")
        stop_loss_price = self._calc_stop_loss(current_price, action)

        # Determine catalyst type from the strongest signal
        catalyst_type = self._infer_catalyst_type(signal_details)

        # ── 8. Select options contracts if actionable ──────────────────
        suggested_contracts: list[dict] = []
        if action in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL"):
            direction = "BULLISH" if action in ("BUY", "STRONG_BUY") else "BEARISH"
            earnings_date = (
                catalyst_window.get("earnings_date") if catalyst_window else None
            )
            suggested_contracts = await self._options_analyzer.select_optimal_contracts(
                ticker=ticker,
                direction=direction,
                iv_rank=iv_rank,
                current_price=float(current_price) if current_price else 0.0,
                catalyst_type=catalyst_type,
                earnings_date=earnings_date,
            )

        # ── 9. Upsert recommendation ──────────────────────────────────
        # Capture prior values BEFORE deletion so the new row can store them
        # for revision-diff display in the UI. Single-row-per-(date,ticker)
        # invariant is preserved; only the most recent prior is kept.
        existing_q = await self._session.execute(
            select(Recommendation).where(
                Recommendation.recommendation_date == today,
                Recommendation.ticker == ticker,
            )
        )
        existing = existing_q.scalars().first()
        if existing is not None:
            prior_action = existing.action
            prior_conviction = existing.conviction_score
            revision_number = (existing.revision_number or 0) + 1
            revised_at = datetime.now(tz=timezone.utc)
        else:
            prior_action = None
            prior_conviction = None
            revision_number = 0
            revised_at = None

        await self._session.execute(
            delete(SuggestedOption).where(
                SuggestedOption.recommendation_id.in_(
                    select(Recommendation.id).where(
                        Recommendation.recommendation_date == today,
                        Recommendation.ticker == ticker,
                    )
                )
            )
        )
        await self._session.execute(
            delete(Recommendation).where(
                Recommendation.recommendation_date == today,
                Recommendation.ticker == ticker,
            )
        )

        rec = Recommendation(
            recommendation_date=today,
            ticker=ticker,
            action=action,
            conviction_score=Decimal(str(round(conviction_score, 2))),
            signal_count=len(signal_details),
            signals=signal_details,
            rationale=rationale,
            catalyst_type=catalyst_type,
            entry_strategy=entry_strategy,
            risk_level=risk_level,
            current_price=(
                Decimal(str(current_price)) if current_price is not None else None
            ),
            target_price=(
                Decimal(str(target_price)) if target_price is not None else None
            ),
            stop_loss_price=(
                Decimal(str(stop_loss_price))
                if stop_loss_price is not None
                else None
            ),
            prior_action=prior_action,
            prior_conviction_score=prior_conviction,
            revision_number=revision_number,
            revised_at=revised_at,
        )
        self._session.add(rec)
        await self._session.flush()  # populate rec.id

        # ── 10. Create SuggestedOption rows ────────────────────────────
        for contract in suggested_contracts:
            opt = SuggestedOption(
                recommendation_id=rec.id,
                contract_type=contract["contract_type"],
                strike=contract["strike"],
                expiry=contract["expiry"],
                premium_estimate=contract.get("premium_estimate"),
                delta_estimate=contract.get("delta_estimate"),
                strategy=contract.get("strategy"),
                strategy_rationale=contract.get("strategy_rationale"),
                days_to_expiry=contract.get("days_to_expiry"),
                breakeven_price=contract.get("breakeven_price"),
            )
            self._session.add(opt)

        await self._session.commit()

        logger.info(
            "Recommendation generated: %s %s (conviction %.1f, %d signals, %d contracts)",
            ticker,
            action,
            conviction_score,
            len(signal_details),
            len(suggested_contracts),
        )
        return rec

    # ------------------------------------------------------------------
    # Historical volume helpers
    # ------------------------------------------------------------------

    async def _get_historical_avg_volume(
        self, ticker: str, days: int = 20
    ) -> tuple[float, float]:
        """Return (avg_call_volume, avg_put_volume) from past options snapshots.

        Excludes today's snapshot so the comparison is against a true
        historical baseline.  Returns (0, 0) if no history exists.
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            stmt = (
                select(
                    sa_func.avg(OptionsSnapshot.total_call_volume),
                    sa_func.avg(OptionsSnapshot.total_put_volume),
                )
                .where(
                    OptionsSnapshot.ticker == ticker,
                    OptionsSnapshot.snapshot_time >= cutoff,
                    OptionsSnapshot.snapshot_time < today_start,
                )
            )
            result = await self._session.execute(stmt)
            row = result.one()
            avg_call = float(row[0] or 0)
            avg_put = float(row[1] or 0)
            return avg_call, avg_put
        except Exception:
            logger.exception("_get_historical_avg_volume failed for %s", ticker)
            return 0.0, 0.0

    # ------------------------------------------------------------------
    # Analyst revision cluster (14-day window)
    # ------------------------------------------------------------------

    async def _analyst_revision_cluster(self, ticker: str) -> dict:
        """Count bullish/bearish analyst actions from T1/T2 firms in last 14 days.

        Bullish action = upgrade, PT raise >5%.
        Bearish action = downgrade, PT cut >5%.
        Only T1/T2 firms count (firm_tier in {1, 2}).
        Each distinct firm counts at most once per direction.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=14)
        stmt = (
            select(AnalystRating)
            .where(
                AnalystRating.ticker == ticker,
                AnalystRating.published_at >= cutoff,
            )
            .order_by(AnalystRating.published_at.desc())
        )
        result = await self._session.execute(stmt)
        ratings = list(result.scalars().all())

        bullish_firms: set[str] = set()
        bearish_firms: set[str] = set()
        for r in ratings:
            if (r.firm_tier or 3) > 2:
                continue
            firm = r.analyst_firm or "Unknown"
            prev = (r.previous_rating or "").upper()
            new = (r.new_rating or "").upper()

            if self._is_upgrade(prev, new):
                bullish_firms.add(firm)
                continue
            if self._is_downgrade(prev, new):
                bearish_firms.add(firm)
                continue

            if r.previous_pt is not None and r.new_pt is not None:
                prev_pt = float(r.previous_pt)
                new_pt = float(r.new_pt)
                if prev_pt > 0:
                    pct = ((new_pt - prev_pt) / prev_pt) * 100.0
                    if pct > 5:
                        bullish_firms.add(firm)
                    elif pct < -5:
                        bearish_firms.add(firm)

        return {
            "bullish_count": len(bullish_firms),
            "bearish_count": len(bearish_firms),
            "bullish_firms": sorted(bullish_firms),
            "bearish_firms": sorted(bearish_firms),
        }

    # ------------------------------------------------------------------
    # IV rank trajectory (5-day delta)
    # ------------------------------------------------------------------

    async def _iv_rank_trajectory(self, ticker: str) -> float | None:
        """Return IV-rank delta over last ~5 days (today - baseline 5d ago).

        Uses the earliest snapshot within the last 7 days (excluding today)
        as baseline, latest snapshot as current.  Returns None if either
        missing.
        """
        try:
            now = datetime.now(tz=timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            window_start = now - timedelta(days=7)

            stmt = (
                select(OptionsSnapshot.iv_rank, OptionsSnapshot.snapshot_time)
                .where(
                    OptionsSnapshot.ticker == ticker,
                    OptionsSnapshot.snapshot_time >= window_start,
                    OptionsSnapshot.snapshot_time < today_start,
                    OptionsSnapshot.iv_rank.isnot(None),
                )
                .order_by(OptionsSnapshot.snapshot_time.asc())
            )
            result = await self._session.execute(stmt)
            rows = list(result.all())
            if not rows:
                return None

            baseline = float(rows[0][0])

            # Latest (today if present, else most-recent pre-today)
            latest_stmt = (
                select(OptionsSnapshot.iv_rank)
                .where(
                    OptionsSnapshot.ticker == ticker,
                    OptionsSnapshot.iv_rank.isnot(None),
                )
                .order_by(OptionsSnapshot.snapshot_time.desc())
                .limit(1)
            )
            latest_result = await self._session.execute(latest_stmt)
            latest_row = latest_result.first()
            if latest_row is None or latest_row[0] is None:
                return None
            latest = float(latest_row[0])
            return latest - baseline
        except Exception:
            logger.exception("_iv_rank_trajectory failed for %s", ticker)
            return None

    async def _rolling_call_volume_3d(self, ticker: str) -> float:
        """Return average call volume over last 3 snapshots (excluding today)."""
        try:
            now = datetime.now(tz=timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            stmt = (
                select(OptionsSnapshot.total_call_volume)
                .where(
                    OptionsSnapshot.ticker == ticker,
                    OptionsSnapshot.snapshot_time < today_start,
                    OptionsSnapshot.total_call_volume.isnot(None),
                )
                .order_by(OptionsSnapshot.snapshot_time.desc())
                .limit(3)
            )
            result = await self._session.execute(stmt)
            vols = [float(r[0]) for r in result.all()]
            if not vols:
                return 0.0
            return sum(vols) / len(vols)
        except Exception:
            logger.exception("_rolling_call_volume_3d failed for %s", ticker)
            return 0.0

    async def _iv_rank_5d_change(self, ticker: str, current_rank: float) -> float | None:
        """Return the change in iv_rank (in percentage points) over the last 5
        trading days, or None if no comparable historical row exists.

        Uses options_snapshots history: picks the row closest to 5 trading
        days ago (within a 4–8 day window to handle weekends / off-days).
        Returns ``current_rank - prior_rank``.
        """
        try:
            cutoff_min = datetime.now(tz=timezone.utc) - timedelta(days=8)
            cutoff_max = datetime.now(tz=timezone.utc) - timedelta(days=4)
            stmt = (
                select(OptionsSnapshot.iv_rank)
                .where(
                    OptionsSnapshot.ticker == ticker,
                    OptionsSnapshot.iv_rank.isnot(None),
                    OptionsSnapshot.snapshot_time >= cutoff_min,
                    OptionsSnapshot.snapshot_time <= cutoff_max,
                )
                .order_by(OptionsSnapshot.snapshot_time.desc())
                .limit(1)
            )
            row = (await self._session.execute(stmt)).first()
            if not row or row[0] is None:
                return None
            prior_rank = float(row[0])
            return current_rank - prior_rank
        except Exception:
            logger.exception("_iv_rank_5d_change failed for %s", ticker)
            return None

    async def _latest_deep_options_risks(
        self, ticker: str, max_age_days: int = 7
    ) -> list[dict]:
        """Return the ``hidden_risks`` list from the most recent
        :class:`DeepOptionsAnalysis` for *ticker* within *max_age_days*.

        Returns ``[]`` when no recent analysis exists. The bubble-up signals
        in `_stack_signals` filter this list by severity.
        """
        try:
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
            stmt = (
                select(DeepOptionsAnalysis.hidden_risks)
                .where(
                    DeepOptionsAnalysis.ticker == ticker,
                    DeepOptionsAnalysis.analyzed_at >= cutoff,
                )
                .order_by(DeepOptionsAnalysis.analyzed_at.desc())
                .limit(1)
            )
            row = (await self._session.execute(stmt)).first()
            if not row or not row[0]:
                return []
            risks = row[0]
            if not isinstance(risks, list):
                return []
            return risks
        except Exception:
            logger.exception("_latest_deep_options_risks failed for %s", ticker)
            return []

    # ------------------------------------------------------------------
    # Signal stacking
    # ------------------------------------------------------------------

    async def _stack_signals(
        self, ticker: str, data: dict
    ) -> tuple[float, list[dict]]:
        """Score a ticker by stacking bullish, bearish, and neutralizing signals.

        Returns ``(conviction_score, signal_details_list)`` where score is
        clamped to [-100, 100].
        """
        score = 0.0
        signals: list[dict] = []

        ratings: list[AnalystRating] = data.get("ratings", [])
        catalyst_window: dict | None = data.get("catalyst_window")
        news_sentiment: dict = data.get("news_sentiment", {})
        options_snapshot: OptionsSnapshot | None = data.get("options_snapshot")
        stock_data: dict = data.get("stock_data", {})
        technicals: dict = data.get("technicals", {})

        current_price = stock_data.get("price")
        avg_analyst_target = stock_data.get("avg_analyst_target")

        # ── Analyst ratings ─────────────────────────────────────────────
        for rating in ratings:
            firm = rating.analyst_firm
            tier = rating.firm_tier or 3
            rt = rating.rating_type or ""
            prev = (rating.previous_rating or "").upper()
            new = (rating.new_rating or "").upper()

            is_upgrade = self._is_upgrade(prev, new)
            is_downgrade = self._is_downgrade(prev, new)

            if rt == "TIER_CHANGE" or is_upgrade or is_downgrade:
                if is_upgrade:
                    pts = {1: 40, 2: 25, 3: 15}.get(tier, 15)
                    score += pts
                    signals.append({
                        "signal": f"T{tier} Upgrade",
                        "points": pts,
                        "detail": f"{firm} upgraded from {rating.previous_rating} to {rating.new_rating}",
                    })
                elif is_downgrade:
                    pts = {1: -40, 2: -25, 3: -15}.get(tier, -15)
                    score += pts
                    signals.append({
                        "signal": f"T{tier} Downgrade",
                        "points": pts,
                        "detail": f"{firm} downgraded from {rating.previous_rating} to {rating.new_rating}",
                    })

            # Price-target changes
            if rating.previous_pt is not None and rating.new_pt is not None:
                prev_pt = float(rating.previous_pt)
                new_pt = float(rating.new_pt)
                if prev_pt > 0:
                    pt_change_pct = ((new_pt - prev_pt) / prev_pt) * 100.0
                    if pt_change_pct > 10:
                        score += 15
                        signals.append({
                            "signal": "PT Raise >10%",
                            "points": 15,
                            "detail": f"{firm} PT raised from ${prev_pt:.0f} to ${new_pt:.0f} ({pt_change_pct:+.1f}%)",
                        })
                    elif pt_change_pct > 5:
                        score += 10
                        signals.append({
                            "signal": "PT Raise 5-10%",
                            "points": 10,
                            "detail": f"{firm} PT raised from ${prev_pt:.0f} to ${new_pt:.0f} ({pt_change_pct:+.1f}%)",
                        })
                    elif pt_change_pct < -10:
                        score -= 15
                        signals.append({
                            "signal": "PT Cut >10%",
                            "points": -15,
                            "detail": f"{firm} PT cut from ${prev_pt:.0f} to ${new_pt:.0f} ({pt_change_pct:+.1f}%)",
                        })
                    elif pt_change_pct < -5:
                        score -= 10
                        signals.append({
                            "signal": "PT Cut 5-10%",
                            "points": -10,
                            "detail": f"{firm} PT cut from ${prev_pt:.0f} to ${new_pt:.0f} ({pt_change_pct:+.1f}%)",
                        })

        # ── Analyst Revision Momentum (14-day cluster) ──────────────────
        cluster = await self._analyst_revision_cluster(ticker)
        if cluster["bullish_count"] >= 2:
            pts = 15 if cluster["bullish_count"] >= 3 else 10
            score += pts
            signals.append({
                "signal": "Analyst Revision Cluster",
                "points": pts,
                "detail": (
                    f"{cluster['bullish_count']} bullish analyst actions in last 14d "
                    f"(T1/T2 firms: {', '.join(cluster['bullish_firms'][:3])})"
                ),
            })
        elif cluster["bearish_count"] >= 2:
            pts = -15 if cluster["bearish_count"] >= 3 else -10
            score += pts
            signals.append({
                "signal": "Analyst Revision Cluster",
                "points": pts,
                "detail": (
                    f"{cluster['bearish_count']} bearish analyst actions in last 14d "
                    f"(T1/T2 firms: {', '.join(cluster['bearish_firms'][:3])})"
                ),
            })

        # ── Earnings signals ────────────────────────────────────────────
        if catalyst_window:
            earnings_date = catalyst_window.get("earnings_date")
            window_status = catalyst_window.get("window_status", "NONE")

            if earnings_date and window_status == "POST":
                earnings_result = await self._check_earnings_result(
                    ticker, earnings_date, news_sentiment
                )
                if earnings_result == "BEAT_RAISED":
                    score += 30
                    signals.append({
                        "signal": "Earnings Beat + Raised Guidance",
                        "points": 30,
                        "detail": "Actual EPS beat consensus with raised guidance",
                    })
                elif earnings_result == "BEAT":
                    score += 20
                    signals.append({
                        "signal": "Earnings Beat",
                        "points": 20,
                        "detail": "Actual EPS beat consensus, guidance in-line",
                    })
                elif earnings_result == "MISS_LOWERED":
                    score -= 30
                    signals.append({
                        "signal": "Earnings Miss + Lowered Guidance",
                        "points": -30,
                        "detail": "Actual EPS missed consensus with lowered guidance",
                    })
                elif earnings_result == "MISS":
                    score -= 20
                    signals.append({
                        "signal": "Earnings Miss",
                        "points": -20,
                        "detail": "Actual EPS missed consensus",
                    })

            if window_status == "ACTIVE":
                score += 5
                signals.append({
                    "signal": "Active Catalyst Window",
                    "points": 5,
                    "detail": f"Within catalyst window (earnings {earnings_date})",
                })

        # ── Technical signals ───────────────────────────────────────────

        # RSI-14
        rsi = technicals.get("rsi_14")
        if rsi is not None:
            if rsi < 30:
                pts = 15 if rsi < 20 else 10
                score += pts
                signals.append({
                    "signal": "RSI Oversold",
                    "points": pts,
                    "detail": f"RSI(14) at {rsi:.1f} — oversold, potential bounce",
                })
            elif rsi > 70:
                pts = -15 if rsi > 80 else -10
                score += pts
                signals.append({
                    "signal": "RSI Overbought",
                    "points": pts,
                    "detail": f"RSI(14) at {rsi:.1f} — overbought, potential pullback",
                })

        # Price vs 50/200-day SMA — horizon-scaled.
        #
        # SMAs describe regime over a 2-6 month window. For a weekly option,
        # being "above the 50d SMA" rarely matters by itself — the move is
        # already priced in and the mean-reversion thesis (when below) takes
        # 3-8 weeks to play out. We scale the contribution by the suggested
        # trade's estimated DTE so SMAs land at full weight on 30+ DTE
        # contracts and shrink to a token contribution on weeklies.
        est_dte = self._estimated_trade_dte(catalyst_window)
        sma_mult = self._sma_horizon_multiplier(est_dte)

        def _emit_sma(label: str, base_pts: int, detail_core: str) -> None:
            scaled = round(base_pts * sma_mult)
            if scaled == 0:
                return
            note = (
                ""
                if sma_mult >= 0.99
                else f" (×{sma_mult:.2f} for ~{est_dte}d DTE)"
            )
            nonlocal_score[0] += scaled
            signals.append({
                "signal": label,
                "points": scaled,
                "detail": detail_core + note,
            })

        # Workaround for not having `nonlocal score` in the current scope —
        # Python closures can't rebind outer-scope ints. Use a 1-element list.
        nonlocal_score = [score]

        sma_50 = stock_data.get("sma_50")
        if current_price and sma_50 and sma_50 > 0:
            pct_vs_50 = ((current_price - sma_50) / sma_50) * 100
            if pct_vs_50 > 5:
                _emit_sma(
                    "Above 50d SMA", 10,
                    f"Price ${current_price:.2f} is {pct_vs_50:.1f}% above 50d SMA ${sma_50:.2f}",
                )
            elif pct_vs_50 < -5:
                _emit_sma(
                    "Below 50d SMA", -10,
                    f"Price ${current_price:.2f} is {pct_vs_50:.1f}% below 50d SMA ${sma_50:.2f}",
                )

        sma_200 = stock_data.get("sma_200")
        if current_price and sma_200 and sma_200 > 0:
            pct_vs_200 = ((current_price - sma_200) / sma_200) * 100
            if pct_vs_200 > 5:
                _emit_sma(
                    "Above 200d SMA", 5,
                    f"Price {pct_vs_200:.1f}% above 200d SMA ${sma_200:.2f} — long-term uptrend",
                )
            elif pct_vs_200 < -5:
                _emit_sma(
                    "Below 200d SMA", -5,
                    f"Price {pct_vs_200:.1f}% below 200d SMA ${sma_200:.2f} — long-term downtrend",
                )

        score = nonlocal_score[0]

        # Multi-day momentum
        momentum_5d = technicals.get("momentum_5d")
        momentum_20d = technicals.get("momentum_20d")
        if momentum_5d is not None:
            if momentum_5d > 5:
                score += 10
                signals.append({
                    "signal": "Strong 5d Momentum",
                    "points": 10,
                    "detail": f"Stock up {momentum_5d:+.1f}% over 5 trading days",
                })
            elif momentum_5d < -5:
                score -= 10
                signals.append({
                    "signal": "Weak 5d Momentum",
                    "points": -10,
                    "detail": f"Stock down {momentum_5d:+.1f}% over 5 trading days",
                })
        if momentum_20d is not None:
            if momentum_20d > 15:
                score += 10
                signals.append({
                    "signal": "Strong 20d Momentum",
                    "points": 10,
                    "detail": f"Stock up {momentum_20d:+.1f}% over 20 trading days",
                })
            elif momentum_20d < -15:
                score -= 10
                signals.append({
                    "signal": "Weak 20d Momentum",
                    "points": -10,
                    "detail": f"Stock down {momentum_20d:+.1f}% over 20 trading days",
                })

        # ── Rapid drawdown detection ────────────────────────────────────
        drawdown_1d = technicals.get("drawdown_1d")
        drawdown_2d = technicals.get("drawdown_2d")
        drawdown_3d = technicals.get("drawdown_3d")
        consec_down = technicals.get("consecutive_down_days", 0)
        down_vol_ratio = technicals.get("down_day_volume_ratio")
        has_rapid_drawdown = False

        # 1-day sharp drop
        if drawdown_1d is not None and drawdown_1d < -5:
            score -= 15
            has_rapid_drawdown = True
            signals.append({
                "signal": "Sharp 1d Drop",
                "points": -15,
                "detail": f"Stock dropped {drawdown_1d:+.1f}% in a single day",
            })

        # 2-day cumulative drop
        if drawdown_2d is not None and drawdown_2d < -7:
            score -= 20
            has_rapid_drawdown = True
            signals.append({
                "signal": "Rapid 2d Drawdown",
                "points": -20,
                "detail": f"Stock dropped {drawdown_2d:+.1f}% over 2 days — sustained selling",
            })
        # 3-day cumulative drop (if 2-day didn't fire)
        elif drawdown_3d is not None and drawdown_3d < -10:
            score -= 15
            has_rapid_drawdown = True
            signals.append({
                "signal": "Rapid 3d Drawdown",
                "points": -15,
                "detail": f"Stock dropped {drawdown_3d:+.1f}% over 3 days — persistent decline",
            })

        # Distribution signal: heavy volume on down days
        if has_rapid_drawdown and down_vol_ratio is not None and down_vol_ratio > 1.3:
            score -= 10
            signals.append({
                "signal": "Distribution (Heavy Selling Volume)",
                "points": -10,
                "detail": f"Down-day volume {down_vol_ratio:.1f}x up-day volume — institutional selling",
            })

        # ── Oversold bounce / dip buy detection ─────────────────────────
        # Only fires when there IS a drawdown, so user sees both signals
        if has_rapid_drawdown and rsi is not None:
            near_support = False
            support_detail = ""

            # Check proximity to 200d SMA (within 3%)
            if current_price and sma_200 and sma_200 > 0:
                dist_to_200 = abs(current_price - sma_200) / sma_200 * 100
                if dist_to_200 < 3:
                    near_support = True
                    support_detail = f"near 200d SMA ${sma_200:.2f}"

            # Check proximity to 52w low (within 10%)
            wk_low = stock_data.get("fifty_two_week_low")
            if not near_support and current_price and wk_low and wk_low > 0:
                dist_to_low = ((current_price - wk_low) / wk_low) * 100
                if dist_to_low < 10:
                    near_support = True
                    support_detail = f"near 52w low ${wk_low:.2f}"

            selling_exhaustion = (
                down_vol_ratio is not None and down_vol_ratio < 0.8
            )

            if rsi < 20 and near_support:
                pts = 25 if selling_exhaustion else 20
                score += pts
                signals.append({
                    "signal": "Strong Reversal Setup",
                    "points": pts,
                    "detail": f"RSI {rsi:.0f} (deeply oversold) + {support_detail}"
                             + (" + selling exhaustion" if selling_exhaustion else ""),
                })
            elif rsi < 30 and near_support:
                pts = 20 if selling_exhaustion else 15
                score += pts
                signals.append({
                    "signal": "Oversold Bounce Setup",
                    "points": pts,
                    "detail": f"RSI {rsi:.0f} (oversold) + {support_detail}"
                             + (" + selling exhaustion" if selling_exhaustion else ""),
                })
            elif rsi < 30:
                score += 5
                signals.append({
                    "signal": "Oversold After Drop",
                    "points": 5,
                    "detail": f"RSI {rsi:.0f} after drawdown — watch for reversal, no support nearby",
                })

        # ── 52-week position (context-gated) ────────────────────────────
        wk_high = stock_data.get("fifty_two_week_high")
        wk_low = stock_data.get("fifty_two_week_low")
        actively_declining = (momentum_5d is not None and momentum_5d < -5)

        if current_price and wk_high and wk_low and wk_high > wk_low:
            pct_from_high = ((wk_high - current_price) / wk_high) * 100
            if pct_from_high < 5:
                score += 5
                signals.append({
                    "signal": "Near 52w High",
                    "points": 5,
                    "detail": f"Within {pct_from_high:.1f}% of 52-week high ${wk_high:.2f} — breakout potential",
                })
            elif pct_from_high > 30:
                # Gate: halve points during active decline (falling knife)
                if actively_declining:
                    score += 5
                    signals.append({
                        "signal": "Deep Pullback from 52w High",
                        "points": 5,
                        "detail": f"{pct_from_high:.0f}% below 52w high — but still declining, caution",
                    })
                else:
                    score += 10
                    signals.append({
                        "signal": "Deep Pullback from 52w High",
                        "points": 10,
                        "detail": f"{pct_from_high:.0f}% below 52-week high ${wk_high:.2f} — potential value",
                    })

        # Volume confirmation (high volume on directional move)
        volume_ratio = technicals.get("volume_ratio")
        if volume_ratio is not None and momentum_5d is not None:
            if volume_ratio > 1.5 and momentum_5d > 3:
                score += 10
                signals.append({
                    "signal": "Volume-Confirmed Rally",
                    "points": 10,
                    "detail": f"Volume {volume_ratio:.1f}x avg with {momentum_5d:+.1f}% 5d move — strong conviction",
                })
            elif volume_ratio > 1.5 and momentum_5d < -3:
                score -= 10
                signals.append({
                    "signal": "Volume-Confirmed Selloff",
                    "points": -10,
                    "detail": f"Volume {volume_ratio:.1f}x avg with {momentum_5d:+.1f}% 5d move — distribution",
                })

        # ── OHLC candlestick signals (skip if OHLC unavailable) ──

        wick_rejection = technicals.get("ohlc_upper_wick_rejection")
        if wick_rejection is not None:
            score -= 8
            signals.append({
                "signal": "Upper Wick Rejection",
                "points": -8,
                "detail": f"Up day but {wick_rejection:.0f}% of range above close — selling into strength",
            })

        gap_down = technicals.get("ohlc_gap_down_after_up")
        if gap_down is not None:
            score -= 12
            signals.append({
                "signal": "Gap-Down After Up Day",
                "points": -12,
                "detail": f"Opened {gap_down:.1f}% below prior close after an up day — overnight sentiment reversal",
            })

        close_pos = technicals.get("ohlc_close_position_in_range")
        if close_pos is not None:
            if close_pos < 0.20:
                score -= 5
                signals.append({
                    "signal": "Closed Near Low",
                    "points": -5,
                    "detail": f"Closed at {close_pos:.0%} of day's range — bears dominated",
                })
            elif close_pos > 0.80:
                score += 5
                signals.append({
                    "signal": "Closed Near High",
                    "points": 5,
                    "detail": f"Closed at {close_pos:.0%} of day's range — bulls dominated",
                })

        # Short interest
        short_pct = stock_data.get("short_pct_float")
        short_ratio = stock_data.get("short_ratio")
        if short_pct is not None and short_pct > 0.10:
            if momentum_5d is not None and momentum_5d > 3:
                score += 15
                signals.append({
                    "signal": "Short Squeeze Setup",
                    "points": 15,
                    "detail": f"{short_pct:.0%} short float with {momentum_5d:+.1f}% 5d rally — squeeze potential",
                })
            else:
                score -= 5
                signals.append({
                    "signal": "High Short Interest",
                    "points": -5,
                    "detail": f"{short_pct:.0%} of float sold short (days to cover: {short_ratio:.1f})",
                })

        # ── Relative strength vs SPY ────────────────────────────────────
        benchmark: dict = data.get("benchmark", {})
        spy_5d = benchmark.get("spy_momentum_5d")
        if momentum_5d is not None and spy_5d is not None:
            relative_strength = momentum_5d - spy_5d
            if relative_strength < -5:
                score -= 10
                signals.append({
                    "signal": "Underperforming Market",
                    "points": -10,
                    "detail": f"Stock {momentum_5d:+.1f}% vs SPY {spy_5d:+.1f}% (5d) — lagging by {abs(relative_strength):.1f}pp",
                })
            elif relative_strength > 5:
                score += 10
                signals.append({
                    "signal": "Outperforming Market",
                    "points": 10,
                    "detail": f"Stock {momentum_5d:+.1f}% vs SPY {spy_5d:+.1f}% (5d) — leading by {relative_strength:.1f}pp",
                })

        # ── Options signals ─────────────────────────────────────────────
        if options_snapshot:
            iv_rank = float(options_snapshot.iv_rank or 0)

            # Compare today's volume against historical average (last 20 days)
            total_call = options_snapshot.total_call_volume or 0
            total_put = options_snapshot.total_put_volume or 0
            hist_avg_call, hist_avg_put = await self._get_historical_avg_volume(ticker)

            # Unusual call volume (today vs historical average)
            if hist_avg_call > 0 and total_call > 1.5 * hist_avg_call:
                score += 15
                signals.append({
                    "signal": "Unusual Call Volume",
                    "points": 15,
                    "detail": f"Call volume {total_call:,} vs 20d avg {hist_avg_call:,.0f} ({total_call / hist_avg_call:.1f}x)",
                })

            # Smart-Money Positioning (3-day sustained flow + rising IV rank)
            rolling_3d_call = await self._rolling_call_volume_3d(ticker)
            iv_trajectory = await self._iv_rank_trajectory(ticker)
            if (
                hist_avg_call > 0
                and rolling_3d_call >= 2.0 * hist_avg_call
                and iv_trajectory is not None
                and iv_trajectory > 10.0
            ):
                score += 12
                signals.append({
                    "signal": "Smart Money Positioning",
                    "points": 12,
                    "detail": (
                        f"3d avg calls {rolling_3d_call:,.0f} ({rolling_3d_call / hist_avg_call:.1f}x) "
                        f"+ IV rank rising {iv_trajectory:+.0f}pp — institutional accumulation"
                    ),
                })

            # Unusual put volume (today vs historical average)
            if hist_avg_put > 0 and total_put > 1.5 * hist_avg_put:
                score -= 15
                signals.append({
                    "signal": "Unusual Put Volume",
                    "points": -15,
                    "detail": f"Put volume {total_put:,} vs 20d avg {hist_avg_put:,.0f} ({total_put / hist_avg_put:.1f}x)",
                })

            # Put/Call OI skew
            call_oi = options_snapshot.total_call_oi or 0
            put_oi = options_snapshot.total_put_oi or 0
            if call_oi > 0 and put_oi > 0:
                pc_oi_ratio = put_oi / call_oi
                if pc_oi_ratio < 0.5:
                    score += 10
                    signals.append({
                        "signal": "Bullish OI Skew",
                        "points": 10,
                        "detail": f"P/C OI ratio {pc_oi_ratio:.2f} — call-heavy positioning",
                    })
                elif pc_oi_ratio > 1.5:
                    score -= 10
                    signals.append({
                        "signal": "Bearish OI Skew",
                        "points": -10,
                        "detail": f"P/C OI ratio {pc_oi_ratio:.2f} — put-heavy positioning",
                    })

            # IV rank neutralizer
            if iv_rank > 70:
                score -= 10
                signals.append({
                    "signal": "High IV Rank",
                    "points": -10,
                    "detail": f"IV rank {iv_rank:.0f} (>70) — elevated premium risk",
                })

        # ── Greek-based signals ────────────────────────────────────────
        greeks_summary: dict | None = data.get("greeks_summary")
        if greeks_summary:
            theta_pct = greeks_summary.get("avg_atm_theta_pct", 0)
            vega_pct = greeks_summary.get("avg_atm_vega_pct", 0)
            near_dte = greeks_summary.get("near_term_dte", 999)

            # Heavy Theta Decay warning
            if theta_pct > 0.03:
                score -= 8
                signals.append({
                    "signal": "Heavy Theta Decay",
                    "points": -8,
                    "detail": f"Near-term ATM options losing {theta_pct * 100:.1f}% premium/day — time decay accelerating",
                })
            elif theta_pct > 0.02:
                score -= 5
                signals.append({
                    "signal": "Elevated Theta Decay",
                    "points": -5,
                    "detail": f"Near-term ATM options losing {theta_pct * 100:.1f}% premium/day",
                })

            # IV Crush Risk — high vega + imminent earnings
            earnings_imminent = False
            if catalyst_window:
                earnings_date = catalyst_window.get("earnings_date")
                if earnings_date:
                    if isinstance(earnings_date, str):
                        try:
                            from datetime import datetime as dt
                            earnings_date = dt.strptime(earnings_date, "%Y-%m-%d").date()
                        except ValueError:
                            earnings_date = None
                    if earnings_date:
                        days_to_earnings = (earnings_date - date.today()).days
                        earnings_imminent = 0 < days_to_earnings <= 7

            if earnings_imminent and vega_pct > 0.08:
                score -= 12
                signals.append({
                    "signal": "IV Crush Risk",
                    "points": -12,
                    "detail": f"Earnings within 7 days + high vega ({vega_pct * 100:.1f}% premium per 1% IV) — expect IV crush post-event",
                })

            # IV Spike Warning — iv_rank rose sharply over the last 5 trading
            # days. Front-loaded premium accumulation outside an earnings
            # window typically resolves with an event + crush; the signal
            # fires regardless of whether we know the catalyst. Skip when the
            # earnings-keyed IV Crush Risk already fired above so the user
            # doesn't see two near-duplicates.
            if options_snapshot and not (earnings_imminent and vega_pct > 0.08):
                current_rank = float(options_snapshot.iv_rank or 0)
                if current_rank > 0:
                    rank_change = await self._iv_rank_5d_change(ticker, current_rank)
                    if rank_change is not None and rank_change >= 20:
                        score -= 12
                        signals.append({
                            "signal": "IV Spike Warning",
                            "points": -12,
                            "detail": (
                                f"IV rank rose {rank_change:+.0f}pp over 5 trading days "
                                f"(now {current_rank:.0f}) — premium building ahead of an "
                                "unknown catalyst; expect crush on resolution"
                            ),
                        })

            # Favorable Theta — low decay with strong conviction
            if near_dte <= 14 and theta_pct < 0.01 and score > 30:
                score += 5
                signals.append({
                    "signal": "Favorable Theta",
                    "points": 5,
                    "detail": f"Low theta decay ({theta_pct * 100:.1f}%/day) with strong conviction — time is manageable",
                })

        # ── Deep-options hidden-risk bubble-up ──────────────────────────
        # Pulls hidden_risks from the most recent deep_options_analyses row
        # (≤7d). Each MEDIUM/HIGH severity finding surfaces as its own signal
        # in the daily stack so users see the structural options warnings
        # without leaving the Dashboard for the Options Lab.
        # Severity gating: BACKWARDATION fires even at LOW severity (it's the
        # critical "IV crush imminent" signal — see APLD May 11). Others
        # require MEDIUM or HIGH to avoid noise from minor structural drift.
        deep_risks = await self._latest_deep_options_risks(ticker)
        if deep_risks:
            seen_codes: set[str] = set()
            for risk in deep_risks:
                code = risk.get("code")
                severity = risk.get("severity", "LOW")
                detail = risk.get("detail") or risk.get("title") or ""
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)

                if code == "BACKWARDATION":
                    # Term-structure backwardation fires at any severity. This
                    # is the textbook "IV crush coming" signature.
                    score -= 12
                    signals.append({
                        "signal": "Term Backwardation",
                        "points": -12,
                        "detail": detail,
                    })
                elif code == "PUT_SKEW" and severity in ("MEDIUM", "HIGH"):
                    score -= 8
                    signals.append({
                        "signal": "Elevated Put Skew",
                        "points": -8,
                        "detail": detail,
                    })
                elif code == "NEGATIVE_GEX" and severity in ("MEDIUM", "HIGH"):
                    score -= 10
                    signals.append({
                        "signal": "Negative Gamma Exposure",
                        "points": -10,
                        "detail": detail,
                    })
                elif code == "PIN_RISK" and severity in ("MEDIUM", "HIGH"):
                    score -= 5
                    signals.append({
                        "signal": "Options Pin Risk",
                        "points": -5,
                        "detail": detail,
                    })

        # ── News sentiment (scaled by magnitude and count) ──────────────
        avg_sentiment = news_sentiment.get("avg_sentiment", 0.0)
        article_count = news_sentiment.get("total_count", 0)

        if avg_sentiment > 0.3:
            magnitude_bonus = min(5, int((avg_sentiment - 0.3) * 15))
            count_bonus = min(5, article_count // 3)
            pts = 5 + magnitude_bonus + count_bonus
            score += pts
            signals.append({
                "signal": "Positive News Sentiment",
                "points": pts,
                "detail": f"Avg sentiment {avg_sentiment:+.3f} across {article_count} articles",
            })
        elif avg_sentiment < -0.3:
            magnitude_bonus = min(5, int((abs(avg_sentiment) - 0.3) * 15))
            count_bonus = min(5, article_count // 3)
            pts = -(5 + magnitude_bonus + count_bonus)
            score += pts
            signals.append({
                "signal": "Negative News Sentiment",
                "points": pts,
                "detail": f"Avg sentiment {avg_sentiment:+.3f} across {article_count} articles",
            })

        # Check for sector tailwind/headwind via news
        sector_news = await self._check_sector_news(ticker, stock_data)
        if sector_news == "TAILWIND":
            score += 10
            signals.append({
                "signal": "Sector Tailwind",
                "points": 10,
                "detail": f"Positive sector news for {stock_data.get('sector', 'Unknown')}",
            })
        elif sector_news == "HEADWIND":
            score -= 10
            signals.append({
                "signal": "Sector Headwind",
                "points": -10,
                "detail": f"Negative sector news for {stock_data.get('sector', 'Unknown')}",
            })

        # ── Geopolitical signals ────────────────────────────────────────
        geo_signals = await self._check_geopolitical_signals(ticker)
        for gs in geo_signals:
            score += gs["points"]
            signals.append(gs)

        # Discount news sentiment when geopolitical signals fire — the same
        # articles drive both signals, so full stacking double-counts.
        if geo_signals:
            for s in signals:
                if s["signal"] in ("Positive News Sentiment", "Negative News Sentiment"):
                    old_pts = s["points"]
                    new_pts = round(old_pts / 2)
                    score += new_pts - old_pts
                    s["points"] = new_pts
                    s["detail"] += " (halved — geo overlap)"
                    break

        # ── Price relative to consensus PT (context-gated) ──────────────
        if current_price and avg_analyst_target:
            discount_pct = (
                (avg_analyst_target - current_price) / current_price
            ) * 100
            if discount_pct > 15:
                # Gate: halve during active decline
                if actively_declining:
                    score += 5
                    signals.append({
                        "signal": "Below Consensus PT",
                        "points": 5,
                        "detail": f"Stock ${current_price:.2f} vs PT ${avg_analyst_target:.2f} ({discount_pct:.0f}% discount) — but declining",
                    })
                else:
                    score += 10
                    signals.append({
                        "signal": "Below Consensus PT",
                        "points": 10,
                        "detail": f"Stock ${current_price:.2f} vs consensus PT ${avg_analyst_target:.2f} ({discount_pct:.0f}% discount)",
                    })

        # ── Insider activity ────────────────────────────────────────────
        await self._check_insider_activity(ticker, signals)
        # Re-sum score from signals to include insider contributions
        score = sum(s["points"] for s in signals)

        # ── Neutralizing: stock already moved ───────────────────────────
        stock_moved_pct = self._calc_stock_move_pct(stock_data)
        if abs(stock_moved_pct) > 5:
            score -= 5
            signals.append({
                "signal": "Stock Already Moved",
                "points": -5,
                "detail": f"Stock moved {stock_moved_pct:+.1f}% on catalyst",
            })

        # Clamp to [-100, 100]
        score = max(-100.0, min(100.0, score))

        return score, signals

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify_action(self, conviction: float) -> str:
        """Map conviction score to an action label."""
        if conviction >= 60:
            return "STRONG_BUY"
        if conviction >= 30:
            return "BUY"
        if conviction >= -15:
            return "HOLD"
        if conviction >= -30:
            return "SELL"
        return "STRONG_SELL"

    # Action tiers ordered toward HOLD for stale-move downgrades.
    _STALE_MOVE_DOWNGRADE = {
        "STRONG_BUY": "BUY",
        "BUY": "HOLD",
        "SELL": "HOLD",
        "STRONG_SELL": "SELL",
    }

    def _apply_stale_move_gate(
        self,
        action: str,
        stock_moved_pct: float,
        signals: list[dict],
    ) -> tuple[str, bool]:
        """Downgrade action and flag entry=WAIT when today's move has
        already exhausted the edge.

        Direction-matched: only fires when the move agrees with the
        action's bias. SELL after a sharp drop sells the low; BUY after a
        sharp rally chases a priced-in move. Counter-move entries
        (buying the dip, selling the rip) are intentional and not gated.
        """
        if abs(stock_moved_pct) <= 5:
            return action, False

        bullish = action in ("BUY", "STRONG_BUY")
        bearish = action in ("SELL", "STRONG_SELL")
        move_agrees = (bullish and stock_moved_pct > 5) or (
            bearish and stock_moved_pct < -5
        )
        if not move_agrees:
            return action, False

        new_action = self._STALE_MOVE_DOWNGRADE.get(action, action)
        signals.append({
            "signal": "Stale Move Gate",
            "points": 0,
            "detail": (
                f"Action downgraded {action} → {new_action} — "
                f"{stock_moved_pct:+.1f}% move already priced in; "
                f"entry deferred to WAIT"
            ),
        })
        return new_action, True

    # ------------------------------------------------------------------
    # Rationale generation
    # ------------------------------------------------------------------

    async def _generate_rationale(
        self,
        ticker: str,
        action: str,
        conviction: float,
        signals: list[dict],
        stock_data: dict,
        entry_strategy: str | None = None,
    ) -> str:
        """Build a natural-language rationale string."""
        display_action = action.replace("_", " ")
        sign = "+" if conviction >= 0 else ""

        # Top signal description
        sorted_signals = sorted(
            signals, key=lambda s: abs(s["points"]), reverse=True
        )
        top_signal = sorted_signals[0]["detail"] if sorted_signals else "No strong signals"

        # Additional signals summary
        additional = [s["signal"] for s in sorted_signals[1:4]]
        additional_summary = ". ".join(additional) if additional else "No additional signals"

        # Price context
        current_price = stock_data.get("price")
        avg_target = stock_data.get("avg_analyst_target")
        if current_price and avg_target:
            discount = ((avg_target - current_price) / current_price) * 100
            price_context = (
                f"Stock trades at ${current_price:.2f} vs ${avg_target:.2f} "
                f"consensus PT ({discount:+.0f}%)"
            )
        elif current_price:
            price_context = f"Stock trades at ${current_price:.2f}"
        else:
            price_context = "Price data unavailable"

        # Entry strategy context
        stock_moved_pct = self._calc_stock_move_pct(stock_data)
        catalyst_window = None
        try:
            catalyst_window = await self._earnings_service.get_catalyst_window(ticker)
        except Exception:
            pass
        entry = entry_strategy or self._determine_entry_strategy(
            conviction, catalyst_window, stock_moved_pct, signals
        )
        # Richer reason when PRE_POSITION fires on a leading signal
        pre_position_reason = "catalyst window active"
        if entry == "PRE_POSITION":
            window_active = (
                catalyst_window
                and catalyst_window.get("window_status") == "ACTIVE"
            )
            if not window_active:
                leading = next(
                    (
                        s for s in signals
                        if s.get("signal") in self._LEADING_PRE_POSITION_SIGNALS
                        and s.get("points", 0) > 0
                    ),
                    None,
                )
                if leading is not None:
                    pre_position_reason = (
                        f"{leading['signal'].lower()} ahead of catalyst"
                    )
        entry_reasons = {
            "PRE_POSITION": pre_position_reason,
            "REACTIVE": "catalyst already in motion",
            "WAIT": "waiting for confirmation",
            "HOLD": "insufficient conviction",
        }
        entry_reason = entry_reasons.get(entry, "")

        rationale = (
            f"{ticker} \u2014 {display_action} (conviction: {sign}{conviction:.0f}). "
            f"{top_signal}. {additional_summary}. {price_context}. "
            f"Entry: {entry} \u2014 {entry_reason}."
        )
        return rationale

    # ------------------------------------------------------------------
    # Entry strategy
    # ------------------------------------------------------------------

    # Leading signals that justify PRE_POSITION even without an active
    # earnings catalyst window.
    _LEADING_PRE_POSITION_SIGNALS = frozenset({
        "Analyst Revision Cluster",
        "Smart Money Positioning",
        "Insider Buy Cluster",
    })

    def _determine_entry_strategy(
        self,
        conviction: float,
        catalyst_window: dict | None,
        stock_moved_pct: float,
        signals: list[dict] | None = None,
    ) -> str:
        """Decide entry timing strategy.

        PRE_POSITION fires when:
        - Active earnings catalyst window + conviction >= 30, OR
        - A leading pre-position signal (analyst cluster, smart-money flow,
          or insider cluster) is present with conviction >= 25 — lets us
          front-run the earnings window when institutional signals cluster.
        """
        if (
            catalyst_window
            and catalyst_window.get("window_status") == "ACTIVE"
            and conviction >= 30
        ):
            return "PRE_POSITION"

        if signals and conviction >= 25:
            for s in signals:
                if (
                    s.get("signal") in self._LEADING_PRE_POSITION_SIGNALS
                    and s.get("points", 0) > 0
                ):
                    return "PRE_POSITION"

        if conviction >= 30 and abs(stock_moved_pct) > 3:
            return "REACTIVE"
        if conviction >= 15:
            return "WAIT"
        return "HOLD"

    # ------------------------------------------------------------------
    # Risk level
    # ------------------------------------------------------------------

    def _determine_risk_level(self, conviction: float, iv_rank: float) -> str:
        """Classify risk based on conviction strength and IV rank."""
        if abs(conviction) >= 60 and iv_rank < 50:
            return "LOW"
        if abs(conviction) >= 30:
            return "MEDIUM"
        return "HIGH"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_upgrade(prev: str, new: str) -> bool:
        """Heuristic: does the rating change represent an upgrade?"""
        bearish = {"SELL", "UNDERPERFORM", "UNDERWEIGHT", "REDUCE", "NEGATIVE"}
        neutral = {"HOLD", "NEUTRAL", "EQUAL-WEIGHT", "MARKET PERFORM", "PEER PERFORM", "SECTOR PERFORM"}
        bullish = {"BUY", "OUTPERFORM", "OVERWEIGHT", "STRONG BUY", "POSITIVE", "ACCUMULATE"}

        def _rank(r: str) -> int:
            if r in bearish:
                return 0
            if r in neutral:
                return 1
            if r in bullish:
                return 2
            return -1

        p, n = _rank(prev), _rank(new)
        if p < 0 or n < 0:
            return False
        return n > p

    @staticmethod
    def _is_downgrade(prev: str, new: str) -> bool:
        """Heuristic: does the rating change represent a downgrade?"""
        bearish = {"SELL", "UNDERPERFORM", "UNDERWEIGHT", "REDUCE", "NEGATIVE"}
        neutral = {"HOLD", "NEUTRAL", "EQUAL-WEIGHT", "MARKET PERFORM", "PEER PERFORM", "SECTOR PERFORM"}
        bullish = {"BUY", "OUTPERFORM", "OVERWEIGHT", "STRONG BUY", "POSITIVE", "ACCUMULATE"}

        def _rank(r: str) -> int:
            if r in bearish:
                return 0
            if r in neutral:
                return 1
            if r in bullish:
                return 2
            return -1

        p, n = _rank(prev), _rank(new)
        if p < 0 or n < 0:
            return False
        return n < p

    async def _check_earnings_result(
        self, ticker: str, earnings_date: date, news_sentiment: dict
    ) -> str | None:
        """Check whether earnings were a beat or miss with guidance context."""
        stmt = select(EarningsCalendar).where(
            EarningsCalendar.ticker == ticker,
            EarningsCalendar.earnings_date == earnings_date,
        )
        result = await self._session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None

        actual = record.actual_eps
        consensus = record.consensus_eps
        if actual is None or consensus is None:
            return None

        # Check news for guidance keywords
        recent_news = await self._news_scanner.get_recent_news(
            ticker=ticker, hours=72
        )
        headlines_text = " ".join(
            (n.headline or "") + " " + (n.summary or "") for n in recent_news
        ).lower()

        if float(actual) > float(consensus):
            if "raised guidance" in headlines_text or "raised outlook" in headlines_text:
                return "BEAT_RAISED"
            return "BEAT"
        elif float(actual) < float(consensus):
            if "lowered guidance" in headlines_text or "lowered outlook" in headlines_text:
                return "MISS_LOWERED"
            return "MISS"
        return None

    async def _resolve_edgeflow_sector(self, ticker: str) -> str | None:
        """Resolve a ticker to its EdgeFlow sector name via UniverseStock -> Sector."""
        stmt = (
            select(Sector.name)
            .join(UniverseStock, UniverseStock.sector_id == Sector.id)
            .where(UniverseStock.ticker == ticker, UniverseStock.is_active.is_(True))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _check_geopolitical_signals(self, ticker: str) -> list[dict]:
        """Detect geopolitical events and return sector-specific signal dicts."""
        edgeflow_sector = await self._resolve_edgeflow_sector(ticker)
        if not edgeflow_sector:
            return []

        try:
            # Scan all general news — geopolitical articles can be classified
            # as any category (MACRO, PRODUCT, SECTOR, etc.) so don't filter
            all_articles = await self._news_scanner.get_recent_news(
                ticker=None, hours=48
            )
        except Exception:
            logger.exception("Failed to fetch news for geopolitical scan")
            return []

        if not all_articles:
            return []

        article_dicts = [
            {
                "headline": n.headline or "",
                "summary": n.summary or "",
                "sentiment_score": float(n.sentiment_score or 0),
            }
            for n in all_articles
        ]

        geo_signals = detect_and_score(article_dicts, edgeflow_sector)
        result = [
            {"signal": gs.signal_name, "points": gs.points, "detail": gs.detail}
            for gs in geo_signals
        ]

        # Cap total geopolitical contribution to ±20 to prevent correlated
        # events (e.g. MILITARY_CONFLICT + OIL_DISRUPTION) from stacking
        if result:
            total = sum(s["points"] for s in result)
            cap = 20
            if abs(total) > cap:
                scale = cap / abs(total)
                for s in result:
                    s["points"] = round(s["points"] * scale)

        return result

    async def _check_sector_news(
        self, ticker: str, stock_data: dict
    ) -> str | None:
        """Detect sector-level tailwind or headwind from general news."""
        sector = stock_data.get("sector")
        if not sector:
            return None

        try:
            general_news = await self._news_scanner.get_recent_news(
                ticker=None, hours=48, category="SECTOR"
            )
        except Exception:
            return None

        if not general_news:
            return None

        sector_lower = sector.lower()
        relevant = [
            n
            for n in general_news
            if sector_lower in ((n.headline or "") + " " + (n.summary or "")).lower()
        ]
        if not relevant:
            return None

        avg = sum(float(n.sentiment_score or 0) for n in relevant) / len(relevant)
        if avg > 0.2:
            return "TAILWIND"
        if avg < -0.2:
            return "HEADWIND"
        return None

    async def _check_insider_activity(
        self, ticker: str, signals: list[dict]
    ) -> None:
        """Check for significant insider buying/selling and append signals.

        Tracks cluster signal when ≥2 distinct insiders buy in last 30 days
        with purchases outnumbering sales by count — a stronger pre-position
        signal than total dollar value alone.
        """
        try:
            loop = asyncio.get_event_loop()
            trades = await loop.run_in_executor(
                None, self._data_client.get_insider_trades, ticker
            )
        except Exception:
            return

        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=30)).strftime(
            "%Y-%m-%d"
        )
        buying_value = 0.0
        selling_value = 0.0
        buyer_names: set[str] = set()
        seller_names: set[str] = set()
        buy_count = 0
        sell_count = 0

        for trade in trades:
            trade_date = trade.get("date", "")
            if trade_date < cutoff:
                continue
            value = abs(float(trade.get("value") or 0))
            tx_type = (trade.get("transaction_type") or "").upper()
            name = (trade.get("name") or trade.get("insider_name") or "").strip()
            # P = Purchase, S = Sale
            if tx_type == "P":
                buying_value += value
                buy_count += 1
                if name:
                    buyer_names.add(name)
            elif tx_type == "S":
                selling_value += value
                sell_count += 1
                if name:
                    seller_names.add(name)

        # Cluster signal (stronger) — ≥2 distinct buyers and buys > sells
        if len(buyer_names) >= 2 and buy_count > sell_count:
            pts = 12 if len(buyer_names) >= 3 else 8
            signals.append({
                "signal": "Insider Buy Cluster",
                "points": pts,
                "detail": (
                    f"{len(buyer_names)} distinct insiders bought (${buying_value:,.0f}) "
                    f"vs {sell_count} sales in last 30d — convergent accumulation"
                ),
            })
        elif buying_value > 500_000:
            signals.append({
                "signal": "Insider Buying",
                "points": 5,
                "detail": f"Insider purchases totaling ${buying_value:,.0f} in last 30 days",
            })
        if selling_value > 1_000_000:
            signals.append({
                "signal": "Insider Selling",
                "points": -5,
                "detail": f"Insider sales totaling ${selling_value:,.0f} in last 30 days",
            })

    @staticmethod
    def _calc_stock_move_pct(stock_data: dict) -> float:
        """Calculate how much the stock moved from previous close.

        Returns the percentage change from yesterday's close to the
        current price — a true daily move measurement.
        """
        price = stock_data.get("price")
        prev_close = stock_data.get("previous_close")
        if price and prev_close and prev_close > 0:
            return ((price - prev_close) / prev_close) * 100.0
        return 0.0

    @staticmethod
    def _estimated_trade_dte(catalyst_window: dict | None) -> int:
        """Estimate the suggested-options DTE this rec is being built for.

        Mirrors the logic ``options_analyzer.select_optimal_contracts`` uses
        to pick a target expiry. Used by the SMA horizon-scaling gate so
        long-term moving-average signals don't dominate weekly trade scores.

        - Active earnings window (T-14 to T+10) → trade is short-dated
          (typically 7-14 DTE); return days_to_earnings + 5, clamped ≥ 7.
        - Earnings 14-30d out → moderate DTE (~21).
        - No catalyst → default 30 (the strike recommender's mid-range).
        """
        if not catalyst_window:
            return 30
        from datetime import date as _date, datetime as _datetime
        earnings_date = catalyst_window.get("earnings_date")
        window_status = catalyst_window.get("window_status", "NONE")
        if not earnings_date:
            return 30
        try:
            ed = (
                earnings_date
                if isinstance(earnings_date, _date) and not isinstance(earnings_date, _datetime)
                else _datetime.fromisoformat(str(earnings_date)).date()
            )
        except (ValueError, TypeError):
            return 30
        days_to = (ed - _date.today()).days
        if window_status == "ACTIVE" and 0 <= days_to <= 14:
            return max(7, days_to + 5)
        if 0 < days_to <= 30:
            return max(14, days_to)
        return 30

    @staticmethod
    def _sma_horizon_multiplier(estimated_dte: int) -> float:
        """Scale SMA signal weight by trade horizon.

        - DTE ≥ 30: full weight (1.0) — SMAs genuinely matter
        - DTE 21-29: 0.7 — trend regime still meaningful
        - DTE 14-20: 0.5 — context only
        - DTE  < 14: 0.25 — token contribution; weeklies don't trade SMAs
        """
        if estimated_dte >= 30:
            return 1.0
        if estimated_dte >= 21:
            return 0.7
        if estimated_dte >= 14:
            return 0.5
        return 0.25

    @staticmethod
    def _calc_stop_loss(
        current_price: float | None, action: str
    ) -> float | None:
        """Calculate a simple stop-loss price based on action direction."""
        if current_price is None:
            return None
        if action in ("BUY", "STRONG_BUY"):
            return round(current_price * 0.93, 2)  # 7% stop
        if action in ("SELL", "STRONG_SELL"):
            return round(current_price * 1.07, 2)  # 7% stop (short)
        return None

    @staticmethod
    def _infer_catalyst_type(signals: list[dict]) -> str | None:
        """Infer the primary catalyst type from signal names."""
        if not signals:
            return None
        top = max(signals, key=lambda s: abs(s["points"]))
        name = top["signal"].upper()
        if "UPGRADE" in name or "DOWNGRADE" in name:
            return "TIER_CHANGE"
        if "PT" in name:
            return "PT_CHANGE"
        if "EARNINGS" in name:
            return "EARNINGS"
        if "CALL VOLUME" in name or "PUT VOLUME" in name:
            return "OPTIONS_FLOW"
        if "SECTOR" in name:
            return "SECTOR"
        if "INSIDER" in name:
            return "INSIDER"
        if "GEOPOLITICAL" in name:
            return "GEOPOLITICAL"
        return "MIXED"
