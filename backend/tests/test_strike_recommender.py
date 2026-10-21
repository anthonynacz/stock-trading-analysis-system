"""Strike recommender must see an expiration inside every risk tier's DTE window.

The default chain is the next 4 expirations (usually all < 30 DTE), so the
moderate (14-50) and aggressive (21-75) tiers found nothing. The recommender
now reads the DTE-spread chain, once per call.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from services.options_analyzer import RISK_PROFILES, OptionsAnalyzer
from utils.data_sources import DataSourceClient

SPOT = 100.0


def _contracts() -> list[dict]:
    return [
        {
            "strike": float(k), "bid": 2.0, "ask": 2.2, "lastPrice": 2.1,
            "volume": 100, "openInterest": 500, "impliedVolatility": 0.35,
        }
        for k in range(70, 131, 5)
    ]


class _FakeClient:
    """Serves one chain per mode: only "spread" reaches past 30 DTE."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_options_chain(self, ticker: str, *, mode: str = "default") -> dict:
        self.calls.append((ticker, mode))
        dtes = [3, 10, 17, 24] if mode == "default" else [3, 10, 24, 52, 120]
        today = date.today()
        chains = {
            (today + timedelta(days=d)).isoformat(): {"calls": _contracts(), "puts": _contracts()}
            for d in dtes
        }
        return {"ticker": ticker, "current_price": SPOT, "chains": chains}


def _in_window(rec: dict | None, level: str) -> bool:
    lo, hi = RISK_PROFILES[level]["dte"]
    return rec is not None and lo <= rec["days_to_expiry"] <= hi


def test_recommend_strikes_all_fetches_spread_chain_once_and_fills_every_tier():
    client = _FakeClient()
    result = asyncio.run(OptionsAnalyzer(None, client).recommend_strikes_all("ABC"))

    assert client.calls == [("ABC", "spread")]
    for level in RISK_PROFILES:
        assert _in_window(result[level]["recommended_call"], level), level
        assert _in_window(result[level]["recommended_put"], level), level


def test_recommend_strikes_aggressive_finds_a_contract():
    client = _FakeClient()
    result = asyncio.run(OptionsAnalyzer(None, client).recommend_strikes("ABC", "aggressive"))

    assert client.calls == [("ABC", "spread")]
    assert _in_window(result["recommended_call"], "aggressive")
    assert _in_window(result["recommended_put"], "aggressive")


def test_spread_expirations_cover_every_risk_tier():
    # Weekly Fridays for 3 months, then monthlies: a typical liquid name.
    today = datetime.now(timezone.utc).date()
    first_friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    weeklies = [first_friday + timedelta(weeks=w) for w in range(13)]
    monthlies = [weeklies[-1] + timedelta(weeks=4 * m) for m in range(1, 7)]
    expirations = tuple(d.isoformat() for d in weeklies + monthlies)

    picked = DataSourceClient._spread_expirations(expirations)
    dtes = [(date.fromisoformat(e) - today).days for e in picked]

    for level, profile in RISK_PROFILES.items():
        lo, hi = profile["dte"]
        assert any(lo <= d <= hi for d in dtes), (level, dtes)
