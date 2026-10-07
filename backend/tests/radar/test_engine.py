"""Engine state machine (signal-model.md 1-10, SPEC 4.5 and 6) on synthetic sessions."""
from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from radar.calendar_nyse import Session, previous_sessions, session_for
from radar.config import PARAMS
from radar.engine import Engine, TickOutput, iso
from radar.types import Bars, Quote
from tests.radar.test_baselines import pack_for, synth_market

GRACE = PARAMS["session"]["bar_final_grace_s"]
SIGNALS = {"z3", "z6", "zday", "rvol3", "rvolc", "dvwap", "er6", "acc"}
EVENT_KEYS = {"v", "id", "ts", "session", "slot", "ticker", "type", "dir", "price", "intensity", "reason", "detail",
              "episode", "held_min", "move_since_entry_pct", "late", "signals", "params_version"}
ROW_KEYS = {"v", "tick", "session", "slot", "ticker", "role", "dir", "state", "price", "chg_day_pct", "chg_5m_pct",
            "move_since_entry_pct", "vol_5m", "intensity", "signals"}
MEMBER_KEYS = {"ticker", "name", "sector", "direction", "state", "late", "entered_at", "entry_price", "last_price",
               "last_bar_at", "minutes_on_radar", "move_since_entry_pct", "peak_since_entry_pct", "chg_5m_pct",
               "chg_15m_pct", "chg_30m_pct", "chg_day_pct", "rvol", "rvol_day", "vwap_dist_pct", "z15", "z30", "zday",
               "intensity", "reasons", "soft_fails", "episode", "spark", "giveback_pct", "mins_since_extreme"}
HEATING_KEYS = {"ticker", "name", "direction", "since", "price", "chg_day_pct", "intensity", "reasons"}
EXIT_KEYS = {"ticker", "name", "direction", "entered_at", "exited_at", "minutes_on_radar", "move_since_entry_pct",
             "exit_reason", "exit_detail"}
EXIT_REASONS = {"FADE", "DRY", "STALL", "REVERSAL", "GIVEBACK", "VWAP_CROSS", "SESSION_END", "HALT_LONG", "DATA_STALE",
                "DISPLACED"}
ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

NOISE = {"SPY": {"px": 600.0, "vol": 400_000, "sigma": 0.0005}, "QQQ": {"px": 500.0, "vol": 300_000},
         "IWM": {"px": 220.0, "vol": 200_000}, **{f"N{i}": {"px": 40.0 + 5 * i} for i in range(1, 7)}}


def racer(n: int = 78, start: int = 20, up: int = 12, step: float = 0.004, down: int = 4, down_step: float = -0.008,
          gap: float = 0.045, jump: float = 0.0, base_v: float = 3.0, burst_v: float = 6.0,
          ret_patch: dict[int, float] | None = None, **extra) -> dict:
    """An 'in play' name (gap + busy day) that climbs from `start`, then drops for `down` bars.

    `extra` passes synth_market's "drop" / "zero_vol" through."""
    ret = np.zeros(n)
    ret[start:start + up] = step
    ret[start] += jump
    ret[start + up:start + up + down] = down_step
    for j, r in (ret_patch or {}).items():
        ret[j] += r
    vm = np.full(n, base_v)
    vm[start:start + up + down] = burst_v
    return {"gap": gap, "ret": ret, "vmult": vm, **extra}


def build(custom: dict[str, dict], *, day: date = date(2026, 8, 31), meta: dict | None = None, seed: int = 7,
          spy: dict | None = None, extra: dict | None = None):
    session = session_for(day)
    days = [*previous_sessions(day, 20), session]
    spec = {**NOISE, **{s: {"px": 50.0} for s in custom}, **(extra or {})}
    key = day.isoformat()
    cust = {(key, s): c for s, c in custom.items()}
    if spy:
        cust[(key, "SPY")] = spy
    bars, daily = synth_market(days, spec, seed=seed, custom=cust)
    pack = pack_for(bars, daily, session, meta)
    return session, pack, bars[key]


def now_after(session: Session, k: int) -> int:
    return session.slot_start(k) + 300 + GRACE


def upto(bars: dict[str, Bars], session: Session, k: int) -> dict[str, Bars]:
    """A live fetch once bar k is final: every bar through the in-progress bar k+1."""
    cut = session.slot_start(k + 1)
    return {s: Bars(s, *(getattr(b, a)[b.ts <= cut] for a in ("ts", "o", "h", "l", "c", "v"))) for s, b in bars.items()}


def feed_at(bars: dict[str, Bars], session: Session, k: int, *, vscale: float | None = None,
            provisional: set[str] | frozenset[str] = frozenset()) -> dict[str, Bars]:
    """`upto`, with volumes scaled by `vscale` (a fallback source on another volume basis) and, for `provisional`
    symbols, no row k+1 yet while bar k may still be revised (Bars.provisional_last)."""
    out = {}
    for s, b in upto(bars, session, k).items():
        keep = b.ts <= session.slot_start(k) if s in provisional else np.ones(len(b), dtype=bool)
        v = b.v[keep] if vscale is None else np.round(b.v[keep] * vscale).astype(np.int64)
        out[s] = Bars(s, b.ts[keep], b.o[keep], b.h[keep], b.l[keep], b.c[keep], v, provisional_last=s in provisional)
    return out


