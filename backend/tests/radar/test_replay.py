"""Replay driver, scorecard (signal-model.md 8.2), CSV loaders and CLI on synthetic data.

The design-dataset replay at the end runs only when RADAR_REPLAY_DATA names the folder holding the recorded
bars (bars_5m_prepost.csv.gz, bars_1d_1y.csv.gz); the dataset is not part of the repository.
"""
from __future__ import annotations

import copy
import csv
import gzip
import json
import os
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from radar.calendar_nyse import next_sessions, session_for
from radar.config import PARAMS
from radar.replay import (load_bars_csv, load_daily_csv, load_meta, naive_params, replay_sessions, run, universe_filter,
                          wilson)
from radar.tools import replay_cli
from radar.types import Bars
from tests.radar.test_baselines import synth_market
from tests.radar.test_engine import NOISE, racer

DAYS = next_sessions(date(2026, 8, 3), 23)
REPLAY = [d.day.isoformat() for d in DAYS[20:]]
SPEC = {**NOISE, "RUN": {"px": 50.0}, "DIP": {"px": 70.0}, "ETFX": {"px": 30.0}}


def dip() -> dict:
    d = racer(start=30, up=12, step=-0.004, down=4, down_step=0.008, gap=-0.045)
    return d


@pytest.fixture(scope="module")
def market():
    custom = {(REPLAY[0], "RUN"): racer(), (REPLAY[1], "DIP"): dip(), (REPLAY[2], "RUN"): racer(start=40)}
    return synth_market(DAYS, SPEC, custom=custom)


@pytest.fixture(scope="module")
def card(market):
    bars, daily = market
    return run(bars, daily, PARAMS, REPLAY)


def test_scorecard_shape_and_entries(card):
    rec = card["rec"]
    assert card["first_session"] == REPLAY[0] and card["last_session"] == REPLAY[-1]
    assert rec["sessions"] == 3 and rec["entries"] >= 3
    for key in ("entries_per_day", "prec15", "prec30", "prec60", "hold30", "flap_rate", "flaps_per_day",
                "post_exit_continuation", "mfe_gt_mae", "latency_1bar", "prec30_wilson95", "distinct_symbol_days"):
        assert key in rec, key
    assert set(rec["dwell_bars"]) == {"median", "p25", "p75"}
    assert set(rec["concurrency"]) == {"median", "p95", "max", "cap_reached_share"}
    assert 0.0 <= rec["prec30"] <= 1.0 and rec["up_share"] < 1.0
    assert set(card["splits"]) == {"calibration", "validation"}
    assert set(card["baseline_all_ticks"]) >= {"prec30", "n"} and card["baseline_all_ticks"]["n"] > 0
    assert card["baseline_naive"]["entries"] >= rec["entries"]
    assert set(card["meets_targets"]) == {"entries_per_day", "prec30", "flap_rate"}
    json.dumps(card, allow_nan=False)


def test_scripted_racers_are_found_and_continue(card, market):
    rec = card["rec"]
    assert rec["exit_reasons"] and set(rec["exit_reasons"]) <= {"REVERSAL", "FADE", "GIVEBACK", "VWAP_CROSS", "STALL",
                                                                "DRY", "SESSION_END", "DATA_STALE"}
    assert rec["prec15"] >= 0.5 and rec["hold30"] >= 0.5


def test_json_roundtrip_every_tick_gives_the_same_card(market, card):
    bars, daily = market
    assert run(bars, daily, PARAMS, REPLAY, roundtrip=True, baselines=False)["rec"] == card["rec"]


def test_stage_a_does_not_change_results(market, card):
    bars, daily = market
    assert run(bars, daily, PARAMS, REPLAY, stage_a=False, baselines=False)["rec"]["entries"] == card["rec"]["entries"]


def test_no_look_ahead_into_later_sessions(market):
    bars, daily = market
    first = run(bars, daily, PARAMS, REPLAY[:1], baselines=False)["rec"]
    later = copy.deepcopy(bars)
    for day in REPLAY[1:]:
        for b in later[day].values():
            b.c[:] = b.c[::-1]
    cut = np.datetime64(REPLAY[1])
    daily_later = {s: type(d)(s, d.day, d.o, d.h, d.l, np.where(d.day >= cut, 1.0, d.c), d.v) for s, d in daily.items()}
    assert run(later, daily_later, PARAMS, REPLAY[:1], baselines=False)["rec"] == first


def test_naive_params_drop_gates_and_hysteresis():
    p = naive_params(PARAMS)
    assert p["entry"]["inplay_zday"] < -10 and p["confirm"]["fast_path_score"] == 0.0
    assert p["hold"]["min_dwell"] == 1 and p["reentry"]["max_episodes"] > 10
    assert PARAMS["entry"]["inplay_zday"] == 2.5 and PARAMS["confirm"]["fast_path_score"] is None


def test_wilson():
    assert wilson(0, 0) is None
    lo, hi = wilson(58, 100)
    assert 0.48 < lo < 0.58 < hi < 0.68


def test_replay_sessions_and_universe_filter(market):
    bars, _ = market
    assert replay_sessions(bars, 20) == REPLAY and replay_sessions(bars, 20, 1) == REPLAY[-1:]
    kept = universe_filter(bars, {"ETFX": {"is_etf": True}, "SPY": {"is_etf": True}})
    assert all("ETFX" not in day and "SPY" in day for day in kept.values())


