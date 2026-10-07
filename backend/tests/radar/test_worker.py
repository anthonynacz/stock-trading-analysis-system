"""radar.worker on a fake clock: scheduling, tick isolation, failure handling, alerts, housekeeping and the
run log (PORT_SPEC section 5; github-spec 4.9 and 12). The store is the real RadarStore on SQLite."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar import calendar_nyse as cal
from radar import worker as worker_mod
from radar.config import RUNTIME
from radar.tick import CONTEXT_ENV, HEALTH_REF, RUN_ID_ENV, health_ref, iso, parse_tick_id, scan_log_row
from tests.radar.test_tick import check_scan_row, check_state, event, scan_rows, seed

BACKEND = Path(__file__).resolve().parents[2]
MON = cal.session_for(date(2026, 9, 28))          # EDT
EST_DAY = cal.session_for(date(2026, 11, 2))
HALF = cal.session_for(date(2026, 11, 27))
DAY = "2026-09-28"


def at(value: str) -> float:
    return float(parse_tick_id(value))


class Clock:
    def __init__(self, start: str):
        self.t = at(start)
        self.naps: list[float] = []
        self.hooks: list = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        assert s >= 0
        self.naps.append(s)
        self.t += s
        for hook in self.hooks:
            hook(self.t)


def parse_cmd(cmd: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tick-id")
    for flag in ("--warmup", "--final", "--ignore-calendar"):
        ap.add_argument(flag, action="store_true")
    return ap.parse_args(cmd[1:])


class FakeTick:
    """Stands in for the tick subprocess: records what the worker passed, then commits like the real tick
    (the worker's context into the engine document, a scan_log row under the worker's run id, the events).

    results: tick id -> status; "timeout"/"error" commit nothing, "committed-timeout" commits and then
    reports a timeout (killed between its commit and its exit)."""

    def __init__(self, store, clock: Clock, *, durations: dict | None = None, results: dict | None = None,
                 events: dict | None = None, hk: dict | None = None, on_run=None):
        self.store, self.clock = store, clock
        self.durations, self.results, self.events, self.hk = durations or {}, results or {}, events or {}, hk or {}
        self.on_run = on_run
        self.runs: list[SimpleNamespace] = []

    def __call__(self, cmd: list[str], timeout_s: float, env: dict[str, str], stop=None) -> dict:
        a = parse_cmd(cmd)
        ctx = json.loads(env[CONTEXT_ENV])
        engine = self.store.load_engine_doc() or {"schema": 1}
        self.runs.append(SimpleNamespace(tick_id=a.tick_id, warmup=a.warmup, final=a.final, at=self.clock(),
                                         loop=ctx["loop"], ops=ctx["ops"], env=env, timeout=timeout_s, stop=stop,
                                         stop_requested=stop() if stop else None,
                                         run_id=env[RUN_ID_ENV], source_health=engine.get("source_health"), cmd=cmd))
        if self.on_run:
            self.on_run(a, env)
        self.clock.t += self.durations.get(a.tick_id, 20)
        status = self.results.get(a.tick_id, "ok")
        if status in ("timeout", "error"):
            return {"status": status, "message": "boom", "duration_ms": 1234}
        evs = [] if a.warmup else self.events.get(a.tick_id, [])
        engine.update(schema=1, loop=ctx["loop"], ops={**(engine.get("ops") or {}), **ctx["ops"]})
        state = {"schema": 1, "tick_id": a.tick_id, "status": "ok" if status == "committed-timeout" else status,
                 "members": [], "heating": [], "health": {"ticks_today": ctx["loop"]["ticks_today"]}}
        row = scan_log_row(a.tick_id, iso(self.clock()), run_id=env[RUN_ID_ENV], session=DAY, phase="regular",
                           status=state["status"])
        self.store.commit_tick(state=state, engine_doc=engine, member_rows=[], events=evs, scan_row=row)
        if status == "committed-timeout":
            return {"status": "timeout", "message": "boom", "duration_ms": 270_000}
        return {"status": status, "message": "", "members": 0, "duration_ms": 20_000, "ms": {"write": len(self.runs)},
                "entered": [e["ticker"] for e in evs if e["type"] == "ENTER"],
                "exited": [e["ticker"] for e in evs if e["type"] == "EXIT"], "hk_request": self.hk.get(a.tick_id)}

    @property
    def ids(self) -> list[str]:
        return [r.tick_id for r in self.runs]


class Alerts:
    """Stands in for services.alerts.dispatch_radar_entries; the first `fail` calls raise."""

    def __init__(self, fail: int = 0):
        self.batches: list[list[str]] = []
        self.fail = fail

    def __call__(self, events: list[dict]) -> int:
        if self.fail:
            self.fail -= 1
            raise RuntimeError("discord is down")
        self.batches.append([e["id"] for e in events])
        return len(events)


class Housekeeping:
    def __init__(self, clock: Clock, error: Exception | None = None, status: str = "ok"):
        self.clock, self.error, self.status = clock, error, status
        self.runs: list[tuple[str, str]] = []

    def __call__(self, trigger: str) -> dict:
        self.runs.append((trigger, iso(self.clock())))
        if self.error:
            raise self.error
        return {"status": self.status, "run_id": f"hk-{len(self.runs)}"}


class RunLog:
    def __init__(self, fail: bool = False):
        self.rows: list[dict] = []
        self.fail = fail

    def start(self, phase: str, meta: dict) -> int:
        if self.fail:
            raise RuntimeError("pipeline_run_log is gone")
        self.rows.append({"phase": phase, "status": "RUNNING", "meta": dict(meta)})
        return len(self.rows)

    def finish(self, row_id, *, ok: bool, error: str | None, meta: dict) -> None:
        if self.fail:
            raise RuntimeError("pipeline_run_log is gone")
        self.rows[row_id - 1].update(status="SUCCESS" if ok else "FAILED", error=error, meta=dict(meta))


def make(store, start: str, *, engine: dict | None = None, state: dict | None = None, live: dict | None = None,
         durations=None, results=None, events=None, hk=None, on_run=None, alerts=None, housekeeping=None,
         run_log=None, env=None):
    seed(store, engine=engine, state=state, live=live)
    clock = Clock(start)
    fake = FakeTick(store, clock, durations=durations, results=results, events=events, hk=hk, on_run=on_run)
    w = worker_mod.Worker(store, clock=clock, sleep=clock.sleep, run_tick=fake, dispatch_alerts=alerts or Alerts(),
                          housekeeping=housekeeping or Housekeeping(clock), run_log=run_log or RunLog(),
                          env=env if env is not None else {"PATH": "x"}, tick_cmd=("tick",))
    return w, clock, fake


def run_until(w: worker_mod.Worker, clock: Clock, until: str) -> None:
    """Run the worker until a SIGTERM at `until` (the worker never ends on its own)."""
    t = at(until)
    clock.hooks.append(lambda now: now >= t and not w.stop and w.request_stop(signal.SIGTERM, None))
    assert w.run() == 0


# ---------------------------------------------------------------- the plan

@pytest.mark.parametrize("session, warm, first, last_regular, final_run, n", [
    (MON, "2026-09-28T13:10:00Z", "2026-09-28T13:35:50Z", "2026-09-28T19:55:00Z", "2026-09-28T20:00:50Z", 78),
    (EST_DAY, "2026-11-02T14:10:00Z", "2026-11-02T14:35:50Z", "2026-11-02T20:55:00Z", "2026-11-02T21:00:50Z", 78),
    (HALF, "2026-11-27T14:10:00Z", "2026-11-27T14:35:50Z", "2026-11-27T17:55:00Z", "2026-11-27T18:00:50Z", 42),
])
def test_plan_session(session, warm, first, last_regular, final_run, n):
    warmup, ticks = worker_mod.plan_session(session)
    assert warmup.warmup and iso(warmup.tick_epoch) == warm == iso(warmup.run_at)
    assert len(ticks) == n and iso(ticks[0].run_at) == first and iso(ticks[-2].tick_epoch) == last_regular
    assert ticks[-1].final and ticks[-1].tick_epoch == session.close_epoch and iso(ticks[-1].run_at) == final_run
    assert not any(t.final for t in ticks[:-1])
    assert all(t.run_at - t.tick_epoch == RUNTIME["tick_offset_s"] for t in ticks[:-1])


def test_next_slot_skips_after_an_overrun_and_always_keeps_the_final_tick():
    _, ticks = worker_mod.plan_session(MON)
    slot, skipped = worker_mod.next_slot(ticks, at("2026-09-28T14:07:30Z"), int(at("2026-09-28T14:00:00Z")))
    assert (iso(slot.tick_epoch), skipped) == ("2026-09-28T14:10:00Z", 1)
    slot, skipped = worker_mod.next_slot(ticks, at("2026-09-28T20:07:00Z"), int(at("2026-09-28T19:50:00Z")))
    assert slot.final and skipped == 1                                  # late, but it runs
    assert worker_mod.next_slot(ticks, at("2026-09-28T20:07:00Z"), MON.close_epoch) == (None, 0)
    slot, skipped = worker_mod.next_slot(ticks, at("2026-09-28T12:33:00Z"), None)
    assert iso(slot.tick_epoch) == "2026-09-28T13:35:00Z" and skipped == 0


@pytest.mark.parametrize("after, expected", [
    ("2026-09-28T07:30:00Z", "2026-09-28T07:30:00Z"),                  # 03:30 EDT itself
    ("2026-09-28T07:30:01Z", "2026-09-29T07:30:00Z"),
    ("2026-10-31T12:00:00Z", "2026-11-01T08:30:00Z"),                  # across the end of DST: 03:30 EST
    ("2026-11-02T03:00:00Z", "2026-11-02T08:30:00Z"),                  # 22:00 ET the evening before
    ("2027-03-14T05:00:00Z", "2027-03-14T07:30:00Z"),                  # the start of DST: 03:30 EDT exists
])
def test_next_nightly(after, expected):
    assert iso(worker_mod.next_nightly(at(after))) == expected


def test_event_time():
    assert iso(worker_mod.event_time("20260928T1435Z-NVDA-ENTER-1")) == "2026-09-28T14:35:00Z"
    assert worker_mod.event_time("old") is None and worker_mod.event_time(None) is None


# ---------------------------------------------------------------- a whole session

def test_full_session(radar_store):
    run_log = RunLog()
    w, clock, fake = make(radar_store, "2026-09-28T12:33:20Z", run_log=run_log)
    run_until(w, clock, "2026-09-28T20:10:00Z")
    runs = fake.runs
    assert len(runs) == 79 and runs[0].warmup and iso(runs[0].at) == "2026-09-28T13:10:00Z"
    assert fake.ids[1] == "2026-09-28T13:35:00Z" and iso(runs[1].at) == "2026-09-28T13:35:50Z"
    assert all(r.at - parse_tick_id(r.tick_id) == 50 for r in runs[1:])
    assert runs[-1].final and runs[-1].tick_id == "2026-09-28T20:00:00Z" and not any(r.final for r in runs[:-1])
    assert all(r.timeout == RUNTIME["tick_timeout_s"] and r.env["PATH"] == "x" for r in runs)
    assert all(r.run_id == w.run_id == "worker-20260928T123320Z" for r in runs)
    assert runs[0].cmd == ["tick", "--tick-id", "2026-09-28T13:10:00Z", "--warmup"]
    assert runs[-1].cmd == ["tick", "--tick-id", "2026-09-28T20:00:00Z", "--final"]
    assert max(clock.naps) <= worker_mod.NAP_S                          # a SIGTERM is noticed within 5 s

    warm, first, second, last = runs[0].loop, runs[1].loop, runs[2].loop, runs[-1].loop
    assert warm["ticks_today"] == 0 and warm["next_tick_at"] == "2026-09-28T13:35:50Z"
    assert first["ticks_today"] == 1 and first["ticks_skipped"] == 0 and first["session"] == DAY
    assert first["started_at"] == "2026-09-28T12:33:20Z" and first["last_tick"] == "2026-09-28T13:35:00Z"
    assert first["next_tick_at"] == "2026-09-28T13:40:50Z" and last["next_tick_at"] is None
    assert first["run_id"] == w.run_id and first["write_ms"] == [1]           # the warmup's commit time
    assert second["write_ms"] == [1, 2] and last["ticks_today"] == 78 and last["ticks_skipped"] == 0
    assert last["write_ms"] == list(range(29, 79))                          # the last 50
    assert all(callable(r.stop) and r.stop_requested is False for r in runs)    # the tick can be stopped

    assert radar_store.load_engine_doc()["loop"]["last_tick"] == "2026-09-28T20:00:00Z"
    assert len(scan_rows(radar_store)) == 79 and w.dispatch_alerts.batches == []
    assert [r["phase"] for r in run_log.rows] == ["radar_tick"] * 79
    assert all(r["status"] == "SUCCESS" for r in run_log.rows)
    assert run_log.rows[0]["meta"]["kind"] == "warmup" and run_log.rows[-1]["meta"]["kind"] == "final"
    assert w.housekeeping.runs == []                                    # the nightly run is at 03:30 ET


def test_an_est_half_day_ends_at_the_early_close(radar_store):
    w, clock, fake = make(radar_store, "2026-11-27T13:07:00Z")
    run_until(w, clock, "2026-11-27T19:00:00Z")
    assert fake.ids[0] == "2026-11-27T14:10:00Z" and fake.runs[0].warmup
    assert fake.ids[-1] == "2026-11-27T18:00:00Z" and fake.runs[-1].final and len(fake.runs) == 43


def test_overrun_skips_the_missed_boundary_and_counts_it(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T13:50:00Z", durations={"2026-09-28T14:00:00Z": 400})
    run_until(w, clock, "2026-09-28T14:30:00Z")
    assert "2026-09-28T14:05:00Z" not in fake.ids
    after = fake.runs[fake.ids.index("2026-09-28T14:10:00Z")]
    assert iso(after.at) == "2026-09-28T14:10:50Z" and after.loop["ticks_skipped"] == 1


def test_final_tick_runs_even_after_an_overrun_past_the_close(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T19:40:00Z", durations={"2026-09-28T19:55:00Z": 700})
    run_until(w, clock, "2026-09-28T21:00:00Z")
    assert fake.ids == ["2026-09-28T19:40:00Z", "2026-09-28T19:45:00Z", "2026-09-28T19:50:00Z",
                        "2026-09-28T19:55:00Z", "2026-09-28T20:00:00Z"]
    assert fake.runs[-1].final and iso(fake.runs[-1].at) == "2026-09-28T20:07:30Z"
    assert fake.runs[-1].loop["ticks_skipped"] == 0


@pytest.mark.parametrize("start, runs_final", [("2026-09-28T23:30:00Z", True), ("2026-09-29T00:30:00Z", False)],
                         ids=["before the end of after-hours", "after it"])
def test_a_missed_final_tick_runs_late_only_until_the_end_of_after_hours(radar_store, start, runs_final):
    """A worker that was down at the close still finishes the session (the engine catches up) during
    after-hours; later, the next session's warmup takes over."""
    engine = {"schema": 1, "loop": {"session": DAY, "ticks_today": 40, "last_tick": "2026-09-28T16:45:00Z"}}
    w, clock, fake = make(radar_store, start, engine=engine)
    run_until(w, clock, "2026-09-29T13:20:00Z")
    assert fake.ids == ["2026-09-28T20:00:00Z"] * runs_final + ["2026-09-29T13:10:00Z"]
    if runs_final:
        assert fake.runs[0].final and fake.runs[0].loop["ticks_today"] == 41 and fake.runs[0].loop["ticks_skipped"] == 38