def quotes_at(bars: dict[str, Bars], session: Session, k: int) -> dict[str, Quote]:
    out = {}
    for s, b in bars.items():
        m = b.ts <= session.slot_start(k)
        if m.any():
            out[s] = Quote(s, price=float(b.c[m][-1]), day_volume=int(b.v[m].sum()))
    return out


def drive(engine: Engine, bars: dict[str, Bars], session: Session, slots, *, stage_a: bool = False,
          drop: set[str] = frozenset(), halted: set[str] = frozenset(), degraded: bool = False,
          vscale: float | None = None, provisional: set[str] = frozenset()) -> list[TickOutput]:
    outs = []
    for k in slots:
        now = now_after(session, k)
        feed = {s: b for s, b in feed_at(bars, session, k, vscale=vscale, provisional=provisional).items()
                if s not in drop}
        if stage_a:
            wanted = set(engine.stage_a(quotes_at(bars, session, k), now))
            feed = {s: b for s, b in feed.items() if s in wanted}
        outs.append(engine.step(feed, now, halted=halted, degraded_volume=degraded))
    return outs


def events(outs: list[TickOutput], ticker: str | None = None) -> list[dict]:
    return [e for o in outs for e in o.events if ticker is None or e["ticker"] == ticker]


def rows(outs: list[TickOutput], ticker: str) -> list[dict]:
    return [r for o in outs for r in o.member_rows if r["ticker"] == ticker]


def as_json(outs: list[TickOutput]) -> str:
    return json.dumps([o.__dict__ for o in outs], sort_keys=True)


@pytest.fixture(scope="module")
def run_day():
    session, pack, bars = build({"RUN": racer(), "LATE": racer(start=58, up=20, step=0.003, down=0)})
    engine = Engine(PARAMS, pack, session)
    outs = drive(engine, bars, session, range(session.n_slots), stage_a=True)
    return session, pack, bars, outs, engine


def test_racer_heats_confirms_enters_and_exits(run_day):
    session, _, bars, outs, _ = run_day
    ev = events(outs, "RUN")
    assert [e["type"] for e in ev] == ["ENTER", "EXIT"]
    enter, leave = ev
    k = enter["slot"]
    assert 21 <= k <= 24 and leave["reason"] == "REVERSAL" and leave["slot"] > k
    heat = [r for r in rows(outs, "RUN") if r["role"] == "heating"]
    assert [r["slot"] for r in heat] == [k - 1] and heat[0]["state"] == "heating"
    for e in ev:
        assert set(e) == EVENT_KEYS and set(e["signals"]) == SIGNALS
        assert e["ts"] == iso(session.slot_start(e["slot"]) + 300) and e["session"] == "2026-08-31"
        assert e["dir"] == "up" and e["episode"] == 1 and e["late"] is False and len(e["detail"]) <= 160
        assert e["params_version"] == PARAMS["params_version"]
    assert enter["id"] == f"{enter['ts'][:4]}{enter['ts'][5:7]}{enter['ts'][8:13]}{enter['ts'][14:16]}Z-RUN-ENTER-1"
    assert enter["reason"] == "ENTRY" and enter["held_min"] is None and enter["move_since_entry_pct"] is None
    assert enter["price"] == pytest.approx(float(bars["RUN"].c[k]), abs=1e-4)
    assert leave["held_min"] == 5 * (leave["slot"] - k)
    assert leave["move_since_entry_pct"] == pytest.approx(100 * (leave["price"] / enter["price"] - 1), abs=0.01)
    assert "15 min" in leave["detail"]
    member = [r for r in rows(outs, "RUN") if r["role"] == "member"]
    assert [r["slot"] for r in member] == list(range(k, leave["slot"]))
    assert all(set(r) == ROW_KEYS and r["state"] in ("racing", "cooling") for r in member)
    assert member[0]["move_since_entry_pct"] == 0.0 and member[0]["tick"] == enter["ts"]


def test_snapshot_shapes(run_day):
    session, _, _, outs, _ = run_day
    k = events(outs, "RUN")[0]["slot"]
    heat_snap = outs[k - 1].snapshot
    assert set(heat_snap) == {"members", "heating", "recent_exits", "market", "sector_banners", "counts"}
    h = next(x for x in heat_snap["heating"] if x["ticker"] == "RUN")
    assert set(h) == HEATING_KEYS and h["direction"] == "up" and ISO_Z.match(h["since"])
    snap = outs[k + 2].snapshot
    m = next(x for x in snap["members"] if x["ticker"] == "RUN")
    assert set(m) == MEMBER_KEYS
    assert m["state"] in ("racing", "cooling") and m["direction"] == "up" and m["late"] is False
    assert m["minutes_on_radar"] == 10 and m["entered_at"] == iso(session.slot_start(k) + 300)
    assert m["last_bar_at"] == iso(session.slot_start(k + 2) + 300)
    assert isinstance(m["intensity"], int) and 0 <= m["intensity"] <= 100
    assert 1 <= len(m["reasons"]) <= 3 and all(isinstance(r, str) and r for r in m["reasons"])
    assert any("σ vs market" in r for r in m["reasons"]) and any("normal for this time" in r for r in m["reasons"])
    sp = m["spark"]
    assert sp["step_s"] == 300 and sp["entry_i"] == 6 and len(sp["p"]) == 9 and ISO_Z.match(sp["t0"])
    assert sp["p"][sp["entry_i"]] == m["entry_price"] and sp["p"][-1] == m["last_price"]
    assert m["peak_since_entry_pct"] >= m["move_since_entry_pct"] > 0
    mk = snap["market"]
    assert set(mk) == {"mode", "dir", "spy_chg_day_pct", "spy_z30", "breadth30"} and mk["mode"] == "normal"
    counts = outs[-1].snapshot["counts"]
    assert set(counts) == {"universe", "stage_b", "members", "heating", "entered_today", "exited_today"}
    assert counts["entered_today"] == counts["exited_today"] == len(events(outs)) // 2
    assert counts["universe"] == sum(b.eligible for b in run_day[1].symbols.values())
    exits = outs[-1].snapshot["recent_exits"]
    assert [x["ticker"] for x in exits] == ["LATE", "RUN"]
    assert all(set(x) == EXIT_KEYS and x["exit_reason"] in EXIT_REASONS for x in exits)
    json.dumps(outs[-1].snapshot, allow_nan=False)


