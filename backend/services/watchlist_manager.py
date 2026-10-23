"""Watchlist selection and rotation engine.

Scores the active ``universe_stocks`` (the hidden candidate / sector
dictionary), applies the liquidity filters and rotates the active watchlist
once per ET day under the rules in ``config.py`` (caps, hysteresis, minimum
stay, protections, daily change budget). See backend/services/CLAUDE.md.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import (
    DROP_TOXIC,
    ENTER_MARGIN,
    ENTER_RANK,
    EXIT_DAYS,
    EXIT_RANK,
    LOCK_DAYS,
    MAX_CHANGES_PER_DAY,
    MAX_PER_SECTOR,
    MAX_WATCHLIST,
    MIN_AVG_VOLUME,
    MIN_MARKET_CAP,
    MIN_OPTIONS_VOLUME,
    MIN_STAY_TRADING_DAYS,
    ROTATION_EXCLUDE,
    ROTATION_INPUTS_MAX_AGE_DAYS,
    SECTORS,
    TOXIC_CONVICTION_DAYS,
)
from db.models import (
    AnalystRating,
    EarningsCalendar,
    PipelineRunLog,
    Position,
    Recommendation,
    Sector,
    UniverseStock,
    Watchlist,
    WatchlistDailySnapshot,
)
from radar.calendar_nyse import ET, is_trading_day
from services.earnings_calendar import EarningsCalendarService
from services.unusual_options import get_unusual_options_tickers
from utils.data_sources import DataSourceClient
from utils.scoring import calculate_composite_score

logger = logging.getLogger(__name__)

# pipeline_run_log phase of the row an automatic rotation writes, in its own
# transaction, when it has run: the once-per-ET-day guard, and the record of
# which tickers had their scoring inputs re-fetched from the API.
ROTATION_MARKER_PHASE = "watchlist_rotation"

# entry_reason of a manual rotate-in (api/routes.py). Its is_locked is a grace
# lock that expires after config.LOCK_DAYS trading days.
GRACE_LOCK_REASON = "rotated in (manual; grace-locked)"


# ----------------------------------------------------------------------
# Module helpers (shared with api/routes.py and scheduler.py)
# ----------------------------------------------------------------------

def et_today(now: datetime | None = None) -> date:
    """Today's date in US/Eastern (the container clock is UTC)."""
    now = now or datetime.now(tz=timezone.utc)
    return now.astimezone(ET).date()


def trading_days_between(start: date | None, end: date) -> int:
    """NYSE trading days d with start < d <= end (0 when start is None or later)."""
    if start is None or start >= end:
        return 0
    n, d = 0, start + timedelta(days=1)
    while d <= end:
        if is_trading_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def is_grace_lock(wl: Watchlist) -> bool:
    return bool(wl.is_locked) and (wl.entry_reason or "") == GRACE_LOCK_REASON


async def get_open_position_tickers(session: AsyncSession) -> set[str]:
    """Tickers (underlyings) with an OPEN position of any user."""
    result = await session.execute(
        select(Position.ticker).where(Position.status == "OPEN").distinct()
    )
    return {str(t).upper() for (t,) in result.all() if t}


