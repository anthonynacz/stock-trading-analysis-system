"""Watchlist rotation rules (services/watchlist_manager.py, config.py).

Runs the real rotation on an in-memory SQLite database through a small
async-over-sync session shim (same approach as tests/radar/test_api_alerts.py).
The data client is a fake: stock data comes from a dict, the FMP-backed
calls are MagicMocks whose call counts tell whether the API was used.
Composite scores are pinned per ticker so ranks are deterministic.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config import SECTORS
from db.models import (
    AnalystRating,
    Base,
    EarningsCalendar,
    OptionsSnapshot,
    PipelineRunLog,
    Position,
    Recommendation,
    Sector,
    UniverseStock,
    User,
    UserPreferences,
    Watchlist,
    WatchlistDailySnapshot,
)
from radar.calendar_nyse import ET, is_trading_day, previous_sessions, session_for
from services import watchlist_manager as wm
from services.unusual_options import get_unusual_options_tickers, last_completed_session_date
from services.watchlist_manager import (
    GRACE_LOCK_REASON,
    ROTATION_MARKER_PHASE,
    WatchlistManager,
    record_snapshot_change,
)

SEC_A, SEC_B = "AI/Semiconductors", "Fintech/Payments"
SECTOR_NAMES = list(SECTORS)


# ── Infrastructure ─────────────────────────────────────────────────────────

class AsyncOverSync:
    """Just enough of AsyncSession over a sync Session."""

    def __init__(self, session: Session) -> None:
        self.s = session

    async def execute(self, stmt, *args, **kwargs):
        return self.s.execute(stmt, *args, **kwargs)

    def add(self, obj) -> None:
        self.s.add(obj)

    async def flush(self) -> None:
        self.s.flush()

    async def commit(self) -> None:
        self.s.commit()

    async def rollback(self) -> None:
        self.s.rollback()


class FakeClient:
    def __init__(self, stocks: dict[str, dict] | None = None) -> None:
        self.stocks = stocks or {}
        self.stock_calls: list[str] = []
        self.get_analyst_ratings = MagicMock(return_value=[])
        self.get_earnings_calendar = MagicMock(return_value=[])

    def get_stock_data(self, ticker: str) -> dict:
        self.stock_calls.append(ticker)
        default = {"ticker": ticker, "market_cap": 50e9, "avg_volume": 10e6,
                   "price": 100.0, "company_name": f"{ticker} Inc"}
        return dict(self.stocks.get(ticker, default))

    def close(self) -> None:
        pass


@pytest.fixture
def db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        User, Sector, UniverseStock, Watchlist, WatchlistDailySnapshot, AnalystRating,
        EarningsCalendar, OptionsSnapshot, Recommendation, Position, PipelineRunLog, UserPreferences,
    )])
    with Session(engine, expire_on_commit=False) as s:
        for name in SECTOR_NAMES:
            s.add(Sector(name=name, max_stocks=12))
        s.commit()
        yield s
    engine.dispose()


@pytest.fixture
def scores(monkeypatch):
    """Pin each ticker's composite score: fill the returned dict."""
    pinned: dict[str, float] = {}
    original = WatchlistManager._score_stock

    def fake(self, ticker, sector_name, stock, earnings_list, ratings, conviction_map):
        row = original(self, ticker, sector_name, stock, earnings_list, ratings, conviction_map)
        row["composite_score"] = pinned.get(ticker, 0.0)
        return row

    monkeypatch.setattr(WatchlistManager, "_score_stock", fake)
    return pinned


def sector_id(db: Session, name: str) -> int:
    return db.execute(select(Sector.id).where(Sector.name == name)).scalar_one()


def trading_days_back(n: int) -> date:
    """An added_date with exactly n trading days in (added_date, today]."""
    d, count = date.today(), 0
    while count < n:
        if is_trading_day(d):
            count += 1
        d -= timedelta(days=1)
    return d


def add_universe(db: Session, tickers: dict[str, str]) -> None:
    for t, sec in tickers.items():
        db.add(UniverseStock(ticker=t, sector_id=sector_id(db, sec), source="SEED", is_active=True))
    db.commit()