@pytest.mark.parametrize("d, peak, base, px, expected", [
    (1, 110.0, 100.0, 103.0, 70.0), (1, 110.0, 100.0, 110.0, 0.0), (1, 110.0, 100.0, 95.0, 150.0),
    (-1, 90.0, 100.0, 97.0, 70.0), (-1, 90.0, 100.0, 91.0, 10.0),
    (1, 100.0, 100.0, 99.0, 0.0),                       # no progress yet
])
def test_giveback_pct_is_the_giveback_rules_measure(run_day, d, peak, base, px, expected):
    """The display gauge and the GIVEBACK exit read the same number: at or above hard_exit.giveback the rule
    fires, just below it does not."""
    engine = run_day[4]
    m = {"dir": d, "peak": peak, "base": base, "stale": 0, "dwell": 5, "soft": 0, "last_ext": 30}
    assert Engine._giveback_pct(m, px) == pytest.approx(expected)
    f = {"z3": np.array([0.0]), "z6": np.array([2.0 * d]), "dvwap": np.array([0.01 * d]), "rvol3": np.array([1.0])}
    reason, info = engine._exit_reason(dict(m), 0, 30, px, f, False)
    limit = 100.0 * PARAMS["hard_exit"]["giveback"]
    if expected >= limit:
        assert reason == "GIVEBACK" and info["gb"] == pytest.approx(expected)
    else:
        assert reason is None
    # a hair under the limit: no exit, and the gauge shows just under 70
    nearly = peak - d * (limit / 100.0 - 0.002) * abs(peak - base)
    if peak != base:
        assert engine._exit_reason(dict(m), 0, 30, nearly, f, False)[0] is None
        assert Engine._giveback_pct(m, nearly) < limit


def test_snapshot_exit_gauges_follow_the_member_state():
    """Every member on every snapshot: giveback_pct is the GIVEBACK rule's measure on its state (so below the
    exit limit, or it would have left) and mins_since_extreme the STALL clock."""
    session, pack, bars = build({"RUN": racer(), "SLOW": racer(start=30, up=16, step=0.003, down=0)})
    engine = Engine(PARAMS, pack, session)
    limit = 100.0 * PARAMS["hard_exit"]["giveback"]
    seen = []
    for k in range(session.n_slots):
        out = drive(engine, bars, session, [k], stage_a=True)[0]
        for mem in out.snapshot["members"]:
            m = engine.st["members"][mem["ticker"]]
            assert mem["giveback_pct"] == Engine._giveback_pct(m, m["view"]["price"])
            assert 0.0 <= mem["giveback_pct"] < limit
            assert mem["mins_since_extreme"] == 5 * (engine.st["last_slot"] - m["last_ext"]) >= 0
            assert mem["mins_since_extreme"] % 5 == 0
            seen.append(mem)
    assert seen and any(x["giveback_pct"] > 0 for x in seen) and any(x["mins_since_extreme"] > 0 for x in seen)


def test_session_end_clears_the_radar(run_day):
    _, _, _, outs, _ = run_day
    ev = events(outs, "LATE")
    assert [e["type"] for e in ev] == ["ENTER", "EXIT"]
    assert ev[0]["slot"] <= PARAMS["session"]["last_confirm_slot"]
    assert ev[1]["slot"] == 76 and ev[1]["reason"] == "SESSION_END"
    assert [o.processed_slots for o in outs] == [[k] for k in range(78)]
    assert not outs[76].snapshot["members"] and not outs[77].snapshot["members"]
    assert not [e for e in events(outs) if e["type"] == "ENTER" and e["slot"] > PARAMS["session"]["last_confirm_slot"]]


def test_state_is_json_small_and_resumes_identically(run_day):
    session, pack, bars, outs, engine = run_day
    state = engine.state_dict()
    text = json.dumps(state, allow_nan=False)
    assert len(text.encode()) < 200 * 1024
    assert len(state["ring"]["px"]) <= 7
    first = Engine(PARAMS, pack, session)
    a = drive(first, bars, session, range(31), stage_a=True)
    second = Engine(PARAMS, pack, session, state=json.loads(json.dumps(first.state_dict())))
    b = drive(second, bars, session, range(31, session.n_slots), stage_a=True)
    assert as_json(a + b) == as_json(outs)
    assert json.dumps(second.state_dict(), sort_keys=True) == json.dumps(state, sort_keys=True)


