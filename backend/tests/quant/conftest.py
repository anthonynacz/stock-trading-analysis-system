"""Shared fixtures for the quant tests: a SQLite QuantStore and a synthetic OHLC generator with known volatility."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from db.models import (
    Base,
    EarningsCalendar,
    OptionsSnapshot,
    PipelineRunLog,
    RadarEvent,
    RadarOptionMetric,
    Sector,
    UniverseStock,
    User,
    Watchlist,
)
from quant.store import QUANT_TABLES, QuantStore

OTHER_TABLES = tuple(m.__table__ for m in (User, Sector, UniverseStock, Watchlist, EarningsCalendar, OptionsSnapshot,
                                           PipelineRunLog, RadarEvent, RadarOptionMetric))


@pytest.fixture
def store(tmp_path):
    s = QuantStore(f"sqlite:///{tmp_path / 'quant.db'}")
    Base.metadata.create_all(s.engine, tables=list(OTHER_TABLES + QUANT_TABLES))
    yield s
    s.close()


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def synth_ohlc(sigmas: np.ndarray, rng: np.random.Generator, *, steps: int = 78, on_share: float = 0.2,
               s0: float = 100.0) -> dict[str, np.ndarray]:
    """Daily bars from a driftless log random walk: day t has total daily variance sigmas[t]^2, a share
    `on_share` of it overnight, the rest spread over `steps` intraday steps (high / low from the path)."""
    n = len(sigmas)
    o, h, l, c = (np.empty(n) for _ in range(4))
    prev = s0
    for t in range(n):
        sd_on = sigmas[t] * np.sqrt(on_share)
        sd_step = sigmas[t] * np.sqrt((1 - on_share) / steps)
        op = prev * np.exp(rng.normal(0, sd_on)) if t else prev
        path = op * np.exp(np.cumsum(rng.normal(0, sd_step, steps)))
        o[t], c[t] = op, path[-1]
        h[t], l[t] = max(op, path.max()), min(op, path.min())
        prev = c[t]
    return {"open": o, "high": h, "low": l, "close": c, "adj_close": c.copy()}


def stochastic_vol(n: int, rng: np.random.Generator, *, mean_annual: float = 0.30, phi: float = 0.985,
                   eta: float = 0.08) -> np.ndarray:
    """Daily sigma path: log sigma is AR(1) around log(mean_annual / sqrt(252))."""
    mu = np.log(mean_annual / np.sqrt(252))
    x = np.empty(n)
    x[0] = mu
    for t in range(1, n):
        x[t] = mu + phi * (x[t - 1] - mu) + rng.normal(0, eta)
    return np.exp(x)


def bars_rows(ticker: str, dates: list[date], ohlc: dict[str, np.ndarray], source: str = "yahoo") -> list[dict]:
    return [{"ticker": ticker, "bar_date": d, "open": float(ohlc["open"][i]), "high": float(ohlc["high"][i]),
             "low": float(ohlc["low"][i]), "close": float(ohlc["close"][i]), "adj_close": float(ohlc["adj_close"][i]),
             "volume": 1_000_000, "source": source} for i, d in enumerate(dates)]


def panel_rows(dates: list[date], ohlc: dict[str, np.ndarray]) -> dict[str, list]:
    return {"date": [d.isoformat() for d in dates], **{k: list(v) for k, v in ohlc.items()}}