def test_a_restart_resumes_the_counters_of_the_session(radar_store):
    prev = {"schema": 1, "loop": {"session": DAY, "ticks_today": 55, "ticks_skipped": 1,
                                  "last_tick": "2026-09-28T18:00:00Z", "write_ms": [7, 8]}}
    w, clock, fake = make(radar_store, "2026-09-28T18:20:10Z", engine=prev)
    run_until(w, clock, "2026-09-28T18:24:00Z")
    (first,) = fake.runs
    assert first.tick_id == "2026-09-28T18:20:00Z" and not first.warmup
    assert first.loop["ticks_today"] == 56 and first.loop["ticks_skipped"] == 4      # 18:05, 18:10, 18:15 missed
    assert first.loop["write_ms"] == [7, 8] and first.loop["run_id"] == w.run_id


def test_counters_of_another_session_are_not_carried(radar_store):
    prev = {"schema": 1, "loop": {"session": "2026-09-25", "ticks_today": 78, "ticks_skipped": 3,
                                  "last_tick": "2026-09-25T20:00:00Z"}}
    w, clock, fake = make(radar_store, "2026-09-28T13:36:00Z", engine=prev)
    run_until(w, clock, "2026-09-28T13:44:00Z")
    assert fake.ids == ["2026-09-28T13:40:00Z"] and fake.runs[0].loop["ticks_today"] == 1
    assert fake.runs[0].loop["ticks_skipped"] == 0