def test_state_from_another_session_is_ignored(run_day):
    _, pack, _, _, engine = run_day
    other = Engine(PARAMS, pack, session_for(date(2026, 9, 1)), state=engine.state_dict())
    st = other.state_dict()
    assert st["session"] == "2026-09-01" and st["last_slot"] == -1 and not st["members"] and not st["exits"]


def test_same_inputs_same_outputs():
    session, pack, bars = build({"RUN": racer()})
    a = drive(Engine(PARAMS, pack, session), bars, session, range(40))
    b = drive(Engine(PARAMS, pack, session), bars, session, range(40))
    assert as_json(a) == as_json(b)


def test_catch_up_matches_live_and_flags_late():
    session, pack, bars = build({"RUN": racer()})
    live = drive(Engine(PARAMS, pack, session), bars, session, range(50))
    eng = Engine(PARAMS, pack, session)
    late = eng.step(upto(bars, session, 40), now_after(session, 40))
    assert late.processed_slots == list(range(41))
    rest = drive(eng, bars, session, range(41, 50))
    strip = lambda ev: [{k: v for k, v in e.items() if k != "late"} for e in ev]
    assert strip(late.events + events(rest)) == strip(events(live))
    enter = next(e for e in late.events if e["type"] == "ENTER")
    assert enter["late"] is True and not any(e["late"] for e in events(live))
    assert eng.step(upto(bars, session, 49), now_after(session, 49)).processed_slots == []


def test_waits_for_spy_before_processing():
    session, pack, bars = build({"RUN": racer()})
    eng = Engine(PARAMS, pack, session)
    out = eng.step({s: b for s, b in upto(bars, session, 5).items() if s != "SPY"}, now_after(session, 5))
    assert out.processed_slots == [] and eng.state_dict()["last_slot"] == -1
    stale_spy = {**upto(bars, session, 5), "SPY": upto(bars, session, 2)["SPY"]}
    assert eng.step(stale_spy, now_after(session, 5)).processed_slots == [0, 1, 2, 3]
    assert eng.step(upto(bars, session, 5), now_after(session, 5)).processed_slots == [4, 5]


def test_stage_a_selects_in_play_names_members_and_references():
    session, pack, bars = build({"RUN": racer()})
    eng = Engine(PARAMS, pack, session)
    assert set(eng.stage_a({}, session.open_epoch + 60)) == {"SPY", "QQQ", "IWM"}
    q = quotes_at(bars, session, 10)
    picked = set(eng.stage_a(q, now_after(session, 10)))
    assert {"SPY", "QQQ", "IWM", "RUN"} <= picked and not picked & {f"N{i}" for i in range(1, 7)}
    eng.st["members"]["N1"] = {}
    eng.st["heating"]["N2"] = {}
    picked = set(eng.stage_a({s: x for s, x in q.items() if s != "N1"}, now_after(session, 10)))
    assert {"N1", "N2"} <= picked
    few = {s: q[s] for s in ("SPY", "RUN")}
    assert set(eng.stage_a(few, now_after(session, 10))) >= {s for s, b in pack.symbols.items() if b.eligible}


def test_stage_a_never_widens_while_quotes_are_down():
    """E2E-1: with the quote call down, Stage B is members, heating names and the references only."""
    session, pack, bars = build({"RUN": racer()})
    eng = Engine(PARAMS, pack, session)
    eng.st["members"]["N1"] = {}
    eng.st["heating"]["N2"] = {}
    want = ["IWM", "N1", "N2", "QQQ", "SPY"]
    q = quotes_at(bars, session, 10)
    for quotes in ({}, {s: q[s] for s in ("SPY", "RUN")}, q):
        assert eng.stage_a(quotes, now_after(session, 10), quotes_ok=False) == want
    assert "RUN" in eng.stage_a(q, now_after(session, 10), quotes_ok=True)


def test_missing_bars_never_evict_a_member():
    session, pack, bars = build({"RUN": racer()})
    eng = Engine(PARAMS, pack, session)
    outs = drive(eng, bars, session, range(26))
    assert "RUN" in eng.state_dict()["members"]
    outs += drive(eng, bars, session, range(26, 78), drop={"RUN"})
    ev = events(outs, "RUN")
    assert [e["type"] for e in ev] == ["ENTER", "EXIT"] and ev[1]["reason"] == "SESSION_END" and ev[1]["slot"] == 76
    assert len({r["price"] for r in rows(outs, "RUN") if r["slot"] >= 26}) == 1


