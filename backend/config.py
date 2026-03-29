from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://edgeflow:edgeflow@localhost:5432/edgeflow"
    FMP_API_KEY: str = ""
    FINNHUB_API_KEY: str = ""
    NEWSAPI_KEY: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# ── Sector universe ──────────────────────────────────────────────────────────

SECTORS: dict[str, dict] = {
    "AI/Semiconductors": {
        "universe": [
            "NVDA", "AMD", "AVGO", "MRVL", "QCOM", "INTC", "MU", "AMAT",
            "LRCX", "KLAC", "ASML", "TSM", "ARM", "SMCI", "ALAB", "CRDO",
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
            "PSX", "HAL", "FSLR", "ENPH", "NEE",
        ],
        "max_stocks": 6,
    },
    "Healthcare/Biotech": {
        "universe": [
            "LLY", "NVO", "ABBV", "JNJ", "PFE", "MRNA", "BMY", "AMGN",
            "GILD", "REGN", "VRTX", "ISRG",
        ],
        "max_stocks": 6,
    },
    "Consumer/Cloud/Enterprise": {
        "universe": [
            "AMZN", "MSFT", "GOOGL", "META", "AAPL", "CRM", "SNOW",
            "PLTR", "NET", "SHOP", "UBER", "ABNB", "NFLX",
        ],
        "max_stocks": 6,
    },
}

# ── Constants ────────────────────────────────────────────────────────────────

MAX_WATCHLIST = 30
MAX_PER_SECTOR = 6
MAX_CHANGES_PER_DAY = 5

# Watchlist filters
MIN_MARKET_CAP = 5_000_000_000      # $5B
MIN_AVG_VOLUME = 2_000_000          # 2M shares/day
MIN_OPTIONS_VOLUME = 1_000          # daily contracts
MIN_ANALYST_COVERAGE = 5

# Scoring weights (must sum to 1.0)
WATCHLIST_WEIGHTS = {
    "catalyst_proximity": 0.25,
    "analyst_momentum": 0.20,
    "options_liquidity": 0.15,
    "sector_momentum": 0.15,
    "volatility_profile": 0.10,
    "institutional_flow": 0.10,
    "price_vs_consensus_pt": 0.05,
}

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