def test_a_restart_after_the_final_tick_waits_for_the_next_session(radar_store):
    prev = {"schema": 1, "loop": {"session": DAY, "ticks_today": 78, "last_tick": "2026-09-28T20:00:00Z"}}
    w, clock, fake = make(radar_store, "2026-09-28T20:01:30Z", engine=prev)
    run_until(w, clock, "2026-09-29T13:12:00Z")
    assert fake.ids == ["2026-09-29T13:10:00Z"] and fake.runs[0].warmup
    assert fake.runs[0].loop["session"] == "2026-09-29" and fake.runs[0].loop["ticks_today"] == 0


def test_a_restart_before_the_first_tick_runs_the_warmup_at_once(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T13:20:00Z")
    run_until(w, clock, "2026-09-28T13:37:00Z")
    assert fake.ids == ["2026-09-28T13:10:00Z", "2026-09-28T13:35:00Z"] and fake.runs[0].warmup
    assert iso(fake.runs[0].at) == "2026-09-28T13:20:00Z"


def test_holidays_and_weekends_run_no_tick_but_the_nightly_housekeeping(radar_store):
    """Thanksgiving: no tick; the next session is the Friday half day. Housekeeping still runs every night."""
    w, clock, fake = make(radar_store, "2026-11-26T15:07:00Z")
    run_until(w, clock, "2026-11-27T14:12:00Z")
    assert fake.ids == ["2026-11-27T14:10:00Z"] and fake.runs[0].warmup
    assert w.housekeeping.runs == [("schedule", "2026-11-27T08:30:00Z")]


def test_nightly_housekeeping_runs_every_day_including_weekends(radar_store):
    prev = {"schema": 1, "loop": {"session": "2026-10-02", "ticks_today": 78, "last_tick": "2026-10-02T20:00:00Z"}}
    w, clock, fake = make(radar_store, "2026-10-02T21:00:00Z", engine=prev)
    run_until(w, clock, "2026-10-05T13:20:00Z")
    assert w.housekeeping.runs == [("schedule", "2026-10-03T07:30:00Z"), ("schedule", "2026-10-04T07:30:00Z"),
                                   ("schedule", "2026-10-05T07:30:00Z")]
    assert fake.ids == ["2026-10-05T13:10:00Z"]


@pytest.mark.parametrize("start, first_run", [("2026-09-28T07:50:00Z", "2026-09-28T07:50:00Z"),
                                              ("2026-09-28T08:45:00Z", "2026-09-29T07:30:00Z")],
                         ids=["restarted 20 min after: catches up", "restarted 75 min after: next night"])
def test_a_restart_soon_after_the_nightly_time_still_runs_it(radar_store, start, first_run):
    w, clock, _ = make(radar_store, start, engine={"schema": 1, "loop": {"session": DAY, "last_tick": "2026-09-28T20:00:00Z"}})
    run_until(w, clock, "2026-09-29T08:00:00Z")
    assert w.housekeeping.runs[0] == ("schedule", first_run)


def test_housekeeping_failures_never_stop_the_radar(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T07:20:00Z")
    w.housekeeping = Housekeeping(clock, error=OSError("no backup volume"))
    run_until(w, clock, "2026-09-28T13:37:00Z")
    assert w.housekeeping.runs == [("schedule", "2026-09-28T07:30:00Z")]
    assert fake.ids == ["2026-09-28T13:10:00Z", "2026-09-28T13:35:00Z"]


# ---------------------------------------------------------------- on-request housekeeping

def test_a_tick_request_runs_housekeeping_at_most_every_six_hours(radar_store):
    reason = "DB write p95 1800 ms over 1500 ms"
    engine = {"schema": 1, "ops": {"hk_dispatched_at": "2026-09-28T08:30:00Z"}}     # 6 h 6 min before 14:36:10
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", engine=engine,
                          hk={"2026-09-28T14:35:00Z": reason, "2026-09-28T14:40:00Z": reason})
    run_until(w, clock, "2026-09-28T14:47:00Z")
    assert w.housekeeping.runs == [("worker_request", "2026-09-28T14:36:10Z")]
    assert fake.runs[0].ops == {"hk_dispatched_at": "2026-09-28T08:30:00Z"}
    assert fake.runs[1].ops == {"hk_dispatched_at": "2026-09-28T14:36:10Z", "hk_dispatch_result": "ok"}
    assert radar_store.load_engine_doc()["ops"]["hk_dispatched_at"] == "2026-09-28T14:36:10Z"   # kept for a restart


def test_a_restart_remembers_the_last_requested_housekeeping(radar_store):
    engine = {"schema": 1, "ops": {"hk_dispatched_at": "2026-09-28T09:00:00Z", "hk_dispatch_result": "ok"}}
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", engine=engine, hk={"2026-09-28T14:35:00Z": "slow"})
    run_until(w, clock, "2026-09-28T14:37:00Z")
    assert w.housekeeping.runs == [] and fake.ids == ["2026-09-28T14:35:00Z"]


@pytest.mark.parametrize("outcome, recorded", [({"error": RuntimeError("archive failed")}, "failed"),
                                               ({"status": "busy"}, "busy"), ({"status": "error"}, "error")],
                         ids=["raised", "another run active", "partition errors"])
def test_the_outcome_of_a_requested_housekeeping_is_recorded(radar_store, outcome, recorded):
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", hk={"2026-09-28T14:35:00Z": "slow"})
    w.housekeeping = Housekeeping(clock, **outcome)
    run_until(w, clock, "2026-09-28T14:42:00Z")
    assert len(w.housekeeping.runs) == 1 and fake.runs[1].ops["hk_dispatch_result"] == recorded


def test_the_default_housekeeping_is_radar_housekeeping_run(radar_store, monkeypatch):
    from radar import housekeeping
    calls = []
    monkeypatch.setattr(housekeeping, "run", lambda **kw: calls.append(kw) or {"status": "ok"})
    w = worker_mod.Worker(radar_store, run_log=RunLog())
    assert w._housekeeping("schedule") == "ok"
    assert calls == [{"mode": "apply", "trigger": "schedule", "store": radar_store}]


# ---------------------------------------------------------------- startup: lock and schema

class Gate:
    """Wraps the store: the advisory lock and the schema check answer from scripted lists first, and each
    call is recorded at the fake clock's time (set `clock` after make())."""

    def __init__(self, inner, locks=(), schema=()):
        self.inner, self.locks, self.schema = inner, list(locks), list(schema)
        self.clock: Clock | None = None
        self.lock_at: list[str] = []
        self.schema_at: list[str] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _now(self) -> str:
        assert self.clock is not None, "set gate.clock to the worker's fake clock"
        return iso(self.clock())

    def try_advisory_lock(self, key):
        self.lock_at.append(self._now())
        assert key == worker_mod.LOCK_KEY == 771234
        nxt = self.locks.pop(0) if self.locks else True
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def schema_ready(self):
        self.schema_at.append(self._now())
        return self.schema.pop(0) if self.schema else True


class RealTimeFence:
    """Stands in for radar.worker's `time` module and records every use. The startup tests run on the fake
    clock only, so their outcome cannot depend on how fast (or how loaded) the machine is; a use of the
    real clock is reported as a test failure rather than surfacing as a flake. It records instead of
    raising because the worker swallows exceptions on its logging and housekeeping paths."""

    def __init__(self):
        self.used: list[str] = []

    def __getattr__(self, name):
        self.used.append(name)
        return getattr(time, name)


def gated(store, start: str, monkeypatch, **gate_kw):
    gate = Gate(store, **gate_kw)
    w, clock, fake = make(gate, start)
    gate.clock = clock
    fence = RealTimeFence()
    monkeypatch.setattr(worker_mod, "time", fence)
    return gate, w, clock, fake, fence


def test_the_worker_waits_for_the_advisory_lock(radar_store, monkeypatch):
    """Deterministic: lock attempts happen on the fake clock, 60 s apart, whatever the machine's load. The
    warmup that fell due while waiting runs the moment the lock is held, well before the first tick."""
    gate, w, clock, fake, fence = gated(radar_store, "2026-09-28T13:30:00Z", monkeypatch,
                                        locks=[False, ConnectionError("db restarting"), False, True])
    run_until(w, clock, "2026-09-28T13:39:00Z")
    assert gate.lock_at == ["2026-09-28T13:30:00Z", "2026-09-28T13:31:00Z", "2026-09-28T13:32:00Z",
                            "2026-09-28T13:33:00Z"]                 # three retries, 60 s apart, the 4th holds
    assert gate.schema_at == ["2026-09-28T13:33:00Z"]                # the schema is checked once the lock is held
    assert fake.ids == ["2026-09-28T13:10:00Z", "2026-09-28T13:35:00Z"] and fake.runs[0].warmup
    assert [iso(r.at) for r in fake.runs] == ["2026-09-28T13:33:00Z", "2026-09-28T13:35:50Z"]
    assert fake.runs[1].loop["ticks_skipped"] == 0 and max(clock.naps) <= worker_mod.NAP_S
    assert fence.used == []                                          # nothing read the real clock


def test_the_worker_waits_for_the_radar_tables(radar_store, monkeypatch):
    gate, w, clock, fake, fence = gated(radar_store, "2026-09-28T13:35:20Z", monkeypatch, schema=[False] * 4)
    run_until(w, clock, "2026-09-28T13:44:00Z")
    assert gate.schema_at == ["2026-09-28T13:35:20Z", "2026-09-28T13:35:30Z", "2026-09-28T13:35:40Z",
                              "2026-09-28T13:35:50Z", "2026-09-28T13:36:00Z"]        # polled every 10 s
    assert fake.ids == ["2026-09-28T13:40:00Z"]                        # the 13:35:50 start passed while waiting
    assert fence.used == []


def test_a_stop_while_waiting_for_the_lock_ends_the_worker(radar_store, monkeypatch):
    gate, w, clock, fake, fence = gated(radar_store, "2026-09-28T13:30:00Z", monkeypatch, locks=[False] * 1000)
    run_until(w, clock, "2026-09-28T13:45:00Z")
    assert fake.runs == [] and gate.schema_at == [] and fence.used == []
    assert gate.lock_at == [f"2026-09-28T13:{m:02d}:00Z" for m in range(30, 45)]   # the 13:45 retry never comes
    assert clock.t - at("2026-09-28T13:45:00Z") <= worker_mod.NAP_S


def test_schema_ready_falls_back_to_reading_the_engine_document():
    class NoCheck:
        def load_engine_doc(self):
            raise RuntimeError("relation radar_runtime does not exist")

    assert worker_mod.Worker(NoCheck(), run_log=RunLog())._schema_ready() is False


# ---------------------------------------------------------------- stopping

def test_sigterm_while_waiting_stops_the_worker(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T13:36:00Z")
    run_until(w, clock, "2026-09-28T13:52:00Z")
    assert fake.ids == ["2026-09-28T13:40:00Z", "2026-09-28T13:45:00Z", "2026-09-28T13:50:00Z"]
    assert clock.t - at("2026-09-28T13:52:00Z") <= worker_mod.NAP_S


def test_sigterm_during_a_tick_records_nothing_and_sends_no_alert(radar_store):
    run_log = RunLog()
    events = {"2026-09-28T13:40:00Z": [event("NVDA", "ENTER", "2026-09-28T13:40:00Z", 1)]}
    w, clock, fake = make(radar_store, "2026-09-28T13:36:00Z", results={"2026-09-28T13:40:00Z": "timeout"},
                          events=events, run_log=run_log,
                          on_run=lambda a, env: w.request_stop(signal.SIGTERM, None))
    assert w.run() == 0
    assert len(fake.runs) == 1 and scan_rows(radar_store) == [] and w.dispatch_alerts.batches == []
    assert run_log.rows == [{"phase": "radar_tick", "status": "FAILED", "error": "timeout: boom",
                             "meta": run_log.rows[0]["meta"]}]


def test_signal_handlers_set_the_stop_flag(radar_store):
    w, *_ = make(radar_store, "2026-09-28T13:36:00Z")
    saved = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        worker_mod.install_signal_handlers(w)
        signal.raise_signal(signal.SIGTERM)
        assert w.stop
    finally:
        signal.signal(signal.SIGTERM, saved[0])
        signal.signal(signal.SIGINT, saved[1])


def test_main_runs_the_worker_on_the_production_store_and_releases_it(monkeypatch):
    closed = []

    class Store:
        def close(self):
            closed.append(True)

    import radar.store
    monkeypatch.setattr(radar.store, "RadarStore", Store)
    monkeypatch.setattr(worker_mod.Worker, "run", lambda self: 7)
    saved = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        assert worker_mod.main([]) == 7
        assert signal.getsignal(signal.SIGTERM).__self__.__class__ is worker_mod.Worker
    finally:
        signal.signal(signal.SIGTERM, saved[0])
        signal.signal(signal.SIGINT, saved[1])
    assert closed == [True]


# ---------------------------------------------------------------- failures

PREV_STATE = {"schema": 1, "generated_at": "2026-09-28T14:30:51Z", "tick_id": "2026-09-28T14:30:00Z",
              "last_bar": "2026-09-28T14:30:00Z", "status": "ok", "message": "",
              "session": {"date": DAY, "phase": "regular", "open": "2026-09-28T13:30:00Z",
                          "close": "2026-09-28T20:00:00Z", "half_day": False},
              "next_tick_at": "2026-09-28T14:35:50Z", "params_version": "radar-sm-1",
              "source": {"name": "yahoo", "status": "ok", "consecutive_failures": 0, "last_ok_at": "2026-09-28T14:30:51Z"},
              "market": {"mode": "normal", "dir": None, "spy_chg_day_pct": 0.1, "spy_z30": 0.2, "breadth30": 0.5},
              "counts": {"universe": 5, "stage_b": 2, "members": 1, "heating": 0, "entered_today": 1, "exited_today": 0},
              "members": [{"ticker": "NVDA"}], "heating": [], "recent_exits": [], "sector_banners": [],
              "health": {"ticks_today": 12, "ticks_skipped": 0, "last_tick_ms": 900, "loop_run_id": "worker-x",
                         "loop_started_at": "2026-09-28T12:33:00Z", "published_late": 0},
              "disclaimer": "Educational analysis of what is moving now, not a forecast and not financial advice."}


@pytest.mark.parametrize("status, word", [("timeout", "timed out"), ("error", "failed")])
def test_a_tick_that_wrote_nothing_is_logged_and_the_snapshot_republished(radar_store, status, word):
    run_log = RunLog()
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", state=PREV_STATE,
                          results={"2026-09-28T14:35:00Z": status}, run_log=run_log)
    run_until(w, clock, "2026-09-28T14:39:00Z")
    assert fake.ids == ["2026-09-28T14:35:00Z"]
    state = radar_store.load_state()
    check_state(state)
    assert state["status"] == "error" and state["message"] == f"The last scan {word}. Stocks already on the radar are held, not dropped."
    assert state["members"] == [{"ticker": "NVDA"}] and state["tick_id"] == PREV_STATE["tick_id"]
    assert state["next_tick_at"] == "2026-09-28T14:40:50Z" and state["health"]["last_tick_ms"] == 1234
    assert state["health"]["ticks_today"] == 1 and state["health"]["loop_run_id"] == w.run_id
    (row,) = scan_rows(radar_store)
    check_scan_row(row)
    assert row["tick"] == "2026-09-28T14:35:00Z" and row["status"] == "error" and row["session"] == DAY
    assert row["phase"] == "regular" and row["lag_s"] == 50 and row["members"] == 1 and row["ms"]["total"] == 1234
    assert row["errors"] == [f"{status}: boom"] and row["run_id"] == w.run_id
    engine = radar_store.load_engine_doc()
    assert engine["loop"]["last_tick"] == "2026-09-28T14:35:00Z"      # the worker's bookkeeping survives
    timed_out = status == "timeout"                                     # a killed tick counts as a failed call
    assert state["source"]["consecutive_failures"] == int(timed_out)
    assert state["source"]["status"] == ("down" if timed_out else "ok")
    assert run_log.rows[0]["status"] == "FAILED" and run_log.rows[0]["error"] == f"{status}: boom"
    assert w.dispatch_alerts.batches == []


FRIDAY_EXITS = [{"ticker": "FSLR", "exited_at": "2026-09-25T19:45:00Z"}, {"ticker": "BALL", "exited_at": "2026-09-25T18:10:00Z"}]
FRIDAY_COUNTS = {"universe": 517, "stage_b": 40, "members": 0, "heating": 0, "entered_today": 9, "exited_today": 9}
WARMUP_STATE = {**PREV_STATE, "generated_at": "2026-09-28T13:10:02Z", "tick_id": "2026-09-28T13:10:00Z",
                "last_bar": "2026-09-25T20:00:00Z", "status": "closed", "message": "The radar starts at 09:35 ET.",
                "session": {**PREV_STATE["session"], "phase": "pre"}, "next_tick_at": None, "counts": FRIDAY_COUNTS,
                "members": [], "recent_exits": FRIDAY_EXITS}
FRIDAY_STATE = {**PREV_STATE, "generated_at": "2026-09-25T20:00:52Z", "tick_id": "2026-09-25T20:00:00Z",
                "last_bar": "2026-09-25T20:00:00Z", "next_tick_at": None, "counts": FRIDAY_COUNTS,
                "session": {"date": "2026-09-25", "phase": "post", "open": "2026-09-25T13:30:00Z",
                            "close": "2026-09-25T20:00:00Z", "half_day": False},
                "members": [{"ticker": "HUM"}], "recent_exits": FRIDAY_EXITS}


@pytest.mark.parametrize("prev", [WARMUP_STATE, FRIDAY_STATE], ids=["after the warmup heartbeat", "warmup wrote nothing"])
@pytest.mark.parametrize("status", ["timeout", "error"])
def test_a_failed_first_tick_republishes_nothing_from_another_session(radar_store, prev, status):
    """INT-1 (SPEC 12.6): the state before the first scan is the pre-open heartbeat (dated today, still showing
    Friday's exits) or Friday's last scan. The error state must not present Friday's exits, counts, members or
    last bar as today's, and is an in-session state of today."""
    w, clock, fake = make(radar_store, "2026-09-28T13:35:50Z", state=prev, results={"2026-09-28T13:35:00Z": status})
    run_until(w, clock, "2026-09-28T13:39:00Z")
    assert fake.ids == ["2026-09-28T13:35:00Z"]
    state = radar_store.load_state()
    check_state(state)
    assert state["status"] == "error" and state["message"].startswith("The last scan ")
    assert state["session"] == {"date": DAY, "phase": "regular", "open": "2026-09-28T13:30:00Z",
                                "close": "2026-09-28T20:00:00Z", "half_day": False}
    assert state["recent_exits"] == [] and state["members"] == [] and state["heating"] == [] and state["last_bar"] is None
    assert state["counts"] == dict.fromkeys(FRIDAY_COUNTS, 0)
    assert state["next_tick_at"] == "2026-09-28T13:40:50Z"


def test_a_failed_tick_after_a_failed_scan_of_today_keeps_its_snapshot(radar_store):
    """An error state the worker wrote for today is a scan of today: a second failure republishes it as is."""
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", state={**PREV_STATE, "status": "error"},
                          results={i: "error" for i in ("2026-09-28T14:35:00Z", "2026-09-28T14:40:00Z")})
    run_until(w, clock, "2026-09-28T14:43:00Z")
    assert fake.ids == ["2026-09-28T14:35:00Z", "2026-09-28T14:40:00Z"]
    state = radar_store.load_state()
    assert state["members"] == PREV_STATE["members"] and state["counts"] == PREV_STATE["counts"]
    assert state["last_bar"] == PREV_STATE["last_bar"] and state["session"] == PREV_STATE["session"]
    assert len(scan_rows(radar_store)) == 2


