"""Geopolitical event detection and sector impact scoring for EdgeFlow.

Detects macro geopolitical events (military conflicts, trade wars, sanctions, etc.)
from news article text via keyword matching, then maps each event to sector-specific
conviction points scaled by FinBERT sentiment magnitude.

An article counts as an event only when its HEADLINE names the event (an anchor term, e.g.
"tariff", "export ban", "missile", "OPEC cut") and the headline plus summary give at least two
distinct keyword hits. Matching is whole-word (plurals count once), and single-company results
stories ("3M's Margins Improve Amid Cost Pressures") are skipped: a company discussing its costs
is not a trade war. Before 2026-10-12 any two substring hits anywhere in headline + summary fired,
so "tariffs" alone counted twice and "What Cheaper Gas Would Mean For Costco's Sales" was an oil
disruption that penalised every chip stock.
"""

from __future__ import annotations

import re
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
        "warship", "ceasefire", "war", "conflict",
        "military", "escalation",
    ],
    EventType.TRADE_WAR: [
        "trade war", "tariff hike", "tariff increase", "import duty",
        "export ban", "trade restriction", "trade deficit",
        "retaliatory tariff", "trade tension", "trade dispute",
        "trade barrier", "import tax", "export control",
        "chip export", "chip ban",
        "tariff", "protectionism", "anti-dumping",
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
        "iran nuclear deal", "deal reached", "agreement reached",
        "ceasefire announced", "talks succeed",
    ],
    EventType.OIL_DISRUPTION: [
        "oil supply disruption", "oil embargo", "opec cut",
        "opec+ cut", "oil production cut", "pipeline attack",
        "strait of hormuz", "oil export ban", "oil supply shock",
        "oil price surge", "crude oil spike", "refinery attack",
        "opec production", "energy crisis", "oil shortage",
        "petroleum disruption", "oil supply", "oil futures",
        "oil price", "opec", "export ban",
    ],
    EventType.REGULATION: [
        "antitrust probe", "antitrust lawsuit", "regulatory crackdown",
        "tech regulation", "ai regulation", "chip ban",
        "chip export restriction", "regulatory probe",
        "regulatory action", "regulatory overhaul",
        "antitrust",
    ],
}

# Anchors: at least one must be in the HEADLINE for the article to describe the event at all.
# Generic words ("conflict", "deal reached", "oil price", "walked out") only confirm an anchor.
_EVENT_ANCHORS: dict[EventType, list[str]] = {
    EventType.MILITARY_CONFLICT: [
        "military strike", "missile attack", "air strike", "airstrike", "armed conflict", "troop deployment",
        "military escalation", "naval blockade", "drone strike", "military operation", "declaration of war",
        "ceasefire violation", "nuclear threat", "iran war", "persian gulf conflict", "middle east conflict",
        "south china sea", "taiwan strait", "invasion", "bombing", "missile", "troops", "warship",
        "war in", "war with", "at war", "war between",
    ],
    EventType.TRADE_WAR: [
        "trade war", "tariff", "import duty", "export ban", "export control", "trade restriction",
        "trade tension", "trade dispute", "trade barrier", "chip export", "chip ban", "protectionism",
        "anti-dumping",
    ],
    EventType.SANCTIONS: ["sanction", "embargo", "blacklist", "asset freeze", "frozen assets"],
    EventType.DIPLOMATIC_BREAKTHROUGH: [
        "peace deal", "peace agreement", "diplomatic breakthrough", "trade agreement", "ceasefire agreement",
        "ceasefire announced", "treaty signed", "iran nuclear deal", "nuclear deal with iran", "summit agreement",
        "de-escalation", "trade deal",
    ],
    EventType.OIL_DISRUPTION: [
        "oil supply disruption", "oil embargo", "opec", "oil production cut", "pipeline attack",
        "strait of hormuz", "oil export ban", "oil supply shock", "oil price surge", "oil prices surge",
        "oil prices spike", "oil prices soar", "crude oil spike", "crude spike", "crude surge",
        "refinery attack", "energy crisis", "oil shortage", "petroleum disruption",
        "diesel export ban", "fuel export ban", "crude export ban", "gasoline export ban", "lng export ban",
    ],
    EventType.REGULATION: [
        "antitrust", "regulatory crackdown", "tech regulation", "ai regulation", "chip ban",
        "chip export restriction", "regulatory probe", "regulatory action", "regulatory overhaul",
    ],
}

