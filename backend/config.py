from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://edgeflow:edgeflow@localhost:5432/edgeflow"
    # Base URL of the portal, used for deep links in Discord alerts (no trailing slash).
    PUBLIC_APP_URL: str = ""
    FMP_API_KEY: str = ""
    FINNHUB_API_KEY: str = ""
    NEWSAPI_KEY: str = ""

    # ── Auth / multi-tenancy ────────────────────────────────────────────────
    # When True, all protected routes resolve to the LEGACY_USER_EMAIL admin
    # user and JWTs are not required. Default ON during the SaaS retrofit so
    # existing UI keeps working; flip OFF when frontend auth integration ships.
    LEGACY_MODE: bool = True
    LEGACY_USER_EMAIL: str = "anthonynacouzy@gmail.com"

    # JWT verification — provider-agnostic. Configure ONE of:
    #   JWT_JWKS_URL   — for OIDC providers (Supabase / Clerk / Auth0)
    #   JWT_HS256_SECRET — for HS256-signed tokens (dev or simple flows)
    JWT_JWKS_URL: str = ""
    JWT_HS256_SECRET: str = ""
    JWT_ALGORITHMS: str = "RS256,HS256"  # comma-separated
    JWT_AUDIENCE: str = ""
    JWT_ISSUER: str = ""

    # Enables POST /api/dev/token (mints a short-lived HS256 JWT). Dev only.
    DEV_TOKEN_ENABLED: bool = False

    # Max in-flight per-ticker news fetches inside NewsScanner.scan_news. The
    # phase is bounded by Finnhub /company-news (60 calls/min on the free
    # tier); 10 is a safe default for a ~60-ticker watchlist.
    NEWS_FETCH_CONCURRENCY: int = 10

    # Anthropic API — used by research enrichment for narrative summary and
    # bull/bear/watch synthesis. When unset, enrichment skips the LLM calls
    # but still produces clustered news / sentiment timeline / top headlines.
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    model_config = {"env_file": [".env", "../.env"], "extra": "ignore"}

    @property
    def jwt_algorithms_list(self) -> list[str]:
        return [a.strip() for a in self.JWT_ALGORITHMS.split(",") if a.strip()]


settings = Settings()

# ── Sector universe ──────────────────────────────────────────────────────────

SECTORS: dict[str, dict] = {
    "AI/Semiconductors": {
        "universe": [
            "NVDA", "AMD", "AVGO", "MRVL", "QCOM", "INTC", "MU", "AMAT",
            "LRCX", "KLAC", "ASML", "TSM", "ARM", "SMCI", "ALAB", "CRDO", "CRWV",
        ],
        "max_stocks": 6,
    },
    "Fintech/Payments": {
        "universe": [
            "PYPL", "SQ", "SOFI", "V", "MA", "ADYEN", "FIS", "GPN",
            "COIN", "AFRM", "NU", "TOST", "BILL",
        ],
        "max_stocks": 6,
    },
    "Energy/Commodities": {
        "universe": [
            "XOM", "CVX", "COP", "SLB", "OXY", "EOG", "MPC", "VLO",
            "PSX", "HAL", "FSLR", "ENPH",
        ],
        "max_stocks": 6,
    },
    "Healthcare/Biotech": {
        "universe": [
            "LLY", "NVO", "ABBV", "JNJ", "PFE", "MRNA", "BMY", "AMGN",
            "GILD", "REGN", "VRTX",
        ],
        "max_stocks": 6,
    },
    "Consumer/Cloud/Enterprise": {
        "universe": [
            "AMZN", "MSFT", "GOOGL", "AAPL", "CRM", "SNOW",
            "PLTR", "NET", "SHOP", "UBER", "ABNB",
        ],
        "max_stocks": 6,
    },
    "Industrials/Defense": {
        "universe": [
            "LMT", "RTX", "NOC", "GD", "BA", "KTOS", "AVAV", "PWR",
            "CAT", "DE", "ETN", "HON",
        ],
        "max_stocks": 6,
    },
    "Power/Utilities/Nuclear": {
        "universe": [
            "VST", "CEG", "TLN", "NEE", "SO", "DUK", "AEP", "SRE",
            "GEV", "SMR", "OKLO", "CCJ",
        ],
        "max_stocks": 6,
    },
    "Communications/Media": {
        "universe": [
            "NFLX", "DIS", "META", "GOOG", "CMCSA", "VZ", "T",
            "TMUS", "SPOT", "TTD", "ROKU",
        ],
        "max_stocks": 6,
    },
}

# Representative sector ETFs — primary tape for industry-level technicals.
# Used by industry_analyzer for RSI / momentum computation.
SECTOR_ETFS: dict[str, str] = {
    "AI/Semiconductors": "SOXX",
    "Fintech/Payments": "XLF",
    "Energy/Commodities": "XLE",
    "Healthcare/Biotech": "XLV",
    "Consumer/Cloud/Enterprise": "XLK",
    "Industrials/Defense": "ITA",
    "Power/Utilities/Nuclear": "XLU",
    "Communications/Media": "XLC",
}