def test_a_failed_warmup_logs_without_touching_the_snapshot(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T13:09:00Z", state=PREV_STATE,
                          results={"2026-09-28T13:10:00Z": "error"})
    run_until(w, clock, "2026-09-28T13:20:00Z")
    assert fake.runs[0].warmup
    assert radar_store.load_state() == PREV_STATE
    (row,) = scan_rows(radar_store)
    assert row["errors"] == ["error: boom"] and row["phase"] == "pre" and row["lag_s"] == 0
    assert radar_store.load_engine_doc()["loop"]["next_tick_at"] == "2026-09-28T13:35:50Z"


def test_a_failed_tick_without_any_snapshot_creates_none(radar_store):
    w, clock, _ = make(radar_store, "2026-09-28T14:34:00Z", results={"2026-09-28T14:35:00Z": "error"})
    run_until(w, clock, "2026-09-28T14:39:00Z")
    assert radar_store.load_state() == {} and len(scan_rows(radar_store)) == 1


def test_an_error_result_with_a_commit_is_not_logged_twice(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", results={"2026-09-28T14:35:00Z": "degraded"})
    run_until(w, clock, "2026-09-28T14:39:00Z")
    assert len(scan_rows(radar_store)) == 1


def test_a_tick_killed_after_its_commit_is_not_overwritten(radar_store):
    """Killed between its commit and its exit: its scan_log row is there, so no failure is recorded over
    the good snapshot it published."""
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", state=PREV_STATE,
                          results={"2026-09-28T14:35:00Z": "committed-timeout"})
    run_until(w, clock, "2026-09-28T14:39:00Z")
    (row,) = scan_rows(radar_store)
    assert row["status"] == "ok" and radar_store.load_state()["status"] == "ok"


