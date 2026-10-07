"""The scan universe (SPEC 4.3): S&P 500 + Nasdaq-100 + reference ETFs, plus same-day dynamic adds."""
from __future__ import annotations

import csv
from pathlib import Path

from radar.config import REFERENCE_SYMBOLS, US_EXCHANGES
from radar.types import Quote

UNIVERSE_CSV = Path(__file__).parent / "data" / "universe.csv"
COLUMNS = ("symbol", "name", "sector", "industry", "in_sp500", "in_ndx", "is_etf")
_TEXT = COLUMNS[:4]
_FLAGS = COLUMNS[4:]

# Yahoo (Morningstar) and Nasdaq sector names mapped onto the GICS names universe.csv uses, so the
# sector cap and the sector banners see one vocabulary for universe names and dynamic adds.
GICS_SECTORS = {
    "Technology": "Information Technology",
    "Financial Services": "Financials",
    "Finance": "Financials",
    "Healthcare": "Health Care",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Basic Materials": "Materials",
    "Telecommunications": "Communication Services",
}


def gics_sector(name: str | None) -> str | None:
    """GICS sector name for a Yahoo or Nasdaq sector name; names already in GICS pass through."""
    if not name:
        return None
    return GICS_SECTORS.get(name, name)


def load_universe(path: Path = UNIVERSE_CSV) -> list[dict]:
    """Rows of universe.csv with typed flags; empty text cells become None."""
    with path.open(encoding="utf-8", newline="") as f:
        return [_typed(raw) for raw in csv.DictReader(f)]


def _typed(raw: dict[str, str]) -> dict:
    row: dict = {k: (raw[k] or "").strip() or None for k in _TEXT}
    row.update({k: (raw[k] or "").strip() == "True" for k in _FLAGS})
    return row


def scan_symbols() -> list[str]:
    """Every non-ETF universe symbol, then the reference ETFs."""
    stocks = [r["symbol"] for r in load_universe() if not r["is_etf"]]
    return stocks + [s for s in REFERENCE_SYMBOLS if s not in stocks]


def dynamic_candidates(movers: list[Quote], known: set[str], params: dict) -> list[Quote]:
    """US-listed equities from the movers screens worth adding for the session, largest |change| first.

    `params` is the full PARAMS dict. The caller enforces dynamic.max_adds_per_day, because only it
    knows how many names were already added today.
    """
    rules = params["dynamic"]
    if not rules["enabled"]:
        return []
    seen = set(known)
    picked: list[Quote] = []
    for q in movers:
        if q.symbol not in seen and _qualifies(q, rules):
            seen.add(q.symbol)
            picked.append(q)
    return sorted(picked, key=lambda q: -abs(q.change_pct))


def _qualifies(q: Quote, rules: dict) -> bool:
    return (q.exchange in US_EXCHANGES and q.quote_type == "EQUITY"
            and q.market_cap is not None and q.market_cap >= rules["market_cap_min"]
            and q.price is not None and q.price >= rules["price_min"]
            and q.change_pct is not None and abs(q.change_pct) >= rules["abs_change_pct_min"])