def test_names_missing_from_a_rebuilt_pack_never_raise():
    """ENG-3: a mid-session pack without a member or heating name (params bump, lost extra pack).

    Heating is dropped, the member stays frozen on its stored name and sector and exits at SESSION_END. A name of
    the same sector admitted later counts the frozen member on its stored sector."""
    meta = {"RUN": {"name": "Run Corp", "sector": "Industrials"}, "LATE": {"name": "Late Co", "sector": "Industrials"}}
    session, pack, bars = build({"RUN": racer(up=30, down=0), "LATE": racer(start=34, up=14, down=0)}, meta=meta)
    eng = Engine(PARAMS, pack, session)
    h = [r["slot"] for r in rows(drive(eng, bars, session, range(26)), "RUN") if r["role"] == "heating"][0]
    state = eng.state_dict()
    run = state["members"]["RUN"]
    assert (run["name"], run["sector"]) == ("Run Corp", "Industrials")
    probe = Engine(PARAMS, pack, session)
    drive(probe, bars, session, range(h + 1))
    hot = probe.state_dict()["heating"]["RUN"]
    assert (hot["name"], hot["sector"]) == ("Run Corp", "Industrials")
    state["heating"]["DYN"] = {"dir": 1, "heat_px": 50.0, "heat_slot": 25, "name": "Dyn Inc", "sector": "Energy",
                               "view": copy.deepcopy(run["view"])}
    smaller = replace(pack, symbols={s: b for s, b in pack.symbols.items() if s != "RUN"})
    again = Engine(PARAMS, smaller, session, state=state)
    assert "DYN" not in again.state_dict()["heating"] and "RUN" in again.state_dict()["members"]
    outs = drive(again, bars, session, range(26, 78), stage_a=True)
    m = next(x for x in outs[0].snapshot["members"] if x["ticker"] == "RUN")
    assert (m["name"], m["sector"], m["last_price"]) == ("Run Corp", "Industrials", round(run["view"]["price"], 4))
    ev = events(outs, "RUN")
    assert [(e["type"], e["reason"], e["slot"]) for e in ev] == [("EXIT", "SESSION_END", 76)]
    assert outs[-1].snapshot["recent_exits"][0]["name"] == "Run Corp"
    late = events(outs, "LATE")[0]                               # admitted through the sector count
    assert (late["type"], late["reason"]) == ("ENTER", "ENTRY") and late["slot"] > 26
    assert sorted((x["ticker"], x["sector"]) for x in outs[late["slot"] - 26].snapshot["members"]) == [
        ("LATE", "Industrials"), ("RUN", "Industrials")]
    old = copy.deepcopy(state)                                   # engine.json written before names were stored
    del old["members"]["RUN"]["name"], old["members"]["RUN"]["sector"]
    for p, want in ((pack, ("Run Corp", "Industrials")), (smaller, (None, "Unknown"))):
        snap = Engine(PARAMS, p, session, state=old).snapshot()
        assert (snap["members"][0]["name"], snap["members"][0]["sector"]) == want


def test_fallback_volume_makes_no_entries_and_suspends_dry():
    """DATA-6: while the bars come from the fallback source (volume ~30% low or worse), nothing new heats or
    enters and DRY never counts; members still exit on price rules."""
    session, pack, bars = build({"RUN": racer(), "LATE": racer(start=40, up=12, down=0)})
    outs = {}
    for degraded, vscale in ((True, 0.05), (False, 0.05), (True, None), (False, None)):
        eng = Engine(PARAMS, pack, session)
        outs[degraded, vscale] = (drive(eng, bars, session, range(25))
                                  + drive(eng, bars, session, range(25, 60), degraded=degraded, vscale=vscale))
    reason = lambda key, t: [(e["type"], e["reason"]) for e in events(outs[key], t)]
    assert reason((False, 0.05), "RUN")[-1] == ("EXIT", "DRY")             # the bias alone would evict it
    assert reason((True, 0.05), "RUN") == [("ENTER", "ENTRY"), ("EXIT", "REVERSAL")]
    assert events(outs[True, 0.05], "RUN")[1]["slot"] == events(outs[False, None], "RUN")[1]["slot"]
    assert reason((False, None), "LATE") == [("ENTER", "ENTRY")]
    assert not rows(outs[True, None], "LATE") and not events(outs[True, None], "LATE")


def test_provisional_bar_blocks_heating_and_entry():
    """DATA-4: a newest bar still being revised (no row k+1 yet) cannot heat, confirm or enter at slot k."""
    session, pack, bars = build({"RUN": racer()})
    clean = drive(Engine(PARAMS, pack, session), bars, session, range(40))
    heat = [r["slot"] for r in rows(clean, "RUN") if r["role"] == "heating"]
    enter = events(clean, "RUN")[0]["slot"]
    eng = Engine(PARAMS, pack, session)
    held = (drive(eng, bars, session, range(heat[0] - 2))
            + drive(eng, bars, session, range(heat[0] - 2, enter + 2), provisional={"RUN"})
            + drive(eng, bars, session, range(enter + 2, 40)))
    assert not [r for r in rows(held, "RUN") if r["slot"] <= enter + 1]
    assert events(held, "RUN")[0]["slot"] > enter + 2
    confirm = Engine(PARAMS, pack, session)                       # heated on a settled bar, confirm bar provisional
    late = (drive(confirm, bars, session, range(enter)) + drive(confirm, bars, session, [enter], provisional={"RUN"})
            + drive(confirm, bars, session, range(enter + 1, 40)))
    assert [r["slot"] for r in rows(late, "RUN") if r["role"] == "heating"][0] == heat[0]
    assert events(late, "RUN")[0]["slot"] > enter
    flagged = {}                                                   # the flag on an older row changes nothing
    eng = Engine(PARAMS, pack, session)
    for k in range(40):
        feed = upto(bars, session, k)
        b = feed["RUN"]
        feed["RUN"] = Bars("RUN", b.ts, b.o, b.h, b.l, b.c, b.v, provisional_last=True)
        flagged[k] = eng.step(feed, now_after(session, k))
    assert as_json(list(flagged.values())) == as_json(clean)