# ── Constants ────────────────────────────────────────────────────────────────

# ── Watchlist rotation rules (services/watchlist_manager.py) ────────────────
# Size caps, enforced by the daily rotation. Above a cap the rotation shrinks
# gradually: it drops the lowest-scoring removable names, at most
# MAX_CHANGES_PER_DAY per run, and exits get the budget before additions.
MAX_WATCHLIST = 60                  # total active names
MAX_PER_SECTOR = 12                 # active names per sector
MAX_CHANGES_PER_DAY = 10            # additions + removals per automatic rotation

# Hysteresis: a candidate enters only if it ranks in the top ENTER_RANK of its
# sector, or beats the weakest removable member of that sector by ENTER_MARGIN
# composite points. At a cap, an entrant needs a free slot or must beat the
# weakest removable member of its sector by ENTER_MARGIN (one in, one out).
ENTER_RANK = 10
ENTER_MARGIN = 5.0
# A member exits only after ranking worse than EXIT_RANK in its sector (or
# failing the liquidity filters) on EXIT_DAYS consecutive rotation runs.
# The count is stored in watchlist.weak_days and resets on a strong run.
EXIT_RANK = 15
EXIT_DAYS = 2
# No automatic exit (weak, toxic or shrink) before this many NYSE trading days
# since the stint's added_date.
MIN_STAY_TRADING_DAYS = 5
# The grace lock set by a manual rotate-in expires after LOCK_DAYS trading days
# since added_date. Locks set by the user (lock toggle) and manual entries
# (is_manual) do not expire.
LOCK_DAYS = 10
# Toxic exit (TOXIC_CONVICTION_DAYS consecutive SELL/STRONG_SELL recs). Off by
# default: bearish names are put candidates, not dead weight. When True, toxic
# names exit like before (overriding unusual-options protection), still
# subject to the minimum stay.
DROP_TOXIC = False
# Never rotated in, never offered as a rotate-out replacement: ETFs / index
# products in universe_stocks that are not single equities (they also fail
# the market-cap filter). The universe table itself is left untouched.
ROTATION_EXCLUDE = frozenset({"SPY", "QQQ", "XLE", "XLF", "HYG", "EWZ"})
# Scoring inputs (analyst grades, earnings dates) come from the analyst_ratings
# and earnings_calendar tables; a ticker's inputs are re-fetched from the API
# only when they are older than this many days (or missing).
ROTATION_INPUTS_MAX_AGE_DAYS = 3
# Unusual-options protection: the last end-of-session options snapshot of the
# last completed session has call volume >= UNUSUAL_OPTIONS_MULTIPLE x the
# average end-of-session call volume of the UNUSUAL_OPTIONS_LOOKBACK_SESSIONS
# sessions before it (at least UNUSUAL_OPTIONS_MIN_BASELINE of them).
UNUSUAL_OPTIONS_MULTIPLE = 3.0
UNUSUAL_OPTIONS_LOOKBACK_SESSIONS = 20
UNUSUAL_OPTIONS_MIN_BASELINE = 5

# Watchlist filters
MIN_MARKET_CAP = 5_000_000_000      # $5B
MIN_AVG_VOLUME = 2_000_000          # 2M shares/day
# Not a filter: scale of the options_liquidity sub-score (avg share volume of
# MIN_OPTIONS_VOLUME * 5000 scores 100).
MIN_OPTIONS_VOLUME = 1_000

# Scoring weights (must sum to 1.0)
WATCHLIST_WEIGHTS = {
    "catalyst_proximity": 0.25,
    "analyst_momentum": 0.20,
    "recommendation_conviction": 0.15,
    "options_liquidity": 0.10,
    "sector_momentum": 0.15,
    "volatility_profile": 0.05,
    "institutional_flow": 0.05,
    "price_vs_consensus_pt": 0.05,
}

# Watchlist toxic-removal: consecutive SELL/STRONG_SELL days to trigger removal
TOXIC_CONVICTION_DAYS = 3

# Firm tiers
TIER1_FIRMS = {
    "Goldman Sachs", "Morgan Stanley", "Bank of America", "JPMorgan",
    "Barclays", "Citigroup", "UBS", "Deutsche Bank", "Wells Fargo",
}
TIER2_FIRMS = {
    "Evercore", "Oppenheimer", "Stifel", "RBC Capital", "Cantor Fitzgerald",
    "Jefferies", "Piper Sandler", "Raymond James", "Wolfe Research",
    "Bernstein", "Cowen", "KeyBanc", "Truist", "BTIG", "Mizuho",
}
# Everything else is T3
