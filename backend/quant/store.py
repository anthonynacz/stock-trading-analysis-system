"""Storage for the quant layer: the only module that writes the quant_* tables.

Synchronous on purpose, like radar/store.py: the jobs run as plain subprocesses (python -m quant.run) and the
API reads through it in the threadpool. Production uses psycopg2 with a URL derived from settings.DATABASE_URL;
tests pass a SQLite URL. Every write is an upsert on the table's primary key, so a re-run is idempotent.
Other app tables (universe, watchlist, earnings, radar, options snapshots, run log) are only read here.
"""
from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.sql.schema import Table

from db.models import (
    EarningsCalendar,
    OptionsSnapshot,
    PipelineRunLog,
    QuantChainSnapshot,
    QuantDailyBar,
    QuantModelRun,
    QuantVolForecast,
    RadarEvent,
    RadarOptionMetric,
    Sector,
    UniverseStock,
    Watchlist,
)
from radar.store import sync_url

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 10
CHUNK = 1000

_BARS: Table = QuantDailyBar.__table__
_CHAINS: Table = QuantChainSnapshot.__table__
_RUNS: Table = QuantModelRun.__table__
_FORECASTS: Table = QuantVolForecast.__table__
QUANT_TABLES: tuple[Table, ...] = (_BARS, _CHAINS, _RUNS, _FORECASTS)

FORECAST_FIELDS = ("ticker", "as_of_date", "model_version", "horizon", "price", "forecast_vol", "realized_vol_21d",
                   "implied_vol_30d", "iv_source", "iv_detail", "iv_to_forecast", "ratio_pctl", "reading",
                   "reading_basis", "earnings_in_horizon", "earnings_date", "flags", "model_run_id")


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"not JSON-serialisable: {type(obj).__name__}")


def _json_dumps(doc: Any) -> str:
    # allow_nan=False: Postgres rejects NaN in json; a NaN must be cleaned (None) before it gets here.
    return json.dumps(doc, separators=(",", ":"), allow_nan=False, default=_json_default)


