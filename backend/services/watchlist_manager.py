"""Watchlist selection and rotation engine.

Scores ~70 stocks across 5 sectors, applies filters, and manages
the active 30-stock watchlist with daily rotation logic.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import (
    MAX_CHANGES_PER_DAY,
    MAX_PER_SECTOR,
    MIN_AVG_VOLUME,
    MIN_MARKET_CAP,
    MIN_OPTIONS_VOLUME,
    SECTORS,
    TOXIC_CONVICTION_DAYS,
)
from db.models import OptionsSnapshot, Recommendation, Sector, UniverseStock, Watchlist, WatchlistDailySnapshot
from services.earnings_calendar import EarningsCalendarService
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
        """Score every ticker in the universe (DB-backed).

        Reads the active universe from ``universe_stocks``, falling back
        to ``config.SECTORS`` if the table is empty (first run before seed).
        """
        scored: list[dict] = []

        # Load universe from DB
        us_result = await self._session.execute(
            select(UniverseStock)
            .where(UniverseStock.is_active.is_(True))
            .options(selectinload(UniverseStock.sector))
        )
        universe = list(us_result.scalars().all())

        # Fallback to config if DB universe is empty
        if not universe:
            logger.warning("universe_stocks table is empty — falling back to config.SECTORS")
            ticker_sector_pairs = [
                (ticker, sector_name)
                for sector_name, cfg in SECTORS.items()
                for ticker in cfg["universe"]
            ]
        else:
            ticker_sector_pairs = [(us.ticker, us.sector.name) for us in universe]
            logger.info("Scoring %d universe stocks from DB", len(ticker_sector_pairs))

        # Batch-load latest recommendation convictions for all tickers
        conviction_map = await self._get_latest_convictions()

        # Build a lookup for lazy company_name backfill
        us_map = {us.ticker: us for us in universe} if universe else {}

        for ticker, sector_name in ticker_sector_pairs:
            row = await self._score_one(ticker, sector_name, conviction_map, us_map)
            if row is not None:
                scored.append(row)

        return scored

    async def score_candidates(self) -> list[dict]:
        """Score only universe stocks NOT currently on the active watchlist.

        Used by the manual rotate-out preview to pick the next-in-line
        replacement. Scoring just the (typically small) non-watchlist set —
        concurrently, bounded by a semaphore — keeps the preview interactive
        instead of re-scoring the entire universe like ``score_universe`` does
        for the daily batch rotation.
        """
        us_result = await self._session.execute(
            select(UniverseStock)
            .where(UniverseStock.is_active.is_(True))
            .options(selectinload(UniverseStock.sector))
        )
        universe = list(us_result.scalars().all())
        if not universe:
            return []

        active = {w.ticker for w in await self.get_active_watchlist()}
        conviction_map = await self._get_latest_convictions()
        us_map = {us.ticker: us for us in universe}
        pairs = [
            (us.ticker, us.sector.name)
            for us in universe
            if us.ticker not in active and us.sector is not None
        ]

        # Bound concurrency — DataSourceClient is already used concurrently by
        # the news scanner, so parallel get_stock_data calls are safe.
        sem = asyncio.Semaphore(8)

        async def _worker(t: str, s: str):
            async with sem:
                return await self._score_one(t, s, conviction_map, us_map)

        results = await asyncio.gather(*(_worker(t, s) for t, s in pairs))
        return [r for r in results if r is not None]

    async def _score_one(
        self,
        ticker: str,
        sector_name: str,
        conviction_map: dict[str, float],
        us_map: dict,
    ) -> dict | None:
        """Score a single universe ticker. Returns the scored dict or None on error."""
        loop = asyncio.get_event_loop()
        try:
            stock = await loop.run_in_executor(
                None, self._data_client.get_stock_data, ticker
            )

            # Lazy-fill company_name on UniverseStock if missing
            us_row = us_map.get(ticker)
            if us_row and not us_row.company_name and stock.get("company_name"):
                us_row.company_name = stock["company_name"]

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

            # Map conviction (-100..+100) → sub-score (0..100)
            # No prior recommendation → neutral (50)
            raw_conviction = conviction_map.get(ticker)
            if raw_conviction is not None:
                recommendation_conviction = (raw_conviction + 100.0) / 2.0
            else:
                recommendation_conviction = 50.0

            sub_scores: dict[str, float] = {
                "catalyst_proximity": catalyst_proximity,
                "analyst_momentum": analyst_momentum,
                "recommendation_conviction": recommendation_conviction,
                "options_liquidity": options_liquidity,
                "sector_momentum": sector_momentum,
                "volatility_profile": volatility_profile,
                "institutional_flow": institutional_flow,
                "price_vs_consensus_pt": price_vs_consensus_pt,
            }

            composite = calculate_composite_score(sub_scores)

            return {
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
        except Exception:
            logger.exception("Failed to score ticker %s — skipping", ticker)
            return None

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

        # Determine additions: in candidates but not in active watchlist.
        # Prefer stocks with catalysts/rating changes, but also allow
        # high-scoring stocks to fill the watchlist up to target size.
        raw_additions_priority = [
            c for c in candidates
            if c["ticker"] not in active_tickers
            and (c["has_catalyst_14d"] or c["has_recent_rating_change"])
        ]
        raw_additions_backfill = [
            c for c in candidates
            if c["ticker"] not in active_tickers
            and not c["has_catalyst_14d"]
            and not c["has_recent_rating_change"]
        ]
        raw_additions_priority.sort(key=lambda x: x["composite_score"], reverse=True)
        raw_additions_backfill.sort(key=lambda x: x["composite_score"], reverse=True)

        # Priority adds first, then backfill if watchlist is below target (30)
        target_size = MAX_PER_SECTOR * len(by_sector)
        current_size = len(active_tickers)
        raw_additions = list(raw_additions_priority)
        if current_size + len(raw_additions) < target_size:
            slots = target_size - current_size - len(raw_additions)
            raw_additions.extend(raw_additions_backfill[:slots])

        # Determine removals: in active watchlist but not in candidates,
        # stale (>30 days, no catalyst, no open recommendation),
        # or toxic (consecutive SELL/STRONG_SELL recommendations)
        today = date.today()
        open_rec_tickers = await self._get_open_recommendation_tickers()
        toxic_tickers = await self._get_toxic_tickers()
        unusual_options_tickers = await self._get_unusual_options_tickers()

        raw_removals: list[dict] = []
        for ticker, wl in active_map.items():
            if wl.is_manual or wl.is_locked:
                continue  # never auto-remove manual or locked entries
            if ticker in unusual_options_tickers and ticker not in toxic_tickers:
                logger.info("Protecting %s from rotation — unusual options activity", ticker)
                continue  # unusual options activity signals pending move
            if ticker not in candidate_tickers:
                raw_removals.append({"ticker": ticker, "reason": "dropped from top candidates"})
                continue

            # Toxic removal: consecutive bearish recommendations
            if ticker in toxic_tickers:
                raw_removals.append({
                    "ticker": ticker,
                    "reason": f"toxic — {TOXIC_CONVICTION_DAYS}+ consecutive SELL/STRONG_SELL",
                })
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

    # ------------------------------------------------------------------
    # Manual rotate-out: replacement proposal + imminent-potential gate
    # ------------------------------------------------------------------

    @staticmethod
    def _build_candidate(cand: dict, *, cross_sector: bool) -> dict:
        """Shape a scored-universe entry into a replacement-candidate dict."""
        reasons: list[str] = []
        if cand.get("has_catalyst_14d"):
            reasons.append("catalyst within 14 days")
        if cand.get("has_recent_rating_change"):
            reasons.append("recent rating change")
        reasons.append(f"composite_score={cand['composite_score']:.1f}")
        return {
            "ticker": cand["ticker"],
            "company_name": cand.get("company_name"),
            "sector_name": cand.get("sector_name"),
            "composite_score": round(float(cand["composite_score"]), 2),
            "reasons": reasons,
            "cross_sector": cross_sector,
        }

    async def propose_replacements(
        self, remove_tickers: list[str], n_per: int = 1
    ) -> dict[str, list[dict]]:
        """Propose the strongest next-in-line candidate(s) per removed ticker.

        For each ticker being rotated out, picks the highest-composite-score
        universe candidate NOT already on the active watchlist, preferring a
        stock in the SAME sector as the removed ticker (keeps the
        sector-balanced watchlist intact). Falls back to the globally highest
        candidate (flagged ``cross_sector``) when the sector is exhausted.

        Candidates are de-duplicated across removals so two simultaneous
        removals never resolve to the same replacement.

        Returns ``{remove_ticker: [candidate_dict, ...]}`` — an empty list
        when no eligible candidate remains.
        """
        # Score only non-watchlist stocks (fast) rather than the whole universe.
        scored = await self.score_candidates()
        filtered = await self.apply_filters(scored)

        active_rows = await self.get_active_watchlist()
        active_tickers = {w.ticker for w in active_rows}
        sector_of: dict[str, str | None] = {
            w.ticker: (w.sector.name if w.sector else None) for w in active_rows
        }

        # Candidates not already active, best-first, grouped by sector.
        eligible = [c for c in filtered if c["ticker"] not in active_tickers]
        eligible.sort(key=lambda x: x["composite_score"], reverse=True)
        by_sector: dict[str | None, list[dict]] = {}
        for c in eligible:
            by_sector.setdefault(c["sector_name"], []).append(c)

        chosen: set[str] = set()
        proposals: dict[str, list[dict]] = {}

        for ticker in remove_tickers:
            picks: list[dict] = []
            sector_name = sector_of.get(ticker)

            # Same-sector candidates first.
            for cand in by_sector.get(sector_name, []):
                if len(picks) >= n_per:
                    break
                if cand["ticker"] in chosen:
                    continue
                picks.append(self._build_candidate(cand, cross_sector=False))
                chosen.add(cand["ticker"])

            # Global fallback when the sector is exhausted.
            if len(picks) < n_per:
                for cand in eligible:
                    if len(picks) >= n_per:
                        break
                    if cand["ticker"] in chosen:
                        continue
                    picks.append(self._build_candidate(cand, cross_sector=True))
                    chosen.add(cand["ticker"])

            proposals[ticker] = picks

        return proposals

    async def evaluate_rotation_gate(
        self,
        ticker: str,
        *,
        unusual_tickers: set[str] | None = None,
        earnings_service: EarningsCalendarService | None = None,
    ) -> dict:
        """Evaluate whether *ticker* has 'strong imminent potential'.

        Hard-blocks rotation when ANY composite condition holds:
          1. latest recommendation ``entry_strategy == PRE_POSITION``
          2. a leading positioning signal fired (Smart Money Positioning /
             Analyst Revision Cluster / Insider Buy Cluster, points > 0)
          3. an active earnings catalyst window (T-14..T-0)
          4. unusual options activity (today's call vol >= 3x 20d avg)
          5. bullish high conviction (BUY/STRONG_BUY with conviction >= 40)

        Returns ``{ticker, blocked, reasons}``. ``unusual_tickers`` and
        ``earnings_service`` may be supplied by a batch caller to avoid
        recomputing them per ticker.
        """
        reasons: list[str] = []

        rec_result = await self._session.execute(
            select(Recommendation)
            .where(Recommendation.ticker == ticker)
            .order_by(Recommendation.recommendation_date.desc())
            .limit(1)
        )
        rec = rec_result.scalar_one_or_none()

        if rec is not None:
            if (rec.entry_strategy or "").strip() == "PRE_POSITION":
                reasons.append("active PRE_POSITION entry strategy")

            # Leading positioning signals — import locally to avoid any
            # import-order coupling with the recommendation engine module.
            from services.recommendation_engine import LEADING_PRE_POSITION_SIGNALS

            signals = rec.signals if isinstance(rec.signals, list) else []
            leading = [
                s.get("signal")
                for s in signals
                if isinstance(s, dict)
                and s.get("signal") in LEADING_PRE_POSITION_SIGNALS
                and (s.get("points") or 0) > 0
            ]
            if leading:
                reasons.append(f"leading signal: {', '.join(leading)}")

            conviction = (
                float(rec.conviction_score)
                if rec.conviction_score is not None
                else 0.0
            )
            if rec.action in ("BUY", "STRONG_BUY") and conviction >= 40:
                reasons.append(
                    f"bullish high conviction ({rec.action} @ {conviction:.0f})"
                )

        # Active earnings catalyst window.
        if earnings_service is None:
            earnings_service = EarningsCalendarService(self._session, self._data_client)
        try:
            window = await earnings_service.get_catalyst_window(ticker)
            if window and window.get("window_status") == "ACTIVE":
                reasons.append("active earnings catalyst window")
        except Exception:
            logger.exception("Gate: catalyst window lookup failed for %s", ticker)

        # Unusual options activity.
        if unusual_tickers is None:
            unusual_tickers = await self._get_unusual_options_tickers()
        if ticker in unusual_tickers:
            reasons.append("unusual options activity")

        return {"ticker": ticker, "blocked": len(reasons) > 0, "reasons": reasons}

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

    async def _get_latest_convictions(self) -> dict[str, float]:
        """Return the most recent conviction score for each ticker.

        Used to feed recommendation output back into watchlist scoring.
        Maps conviction (-100..+100) → raw value; caller converts to 0-100.
        """
        try:
            # Subquery: latest recommendation date per ticker
            latest_sq = (
                select(
                    Recommendation.ticker,
                    sa_func.max(Recommendation.recommendation_date).label("max_date"),
                )
                .group_by(Recommendation.ticker)
                .subquery()
            )
            result = await self._session.execute(
                select(Recommendation.ticker, Recommendation.conviction_score)
                .join(
                    latest_sq,
                    (Recommendation.ticker == latest_sq.c.ticker)
                    & (Recommendation.recommendation_date == latest_sq.c.max_date),
                )
            )
            return {
                row.ticker: float(row.conviction_score)
                for row in result.all()
                if row.conviction_score is not None
            }
        except Exception:
            logger.exception("Failed to query latest convictions")
            return {}

    async def _get_toxic_tickers(self) -> set[str]:
        """Return tickers with N+ consecutive SELL/STRONG_SELL recommendations.

        These should be flagged for removal regardless of composite score.
        """
        try:
            cutoff = date.today() - timedelta(days=TOXIC_CONVICTION_DAYS + 5)
            result = await self._session.execute(
                select(
                    Recommendation.ticker,
                    Recommendation.action,
                    Recommendation.recommendation_date,
                )
                .where(Recommendation.recommendation_date >= cutoff)
                .order_by(Recommendation.ticker, Recommendation.recommendation_date.desc())
            )
            rows = result.all()

            # Group by ticker, check if last N recs are all SELL/STRONG_SELL
            by_ticker: dict[str, list[str]] = {}
            for row in rows:
                by_ticker.setdefault(row.ticker, []).append(row.action)

            toxic = set()
            for ticker, actions in by_ticker.items():
                # actions are ordered most-recent-first
                consecutive_bearish = 0
                for action in actions:
                    if action in ("SELL", "STRONG_SELL"):
                        consecutive_bearish += 1
                    else:
                        break
                if consecutive_bearish >= TOXIC_CONVICTION_DAYS:
                    toxic.add(ticker)

            return toxic
        except Exception:
            logger.exception("Failed to query toxic tickers")
            return set()

    async def _get_unusual_options_tickers(self) -> set[str]:
        """Return tickers where today's call volume is ≥3x the 20-day avg.

        These are protected from auto-rotation — strongly elevated call
        volume often precedes large moves that the signal engine hasn't
        scored yet.  Uses the same historical average as the recommendation
        engine but with a higher threshold (3x vs 1.5x) so only genuinely
        unusual activity blocks rotation.
        """
        try:
            from sqlalchemy import case, cast, Float

            cutoff_20d = datetime.now() - timedelta(days=20)
            today_start = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            # Subquery: 20-day historical avg call volume per ticker (excluding today)
            hist_avg = (
                select(
                    OptionsSnapshot.ticker,
                    sa_func.avg(
                        cast(OptionsSnapshot.total_call_volume, Float)
                    ).label("avg_call"),
                )
                .where(
                    OptionsSnapshot.snapshot_time >= cutoff_20d,
                    OptionsSnapshot.snapshot_time < today_start,
                )
                .group_by(OptionsSnapshot.ticker)
                .subquery()
            )

            # Today's latest snapshot per ticker
            today_snap = (
                select(
                    OptionsSnapshot.ticker,
                    sa_func.max(OptionsSnapshot.total_call_volume).label("today_call"),
                )
                .where(OptionsSnapshot.snapshot_time >= today_start)
                .group_by(OptionsSnapshot.ticker)
                .subquery()
            )

            # Join and filter: today_call >= 3 * avg_call
            result = await self._session.execute(
                select(today_snap.c.ticker).join(
                    hist_avg, today_snap.c.ticker == hist_avg.c.ticker
                ).where(
                    hist_avg.c.avg_call > 0,
                    today_snap.c.today_call >= 3 * hist_avg.c.avg_call,
                )
            )
            tickers = {row[0] for row in result.all()}
            if tickers:
                logger.info(
                    "Unusual options activity (≥3x avg): %s",
                    ", ".join(sorted(tickers)),
                )
            return tickers
        except Exception:
            logger.exception("Failed to query unusual options tickers")
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

        # Persist additions (guard against duplicate on re-runs)
        for entry in additions:
            sector_id = sector_map.get(entry["sector_name"])
            reasons: list[str] = []
            if entry.get("has_catalyst_14d"):
                reasons.append("catalyst within 14 days")
            if entry.get("has_recent_rating_change"):
                reasons.append("recent rating change")
            reasons.append(f"composite_score={entry['composite_score']:.1f}")

            dup_result = await self._session.execute(
                select(Watchlist).where(
                    Watchlist.ticker == entry["ticker"],
                    Watchlist.added_date == today,
                )
            )
            dup_row = dup_result.scalar_one_or_none()
            if dup_row:
                dup_row.is_active = True
                dup_row.removed_date = None
                dup_row.entry_reason = "; ".join(reasons)
            else:
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

        # Build sector lookup for existing/removed items
        active_sector_map: dict[str, str | None] = {}
        for wl in await self.get_active_watchlist():
            active_sector_map[wl.ticker] = wl.sector.name if wl.sector else None

        # Snapshot — delete previous rows for today (overwrite on re-runs)
        await self._session.execute(
            delete(WatchlistDailySnapshot).where(
                WatchlistDailySnapshot.snapshot_date == today,
            )
        )
        for entry in existing:
            self._session.add(
                WatchlistDailySnapshot(
                    snapshot_date=today,
                    ticker=entry["ticker"],
                    status="EXISTING",
                    sector=active_sector_map.get(entry["ticker"]) or entry.get("sector_name"),
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
                    sector=active_sector_map.get(entry["ticker"]),
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