def add_member(db: Session, ticker: str, sec: str, *, days: int = 30, **kw) -> Watchlist:
    w = Watchlist(ticker=ticker, sector_id=sector_id(db, sec), added_date=trading_days_back(days),
                  is_active=True, company_name=ticker, **kw)
    db.add(w)
    db.commit()
    return w


def mark_inputs_fresh(db: Session, tickers: list[str]) -> None:
    """Pipeline ratings + earnings ran recently (members fresh) and an earlier
    rotation (yesterday) refreshed *tickers*."""
    now = datetime.now(tz=timezone.utc)
    for phase in ("ratings", "earnings"):
        db.add(PipelineRunLog(phase=phase, status="SUCCESS", started_at=now - timedelta(hours=2)))
    db.add(PipelineRunLog(phase=ROTATION_MARKER_PHASE, status="SUCCESS",
                          started_at=now - timedelta(days=1),
                          meta={"et_date": "2000-01-01", "refreshed": sorted(tickers)}))
    db.commit()


def next_day(db: Session) -> None:
    """Let the next rotation run: today's marker becomes an earlier day's."""
    for m in db.execute(select(PipelineRunLog).where(PipelineRunLog.phase == ROTATION_MARKER_PHASE)).scalars():
        if (m.meta or {}).get("et_date") != "2000-01-01":
            m.meta = {**m.meta, "et_date": "2000-01-02"}
    db.commit()


async def rotate(db: Session, client: FakeClient | None = None) -> dict:
    return await WatchlistManager(AsyncOverSync(db), client or FakeClient()).rotate_watchlist()


def active(db: Session) -> set[str]:
    return set(db.execute(select(Watchlist.ticker).where(Watchlist.is_active.is_(True))).scalars())


def member(db: Session, ticker: str) -> Watchlist:
    return db.execute(select(Watchlist).where(Watchlist.ticker == ticker)
                      .order_by(Watchlist.id.desc())).scalars().first()


# ── 1. Hysteresis ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_candidate_enters_only_within_enter_rank(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "ENTER_RANK", 3)
    names = {f"C{i}": SEC_A for i in range(6)}
    add_universe(db, names)
    scores.update({f"C{i}": 90.0 - i for i in range(6)})
    mark_inputs_fresh(db, list(names))

    result = await rotate(db)

    assert set(result["added_tickers"]) == {"C0", "C1", "C2"}
    assert active(db) == {"C0", "C1", "C2"}


@pytest.mark.asyncio
async def test_candidate_outside_enter_rank_enters_when_it_beats_the_weakest_member_by_margin(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "ENTER_RANK", 1)
    monkeypatch.setattr(wm, "MAX_WATCHLIST", 3)
    add_universe(db, {"M1": SEC_A, "M2": SEC_A, "M3": SEC_A, "C1": SEC_A, "C2": SEC_A})
    for t in ("M1", "M2", "M3"):
        add_member(db, t, SEC_A)
    # C1 ranks 3rd (outside ENTER_RANK) but beats the weakest member M3 by >= 5: one in, one out.
    # C2 beats M3 by less than the margin: stays out.
    scores.update({"M1": 80, "M2": 70, "M3": 30, "C1": 36, "C2": 34})
    mark_inputs_fresh(db, ["C1", "C2"])

    result = await rotate(db)

    assert result["added_tickers"] == ["C1"] and result["removed_tickers"] == ["M3"]
    assert "replaced by C1" in member(db, "M3").exit_reason
    assert active(db) == {"M1", "M2", "C1"}


@pytest.mark.asyncio
async def test_member_exits_after_two_weak_runs_and_a_strong_run_resets_the_count(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "EXIT_RANK", 2)
    monkeypatch.setattr(wm, "ENTER_RANK", 0)          # no additions in this test
    monkeypatch.setattr(wm, "ENTER_MARGIN", 1000.0)
    tickers = {"A1": SEC_A, "A2": SEC_A, "A3": SEC_A, "A4": SEC_A}
    add_universe(db, tickers)
    for t in tickers:
        add_member(db, t, SEC_A)
    scores.update({"A1": 90, "A2": 80, "A3": 50, "A4": 40})   # A3, A4 rank 3 and 4 > EXIT_RANK
    mark_inputs_fresh(db, [])

    first = await rotate(db)
    assert first["removed_tickers"] == []
    assert member(db, "A3").weak_days == 1 and member(db, "A4").weak_days == 1
    assert member(db, "A1").weak_days == 0

    next_day(db)
    scores.update({"A3": 95})                         # A3 strong again, A2 drops to rank 3
    second = await rotate(db)
    assert second["removed_tickers"] == ["A4"]
    assert "weak" in member(db, "A4").exit_reason
    assert member(db, "A3").weak_days == 0
    assert member(db, "A2").weak_days == 1
    assert active(db) == {"A1", "A2", "A3"}