def test_a_failure_that_cannot_be_recorded_never_stops_the_worker(radar_store):
    class Broken:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def write_failure(self, **kw):
            raise RuntimeError("database is gone")

    w, clock, fake = make(Broken(radar_store), "2026-09-28T14:34:00Z", results={"2026-09-28T14:35:00Z": "error"})
    run_until(w, clock, "2026-09-28T14:43:00Z")
    assert fake.ids == ["2026-09-28T14:35:00Z", "2026-09-28T14:40:00Z"]


# ---------------------------------------------------------------- source health of a tick that wrote nothing

HEALTHY = {"schema": 1, "name": "yahoo", "status": "ok", "consecutive_failures": 0, "last_ok_at": "2026-09-28T14:30:51Z",
           "workers": 16, "breaker": {"open": False, "opened_at": None, "calls": 0, "good_probes": 0}}
FIVE_MIN = ["2026-09-28T14:35:00Z", "2026-09-28T14:40:00Z", "2026-09-28T14:45:00Z"]


def write_live(store, env, tick_id, health) -> None:
    """What the tick's fetcher leaves after a call: its health, stamped with the worker's run id and the tick."""
    store.write_live_health({**health, HEALTH_REF: health_ref(env[RUN_ID_ENV], tick_id)})


def test_timed_out_ticks_open_the_breaker(radar_store):
    """RT-1/E2E-1/DATA-2: a tick killed by the timeout leaves no health of its own, so the worker counts it as
    a failed call; BREAKER_TRIP of them in a row switch the next tick to the Nasdaq fallback."""
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", state=PREV_STATE,
                          engine={"schema": 1, "source_health": HEALTHY}, results={i: "timeout" for i in FIVE_MIN})
    run_until(w, clock, "2026-09-28T14:52:00Z")
    assert fake.ids == FIVE_MIN + ["2026-09-28T14:50:00Z"]
    assert [r.source_health["consecutive_failures"] for r in fake.runs] == [0, 1, 2, 3]
    assert [r.source_health["breaker"]["open"] for r in fake.runs] == [False, False, False, True]
    opened = fake.runs[-1].source_health
    assert opened["name"] == "nasdaq" and opened["status"] == "down" and opened["workers"] == 16
    assert opened["breaker"] == {"open": True, "opened_at": "2026-09-28T14:46:10Z", "calls": 0, "good_probes": 0}
    assert [r["source"] for r in scan_rows(radar_store)[:3]] == ["yahoo", "yahoo", "nasdaq"]


