"""User preferences (personalization) service.

Three responsibilities:

1. **Defaults + catalog** — `DEFAULT_PREFS`, `SIGNAL_GROUPS`, `RISK_PROFILES`,
   `ALERT_KEYS` are the canonical schema. Frontend reads these via
   `GET /api/me/preferences` (which also includes available sectors from
   `config.SECTORS`) so it can render the full settings UI in one round-trip.

2. **Validation + merge** — `validate_and_merge` accepts a partial payload,
   clamps weight ranges to [0, 2], drops unknown signal-group / sector keys,
   caps `custom_universe` size by the user's tier (`max_universe_slots`),
   and merges into the existing row. Save anything the user submits even if
   the feature isn't entitled — they can configure-now-use-later when
   upgrading. The downstream consumers (alerts worker, signal stacker)
   independently check tier before applying saved prefs.

3. **Ensure** — `ensure_preferences(db, user)` is idempotent; called from
   the auth middleware on every request so pre-retrofit users self-heal,
   matching the `ensure_subscription` pattern.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import SECTORS
from db.models import User, UserPreferences
from services.entitlements import (
    UNLIMITED,
    get_entitlements_for,
    get_subscription,
    get_tier_for,
)

logger = logging.getLogger(__name__)


# ── Catalog ─────────────────────────────────────────────────────────────────

RISK_PROFILES: list[dict[str, str]] = [
    {
        "key": "conservative",
        "label": "Conservative",
        "description": "Higher-delta ITM/ATM strikes, shorter DTE, prioritize probability over leverage.",
    },
    {
        "key": "moderate",
        "label": "Balanced",
        "description": "ATM-to-slightly-OTM, moderate DTE, mix of probability and leverage.",
    },
    {
        "key": "aggressive",
        "label": "Aggressive",
        "description": "Lower-delta OTM, longer DTE, maximum leverage with lower probability.",
    },
]


# Aggregated signal categories the user weights — finer granularity than the
# 50+ raw signals would be UI noise. The recommendation engine maps each
# raw signal to one of these groups when applying weights.
SIGNAL_GROUPS: list[dict[str, str]] = [
    {"key": "analyst",          "label": "Analyst Ratings",     "description": "T1/T2/T3 upgrades, downgrades, PT changes, revision clusters."},
    {"key": "earnings",         "label": "Earnings",            "description": "Beat/miss, catalyst window proximity, fiscal results."},
    {"key": "technical",        "label": "Technical",           "description": "RSI, SMA cross, momentum, volume confirmation, short squeeze setups."},
    {"key": "drawdown",         "label": "Drawdown",            "description": "Sharp 1d/2d/3d drops, distribution / heavy-selling volume."},
    {"key": "reversal",         "label": "Reversal",            "description": "Oversold bounces, strong reversal setups (drawdown + RSI + support)."},
    {"key": "value",            "label": "Value / 52w",         "description": "Near 52w high, deep pullback, below consensus PT."},
    {"key": "relative_strength","label": "Relative Strength",   "description": "Stock vs SPY 5d momentum comparison."},
    {"key": "ohlc",             "label": "Candlestick (OHLC)",  "description": "Gap-down after up day, upper-wick rejection, close near high/low."},
    {"key": "options_flow",     "label": "Options Flow",        "description": "Unusual call/put volume, P/C OI skew, IV rank, smart-money positioning."},
    {"key": "greeks",           "label": "Greeks",              "description": "Heavy theta decay, IV crush risk, favorable theta on near-term contracts."},
    {"key": "news_sentiment",   "label": "News Sentiment",      "description": "FinBERT sentiment, sector tailwind / headwind."},
    {"key": "geopolitical",     "label": "Geopolitical",        "description": "Military conflict, trade war, sanctions, oil disruption (sector-mapped)."},
    {"key": "insider",          "label": "Insider Activity",    "description": "Insider buying / selling, insider buy clusters."},
]


ALERT_KEYS: list[dict[str, Any]] = [
    {"key": "rec_change",        "label": "Recommendation changes",     "description": "When a watchlist ticker's daily action flips (e.g., HOLD → BUY).", "tier_min": "STARTER"},
    {"key": "conviction_breach", "label": "Conviction threshold breach", "description": "When a watchlist ticker's conviction crosses your threshold.",   "tier_min": "PRO"},
    {"key": "earnings_proximity","label": "Earnings approaching",        "description": "T-N days before a watchlist ticker reports.",                    "tier_min": "STARTER"},
    {"key": "unusual_flow",      "label": "Unusual options flow",        "description": "Call or put volume ≥3x the 20-day historical average.",         "tier_min": "PRO"},
    {"key": "news_spike",        "label": "News sentiment spike",        "description": "FinBERT magnitude crosses ±0.5 for a watchlist name.",          "tier_min": "PRO"},
    {"key": "insider_filing",    "label": "Insider filings",             "description": "New insider buy/sell on a watchlist name.",                    "tier_min": "PRO"},
]


def _default_signal_group_weights() -> dict[str, float]:
    return {g["key"]: 1.0 for g in SIGNAL_GROUPS}


def _default_industry_weights() -> dict[str, float]:
    return {sector: 1.0 for sector in SECTORS.keys()}


def _default_alerts_config() -> dict[str, Any]:
    return {
        "channel": "email",  # email | discord (Premium) — placeholder for future
        "rec_change": True,
        "conviction_breach": False,
        "conviction_threshold": 60,  # absolute conviction value
        "earnings_proximity": True,
        "earnings_days_before": 3,
        "unusual_flow": False,
        "news_spike": False,
        "insider_filing": False,
        "discord_webhook_url": None,
    }


def _default_digest_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "send_time_utc": "11:00",  # 7am EDT
        "include_top_picks": True,
        "include_position_health": True,
        "include_catalysts_3d": True,
        "include_industry_summary": False,
    }


def DEFAULT_PREFS() -> dict[str, Any]:
    return {
        "risk_profile": "moderate",
        "signal_group_weights": _default_signal_group_weights(),
        "industry_weights": _default_industry_weights(),
        "custom_universe": [],
        "alerts_config": _default_alerts_config(),
        "digest_config": _default_digest_config(),
    }


# ── Resolution ──────────────────────────────────────────────────────────────

async def ensure_preferences(db: AsyncSession, user: User) -> UserPreferences:
    """Idempotently create a UserPreferences row for the user.

    Caller commits. Cheap on the hot path: one SELECT when row exists, one
    INSERT-and-flush when not.
    """
    res = await db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    )
    pref = res.scalars().first()
    if pref is not None:
        return pref
    defaults = DEFAULT_PREFS()
    pref = UserPreferences(
        user_id=user.id,
        risk_profile=defaults["risk_profile"],
        signal_group_weights=defaults["signal_group_weights"],
        industry_weights=defaults["industry_weights"],
        custom_universe=defaults["custom_universe"],
        alerts_config=defaults["alerts_config"],
        digest_config=defaults["digest_config"],
    )
    db.add(pref)
    await db.flush()
    return pref


def serialize_preferences(pref: UserPreferences) -> dict[str, Any]:
    """Convert a UserPreferences row to a JSON-serializable dict.

    Returns the merged-with-defaults shape so downstream code never has to
    handle missing keys (e.g. a new alert key added after the row was created).
    """
    defaults = DEFAULT_PREFS()
    out: dict[str, Any] = {
        "risk_profile": pref.risk_profile or defaults["risk_profile"],
        "signal_group_weights": {**defaults["signal_group_weights"], **(pref.signal_group_weights or {})},
        "industry_weights": {**defaults["industry_weights"], **(pref.industry_weights or {})},
        "custom_universe": list(pref.custom_universe or []),
        "alerts_config": {**defaults["alerts_config"], **(pref.alerts_config or {})},
        "digest_config": {**defaults["digest_config"], **(pref.digest_config or {})},
        "updated_at": pref.updated_at.isoformat() if pref.updated_at else None,
    }
    return out


# ── Validation + merge ──────────────────────────────────────────────────────

def _clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return max(lo, min(hi, f))


def _valid_risk_profile(v: Any) -> str:
    keys = {p["key"] for p in RISK_PROFILES}
    return v if isinstance(v, str) and v in keys else "moderate"


def _validate_weight_dict(payload: Any, allowed_keys: set[str]) -> dict[str, float]:
    """Filter unknown keys, clamp values to [0, 2]."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in payload.items():
        if k in allowed_keys:
            out[k] = _clamp(v, 0.0, 2.0, default=1.0)
    return out


