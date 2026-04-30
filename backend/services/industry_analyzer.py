"""Industry-level (sector-level) signal stacking.

Treats each industry as the unit — not a ticker. Signals are native to an
industry: breadth, cap-weighted conviction, sector-ETF technicals,
aggregated news sentiment, geopolitical impact, and catalyst density.
Output: one row per (rec_date, industry) in ``industry_recommendations``,
with action / conviction / signals / rationale / representative tickers.

Runs after the ticker-level ``recommendations`` phase in the scheduler so
today's individual recommendations are available for aggregation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import SECTORS, SECTOR_ETFS
from db.models import (
    EarningsCalendar,
    IndustryRecommendation,
    MarketNews,
    NewsTickerRelevance,
    Recommendation,
    Watchlist,
)
from utils.data_sources import DataSourceClient
from utils.geopolitical import detect_and_score

logger = logging.getLogger(__name__)


# Classification bands — same as ticker recommendations so the label
# semantics are consistent across ticker and industry views.
def _classify(conviction: float) -> str:
    if conviction >= 60:
        return "STRONG_BUY"
    if conviction >= 30:
        return "BUY"
    if conviction >= -15:
        return "HOLD"
    if conviction >= -30:
        return "SELL"
    return "STRONG_SELL"


def _to_decimal(v: Any, q: str = "0.01") -> Decimal | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return Decimal(str(f)).quantize(Decimal(q))
    except Exception:
        return None


def _rsi_14(closes: list[float]) -> float | None:
    """Wilder's RSI over last 14 periods."""
    if len(closes) < 15:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, 15):
        diff = closes[-15 + i] - closes[-16 + i]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_g = gains / 14
    avg_l = losses / 14
    for i in range(15, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0)
        loss = max(-diff, 0)
        avg_g = (avg_g * 13 + gain) / 14
        avg_l = (avg_l * 13 + loss) / 14
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