def test_provisional_bar_counts_no_dry_soft_fail():
    session, pack, bars = build({"RUN": racer()})
    outs = {}
    for prov in (frozenset(), {"RUN"}):
        eng = Engine(PARAMS, pack, session)
        outs[bool(prov)] = drive(eng, bars, session, range(25)) + drive(eng, bars, session, range(25, 31),
                                                                       vscale=0.05, provisional=prov)
        if prov:
            assert eng.state_dict()["members"]["RUN"]["soft"] == 0
    assert events(outs[False], "RUN")[-1]["reason"] == "DRY"
    assert [e["type"] for e in events(outs[True], "RUN")] == ["ENTER"]


def plateau(**extra) -> dict:
    """Enters on the racer's thrust, edges down, then goes nearly flat: no new high, so the stall clock runs."""
    r = racer(up=12, down=0, **extra)
    r["ret"][32:34] = -0.001
    r["ret"][34:64] = 0.0001
    r["vmult"][32:64] = 6.0
    return r


def test_provisional_bar_counts_no_stall_soft_fail():
    """DATA-4: a bar still being revised neither counts a STALL soft-fail nor clears one already counted."""
    session, pack, bars = build({"RUN": plateau()}, seed=3)
    clean = drive(Engine(PARAMS, pack, session), bars, session, range(46))
    assert [(e["type"], e["reason"], e["slot"]) for e in events(clean, "RUN")][1:] == [("EXIT", "STALL", 41)]
    eng = Engine(PARAMS, pack, session)
    held = drive(eng, bars, session, range(36)) + drive(eng, bars, session, range(36, 42), provisional={"RUN"})
    assert [e["type"] for e in events(held, "RUN")] == ["ENTER"] and eng.state_dict()["members"]["RUN"]["soft"] == 0
    eng = Engine(PARAMS, pack, session)                            # one soft-fail at 40, a provisional bar at 41
    kept = (drive(eng, bars, session, range(41)) + drive(eng, bars, session, [41], provisional={"RUN"})
            + drive(eng, bars, session, range(42, 46)))
    assert [(e["type"], e["reason"], e["slot"]) for e in events(kept, "RUN")][1:] == [("EXIT", "STALL", 42)]


def test_data_stale_exit_after_three_bars_without_volume():
    session, pack, bars = build({"RUN": racer(zero_vol=(27, 28, 29))})
    outs = drive(Engine(PARAMS, pack, session), bars, session, range(40))
    leave = events(outs, "RUN")[1]
    assert leave["reason"] == "DATA_STALE" and leave["slot"] == 29 and "3 scans" in leave["detail"]


def drifter(**extra) -> dict:
    """Enters on the racer's thrust, then drifts up gently (no halt-sized move) with steady volume."""
    r = racer(up=12, down=0, **extra)
    r["ret"][32:60] = 0.0012
    r["vmult"][32:60] = 6.0
    return r


@pytest.mark.parametrize("spec", [racer(up=24, down=0, zero_vol=(27,)), drifter(drop=(34,))],
                         ids=["one-zero-volume-bar", "one-missing-bar"])
def test_one_bad_bar_is_not_data_stale(spec):
    """ENG-2: DATA_STALE counts consecutive bars without a fresh print, not 3-bar windows."""
    session, pack, bars = build({"RUN": spec})
    outs = drive(Engine(PARAMS, pack, session), bars, session, range(50))
    ev = events(outs, "RUN")
    assert ev[0]["type"] == "ENTER" and ev[0]["slot"] < 27
    assert not [e for e in ev if e["reason"] == "DATA_STALE"]
    assert [r["slot"] for r in rows(outs, "RUN") if r["role"] == "member"] == list(range(ev[0]["slot"], 50))


def test_fallback_bars_never_count_toward_data_stale():
    """SPEC 4.5: missing data counts toward DATA_STALE only while the source is healthy."""
    session, pack, bars = build({"RUN": racer(up=24, down=0, zero_vol=(27, 28, 29))})
    eng = Engine(PARAMS, pack, session)
    outs = drive(eng, bars, session, range(26)) + drive(eng, bars, session, range(26, 40), degraded=True)
    assert not [e for e in events(outs, "RUN") if e["reason"] == "DATA_STALE"]
    assert "RUN" in eng.state_dict()["members"]


def test_a_fresh_fallback_bar_ends_a_stale_run():
    """SPEC 12.2: any fresh print ends a DATA_STALE run, fallback bars included (they only never add to it).
    Two bad bars, fresh fallback bars, then one bad bar are never three stale scans."""
    session, pack, bars = build({"RUN": racer(up=30, down=0, zero_vol=(27, 28, 35))})
    eng = Engine(PARAMS, pack, session)
    outs = drive(eng, bars, session, range(29))
    assert eng.state_dict()["members"]["RUN"]["stale"] == 2
    outs += drive(eng, bars, session, range(29, 35), degraded=True)
    assert eng.state_dict()["members"]["RUN"]["stale"] == 0
    outs += drive(eng, bars, session, range(35, 45))
    assert [(e["type"], e["reason"]) for e in events(outs, "RUN")] == [("ENTER", "ENTRY")]
    assert "RUN" in eng.state_dict()["members"]


