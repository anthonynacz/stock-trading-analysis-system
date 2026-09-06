"""Regression test for /api/outcomes/summary per-signal attribution.

The engine stores signal dicts as {"signal", "points", "detail"}; the
aggregation must key on "signal" (falling back to legacy "name") or the
per-signal table on the Performance page is always empty.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from api.routes import outcomes_summary


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _FakeSession:
    def __init__(self, rows): self._rows = rows
    async def execute(self, _stmt): return _FakeResult(self._rows)


def _outcome(action="BUY", t5=Decimal("2.50"), t20=None):
    return SimpleNamespace(
        action=action,
        return_t1_pct=None,
        return_t5_pct=t5,
        return_t20_pct=t20,
    )


@pytest.mark.asyncio
async def test_signal_key_populates_per_signal_table():
    signals = [{"signal": "T1 Upgrade", "points": 30, "detail": ""}]
    rows = [(_outcome(), signals) for _ in range(3)]  # meets min_signal_n=3 at T+5

    out = await outcomes_summary(days=90, min_signal_n=3, db=_FakeSession(rows), user=None)

    assert out["rows"] == 3
    assert out["signals"] and out["signals"][0]["name"] == "T1 Upgrade"
    assert out["signals"][0]["count"] == 3
    assert out["signals"][0]["t5"] == {"n": 3, "hit_rate": 1.0, "avg_adj_return_pct": 2.5}
    assert out["overall"]["t5"]["hit_rate"] == 1.0


@pytest.mark.asyncio
async def test_legacy_name_key_still_accepted_and_threshold_applies():
    rows = [
        (_outcome(action="SELL", t5=Decimal("-1.00")), [{"name": "Downgrade", "points": -20}]),
        (_outcome(), [{"signal": "Rare", "points": 10}]),
    ]

    out = await outcomes_summary(days=90, min_signal_n=1, db=_FakeSession(rows), user=None)
    names = {s["name"] for s in out["signals"]}
    assert names == {"Downgrade", "Rare"}
    # Bearish signal with negative return counts as a hit.
    dg = next(s for s in out["signals"] if s["name"] == "Downgrade")
    assert dg["t5"]["hit_rate"] == 1.0

    out = await outcomes_summary(days=90, min_signal_n=3, db=_FakeSession(rows), user=None)
    assert out["signals"] == []