class IndustryAnalyzer:
    def __init__(self, session: AsyncSession, data_client: DataSourceClient) -> None:
        self._session = session
        self._data = data_client

    # ------------------------------------------------------------------

    async def run_and_persist(self, today: date | None = None) -> dict:
        """Score every sector, persist one row per industry, return summary."""
        today = today or date.today()
        loop = asyncio.get_event_loop()

        # Load today's news (48h) once — same list is filtered per sector
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=48)
        news_q = await self._session.execute(
            select(MarketNews).where(MarketNews.published_at >= cutoff)
        )
        all_news = list(news_q.scalars().all())

        # Delete any existing rows for today so re-runs are idempotent
        await self._session.execute(
            delete(IndustryRecommendation).where(IndustryRecommendation.rec_date == today)
        )

        summary_rows = []
        for sector_name in SECTORS.keys():
            try:
                row = await self._analyze_sector(sector_name, today, all_news, loop)
                summary_rows.append(row)
                self._session.add(IndustryRecommendation(**row))
            except Exception:
                logger.exception("industry_analyzer failed for %s", sector_name)

        await self._session.commit()

        return {
            "status": "ok",
            "rec_date": str(today),
            "industries": len(summary_rows),
        }

    # ------------------------------------------------------------------

    async def _analyze_sector(
        self, sector: str, today: date, all_news: list[MarketNews], loop
    ) -> dict:
        """Compute signals for one sector and return the DB row dict."""
        cfg = SECTORS[sector]
        universe_tickers: list[str] = cfg["universe"]

        # 1. Watchlist members in this sector (active recs today)
        wl_result = await self._session.execute(
            select(Watchlist).where(
                Watchlist.is_active.is_(True),
                Watchlist.ticker.in_(universe_tickers),
            )
        )
        wl_tickers = [w.ticker for w in wl_result.scalars().all()]

        # 2. Today's recommendations for those tickers
        if wl_tickers:
            rec_result = await self._session.execute(
                select(Recommendation).where(
                    Recommendation.recommendation_date == today,
                    Recommendation.ticker.in_(wl_tickers),
                )
            )
            recs = list(rec_result.scalars().all())
        else:
            recs = []

        # 3. Stock data for cap + sma_50 — fetch for whichever tickers have recs
        stock_map: dict[str, dict] = {}
        for ticker in [r.ticker for r in recs]:
            try:
                stock_map[ticker] = await loop.run_in_executor(
                    None, self._data.get_stock_data, ticker
                )
            except Exception:
                stock_map[ticker] = {"ticker": ticker}

        # 4. ETF technicals
        etf_symbol = SECTOR_ETFS.get(sector)
        etf_rsi = None
        etf_mom_20d = None
        if etf_symbol:
            try:
                ret = await loop.run_in_executor(
                    None, self._data.get_long_horizon_returns, etf_symbol
                )
                # 20d momentum ~= cube-root-ish of 3m return, easier: use 3m return as proxy
                # But we want pure 20d. Use yfinance history to compute precisely.
                import yfinance as yf
                hist = await loop.run_in_executor(
                    None, lambda: yf.Ticker(etf_symbol).history(period="3mo", auto_adjust=True)
                )
                if hist is not None and len(hist) >= 21:
                    closes = hist["Close"].dropna().tolist()
                    etf_rsi = _rsi_14(closes)
                    if len(closes) >= 21:
                        etf_mom_20d = (closes[-1] - closes[-21]) / closes[-21]
            except Exception:
                logger.debug("etf fetch failed for %s (%s)", sector, etf_symbol, exc_info=True)

        # 5. Sector news — articles whose related_tickers intersect this sector universe
        sector_tickers_set = set(universe_tickers)
        sector_news: list[MarketNews] = []
        for n in all_news:
            rel_result = await self._session.execute(
                select(NewsTickerRelevance).where(NewsTickerRelevance.news_id == n.id)
            )
            rels = list(rel_result.scalars().all())
            if any(r.ticker in sector_tickers_set for r in rels):
                sector_news.append(n)
            elif n.category == "SECTOR" and sector.split("/")[0].lower() in (n.summary or "").lower():
                sector_news.append(n)

        # News sentiment aggregate (FinBERT scores already on MarketNews rows)
        news_sent = None
        if sector_news:
            scores = [float(n.sentiment_score) for n in sector_news if n.sentiment_score is not None]
            if scores:
                news_sent = sum(scores) / len(scores)

        # 6. Geopolitical impact — reuse existing utility
        geo_signals = []
        if all_news:
            article_dicts = [
                {
                    "headline": n.headline,
                    "summary": n.summary or "",
                    "category": n.category,
                    "sentiment_score": float(n.sentiment_score) if n.sentiment_score is not None else 0.0,
                }
                for n in all_news
            ]
            geo_signals = detect_and_score(article_dicts, sector)

        # 7. Active catalyst density
        active_cats = 0
        if universe_tickers:
            now = datetime.now(tz=timezone.utc)
            ec_result = await self._session.execute(
                select(EarningsCalendar).where(
                    EarningsCalendar.ticker.in_(universe_tickers),
                    EarningsCalendar.earnings_date >= (today - timedelta(days=0)),
                    EarningsCalendar.earnings_date <= (today + timedelta(days=14)),
                )
            )
            active_cats = len(list(ec_result.scalars().all()))
            _ = now

        # ── Score signals ────────────────────────────────────────────────
        signals: list[dict] = []
        score = 0.0

        member_count = len(recs)
        bullish_count = sum(1 for r in recs if float(r.conviction_score) >= 30)
        bearish_count = sum(1 for r in recs if float(r.conviction_score) <= -16)
        breadth_pos_pct = (bullish_count / member_count * 100) if member_count else None

        # Signal A: Breadth (% bullish)
        if member_count >= 3 and breadth_pos_pct is not None:
            if breadth_pos_pct >= 60:
                pts = 15
                signals.append({"signal": "Broad Bullish Breadth", "points": pts,
                                "detail": f"{bullish_count}/{member_count} sector members rated BUY+ ({breadth_pos_pct:.0f}%)"})
                score += pts
            elif breadth_pos_pct >= 40:
                pts = 8
                signals.append({"signal": "Positive Breadth", "points": pts,
                                "detail": f"{bullish_count}/{member_count} sector members rated BUY+ ({breadth_pos_pct:.0f}%)"})
                score += pts
            elif breadth_pos_pct <= 20 and bearish_count >= 2:
                pts = -15
                signals.append({"signal": "Bearish Breadth", "points": pts,
                                "detail": f"{bearish_count}/{member_count} members rated SELL- — broad weakness"})
                score += pts
            elif breadth_pos_pct <= 30 and bearish_count >= 2:
                pts = -8
                signals.append({"signal": "Negative Breadth", "points": pts,
                                "detail": f"{bearish_count}/{member_count} members rated SELL-"})
                score += pts

        # Signal B: % above 50d SMA
        above_50d = 0
        have_sma = 0
        for t in [r.ticker for r in recs]:
            s = stock_map.get(t) or {}
            price = s.get("price")
            sma = s.get("sma_50")
            if price and sma:
                have_sma += 1
                if price >= sma:
                    above_50d += 1
        breadth_50d_pct = (above_50d / have_sma * 100) if have_sma else None
        if have_sma >= 3 and breadth_50d_pct is not None:
            if breadth_50d_pct >= 75:
                pts = 10
                signals.append({"signal": "Trend: Above 50d SMA", "points": pts,
                                "detail": f"{above_50d}/{have_sma} members above 50d SMA ({breadth_50d_pct:.0f}%)"})
                score += pts
            elif breadth_50d_pct <= 25:
                pts = -10
                signals.append({"signal": "Trend: Below 50d SMA", "points": pts,
                                "detail": f"only {above_50d}/{have_sma} members above 50d SMA ({breadth_50d_pct:.0f}%)"})
                score += pts

        # Signal C: Cap-weighted conviction
        weighted_conv = None
        if recs:
            total_cap = 0.0
            weighted_sum = 0.0
            for r in recs:
                cap = (stock_map.get(r.ticker) or {}).get("market_cap") or 0
                if cap and cap > 0:
                    total_cap += cap
                    weighted_sum += float(r.conviction_score) * cap
            if total_cap > 0:
                weighted_conv = weighted_sum / total_cap
                if weighted_conv >= 40:
                    pts = 20
                    signals.append({"signal": "Cap-Weighted Bullish", "points": pts,
                                    "detail": f"Market-cap-weighted conviction {weighted_conv:+.0f} — large caps leading up"})
                    score += pts
                elif weighted_conv >= 20:
                    pts = 10
                    signals.append({"signal": "Cap-Weighted Bullish", "points": pts,
                                    "detail": f"Market-cap-weighted conviction {weighted_conv:+.0f}"})
                    score += pts
                elif weighted_conv <= -40:
                    pts = -20
                    signals.append({"signal": "Cap-Weighted Bearish", "points": pts,
                                    "detail": f"Market-cap-weighted conviction {weighted_conv:+.0f} — large caps leading down"})
                    score += pts
                elif weighted_conv <= -20:
                    pts = -10
                    signals.append({"signal": "Cap-Weighted Bearish", "points": pts,
                                    "detail": f"Market-cap-weighted conviction {weighted_conv:+.0f}"})
                    score += pts

        # Signal D: ETF RSI / momentum
        if etf_rsi is not None:
            if etf_rsi < 30:
                pts = 10
                signals.append({"signal": f"{etf_symbol} Oversold", "points": pts,
                                "detail": f"{etf_symbol} RSI(14) {etf_rsi:.0f} — sector ETF oversold, potential bounce"})
                score += pts
            elif etf_rsi > 70:
                pts = -10
                signals.append({"signal": f"{etf_symbol} Overbought", "points": pts,
                                "detail": f"{etf_symbol} RSI(14) {etf_rsi:.0f} — sector ETF overbought, pullback risk"})
                score += pts
        if etf_mom_20d is not None:
            if etf_mom_20d >= 0.05:
                pts = 10
                signals.append({"signal": f"{etf_symbol} Strong 20d Momentum", "points": pts,
                                "detail": f"{etf_symbol} up {etf_mom_20d*100:+.1f}% over 20 days"})
                score += pts
            elif etf_mom_20d <= -0.05:
                pts = -10
                signals.append({"signal": f"{etf_symbol} Weak 20d Momentum", "points": pts,
                                "detail": f"{etf_symbol} down {etf_mom_20d*100:+.1f}% over 20 days"})
                score += pts

        # Signal E: News sentiment
        if news_sent is not None and len(sector_news) >= 2:
            mag = min(abs(news_sent), 1.0)
            base = 15 if len(sector_news) >= 6 else 10
            pts = int(round(base * mag)) * (1 if news_sent >= 0 else -1)
            if abs(pts) >= 4:
                sig_name = "Positive Sector News" if pts > 0 else "Negative Sector News"
                signals.append({"signal": sig_name, "points": pts,
                                "detail": f"avg FinBERT sentiment {news_sent:+.2f} across {len(sector_news)} articles"})
                score += pts

        # Signal F: Geopolitical
        geo_total = 0
        for gs in geo_signals:
            signals.append({"signal": gs.signal_name, "points": gs.points, "detail": gs.detail})
            geo_total += gs.points
        # Cap geopolitical contribution at ±25 to prevent correlated events stacking
        if abs(geo_total) > 25:
            excess = geo_total - (25 if geo_total > 0 else -25)
            score -= excess
            signals.append({"signal": "Geopolitical Cap", "points": -excess,
                            "detail": f"Clamped net geopolitical contribution to ±25 (raw {geo_total:+d})"})
            score += geo_total - excess
        else:
            score += geo_total

        # Signal G: Catalyst density
        if active_cats >= 4:
            pts = 5
            signals.append({"signal": "Heavy Catalyst Week", "points": pts,
                            "detail": f"{active_cats} sector members reporting earnings within 14 days"})
            score += pts

        # Clamp and classify
        conviction = max(-100.0, min(100.0, score))
        action = _classify(conviction)

        # Representative tickers: top 3 by abs(conviction) (most meaningful movers)
        reps = sorted(recs, key=lambda r: abs(float(r.conviction_score)), reverse=True)[:3]
        rep_tickers = [
            {
                "ticker": r.ticker,
                "action": r.action,
                "conviction": float(r.conviction_score),
            }
            for r in reps
        ]

        # Rationale
        top_sig = sorted(signals, key=lambda s: abs(s["points"]), reverse=True)[:2]
        rationale = (
            f"{sector} — {action} (conviction {conviction:+.0f}). "
            + ("; ".join(s["signal"] for s in top_sig) if top_sig else "No strong signals")
            + f". {member_count} watchlist members analyzed."
        )

        return {
            "rec_date": today,
            "industry": sector,
            "action": action,
            "conviction_score": _to_decimal(conviction, "0.01") or Decimal("0"),
            "signal_count": len(signals),
            "member_count": member_count,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "breadth_positive_pct": _to_decimal(breadth_pos_pct, "0.01"),
            "breadth_above_50d_pct": _to_decimal(breadth_50d_pct, "0.01"),
            "cap_weighted_conviction": _to_decimal(weighted_conv, "0.01"),
            "etf_symbol": etf_symbol,
            "etf_rsi_14": _to_decimal(etf_rsi, "0.01"),
            "etf_momentum_20d": _to_decimal(etf_mom_20d, "0.0001"),
            "avg_news_sentiment": _to_decimal(news_sent, "0.0001"),
            "news_article_count": len(sector_news),
            "geopolitical_points": _to_decimal(geo_total, "0.01"),
            "active_catalyst_count": active_cats,
            "representative_tickers": rep_tickers,
            "signals": signals,
            "rationale": rationale,
        }
