"""Data types shared by the fetch layer, the baseline builder, the engine and the tick.

These are the contract between modules (see radar/docs/github-spec.md section 4). Change them only
together with every caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Bars:
    """5-minute bars for one symbol, ascending by ts, unique, every ts % 300 == 0.

    ts is the UTC epoch second of the bar START. Bars may include extended-hours rows (Yahoo
    reports volume 0 there). Slots with no trades are simply absent; the engine fills them.
    The newest bar can still be in progress: finality is decided by the engine from the clock.
    """
    symbol: str
    ts: np.ndarray          # int64
    o: np.ndarray           # float64
    h: np.ndarray           # float64
    l: np.ndarray           # float64
    c: np.ndarray           # float64
    v: np.ndarray           # int64
    last_trade_ts: int | None = None    # Yahoo's appended off-grid "last trade" row, if present
    last_trade_px: float | None = None
    # True when the newest grid row may still be revised: the last trade is still inside that row, so Yahoo
    # has not yet folded the first post-boundary trade in and closed it. SPEC 12.1.
    provisional_last: bool = False

    def __len__(self) -> int:
        return int(self.ts.shape[0])


@dataclass
class DailyBars:
    """Daily bars for one symbol, ascending. day holds America/New_York session dates."""
    symbol: str
    day: np.ndarray         # datetime64[D]
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray           # split-adjusted close
    v: np.ndarray           # int64


@dataclass
class Quote:
    """One snapshot quote. Percent fields are in percent units (3.2 means +3.2%)."""
    symbol: str
    price: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    day_volume: int | None = None           # consolidated regular-session volume so far
    market_time: int | None = None          # UTC epoch seconds of the last trade
    market_state: str | None = None         # PREPRE | PRE | REGULAR | POST | POSTPOST | CLOSED
    avg_volume_3m: int | None = None
    market_cap: float | None = None
    exchange: str | None = None             # Yahoo exchange code, e.g. NMS, NYQ, ASE, PCX, BTS
    quote_type: str | None = None           # EQUITY | ETF
    name: str | None = None
    sector: str | None = None


@dataclass
class FetchReport:
    """What one fetch call did. status: ok | degraded | down."""
    source: str                             # yahoo | nasdaq | none
    status: str
    requested: int = 0
    ok: int = 0
    failed: list[str] = field(default_factory=list)
    http_429: int = 0
    ms: int = 0
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"source": self.source, "status": self.status, "requested": self.requested, "ok": self.ok,
                "failed": self.failed[:20], "failed_count": len(self.failed), "http_429": self.http_429,
                "ms": self.ms, "notes": self.notes[:5]}