def _validate_alerts(payload: Any) -> dict[str, Any]:
    defaults = _default_alerts_config()
    if not isinstance(payload, dict):
        return defaults
    out = dict(defaults)
    bool_keys = {"rec_change", "conviction_breach", "earnings_proximity",
                 "unusual_flow", "news_spike", "insider_filing"}
    for k in bool_keys:
        if k in payload:
            out[k] = bool(payload[k])
    if "channel" in payload and payload["channel"] in ("email", "discord"):
        out["channel"] = payload["channel"]
    if "conviction_threshold" in payload:
        out["conviction_threshold"] = int(_clamp(payload["conviction_threshold"], 0, 100, default=60))
    if "earnings_days_before" in payload:
        out["earnings_days_before"] = int(_clamp(payload["earnings_days_before"], 0, 14, default=3))
    if "discord_webhook_url" in payload:
        v = payload["discord_webhook_url"]
        out["discord_webhook_url"] = v if isinstance(v, str) and v.startswith("https://") else None
    return out


def _validate_digest(payload: Any) -> dict[str, Any]:
    defaults = _default_digest_config()
    if not isinstance(payload, dict):
        return defaults
    out = dict(defaults)
    if "enabled" in payload:
        out["enabled"] = bool(payload["enabled"])
    if "send_time_utc" in payload:
        v = payload["send_time_utc"]
        # HH:MM 24h
        if isinstance(v, str) and len(v) == 5 and v[2] == ":":
            try:
                hh, mm = int(v[:2]), int(v[3:])
                if 0 <= hh < 24 and 0 <= mm < 60:
                    out["send_time_utc"] = f"{hh:02d}:{mm:02d}"
            except ValueError:
                pass
    for k in ("include_top_picks", "include_position_health",
              "include_catalysts_3d", "include_industry_summary"):
        if k in payload:
            out[k] = bool(payload[k])
    return out


