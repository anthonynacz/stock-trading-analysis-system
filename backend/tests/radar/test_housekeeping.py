"""Radar housekeeping (PORT_SPEC section 6) on SQLite + a tmp backup dir.

Covers: thresholds and the synthetic-volume trigger, median-of-3 interleaved lookups, archive + squeeze,
verify-before-delete, crash at every step (and inside retention) then a converging rerun, idempotent reruns,
3-calendar-month retention (month-end clamping, boundary month), dry-run, frozen tables, manifest rebuild, run
records (including a refused apply), and the query / restore / verify CLI round trip.
Thresholds are scaled down through `policies=` so the tests run in seconds; the logic is the production one.
"""
from __future__ import annotations

import dataclasses
import gzip
import hashlib
import json
import os
import random
import shutil
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, func, insert, select

from db.models import (PipelineRunLog, RadarBackupManifest, RadarEvent, RadarHousekeepingRun, RadarMemberTick,
                       RadarScanLog, RadarTableMetric)
from radar import housekeeping as hk
from radar.store import RADAR_TABLES, RadarStore

UTC = timezone.utc
AS_OF = datetime(2026, 9, 26, 3, 30, tzinfo=UTC)          # Sat 03:30Z, after the 2026-09-25 session
MT, EV, SC = "radar_member_ticks", "radar_events", "radar_scan_log"
TABLES = {MT: RadarMemberTick.__table__, EV: RadarEvent.__table__, SC: RadarScanLog.__table__}
SIGNALS = ("z3", "z6", "zday", "rvol3", "rvolc", "dvwap", "er6", "acc")
NAMES = ["NVDA", "AMD", "TSLA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "SMCI", "PLTR", "COIN", "MSTR"]

SMALL = {
    MT: dataclasses.replace(hk.POLICIES[MT], max_rows=1500),
    EV: dataclasses.replace(hk.POLICIES[EV], max_rows=300),
    SC: dataclasses.replace(hk.POLICIES[SC], max_rows=400),
}


def with_policy(table: str, **kw: object) -> dict[str, hk.TablePolicy]:
    return {**SMALL, table: dataclasses.replace(SMALL[table], **kw)}


# ------------------------------------------------------------------ synthetic rows (PORT_SPEC section 4 shapes)

def weekdays(start: date, days: int) -> Iterator[date]:
    for i in range(days):
        d = start + timedelta(days=i)
        if d.weekday() < 5:
            yield d


def mt_rows(start: date, days: int, *, names: int = 8, ticks: int = 4, seed: int = 1) -> list[dict]:
    rng = random.Random(seed)
    tickers = NAMES[:names] if names <= len(NAMES) else [f"T{i:03d}" for i in range(names)]
    out = []
    for d in weekdays(start, days):
        for k in range(ticks):
            tick = datetime(d.year, d.month, d.day, 14, 0, tzinfo=UTC) + timedelta(minutes=5 * k)
            for name in tickers:
                heating = rng.random() < 0.25
                out.append({
                    "tick": tick, "session_date": d, "slot": 54 + k, "ticker": name,
                    "role": "heating" if heating else "member", "dir": rng.choice(("up", "down")),
                    "state": "heating" if heating else rng.choice(("racing", "cooling")),
                    "price": round(rng.uniform(5, 900), 4), "chg_day_pct": round(rng.uniform(-15, 15), 2),
                    "chg_5m_pct": round(rng.uniform(-3, 3), 2),
                    "move_since_entry_pct": None if heating else round(rng.uniform(-8, 8), 2),
                    "vol_5m": rng.randint(10_000, 5_000_000), "intensity": round(rng.uniform(0, 100), 1),
                    "signals": {s: round(rng.uniform(-4, 6), 3) for s in SIGNALS}})
    return out


def ev_rows(start: date, days: int, seed: int = 3) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for d in weekdays(start, days):
        t0 = datetime(d.year, d.month, d.day, 14, 0, tzinfo=UTC)
        for etype, off in (("ENTER", 0), ("EXIT", 45)):
            ts = t0 + timedelta(minutes=off)
            enter = etype == "ENTER"
            out.append({
                "id": f"{ts:%Y%m%dT%H%MZ}-NVDA-{etype}-1", "ts": ts, "session_date": d, "slot": 54,
                "ticker": "NVDA", "type": etype, "dir": "up", "price": round(rng.uniform(100, 200), 4),
                "intensity": round(rng.uniform(40, 100), 1), "reason": "ENTRY" if enter else "FADE",
                # literal U+2028: JSONL must be split on "\n" only (SPEC 12.4)
                "detail": "+2.9σ vs market in 15 min volume 3.4× normal (synthetic)",
                "episode": 1, "held_min": None if enter else 45,
                "move_since_entry_pct": None if enter else 1.25, "late": False,
                "signals": {s: round(rng.uniform(-4, 6), 3) for s in SIGNALS}, "params_version": "radar-sm-1"})
    return out


def sc_rows(start: date, days: int, *, write_ms: float = 40.0, per_day: int = 3) -> list[dict]:
    out = []
    for d in weekdays(start, days):
        for k in range(per_day):
            tick = datetime(d.year, d.month, d.day, 14, 0, 50, tzinfo=UTC) + timedelta(minutes=5 * k)
            row = {"v": 1, "tick": hk.iso(tick), "run_id": "local", "session": d.isoformat(), "phase": "regular",
                   "status": "ok", "ms": {"fetch_bars": 900, "compute": 120, "write": write_ms + k, "total": 1500},
                   "errors": []}
            out.append({"tick": tick, "run_id": "local", "written_at": tick + timedelta(seconds=40),
                        "session_date": d, "phase": "regular", "status": "ok", "row": row})
    return out


def gen_key(table: str, row: dict) -> str:
    if table == EV:
        return row["id"]
    return f"{hk.iso(row['tick'])}|{row['ticker'] if table == MT else row['run_id']}"


def gen_ts(table: str, row: dict) -> datetime:
    return row["ts"] if table == EV else row["tick"]


# ------------------------------------------------------------------ environment

@dataclasses.dataclass
class Env:
    url: str
    store: RadarStore
    backup: str
    policies: dict

    def run(self, **kw: object) -> dict:
        kw.setdefault("policies", self.policies)
        kw.setdefault("repeats", 1)
        return hk.run(store=self.store, backup_dir=self.backup, retention_months=3, **kw)

    def insert(self, table: str, rows: list[dict]) -> None:
        if rows:
            with self.store.engine.begin() as c:
                c.execute(insert(TABLES[table]), rows)

    def hot(self, table: str) -> list[dict]:
        t = TABLES[table]
        with self.store.engine.connect() as c:
            return [hk.db_row(t, hk.POLICIES[table], r) for r in c.execute(select(t)).mappings()]

    def backups(self, table: str) -> list[dict]:
        rows: list[dict] = []
        folder = os.path.join(self.backup, table)
        for fn in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            if fn.endswith(".jsonl.gz"):
                with open(os.path.join(folder, fn), "rb") as f:
                    rows += [json.loads(x) for x in hk.jsonl_lines(gzip.decompress(f.read()).decode("utf-8"))]
        return rows

    def manifest(self) -> dict[str, dict]:
        with self.store.engine.connect() as c:
            return {f"{r.table_name}/{r.month}": {k: hk.json_value(v) for k, v in r._mapping.items() if k != "id"}
                    for r in c.execute(select(RadarBackupManifest.__table__))}

    def count(self, table: object) -> int:
        with self.store.engine.connect() as c:
            return int(c.execute(select(func.count()).select_from(table)).scalar())

    def verify(self) -> dict:
        return hk.verify(store=self.store, backup_dir=self.backup, policies=self.policies)


def make_env(url: str, backup: str, policies: dict | None = None) -> Env:
    return Env(url, RadarStore(url), backup, policies or SMALL)


def _fixed_time_query(conn, stmt) -> float:
    """Run the lookup (so its SQL is exercised) but report 1 ms: a loaded test machine must not flip the latency
    rule and make runs that should be identical diverge. The latency rule has its own test."""
    conn.execute(stmt).fetchall()
    return 1.0


@pytest.fixture
def env(radar_db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setattr(hk, "_timed_query", _fixed_time_query)
    engine = create_engine(radar_db_url)
    PipelineRunLog.__table__.create(engine)     # housekeeping logs to pipeline_run_log like the other phases
    engine.dispose()
    backup = tmp_path / "backups"
    backup.mkdir()
    e = make_env(radar_db_url, str(backup))
    try:
        yield e
    finally:
        e.store.close()


@pytest.fixture
def history(env: Env) -> tuple[Env, list[dict]]:
    """member_ticks 2026-05-04..08-14 archived once at 2026-08-15 (DEGRADED on rows), then 08-17..09-25
    appended; events and scan_log 05-04..09-25 stay small."""
    first = mt_rows(date(2026, 5, 4), 103)
    env.insert(MT, first)
    env.insert(EV, ev_rows(date(2026, 5, 4), 145))
    env.insert(SC, sc_rows(date(2026, 5, 4), 145))
    rep = env.run(now=datetime(2026, 8, 15, 3, 30, tzinfo=UTC))
    assert rep["tables"][MT]["status"] == "DEGRADED" and rep["status"] == "ok", rep
    assert set(env.manifest()) == {f"{MT}/2026-05", f"{MT}/2026-06", f"{MT}/2026-07"}
    second = mt_rows(date(2026, 8, 17), 40, seed=2)
    env.insert(MT, second)
    return env, first + second


def digest(root: str) -> dict[str, str]:
    out = {}
    for d, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(d, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root).replace("\\", "/")] = hashlib.sha256(f.read()).hexdigest()
    return out


def db_dump(env: Env) -> dict[str, list[str]]:
    out = {}
    with env.store.engine.connect() as c:
        for t in (*RADAR_TABLES, PipelineRunLog.__table__):
            out[t.name] = sorted(json.dumps({k: str(v) for k, v in r._mapping.items()}, sort_keys=True)
                                 for r in c.execute(select(t)))
    return out


def state(env: Env) -> dict:
    """Everything housekeeping owns, minus bookkeeping that legitimately differs between equivalent runs."""
    fields = ("rows", "bytes", "raw_bytes", "sha256", "content_sha256", "min_ts", "max_ts", "path")
    return {"files": digest(env.backup),
            "hot": {t: sorted(map(hk.canon, env.hot(t))) for t in TABLES},
            "manifest": {k: {f: e[f] for f in fields} for k, e in env.manifest().items()}}


def keys(table: str, rows: list[dict]) -> list[str]:
    return [hk.row_key(hk.POLICIES[table], r) for r in rows]


# ------------------------------------------------------------------ policy, primitives

def test_policies_match_port_spec_section_6():
    assert {n: (p.max_rows, p.max_bytes, p.max_lookup_ms, p.hot_days) for n, p in hk.POLICIES.items()} == {
        MT: (2_000_000, 1024 ** 3, 150, 14),
        EV: (200_000, 256 * 1024 ** 2, 100, 45),
        SC: (300_000, 512 * 1024 ** 2, 100, 14),
    }
    assert {n: (p.ts, p.key) for n, p in hk.POLICIES.items()} == {
        MT: ("tick", ("tick", "ticker")), EV: ("ts", ("id",)), SC: ("tick", ("tick", "run_id"))}
    assert hk.resolve_table("events") == EV and hk.resolve_table(MT) == MT
    with pytest.raises(ValueError):
        hk.resolve_table("alerts_log")


@pytest.mark.parametrize("as_of,expected", [
    (date(2026, 9, 27), datetime(2026, 6, 27, tzinfo=UTC)),
    (date(2026, 5, 31), datetime(2026, 2, 28, tzinfo=UTC)),     # month-end clamping
    (date(2024, 5, 31), datetime(2024, 2, 29, tzinfo=UTC)),     # leap year
    (date(2026, 1, 15), datetime(2025, 10, 15, tzinfo=UTC)),    # year boundary
    (date(2026, 11, 30), datetime(2026, 8, 30, tzinfo=UTC)),
])
def test_retention_cutoff_calendar_months(as_of, expected):
    assert hk.retention_cutoff(as_of) == expected


def test_write_p95_reads_ms_write_of_the_newest_rows():
    rows = [{"ms": {"write": 100 + i}} for i in range(50)]
    assert hk.write_p95_ms(rows) == 100 + round(0.95 * 49)
    assert hk.write_p95_ms(rows[:9]) is None                          # fewer than 10 timed rows
    assert hk.write_p95_ms([json.dumps(r) for r in rows]) == hk.write_p95_ms(rows)
    assert hk.write_p95_ms([{"ms": {"write": True}}] * 20 + [{"ms": {}}] * 20) is None


def test_invalid_arguments_are_rejected(env):
    with pytest.raises(ValueError):
        env.run(mode="apply-now")
    with pytest.raises(ValueError):
        env.run(trigger="cron")
    with pytest.raises(ValueError):
        env.run(force_tables=["alerts_log"])


# ------------------------------------------------------------------ thresholds and volume

def test_small_tables_untouched_and_the_run_is_recorded(env):
    rows = mt_rows(date(2026, 9, 14), 12)
    env.insert(MT, rows)
    before = sorted(map(hk.canon, env.hot(MT)))
    rep = env.run(now=AS_OF, trigger="schedule")
    assert rep["status"] == "ok" and rep["tables"][MT]["status"] in ("OK", "WARN")
    assert sorted(map(hk.canon, env.hot(MT))) == before
    assert digest(env.backup) == {} and env.manifest() == {}
    with env.store.engine.connect() as c:
        metrics = c.execute(select(RadarTableMetric.__table__)).mappings().all()
        runs = c.execute(select(RadarHousekeepingRun.__table__)).mappings().all()
        plog = c.execute(select(PipelineRunLog.__table__)).mappings().all()
    assert sorted(m["table_name"] for m in metrics) == sorted(TABLES)
    m = next(x for x in metrics if x["table_name"] == MT)
    assert (m["rows"], m["action"], m["rows_after"], m["archived_rows"]) == (len(rows), "none", len(rows), 0)
    assert set(m["lookup_ms"]) == set(hk.POLICIES[MT].lookups) and m["run_id"] == rep["run_id"]
    assert len(runs) == 1 and runs[0]["status"] == "ok" and runs[0]["trigger"] == "schedule"
    assert runs[0]["finished_at"] is not None and runs[0]["report"]["run_id"] == rep["run_id"]
    assert len(plog) == 1 and plog[0]["phase"] == "radar_housekeeping" and plog[0]["status"] == "SUCCESS"
    assert plog[0]["meta"]["run_id"] == rep["run_id"] and plog[0]["duration_ms"] is not None


def test_row_threshold_triggers_archive_and_squeeze(env):
    pol = with_policy(MT, max_rows=500)
    rows = mt_rows(date(2026, 8, 3), 26) + mt_rows(date(2026, 9, 14), 12, seed=7)   # 640 + 320 rows
    env.insert(MT, rows)
    rep = env.run(now=AS_OF, policies=pol)
    t = rep["tables"][MT]
    assert t["status"] == "DEGRADED" and [r.split()[0] for r in t["reasons"]] == ["rows"], t
    spec = pol[MT]
    hot, bak = env.hot(MT), env.backups(MT)
    assert len(hot) <= spec.target_ratio * spec.max_rows                     # squeezed below the target
    assert set(keys(MT, hot)).isdisjoint(keys(MT, bak))
    assert sorted(keys(MT, hot) + keys(MT, bak)) == sorted(gen_key(MT, r) for r in rows)
    floor = AS_OF - timedelta(hours=spec.min_hot_hours)
    assert all(hk.parse_ts(r["tick"]) < floor for r in bak)                # the last session stays hot
    assert set(env.manifest()) == {f"{MT}/2026-08", f"{MT}/2026-09"}
    assert sum(e["rows"] for e in env.manifest().values()) == len(bak) == t["archive_rows"]
    assert t["rows_after"] == len(hot) and t["action"] == "archive"
    assert env.verify()["problems"] == []
    assert rep["tables"][EV]["status"] == "OK" and rep["tables"][EV]["archive_rows"] == 0


def test_degradation_trigger_fires_on_synthetic_volume(env):
    """Five weeks of nightly runs while member_ticks grows by one session (32 rows) a day: the verdict follows
    the volume (OK, then WARN at 80%, DEGRADED exactly above max_rows), archiving brings the table back under
    target, the cycle repeats, and no row is ever lost or duplicated."""
    pol = with_policy(MT, max_rows=320)
    spec = pol[MT]
    generated: list[dict] = []
    seen: list[str] = []
    day = date(2026, 7, 27)
    while day <= date(2026, 8, 28):
        if day.weekday() < 5:
            rows = mt_rows(day, 1, seed=day.toordinal())
            env.insert(MT, rows)
            generated += rows
            now = datetime(day.year, day.month, day.day, 3, 30, tzinfo=UTC) + timedelta(days=1)
            rep = env.run(now=now, policies=pol)
            t = rep["tables"][MT]
            assert (t["rows"] > spec.max_rows) == (t["status"] == "DEGRADED"), (day, t)
            if t["status"] != "DEGRADED":
                assert (t["rows"] >= hk.WARN_RATIO * spec.max_rows) == (t["status"] == "WARN"), (day, t)
            else:
                assert t["archive_rows"] > 0 and t["rows_after"] <= spec.target_ratio * spec.max_rows
            seen.append(t["status"])
            cutoff = hk.retention_cutoff(now.date())
            hot, bak = keys(MT, env.hot(MT)), keys(MT, env.backups(MT))
            assert len(hot) + len(bak) == len(set(hot) | set(bak))          # hot and backups disjoint
            assert len(bak) == len(set(bak))                                  # no duplicate in the backups
            assert set(hot) | set(bak) == {gen_key(MT, r) for r in generated if r["tick"] >= cutoff}
        day += timedelta(days=1)
    assert seen[0] == "OK" and "WARN" in seen and seen.count("DEGRADED") >= 2
    assert seen.index("WARN") < seen.index("DEGRADED")
    assert env.verify()["problems"] == []


def test_bytes_rule_fires_on_volume(env):
    rows = mt_rows(date(2026, 9, 1), 25, names=12)
    env.insert(MT, rows)
    probe = env.run(now=AS_OF, mode="dry-run")
    size = probe["tables"][MT]["bytes"]
    if size is None:
        pytest.skip("this SQLite build has no dbstat, so table bytes are unknown")
    assert probe["tables"][MT]["status"] == "OK"
    rep = env.run(now=AS_OF, mode="dry-run", policies=with_policy(MT, max_rows=10 ** 9, max_bytes=size // 2))
    t = rep["tables"][MT]
    assert t["status"] == "DEGRADED" and [r.split()[0] for r in t["reasons"]] == ["bytes"]
    assert rep["planned"]["rows_archive"][MT] > 0
    warn = env.run(now=AS_OF, mode="dry-run", policies=with_policy(MT, max_rows=10 ** 9, max_bytes=int(size * 1.1)))
    assert warn["tables"][MT]["status"] == "WARN"


def test_latency_rule_counts_only_from_20_percent_of_max_rows(env, monkeypatch):
    monkeypatch.setattr(hk, "_timed_query", lambda conn, stmt: 500.0)       # every lookup "takes" 500 ms
    env.insert(MT, mt_rows(date(2026, 9, 14), 12))                           # 320 rows
    small = env.run(now=AS_OF, mode="dry-run", policies=with_policy(MT, max_rows=2000))   # 16% of max_rows
    assert small["tables"][MT]["status"] == "OK" and small["tables"][MT]["lookup_max_ms"] == 500.0
    big = env.run(now=AS_OF, mode="dry-run", policies=with_policy(MT, max_rows=1600))     # 20% of max_rows
    assert big["tables"][MT]["status"] == "DEGRADED"
    assert [r.split()[0] for r in big["tables"][MT]["reasons"]] == ["lookup_ms"]


def test_lookup_latency_is_the_median_of_three_interleaved_cold_runs(env, monkeypatch):
    env.insert(MT, mt_rows(date(2026, 9, 14), 2))
    env.insert(SC, sc_rows(date(2026, 9, 14), 2))
    calls: list[str] = []
    fake = iter([30.0, 1.0, 2.0] * 50)      # each lookup's three samples are spread over the three rounds

    def timed(conn, stmt):
        calls.append(str(stmt))
        conn.execute(stmt).fetchall()
        return next(fake)
    monkeypatch.setattr(hk, "_timed_query", timed)
    rep = env.run(now=AS_OF, mode="dry-run", repeats=3)
    n = len(hk.POLICIES[MT].lookups)
    lk = rep["tables"][MT]["lookup_ms"]
    assert set(lk) == set(hk.POLICIES[MT].lookups) and len(calls) >= 3 * n
    assert calls[:n] == calls[n:2 * n] == calls[2 * n:3 * n]                 # round robin, not back to back
    assert all(calls[i] != calls[i + 1] for i in range(3 * n - 1))
    samples = [30.0, 1.0, 2.0] * 50
    for i, name in enumerate(hk.POLICIES[MT].lookups):
        assert lk[name] == sorted(samples[i::n][:3])[1]                      # median of its 3 runs
    assert rep["tables"][MT]["lookup_max_ms"] == max(lk.values())
    assert set(rep["tables"][SC]["lookup_ms"]) == set(hk.POLICIES[SC].lookups)


def test_write_p95_rule_charges_only_big_per_tick_tables(env):
    env.insert(MT, mt_rows(date(2026, 9, 1), 25, names=12))
    env.insert(SC, sc_rows(date(2026, 9, 21), 5, write_ms=900, per_day=4))    # 20 slow tick writes
    probe = env.run(now=AS_OF, mode="dry-run")
    size = probe["tables"][MT]["bytes"]
    if size is None:
        pytest.skip("this SQLite build has no dbstat, so table bytes are unknown")
    assert probe["tables"][MT]["write_p95_ms"] >= 900
    rep = env.run(now=AS_OF, mode="dry-run", policies=with_policy(MT, max_rows=10 ** 9, max_bytes=int(size * 1.6)))
    assert [r.split()[0] for r in rep["tables"][MT]["reasons"]] == ["write_p95_ms"]
    assert not any(r.startswith("write_p95_ms") for r in rep["tables"][SC]["reasons"])  # small table


# ------------------------------------------------------------------ verify before delete, crashes, idempotency

def test_nothing_is_deleted_before_the_partition_is_verified(env, monkeypatch):
    rows = mt_rows(date(2026, 8, 3), 20)
    env.insert(MT, rows)
    seen = {}

    def spy(step):
        if step == "partitions_written":      # files are final and verified, Postgres still has every row
            seen["files"] = env.verify()["problems"], len(env.backups(MT)), len(env.hot(MT))
        if step == "rows_deleted":
            seen["deleted"] = len(env.hot(MT))
    monkeypatch.setattr(hk, "checkpoint", spy)
    env.run(now=AS_OF, force_tables=["member_ticks"])
    problems, n_backup, n_hot = seen["files"]
    assert n_backup == len(rows) and n_hot == len(rows)
    assert all("file not in the manifest" in p for p in problems)   # the manifest is written after the delete
    assert seen["deleted"] == 0


def test_a_partition_that_fails_verification_deletes_nothing(env, monkeypatch):
    rows = mt_rows(date(2026, 8, 3), 20)
    env.insert(MT, rows)
    before = sorted(map(hk.canon, env.hot(MT)))
    real = hk.gz_deterministic
    monkeypatch.setattr(hk, "gz_deterministic", lambda content: real(content)[:-12])   # a torn write
    with pytest.raises(hk.PartitionCorrupt):
        env.run(now=AS_OF, force_tables=["member_ticks"], run_id="hk-torn")
    assert sorted(map(hk.canon, env.hot(MT))) == before
    assert digest(env.backup) == {} and env.manifest() == {}
    with env.store.engine.connect() as c:
        run_row = c.execute(select(RadarHousekeepingRun.__table__)).mappings().one()
        plog = c.execute(select(PipelineRunLog.__table__)).mappings().one()
    assert run_row["status"] == "failed" and "PartitionCorrupt" in run_row["report"]["error"]
    assert plog["status"] == "FAILED"


@pytest.mark.parametrize("step", hk.STEPS)
def test_crash_at_each_step_then_rerun_converges(history, tmp_path, monkeypatch, step):
    env, all_rows = history
    clean_db, clean_backup = tmp_path / "clean.db", tmp_path / "clean_backups"
    shutil.copyfile(env.url.removeprefix("sqlite:///"), clean_db)
    shutil.copytree(env.backup, clean_backup)
    clean = make_env(f"sqlite:///{clean_db.as_posix()}", str(clean_backup))
    try:
        clean.run(now=AS_OF, force_tables=["member_ticks"])
        expected = state(clean)
    finally:
        clean.store.close()

    def boom(s):
        if s == step:
            raise hk.SimulatedCrash(s)
    monkeypatch.setattr(hk, "checkpoint", boom)
    with pytest.raises(hk.SimulatedCrash):
        env.run(now=AS_OF, force_tables=["member_ticks"])
    cutoff = hk.retention_cutoff(AS_OF.date())
    alive = {gen_key(MT, r) for r in all_rows if r["tick"] >= cutoff}
    assert alive <= set(keys(MT, env.hot(MT))) | set(keys(MT, env.backups(MT)))   # nothing unexpired lost
    monkeypatch.setattr(hk, "checkpoint", lambda s: None)
    rep = env.run(now=AS_OF, force_tables=["member_ticks"])
    assert state(env) == expected
    assert env.verify()["problems"] == [] and rep["status"] == "ok"
    bak = keys(MT, env.backups(MT))
    assert len(bak) == len(set(bak))


def test_crash_inside_retention_before_the_manifest_save_converges(history, tmp_path, monkeypatch):
    """Retention removed the expired month and rewrote the boundary month, then died before the manifest
    save: the rerun drops the stale entry, rebuilds the boundary one from its file, and ends where a run
    that never crashed ends."""
    env, all_rows = history
    clean_db, clean_backup = tmp_path / "clean.db", tmp_path / "clean_backups"
    shutil.copyfile(env.url.removeprefix("sqlite:///"), clean_db)
    shutil.copytree(env.backup, clean_backup)
    clean = make_env(f"sqlite:///{clean_db.as_posix()}", str(clean_backup))
    try:
        clean.run(now=AS_OF)
        expected = state(clean)
    finally:
        clean.store.close()

    real = hk.save_manifest

    def dies_on_retention(conn, db, manifest, upserts, deletes):
        if deletes:                       # only the retention save removes entries in this scenario
            raise hk.SimulatedCrash("before the retention manifest save")
        real(conn, db, manifest, upserts, deletes)
    monkeypatch.setattr(hk, "save_manifest", dies_on_retention)
    with pytest.raises(hk.SimulatedCrash):
        env.run(now=AS_OF)
    assert not os.path.exists(os.path.join(env.backup, MT, "2026-05.jsonl.gz"))
    assert f"{MT}/2026-05" in env.manifest()                                  # stale entry left behind
    assert env.verify()["problems"]
    monkeypatch.setattr(hk, "save_manifest", real)
    rep = env.run(now=AS_OF)
    assert any("expired partition already deleted" in r for r in rep["manifest_repairs"])
    assert any(r.startswith(f"{MT}/2026-06: manifest entry rebuilt") for r in rep["manifest_repairs"])
    assert rep["status"] == "ok" and state(env) == expected and env.verify()["problems"] == []
    cutoff = hk.retention_cutoff(AS_OF.date())
    alive = {gen_key(MT, r) for r in all_rows if r["tick"] >= cutoff}
    assert set(keys(MT, env.hot(MT))) | set(keys(MT, env.backups(MT))) == alive


def test_rerun_is_idempotent(history):
    env, _ = history
    env.run(now=AS_OF, force_tables=["member_ticks", "events"])
    files, hot, manifest = digest(env.backup), {t: sorted(map(hk.canon, env.hot(t))) for t in TABLES}, env.manifest()
    for force in (["member_ticks", "events"], []):
        rep = env.run(now=AS_OF, force_tables=force)
        assert digest(env.backup) == files and env.manifest() == manifest     # not even updated_at / writes
        assert {t: sorted(map(hk.canon, env.hot(t))) for t in TABLES} == hot
        assert rep["partitions_written"] == rep["partitions_deleted"] == rep["partitions_filtered"] == []
        assert all(t["archive_rows"] == t["heal_rows"] == t["purge_rows"] == 0 for t in rep["tables"].values())


def test_same_month_archived_twice_has_no_duplicates(env):
    a = mt_rows(date(2026, 9, 1), 4)
    env.insert(MT, a)
    env.run(now=AS_OF, force_tables=["member_ticks"])
    n1 = len(env.backups(MT))
    assert n1 == len(a)
    b = mt_rows(date(2026, 9, 7), 3, seed=4)
    env.insert(MT, a[:50] + b)          # 50 rows already backed up come back (overlap), plus new rows of the month
    rep = env.run(now=AS_OF, force_tables=["member_ticks"])
    assert rep["partitions_written"] == [f"{MT}/2026-09"]
    bak = keys(MT, env.backups(MT))
    assert len(bak) == len(set(bak)) == len(a) + len(b)
    assert env.hot(MT) == [] and env.verify()["problems"] == []


def test_heal_removes_hot_rows_already_identical_in_a_partition(env):
    """An interrupted run (or a restore pushed back by hand) leaves old rows both hot and backed up: a later
    run that does not archive drops the hot copies, but only the identical ones."""
    rows = mt_rows(date(2026, 8, 3), 5)
    env.insert(MT, rows)
    env.run(now=AS_OF, force_tables=["member_ticks"])
    changed = dict(rows[1], price=1.0)
    env.insert(MT, [rows[0], changed])
    rep = env.run(now=AS_OF)
    t = rep["tables"][MT]
    assert t["status"] == "OK" and t["heal_rows"] == 1 and t["archive_rows"] == 0 and t["action"] == "heal"
    assert [r["price"] for r in env.hot(MT)] == [1.0]          # the different copy stays hot
    assert len(env.backups(MT)) == len(rows)


# ------------------------------------------------------------------ retention

def test_retention_three_calendar_months_with_boundary_month(history):
    env, all_rows = history
    june_before = env.manifest()[f"{MT}/2026-06"]
    july_before = env.manifest()[f"{MT}/2026-07"]
    with env.store.engine.begin() as c:     # ops rows past retention go too
        c.execute(insert(RadarTableMetric.__table__).values(run_id="hk-old", table_name=MT, status="OK",
                                                            measured_at=datetime(2026, 5, 1, tzinfo=UTC)))
        c.execute(insert(RadarHousekeepingRun.__table__).values(run_id="hk-old", status="ok", mode="apply",
                                                                started_at=datetime(2026, 5, 1, tzinfo=UTC)))
    cutoff = hk.retention_cutoff(AS_OF.date())
    assert cutoff == datetime(2026, 6, 26, tzinfo=UTC)
    rep = env.run(now=AS_OF)
    m = env.manifest()
    assert f"{MT}/2026-05" not in m and not os.path.exists(os.path.join(env.backup, MT, "2026-05.jsonl.gz"))
    assert rep["partitions_deleted"] == [f"{MT}/2026-05"] and rep["partitions_filtered"] == [f"{MT}/2026-06"]
    june = m[f"{MT}/2026-06"]
    assert june["min_ts"] >= "2026-06-26" and 0 < june["rows"] < june_before["rows"]
    assert m[f"{MT}/2026-07"]["sha256"] == july_before["sha256"]            # untouched
    for table in TABLES:
        ts = hk.POLICIES[table].ts
        for r in env.backups(table) + env.hot(table):
            assert hk.parse_ts(r[ts]) >= cutoff, (table, r[ts])
    alive = {gen_key(MT, r) for r in all_rows if r["tick"] >= cutoff}
    assert set(keys(MT, env.hot(MT))) | set(keys(MT, env.backups(MT))) == alive
    assert rep["tables"][EV]["purge_rows"] > 0 and rep["tables"][SC]["purge_rows"] > 0
    assert rep["retention_only"] == {"radar_table_metrics": 1, "radar_housekeeping_runs": 1}
    with env.store.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(RadarHousekeepingRun.__table__)
                         .where(RadarHousekeepingRun.__table__.c.run_id == "hk-old")).scalar() == 0
    assert env.verify()["problems"] == []


def test_retention_clamps_to_the_month_end_in_a_run(env):
    """Run on 2026-05-31: the cutoff is 2026-02-28 00:00Z (no Feb 31), applied to hot rows and backups."""
    def at(y, m, d, hh=14, mm=0, ss=0):
        return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)
    base = ev_rows(date(2026, 3, 2), 1)[0]
    rows = [dict(base, id=f"{ts:%Y%m%dT%H%M%S}-NVDA-ENTER-1", ts=ts, session_date=ts.date())
            for ts in (at(2026, 2, 27, 23, 59, 59), at(2026, 2, 28, 0, 0, 0), at(2026, 3, 2))]
    env.insert(EV, rows)
    env.run(now=datetime(2026, 5, 1, 3, 30, tzinfo=UTC), force_tables=["events"])     # all three to backups
    assert len(env.backups(EV)) == 3 and env.hot(EV) == []
    rep = env.run(now=datetime(2026, 5, 31, 3, 30, tzinfo=UTC))
    assert rep["retention_cutoff"] == "2026-02-28T00:00:00Z"
    assert sorted(r["ts"] for r in env.backups(EV)) == ["2026-02-28T00:00:00Z", "2026-03-02T14:00:00Z"]
    assert rep["partitions_filtered"] == [f"{EV}/2026-02"]


# ------------------------------------------------------------------ dry-run, frozen tables, repairs

def test_dry_run_writes_nothing(history):
    env, _ = history
    before_db, before_files = db_dump(env), digest(env.backup)
    rep = env.run(now=AS_OF, mode="dry-run", force_tables=["member_ticks"])
    assert db_dump(env) == before_db and digest(env.backup) == before_files
    assert rep["mode"] == "dry-run" and rep["planned"]["partitions_delete"] == [f"{MT}/2026-05"]
    assert rep["planned"]["partitions_filter"] == [f"{MT}/2026-06"]
    assert rep["planned"]["partitions_write"] == [f"{MT}/2026-08", f"{MT}/2026-09"]
    assert rep["tables"][MT]["archive_rows"] > 0 and rep["partitions_written"] == []
    assert f"| {MT} |" in hk.render_markdown(rep)
    rec = env.run(now=AS_OF, mode="dry-run", record_dry_run=True, run_id="hk-plan")   # the API's dry-run
    after = db_dump(env)
    assert {t: v for t, v in after.items() if t != hk.RUNS_TABLE} == \
           {t: v for t, v in before_db.items() if t != hk.RUNS_TABLE}
    with env.store.engine.connect() as c:
        row = c.execute(select(RadarHousekeepingRun.__table__)
                        .where(RadarHousekeepingRun.__table__.c.run_id == "hk-plan")).mappings().one()
    assert row["mode"] == "dry-run" and row["report"]["planned"] == rec["planned"]



def test_corrupt_partition_freezes_only_its_table(history):
    env, _ = history
    p = os.path.join(env.backup, MT, "2026-07.jsonl.gz")
    with open(p, "r+b") as f:
        f.seek(40)
        f.write(b"\x00garbage\x00")
    hot_before = sorted(map(hk.canon, env.hot(MT)))
    rep = env.run(now=AS_OF, force_tables=["member_ticks", "events"])
    t = rep["tables"][MT]
    assert t["status"] == "ERROR" and t["action"] == "frozen" and MT in rep["partition_errors"]
    assert rep["status"] == "error"
    assert sorted(map(hk.canon, env.hot(MT))) == hot_before                  # not archived
    assert os.path.exists(os.path.join(env.backup, MT, "2026-05.jsonl.gz"))  # not even its expired month
    assert rep["tables"][EV]["status"] != "ERROR" and rep["tables"][EV]["archive_rows"] > 0
    assert rep["tables"][SC]["purge_rows"] > 0
    with env.store.engine.connect() as c:
        plog = c.execute(select(PipelineRunLog.__table__).order_by(PipelineRunLog.__table__.c.id.desc())).first()
    assert plog.status == "FAILED" and "2026-07" in plog.error_message
    assert any("2026-07" in p for p in env.verify()["problems"])


def test_missing_live_partition_and_stray_files_freeze_their_table(history):
    env, _ = history
    os.remove(os.path.join(env.backup, MT, "2026-07.jsonl.gz"))
    os.makedirs(os.path.join(env.backup, SC), exist_ok=True)
    with open(os.path.join(env.backup, SC, "notes.jsonl.gz"), "wb") as f:
        f.write(gzip.compress(b"x"))
    rep = env.run(now=AS_OF)
    assert "file is missing" in rep["partition_errors"][MT]
    assert rep["tables"][SC]["status"] == "ERROR" and rep["tables"][EV]["status"] != "ERROR"
    assert any("notes.jsonl.gz" in p for p in env.verify()["problems"])


def test_manifest_is_rebuilt_from_the_files(history):
    env, _ = history
    fields = ("rows", "sha256", "content_sha256", "min_ts", "max_ts", "path")
    before = {k: {f: e[f] for f in fields} for k, e in env.manifest().items()}
    with env.store.engine.begin() as c:
        c.execute(delete(RadarBackupManifest.__table__))
    rep = env.run(now=datetime(2026, 8, 15, 4, 0, tzinfo=UTC))   # same cutoff as the archive run: no retention
    assert len(rep["manifest_repairs"]) == len(before) and not rep["partition_errors"]
    assert {k: {f: e[f] for f in fields} for k, e in env.manifest().items()} == before
    assert env.verify()["problems"] == []


def test_concurrent_run_is_reported_busy(env):
    assert hk._LOCAL_LOCK.acquire(blocking=False)
    try:
        rep = env.run(now=AS_OF, run_id="hk-busy")
    finally:
        hk._LOCAL_LOCK.release()
    assert rep["status"] == "busy"
    with env.store.engine.connect() as c:
        row = c.execute(select(RadarHousekeepingRun.__table__)).mappings().one()
    assert (row["run_id"], row["status"]) == ("hk-busy", "busy")


def test_apply_refuses_an_unmounted_backup_dir(env, tmp_path, capsys):
    rows = mt_rows(date(2026, 8, 3), 20)
    env.insert(MT, rows)
    missing = str(tmp_path / "not-mounted")
    with pytest.raises(hk.HousekeepingError, match="radar_backups"):
        hk.run(store=env.store, backup_dir=missing, retention_months=3, now=AS_OF, force_tables=["member_ticks"],
               run_id="hk-unmounted")
    assert not os.path.exists(missing) and len(env.hot(MT)) == len(rows)
    with env.store.engine.connect() as c:      # the refusal is recorded, so the health page and /schedule show it
        run_row = c.execute(select(RadarHousekeepingRun.__table__)).mappings().one()
        plog = c.execute(select(PipelineRunLog.__table__)).mappings().one()
        assert c.execute(select(func.count()).select_from(RadarTableMetric.__table__)).scalar() == 0
    assert (run_row["run_id"], run_row["status"]) == ("hk-unmounted", "failed")
    assert "HousekeepingError" in run_row["report"]["error"]
    assert plog["status"] == "FAILED" and "radar_backups" in plog["error_message"]
    assert plog["meta"]["trigger"] == "manual"
    assert hk.main(["run", "--as-of", "2026-09-26T03:30:00Z", "--backup-dir", missing, "--db-url", env.url]) == 2
    assert "radar_backups" in capsys.readouterr().err and not os.path.exists(missing)
    rep = hk.run(store=env.store, backup_dir=missing, retention_months=3, now=AS_OF, mode="dry-run")
    assert rep["status"] == "ok" and not os.path.exists(missing)


def test_vacuum_is_planned_after_a_big_delete(env, monkeypatch):
    monkeypatch.setattr(hk, "VACUUM_MIN_ROWS", 100)
    env.insert(MT, mt_rows(date(2026, 8, 3), 20))
    rep = env.run(now=AS_OF, force_tables=["member_ticks"])
    assert rep["tables"][MT]["vacuum"] == "skipped (sqlite)"     # VACUUM (ANALYZE) runs on Postgres only
    assert rep["tables"][EV]["vacuum"] is None


def test_default_store_and_config_paths(env, monkeypatch):
    """The worker calls run(mode="apply", trigger="schedule"): the store comes from radar.store's default URL
    and the backup dir and retention from radar.config."""
    import radar.config
    import radar.store

    monkeypatch.setattr(radar.store, "default_url", lambda: env.url)
    monkeypatch.setattr(radar.config, "BACKUP_DIR", env.backup)
    env.insert(MT, mt_rows(date(2026, 8, 3), 10))
    rep = hk.run(mode="apply", trigger="schedule", now=AS_OF, force_tables=["member_ticks"])
    assert rep["status"] == "ok" and rep["backup_dir"] == env.backup
    assert rep["retention"].startswith("3 calendar months") and env.hot(MT) == []


# ------------------------------------------------------------------ query / restore / verify

def test_query_restore_verify_cli_round_trip(history, tmp_path, capsys):
    env, all_rows = history
    common = ["--backup-dir", env.backup, "--db-url", env.url]
    assert hk.main(["run", "--as-of", "2026-09-26T03:30:00Z", *common]) == 0
    assert "| radar_member_ticks |" in capsys.readouterr().out

    # a hot copy of a backed-up row wins over the backup
    july_row = next(r for r in all_rows if r["ticker"] == "NVDA" and r["tick"].month == 7)
    env.insert(MT, [dict(july_row, price=12.34)])
    assert hk.main(["query", "--table", "member_ticks", "--from", "2026-07-01T00:00:00Z",
                    "--to", "2026-08-10T00:00:00Z", "--where", "ticker=NVDA", *common]) == 0
    got = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    start, end = datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 8, 10, tzinfo=UTC)
    want = sorted(gen_key(MT, r) for r in all_rows if r["ticker"] == "NVDA" and start <= r["tick"] < end)
    assert keys(MT, got) == want                                  # backups (July) + hot (August), no duplicate
    assert next(r for r in got if r["tick"] == hk.iso(july_row["tick"]))["price"] == 12.34

    out = str(tmp_path / "mt-2026-07.jsonl")
    assert hk.main(["restore", "--table", "member_ticks", "--month", "2026-07", "--out", out, *common]) == 0
    info = json.loads(capsys.readouterr().out)
    with open(out, encoding="utf-8") as f:
        restored = [json.loads(x) for x in hk.jsonl_lines(f.read())]
    july = [r for r in all_rows if r["tick"].month == 7]
    assert info["rows"] == len(restored) == len(july) and info["hot_rows"] == 1
    assert sorted(keys(MT, restored)) == sorted(gen_key(MT, r) for r in july)

    assert hk.main(["verify", *common]) == 0
    capsys.readouterr()
    with open(os.path.join(env.backup, MT, "2026-07.jsonl.gz"), "ab") as f:
        f.write(b"tail")
    assert hk.main(["verify", *common]) == 2
    assert "sha256 mismatch" in capsys.readouterr().out
    assert hk.main(["query", "--table", "member_ticks", "--from", "2026-07-01T00:00:00Z",
                    "--to", "2026-07-02T00:00:00Z", *common]) == 2          # never a partial answer
    assert "2026-07" in capsys.readouterr().err
    assert hk.main(["restore", "--table", "member_ticks", "--month", "2026-07", "--out", out, *common]) == 2
    assert hk.main(["run", "--as-of", "2026-09-26T03:30:00Z", *common]) == 2     # frozen: exit code 2


def test_query_where_accepts_json_and_python_spellings(history):
    env, _ = history
    env.run(now=AS_OF, force_tables=["events"])
    start, end = datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)

    def q(**where):
        return hk.query("events", start, end, where, store=env.store, backup_dir=env.backup)
    rows = q()
    enters = [r for r in rows if r["held_min"] is None]
    assert rows and enters and len(enters) < len(rows) and all(r["late"] is False for r in rows)
    assert len(env.backups(EV)) > 0 and env.hot(EV)                      # spans both places
    assert q(late="false") == q(late="False") == rows
    assert q(held_min="null") == q(held_min="None") == enters
    assert q(late="true") == q(late="0") == []
    assert q(ticker="NVDA") == q(ticker='"NVDA"') == rows
    assert all(" " in r["detail"] for r in rows)                      # U+2028 survives the round trip