@pytest.mark.asyncio
async def test_member_failing_the_filters_counts_as_weak(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "ENTER_RANK", 0)
    monkeypatch.setattr(wm, "ENTER_MARGIN", 1000.0)
    add_universe(db, {"OK": SEC_A, "THIN": SEC_A})
    add_member(db, "OK", SEC_A)
    add_member(db, "THIN", SEC_A, weak_days=1)
    scores.update({"OK": 60})
    mark_inputs_fresh(db, [])
    client = FakeClient({"THIN": {"ticker": "THIN", "market_cap": 1e9, "avg_volume": 10e6, "price": 5}})

    result = await rotate(db, client)

    assert result["removed_tickers"] == ["THIN"]
    assert member(db, "THIN").weak_days == 2


# ── 2. Minimum stay ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_automatic_exit_before_the_minimum_stay(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "EXIT_RANK", 1)
    monkeypatch.setattr(wm, "ENTER_RANK", 0)
    monkeypatch.setattr(wm, "ENTER_MARGIN", 1000.0)
    add_universe(db, {"TOP": SEC_A, "NEW": SEC_A, "OLD": SEC_A})
    add_member(db, "TOP", SEC_A)
    add_member(db, "NEW", SEC_A, days=wm.MIN_STAY_TRADING_DAYS - 1, weak_days=5)
    add_member(db, "OLD", SEC_A, days=wm.MIN_STAY_TRADING_DAYS, weak_days=5)
    scores.update({"TOP": 90, "NEW": 10, "OLD": 20})
    mark_inputs_fresh(db, [])

    result = await rotate(db)

    assert result["removed_tickers"] == ["OLD"]
    assert "NEW" in active(db)


# ── 3. Caps and gradual shrink ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_over_the_cap_the_watchlist_shrinks_to_60_within_the_daily_budget(db, scores):
    tickers: dict[str, str] = {}
    for i in range(95):
        tickers[f"T{i:02d}"] = SECTOR_NAMES[i % 8]      # 12 per sector, one sector 11
    add_universe(db, tickers)
    for i, (t, sec) in enumerate(tickers.items()):
        add_member(db, t, sec, is_manual=(i in (0, 1, 2)))
        scores[t] = float(i)                           # T00 lowest score
    mark_inputs_fresh(db, [])

    removed_per_day = []
    for _ in range(5):
        result = await rotate(db)
        assert result["additions"] == 0
        assert result["removals"] <= wm.MAX_CHANGES_PER_DAY
        removed_per_day.append(result["removed_tickers"])
        next_day(db)

    assert [len(r) for r in removed_per_day] == [10, 10, 10, 5, 0]
    left = active(db)
    assert len(left) == 60
    # The three manual names (lowest scores) are protected; the next 35 lowest went.
    assert {"T00", "T01", "T02"} <= left
    assert all(f"T{i:02d}" not in left for i in range(3, 38))
    assert all("over watchlist cap" in member(db, f"T{i:02d}").exit_reason for i in range(3, 38))


@pytest.mark.asyncio
async def test_sector_over_its_cap_drops_its_lowest_scores(db, scores):
    names = {f"S{i:02d}": SEC_A for i in range(14)}
    add_universe(db, names)
    for i, t in enumerate(names):
        add_member(db, t, SEC_A)
        scores[t] = 50.0 + i
    mark_inputs_fresh(db, [])

    result = await rotate(db)

    assert sorted(result["removed_tickers"]) == ["S00", "S01"]
    assert all("over sector cap" in member(db, t).exit_reason for t in ("S00", "S01"))