def test_long_halt_freezes_then_exits():
    session, pack, bars = build({"RUN": racer(up=20, down=0, jump=0.0, drop=range(28, 34),
                                              ret_patch={27: 0.02})})
    outs = drive(Engine(PARAMS, pack, session), bars, session, range(45))
    ev = events(outs, "RUN")
    assert [e["type"] for e in ev[:2]] == ["ENTER", "EXIT"]
    assert ev[1]["reason"] == "HALT_LONG" and ev[1]["slot"] == 33 and "30 min" in ev[1]["detail"]
    halted = [r["slot"] for r in rows(outs, "RUN") if r["state"] == "halted"]
    assert halted == [28, 29, 30, 31, 32]
    assert all(e["slot"] >= 33 + PARAMS["reentry"]["cool_same_bars"] for e in ev[2:])


def test_external_halt_evidence_is_used():
    session, pack, bars = build({"RUN": racer(up=30, down=0)})
    eng = Engine(PARAMS, pack, session)
    drive(eng, bars, session, range(26))
    outs = drive(eng, bars, session, range(26, 32), halted={"RUN"})
    assert [r["state"] for r in rows(outs, "RUN")] == ["halted"] * 5
    assert events(outs, "RUN")[0]["reason"] == "HALT_LONG" and events(outs, "RUN")[0]["slot"] == 31


def test_reopen_window_blocks_new_heating():
    reop = racer(start=30, up=16, step=0.004, down=0, drop=(31, 32), ret_patch={30: 0.02, 33: 0.03})
    session, pack, bars = build({"REOP": reop})
    outs = drive(Engine(PARAMS, pack, session), bars, session, range(50))
    heat = [r["slot"] for r in rows(outs, "REOP") if r["role"] == "heating"]
    assert heat and not set(heat) & {31, 32, 33, 34} and min(s for s in heat if s > 30) >= 35


def test_reentry_rules():
    session, pack, _ = build({})
    eng = Engine(PARAMS, pack, session)
    mem = eng.st["memory"]
    mem["N1"] = {"episodes": 1, "last_exit_slot": 30, "last_exit_dir": 1, "last_peak": 52.0}
    blocked = lambda d, k, px: eng._reentry_blocked("N1", d, k, px)
    assert blocked(1, 35, 60.0)                 # same direction inside 6 bars
    assert blocked(1, 36, 51.0)                 # cooled down but no new peak
    assert not blocked(1, 36, 52.5)
    assert blocked(-1, 32, 40.0) and not blocked(-1, 33, 40.0)
    mem["N1"]["episodes"] = PARAMS["reentry"]["max_episodes"]
    assert blocked(1, 60, 99.0) and blocked(-1, 60, 1.0)
    assert not eng._reentry_blocked("N2", 1, 10, 1.0)


def test_second_episode_needs_cooldown_and_a_new_peak():
    twice = racer(up=12, down=4)
    twice["ret"][45:60] = 0.006
    twice["vmult"][45:60] = 6.0
    session, pack, bars = build({"TWICE": twice})
    ev = events(drive(Engine(PARAMS, pack, session), bars, session, range(78)), "TWICE")
    enters = [e for e in ev if e["type"] == "ENTER"]
    assert [e["episode"] for e in enters] == [1, 2]
    first_exit = next(e for e in ev if e["type"] == "EXIT")
    assert enters[1]["slot"] - first_exit["slot"] >= PARAMS["reentry"]["cool_same_bars"]
    assert enters[1]["id"].endswith("-TWICE-ENTER-2")


def sharp(n: int = 78, start: int = 20) -> dict:
    """Heats on one big bar at `start` with a volume burst, so several names confirm on the same bar."""
    r = racer(n=n, start=start, up=30, step=0.004, down=0, jump=0.015)
    r["vmult"][start] = 18.0
    return r


def test_caps_limit_new_names_per_tick():
    names = [f"R{i}" for i in range(5)]
    meta = {s: {"sector": f"Sector {i}"} for i, s in enumerate(names)}
    session, pack, bars = build({s: sharp() for s in names}, meta=meta)
    enters = [e for e in events(drive(Engine(PARAMS, pack, session), bars, session, range(40)))
              if e["type"] == "ENTER"]
    per_slot = Counter(e["slot"] for e in enters)
    first = min(per_slot)
    assert per_slot[first] == PARAMS["caps"]["max_new_per_tick"] and max(per_slot.values()) <= 4


def test_sector_cap_and_banner():
    names = [f"F{i}" for i in range(5)]
    meta = {s: {"name": f"Fin {s}", "sector": "Financials"} for s in names}
    session, pack, bars = build({s: sharp() for s in names}, meta=meta)
    eng = Engine(PARAMS, pack, session)
    outs = drive(eng, bars, session, range(30))
    snap = outs[-1].snapshot
    fin = [m for m in snap["members"] if m["sector"] == "Financials"]
    assert len(fin) == PARAMS["caps"]["max_per_sector_dir"]
    banners = [b for o in outs for b in o.snapshot["sector_banners"]]
    assert banners and all(set(b) == {"sector", "direction", "count", "tickers"} for b in banners)
    b = banners[0]
    assert b["sector"] == "Financials" and b["direction"] == "up" and b["count"] == len(b["tickers"]) >= 1
    assert not set(b["tickers"]) & {m["ticker"] for m in fin}


