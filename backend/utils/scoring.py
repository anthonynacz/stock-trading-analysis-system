"""Scoring and calculation utilities for EdgeFlow services."""

from config import TIER1_FIRMS, TIER2_FIRMS, WATCHLIST_WEIGHTS

# ── Rating tier lookup ──────────────────────────────────────────────────────

_POSITIVE_RATINGS = {"buy", "outperform", "overweight"}
_NEUTRAL_RATINGS = {"hold", "neutral", "equal-weight", "market perform"}
_NEGATIVE_RATINGS = {"sell", "underperform", "underweight"}

# ── Sentiment keyword sets ──────────────────────────────────────────────────

_POSITIVE_WORDS = {
    "upgrade", "upgraded", "beat", "beats", "raised", "raises",
    "growth", "bullish", "strong", "stronger", "outperform",
    "buy", "positive", "exceeded", "record", "innovation",
    "breakthrough", "partnership", "approval", "surge", "surges",
    "surged", "rally", "rallied", "rallies", "gains", "gained",
    "climbs", "climbed", "soars", "soared", "jumps", "jumped",
    "rises", "rose", "rising", "momentum", "demand", "robust",
    "likes", "liked", "favors", "favored", "optimistic",
    "opportunity", "opportunities", "expansion", "expands",
    "expanded", "launch", "launches", "launched",
    "profit", "profits", "profitable", "revenue",
    "upside", "winner", "winning", "boom", "booming",
    "accelerate", "accelerating", "recommends", "recommended",
}

_NEGATIVE_WORDS = {
    "downgrade", "downgraded", "miss", "misses", "missed",
    "cut", "cuts", "decline", "declines", "declined", "declining",
    "bearish", "weak", "weakens", "weakness",
    "underperform", "sell", "negative", "warning", "warns",
    "layoff", "layoffs", "lawsuit", "investigation",
    "recall", "default", "defaults", "crash", "crashes",
    "crashed", "plunge", "plunges", "plunged", "drops",
    "dropped", "falls", "fell", "falling", "slump", "slumps",
    "slumped", "tumbles", "tumbled", "sinks", "sank",
    "risk", "risks", "risky", "concern", "concerns",
    "loss", "losses", "losing", "fear", "fears",
    "downturn", "recession", "slowdown", "slowing",
    "overvalued", "bubble", "selloff", "headwinds",
    "disappointing", "disappointed", "struggles", "struggling",
}

# ── News category keyword mapping ──────────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "EARNINGS": [
        "earnings", "revenue", "profit", "EPS", "quarterly",
        "guidance", "beat", "miss", "results", "fiscal",
    ],
    "ANALYST": [
        "analyst", "upgrade", "downgrade", "price target",
        "rating", "initiated", "reiterate", "coverage",
    ],
    "MACRO": [
        "fed", "interest rate", "inflation", "GDP", "unemployment",
        "treasury", "monetary", "fiscal policy", "recession", "CPI",
    ],
    "SECTOR": [
        "sector", "industry", "market share", "competition",
        "regulation", "tariff", "trade", "supply chain",
    ],
    "INSIDER": [
        "insider", "CEO", "CFO", "executive", "board",
        "filing", "10-K", "10-Q", "SEC", "buyback",
    ],
    "PRODUCT": [
        "launch", "product", "release", "patent", "FDA",
        "approval", "innovation", "partnership", "deal", "contract",
    ],
}

# ── Tier bonus helpers ──────────────────────────────────────────────────────

_TIER_BONUS = {1: 30, 2: 15, 3: 5}
_REITERATION_BONUS = {1: 10, 2: 5, 3: 2}


def _rating_tier(rating: str) -> str:
    """Map a rating string to its tier: positive / neutral / negative."""
    r = rating.strip().lower()
    if r in _POSITIVE_RATINGS:
        return "positive"
    if r in _NEUTRAL_RATINGS:
        return "neutral"
    if r in _NEGATIVE_RATINGS:
        return "negative"
    return "unknown"


# ── Public API ──────────────────────────────────────────────────────────────


def get_firm_tier(firm_name: str) -> int:
    """Return 1 for tier-1, 2 for tier-2, 3 otherwise.

    Uses case-insensitive substring matching so that e.g. ``"Goldman"``
    matches ``"Goldman Sachs"`` in the tier-1 set.
    """
    name_lower = firm_name.lower()
    for firm in TIER1_FIRMS:
        if name_lower in firm.lower() or firm.lower() in name_lower:
            return 1
    for firm in TIER2_FIRMS:
        if name_lower in firm.lower() or firm.lower() in name_lower:
            return 2
    return 3


def calculate_impact_score(
    rating_type: str,
    firm_tier: int,
    pt_change_pct: float = 0.0,
) -> float:
    """Return a 0-100 impact score for an analyst action.

    Parameters
    ----------
    rating_type:
        One of TIER_CHANGE, PT_CHANGE, INITIATION, REITERATION.
    firm_tier:
        1, 2, or 3 — from :func:`get_firm_tier`.
    pt_change_pct:
        Percentage change in price target (used for PT_CHANGE).
    """
    rt = rating_type.upper()
    bonus = _TIER_BONUS.get(firm_tier, 5)

    if rt == "TIER_CHANGE":
        return min(70.0 + bonus, 100.0)

    if rt == "PT_CHANGE":
        # Scale abs(pt_change_pct) into 0-40 range (cap at 40)
        proportional = min(abs(pt_change_pct), 100.0) / 100.0 * 40.0
        return min(30.0 + proportional, 100.0)

    if rt == "INITIATION":
        return min(50.0 + bonus, 100.0)

    if rt == "REITERATION":
        reit_bonus = _REITERATION_BONUS.get(firm_tier, 2)
        return min(10.0 + reit_bonus, 100.0)

    return 0.0