def write_csvs(tmp_path: Path, bars: dict[str, dict[str, Bars]], daily) -> tuple[Path, Path]:
    bpath, dpath = tmp_path / "bars.csv.gz", tmp_path / "daily.csv.gz"
    with gzip.open(bpath, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "ts", "open", "high", "low", "close", "volume"])
        for day in sorted(bars, reverse=True):                 # any row order is fine
            for s, b in bars[day].items():
                for row in zip(b.ts, b.o, b.h, b.l, b.c, b.v):
                    w.writerow([s, *(repr(float(x)) if i else int(x) for i, x in enumerate(row[:5])), int(row[5])])
    with gzip.open(dpath, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "date", "open", "high", "low", "close", "adjclose", "volume"])
        for s, d in daily.items():
            for i in range(len(d.day)):
                w.writerow([s, str(d.day[i]), repr(float(d.o[i])), repr(float(d.h[i])), repr(float(d.l[i])),
                            repr(float(d.c[i])), repr(float(d.c[i]) * 0.99), int(d.v[i])])
    return bpath, dpath


def test_load_bars_csv_assigns_sessions_dedupes_and_drops(tmp_path):
    s1, s2 = session_for(date(2026, 9, 4)), session_for(date(2026, 9, 8))       # Friday, then Tuesday after Labor Day
    rows = [("AAA", s2.slot_start(1), 11), ("AAA", s1.slot_start(0), 10), ("AAA", s1.slot_start(0), 12),
            ("AAA", s1.slot_start(0) - 3600, 9), ("AAA", s1.close_epoch + 3 * 3600, 13),
            ("AAA", s1.slot_start(0) + 3 * 86400, 99), ("BBB", s1.slot_start(5), 20)]
    path = tmp_path / "b.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "ts", "open", "high", "low", "close", "volume"])
        for sym, ts, px in rows:
            w.writerow([sym, ts, px, px, px, px, 100])
    out = load_bars_csv(str(path))
    assert list(out) == ["2026-09-04", "2026-09-08"]
    a = out["2026-09-04"]["AAA"]
    assert a.ts.tolist() == [s1.slot_start(0) - 3600, s1.slot_start(0), s1.close_epoch + 3 * 3600]
    assert a.c.tolist() == [9.0, 12.0, 13.0] and a.v.dtype == np.int64
    assert out["2026-09-08"]["AAA"].c.tolist() == [11.0] and set(out["2026-09-08"]) == {"AAA"}
    assert out["2026-09-04"]["BBB"].ts.tolist() == [s1.slot_start(5)]


def test_loaders_roundtrip_and_cli(tmp_path, market, card):
    bars, daily = market
    bpath, dpath = write_csvs(tmp_path, bars, daily)
    loaded = load_bars_csv(str(bpath))
    assert list(loaded) == sorted(bars)
    b0, l0 = bars[REPLAY[0]]["RUN"], loaded[REPLAY[0]]["RUN"]
    assert np.array_equal(b0.ts, l0.ts) and np.allclose(b0.c, l0.c) and np.array_equal(b0.v, l0.v)
    d = load_daily_csv(str(dpath))
    assert np.allclose(d["RUN"].c, daily["RUN"].c) and d["RUN"].day.dtype == np.dtype("datetime64[D]")
    meta_path = tmp_path / "universe.csv"
    meta_path.write_text("symbol,name,sector,industry,in_sp500,in_ndx,is_etf\n"
                         "RUN,Run Corp,Industrials,Machinery,True,False,False\n"
                         "ETFX,Some Fund,,,False,False,True\n", encoding="utf-8")
    meta = load_meta(str(meta_path))
    assert meta["RUN"] == {"name": "Run Corp", "sector": "Industrials", "is_etf": False} and meta["ETFX"]["is_etf"]
    out = tmp_path / "card.json"
    assert replay_cli.main(["--bars", str(bpath), "--daily", str(dpath), "--meta", str(meta_path),
                            "--out", str(out), "--no-baselines"]) == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["rec"]["entries"] == card["rec"]["entries"] and got["first_session"] == REPLAY[0]
    assert got["symbols"] == len(SPEC) - 1


DESIGN_DATA = Path(os.environ.get("RADAR_REPLAY_DATA", "")) if os.environ.get("RADAR_REPLAY_DATA") else None
DESIGN_FILES = ("bars_5m_prepost.csv.gz", "bars_1d_1y.csv.gz")
PUBLISHED_CARD = Path(__file__).resolve().parents[2] / "radar" / "docs" / "replay-scorecard.json"


@pytest.mark.soak
@pytest.mark.skipif(DESIGN_DATA is None or not all((DESIGN_DATA / f).exists() for f in DESIGN_FILES),
                    reason="design dataset absent: set RADAR_REPLAY_DATA to the folder with the recorded bars")
def test_design_dataset_reproduces_the_published_scorecard(tmp_path):
    """The 40-session calibration replay behind radar/docs/replay-scorecard.json comes out number for number, so
    the engine, baselines and loaders behave the same under the image's numpy 2 / pandas 3 as when the
    parameters were calibrated (numpy 1.26). Takes about 7 minutes."""
    out = tmp_path / "card.json"
    assert replay_cli.main(["--bars", str(DESIGN_DATA / DESIGN_FILES[0]), "--daily", str(DESIGN_DATA / DESIGN_FILES[1]),
                            "--out", str(out)]) == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    want = json.loads(PUBLISHED_CARD.read_text(encoding="utf-8"))
    for key in ("runtime_s", "dataset", "notes"):     # run time and the prose added when the card was published
        got.pop(key, None)
        want.pop(key, None)
    assert got == want
