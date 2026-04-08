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
    EarningsCalendar,
    MarketNews,
    OptionsSnapshot,
    Recommendation,
    SuggestedOption,
)
from services.analyst_tracker import AnalystTracker
from services.earnings_calendar import EarningsCalendarService
from services.news_scanner import NewsScanner
from services.options_analyzer import OptionsAnalyzer
from utils.data_sources import DataSourceClient

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

        ratings = await self._analyst_tracker.get_recent_ratings(
            ticker=ticker, hours=48
        )
        catalyst_window = await self._earnings_service.get_catalyst_window(ticker)
        news_sentiment = await self._news_scanner.get_sentiment_summary(
            ticker=ticker, hours=48
        )
        options_snapshot = await self._options_analyzer.get_latest_snapshot(ticker)

        signals_data: dict[str, Any] = {
            "ratings": ratings,
            "catalyst_window": catalyst_window,
            "news_sentiment": news_sentiment,
            "options_snapshot": options_snapshot,
            "stock_data": stock_data,
            "technicals": technicals,
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
        entry_strategy = self._determine_entry_strategy(
            conviction_score, catalyst_window, stock_moved_pct
        )

        # ── 5. Rationale ───────────────────────────────────────────────
        rationale = await self._generate_rationale(
            ticker, action, conviction_score, signal_details, stock_data
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

        ratings = await self._analyst_tracker.get_recent_ratings(
            ticker=ticker, hours=48
        )
        catalyst_window = await self._earnings_service.get_catalyst_window(ticker)
        news_sentiment = await self._news_scanner.get_sentiment_summary(
            ticker=ticker, hours=48
        )
        options_snapshot = await self._options_analyzer.get_latest_snapshot(ticker)

        signals_data: dict[str, Any] = {
            "ratings": ratings,
            "catalyst_window": catalyst_window,
            "news_sentiment": news_sentiment,
            "options_snapshot": options_snapshot,
            "stock_data": stock_data,
            "technicals": technicals,
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
        entry_strategy = self._determine_entry_strategy(
            conviction_score, catalyst_window, stock_moved_pct
        )

        # ── 5. Rationale ───────────────────────────────────────────────
        rationale = await self._generate_rationale(
            ticker, action, conviction_score, signal_details, stock_data
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
        # Delete existing recommendation for same date + ticker
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

        # Price vs 50-day SMA
        sma_50 = stock_data.get("sma_50")
        if current_price and sma_50 and sma_50 > 0:
            pct_vs_50 = ((current_price - sma_50) / sma_50) * 100
            if pct_vs_50 > 5:
                score += 10
                signals.append({
                    "signal": "Above 50d SMA",
                    "points": 10,
                    "detail": f"Price ${current_price:.2f} is {pct_vs_50:.1f}% above 50d SMA ${sma_50:.2f}",
                })
            elif pct_vs_50 < -5:
                score -= 10
                signals.append({
                    "signal": "Below 50d SMA",
                    "points": -10,
                    "detail": f"Price ${current_price:.2f} is {pct_vs_50:.1f}% below 50d SMA ${sma_50:.2f}",
                })

        # Price vs 200-day SMA
        sma_200 = stock_data.get("sma_200")
        if current_price and sma_200 and sma_200 > 0:
            pct_vs_200 = ((current_price - sma_200) / sma_200) * 100
            if pct_vs_200 > 5:
                score += 5
                signals.append({
                    "signal": "Above 200d SMA",
                    "points": 5,
                    "detail": f"Price {pct_vs_200:.1f}% above 200d SMA ${sma_200:.2f} — long-term uptrend",
                })
            elif pct_vs_200 < -5:
                score -= 5
                signals.append({
                    "signal": "Below 200d SMA",
                    "points": -5,
                    "detail": f"Price {pct_vs_200:.1f}% below 200d SMA ${sma_200:.2f} — long-term downtrend",
                })

        # 52-week position
        wk_high = stock_data.get("fifty_two_week_high")
        wk_low = stock_data.get("fifty_two_week_low")
        if current_price and wk_high and wk_low and wk_high > wk_low:
            pct_from_high = ((wk_high - current_price) / wk_high) * 100
            pct_from_low = ((current_price - wk_low) / wk_low) * 100
            if pct_from_high < 5:
                score += 5
                signals.append({
                    "signal": "Near 52w High",
                    "points": 5,
                    "detail": f"Within {pct_from_high:.1f}% of 52-week high ${wk_high:.2f} — breakout potential",
                })
            elif pct_from_high > 30:
                score += 10
                signals.append({
                    "signal": "Deep Pullback from 52w High",
                    "points": 10,
                    "detail": f"{pct_from_high:.0f}% below 52-week high ${wk_high:.2f} — potential value",
                })

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

        # Short interest
        short_pct = stock_data.get("short_pct_float")
        short_ratio = stock_data.get("short_ratio")
        if short_pct is not None and short_pct > 0.10:
            # High short interest — bearish pressure but squeeze potential if
            # combined with bullish momentum
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

        # ── News sentiment (scaled by magnitude and count) ──────────────
        avg_sentiment = news_sentiment.get("avg_sentiment", 0.0)
        article_count = news_sentiment.get("total_count", 0)

        if avg_sentiment > 0.3:
            # Scale: base 5, add up to 10 more for strong + high-count
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

        # ── Price relative to consensus PT ──────────────────────────────
        if current_price and avg_analyst_target:
            discount_pct = (
                (avg_analyst_target - current_price) / current_price
            ) * 100
            if discount_pct > 15:
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
        entry = self._determine_entry_strategy(
            conviction, catalyst_window, stock_moved_pct
        )
        entry_reasons = {
            "PRE_POSITION": "catalyst window active",
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

    def _determine_entry_strategy(
        self,
        conviction: float,
        catalyst_window: dict | None,
        stock_moved_pct: float,
    ) -> str:
        """Decide entry timing strategy."""
        if (
            catalyst_window
            and catalyst_window.get("window_status") == "ACTIVE"
            and conviction >= 30
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
        """Check for significant insider buying/selling and append signals."""
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

        for trade in trades:
            trade_date = trade.get("date", "")
            if trade_date < cutoff:
                continue
            value = abs(float(trade.get("value") or 0))
            tx_type = (trade.get("transaction_type") or "").upper()
            # P = Purchase, S = Sale
            if tx_type == "P":
                buying_value += value
            elif tx_type == "S":
                selling_value += value

        if buying_value > 500_000:
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
        return "MIXED"