def calculate_iv_rank(
    current_iv: float,
    high_52w: float,
    low_52w: float,
) -> float:
    """Return 0-100 IV rank.  Returns 50.0 when the 52-week range is zero."""
    spread = high_52w - low_52w
    if spread == 0:
        return 50.0
    return (current_iv - low_52w) / spread * 100.0


def calculate_composite_score(stock_data: dict) -> float:
    """Weighted composite watchlist score (0-100).

    ``stock_data`` keys should match :pydata:`WATCHLIST_WEIGHTS`.
    Missing keys default to 50 (neutral).
    """
    score = 0.0
    for key, weight in WATCHLIST_WEIGHTS.items():
        sub_score = stock_data.get(key, 50.0)
        score += sub_score * weight
    return score


def classify_rating_type(
    previous_rating: str | None,
    new_rating: str | None,
) -> str:
    """Classify an analyst action into a rating-type bucket.

    Returns one of INITIATION, TIER_CHANGE, PT_CHANGE, or REITERATION.
    """
    if previous_rating is None:
        return "INITIATION"

    if new_rating is None:
        return "REITERATION"

    prev_tier = _rating_tier(previous_rating)
    new_tier = _rating_tier(new_rating)

    if prev_tier != new_tier:
        return "TIER_CHANGE"

    if previous_rating.strip().lower() != new_rating.strip().lower():
        return "PT_CHANGE"

    return "REITERATION"


_POSITIVE_PHRASES = [
    "price target raised", "strong demand", "strong growth",
    "strong buy", "top pick", "best idea", "ai chip demand",
    "beat expectations", "above consensus", "market outperform",
    "new high", "all-time high", "robust demand", "strong earnings",
    "upward revision", "positive outlook", "buy rating",
]

_NEGATIVE_PHRASES = [
    "price target cut", "price target lowered", "supply chain",
    "trade war", "under pressure", "below expectations",
    "missed estimates", "weak guidance", "profit warning",
    "going concern", "debt downgrade", "market crash",
    "sell rating", "negative outlook", "downward revision",
]


def calculate_sentiment_score(text: str) -> float:
    """Keyword-based finance sentiment score in [-1.0, 1.0].

    Combines single-word matching with phrase matching for better
    coverage of financial headlines.
    """
    lower = text.lower()
    words = lower.split()

    # Single-word matches
    pos = sum(1 for w in words if w in _POSITIVE_WORDS)
    neg = sum(1 for w in words if w in _NEGATIVE_WORDS)

    # Phrase matches (weighted heavier — a phrase is more deliberate signal)
    for phrase in _POSITIVE_PHRASES:
        if phrase in lower:
            pos += 2
    for phrase in _NEGATIVE_PHRASES:
        if phrase in lower:
            neg += 2

    total = pos + neg
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / total))


def classify_news_category(headline: str) -> str:
    """Classify a headline into a news category via keyword matching.

    Returns EARNINGS, ANALYST, MACRO, SECTOR, INSIDER, or PRODUCT.
    Falls back to MACRO when no keywords match.
    """
    h_lower = headline.lower()
    best_category = "MACRO"
    best_count = 0

    for category, keywords in _CATEGORY_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw.lower() in h_lower)
        if count > best_count:
            best_count = count
            best_category = category

    return best_category


def assign_impact_level(
    category: str,
    sentiment_score: float,
    firm_tier: int | None = None,
) -> str:
    """Return HIGH, MEDIUM, or LOW impact level."""
    if abs(sentiment_score) > 0.6:
        return "HIGH"
    if category.upper() == "ANALYST" and firm_tier == 1:
        return "HIGH"
    if category.upper() == "EARNINGS":
        return "HIGH"
    if abs(sentiment_score) < 0.2:
        return "LOW"
    return "MEDIUM"


def compute_ticker_relevance(
    ticker: str,
    company_name: str | None,
    headline: str,
    summary: str | None,
    api_related_tickers: list[str],
    was_searched_ticker: bool,
) -> tuple[float, str]:
    """Compute how relevant a news article is to a specific ticker.

    Returns ``(relevance_score, relevance_source)`` or ``(0.0, "")`` if
    the article is not relevant.
    """
    headline_lower = headline.lower()
    summary_lower = (summary or "").lower()
    ticker_lower = ticker.lower()

    # Build searchable name tokens from company name (first word if > 3 chars)
    name_tokens: list[str] = []
    if company_name:
        for word in company_name.split():
            w = word.strip(",.()").lower()
            if len(w) > 3:
                name_tokens.append(w)
                break  # use only the first significant word

    def _in_text(text: str) -> bool:
        if ticker_lower in text.split():
            return True
        return any(t in text for t in name_tokens)

    if _in_text(headline_lower):
        return 1.0, "HEADLINE"

    if summary_lower and _in_text(summary_lower):
        return 0.7, "SUMMARY"

    if ticker.upper() in [t.upper().strip() for t in api_related_tickers]:
        return 0.5, "API_RELATED"

    if was_searched_ticker:
        return 0.3, "SEARCHED"

    return 0.0, ""