def _validate_custom_universe(payload: Any, max_slots: int) -> list[str]:
    """Uppercase, dedupe, validate ticker shape, cap by tier slots.

    Cap check happens BEFORE append so max_slots=0 (FREE tier) returns []
    rather than the first valid ticker.
    """
    if not isinstance(payload, list) or max_slots <= 0:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in payload:
        if len(out) >= max_slots:
            break
        if not isinstance(raw, str):
            continue
        t = raw.strip().upper()
        if not t or len(t) > 10:
            continue
        if not t.replace(".", "").replace("-", "").isalpha():
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


async def validate_and_merge(
    db: AsyncSession,
    user: User,
    payload: dict[str, Any],
    pref: UserPreferences,
) -> UserPreferences:
    """Apply a (possibly partial) payload to the preferences row.

    All writes are accepted regardless of tier — UX is "configure now, use
    after upgrade." Tier-aware caps still apply where saving more would be
    misleading (custom_universe is capped by max_universe_slots).
    """
    sub = await get_subscription(db, user)
    tier = get_tier_for(user, sub)
    ent = get_entitlements_for(tier)

    if "risk_profile" in payload:
        pref.risk_profile = _valid_risk_profile(payload["risk_profile"])

    if "signal_group_weights" in payload:
        existing = pref.signal_group_weights or _default_signal_group_weights()
        validated = _validate_weight_dict(
            payload["signal_group_weights"],
            allowed_keys={g["key"] for g in SIGNAL_GROUPS},
        )
        pref.signal_group_weights = {**existing, **validated}

    if "industry_weights" in payload:
        existing = pref.industry_weights or _default_industry_weights()
        validated = _validate_weight_dict(
            payload["industry_weights"],
            allowed_keys=set(SECTORS.keys()),
        )
        pref.industry_weights = {**existing, **validated}

    if "alerts_config" in payload:
        existing = pref.alerts_config or _default_alerts_config()
        merged = {**existing, **_validate_alerts(payload["alerts_config"])}
        pref.alerts_config = merged

    if "digest_config" in payload:
        existing = pref.digest_config or _default_digest_config()
        merged = {**existing, **_validate_digest(payload["digest_config"])}
        pref.digest_config = merged

    if "custom_universe" in payload:
        max_slots = ent.max_universe_slots
        if max_slots >= UNLIMITED:
            max_slots = 200  # hard sanity cap even for ADMIN/PREMIUM
        pref.custom_universe = _validate_custom_universe(
            payload["custom_universe"], max_slots
        )

    await db.commit()
    await db.refresh(pref)
    return pref


