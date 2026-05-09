"""Executive summary + 5-day forward outlook for an industry.

Produces narrative text and a forward conviction projection from data
already on the IndustryRecommendation row plus a few member-ticker
financials. Deterministic (no LLM) — facts in, narrative out.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional


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


def _fmt_market_cap(cap: Optional[float]) -> str:
    if not cap or cap <= 0:
        return "—"
    if cap >= 1_000_000_000_000:
        return f"${cap / 1_000_000_000_000:.2f}T"
    if cap >= 1_000_000_000:
        return f"${cap / 1_000_000_000:.1f}B"
    if cap >= 1_000_000:
        return f"${cap / 1_000_000:.0f}M"
    return f"${cap:,.0f}"


def compute_forward_outlook(
    current_conviction: float,
    history_scores: list[float],
    etf_momentum_20d: Optional[float],
    avg_news_sentiment: Optional[float],
    active_catalyst_count: int,
    today: Optional[date] = None,
    horizon: int = 5,
) -> list[dict]:
    """Project conviction `horizon` calendar days forward.

    Light-touch model — explicit and explainable, NOT predictive in any
    statistical sense. Combines:

    - Historical slope: linear regression of last up-to-10 history points
      gives a per-day drift.
    - ETF 20d momentum: continuation nudge in the direction of the
      sector ETF's medium-term trend.
    - News sentiment: small drift toward the sentiment sign when news
      flow is non-trivial.
    - Mean reversion: 3% pull per day toward 0, so the projection
      doesn't run away.
    - Catalyst presence: when active catalysts cluster within the
      forward window, amplify the current sign (uncertainty cuts both
      ways but earnings catalysts statistically expand range).
    """
    today = today or date.today()
    cur = max(-100.0, min(100.0, float(current_conviction)))

    # Historical slope from last N points (where N=min(10, len))
    slope_per_day = 0.0
    pts = history_scores[-10:] if history_scores else []
    if len(pts) >= 3:
        n = len(pts)
        xs = list(range(n))
        mx = sum(xs) / n
        my = sum(pts) / n
        num = sum((xs[i] - mx) * (pts[i] - my) for i in range(n))
        den = sum((xs[i] - mx) ** 2 for i in range(n))
        slope_per_day = num / den if den > 0 else 0.0

    # ETF momentum drift — convert 20d total return to per-day points
    etf_drift_per_day = 0.0
    if etf_momentum_20d is not None:
        # 5pp 20d move -> ~1pt/day drift
        etf_drift_per_day = float(etf_momentum_20d) * 100.0 / 5.0

    # News drift — when sentiment magnitude is meaningful, add a small
    # daily nudge that decays with horizon.
    news_drift_per_day = 0.0
    if avg_news_sentiment is not None and abs(float(avg_news_sentiment)) >= 0.2:
        news_drift_per_day = float(avg_news_sentiment) * 1.5

    catalyst_amp = 0.0
    if active_catalyst_count >= 4:
        catalyst_amp = 4.0
    elif active_catalyst_count >= 2:
        catalyst_amp = 2.0

    # Blend: empirical weights chosen so the projection moves but not
    # wildly. Total per-day drift is bounded.
    raw_slope = (
        0.45 * slope_per_day
        + 0.35 * etf_drift_per_day
        + 0.20 * news_drift_per_day
    )
    # Cap drift to ±3 conviction points/day so the projection stays sane
    drift_per_day = max(-3.0, min(3.0, raw_slope))

    out: list[dict] = []
    for d in range(1, horizon + 1):
        # Mean reversion factor: gently pull toward 0
        decayed = cur * (0.97 ** d)
        # Catalyst amplification — same sign as current conviction
        amp = catalyst_amp * (1 if cur >= 0 else -1)
        projected = decayed + drift_per_day * d + amp
        projected = max(-100.0, min(100.0, projected))
        out.append({
            "forecast_date": today + timedelta(days=d),
            "day_offset": d,
            "conviction_score": Decimal(str(round(projected, 2))),
            "action": _classify(projected),
        })
    return out


def build_executive_summary(
    industry: str,
    action: str,
    conviction: float,
    member_count: int,
    bullish_count: int,
    bearish_count: int,
    breadth_above_50d_pct: Optional[float],
    etf_symbol: Optional[str],
    etf_rsi_14: Optional[float],
    etf_momentum_20d: Optional[float],
    avg_news_sentiment: Optional[float],
    news_article_count: int,
    geopolitical_points: Optional[float],
    active_catalyst_count: int,
    top_components: Iterable[dict],
    top_signals: Iterable[dict],
) -> str:
    """Build a 3-4 sentence narrative summary from raw observations."""
    parts: list[str] = []

    # 1. Headline
    parts.append(
        f"{industry} reads {action.replace('_', ' ')} "
        f"with conviction {conviction:+.0f}, scored across "
        f"{member_count} watchlist members "
        f"({bullish_count} bullish / {bearish_count} bearish)."
    )

    # 2. Top components — name the movers and their cap
    tops = list(top_components)
    if tops:
        component_phrases = []
        for c in tops[:3]:
            cap_str = _fmt_market_cap(c.get("market_cap"))
            conv = float(c.get("conviction") or 0)
            component_phrases.append(
                f"{c['ticker']} ({c.get('action','HOLD').replace('_',' ')}, "
                f"conv {conv:+.0f}, {cap_str})"
            )
        parts.append("Top movers: " + ", ".join(component_phrases) + ".")

    # 3. News + ETF technicals
    tech_bits: list[str] = []
    if avg_news_sentiment is not None and news_article_count >= 2:
        tone = (
            "constructive" if avg_news_sentiment >= 0.15
            else "cautious" if avg_news_sentiment <= -0.15
            else "mixed"
        )
        tech_bits.append(
            f"news flow {tone} ({avg_news_sentiment:+.2f} avg across "
            f"{news_article_count} articles)"
        )
    if etf_symbol and (etf_rsi_14 is not None or etf_momentum_20d is not None):
        rsi_phrase = (
            f"RSI {etf_rsi_14:.0f}" if etf_rsi_14 is not None else None
        )
        mom_phrase = (
            f"20d {etf_momentum_20d * 100:+.1f}%"
            if etf_momentum_20d is not None
            else None
        )
        etf_phrase = " / ".join(p for p in [rsi_phrase, mom_phrase] if p)
        if etf_phrase:
            tech_bits.append(f"{etf_symbol} {etf_phrase}")
    if breadth_above_50d_pct is not None:
        tech_bits.append(
            f"{breadth_above_50d_pct:.0f}% of members above 50d SMA"
        )
    if tech_bits:
        parts.append("Backdrop: " + "; ".join(tech_bits) + ".")

    # 4. Catalysts + geopolitics + dominant signal
    catalyst_geo: list[str] = []
    if active_catalyst_count >= 1:
        catalyst_geo.append(
            f"{active_catalyst_count} earnings catalyst"
            f"{'s' if active_catalyst_count != 1 else ''} within 14 days"
        )
    if geopolitical_points is not None and abs(float(geopolitical_points)) >= 5:
        gp = float(geopolitical_points)
        catalyst_geo.append(
            f"geopolitical net {gp:+.0f}"
        )
    sig_list = list(top_signals)
    if sig_list:
        # Highlight top 1 signal by absolute points
        top_sig = max(sig_list, key=lambda s: abs(int(s.get("points", 0))))
        catalyst_geo.append(
            f"dominant signal {top_sig['signal']} ({int(top_sig['points']):+d})"
        )
    if catalyst_geo:
        parts.append("Drivers: " + "; ".join(catalyst_geo) + ".")

    return " ".join(parts)