def test_the_killed_ticks_own_health_is_merged_before_counting_the_timeout(radar_store):
    """The fetcher writes the live health after every call; the worker merges it (which clears it)."""
    side = {**HEALTHY, "status": "down", "consecutive_failures": 2, "workers": 4}
    w, clock, _ = make(radar_store, "2026-09-28T14:34:00Z", state=PREV_STATE,
                       engine={"schema": 1, "source_health": {**HEALTHY, "kept": True}},
                       results={FIVE_MIN[0]: "timeout"},
                       on_run=lambda a, env: write_live(radar_store, env, a.tick_id, side))
    run_until(w, clock, "2026-09-28T14:39:00Z")
    h = radar_store.load_engine_doc()["source_health"]
    assert h["consecutive_failures"] == 3 and h["breaker"]["open"] is True and h["workers"] == 4 and h["kept"] is True
    assert HEALTH_REF not in h and radar_store.load_live_health() is None


def test_a_failed_tick_keeps_its_health_without_counting_and_a_stale_live_copy_is_ignored(radar_store, tmp_path):
    side = {**HEALTHY, "status": "degraded", "consecutive_failures": 1}
    stale = {**HEALTHY, "consecutive_failures": 9, HEALTH_REF: "worker-old/2026-09-28T14:30:00Z"}   # an earlier tick's
    w, clock, _ = make(radar_store, "2026-09-28T14:34:00Z", state=PREV_STATE, live=stale,
                       engine={"schema": 1, "source_health": HEALTHY}, results={FIVE_MIN[0]: "error"})
    run_until(w, clock, "2026-09-28T14:39:00Z")
    assert radar_store.load_engine_doc()["source_health"] == HEALTHY       # nothing new from this tick

    from radar.store import RadarStore
    other = RadarStore(_fresh_db(tmp_path / "2"))
    try:
        w, clock, _ = make(other, "2026-09-28T14:34:00Z", state=PREV_STATE, engine={"schema": 1, "source_health": HEALTHY},
                           results={FIVE_MIN[0]: "error"}, on_run=lambda a, env: write_live(other, env, a.tick_id, side))
        run_until(w, clock, "2026-09-28T14:39:00Z")
        assert other.load_engine_doc()["source_health"] == side           # a crash is not a timeout: not counted
    finally:
        other.close()