def test_sector_banner_drops_a_name_once_it_is_admitted():
    """E2E-2: B is held back by the cap while A is on the radar; when A exits and B enters, the banner no
    longer lists B (count = names held back beyond the cap)."""
    params = copy.deepcopy(PARAMS)
    params["caps"]["max_per_sector_dir"] = 1
    meta = {"A": {"name": "A Corp", "sector": "Technology"}, "B": {"name": "B Corp", "sector": "Technology"}}
    first = racer(start=20, up=10, down=4, jump=0.015)
    first["vmult"][20] = 18.0
    session, pack, bars = build({"A": first, "B": racer(start=24, up=30, down=0)}, meta=meta)
    outs = drive(Engine(params, pack, session), bars, session, range(45))
    a_exit = events(outs, "A")[1]
    b_enter = events(outs, "B")[0]
    assert b_enter["type"] == "ENTER" and b_enter["slot"] >= a_exit["slot"]
    held = [b for b in outs[a_exit["slot"] - 1].snapshot["sector_banners"] if b["sector"] == "Technology"]
    assert held and held[0]["tickers"] == ["B"] and held[0]["count"] == 1
    for o in outs[b_enter["slot"]:]:
        members = {m["ticker"] for m in o.snapshot["members"]}
        for ban in o.snapshot["sector_banners"]:
            assert not set(ban["tickers"]) & members and ban["count"] == len(ban["tickers"]) >= 1


def test_full_radar_displaces_the_weakest_member():
    params = copy.deepcopy(PARAMS)
    params["caps"].update(max_members=2, displace_margin=-100)
    session, pack, bars = build({"R1": sharp(), "R2": sharp(), "R3": sharp(start=32)})
    outs = drive(Engine(params, pack, session), bars, session, range(40))
    displaced = [e for e in events(outs) if e["reason"] == "DISPLACED"]
    assert len(displaced) == 1 and displaced[0]["ticker"] in ("R1", "R2") and "R3" in displaced[0]["detail"]
    r3 = [e for e in events(outs, "R3") if e["type"] == "ENTER"]
    assert r3 and r3[0]["slot"] == displaced[0]["slot"]
    assert max(len(o.snapshot["members"]) for o in outs) == 2


def test_state_stays_small_with_a_full_universe_and_a_full_radar():
    crowd = {f"U{i:03d}": {"px": 20.0 + i % 50, "beta": 0.0} for i in range(600)}
    session, pack, bars = build({f"R{i:02d}": sharp() for i in range(12)}, extra=crowd)
    eng = Engine(PARAMS, pack, session)
    outs = drive(eng, bars, session, range(50), stage_a=True)
    assert len(outs[-1].snapshot["members"]) == PARAMS["caps"]["max_members"]
    size = len(json.dumps(eng.state_dict(), allow_nan=False).encode())
    assert size < 200 * 1024, size


def test_market_mode_turns_on_fast_and_off_with_hysteresis():
    ret = np.zeros(78)
    ret[20:26] = 0.003
    independent = {f"Z{i:02d}": {"px": 30.0, "beta": 0.0} for i in range(30)}     # breadth stays low
    session, pack, bars = build({}, spy={"ret": ret}, extra=independent)
    outs = drive(Engine(PARAMS, pack, session), bars, session, range(60))
    mk = [o.snapshot["market"] for o in outs]
    pm = PARAMS["market"]
    mode, calm = "normal", 0
    for k, m in enumerate(mk):
        z, b = abs(m["spy_z30"] or 0.0), m["breadth30"] or 0.0
        if mode == "normal":
            mode = "market" if z >= pm["on_spy_z6"] or b >= pm["on_breadth"] else "normal"
            calm = 0
        elif z < pm["off_spy_z6"] and b < pm["off_breadth"]:
            calm += 1
            if calm >= pm["off_bars"]:
                mode, calm = "normal", 0
        else:
            calm = 0
        assert m["mode"] == mode, k
    on = [k for k, m in enumerate(mk) if m["mode"] == "market"]
    assert on and 20 <= on[0] <= 22 and mk[on[0]]["dir"] == "up" and mk[-1]["mode"] == "normal"
    assert all(mk[k]["dir"] is None for k in range(len(mk)) if mk[k]["mode"] == "normal")


def test_split_guard_blocks_the_day():
    session, pack, bars = build({"SPLT": racer(gap=math.log(2.0)), "CTRL": racer()})
    ev = events(drive(Engine(PARAMS, pack, session), bars, session, range(40)))
    assert {e["ticker"] for e in ev if e["type"] == "ENTER"} == {"CTRL"}


def test_half_day_slot_map():
    day = date(2026, 11, 27)
    session, pack, bars = build({"HALF": racer(n=42, start=10, up=32, step=0.003, down=0)}, day=day)
    assert session.n_slots == 42
    eng = Engine(PARAMS, pack, session)
    assert (eng.last_entry, eng.last_confirm, eng.session_end) == (29, 30, 40)
    outs = drive(eng, bars, session, range(42))
    ev = events(outs, "HALF")
    assert [e["type"] for e in ev] == ["ENTER", "EXIT"] and ev[1]["reason"] == "SESSION_END" and ev[1]["slot"] == 40
    assert [o.processed_slots for o in outs] == [[k] for k in range(42)]
    assert eng.step(upto(bars, session, 41), session.close_epoch + 3600).processed_slots == []