@pytest.mark.asyncio
async def test_at_the_cap_additions_are_one_in_one_out(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "MAX_WATCHLIST", 2)
    add_universe(db, {"M1": SEC_A, "M2": SEC_B, "C1": SEC_A, "C2": SEC_B})
    add_member(db, "M1", SEC_A)
    add_member(db, "M2", SEC_B)
    # C1 ranks 1st in its sector but does not beat M1 by the margin: no free slot, no swap.
    # C2 beats M2 by >= ENTER_MARGIN: swap.
    scores.update({"M1": 60, "C1": 62, "M2": 40, "C2": 50})
    mark_inputs_fresh(db, ["C1", "C2"])

    result = await rotate(db)

    assert result["added_tickers"] == ["C2"] and result["removed_tickers"] == ["M2"]
    assert active(db) == {"M1", "C2"}


# ── 4. Open positions ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_position_is_never_removed_automatically(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "MAX_WATCHLIST", 2)
    add_universe(db, {"HELD": SEC_A, "P2": SEC_A, "P3": SEC_A})
    for t in ("HELD", "P2", "P3"):
        add_member(db, t, SEC_A, weak_days=5)
    db.add(Position(ticker="HELD", position_type="PUT", quantity=1, entry_price=Decimal("2.5"), status="OPEN"))
    db.add(Position(ticker="P3", position_type="CALL", quantity=1, entry_price=Decimal("1"), status="CLOSED"))
    db.commit()
    scores.update({"HELD": 1, "P2": 50, "P3": 2})
    mark_inputs_fresh(db, [])

    result = await rotate(db)

    assert result["removed_tickers"] == ["P3"]
    assert "HELD" in active(db)


@pytest.mark.asyncio
async def test_rotate_out_preview_hard_blocks_an_open_position(db, scores, monkeypatch):
    from api import routes
    import db.connection as connection
    from utils.schemas import RotationPreviewRequest

    add_universe(db, {"HELD": SEC_A, "FREE": SEC_A, "NEXT": SEC_A})
    add_member(db, "HELD", SEC_A)
    add_member(db, "FREE", SEC_A)
    db.add(Position(ticker="HELD", position_type="STOCK", quantity=10, entry_price=Decimal("100"), status="OPEN"))
    db.commit()
    scores.update({"NEXT": 70})
    mark_inputs_fresh(db, ["NEXT"])

    @asynccontextmanager
    async def fake_session():
        yield AsyncOverSync(db)

    monkeypatch.setattr(connection, "async_session", fake_session)
    monkeypatch.setattr(routes, "DataSourceClient", FakeClient)

    resp = await routes.rotate_out_preview(RotationPreviewRequest(tickers=["HELD", "FREE"]),
                                           user=SimpleNamespace(id=1))
    items = {i.ticker: i for i in resp.items}
    assert items["HELD"].gate.blocked is True
    assert any("open position" in r for r in items["HELD"].gate.reasons)
    assert items["FREE"].gate.blocked is False


# ── 5. Once per day + snapshot rows ────────────────────────────────────────

@pytest.mark.asyncio
async def test_second_rotation_the_same_day_is_a_no_op_and_keeps_the_snapshot(db, scores):
    add_universe(db, {"M1": SEC_A, "C1": SEC_A})
    add_member(db, "M1", SEC_A)
    scores.update({"M1": 50, "C1": 60})
    mark_inputs_fresh(db, ["C1"])

    first = await rotate(db)
    assert first["added_tickers"] == ["C1"]
    snap = {(s.ticker, s.status) for s in db.execute(select(WatchlistDailySnapshot)).scalars()}
    assert snap == {("M1", "EXISTING"), ("C1", "NEW_ENTRANT")}
    weak_before = member(db, "M1").weak_days

    scores.update({"C2": 99})
    second = await rotate(db)

    assert second["skipped"] is True
    assert {(s.ticker, s.status) for s in db.execute(select(WatchlistDailySnapshot)).scalars()} == snap
    assert member(db, "M1").weak_days == weak_before
    markers = db.execute(select(PipelineRunLog).where(PipelineRunLog.phase == ROTATION_MARKER_PHASE,
                                                      PipelineRunLog.meta.is_not(None))).scalars().all()
    assert len([m for m in markers if m.meta.get("et_date") not in ("2000-01-01",)]) == 1