def clean(v: Any) -> Any:
    """NaN / inf -> None, recursively (numpy scalars to Python)."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class QuantStore:
    def __init__(self, url: str | None = None) -> None:
        if url is None:
            from config import settings     # lazy: tests pass a URL
            url = settings.DATABASE_URL
        url = sync_url(url)
        backend = make_url(url).get_backend_name()
        if backend not in ("postgresql", "sqlite"):
            raise ValueError(f"QuantStore supports postgresql and sqlite URLs, not {backend!r}")
        kwargs: dict[str, Any] = {"pool_pre_ping": True, "json_serializer": _json_dumps}
        if backend == "postgresql":
            kwargs.update(pool_size=2, max_overflow=2, pool_recycle=1800,
                          connect_args={"connect_timeout": CONNECT_TIMEOUT_S, "application_name": "vela-quant"})
        self.engine: Engine = create_engine(url, **kwargs)
        self._insert: Callable[..., Any] = postgresql.insert if backend == "postgresql" else sqlite.insert

    def close(self) -> None:
        self.engine.dispose()

    # ------------------------------------------------------------ helpers

    def _upsert(self, conn, table: Table, rows: list[dict], keys: tuple[str, ...]) -> int:
        if not rows:
            return 0
        stmt = self._insert(table)
        cols = [c for c in rows[0] if c not in keys]
        stmt = stmt.on_conflict_do_update(index_elements=list(keys),
                                          set_={c: stmt.excluded[c] for c in cols})
        for i in range(0, len(rows), CHUNK):
            conn.execute(stmt, rows[i:i + CHUNK])
        return len(rows)

    # ------------------------------------------------------------ bars

    def bar_last_dates(self) -> dict[str, date]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_BARS.c.ticker, func.max(_BARS.c.bar_date)).group_by(_BARS.c.ticker)).all()
        return {t: d for t, d in rows}

    def load_bar_window(self, ticker: str, since: date) -> dict[date, dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_BARS).where(_BARS.c.ticker == ticker, _BARS.c.bar_date >= since)).mappings()
            return {r["bar_date"]: dict(r) for r in rows}

    def upsert_bars(self, rows: list[dict], replace_ticker: str | None = None) -> int:
        """Upsert bar rows; with replace_ticker, that ticker's stored history is deleted first (same
        transaction): a split or dividend re-scaled the vendor's whole history."""
        now = datetime.now(timezone.utc)
        rows = [{**r, "updated_at": now} for r in rows]
        with self.engine.begin() as conn:
            if replace_ticker:
                conn.execute(delete(_BARS).where(_BARS.c.ticker == replace_ticker))
            return self._upsert(conn, _BARS, rows, ("ticker", "bar_date"))

    def load_bars(self, tickers: Iterable[str] | None = None, since: date | None = None) -> dict[str, dict[str, list]]:
        """{ticker: {"date", "open", "high", "low", "close", "adj_close", "volume"}} ascending."""
        q = select(_BARS.c.ticker, _BARS.c.bar_date, _BARS.c.open, _BARS.c.high, _BARS.c.low, _BARS.c.close,
                   _BARS.c.adj_close, _BARS.c.volume).order_by(_BARS.c.ticker, _BARS.c.bar_date)
        if tickers is not None:
            q = q.where(_BARS.c.ticker.in_(list(tickers)))
        if since is not None:
            q = q.where(_BARS.c.bar_date >= since)
        out: dict[str, dict[str, list]] = {}
        with self.engine.connect() as conn:
            for t, d, o, h, l, c, a, v in conn.execute(q):
                r = out.setdefault(t, {k: [] for k in ("date", "open", "high", "low", "close", "adj_close", "volume")})
                r["date"].append(d.isoformat())
                r["open"].append(o)
                r["high"].append(h)
                r["low"].append(l)
                r["close"].append(c)
                r["adj_close"].append(a if a is not None else float("nan"))
                r["volume"].append(v)
        return out

    # ------------------------------------------------------------ chain snapshots

    def upsert_chain_rows(self, rows: list[dict]) -> int:
        with self.engine.begin() as conn:
            return self._upsert(conn, _CHAINS, [clean(r) for r in rows], ("ticker", "snapshot_date", "expiry"))

    def load_chain_summaries(self, snapshot_date: date, tickers: Iterable[str] | None = None) -> dict[str, list[dict]]:
        """Per ticker, that day's snapshot rows without the contracts (expiry, snapshot_at, atm_iv, ...)."""
        c = _CHAINS.c
        q = select(c.ticker, c.expiry, c.snapshot_at, c.underlying_price, c.dte, c.atm_iv, c.forward, c.iv_method,
                   c.earnings_date).where(c.snapshot_date == snapshot_date)
        if tickers is not None:
            q = q.where(c.ticker.in_(list(tickers)))
        out: dict[str, list[dict]] = defaultdict(list)
        with self.engine.connect() as conn:
            for r in conn.execute(q).mappings():
                d = dict(r)
                d["snapshot_at"] = _aware(d["snapshot_at"])
                out[d["ticker"]].append(d)
        return dict(out)

    # ------------------------------------------------------------ model registry

    def record_model_run(self, *, kind: str, model: str, version: str, params: dict | None = None,
                         window_start: date | None = None, window_end: date | None = None, n_obs: int | None = None,
                         n_tickers: int | None = None, metrics: dict | None = None) -> int:
        with self.engine.begin() as conn:
            res = conn.execute(_RUNS.insert().values(
                kind=kind, model=model, version=version, params=clean(params), window_start=window_start,
                window_end=window_end, n_obs=n_obs, n_tickers=n_tickers, metrics=clean(metrics),
                created_at=datetime.now(timezone.utc)))
            return int(res.inserted_primary_key[0])

    def latest_model_run(self, kind: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(_RUNS).where(_RUNS.c.kind == kind)
                             .order_by(_RUNS.c.created_at.desc(), _RUNS.c.id.desc()).limit(1)).mappings().first()
        return dict(r) if r else None

    def count_model_runs(self) -> dict[str, int]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_RUNS.c.kind, func.count()).group_by(_RUNS.c.kind)).all()
        return {k: int(n) for k, n in rows}

    # ------------------------------------------------------------ forecasts

    def upsert_forecasts(self, rows: list[dict]) -> int:
        now = datetime.now(timezone.utc)
        rows = [{**{k: clean(r.get(k)) for k in FORECAST_FIELDS}, "updated_at": now} for r in rows]
        with self.engine.begin() as conn:
            return self._upsert(conn, _FORECASTS, rows, ("ticker", "as_of_date", "model_version", "horizon"))

    def own_ratio_history(self, tickers: Iterable[str], since: date, before: date,
                          model_version: str) -> dict[str, list[float]]:
        """Earlier earnings-free IV / forecast ratios per ticker (the own-history reference)."""
        f = _FORECASTS.c
        q = select(f.ticker, f.iv_to_forecast).where(
            f.ticker.in_(list(tickers)), f.as_of_date >= since, f.as_of_date < before,
            f.model_version == model_version, f.earnings_in_horizon.is_(False), f.iv_to_forecast.is_not(None))
        out: dict[str, list[float]] = defaultdict(list)
        with self.engine.connect() as conn:
            for t, r in conn.execute(q):
                out[t].append(float(r))
        return dict(out)

    def latest_forecasts(self) -> tuple[date | None, str | None, list[dict]]:
        """(as_of, model_version, rows) of the most recent forecast date."""
        f = _FORECASTS.c
        with self.engine.connect() as conn:
            last = conn.execute(select(f.as_of_date, f.model_version).order_by(f.as_of_date.desc(),
                                                                                f.updated_at.desc()).limit(1)).first()
            if last is None:
                return None, None, []
            as_of, version = last
            rows = conn.execute(select(_FORECASTS).where(f.as_of_date == as_of, f.model_version == version)
                                .order_by(f.ticker)).mappings().all()
        return as_of, version, [dict(r) for r in rows]

    def forecast_history(self, ticker: str, since: date | None = None) -> list[dict]:
        f = _FORECASTS.c
        q = select(_FORECASTS).where(f.ticker == ticker).order_by(f.as_of_date, f.model_version)
        if since is not None:
            q = q.where(f.as_of_date >= since)
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(q).mappings()]

    # ------------------------------------------------------------ reads of other app tables

    def universe_sectors(self) -> dict[str, str]:
        """{ticker: sector name} of active universe stocks."""
        q = (select(UniverseStock.ticker, Sector.name).join(Sector, Sector.id == UniverseStock.sector_id)
             .where(UniverseStock.is_active.is_(True)))
        with self.engine.connect() as conn:
            return {t: s for t, s in conn.execute(q)}

    def watchlist_sectors(self) -> dict[str, str | None]:
        q = (select(Watchlist.ticker, Sector.name).outerjoin(Sector, Sector.id == Watchlist.sector_id)
             .where(Watchlist.is_active.is_(True)))
        with self.engine.connect() as conn:
            return {t: s for t, s in conn.execute(q)}

    def radar_recent_tickers(self, since: datetime) -> list[str]:
        q = select(RadarEvent.ticker).where(RadarEvent.ts >= since).distinct()
        with self.engine.connect() as conn:
            return sorted(t for (t,) in conn.execute(q))

    def earnings_between(self, tickers: Iterable[str], first: date, last: date) -> dict[str, list[tuple[date, str | None]]]:
        q = select(EarningsCalendar.ticker, EarningsCalendar.earnings_date, EarningsCalendar.earnings_time).where(
            EarningsCalendar.ticker.in_(list(tickers)), EarningsCalendar.earnings_date >= first,
            EarningsCalendar.earnings_date <= last)
        out: dict[str, list] = defaultdict(list)
        with self.engine.connect() as conn:
            for t, d, tm in conn.execute(q):
                out[t].append((d, tm))
        return dict(out)

    def radar_iv30(self, tickers: Iterable[str], since: datetime) -> dict[str, dict]:
        """{ticker: {"iv", "as_of", "expiry"}} from radar_option_metrics (iv_30d_pct, percent)."""
        q = select(RadarOptionMetric.ticker, RadarOptionMetric.as_of, RadarOptionMetric.metrics).where(
            RadarOptionMetric.ticker.in_(list(tickers)), RadarOptionMetric.as_of >= since)
        out = {}
        with self.engine.connect() as conn:
            for t, as_of, m in conn.execute(q):
                iv = (m or {}).get("iv_30d_pct") if isinstance(m, dict) else None
                if isinstance(iv, (int, float)) and math.isfinite(iv) and iv > 0:
                    out[t] = {"iv": float(iv) / 100.0, "as_of": _aware(as_of).isoformat(),
                              "expiry": (m or {}).get("iv_30d_expiry")}
        return out

    def options_snapshot_iv(self, tickers: Iterable[str], since: datetime) -> dict[str, dict]:
        """{ticker: {"iv", "as_of"}}: the latest options_snapshots.atm_iv since `since` (nearest expiry, not 30d)."""
        q = (select(OptionsSnapshot.ticker, OptionsSnapshot.snapshot_time, OptionsSnapshot.atm_iv)
             .where(OptionsSnapshot.ticker.in_(list(tickers)), OptionsSnapshot.snapshot_time >= since,
                    OptionsSnapshot.atm_iv.is_not(None))
             .order_by(OptionsSnapshot.snapshot_time))
        out = {}
        with self.engine.connect() as conn:
            for t, ts, iv in conn.execute(q):
                if iv is not None and float(iv) > 0:
                    out[t] = {"iv": float(iv), "as_of": _aware(ts).isoformat()}
        return out

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        b, c, f = _BARS.c, _CHAINS.c, _FORECASTS.c
        with self.engine.connect() as conn:
            bars = conn.execute(select(func.count(func.distinct(b.ticker)), func.min(b.bar_date),
                                       func.max(b.bar_date), func.count())).first()
            chains = conn.execute(select(func.count(func.distinct(c.ticker)), func.count(func.distinct(c.snapshot_date)),
                                         func.min(c.snapshot_date), func.max(c.snapshot_date), func.count())).first()
            fc = conn.execute(select(func.count(func.distinct(f.ticker)), func.count(func.distinct(f.as_of_date)),
                                     func.min(f.as_of_date), func.max(f.as_of_date))).first()
            last_day = chains[3]
            chain_last_tickers = 0
            if last_day is not None:
                chain_last_tickers = conn.execute(select(func.count(func.distinct(c.ticker)))
                                                  .where(c.snapshot_date == last_day)).scalar() or 0
            jobs = {}
            for job in ("bars", "chains", "forecast"):
                r = conn.execute(select(PipelineRunLog).where(PipelineRunLog.phase == f"quant_{job}")
                                 .order_by(PipelineRunLog.started_at.desc()).limit(1)).mappings().first()
                jobs[job] = dict(r) if r else None
        return {
            "bars": {"tickers": int(bars[0] or 0), "first_date": bars[1], "last_date": bars[2], "rows": int(bars[3] or 0)},
            "chains": {"tickers": int(chains[0] or 0), "days_collected": int(chains[1] or 0), "first_date": chains[2],
                       "last_date": chains[3], "rows": int(chains[4] or 0), "tickers_last_day": int(chain_last_tickers)},
            "forecasts": {"tickers": int(fc[0] or 0), "days": int(fc[1] or 0), "first_date": fc[2], "last_date": fc[3]},
            "jobs": jobs,
        }