async def record_snapshot_change(
    session: AsyncSession,
    ticker: str,
    status: str,
    sector: str | None = None,
    snapshot_date: date | None = None,
) -> None:
    """Record a watchlist change in today's ``watchlist_daily_snapshot``.

    Never deletes other rows of the day. The table holds one row per
    (date, ticker), so a ticker that already has a row today gets its status
    updated (e.g. EXISTING -> REMOVED after a manual delete); otherwise a row
    is added.
    """
    snapshot_date = snapshot_date or date.today()
    result = await session.execute(
        select(WatchlistDailySnapshot).where(
            WatchlistDailySnapshot.snapshot_date == snapshot_date,
            WatchlistDailySnapshot.ticker == ticker,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        row.status = status
        if sector:
            row.sector = sector
    else:
        session.add(WatchlistDailySnapshot(
            snapshot_date=snapshot_date, ticker=ticker, status=status, sector=sector,
        ))


class WatchlistManager:
    """Scores, filters, and rotates the active watchlist."""

    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data_client = data_client
        # Details of the last scoring pass (read by rotate_watchlist).
        self.last_filtered_out: set[str] = set()   # failed market cap / volume
        self.last_score_errors: set[str] = set()   # stock data unavailable
        self.last_refreshed: list[str] = []        # inputs re-fetched from the API

    # ------------------------------------------------------------------
    # 1. Score the universe
    # ------------------------------------------------------------------

    async def _load_universe(self) -> tuple[list[tuple[str, str]], dict[str, UniverseStock]]:
        """(ticker, sector) pairs of the active universe minus ROTATION_EXCLUDE.

        Falls back to ``config.SECTORS`` if the table is empty (first run).
        """
        us_result = await self._session.execute(
            select(UniverseStock)
            .where(UniverseStock.is_active.is_(True))
            .options(selectinload(UniverseStock.sector))
        )
        universe = list(us_result.scalars().all())
        if not universe:
            logger.warning("universe_stocks table is empty — falling back to config.SECTORS")
            pairs = [
                (ticker, sector_name)
                for sector_name, cfg in SECTORS.items()
                for ticker in cfg["universe"]
            ]
            us_map: dict[str, UniverseStock] = {}
        else:
            pairs = [(us.ticker, us.sector.name) for us in universe if us.sector is not None]
            us_map = {us.ticker: us for us in universe}
        pairs = [(t, s) for t, s in pairs if t not in ROTATION_EXCLUDE]
        return pairs, us_map

    async def score_universe(self) -> list[dict]:
        """Score every rotation-eligible universe ticker that passes the filters."""
        pairs, us_map = await self._load_universe()
        logger.info("Scoring %d universe stocks", len(pairs))
        active = {w.ticker for w in await self.get_active_watchlist()}
        return await self._score_pairs(pairs, us_map, active)

    async def score_candidates(self) -> list[dict]:
        """Score only universe stocks NOT currently on the active watchlist.

        Used by the manual rotate-out preview to pick the next-in-line
        replacement.
        """
        pairs, us_map = await self._load_universe()
        if not us_map:
            return []
        active = {w.ticker for w in await self.get_active_watchlist()}
        pairs = [(t, s) for t, s in pairs if t not in active]
        return await self._score_pairs(pairs, us_map, active)

    async def _score_pairs(
        self,
        pairs: list[tuple[str, str]],
        us_map: dict[str, UniverseStock],
        active_tickers: set[str],
    ) -> list[dict]:
        """Score (ticker, sector) pairs.

        1. Stock data (yfinance, concurrent) — market cap, volume, price, beta.
        2. Cheap liquidity filter BEFORE any FMP call.
        3. Analyst grades + earnings dates from the analyst_ratings /
           earnings_calendar tables; re-fetched from the API (and stored)
           only for tickers whose inputs are older than
           ROTATION_INPUTS_MAX_AGE_DAYS.
        """
        self.last_filtered_out, self.last_score_errors, self.last_refreshed = set(), set(), []
        if not pairs:
            return []

        loop = asyncio.get_event_loop()
        sem = asyncio.Semaphore(8)

        async def _stock(t: str) -> tuple[str, dict | None]:
            async with sem:
                try:
                    return t, await loop.run_in_executor(None, self._data_client.get_stock_data, t)
                except Exception:
                    logger.exception("Stock data failed for %s", t)
                    return t, None

        stock_map = dict(await asyncio.gather(*(_stock(t) for t, _ in pairs)))

        passing: list[tuple[str, str, dict]] = []
        for ticker, sector_name in pairs:
            stock = stock_map.get(ticker)
            # get_stock_data returns {"ticker": t} on failure: unknown, not a filter miss.
            if not stock or "market_cap" not in stock:
                self.last_score_errors.add(ticker)
                continue
            if not self._passes_filters(stock):
                self.last_filtered_out.add(ticker)
                continue
            passing.append((ticker, sector_name, stock))

        tickers = [t for t, _, _ in passing]
        self.last_refreshed = await self._refresh_stale_inputs(tickers, active_tickers)
        earnings_map = await self._stored_earnings(tickers)
        ratings_map = await self._stored_ratings(tickers)
        conviction_map = await self._get_latest_convictions()

        scored: list[dict] = []
        for ticker, sector_name, stock in passing:
            us_row = us_map.get(ticker)
            try:  # lazy company_name backfill; best effort
                if us_row and not us_row.company_name and stock.get("company_name"):
                    us_row.company_name = stock["company_name"]
            except Exception:
                logger.debug("company_name backfill skipped for %s", ticker)
            row = self._score_stock(
                ticker, sector_name, stock,
                earnings_map.get(ticker, []), ratings_map.get(ticker, []),
                conviction_map,
            )
            scored.append(row)
        return scored

    @staticmethod
    def _passes_filters(stock: dict) -> bool:
        return (stock.get("market_cap") or 0) >= MIN_MARKET_CAP and (
            stock.get("avg_volume") or 0
        ) >= MIN_AVG_VOLUME

    async def _refresh_stale_inputs(self, tickers: list[str], active_tickers: set[str]) -> list[str]:
        """Re-fetch analyst grades + earnings dates for tickers whose stored
        inputs are stale, through the pipeline services (so they land in
        analyst_ratings / earnings_calendar). Returns the refreshed tickers.

        Fresh = refreshed within ROTATION_INPUTS_MAX_AGE_DAYS, either by the
        ratings + earnings pipeline phases (active watchlist members and names
        removed within that window) or by an earlier rotation (its marker row
        lists the tickers it refreshed).
        """
        if not tickers:
            return []
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=ROTATION_INPUTS_MAX_AGE_DAYS)
        fresh: set[str] = set()
        if await self._pipeline_inputs_fresh(cutoff):
            fresh |= active_tickers
            # Names that left the watchlist recently were refreshed by the
            # pipeline up to the day before they left.
            left = await self._session.execute(
                select(Watchlist.ticker).where(
                    Watchlist.removed_date > date.today() - timedelta(days=ROTATION_INPUTS_MAX_AGE_DAYS)
                )
            )
            fresh |= {t for (t,) in left.all()}
        markers = await self._rotation_markers(cutoff)
        for m in markers:
            fresh |= set((m.meta or {}).get("refreshed") or [])

        stale = [t for t in tickers if t not in fresh]
        if not stale:
            return []
        logger.info("Refreshing scoring inputs of %d tickers from the API", len(stale))
        from services.analyst_tracker import AnalystTracker

        try:
            await AnalystTracker(self._session, self._data_client).scan_ratings(stale)
        except Exception:
            logger.exception("Rotation: analyst grade refresh failed")
        try:
            await EarningsCalendarService(self._session, self._data_client).refresh_calendar(stale)
        except Exception:
            logger.exception("Rotation: earnings refresh failed")
        return stale

    async def _pipeline_inputs_fresh(self, cutoff: datetime) -> bool:
        """True when both the ratings and earnings phases succeeded since cutoff."""
        result = await self._session.execute(
            select(PipelineRunLog.phase).where(
                PipelineRunLog.phase.in_(("ratings", "earnings")),
                PipelineRunLog.status == "SUCCESS",
                PipelineRunLog.started_at >= cutoff,
            ).distinct()
        )
        return {p for (p,) in result.all()} == {"ratings", "earnings"}

    async def _rotation_markers(self, since: datetime) -> list[PipelineRunLog]:
        result = await self._session.execute(
            select(PipelineRunLog).where(
                PipelineRunLog.phase == ROTATION_MARKER_PHASE,
                PipelineRunLog.status == "SUCCESS",
                PipelineRunLog.started_at >= since,
            )
        )
        return list(result.scalars().all())

    async def _stored_earnings(self, tickers: list[str]) -> dict[str, list[dict]]:
        if not tickers:
            return {}
        result = await self._session.execute(
            select(EarningsCalendar.ticker, EarningsCalendar.earnings_date).where(
                EarningsCalendar.ticker.in_(tickers),
                EarningsCalendar.earnings_date >= date.today(),
            )
        )
        out: dict[str, list[dict]] = {}
        for ticker, d in result.all():
            out.setdefault(ticker, []).append({"earnings_date": d.isoformat()})
        for rows in out.values():
            rows.sort(key=lambda r: r["earnings_date"])
        return out

    async def _stored_ratings(self, tickers: list[str]) -> dict[str, list[dict]]:
        """Stored grade history reduced to what scoring reads: whether any
        grade exists (analyst momentum bonus) and the latest publish time
        (recent rating change)."""
        if not tickers:
            return {}
        result = await self._session.execute(
            select(AnalystRating.ticker, sa_func.max(AnalystRating.published_at))
            .where(AnalystRating.ticker.in_(tickers))
            .group_by(AnalystRating.ticker)
        )
        return {
            ticker: [{"published_at": latest.isoformat() if latest else ""}]
            for ticker, latest in result.all()
        }

    def _score_stock(
        self,
        ticker: str,
        sector_name: str,
        stock: dict,
        earnings_list: list[dict],
        ratings: list[dict],
        conviction_map: dict[str, float],
    ) -> dict:
        catalyst_proximity = self._score_catalyst_proximity(earnings_list)
        analyst_momentum = self._score_analyst_momentum(stock, ratings)
        options_liquidity = self._score_options_liquidity(stock)
        sector_momentum = self._score_sector_momentum(stock)
        volatility_profile = self._score_volatility_profile(stock)
        institutional_flow = 50.0  # hard to get freely
        price_vs_consensus_pt = self._score_price_vs_pt(stock)

        # Map conviction (-100..+100) → sub-score (0..100); no rec → neutral 50
        raw_conviction = conviction_map.get(ticker)
        recommendation_conviction = (
            (raw_conviction + 100.0) / 2.0 if raw_conviction is not None else 50.0
        )

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

        # Rating-history bonus
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
    # 3. Plan the rotation (additions / removals / existing)
    # ------------------------------------------------------------------

    async def select_watchlist(
        self, filtered: list[dict], score_errors: set[str] | None = None
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Plan today's additions and removals under the config rules.

        Side effects on the loaded watchlist rows (persisted by the caller's
        commit): ``weak_days`` is updated for every member, and expired grace
        locks are cleared.

        Order of the daily budget (MAX_CHANGES_PER_DAY):
          1. Over a cap: shrink — exits of the lowest-scoring removable names.
          2. Additions into free slots.
          3. Rule exits (weak for EXIT_DAYS runs; toxic if DROP_TOXIC).
          4. Additions into the slots step 3 freed.
          5. One-in-one-out swaps: an entrant replaces the weakest removable
             member of its sector when it beats it by ENTER_MARGIN.

        Returns (additions, removals, existing).
        """
        score_errors = score_errors or set()
        today = date.today()

        # Rank candidates within each sector (1 = best).
        by_sector: dict[str, list[dict]] = {}
        for s in filtered:
            by_sector.setdefault(s["sector_name"], []).append(s)
        rank_of: dict[str, int] = {}
        cand_of: dict[str, dict] = {}
        for stocks in by_sector.values():
            stocks.sort(key=lambda x: x["composite_score"], reverse=True)
            for i, s in enumerate(stocks, start=1):
                rank_of[s["ticker"]] = i
                cand_of[s["ticker"]] = s

        members = await self.get_active_watchlist()
        member_tickers = {w.ticker for w in members}
        open_positions = await get_open_position_tickers(self._session)
        unusual = await self._get_unusual_options_tickers()
        toxic = await self._get_toxic_tickers() if DROP_TOXIC else set()

        # Grace locks from a manual rotate-in expire after LOCK_DAYS trading days.
        for w in members:
            if is_grace_lock(w) and trading_days_between(w.added_date, today) >= LOCK_DAYS:
                logger.info("Grace lock of %s expired (added %s)", w.ticker, w.added_date)
                w.is_locked = False

        # Hysteresis counter: weak = filtered out / not ranked, or rank > EXIT_RANK.
        for w in members:
            if w.ticker in score_errors:
                continue  # no data today: neither weak nor strong
            weak = rank_of.get(w.ticker, EXIT_RANK + 1) > EXIT_RANK
            w.weak_days = (w.weak_days or 0) + 1 if weak else 0

        def sector_of(w: Watchlist) -> str | None:
            if w.sector is not None:
                return w.sector.name
            cand = cand_of.get(w.ticker)
            return cand["sector_name"] if cand else None

        def score_of(t: str) -> float | None:
            cand = cand_of.get(t)
            return float(cand["composite_score"]) if cand else None

        def protections(w: Watchlist) -> list[str]:
            reasons = []
            if w.is_manual:
                reasons.append("manual")
            if w.is_locked:
                reasons.append("locked")
            if w.ticker in open_positions:
                reasons.append("open position")
            if w.ticker in unusual and w.ticker not in toxic:
                reasons.append("unusual options activity")
            return reasons

        removable: dict[str, Watchlist] = {}
        for w in members:
            reasons = protections(w)
            if reasons:
                logger.debug("Protecting %s from rotation: %s", w.ticker, ", ".join(reasons))
                continue
            if trading_days_between(w.added_date, today) < MIN_STAY_TRADING_DAYS:
                continue
            removable[w.ticker] = w

        def low_first(w: Watchlist) -> tuple[float, str]:
            s = score_of(w.ticker)
            return (s if s is not None else float("-inf"), w.ticker)

        count = len(members)
        sec_count: Counter[str | None] = Counter(sector_of(w) for w in members)
        budget = MAX_CHANGES_PER_DAY
        removals: list[dict] = []
        additions: list[dict] = []
        removed: set[str] = set()

        def remove(w: Watchlist, reason: str) -> None:
            nonlocal count, budget
            removals.append({"ticker": w.ticker, "reason": reason, "sector_name": sector_of(w)})
            removed.add(w.ticker)
            count -= 1
            sec_count[sector_of(w)] -= 1
            budget -= 1

        def add(c: dict) -> None:
            nonlocal count, budget
            additions.append(c)
            count += 1
            sec_count[c["sector_name"]] += 1
            budget -= 1

        # Rule exits, in exit order: toxic first, then weak by lowest score.
        rule_exits: list[tuple[Watchlist, str]] = []
        for w in sorted(removable.values(), key=low_first):
            if w.ticker in toxic:
                rule_exits.insert(0, (w, f"toxic — {TOXIC_CONVICTION_DAYS}+ consecutive SELL/STRONG_SELL"))
            elif (w.weak_days or 0) >= EXIT_DAYS:
                rule_exits.append((w, f"weak — outside sector top {EXIT_RANK} for {w.weak_days} runs"))
        rule_reason = {w.ticker: reason for w, reason in rule_exits}

        # 1. Shrink while over a cap (sector caps first, then the total).
        def shrink_pool(sector: Any = ...) -> list[Watchlist]:
            # A member without stock data today is never dropped on unknown data.
            pool = [
                w for w in removable.values()
                if w.ticker not in removed and w.ticker not in score_errors
                and (sector is ... or sector_of(w) == sector)
            ]
            # Names already due to exit go first, then the lowest scores.
            return sorted(pool, key=lambda w: (w.ticker not in rule_reason, low_first(w)))

        for sector in sorted(s for s in sec_count if s is not None):
            for w in shrink_pool(sector):
                if sec_count[sector] <= MAX_PER_SECTOR or budget <= 0:
                    break
                remove(w, rule_reason.get(
                    w.ticker, f"over sector cap ({MAX_PER_SECTOR}) — lowest score"))
        for w in shrink_pool():
            if count <= MAX_WATCHLIST or budget <= 0:
                break
            remove(w, rule_reason.get(
                w.ticker, f"over watchlist cap ({MAX_WATCHLIST}) — lowest score"))

        # Entrants: eligible = sector top ENTER_RANK, or beats the weakest
        # removable member of the sector by ENTER_MARGIN. Catalyst / rating-
        # change names first, then by score.
        weakest: dict[str, float] = {}
        for w in removable.values():
            s, sec = score_of(w.ticker), sector_of(w)
            if s is not None and sec is not None:
                weakest[sec] = min(weakest.get(sec, s), s)
        entrants = [
            c for c in filtered
            if c["ticker"] not in member_tickers
            and (
                rank_of[c["ticker"]] <= ENTER_RANK
                or (c["sector_name"] in weakest
                    and c["composite_score"] >= weakest[c["sector_name"]] + ENTER_MARGIN)
            )
        ]
        entrants.sort(key=lambda c: (
            not (c["has_catalyst_14d"] or c["has_recent_rating_change"]),
            -c["composite_score"],
        ))
        pending = list(entrants)

        def fill_free_slots() -> None:
            for c in list(pending):
                if budget <= 0 or count >= MAX_WATCHLIST:
                    return
                if sec_count[c["sector_name"]] >= MAX_PER_SECTOR:
                    continue
                add(c)
                pending.remove(c)

        # 2. Additions into free slots.
        fill_free_slots()
        # 3. Rule exits.
        for w, reason in rule_exits:
            if budget <= 0:
                break
            if w.ticker not in removed:
                remove(w, reason)
        # 4. Additions into the freed slots.
        fill_free_slots()
        # 5. One-in-one-out swaps within the sector.
        for c in list(pending):
            if budget < 2:
                break
            targets = [
                w for w in removable.values()
                if w.ticker not in removed and sector_of(w) == c["sector_name"]
                and score_of(w.ticker) is not None
            ]
            if not targets:
                continue
            target = min(targets, key=low_first)
            if c["composite_score"] < score_of(target.ticker) + ENTER_MARGIN:
                continue
            remove(target, f"replaced by {c['ticker']} (score {c['composite_score']:.1f} vs "
                           f"{score_of(target.ticker):.1f})")
            add(c)
            pending.remove(c)

        existing = [
            {"ticker": w.ticker, "company_name": w.company_name, "sector_name": sector_of(w)}
            for w in members
            if w.ticker not in removed
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
        universe candidate NOT already on the active watchlist (never one of
        ROTATION_EXCLUDE), preferring a stock in the SAME sector as the removed
        ticker. Falls back to the globally highest candidate (flagged
        ``cross_sector``) when the sector is exhausted.

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
        open_position_tickers: set[str] | None = None,
    ) -> dict:
        """Evaluate whether *ticker* may be rotated out by hand.

        Hard-blocks rotation when ANY condition holds:
          1. an open position in the ticker (never rotated out)
          2. latest recommendation ``entry_strategy == PRE_POSITION``
          3. a leading positioning signal fired (Smart Money Positioning /
             Analyst Revision Cluster / Insider Buy Cluster, points > 0)
          4. an active earnings catalyst window (T-14..T-0)
          5. unusual options activity in the last completed session
          6. bullish high conviction (BUY/STRONG_BUY with conviction >= 40)

        Returns ``{ticker, blocked, reasons}``. ``unusual_tickers``,
        ``earnings_service`` and ``open_position_tickers`` may be supplied by a
        batch caller to avoid recomputing them per ticker.
        """
        reasons: list[str] = []

        if open_position_tickers is None:
            open_position_tickers = await get_open_position_tickers(self._session)
        if ticker in open_position_tickers:
            reasons.append("open position held — close it before rotating this ticker out")

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

        Used only when config.DROP_TOXIC is True.
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
        """Unusual call volume in the last completed session (shared helper)."""
        return await get_unusual_options_tickers(self._session)

    # ------------------------------------------------------------------
    # 4. Rotate watchlist (orchestrator)
    # ------------------------------------------------------------------

    async def rotation_ran_on(self, day: date) -> bool:
        """True when an automatic rotation already committed for ET date *day*."""
        since = datetime.now(tz=timezone.utc) - timedelta(days=2)
        for m in await self._rotation_markers(since):
            if (m.meta or {}).get("et_date") == day.isoformat():
                return True
        return False

    async def rotate_watchlist(self) -> dict[str, Any]:
        """Full rotation: score, filter, plan, persist, snapshot.

        Runs at most once per ET day: a later call (second scheduled run,
        the "Full" pipeline preset) logs and returns ``{"skipped": True}``.
        """
        started = datetime.now(tz=timezone.utc)
        et_day = et_today(started)
        if await self.rotation_ran_on(et_day):
            logger.info("Watchlist rotation already ran for %s — skipping this run", et_day)
            return {"date": str(et_day), "skipped": True, "reason": "already ran today"}

        logger.info("Starting watchlist rotation")

        scored = await self.score_universe()
        logger.info(
            "Scored %d tickers (%d failed the filters, %d without data, %d inputs refreshed)",
            len(scored), len(self.last_filtered_out), len(self.last_score_errors),
            len(self.last_refreshed),
        )

        filtered = await self.apply_filters(scored)
        logger.info("After filters: %d tickers remain", len(filtered))
        if not filtered:
            # Data outage (nothing scored): change nothing and leave no marker,
            # so a later run today can still rotate.
            await self._session.rollback()
            logger.warning("Watchlist rotation aborted: no ticker could be scored")
            return {"date": str(et_day), "skipped": True, "reason": "no ticker scored"}

        additions, removals, existing = await self.select_watchlist(
            filtered, score_errors=set(self.last_score_errors)
        )
        logger.info(
            "Rotation plan: +%d additions, -%d removals, %d existing",
            len(additions), len(removals), len(existing),
        )

        today = date.today()
        sector_map = await self._get_or_create_sector_map()
        members = {w.ticker: w for w in await self.get_active_watchlist()}

        # Persist additions (resurrect a same-day row instead of a duplicate).
        for entry in additions:
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
                dup_row.exit_reason = None
                dup_row.weak_days = 0
                dup_row.entry_reason = "; ".join(reasons)
            else:
                self._session.add(Watchlist(
                    ticker=entry["ticker"],
                    company_name=entry.get("company_name"),
                    sector_id=sector_map.get(entry["sector_name"]),
                    added_date=today,
                    is_active=True,
                    weak_days=0,
                    entry_reason="; ".join(reasons),
                ))

        # Persist removals
        for entry in removals:
            wl_row = members.get(entry["ticker"])
            if wl_row:
                wl_row.is_active = False
                wl_row.removed_date = today
                wl_row.exit_reason = entry.get("reason", "rotation")
        await self._session.flush()

        # Snapshot: never delete earlier rows of the day. Additions and
        # removals record their status; continuing members get EXISTING unless
        # an earlier change today already wrote their row.
        snap_result = await self._session.execute(
            select(WatchlistDailySnapshot.ticker).where(
                WatchlistDailySnapshot.snapshot_date == today,
            )
        )
        already = {t for (t,) in snap_result.all()}
        for entry in existing:
            if entry["ticker"] not in already:
                self._session.add(WatchlistDailySnapshot(
                    snapshot_date=today,
                    ticker=entry["ticker"],
                    status="EXISTING",
                    sector=entry.get("sector_name"),
                ))
        for entry in additions:
            await record_snapshot_change(
                self._session, entry["ticker"], "NEW_ENTRANT", entry.get("sector_name"), today,
            )
        for entry in removals:
            await record_snapshot_change(
                self._session, entry["ticker"], "REMOVED", entry.get("sector_name"), today,
            )

        finished = datetime.now(tz=timezone.utc)
        self._session.add(PipelineRunLog(
            phase=ROTATION_MARKER_PHASE,
            user_id=None,
            status="SUCCESS",
            started_at=started,
            finished_at=finished,
            duration_ms=int((finished - started).total_seconds() * 1000),
            meta={
                "et_date": et_day.isoformat(),
                "added": [e["ticker"] for e in additions],
                "removed": [e["ticker"] for e in removals],
                "refreshed": sorted(self.last_refreshed),
                "active_total": len(existing) + len(additions),
            },
        ))

        await self._session.commit()
        logger.info("Watchlist rotation committed successfully")

        return {
            "date": str(today),
            "skipped": False,
            "scored_total": len(scored),
            "filtered_total": len(filtered),
            "additions": len(additions),
            "removals": len(removals),
            "existing": len(existing),
            "active_total": len(existing) + len(additions),
            "added_tickers": [e["ticker"] for e in additions],
            "removed_tickers": [e["ticker"] for e in removals],
            "inputs_refreshed": len(self.last_refreshed),
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
