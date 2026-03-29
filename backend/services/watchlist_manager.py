"""Watchlist selection and rotation engine.

Scores ~70 stocks across 5 sectors, applies filters, and manages
the active 30-stock watchlist with daily rotation logic.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import (
    MAX_CHANGES_PER_DAY,
    MAX_PER_SECTOR,
    MIN_AVG_VOLUME,
    MIN_MARKET_CAP,
    MIN_OPTIONS_VOLUME,
    SECTORS,
)
from db.models import Recommendation, Sector, Watchlist, WatchlistDailySnapshot
from utils.data_sources import DataSourceClient
from utils.scoring import calculate_composite_score

logger = logging.getLogger(__name__)


class WatchlistManager:
    """Scores, filters, and rotates the active watchlist."""

    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data_client = data_client

    # ------------------------------------------------------------------
    # 1. Score the full universe
    # ------------------------------------------------------------------

    async def score_universe(self) -> list[dict]:
        """Score every ticker in the config universe.

        Calls ``data_client.get_stock_data`` (sync) via an executor and
        computes sub-scores that feed into the composite score.
        Returns a list of enriched dicts, one per ticker.
        """
        loop = asyncio.get_event_loop()
        scored: list[dict] = []

        for sector_name, sector_cfg in SECTORS.items():
            for ticker in sector_cfg["universe"]:
                try:
                    stock = await loop.run_in_executor(
                        None, self._data_client.get_stock_data, ticker
                    )

                    # Fetch earnings calendar for catalyst proximity
                    earnings_list = await loop.run_in_executor(
                        None, self._data_client.get_earnings_calendar, [ticker]
                    )

                    # Fetch analyst ratings for momentum / rating-change detection
                    ratings = await loop.run_in_executor(
                        None, self._data_client.get_analyst_ratings, ticker
                    )

                    # --- Sub-scores (0-100) --------------------------------

                    catalyst_proximity = self._score_catalyst_proximity(earnings_list)
                    analyst_momentum = self._score_analyst_momentum(stock, ratings)
                    options_liquidity = self._score_options_liquidity(stock)
                    sector_momentum = self._score_sector_momentum(stock)
                    volatility_profile = self._score_volatility_profile(stock)
                    institutional_flow = 50.0  # hard to get freely
                    price_vs_consensus_pt = self._score_price_vs_pt(stock)

                    sub_scores: dict[str, float] = {
                        "catalyst_proximity": catalyst_proximity,
                        "analyst_momentum": analyst_momentum,
                        "options_liquidity": options_liquidity,
                        "sector_momentum": sector_momentum,
                        "volatility_profile": volatility_profile,
                        "institutional_flow": institutional_flow,
                        "price_vs_consensus_pt": price_vs_consensus_pt,
                    }

                    composite = calculate_composite_score(sub_scores)

                    scored.append(
                        {
                            "ticker": ticker,
                            "sector_name": sector_name,
                            "company_name": stock.get("company_name"),
                            "market_cap": stock.get("market_cap"),
                            "avg_volume": stock.get("avg_volume"),
                            "price": stock.get("price"),
                            "beta": stock.get("beta"),
                            "analyst_count": stock.get("analyst_count"),
                            "avg_analyst_target": stock.get("avg_analyst_target"),
                            "composite_score": composite,
                            "has_catalyst_14d": catalyst_proximity >= 70,
                            "has_recent_rating_change": self._has_recent_rating_change(ratings),
                            **sub_scores,
                        }
                    )

                except Exception:
                    logger.exception("Failed to score ticker %s — skipping", ticker)

        return scored

    # ------------------------------------------------------------------
    # Sub-score helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _score_catalyst_proximity(earnings_list: list[dict]) -> float:
        """100 if earnings within 7d, 70 within 14d, 30 within 30d, else 0."""
        today = date.today()
        for entry in earnings_list:
            raw = entry.get("earnings_date")
            if not raw:
                continue
            try:
                earnings_dt = (
                    datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
                    if "T" in str(raw)
                    else datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
                )
            except (ValueError, TypeError):
                continue

            days_away = (earnings_dt - today).days
            if days_away < 0:
                continue
            if days_away <= 7:
                return 100.0
            if days_away <= 14:
                return 70.0
            if days_away <= 30:
                return 30.0
        return 0.0

    @staticmethod
    def _score_analyst_momentum(stock: dict, ratings: list[dict]) -> float:
        """Based on analyst count and distance from consensus PT."""
        analyst_count = stock.get("analyst_count") or 0
        price = stock.get("price") or 0
        target = stock.get("avg_analyst_target") or 0

        # Coverage score component (0-50)
        coverage = min(analyst_count / 20.0, 1.0) * 50.0

        # PT upside component (0-50)
        if price and target and price > 0:
            upside_pct = (target - price) / price * 100.0
            pt_component = max(0.0, min(upside_pct / 40.0, 1.0)) * 50.0
        else:
            pt_component = 25.0  # neutral

        # Recent rating-change bonus
        bonus = 10.0 if ratings else 0.0

        return min(coverage + pt_component + bonus, 100.0)

    @staticmethod
    def _score_options_liquidity(stock: dict) -> float:
        """Scale avg_volume relative to MIN_OPTIONS_VOLUME threshold."""
        vol = stock.get("avg_volume") or 0
        if vol <= 0:
            return 0.0
        # Use avg_volume as proxy; scale so MIN_OPTIONS_VOLUME * 5000 => 100
        ratio = vol / (MIN_OPTIONS_VOLUME * 5000)
        return min(ratio * 100.0, 100.0)

    @staticmethod
    def _score_sector_momentum(stock: dict) -> float:
        """Use price vs 52-week range as proxy for recent momentum."""
        price = stock.get("price")
        high = stock.get("fifty_two_week_high")
        low = stock.get("fifty_two_week_low")
        if not price or not high or not low or high == low:
            return 50.0
        pct_in_range = (price - low) / (high - low)
        # >0.9 in range => strong momentum (~100), 0.5 => 50, <0.1 => ~0
        return max(0.0, min(pct_in_range * 100.0, 100.0))

    @staticmethod
    def _score_volatility_profile(stock: dict) -> float:
        """Scale beta: 1.5+ = 100, 1.0 = 50, 0.5 = 0."""
        beta = stock.get("beta")
        if beta is None:
            return 50.0
        return max(0.0, min((beta - 0.5) / 1.0 * 100.0, 100.0))

    @staticmethod
    def _score_price_vs_pt(stock: dict) -> float:
        """Discount to analyst PT: >20% below = 100, at PT = 0."""
        price = stock.get("price")
        target = stock.get("avg_analyst_target")
        if not price or not target or price <= 0:
            return 50.0
        discount_pct = (target - price) / target * 100.0
        if discount_pct <= 0:
            return 0.0
        return min(discount_pct / 20.0 * 100.0, 100.0)

    @staticmethod
    def _has_recent_rating_change(ratings: list[dict]) -> bool:
        """True if any rating was published in the last 14 days."""
        cutoff = datetime.now() - timedelta(days=14)
        for r in ratings:
            raw = r.get("published_at")
            if not raw:
                continue
            try:
                pub = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if pub.tzinfo:
                    pub = pub.replace(tzinfo=None)
                if pub >= cutoff:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    # ------------------------------------------------------------------
    # 2. Apply filters
    # ------------------------------------------------------------------

    async def apply_filters(self, scored: list[dict]) -> list[dict]:
        """Remove stocks failing market cap or volume thresholds."""
        filtered: list[dict] = []
        for s in scored:
            mcap = s.get("market_cap") or 0
            vol = s.get("avg_volume") or 0
            if mcap < MIN_MARKET_CAP:
                logger.debug("Filtered %s: market_cap %s < %s", s["ticker"], mcap, MIN_MARKET_CAP)
                continue
            if vol < MIN_AVG_VOLUME:
                logger.debug("Filtered %s: avg_volume %s < %s", s["ticker"], vol, MIN_AVG_VOLUME)
                continue
            filtered.append(s)
        return filtered

    # ------------------------------------------------------------------
    # 3. Select watchlist (additions / removals / existing)
    # ------------------------------------------------------------------

    async def select_watchlist(
        self, filtered: list[dict]
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Determine additions, removals, and continuing watchlist members.

        Returns (additions, removals, existing).
        """
        # Sort by composite_score DESC within each sector, take top MAX_PER_SECTOR
        by_sector: dict[str, list[dict]] = {}
        for s in filtered:
            by_sector.setdefault(s["sector_name"], []).append(s)

        candidates: list[dict] = []
        for sector_name, stocks in by_sector.items():
            stocks.sort(key=lambda x: x["composite_score"], reverse=True)
            candidates.extend(stocks[:MAX_PER_SECTOR])

        candidate_tickers = {c["ticker"] for c in candidates}

        # Load current active watchlist from DB
        current_active = await self.get_active_watchlist()
        active_tickers = {w.ticker for w in current_active}
        active_map: dict[str, Watchlist] = {w.ticker: w for w in current_active}

        # Determine additions: in candidates but not in active watchlist
        raw_additions = [
            c for c in candidates
            if c["ticker"] not in active_tickers
            and (c["has_catalyst_14d"] or c["has_recent_rating_change"])
        ]
        raw_additions.sort(key=lambda x: x["composite_score"], reverse=True)

        # Determine removals: in active watchlist but not in candidates,
        # or stale (>30 days, no catalyst, no open recommendation)
        today = date.today()
        open_rec_tickers = await self._get_open_recommendation_tickers()

        raw_removals: list[dict] = []
        for ticker, wl in active_map.items():
            if ticker not in candidate_tickers:
                raw_removals.append({"ticker": ticker, "reason": "dropped from top candidates"})
                continue

            days_on = (today - wl.added_date).days if wl.added_date else 0
            if days_on > 30 and ticker not in open_rec_tickers:
                # Check if the candidate version still has a catalyst
                cand = next((c for c in candidates if c["ticker"] == ticker), None)
                if cand and not cand["has_catalyst_14d"]:
                    raw_removals.append({"ticker": ticker, "reason": "stale — >30 days, no catalyst"})

        # Cap total changes
        total_changes = len(raw_additions) + len(raw_removals)
        if total_changes > MAX_CHANGES_PER_DAY:
            # Prefer additions of high-scoring stocks over removals
            budget = MAX_CHANGES_PER_DAY
            additions = raw_additions[: budget]
            remaining = budget - len(additions)
            removals = raw_removals[: remaining]
        else:
            additions = raw_additions
            removals = raw_removals

        # Existing = active tickers minus removals
        removal_tickers = {r["ticker"] for r in removals}
        existing = [
            {"ticker": w.ticker, "company_name": w.company_name, "sector_name": None}
            for w in current_active
            if w.ticker not in removal_tickers
        ]

        return additions, removals, existing

    async def _get_open_recommendation_tickers(self) -> set[str]:
        """Return tickers with a recent (last 7 days) recommendation."""
        cutoff = date.today() - timedelta(days=7)
        try:
            result = await self._session.execute(
                select(Recommendation.ticker).where(
                    Recommendation.recommendation_date >= cutoff
                )
            )
            return {row[0] for row in result.all()}
        except Exception:
            logger.exception("Failed to query open recommendations")
            return set()

    # ------------------------------------------------------------------
    # 4. Rotate watchlist (orchestrator)
    # ------------------------------------------------------------------

    async def rotate_watchlist(self) -> dict[str, Any]:
        """Full rotation: score, filter, select, persist, snapshot."""
        logger.info("Starting watchlist rotation")

        scored = await self.score_universe()
        logger.info("Scored %d tickers", len(scored))

        filtered = await self.apply_filters(scored)
        logger.info("After filters: %d tickers remain", len(filtered))

        additions, removals, existing = await self.select_watchlist(filtered)
        logger.info(
            "Rotation plan: +%d additions, -%d removals, %d existing",
            len(additions), len(removals), len(existing),
        )

        today = date.today()

        # Resolve sector IDs
        sector_map = await self._get_or_create_sector_map()

        # Persist additions
        for entry in additions:
            sector_id = sector_map.get(entry["sector_name"])
            reasons: list[str] = []
            if entry.get("has_catalyst_14d"):
                reasons.append("catalyst within 14 days")
            if entry.get("has_recent_rating_change"):
                reasons.append("recent rating change")
            reasons.append(f"composite_score={entry['composite_score']:.1f}")

            wl = Watchlist(
                ticker=entry["ticker"],
                company_name=entry.get("company_name"),
                sector_id=sector_id,
                added_date=today,
                is_active=True,
                entry_reason="; ".join(reasons),
            )
            self._session.add(wl)

        # Persist removals
        for entry in removals:
            result = await self._session.execute(
                select(Watchlist).where(
                    Watchlist.ticker == entry["ticker"],
                    Watchlist.is_active.is_(True),
                )
            )
            wl_row = result.scalar_one_or_none()
            if wl_row:
                wl_row.is_active = False
                wl_row.removed_date = today
                wl_row.exit_reason = entry.get("reason", "rotation")

        # Snapshot
        for entry in existing:
            self._session.add(
                WatchlistDailySnapshot(
                    snapshot_date=today,
                    ticker=entry["ticker"],
                    status="EXISTING",
                    sector=entry.get("sector_name"),
                )
            )
        for entry in additions:
            self._session.add(
                WatchlistDailySnapshot(
                    snapshot_date=today,
                    ticker=entry["ticker"],
                    status="NEW_ENTRANT",
                    sector=entry.get("sector_name"),
                )
            )
        for entry in removals:
            self._session.add(
                WatchlistDailySnapshot(
                    snapshot_date=today,
                    ticker=entry["ticker"],
                    status="REMOVED",
                    sector=None,
                )
            )

        await self._session.commit()
        logger.info("Watchlist rotation committed successfully")

        return {
            "date": str(today),
            "scored_total": len(scored),
            "filtered_total": len(filtered),
            "additions": len(additions),
            "removals": len(removals),
            "existing": len(existing),
            "active_total": len(existing) + len(additions),
            "added_tickers": [e["ticker"] for e in additions],
            "removed_tickers": [e["ticker"] for e in removals],
        }

    # ------------------------------------------------------------------
    # 5. Get active watchlist
    # ------------------------------------------------------------------

    async def get_active_watchlist(self) -> list[Watchlist]:
        """Return all active watchlist entries with sector info loaded."""
        result = await self._session.execute(
            select(Watchlist)
            .where(Watchlist.is_active.is_(True))
            .options(selectinload(Watchlist.sector))
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_or_create_sector_map(self) -> dict[str, int]:
        """Return {sector_name: sector_id}, creating rows if needed."""
        result = await self._session.execute(select(Sector))
        existing: dict[str, int] = {s.name: s.id for s in result.scalars().all()}

        for sector_name, cfg in SECTORS.items():
            if sector_name not in existing:
                sector = Sector(name=sector_name, max_stocks=cfg["max_stocks"])
                self._session.add(sector)
                await self._session.flush()
                existing[sector_name] = sector.id

        return existing