# ── Signal-name → signal-group classifier ──────────────────────────────────
#
# Maps the raw signal names emitted by services/recommendation_engine.py to
# the user-facing signal_groups defined above. Used at READ time by
# `apply_signal_weights` to re-stack each recommendation's signals using the
# user's saved per-group multiplier and reclassify the action band.
#
# Dynamic prefixes (T1/T2/T3 Upgrade|Downgrade, Geopolitical: *) are handled
# by `signal_name_to_group` falling through to prefix tests.

_SIGNAL_NAME_TO_GROUP: dict[str, str] = {
    # analyst
    "Analyst Revision Cluster": "analyst",
    "PT Raise 5-10%": "analyst",
    "PT Raise >10%": "analyst",
    "PT Cut 5-10%": "analyst",
    "PT Cut >10%": "analyst",
    "Below Consensus PT": "analyst",
    # earnings
    "Earnings Beat": "earnings",
    "Earnings Beat + Raised Guidance": "earnings",
    "Earnings Miss": "earnings",
    "Earnings Miss + Lowered Guidance": "earnings",
    "Active Catalyst Window": "earnings",
    # technical
    "RSI Overbought": "technical",
    "RSI Oversold": "technical",
    "Above 50d SMA": "technical",
    "Below 50d SMA": "technical",
    "Above 200d SMA": "technical",
    "Below 200d SMA": "technical",
    "Strong 5d Momentum": "technical",
    "Weak 5d Momentum": "technical",
    "Strong 20d Momentum": "technical",
    "Weak 20d Momentum": "technical",
    "Volume-Confirmed Rally": "technical",
    "Volume-Confirmed Selloff": "technical",
    "Short Squeeze Setup": "technical",
    "High Short Interest": "technical",
    # drawdown
    "Sharp 1d Drop": "drawdown",
    "Rapid 2d Drawdown": "drawdown",
    "Rapid 3d Drawdown": "drawdown",
    "Distribution (Heavy Selling Volume)": "drawdown",
    # reversal
    "Oversold Bounce Setup": "reversal",
    "Strong Reversal Setup": "reversal",
    "Oversold After Drop": "reversal",
    # value / 52w
    "Near 52w High": "value",
    "Deep Pullback from 52w High": "value",
    # relative strength
    "Outperforming Market": "relative_strength",
    "Underperforming Market": "relative_strength",
    # OHLC candlestick
    "Gap-Down After Up Day": "ohlc",
    "Upper Wick Rejection": "ohlc",
    "Closed Near Low": "ohlc",
    "Closed Near High": "ohlc",
    # options flow
    "Unusual Call Volume": "options_flow",
    "Unusual Put Volume": "options_flow",
    "Bullish OI Skew": "options_flow",
    "Bearish OI Skew": "options_flow",
    "High IV Rank": "options_flow",
    "Smart Money Positioning": "options_flow",
    "Term Backwardation": "options_flow",
    "Elevated Put Skew": "options_flow",
    "Negative Gamma Exposure": "options_flow",
    "Options Pin Risk": "options_flow",
    # greeks
    "Heavy Theta Decay": "greeks",
    "Elevated Theta Decay": "greeks",
    "IV Crush Risk": "greeks",
    "IV Spike Warning": "greeks",
    "Favorable Theta": "greeks",
    # news sentiment
    "Positive News Sentiment": "news_sentiment",
    "Negative News Sentiment": "news_sentiment",
    "Sector Tailwind": "news_sentiment",
    "Sector Headwind": "news_sentiment",
    # insider
    "Insider Buying": "insider",
    "Insider Selling": "insider",
    "Insider Buy Cluster": "insider",
    # gates / not user-weightable (treat as ungrouped)
    "Stale Move Gate": "_other",
    "Stock Already Moved": "_other",
}