@pytest.mark.asyncio
async def test_later_changes_append_their_own_snapshot_rows(db, scores, monkeypatch):
    from services import scheduler

    add_universe(db, {"M1": SEC_A, "M2": SEC_A})
    add_member(db, "M1", SEC_A)
    add_member(db, "M2", SEC_A)
    scores.update({"M1": 50, "M2": 40})
    mark_inputs_fresh(db, [])
    await rotate(db)

    # A manual delete of M2 and a custom-universe injection later the same day.
    shim = AsyncOverSync(db)
    await record_snapshot_change(shim, "M2", "REMOVED", SEC_A)
    db.add(UserPreferences(user_id=1, custom_universe=["ZZZ"]))
    db.commit()
    injected = await scheduler._inject_custom_universe(shim)

    assert injected == {"ZZZ"}
    snap = {s.ticker: s.status for s in db.execute(select(WatchlistDailySnapshot)).scalars()}
    assert snap == {"M1": "EXISTING", "M2": "REMOVED", "ZZZ": "NEW_ENTRANT"}


# ── 6. Grace lock expiry ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grace_lock_expires_after_lock_days_but_user_locks_and_manual_stay(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "ENTER_RANK", 0)
    monkeypatch.setattr(wm, "ENTER_MARGIN", 1000.0)
    monkeypatch.setattr(wm, "EXIT_RANK", 0)            # every member is weak
    names = {"OLDGRACE": SEC_A, "NEWGRACE": SEC_A, "USERLOCK": SEC_A, "MANUAL": SEC_A}
    add_universe(db, names)
    add_member(db, "OLDGRACE", SEC_A, days=wm.LOCK_DAYS, is_locked=True,
               entry_reason=GRACE_LOCK_REASON, weak_days=3)
    add_member(db, "NEWGRACE", SEC_A, days=wm.LOCK_DAYS - 1, is_locked=True,
               entry_reason=GRACE_LOCK_REASON, weak_days=3)
    add_member(db, "USERLOCK", SEC_A, days=60, is_locked=True, entry_reason="composite_score=50.0",
               weak_days=3)
    add_member(db, "MANUAL", SEC_A, days=60, is_manual=True, weak_days=3)
    scores.update({t: 10.0 for t in names})
    mark_inputs_fresh(db, [])

    result = await rotate(db)

    assert result["removed_tickers"] == ["OLDGRACE"]
    assert member(db, "OLDGRACE").is_locked is False
    assert member(db, "NEWGRACE").is_locked is True
    assert active(db) == {"NEWGRACE", "USERLOCK", "MANUAL"}


# ── 7. Toxic flag ──────────────────────────────────────────────────────────

def _bearish_recs(db: Session, ticker: str) -> None:
    for k in range(3):
        db.add(Recommendation(ticker=ticker, recommendation_date=date.today() - timedelta(days=k),
                              action="SELL", conviction_score=Decimal("-40"), rationale="x"))
    db.commit()


@pytest.mark.asyncio
async def test_toxic_names_stay_by_default(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "ENTER_RANK", 0)
    monkeypatch.setattr(wm, "ENTER_MARGIN", 1000.0)
    add_universe(db, {"BEAR": SEC_A})
    add_member(db, "BEAR", SEC_A)
    _bearish_recs(db, "BEAR")
    scores.update({"BEAR": 60})
    mark_inputs_fresh(db, [])

    result = await rotate(db)

    assert wm.DROP_TOXIC is False
    assert result["removed_tickers"] == []


@pytest.mark.asyncio
async def test_toxic_exit_when_enabled_respects_the_minimum_stay(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "DROP_TOXIC", True)
    monkeypatch.setattr(wm, "ENTER_RANK", 0)
    monkeypatch.setattr(wm, "ENTER_MARGIN", 1000.0)
    add_universe(db, {"BEAR": SEC_A, "CUB": SEC_A})
    add_member(db, "BEAR", SEC_A)
    add_member(db, "CUB", SEC_A, days=1)
    _bearish_recs(db, "BEAR")
    _bearish_recs(db, "CUB")
    scores.update({"BEAR": 60, "CUB": 60})
    mark_inputs_fresh(db, [])

    result = await rotate(db)

    assert result["removed_tickers"] == ["BEAR"]
    assert "toxic" in member(db, "BEAR").exit_reason
    assert "CUB" in active(db)


