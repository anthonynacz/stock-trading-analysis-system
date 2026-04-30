"""Multi-bagger scanner — positional (3-12 month horizon) signal stacker.

Runs on a broader universe than the tactical watchlist (~100 growth-candidate
tickers across secular themes). Each run scores every active universe member
against six positional signals and writes one MultibaggerSnapshot row per
(run_date, ticker). The ranked output lets a user spot stocks with the DNA
of multi-baggers — revenue acceleration, margin expansion, long-horizon
momentum breakouts, analyst chase, recent structural events, and analyst
revision clusters.

This is intentionally separate from the tactical recommendation engine: the
horizons differ (12 months vs 2 weeks) and signal stacking for one doesn't
fit the other.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AnalystRating, MultibaggerSnapshot, MultibaggerUniverse
from utils.data_sources import DataSourceClient

logger = logging.getLogger(__name__)


# Tier thresholds — tuned so HOT requires a genuine signal cluster,
# not just one standout metric.
TIER_HOT_MIN_SIGNALS = 4
TIER_HOT_MIN_COMPOSITE = 60
TIER_WATCH_MIN_SIGNALS = 3
TIER_WATCH_MIN_COMPOSITE = 40
TIER_MONITOR_MIN_SIGNALS = 2


def _to_decimal(v: Any, q: str = "0.0001") -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v)).quantize(Decimal(q))
    except Exception:
        return None


def _pct_rank(value: float | None, distribution: list[float]) -> float | None:
    """Return percentile (0-100) of value within distribution. None-safe."""
    if value is None:
        return None
    cleaned = [x for x in distribution if x is not None]
    if not cleaned:
        return None
    below = sum(1 for x in cleaned if x < value)
    return 100.0 * below / len(cleaned)


class MultibaggerScanner:
    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data = data_client

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    async def run_and_persist(self, run_date: date | None = None) -> dict:
        """Score the universe, write snapshot rows, return run summary."""
        run_date = run_date or date.today()
        loop = asyncio.get_event_loop()

        # 1. Load active universe
        universe = (
            await self._session.execute(
                select(MultibaggerUniverse).where(MultibaggerUniverse.is_active.is_(True))
            )
        ).scalars().all()
        tickers = [(u.ticker, u.theme, u.company_name) for u in universe]
        if not tickers:
            return {"status": "empty_universe", "run_date": str(run_date)}

        # 2. First pass — fetch raw observations per ticker
        raw_rows: list[dict] = []
        for ticker, theme, cname in tickers:
            try:
                row = await self._gather_raw(ticker, theme, cname, loop)
                raw_rows.append(row)
            except Exception:
                logger.exception("scanner: gather failed for %s", ticker)

        # 3. Universe-wide distributions (for percentile ranks)
        ret12 = [r["return_12m"] for r in raw_rows]
        ret6 = [r["return_6m"] for r in raw_rows]

        # 4. Second pass — score each ticker
        scored: list[dict] = []
        for r in raw_rows:
            pct12 = _pct_rank(r["return_12m"], ret12) if r["return_12m"] is not None else None
            pct6 = _pct_rank(r["return_6m"], ret6) if r["return_6m"] is not None else None
            scored.append(self._score_ticker(r, pct12, pct6))

        # 5. Persist — delete existing rows for run_date, then insert fresh
        await self._session.execute(
            delete(MultibaggerSnapshot).where(MultibaggerSnapshot.run_date == run_date)
        )
        for s in scored:
            self._session.add(
                MultibaggerSnapshot(
                    run_date=run_date,
                    ticker=s["ticker"],
                    company_name=s.get("company_name"),
                    theme=s.get("theme"),
                    composite_score=_to_decimal(s["composite_score"], "0.01") or Decimal("0"),
                    tier=s["tier"],
                    signals_fired=s["signals_fired"],
                    price=_to_decimal(s.get("price"), "0.0001"),
                    market_cap=_to_decimal(s.get("market_cap"), "0.01"),
                    stock_age_months=_to_decimal(s.get("stock_age_months"), "0.1"),
                    return_12m=_to_decimal(s.get("return_12m"), "0.0001"),
                    return_6m=_to_decimal(s.get("return_6m"), "0.0001"),
                    momentum_percentile=_to_decimal(s.get("momentum_percentile"), "0.01"),
                    rev_growth_latest=_to_decimal(s.get("rev_growth_latest"), "0.0001"),
                    rev_growth_prior=_to_decimal(s.get("rev_growth_prior"), "0.0001"),
                    rev_accel_pp=_to_decimal(s.get("rev_accel_pp"), "0.0001"),
                    gross_margin_latest=_to_decimal(s.get("gross_margin_latest"), "0.0001"),
                    gross_margin_prior=_to_decimal(s.get("gross_margin_prior"), "0.0001"),
                    margin_delta_pp=_to_decimal(s.get("margin_delta_pp"), "0.0001"),
                    avg_pt=_to_decimal(s.get("avg_pt"), "0.0001"),
                    pt_chase_ratio=_to_decimal(s.get("pt_chase_ratio"), "0.0001"),
                    revisions_90d=s.get("revisions_90d"),
                    signals=s.get("signals"),
                    rationale=s.get("rationale"),
                )
            )
        await self._session.commit()

        hot = sum(1 for s in scored if s["tier"] == "HOT")
        watch = sum(1 for s in scored if s["tier"] == "WATCH")
        return {
            "status": "ok",
            "run_date": str(run_date),
            "scored": len(scored),
            "hot": hot,
            "watch": watch,
        }

    # ------------------------------------------------------------------
    # Raw-data gathering (one ticker)
    # ------------------------------------------------------------------

    async def _gather_raw(
        self, ticker: str, theme: str | None, cname: str | None, loop
    ) -> dict:
        """Fetch everything we need for one ticker without yet scoring."""
        stock = await loop.run_in_executor(None, self._data.get_stock_data, ticker)
        returns = await loop.run_in_executor(None, self._data.get_long_horizon_returns, ticker)
        age_m = await loop.run_in_executor(None, self._data.get_stock_age_months, ticker)
        income = await loop.run_in_executor(
            None, self._data.get_income_statement_quarterly, ticker, 5
        )
        revisions_90d = await self._count_analyst_revisions(ticker, days=90)

        # Revenue + margin trajectory
        rev_latest = rev_prior = None
        gm_latest = gm_prior = None
        if len(income) >= 5:
            # income is most-recent-first
            rev_latest_q = _safe(income[0].get("revenue"))
            rev_year_ago = _safe(income[4].get("revenue"))
            rev_prior_q = _safe(income[1].get("revenue"))
            rev_year_ago_prior = _safe(income[4].get("revenue")) if len(income) == 5 else (
                _safe(income[5].get("revenue")) if len(income) > 5 else None
            )
            gp_latest_q = _safe(income[0].get("grossProfit"))
            gp_year_ago = _safe(income[4].get("grossProfit"))
            if rev_latest_q and rev_year_ago and rev_year_ago > 0:
                rev_latest = (rev_latest_q - rev_year_ago) / rev_year_ago
            if rev_prior_q and rev_year_ago_prior and rev_year_ago_prior > 0:
                rev_prior = (rev_prior_q - rev_year_ago_prior) / rev_year_ago_prior
            if rev_latest_q and gp_latest_q and rev_latest_q > 0:
                gm_latest = gp_latest_q / rev_latest_q
            if rev_year_ago and gp_year_ago and rev_year_ago > 0:
                gm_prior = gp_year_ago / rev_year_ago

        return {
            "ticker": ticker,
            "theme": theme,
            "company_name": cname or stock.get("company_name"),
            "price": stock.get("price"),
            "market_cap": stock.get("market_cap"),
            "avg_pt": stock.get("avg_analyst_target"),
            "stock_age_months": age_m,
            "return_12m": returns.get("return_12m"),
            "return_6m": returns.get("return_6m"),
            "rev_growth_latest": rev_latest,
            "rev_growth_prior": rev_prior,
            "gross_margin_latest": gm_latest,
            "gross_margin_prior": gm_prior,
            "revisions_90d": revisions_90d,
        }

    async def _count_analyst_revisions(self, ticker: str, days: int = 90) -> int:
        """Count analyst ratings (upgrades / PT raises) for ticker in last N days."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        result = await self._session.execute(
            select(AnalystRating).where(
                AnalystRating.ticker == ticker,
                AnalystRating.published_at >= cutoff,
            )
        )
        rows = result.scalars().all()
        count = 0
        for r in rows:
            # PT raise
            if r.previous_pt and r.new_pt and r.new_pt > r.previous_pt:
                count += 1
                continue
            # Tier upgrade
            if r.rating_type == "TIER_CHANGE" and r.new_rating and r.previous_rating:
                bullish = {"Buy", "Outperform", "Overweight", "Strong Buy"}
                if r.new_rating in bullish and r.previous_rating not in bullish:
                    count += 1
                    continue
            # New initiation at bullish
            if r.rating_type == "INITIATION" and r.new_rating in {"Buy", "Outperform", "Overweight", "Strong Buy"}:
                count += 1
        return count

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_ticker(
        self, raw: dict, pct12: float | None, pct6: float | None
    ) -> dict:
        """Apply the 6 positional signals and produce a scored row."""
        signals: list[dict] = []
        score = 0.0

        # 1. Revenue acceleration (YoY latest Q minus YoY prior Q, in pp)
        rl = raw.get("rev_growth_latest")
        rp = raw.get("rev_growth_prior")
        rev_accel_pp = None
        if rl is not None and rp is not None:
            rev_accel_pp = (rl - rp) * 100
            if rev_accel_pp >= 20:
                pts = 25
                signals.append({
                    "signal": "Revenue Growth Accelerating",
                    "points": pts,
                    "detail": f"YoY rev growth {rl*100:.0f}% vs prior Q {rp*100:.0f}% — +{rev_accel_pp:.0f}pp acceleration",
                })
                score += pts
            elif rev_accel_pp >= 10:
                pts = 15
                signals.append({
                    "signal": "Revenue Growth Accelerating",
                    "points": pts,
                    "detail": f"YoY rev growth {rl*100:.0f}% vs prior Q {rp*100:.0f}% — +{rev_accel_pp:.0f}pp",
                })
                score += pts
            elif rl >= 0.50:
                pts = 10
                signals.append({
                    "signal": "High Revenue Growth",
                    "points": pts,
                    "detail": f"YoY rev growth {rl*100:.0f}% (not accelerating but elevated)",
                })
                score += pts

        # 2. Margin expansion (gross margin YoY delta in pp)
        gml = raw.get("gross_margin_latest")
        gmp = raw.get("gross_margin_prior")
        margin_delta_pp = None
        if gml is not None and gmp is not None:
            margin_delta_pp = (gml - gmp) * 100
            if margin_delta_pp >= 5:
                pts = 20
                signals.append({
                    "signal": "Margin Expansion",
                    "points": pts,
                    "detail": f"Gross margin {gml*100:.1f}% vs {gmp*100:.1f}% YoY — +{margin_delta_pp:.1f}pp",
                })
                score += pts
            elif margin_delta_pp >= 2:
                pts = 10
                signals.append({
                    "signal": "Margin Expansion",
                    "points": pts,
                    "detail": f"Gross margin {gml*100:.1f}% vs {gmp*100:.1f}% YoY — +{margin_delta_pp:.1f}pp",
                })
                score += pts

        # 3. Long-horizon momentum (percentile rank of 12m return vs universe)
        if pct12 is not None:
            if pct12 >= 90:
                pts = 25
                signals.append({
                    "signal": "Top-Decile 12m Momentum",
                    "points": pts,
                    "detail": f"12m return in top {100 - pct12:.0f}% of universe (+{(raw.get('return_12m') or 0)*100:.0f}%)",
                })
                score += pts
            elif pct12 >= 75:
                pts = 15
                signals.append({
                    "signal": "Top-Quartile 12m Momentum",
                    "points": pts,
                    "detail": f"12m return in top {100 - pct12:.0f}% of universe (+{(raw.get('return_12m') or 0)*100:.0f}%)",
                })
                score += pts

        # 4. PT chase (stock price above consensus — analysts updating, not leading)
        price = raw.get("price")
        avg_pt = raw.get("avg_pt")
        pt_chase_ratio = None
        if price and avg_pt and avg_pt > 0:
            pt_chase_ratio = price / avg_pt
            if pt_chase_ratio >= 1.15:
                pts = 15
                signals.append({
                    "signal": "Analysts Chasing",
                    "points": pts,
                    "detail": f"Price ${price:.2f} is {(pt_chase_ratio - 1)*100:.0f}% above avg PT ${avg_pt:.2f}",
                })
                score += pts
            elif pt_chase_ratio >= 1.00:
                pts = 8
                signals.append({
                    "signal": "At/Above Consensus PT",
                    "points": pts,
                    "detail": f"Price ${price:.2f} ≥ avg PT ${avg_pt:.2f} ({(pt_chase_ratio - 1)*100:+.0f}%)",
                })
                score += pts

        # 5. Recent structural event (IPO/spinoff <18mo)
        age = raw.get("stock_age_months")
        if age is not None:
            if age <= 12:
                pts = 15
                signals.append({
                    "signal": "Recent IPO/Spinoff",
                    "points": pts,
                    "detail": f"Listed {age:.0f} months ago — structural re-pricing in progress",
                })
                score += pts
            elif age <= 24:
                pts = 8
                signals.append({
                    "signal": "Recent IPO/Spinoff",
                    "points": pts,
                    "detail": f"Listed {age:.0f} months ago — still finding its valuation",
                })
                score += pts

        # 6. Analyst revision cluster 90d
        rev90 = raw.get("revisions_90d") or 0
        if rev90 >= 5:
            pts = 20
            signals.append({
                "signal": "Revision Cascade",
                "points": pts,
                "detail": f"{rev90} bullish analyst actions in trailing 90d — estimates chasing price",
            })
            score += pts
        elif rev90 >= 3:
            pts = 10
            signals.append({
                "signal": "Revision Cluster",
                "points": pts,
                "detail": f"{rev90} bullish analyst actions in trailing 90d",
            })
            score += pts

        # Clamp composite
        composite = max(0.0, min(120.0, score))

        # Tier classification
        fired = len(signals)
        if fired >= TIER_HOT_MIN_SIGNALS and composite >= TIER_HOT_MIN_COMPOSITE:
            tier = "HOT"
        elif fired >= TIER_WATCH_MIN_SIGNALS or composite >= TIER_WATCH_MIN_COMPOSITE:
            tier = "WATCH"
        elif fired >= TIER_MONITOR_MIN_SIGNALS:
            tier = "MONITOR"
        else:
            tier = "IGNORE"

        # Rationale — top 2 signals + tier
        sorted_sigs = sorted(signals, key=lambda s: s["points"], reverse=True)
        top_txt = "; ".join(s["signal"] for s in sorted_sigs[:2]) or "No positional signals"
        rationale = f"{raw['ticker']} — {tier} ({fired} signals, composite {composite:.0f}). {top_txt}."

        return {
            "ticker": raw["ticker"],
            "company_name": raw.get("company_name"),
            "theme": raw.get("theme"),
            "composite_score": composite,
            "tier": tier,
            "signals_fired": fired,
            "signals": signals,
            "rationale": rationale,
            "price": raw.get("price"),
            "market_cap": raw.get("market_cap"),
            "stock_age_months": age,
            "return_12m": raw.get("return_12m"),
            "return_6m": raw.get("return_6m"),
            "momentum_percentile": pct12,
            "rev_growth_latest": rl,
            "rev_growth_prior": rp,
            "rev_accel_pp": (rev_accel_pp / 100) if rev_accel_pp is not None else None,
            "gross_margin_latest": gml,
            "gross_margin_prior": gmp,
            "margin_delta_pp": (margin_delta_pp / 100) if margin_delta_pp is not None else None,
            "avg_pt": avg_pt,
            "pt_chase_ratio": pt_chase_ratio,
            "revisions_90d": rev90,
        }


def _safe(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None