def _fresh_db(path: Path) -> str:
    from sqlalchemy import create_engine

    from db.models import Base
    from radar.store import RADAR_TABLES
    path.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{(path / 'radar.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine, tables=list(RADAR_TABLES))
    engine.dispose()
    return url


def fam(failures: int, is_open: bool = False) -> dict:
    return {"status": "ok", "consecutive_failures": failures,
            "breaker": {"open": is_open, "opened_at": None, "calls": 0, "good_probes": 0}}


def test_a_timeout_raises_the_worst_family_that_is_still_closed():
    now = "2026-09-28T14:40:20Z"
    h = {"name": "yahoo", "status": "degraded", "consecutive_failures": 2, "last_ok_at": None,
         "families": {"chart": fam(2), "crumb": fam(1)}}
    out = worker_mod.health_after_timeout(h, now)
    assert out["families"]["chart"] == {"status": "down", "consecutive_failures": 3,
                                        "breaker": {"open": True, "opened_at": now, "calls": 0, "good_probes": 0}}
    assert out["families"]["crumb"] == fam(1) and h["families"]["chart"] == fam(2)        # the input is not changed
    assert (out["name"], out["status"], out["consecutive_failures"]) == ("nasdaq", "down", 3)
    assert out["breaker"] == out["families"]["chart"]["breaker"]                          # as health_state() derives it
    out = worker_mod.health_after_timeout({"families": {"chart": fam(5, True), "crumb": fam(0)}}, now)
    assert out["families"]["chart"] == fam(5, True) and out["families"]["crumb"]["consecutive_failures"] == 1
    assert out["families"]["crumb"]["breaker"]["open"] is False and out["name"] == "nasdaq"
    out = worker_mod.health_after_timeout({"name": "yahoo", "consecutive_failures": 2, "breaker": {"open": False}}, now)
    assert out["breaker"]["open"] is True and out["name"] == "nasdaq"                      # the flat format
    out = worker_mod.health_after_timeout(None, now)
    assert out["consecutive_failures"] == 1 and out["breaker"] == {}


def test_a_blind_timeout_raises_every_closed_family():
    """INT-4: without a live copy nothing tells which family hangs; dict order must not pick chart."""
    now = "2026-09-28T14:40:20Z"
    out = worker_mod.health_after_timeout({"families": {"chart": fam(0), "crumb": fam(0)}}, now, blind=True)
    assert [out["families"][k]["consecutive_failures"] for k in ("chart", "crumb")] == [1, 1]
    out = worker_mod.health_after_timeout({"families": {"chart": fam(2), "crumb": fam(2)}}, now, blind=True)
    assert all(f["breaker"]["open"] and f["breaker"]["opened_at"] == now for f in out["families"].values())
    out = worker_mod.health_after_timeout({"families": {"chart": fam(4, True), "crumb": fam(1)}}, now, blind=True)
    assert out["families"]["chart"] == fam(4, True) and out["families"]["crumb"]["consecutive_failures"] == 2
    out = worker_mod.health_after_timeout({"name": "yahoo", "consecutive_failures": 0, "breaker": {"open": False}}, now,
                                          blind=True)
    assert out["consecutive_failures"] == 1 and out["breaker"]["open"] is False             # the flat format


FAMILIES_OK = {**HEALTHY, "families": {"chart": fam(0), "crumb": fam(0)}}


def counts(run: SimpleNamespace) -> dict:
    return {k: (f["consecutive_failures"], f["breaker"]["open"]) for k, f in run.source_health["families"].items()}


@pytest.mark.parametrize("live", [None, {**FAMILIES_OK, "families": {"chart": fam(2), "crumb": fam(0)},
                                         HEALTH_REF: "worker-old/2026-09-28T14:30:00Z"}],
                         ids=["no live copy", "an earlier tick's live copy"])
def test_ticks_killed_before_any_call_count_on_both_families(radar_store, live):
    """INT-4 (SPEC 12.6): a crumb-only hang that kills every tick before a call finishes used to open the chart
    breaker first (dict order), sending healthy chart traffic to Nasdaq. Both families now count, and a live
    copy left by an earlier tick is no evidence about this one."""
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", state=PREV_STATE, live=live,
                          engine={"schema": 1, "source_health": FAMILIES_OK}, results={i: "timeout" for i in FIVE_MIN})
    run_until(w, clock, "2026-09-28T14:52:00Z")
    assert [counts(r) for r in fake.runs] == [{"chart": (n, n >= 3), "crumb": (n, n >= 3)} for n in range(4)]
    assert fake.runs[-1].source_health["name"] == "nasdaq"


def test_a_timeout_with_a_live_copy_still_raises_only_the_worst_family(radar_store):
    side = {**FAMILIES_OK, "families": {"chart": fam(0), "crumb": {**fam(1), "status": "down"}}}
    w, clock, _ = make(radar_store, "2026-09-28T14:34:00Z", state=PREV_STATE,
                       engine={"schema": 1, "source_health": FAMILIES_OK}, results={FIVE_MIN[0]: "timeout"},
                       on_run=lambda a, env: write_live(radar_store, env, a.tick_id, side))
    run_until(w, clock, "2026-09-28T14:39:00Z")
    h = radar_store.load_engine_doc()["source_health"]
    assert h["families"]["chart"] == fam(0) and h["families"]["crumb"]["consecutive_failures"] == 2


def test_the_worker_trips_the_breaker_at_the_fetchers_count():
    """The worker does not import fetch (numpy, curl_cffi), so it keeps its own copy of the trip count; the
    record it writes must restore into the real fetcher as an open breaker."""
    from radar import fetch
    assert worker_mod.BREAKER_TRIP == fetch.BREAKER_TRIP
    h = fetch.Fetcher(transport=object()).health_state()
    for _ in range(fetch.BREAKER_TRIP):
        assert fetch.Fetcher(health=h, transport=object()).health_state()["name"] == "yahoo"
        h = worker_mod.health_after_timeout(h, "2026-09-28T14:40:20Z")
    restored = fetch.Fetcher(health=h, transport=object()).health_state()
    assert restored["name"] == "nasdaq" and restored["consecutive_failures"] == fetch.BREAKER_TRIP
    assert restored["families"]["chart"]["breaker"]["opened_at"] == "2026-09-28T14:40:20Z"


# ---------------------------------------------------------------- alerts after each tick

def ev(ticker: str, type_: str, ts: str) -> dict:
    return event(ticker, type_, ts, 1)


def test_new_entries_after_the_cursor_are_dispatched_and_the_cursor_moves(radar_store):
    t1, t2 = "2026-09-28T14:35:00Z", "2026-09-28T14:40:00Z"
    events = {t1: [ev("NVDA", "ENTER", t1), ev("AAPL", "EXIT", t1)], t2: [ev("AMD", "ENTER", t2)]}
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", events=events)
    run_until(w, clock, "2026-09-28T14:47:00Z")
    assert fake.ids == [t1, t2, "2026-09-28T14:45:00Z"]
    assert w.dispatch_alerts.batches == [["20260928T1435Z-NVDA-ENTER-1"], ["20260928T1440Z-AMD-ENTER-1"]]
    assert radar_store.get_alert_cursor() == "20260928T1440Z-AMD-ENTER-1"


def test_events_up_to_the_cursor_are_never_sent_again(radar_store):
    t1 = "2026-09-28T14:35:00Z"
    radar_store.set_alert_cursor("20260928T1435Z-NVDA-ENTER-1")
    events = {t1: [ev("NVDA", "ENTER", t1), ev("AAPL", "ENTER", t1), ev("ZS", "ENTER", t1)]}
    w, clock, _ = make(radar_store, "2026-09-28T14:34:00Z", events=events)
    run_until(w, clock, "2026-09-28T14:39:00Z")
    assert w.dispatch_alerts.batches == [["20260928T1435Z-ZS-ENTER-1"]]
    assert radar_store.get_alert_cursor() == "20260928T1435Z-ZS-ENTER-1"


def test_stale_entries_are_not_pushed_but_the_cursor_moves_past_them(radar_store):
    """After an outage, "entered the radar" 45 minutes ago is not news: no alert, and no retry later."""
    t1 = "2026-09-28T14:35:00Z"
    events = {t1: [ev("OLD", "ENTER", "2026-09-28T13:50:00Z"), ev("NVDA", "EXIT", t1)]}
    w, clock, _ = make(radar_store, "2026-09-28T14:34:00Z", events=events)
    run_until(w, clock, "2026-09-28T14:39:00Z")
    assert w.dispatch_alerts.batches == [] and radar_store.get_alert_cursor() == "20260928T1435Z-NVDA-EXIT-1"


def test_a_failed_dispatch_is_retried_on_the_next_tick_and_never_stops_the_radar(radar_store):
    t1, t2 = "2026-09-28T14:35:00Z", "2026-09-28T14:40:00Z"
    events = {t1: [ev("NVDA", "ENTER", t1)], t2: [ev("AMD", "ENTER", t2)]}
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", events=events, alerts=Alerts(fail=1))
    run_until(w, clock, "2026-09-28T14:43:00Z")
    assert fake.ids == [t1, t2]
    assert w.dispatch_alerts.batches == [["20260928T1435Z-NVDA-ENTER-1", "20260928T1440Z-AMD-ENTER-1"]]
    assert radar_store.get_alert_cursor() == "20260928T1440Z-AMD-ENTER-1"


def test_no_alerts_after_a_warmup_or_a_failed_tick(radar_store):
    t1 = "2026-09-28T13:35:00Z"
    radar_store.commit_tick(state={"schema": 1}, engine_doc={"schema": 1}, member_rows=[],
                            events=[ev("NVDA", "ENTER", "2026-09-28T13:05:00Z")],
                            scan_row=scan_log_row("2026-09-28T13:05:00Z", "2026-09-28T13:05:50Z", run_id="x"))
    w, clock, fake = make(radar_store, "2026-09-28T13:09:00Z", results={t1: "error"})
    run_until(w, clock, "2026-09-28T13:39:00Z")
    assert fake.ids == ["2026-09-28T13:10:00Z", t1]
    assert w.dispatch_alerts.batches == [] and radar_store.get_alert_cursor() is None


def test_store_errors_in_the_alert_path_never_stop_the_radar(radar_store):
    class Broken:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def recent_events(self, **kw):
            raise RuntimeError("database is gone")

    w, clock, fake = make(Broken(radar_store), "2026-09-28T14:34:00Z")
    run_until(w, clock, "2026-09-28T14:43:00Z")
    assert fake.ids == ["2026-09-28T14:35:00Z", "2026-09-28T14:40:00Z"]


def test_the_default_dispatch_is_services_alerts(monkeypatch):
    import services.alerts
    seen = []

    async def dispatch(events):
        seen.append(events)
        return 3

    monkeypatch.setattr(services.alerts, "dispatch_radar_entries", dispatch)
    events = [ev("NVDA", "ENTER", "2026-09-28T14:35:00Z")]
    assert worker_mod.dispatch_alerts(events) == 3 and seen == [events]
    assert worker_mod.dispatch_alerts(events) == 3                      # a second event loop works as well


# ---------------------------------------------------------------- pipeline_run_log

def test_every_tick_gets_a_pipeline_run_log_row(radar_store):
    run_log = RunLog()
    w, clock, fake = make(radar_store, "2026-09-28T13:09:00Z", run_log=run_log,
                          results={"2026-09-28T13:40:00Z": "timeout"})
    run_until(w, clock, "2026-09-28T13:44:00Z")
    assert [(r["phase"], r["status"], r["meta"]["kind"], r["meta"]["tick"]) for r in run_log.rows] == [
        ("radar_tick", "SUCCESS", "warmup", "2026-09-28T13:10:00Z"),
        ("radar_tick", "SUCCESS", "tick", "2026-09-28T13:35:00Z"),
        ("radar_tick", "FAILED", "tick", "2026-09-28T13:40:00Z")]
    assert run_log.rows[2]["error"] == "timeout: boom" and run_log.rows[1]["meta"]["worker"] == w.run_id
    assert run_log.rows[1]["meta"]["status"] == "ok" and run_log.rows[1]["meta"]["duration_ms"] == 20_000


def test_a_broken_run_log_never_stops_the_radar(radar_store):
    w, clock, fake = make(radar_store, "2026-09-28T14:34:00Z", run_log=RunLog(fail=True))
    run_until(w, clock, "2026-09-28T14:43:00Z")
    assert fake.ids == ["2026-09-28T14:35:00Z", "2026-09-28T14:40:00Z"] and len(scan_rows(radar_store)) == 2


def test_the_default_run_log_is_services_run_log_on_a_fresh_loop_each_call(monkeypatch):
    """services.run_log is async on the backend's shared asyncpg engine; each call from this sync process
    gets its own event loop and a fresh pool, so consecutive calls never reuse a connection of a dead loop."""
    import asyncio

    from services import run_log
    calls, loops = [], []                  # the loops themselves: an id() can be reused once one is gone

    async def start(phase, user_id=None, meta=None):
        loops.append(asyncio.get_running_loop())
        calls.append(("start", phase, meta))
        return 41

    async def finish(run_id, status="SUCCESS", error_message=None, meta=None):
        loops.append(asyncio.get_running_loop())
        calls.append(("finish", run_id, status, error_message, meta))

    monkeypatch.setattr(run_log, "record_run_start", start)
    monkeypatch.setattr(run_log, "record_run_finish", finish)
    log = worker_mod.PipelineRunLog()
    assert log.start("radar_tick", {"tick": "t"}) == 41
    log.finish(41, ok=False, error="timeout: boom", meta={"k": 1})
    log.finish(None, ok=True, error=None, meta={})                      # no row was started: nothing to finish
    assert calls == [("start", "radar_tick", {"tick": "t"}), ("finish", 41, "FAILED", "timeout: boom", {"k": 1})]
    assert len(loops) == 2 and loops[0] is not loops[1] and all(lp.is_closed() for lp in loops)


def test_a_hung_async_call_is_cut_by_its_timeout():
    import asyncio

    async def hang():
        await asyncio.sleep(30)

    async def noop():
        return "warm"

    # Warm-up outside the timer: the first run_async call imports db.connection (asyncpg plus the
    # SQLAlchemy async stack), which alone can take seconds on a busy machine. Only the cut is timed.
    assert worker_mod.run_async(noop, 30) == "warm"
    t0 = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        worker_mod.run_async(hang, 0.2)
    assert time.monotonic() - t0 < 10        # far below the 30 s hang: the timeout cut the call


# ---------------------------------------------------------------- the tick subprocess

def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_subprocess_timeout():
    res = worker_mod.run_tick_subprocess(py("import time; time.sleep(30)"), 0.5, dict(os.environ))
    assert res["status"] == "timeout" and res["duration_ms"] < 20_000


def test_subprocess_result_is_the_last_json_line():
    res = worker_mod.run_tick_subprocess(
        py("print('progress'); print('{\"status\": \"ok\", \"members\": 3}'); print('bye'); print('[1]')"),
        30, dict(os.environ))
    assert res["status"] == "ok" and res["members"] == 3 and res["duration_ms"] >= 0


def test_subprocess_stop_request_terminates_the_tick():
    t0 = time.monotonic()
    res = worker_mod.run_tick_subprocess(py("import time; time.sleep(60)"), 30, dict(os.environ),
                                         stop=lambda: time.monotonic() - t0 > 0.3)
    assert res["status"] == "stopped" and res["duration_ms"] < 5_000


def test_subprocess_crash_is_an_error():
    res = worker_mod.run_tick_subprocess(py("import sys; print('{\"status\": \"ok\"}'); sys.exit(3)"), 30, dict(os.environ))
    assert res["status"] == "error" and res["message"] == "the scan exited with code 3"
    res = worker_mod.run_tick_subprocess(py("pass"), 30, dict(os.environ))
    assert res["status"] == "error" and res["message"] == "the scan printed no result"


def test_a_stop_request_ends_a_running_tick_within_seconds(radar_store, monkeypatch):
    """RT-2: on a SIGTERM (docker stop) the worker must not wait 270 s for its tick, and must leave no tick
    running behind it."""
    procs = []

    class Recording(subprocess.Popen):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            procs.append(self)

    monkeypatch.setattr(worker_mod.subprocess, "Popen", Recording)
    w = worker_mod.Worker(radar_store, env=dict(os.environ), run_log=RunLog(), dispatch_alerts=Alerts(),
                          tick_cmd=(sys.executable, "-c", "import time; time.sleep(60)"))
    slot = worker_mod.Slot(int(at("2026-09-28T14:35:00Z")), int(at("2026-09-28T14:35:50Z")))
    timer = threading.Timer(0.5, w.request_stop, args=(signal.SIGTERM, None))
    timer.start()
    t0 = time.monotonic()
    try:
        w._run(worker_mod.Job(slot.run_at, slot, MON, following=None))
    finally:
        timer.cancel()
    assert time.monotonic() - t0 < 5
    (proc,) = procs
    assert proc.poll() is not None and scan_rows(radar_store) == [] and radar_store.load_state() == {}


# ---------------------------------------------------------------- real subprocess ticks on a shared SQLite file

FAKE_TICK = """
import argparse, json, os, time
from radar import tick
from radar.store import RadarStore
ap = argparse.ArgumentParser()
ap.add_argument("--tick-id")
for flag in ("--warmup", "--final", "--ignore-calendar"):
    ap.add_argument(flag, action="store_true")
a = ap.parse_args()
store = RadarStore(os.environ["RADAR_TEST_DB"])
ctx = tick.read_context()
engine = store.load_engine_doc() or {"schema": 1}
engine.update(loop=ctx["loop"], ops={**(engine.get("ops") or {}), **ctx["ops"]})
day, hm = a.tick_id[:10], a.tick_id[11:13] + a.tick_id[14:16]
events = [] if a.warmup else [{"v": 1, "id": day.replace("-", "") + "T" + hm + "Z-NVDA-ENTER-" + hm, "ts": a.tick_id,
          "session": day, "slot": 1, "ticker": "NVDA", "type": "ENTER", "dir": "up", "intensity": 70}]
row = tick.scan_log_row(a.tick_id, tick.iso(time.time()), session=day, phase="regular", status="ok")
state = {"schema": 1, "tick_id": a.tick_id, "final": a.final, "ticks_today": ctx["loop"]["ticks_today"]}
store.commit_tick(state=state, engine_doc=engine, member_rows=[], events=events, scan_row=row)
print("fetching...")
print(json.dumps({"status": "ok", "message": "", "members": 0, "entered": ["NVDA"], "exited": [], "ms": {"write": 3}}))
"""


def test_worker_with_subprocess_ticks_on_a_shared_database(radar_db_url, radar_store):
    clock = Clock("2026-09-28T19:50:00Z")
    path = os.pathsep.join(p for p in (str(BACKEND), os.environ.get("PYTHONPATH")) if p)
    env = {**os.environ, "PYTHONPATH": path, "RADAR_TEST_DB": radar_db_url}
    alerts = Alerts()
    w = worker_mod.Worker(radar_store, clock=clock, sleep=clock.sleep, env=env, run_log=RunLog(),
                          dispatch_alerts=alerts, housekeeping=Housekeeping(clock),
                          tick_cmd=(sys.executable, "-c", FAKE_TICK))
    run_until(w, clock, "2026-09-28T20:10:00Z")
    rows = scan_rows(radar_store)
    assert [r["tick"] for r in rows] == ["2026-09-28T19:50:00Z", "2026-09-28T19:55:00Z", "2026-09-28T20:00:00Z"]
    assert all(r["run_id"] == w.run_id for r in rows)
    state, engine = radar_store.load_state(), radar_store.load_engine_doc()
    assert state["tick_id"] == "2026-09-28T20:00:00Z" and state["final"] is True and state["ticks_today"] == 3
    assert engine["loop"]["last_tick"] == "2026-09-28T20:00:00Z" and engine["loop"]["write_ms"] == [3, 3]
    assert [len(b) for b in alerts.batches] == [1] * 3
    assert radar_store.get_alert_cursor() == "20260928T2000Z-NVDA-ENTER-2000"