# ── 8. Unusual options from the previous completed session ─────────────────

def _snap(db: Session, ticker: str, ts: datetime, calls: int) -> None:
    db.add(OptionsSnapshot(ticker=ticker, snapshot_time=ts, total_call_volume=calls))


@pytest.mark.asyncio
async def test_unusual_options_compare_end_of_session_snapshots_of_the_prior_session(db):
    now = datetime(2026, 10, 23, 9, 30, tzinfo=timezone.utc)          # 05:30 ET Friday
    ref = last_completed_session_date(now)
    assert ref == date(2026, 10, 22)
    ref_s = session_for(ref)
    eod = lambda s: s.close + timedelta(minutes=15)                    # the 16:15 ET options run
    early = lambda s: s.open + timedelta(minutes=5)                    # the 09:35 ET options run

    for s in previous_sessions(ref, 20):
        _snap(db, "HOT", eod(s), 100)
        _snap(db, "DILUTED", eod(s), 100)
        _snap(db, "DILUTED", early(s), 5)          # partial-day readings must not drag the average down
        _snap(db, "MORNING", eod(s), 100)
    for s in previous_sessions(ref, 3):
        _snap(db, "YOUNG", eod(s), 10)             # too little history
    _snap(db, "HOT", eod(ref_s), 400)
    _snap(db, "DILUTED", eod(ref_s), 200)
    _snap(db, "MORNING", early(ref_s), 900)        # only a 09:35 reading in the prior session
    _snap(db, "MORNING", eod(ref_s), 120)
    _snap(db, "YOUNG", eod(ref_s), 500)
    # Today's 09:35 snapshot (after `now` anyway) is not a completed session.
    _snap(db, "DILUTED", datetime.combine(date(2026, 10, 23), time(9, 35), tzinfo=ET), 10_000)
    db.commit()

    assert await get_unusual_options_tickers(AsyncOverSync(db), now=now) == {"HOT"}

    # Intraday on Friday the prior session is still Thursday.
    later = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)
    assert last_completed_session_date(later) == date(2026, 10, 22)


@pytest.mark.asyncio
async def test_unusual_options_protect_a_member_from_rotation(db, scores, monkeypatch):
    monkeypatch.setattr(wm, "MAX_WATCHLIST", 1)
    add_universe(db, {"FLOW": SEC_A, "QUIET": SEC_A})
    add_member(db, "FLOW", SEC_A)
    add_member(db, "QUIET", SEC_A)
    scores.update({"FLOW": 10, "QUIET": 50})
    mark_inputs_fresh(db, [])

    async def fake_unusual(session, now=None):
        return {"FLOW"}

    monkeypatch.setattr(wm, "get_unusual_options_tickers", fake_unusual)
    result = await rotate(db)

    assert result["removed_tickers"] == ["QUIET"]


# ── 9. ETF exclusion ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_etfs_are_never_scored_added_or_proposed(db, scores):
    add_universe(db, {"SPY": SEC_B, "QQQ": SEC_A, "M1": SEC_A, "C1": SEC_A})
    add_member(db, "M1", SEC_A)
    scores.update({"SPY": 99, "QQQ": 99, "M1": 50, "C1": 40})
    mark_inputs_fresh(db, ["C1"])
    client = FakeClient()

    result = await rotate(db, client)
    assert "SPY" not in client.stock_calls and "QQQ" not in client.stock_calls
    assert result["added_tickers"] == ["C1"]

    proposals = await WatchlistManager(AsyncOverSync(db), client).propose_replacements(["M1"])
    assert all(c["ticker"] not in ("SPY", "QQQ") for c in proposals["M1"])


# ── 10. Stored inputs instead of FMP calls ─────────────────────────────────