# A single company's results story ("3M's Margins Improve ...", "Costco's Sales And Margins") is
# never a macro event, whatever costs or tariffs it mentions.
_COMPANY_RESULTS = re.compile(
    r"\b[\w&.\-]+'s\b.*\b(?:margins?|earnings|sales|revenue|profits?|guidance|quarter|results|outlook|stock|shares)\b",
    re.IGNORECASE,
)


# Escalation events that a headline can describe in reverse: tariff / sanctions RELIEF is not a trade war.
_RELIEF = re.compile(
    r"\b(?:relief|roll(?:s|ed|ing)?\s+back|rollback|eas(?:e|es|ed|ing)|lift(?:s|ed|ing)?|exempt\w*|"
    r"paus(?:e|es|ed|ing)|truce|scrap\w*|remov\w*|lower(?:s|ed|ing)?|cuts?\s+tariffs?|waiv\w*)\b",
    re.IGNORECASE,
)
_RELIEF_EVENTS = {EventType.TRADE_WAR, EventType.SANCTIONS}
_FUEL_EXPORT = re.compile(r"\b(?:diesel|fuel|oil|crude|lng|gasoline|gas)\s+export\s+bans?\b", re.IGNORECASE)
# A weapons or defense CONTRACT is business news, not a conflict ("Boeing Wins $14.7B Missile Deal").
_DEFENSE_CONTRACT = re.compile(
    r"\b(?:missiles?|defen[cs]e|fighter\s+jets?|munitions?|weapons?)\s+(?:deal|contract|order|award|program)s?\b",
    re.IGNORECASE,
)


def _term_regex(term: str) -> re.Pattern:
    """Whole-word, case-insensitive; plural and verb endings ("tariffs", "blacklisted") count as the term."""
    body = re.escape(term.strip()).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){body}(?:s|es|ed|d|ing)?(?![a-z0-9])", re.IGNORECASE)


_KEYWORD_RE: dict[EventType, list[re.Pattern]] = {
    et: [_term_regex(k) for k in dict.fromkeys(kws + _EVENT_ANCHORS[et])] for et, kws in _EVENT_KEYWORDS.items()
}
_ANCHOR_RE: dict[EventType, list[re.Pattern]] = {
    et: [_term_regex(k) for k in kws] for et, kws in _EVENT_ANCHORS.items()
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

def _count_keyword_matches(text: str, keywords: list[str] | list[re.Pattern]) -> int:
    """Count keyword hits in *text*: whole words (plural included), each occurrence once, and a
    phrase and a word inside it ("missile attack" / "missile") count once (overlaps are merged)."""
    pats = [k if isinstance(k, re.Pattern) else _term_regex(k) for k in keywords]
    spans = sorted(m.span() for p in pats for m in p.finditer(text))
    count, end = 0, -1
    for a, b in spans:
        if a >= end:
            count += 1
            end = b
        else:
            end = max(end, b)
    return count


def _anchored(headline: str, event_type: EventType) -> bool:
    return any(p.search(headline) for p in _ANCHOR_RE[event_type])


def _phrase_anchored(headline: str, event_type: EventType) -> bool:
    """A multi-word anchor in the headline ("military strike", "export ban") is specific enough to
    count as two hits on its own."""
    return any(" " in k and _term_regex(k).search(headline) for k in _EVENT_ANCHORS[event_type])


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
        if _COMPANY_RESULTS.search(headline):
            continue

        for event_type in _EVENT_KEYWORDS:
            if not _anchored(headline, event_type):
                continue
            if event_type in _RELIEF_EVENTS and _RELIEF.search(headline):
                continue
            if event_type == EventType.MILITARY_CONFLICT and _DEFENSE_CONTRACT.search(headline):
                continue
            if event_type == EventType.TRADE_WAR and _FUEL_EXPORT.search(headline):
                continue
            hits = _count_keyword_matches(text, _KEYWORD_RE[event_type])
            hits += 1 if _phrase_anchored(headline, event_type) else 0
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