def signal_name_to_group(signal_name: str) -> Optional[str]:
    """Classify a raw signal name into one of SIGNAL_GROUPS' keys.

    Returns None for ungrouped signals (gates like 'Stale Move Gate' or any
    novel name we haven't categorized yet) — those skip user-weighting and
    contribute their raw points unchanged.
    """
    if signal_name in _SIGNAL_NAME_TO_GROUP:
        g = _SIGNAL_NAME_TO_GROUP[signal_name]
        return None if g == "_other" else g
    # Dynamic prefixes
    if signal_name.startswith("Geopolitical:"):
        return "geopolitical"
    # T1/T2/T3 Upgrade or Downgrade
    if (signal_name.startswith(("T1 ", "T2 ", "T3 "))
            and (signal_name.endswith("Upgrade") or signal_name.endswith("Downgrade"))):
        return "analyst"
    return None


# ── Read-time re-weighting ──────────────────────────────────────────────────

# Mirror of recommendation_engine._classify; duplicated here so this module
# stays self-contained. Update in lockstep if classification bands change.
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


def apply_signal_weights(
    raw_conviction: Optional[float],
    raw_action: str,
    signals: Optional[list[dict[str, Any]]],
    weights: dict[str, float],
) -> tuple[float, str, list[dict[str, Any]]]:
    """Re-weight each signal by the user's signal-group multiplier and
    return (new_conviction, new_action, weighted_signals).

    Called by routes that surface recommendations. Pure function — no DB.
    Edge cases:
      - signals is None / empty → returns raw values unchanged.
      - weights is empty / all 1.0 → conviction recomputes to ~raw (small
        rounding); action is preserved.
      - Unknown signal name → keeps raw points (multiplier = 1.0).

    The returned signal dicts include `points` AS WEIGHTED (not raw) plus
    a `points_raw` field for transparency. Frontend can show "+15 (×0.5
    from your weighting)".
    """
    if not signals:
        return float(raw_conviction or 0), raw_action, []

    out_signals: list[dict[str, Any]] = []
    total = 0.0
    for s in signals:
        name = s.get("signal", "")
        raw_pts = float(s.get("points", 0) or 0)
        group = signal_name_to_group(name)
        if group is None:
            mult = 1.0
        else:
            mult = float(weights.get(group, 1.0))
        weighted_pts = raw_pts * mult
        total += weighted_pts
        out_signals.append({
            "signal": name,
            "points": int(round(weighted_pts)),
            "points_raw": int(round(raw_pts)),
            "weight": round(mult, 2),
            "group": group,
            "detail": s.get("detail"),
        })

    # Same clamp band as the engine
    new_conviction = max(-100.0, min(100.0, total))
    return new_conviction, _classify(new_conviction), out_signals


def get_signal_group_weights(pref: UserPreferences) -> dict[str, float]:
    """Return the user's signal_group_weights dict, defaulting unset groups
    to 1.0 so callers never see KeyError on a new-since-save group."""
    base = {g["key"]: 1.0 for g in SIGNAL_GROUPS}
    base.update(pref.signal_group_weights or {})
    return base


# ── Catalog payload (frontend renders UI from this) ─────────────────────────

def get_catalog() -> dict[str, Any]:
    return {
        "risk_profiles": RISK_PROFILES,
        "signal_groups": SIGNAL_GROUPS,
        "alert_keys": ALERT_KEYS,
        "industries": list(SECTORS.keys()),
    }