@pytest.mark.asyncio
async def test_no_fmp_calls_when_stored_inputs_are_fresh(db, scores):
    add_universe(db, {"M1": SEC_A, "C1": SEC_A, "C2": SEC_A})
    add_member(db, "M1", SEC_A)
    db.add(EarningsCalendar(ticker="C1", earnings_date=date.today() + timedelta(days=5)))
    db.add(AnalystRating(ticker="C2", analyst_firm="Goldman Sachs", rating_type="TIER_CHANGE",
                         published_at=datetime.now(tz=timezone.utc) - timedelta(days=2)))
    db.commit()
    scores.update({"M1": 50, "C1": 40, "C2": 30})
    mark_inputs_fresh(db, ["C1", "C2"])
    client = FakeClient()

    mgr = WatchlistManager(AsyncOverSync(db), client)
    scored = {s["ticker"]: s for s in await mgr.score_universe()}

    assert client.get_analyst_ratings.call_count == 0
    assert client.get_earnings_calendar.call_count == 0
    assert scored["C1"]["catalyst_proximity"] == 100.0 and scored["C1"]["has_catalyst_14d"]
    assert scored["C2"]["has_recent_rating_change"] and not scored["C2"]["has_catalyst_14d"]
    assert mgr.last_refreshed == []


@pytest.mark.asyncio
async def test_stale_inputs_are_fetched_once_after_the_cheap_filter_and_recorded(db, scores):
    add_universe(db, {"M1": SEC_A, "C1": SEC_A, "SMALL": SEC_A, "LEFT": SEC_A})
    add_member(db, "M1", SEC_A)
    left = add_member(db, "LEFT", SEC_A)               # left the watchlist yesterday: still fresh
    left.is_active, left.removed_date = False, date.today() - timedelta(days=1)
    db.commit()
    scores.update({"M1": 50, "C1": 40})
    client = FakeClient({"SMALL": {"ticker": "SMALL", "market_cap": 1e9, "avg_volume": 5e6}})
    # Members are fresh (pipeline ran); C1 was never refreshed; SMALL fails the filter.
    now = datetime.now(tz=timezone.utc)
    for phase in ("ratings", "earnings"):
        db.add(PipelineRunLog(phase=phase, status="SUCCESS", started_at=now - timedelta(hours=1)))
    db.commit()

    await rotate(db, client)

    fetched = {c.args[0] for c in client.get_analyst_ratings.call_args_list}
    assert fetched == {"C1"}
    assert [c.args[0] for c in client.get_earnings_calendar.call_args_list] == [["C1"]]
    marker = db.execute(select(PipelineRunLog).where(
        PipelineRunLog.phase == ROTATION_MARKER_PHASE)).scalars().one()
    assert marker.meta["refreshed"] == ["C1"]

    # The next day C1 is fresh: no API call.
    next_day(db)
    client2 = FakeClient({"SMALL": {"ticker": "SMALL", "market_cap": 1e9, "avg_volume": 5e6}})
    await rotate(db, client2)
    assert client2.get_analyst_ratings.call_count == 0 and client2.get_earnings_calendar.call_count == 0


# ── Upcoming catalysts only list followed names ─────────────────────────────

def test_catalysts_skip_earnings_stored_only_for_rotation_candidates(db):
    """Rotation scoring stores earnings dates of non-watchlist candidates; the Dashboard's catalysts must not
    show them. Names on the watchlist (now or in an earlier stint) and held names stay."""
    from api.routes import get_catalysts

    soon = date.today() + timedelta(days=3)
    add_member(db, "WATCH", SEC_A)
    old = add_member(db, "PAST", SEC_A)
    old.is_active = False
    db.add(Position(ticker="HELD", position_type="CALL", quantity=1, entry_price=Decimal("2"), status="OPEN"))
    db.add(Position(ticker="SOLD", position_type="CALL", quantity=1, entry_price=Decimal("2"), status="CLOSED"))
    for t in ("WATCH", "PAST", "HELD", "SOLD", "CAND"):
        db.add(EarningsCalendar(ticker=t, earnings_date=soon))
    db.commit()
    import asyncio
    events = asyncio.run(get_catalysts(db=AsyncOverSync(db)))
    assert sorted(e["ticker"] if isinstance(e, dict) else e.ticker for e in events) == ["HELD", "PAST", "WATCH"]
