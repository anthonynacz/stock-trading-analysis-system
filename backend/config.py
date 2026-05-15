from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://edgeflow:edgeflow@localhost:5432/edgeflow"
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

MAX_WATCHLIST = 60
MAX_PER_SECTOR = 12
MAX_CHANGES_PER_DAY = 10

# Watchlist filters
MIN_MARKET_CAP = 5_000_000_000      # $5B
MIN_AVG_VOLUME = 2_000_000          # 2M shares/day
MIN_OPTIONS_VOLUME = 1_000          # daily contracts
MIN_ANALYST_COVERAGE = 5

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
