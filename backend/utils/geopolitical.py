"""Geopolitical event detection and sector impact scoring for EdgeFlow.

Detects macro geopolitical events (military conflicts, trade wars, sanctions, etc.)
from news article text via keyword matching, then maps each event to sector-specific
conviction points scaled by FinBERT sentiment magnitude.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ── Event types ────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    MILITARY_CONFLICT = "MILITARY_CONFLICT"
    TRADE_WAR = "TRADE_WAR"
    SANCTIONS = "SANCTIONS"
    DIPLOMATIC_BREAKTHROUGH = "DIPLOMATIC_BREAKTHROUGH"
    OIL_DISRUPTION = "OIL_DISRUPTION"
    REGULATION = "REGULATION"


# ── Keyword sets per event type ────────────────────────────────────────────────
# Phrases are checked first (more specific), then single keywords.
# Detection requires >= 2 keyword hits per article to reduce false positives.

_EVENT_KEYWORDS: dict[EventType, list[str]] = {
    EventType.MILITARY_CONFLICT: [
        # Phrases (specific)
        "military strike", "missile attack", "air strike", "airstrike",
        "armed conflict", "troop deployment", "military escalation",
        "naval blockade", "drone strike", "military operation",
        "declaration of war", "ceasefire violation", "nuclear threat",
        "iran war", "persian gulf conflict", "middle east conflict",
        "south china sea", "taiwan strait",
        "no deal", "talks end", "talks collapse", "without an agreement",
        "talks fail", "negotiations fail", "walked out",
        # Keywords (broad — need >= 2 to fire)
        "invasion", "bombing", "missile", "troops",
        "combat", "insurgent", "terrorism", "retaliation",
        "warship", "ceasefire", "war ", "conflict",
        "military", "escalation",
    ],
    EventType.TRADE_WAR: [
        "trade war", "tariff hike", "tariff increase", "import duty",
        "export ban", "trade restriction", "trade deficit",
        "retaliatory tariff", "trade tension", "trade dispute",
        "trade barrier", "import tax", "export control",
        "chip export", "chip ban",
        "tariff", "tariffs", "protectionism", "dumping",
    ],
    EventType.SANCTIONS: [
        "economic sanctions", "trade sanctions", "sanctions imposed",
        "sanctions lifted", "sanctions relief", "sanctions package",
        "sanctions expanded", "financial sanctions",
        "frozen assets", "asset freeze",
        "sanctions", "sanctioned", "embargo", "blacklist",
    ],
    EventType.DIPLOMATIC_BREAKTHROUGH: [
        "peace deal", "peace agreement", "diplomatic breakthrough",
        "trade agreement", "ceasefire agreement",
        "diplomatic resolution", "treaty signed", "normalization",
        "de-escalation", "diplomatic progress", "summit agreement",
        "nuclear deal", "deal reached", "agreement reached",
        "ceasefire announced", "talks succeed",
    ],
    EventType.OIL_DISRUPTION: [
        "oil supply disruption", "oil embargo", "opec cut",
        "opec+ cut", "oil production cut", "pipeline attack",
        "strait of hormuz", "oil export ban", "oil supply shock",
        "oil price surge", "crude oil spike", "refinery attack",
        "opec production", "energy crisis", "oil shortage",
        "petroleum disruption", "oil supply", "oil futures",
        "oil price", "fuel cost", "gas price",
    ],
    EventType.REGULATION: [
        "antitrust probe", "antitrust lawsuit", "regulatory crackdown",
        "tech regulation", "ai regulation", "chip ban",
        "chip export restriction", "regulatory probe",
        "regulatory action", "regulatory overhaul",
        "antitrust", "deregulation",
    ],
}


# ── Sector impact map ──────────────────────────────────────────────────────────
# Maps (event_type) -> {edgeflow_sector_name: base_points}.
# Positive = bullish for that sector, negative = bearish.
# All base values >= 10 to ensure meaningful signals.

_SECTOR_IMPACT: dict[EventType, dict[str, int]] = {
    EventType.MILITARY_CONFLICT: {
        "AI/Semiconductors": -12,
        "Fintech/Payments": -10,
        "Energy/Commodities": +18,
        "Healthcare/Biotech": -10,
        "Consumer/Cloud/Enterprise": -12,
        "Industrials/Defense": +18,
        "Power/Utilities/Nuclear": +8,
        "Communications/Media": -10,
    },
    EventType.TRADE_WAR: {
        "AI/Semiconductors": -18,
        "Fintech/Payments": -12,
        "Energy/Commodities": -10,
        "Healthcare/Biotech": -10,
        "Consumer/Cloud/Enterprise": -12,
        "Industrials/Defense": -12,
        "Power/Utilities/Nuclear": +10,
        "Communications/Media": -10,
    },
    EventType.SANCTIONS: {
        "AI/Semiconductors": -15,
        "Fintech/Payments": -12,
        "Energy/Commodities": +12,
        "Healthcare/Biotech": -10,
        "Consumer/Cloud/Enterprise": -10,
        "Industrials/Defense": +12,
        "Power/Utilities/Nuclear": +12,
        "Communications/Media": -10,
    },
    EventType.DIPLOMATIC_BREAKTHROUGH: {
        "AI/Semiconductors": +12,
        "Fintech/Payments": +12,
        "Energy/Commodities": -10,
        "Healthcare/Biotech": +10,
        "Consumer/Cloud/Enterprise": +12,
        "Industrials/Defense": -12,
        "Power/Utilities/Nuclear": -10,
        "Communications/Media": +12,
    },
    EventType.OIL_DISRUPTION: {
        "AI/Semiconductors": -12,
        "Fintech/Payments": -10,
        "Energy/Commodities": +20,
        "Healthcare/Biotech": -10,
        "Consumer/Cloud/Enterprise": -12,
        "Industrials/Defense": -10,
        "Power/Utilities/Nuclear": +15,
        "Communications/Media": -10,
    },
    EventType.REGULATION: {
        "AI/Semiconductors": -15,
        "Fintech/Payments": -15,
        "Energy/Commodities": -10,
        "Healthcare/Biotech": -12,
        "Consumer/Cloud/Enterprise": -15,
        "Industrials/Defense": -10,
        "Power/Utilities/Nuclear": -15,
        "Communications/Media": -18,
    },
}

# Human-readable labels for signal names
_EVENT_LABELS: dict[EventType, str] = {
    EventType.MILITARY_CONFLICT: "Military Conflict",
    EventType.TRADE_WAR: "Trade War",
    EventType.SANCTIONS: "Sanctions",
    EventType.DIPLOMATIC_BREAKTHROUGH: "Diplomatic Breakthrough",
    EventType.OIL_DISRUPTION: "Oil/Energy Disruption",
    EventType.REGULATION: "Regulatory Crackdown",
}

# Minimum keyword matches per article to trigger detection
_MIN_KEYWORD_HITS = 2


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class DetectedEvent:
    """A geopolitical event detected from a news article."""
    event_type: EventType
    headline: str
    sentiment_score: float
    keyword_matches: int


@dataclass
class GeopoliticalSignal:
    """A sector-specific signal ready for the recommendation engine."""
    signal_name: str
    points: int
    detail: str


# ── Detection ──────────────────────────────────────────────────────────────────

def _count_keyword_matches(text: str, keywords: list[str]) -> int:
    """Count how many keywords/phrases appear in *text* (case-insensitive)."""
    lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in lower)


def detect_geopolitical_events(
    articles: list[dict],
) -> list[DetectedEvent]:
    """Detect geopolitical events from news article dicts.

    Each dict must have keys: ``headline``, ``summary``, ``sentiment_score``.

    Returns deduplicated events — one per event type, keeping the article
    with the strongest signal (highest keyword_matches * abs(sentiment)).
    """
    # Best event per type: (strength, DetectedEvent)
    best: dict[EventType, tuple[float, DetectedEvent]] = {}

    for article in articles:
        headline = article.get("headline", "")
        summary = article.get("summary", "")
        sentiment = float(article.get("sentiment_score", 0))
        text = f"{headline} {summary}"

        for event_type, keywords in _EVENT_KEYWORDS.items():
            hits = _count_keyword_matches(text, keywords)
            if hits < _MIN_KEYWORD_HITS:
                continue

            strength = hits * max(abs(sentiment), 0.1)
            current_best = best.get(event_type)

            if current_best is None or strength > current_best[0]:
                best[event_type] = (
                    strength,
                    DetectedEvent(
                        event_type=event_type,
                        headline=headline,
                        sentiment_score=sentiment,
                        keyword_matches=hits,
                    ),
                )

    return [ev for _, ev in best.values()]


# ── Scoring ────────────────────────────────────────────────────────────────────

def get_sector_signals(
    events: list[DetectedEvent],
    edgeflow_sector: str,
) -> list[GeopoliticalSignal]:
    """Map detected events to sector-specific signals.

    Severity scaling: ``base_points * (0.6 + 0.4 * min(abs(sentiment), 1.0))``.
    Floor of 0.6x ensures detected events always produce meaningful points.
    Final points clamped to [-20, +20].
    """
    signals: list[GeopoliticalSignal] = []

    for event in events:
        impact_map = _SECTOR_IMPACT.get(event.event_type)
        if not impact_map:
            continue

        base_points = impact_map.get(edgeflow_sector)
        if not base_points:
            continue

        severity = 0.6 + 0.4 * min(abs(event.sentiment_score), 1.0)
        raw = base_points * severity
        points = max(-20, min(20, round(raw)))

        if points == 0:
            continue

        label = _EVENT_LABELS.get(event.event_type, event.event_type.value)
        article_count_hint = f"{event.keyword_matches} keyword matches"

        signals.append(GeopoliticalSignal(
            signal_name=f"Geopolitical: {label}",
            points=points,
            detail=(
                f"{label} detected — \"{event.headline[:80]}\" "
                f"({article_count_hint}, sentiment {event.sentiment_score:+.2f})"
            ),
        ))

    return signals


# ── Convenience ────────────────────────────────────────────────────────────────

def detect_and_score(
    articles: list[dict],
    edgeflow_sector: str,
) -> list[GeopoliticalSignal]:
    """One-call entry point: detect events then score for a sector."""
    events = detect_geopolitical_events(articles)
    return get_sector_signals(events, edgeflow_sector)
