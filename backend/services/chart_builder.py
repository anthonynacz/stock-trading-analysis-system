"""Dynamic chart builder — dataset-first BI for EdgeFlow data.

Three curated datasets cover the most-asked-for analytical views without
falling into the universal-pivot trap (where every column is selectable
but most combinations are nonsensical):

1. ``ticker_time_series`` — one ticker, multiple metrics, X = date
2. ``signal_breakdown`` — recommendation signals grouped by signal name
3. ``industry_comparison`` — industry-level data over time or snapshot

Each dataset has its own handler that constructs a parametrized SQL query
(no string concat, no SQL injection surface) and returns a standardized
response shape ready for Recharts:

    {
      "dataset": "...",
      "x_label": "Date",
      "y_label": "Value",
      "chart_type": "line",
      "series": [
          {"name": "Conviction", "data": [{"x": "2026-04-01", "y": 35}, ...]},
          ...
      ],
      "meta": {...}  # dataset-specific extras
    }
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    IndustryRecommendation,
    MarketNews,
    NewsTickerRelevance,
    OptionsSnapshot,
    Recommendation,
)

logger = logging.getLogger(__name__)


# ── Dataset registry ────────────────────────────────────────────────────────

# Metric definitions for ticker_time_series. Each maps to a SQL source +
# the column / aggregation. Exposed to the frontend via /api/charts/datasets.
TICKER_METRICS: dict[str, dict[str, str]] = {
    "price": {"label": "Price", "source": "recommendation", "column": "current_price"},
    "conviction_score": {"label": "Conviction", "source": "recommendation", "column": "conviction_score"},
    "signal_count": {"label": "Signal Count", "source": "recommendation", "column": "signal_count"},
    "target_price": {"label": "Target Price", "source": "recommendation", "column": "target_price"},
    "stop_loss_price": {"label": "Stop Loss", "source": "recommendation", "column": "stop_loss_price"},
    "iv_rank": {"label": "IV Rank", "source": "options", "column": "iv_rank"},
    "iv_percentile": {"label": "IV Percentile", "source": "options", "column": "iv_percentile"},
    "put_call_ratio": {"label": "Put/Call Ratio", "source": "options", "column": "put_call_ratio"},
    "atm_iv": {"label": "ATM IV", "source": "options", "column": "atm_iv"},
    "total_call_volume": {"label": "Call Volume", "source": "options", "column": "total_call_volume"},
    "total_put_volume": {"label": "Put Volume", "source": "options", "column": "total_put_volume"},
    "avg_news_sentiment": {"label": "News Sentiment (avg)", "source": "news", "column": "sentiment_score"},
    "news_article_count": {"label": "News Article Count", "source": "news", "column": "_count"},
}

INDUSTRY_METRICS: dict[str, dict[str, str]] = {
    "conviction_score": {"label": "Conviction", "column": "conviction_score"},
    "breadth_positive_pct": {"label": "% Bullish Members", "column": "breadth_positive_pct"},
    "breadth_above_50d_pct": {"label": "% Above 50d SMA", "column": "breadth_above_50d_pct"},
    "cap_weighted_conviction": {"label": "Cap-Weighted Conviction", "column": "cap_weighted_conviction"},
    "etf_rsi_14": {"label": "ETF RSI(14)", "column": "etf_rsi_14"},
    "etf_momentum_20d": {"label": "ETF 20d Momentum", "column": "etf_momentum_20d"},
    "avg_news_sentiment": {"label": "News Sentiment", "column": "avg_news_sentiment"},
    "geopolitical_points": {"label": "Geopolitical Pts", "column": "geopolitical_points"},
    "active_catalyst_count": {"label": "Catalyst Count", "column": "active_catalyst_count"},
    "signal_count": {"label": "Signal Count", "column": "signal_count"},
}


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def _smooth(points: list[dict], window: int) -> list[dict]:
    """Trailing simple-moving-average smoother. Keeps first window-1 raw."""
    if window <= 1 or len(points) < 2:
        return points
    out: list[dict] = []
    buf: list[float] = []
    for p in points:
        v = p.get("y")
        if v is not None:
            buf.append(float(v))
        else:
            buf.clear()
        if len(buf) > window:
            buf.pop(0)
        smoothed = sum(buf) / len(buf) if buf else None
        out.append({**p, "y": smoothed})
    return out


# ── Service ─────────────────────────────────────────────────────────────────

class ChartBuilder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query(self, dataset: str, spec: dict) -> dict:
        if dataset == "ticker_time_series":
            return await self._ticker_time_series(spec)
        if dataset == "signal_breakdown":
            return await self._signal_breakdown(spec)
        if dataset == "industry_comparison":
            return await self._industry_comparison(spec)
        raise ValueError(f"Unknown dataset: {dataset}")

    # ── Ticker Time Series ──────────────────────────────────────────────

    async def _ticker_time_series(self, spec: dict) -> dict:
        """One series per requested metric, X = date.

        Spec:
            ticker (str, required)
            metrics (list[str], required) — keys from TICKER_METRICS
            from_date (str/date, optional, default 30d ago)
            to_date (str/date, optional, default today)
            smoothing_window (int, optional, default 1) — SMA period
        """
        ticker = (spec.get("ticker") or "").upper().strip()
        if not ticker:
            raise ValueError("ticker required")
        metrics: list[str] = spec.get("metrics") or ["conviction_score"]
        from_date = _parse_date(spec.get("from_date"), default=date.today() - timedelta(days=30))
        to_date = _parse_date(spec.get("to_date"), default=date.today())
        smoothing = int(spec.get("smoothing_window") or 1)

        # Fetch raw data per source once, then assemble per metric
        rec_rows: list[dict] = []
        opt_rows: list[dict] = []
        news_rows: list[dict] = []

        if any(TICKER_METRICS[m]["source"] == "recommendation" for m in metrics if m in TICKER_METRICS):
            rec_q = await self._session.execute(
                select(Recommendation).where(
                    Recommendation.ticker == ticker,
                    Recommendation.recommendation_date >= from_date,
                    Recommendation.recommendation_date <= to_date,
                ).order_by(Recommendation.recommendation_date.asc())
            )
            for r in rec_q.scalars():
                rec_rows.append({
                    "date": r.recommendation_date.isoformat(),
                    "current_price": _to_float(r.current_price),
                    "conviction_score": _to_float(r.conviction_score),
                    "signal_count": r.signal_count,
                    "target_price": _to_float(r.target_price),
                    "stop_loss_price": _to_float(r.stop_loss_price),
                })

        if any(TICKER_METRICS[m]["source"] == "options" for m in metrics if m in TICKER_METRICS):
            opt_q = await self._session.execute(
                select(OptionsSnapshot).where(
                    OptionsSnapshot.ticker == ticker,
                    OptionsSnapshot.snapshot_time >= datetime.combine(from_date, datetime.min.time(), tzinfo=timezone.utc),
                    OptionsSnapshot.snapshot_time <= datetime.combine(to_date, datetime.max.time(), tzinfo=timezone.utc),
                ).order_by(OptionsSnapshot.snapshot_time.asc())
            )
            # Latest snapshot per day
            latest_by_day: dict[str, dict] = {}
            for s in opt_q.scalars():
                day = s.snapshot_time.date().isoformat()
                latest_by_day[day] = {
                    "date": day,
                    "iv_rank": _to_float(s.iv_rank),
                    "iv_percentile": _to_float(s.iv_percentile),
                    "put_call_ratio": _to_float(s.put_call_ratio),
                    "atm_iv": _to_float(s.atm_iv),
                    "total_call_volume": s.total_call_volume,
                    "total_put_volume": s.total_put_volume,
                }
            opt_rows = list(latest_by_day.values())

        if any(TICKER_METRICS[m]["source"] == "news" for m in metrics if m in TICKER_METRICS):
            # News tagged to this ticker via NewsTickerRelevance, grouped by date
            news_q = await self._session.execute(
                select(MarketNews, NewsTickerRelevance.ticker)
                .join(NewsTickerRelevance, NewsTickerRelevance.news_id == MarketNews.id)
                .where(
                    NewsTickerRelevance.ticker == ticker,
                    MarketNews.published_at >= datetime.combine(from_date, datetime.min.time(), tzinfo=timezone.utc),
                    MarketNews.published_at <= datetime.combine(to_date, datetime.max.time(), tzinfo=timezone.utc),
                )
            )
            agg: dict[str, list[float]] = defaultdict(list)
            counts: dict[str, int] = defaultdict(int)
            for row in news_q.all():
                article = row[0]
                day = article.published_at.date().isoformat()
                if article.sentiment_score is not None:
                    agg[day].append(float(article.sentiment_score))
                counts[day] += 1
            for day in sorted(set(agg.keys()) | set(counts.keys())):
                news_rows.append({
                    "date": day,
                    "sentiment_score": (sum(agg[day]) / len(agg[day])) if agg.get(day) else None,
                    "_count": counts[day],
                })

        # Build a series per metric
        series: list[dict] = []
        for m in metrics:
            if m not in TICKER_METRICS:
                continue
            cfg = TICKER_METRICS[m]
            src = cfg["source"]
            col = cfg["column"]
            rows = {"recommendation": rec_rows, "options": opt_rows, "news": news_rows}[src]
            data = [{"x": r["date"], "y": r.get(col)} for r in rows]
            if smoothing > 1:
                data = _smooth(data, smoothing)
            series.append({"name": cfg["label"], "data": data, "metric_key": m})

        return {
            "dataset": "ticker_time_series",
            "x_label": "Date",
            "y_label": "Value",
            "chart_type": "line",
            "series": series,
            "meta": {
                "ticker": ticker,
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "smoothing_window": smoothing,
                "available_metrics": [
                    {"key": k, "label": v["label"], "source": v["source"]}
                    for k, v in TICKER_METRICS.items()
                ],
            },
        }

    # ── Signal Breakdown ────────────────────────────────────────────────

    async def _signal_breakdown(self, spec: dict) -> dict:
        """Group recommendation signals by signal name with chosen aggregation.

        Spec:
            tickers (list[str], optional) — filter
            from_date / to_date — defaults: last 30 days
            aggregation (str): count | sum | avg | min | max — over signal points
            actions (list[str], optional) — filter recs by action
            limit (int, default 25) — top N signals
            sort_by (str): value | name — default value (desc)
        """
        tickers: list[str] = [t.upper() for t in (spec.get("tickers") or [])]
        from_date = _parse_date(spec.get("from_date"), default=date.today() - timedelta(days=30))
        to_date = _parse_date(spec.get("to_date"), default=date.today())
        aggregation = (spec.get("aggregation") or "count").lower()
        if aggregation not in {"count", "sum", "avg", "min", "max"}:
            raise ValueError(f"Unknown aggregation: {aggregation}")
        actions: list[str] = [a.upper() for a in (spec.get("actions") or [])]
        limit = int(spec.get("limit") or 25)

        stmt = select(Recommendation).where(
            Recommendation.recommendation_date >= from_date,
            Recommendation.recommendation_date <= to_date,
        )
        if tickers:
            stmt = stmt.where(Recommendation.ticker.in_(tickers))
        if actions:
            stmt = stmt.where(Recommendation.action.in_(actions))

        result = await self._session.execute(stmt)
        recs = list(result.scalars().all())

        # Aggregate over the JSON signals array
        bucket_points: dict[str, list[float]] = defaultdict(list)
        bucket_count: Counter = Counter()
        for r in recs:
            signals = r.signals or []
            if not isinstance(signals, list):
                continue
            for s in signals:
                name = s.get("signal")
                pts = _to_float(s.get("points")) or 0.0
                if not name:
                    continue
                bucket_points[name].append(pts)
                bucket_count[name] += 1

        # Apply aggregation
        agg_rows: list[dict] = []
        for name in bucket_points.keys() | set(bucket_count.keys()):
            pts = bucket_points.get(name, [])
            cnt = bucket_count[name]
            if aggregation == "count":
                value = cnt
            elif aggregation == "sum":
                value = round(sum(pts), 2)
            elif aggregation == "avg":
                value = round(sum(pts) / len(pts), 2) if pts else 0.0
            elif aggregation == "min":
                value = round(min(pts), 2) if pts else 0.0
            else:  # max
                value = round(max(pts), 2) if pts else 0.0
            agg_rows.append({"name": name, "value": value, "count": cnt})

        sort_by = (spec.get("sort_by") or "value").lower()
        if sort_by == "name":
            agg_rows.sort(key=lambda r: r["name"])
        else:
            agg_rows.sort(key=lambda r: abs(r["value"]), reverse=True)
        agg_rows = agg_rows[:limit]

        # One series, X = signal name, Y = aggregation value
        return {
            "dataset": "signal_breakdown",
            "x_label": "Signal",
            "y_label": f"Points ({aggregation})" if aggregation != "count" else "Occurrences",
            "chart_type": "bar",
            "series": [{
                "name": f"{aggregation.upper()} of points" if aggregation != "count" else "Count",
                "data": [{"x": r["name"], "y": r["value"], "count": r["count"]} for r in agg_rows],
            }],
            "meta": {
                "aggregation": aggregation,
                "tickers_filter": tickers,
                "actions_filter": actions,
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "rec_count": len(recs),
                "available_aggregations": ["count", "sum", "avg", "min", "max"],
            },
        }

    # ── Industry Comparison ─────────────────────────────────────────────

    async def _industry_comparison(self, spec: dict) -> dict:
        """Industry-level metric across industries (snapshot) or over time (trend).

        Spec:
            industries (list[str], optional) — default all
            metric (str, default conviction_score) — from INDUSTRY_METRICS
            from_date / to_date — defaults: last 30 days for trend, ignored for snapshot
            view (str): trend | snapshot — default trend
        """
        industries: list[str] = spec.get("industries") or []
        metric = spec.get("metric") or "conviction_score"
        if metric not in INDUSTRY_METRICS:
            raise ValueError(f"Unknown industry metric: {metric}")
        col = INDUSTRY_METRICS[metric]["column"]
        view = (spec.get("view") or "trend").lower()
        from_date = _parse_date(spec.get("from_date"), default=date.today() - timedelta(days=30))
        to_date = _parse_date(spec.get("to_date"), default=date.today())

        if view == "snapshot":
            # Latest row per industry
            latest_q = await self._session.execute(select(func.max(IndustryRecommendation.rec_date)))
            latest_date = latest_q.scalar()
            if latest_date is None:
                return _empty("industry_comparison", "Industry", INDUSTRY_METRICS[metric]["label"], "bar")
            stmt = select(IndustryRecommendation).where(IndustryRecommendation.rec_date == latest_date)
            if industries:
                stmt = stmt.where(IndustryRecommendation.industry.in_(industries))
            stmt = stmt.order_by(IndustryRecommendation.industry.asc())
            rows = list((await self._session.execute(stmt)).scalars().all())
            data = [{"x": r.industry, "y": _to_float(getattr(r, col))} for r in rows]
            return {
                "dataset": "industry_comparison",
                "x_label": "Industry",
                "y_label": INDUSTRY_METRICS[metric]["label"],
                "chart_type": "bar",
                "series": [{"name": INDUSTRY_METRICS[metric]["label"], "data": data}],
                "meta": {
                    "view": "snapshot",
                    "metric": metric,
                    "snapshot_date": latest_date.isoformat() if latest_date else None,
                    "available_metrics": [
                        {"key": k, "label": v["label"]} for k, v in INDUSTRY_METRICS.items()
                    ],
                },
            }

        # trend view — one series per industry over date range
        stmt = select(IndustryRecommendation).where(
            IndustryRecommendation.rec_date >= from_date,
            IndustryRecommendation.rec_date <= to_date,
        )
        if industries:
            stmt = stmt.where(IndustryRecommendation.industry.in_(industries))
        stmt = stmt.order_by(IndustryRecommendation.industry.asc(), IndustryRecommendation.rec_date.asc())
        rows = list((await self._session.execute(stmt)).scalars().all())

        by_industry: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_industry[r.industry].append({
                "x": r.rec_date.isoformat(),
                "y": _to_float(getattr(r, col)),
            })
        series = [{"name": ind, "data": pts} for ind, pts in sorted(by_industry.items())]

        return {
            "dataset": "industry_comparison",
            "x_label": "Date",
            "y_label": INDUSTRY_METRICS[metric]["label"],
            "chart_type": "line",
            "series": series,
            "meta": {
                "view": "trend",
                "metric": metric,
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "available_metrics": [
                    {"key": k, "label": v["label"]} for k, v in INDUSTRY_METRICS.items()
                ],
            },
        }


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_date(value: Any, default: date) -> date:
    if value is None or value == "":
        return default
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return default


def _empty(dataset: str, x_label: str, y_label: str, chart_type: str) -> dict:
    return {
        "dataset": dataset,
        "x_label": x_label,
        "y_label": y_label,
        "chart_type": chart_type,
        "series": [],
        "meta": {},
    }
