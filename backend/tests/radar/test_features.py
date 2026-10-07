"""Features (signal-model.md section 2), gates (4.2), intensity (4.4), split guard (3) and halt flags (2.3)."""
from __future__ import annotations

import math
import time

import numpy as np
import pytest

from radar.config import PARAMS
from radar.features import (Grid, SessionFeatures, build_grid, direction, entry_gates, fill_slots, halt_flags, score,
                            split_flags)
from radar.types import Bars

OPEN = 1_790_000_000 - 1_790_000_000 % 300
N = 78
ENTRY = PARAMS["entry"]
WEIGHTS = PARAMS["score_weights"]


def grid_from(closes: dict[str, list[float]], vols: dict[str, list[float]] | None = None,
              missing: dict[str, list[int]] | None = None, wick: float = 0.0) -> Grid:
    bars = {}
    for s, c in closes.items():
        c = np.asarray(c, dtype=float)
        n = len(c)
        o = np.r_[c[0], c[:-1]]
        v = np.asarray((vols or {}).get(s, [1000] * n), dtype=np.int64)
        keep = np.ones(n, dtype=bool)
        keep[(missing or {}).get(s, [])] = False
        ts = OPEN + 300 * np.arange(n)
        bars[s] = Bars(s, ts[keep], o[keep], (np.maximum(o, c) * (1 + wick))[keep], (np.minimum(o, c) * (1 - wick))[keep],
                       c[keep], v[keep])
    n = max(len(c) for c in closes.values())
    return build_grid(bars, sorted(closes), OPEN, n)


def feats_for(grid: Grid, *, sig5=0.001, beta=1.0, sigd=0.02, pc=None, vm=1000.0, m=None) -> SessionFeatures:
    S, n = grid.c.shape
    spy = grid.symbols.index("SPY")
    m = np.ones(N) if m is None else m
    sig = np.full(S, sig5, dtype=float)
    sig[spy] = sig5 / 2
    b = np.full(S, beta, dtype=float)
    b[spy] = 1.0
    vmv = np.full((S, n), vm)
    return SessionFeatures(grid, spy=spy, m=m[:n], beta=b, sig5=sig, sig5_spy=float(sig[spy]), vm=vmv,
                           cvm=np.cumsum(vmv, axis=1), sigd=np.full(S, sigd),
                           pc=np.array(pc if pc is not None else grid.c[:, 0], dtype=float),
                           sigday_scale=float(np.sqrt(np.sum(m[1:] ** 2))))


def test_build_grid_places_fills_and_drops():
    ts = np.array([OPEN - 600, OPEN, OPEN + 150, OPEN + 600, OPEN + 900, OPEN + 300 * N], dtype=np.int64)
    c = np.array([9.0, 10.0, 99.0, 11.0, np.nan, 50.0])
    b = Bars("X", ts, c, c + 1, c - 1, c, np.array([0, 5, 7, 6, 3, 9], dtype=np.int64))
    g = build_grid({"X": b}, ["X", "Y"], OPEN, 6)
    assert g.present[0].tolist() == [True, False, True, False, False, False]
    assert g.c[0].tolist() == [10.0, 10.0, 11.0, 11.0, 11.0, 11.0]
    assert g.v[0].tolist() == [5, 0, 6, 0, 0, 0]
    assert g.h[0, 1] == g.l[0, 1] == 10.0 and g.h[0, 2] == 12.0
    assert g.first.tolist() == [0, 6] and np.isnan(g.c[1]).all()


def test_fill_slots_leading_gap_takes_first_print():
    c = np.array([[np.nan, np.nan, 3.0, np.nan, 5.0], [np.nan] * 5])
    out = fill_slots(c, ~np.isnan(c))
    assert out[0].tolist() == [3.0, 3.0, 3.0, 3.0, 5.0] and np.isnan(out[1]).all()


def test_thrust_scale_anchor_and_market_relative():
    spy = [100 * math.exp(0.001 * j) for j in range(N)]
    x = [50 * math.exp(0.002 * j) for j in range(N)]                  # 1 bp/slot of its own above beta * SPY
    f = feats_for(grid_from({"SPY": spy, "X": x}), beta=1.0)
    at = f.at(20)
    xi = 1
    assert at["r3"][xi] == pytest.approx(0.006) and at["r6"][xi] == pytest.approx(0.012)
    assert at["z3"][xi] == pytest.approx(0.003 / (0.001 * math.sqrt(3)), rel=1e-6)
    assert at["z6"][xi] == pytest.approx(0.006 / (0.001 * math.sqrt(6)), rel=1e-6)
    assert at["zS6"] == pytest.approx(0.006 / (0.0005 * math.sqrt(6)), rel=1e-6)
    assert at["acc"][xi] == pytest.approx(0.0, abs=1e-6)
    early = f.at(2)                                                    # anchor: never before C[0]
    assert early["r6"][xi] == pytest.approx(0.004) and early["z6"][xi] == pytest.approx(0.002 / (0.001 * math.sqrt(2)))
    assert np.isnan(f.at(0)["z3"][xi])                                # the opening bar is never a thrust


def test_time_of_day_multiplier_scales_thrust():
    spy = [100.0] * N
    x = [50 * math.exp(0.002 * j) for j in range(N)]
    m = np.ones(N)
    m[18:21] = 2.0
    at = feats_for(grid_from({"SPY": spy, "X": x}), m=m).at(20)
    assert at["z3"][1] == pytest.approx(0.006 / (0.001 * math.sqrt(12)), rel=1e-6)


def test_volume_vwap_extremes_efficiency_day_context():
    n = 30
    spy = [100.0] * n
    x = [50.0] * 20 + [50.0 * (1 + 0.01 * (j - 19)) for j in range(20, n)]
    vol = [1000] * 18 + [4000] * 12
    f = feats_for(grid_from({"SPY": spy, "X": x}, {"SPY": [1000] * n, "X": vol}), sigd=0.02, pc=[100.0, 45.0])
    at = f.at(22)
    assert at["rvol3"][1] == pytest.approx(4.0) and at["rvolc"][1] == pytest.approx((18 * 1000 + 5 * 4000) / 23000)
    g = f.g
    tp = (g.h[1, :23] + g.l[1, :23] + g.c[1, :23]) / 3
    vwap = float((tp * g.v[1, :23]).sum() / g.v[1, :23].sum())
    assert at["vwap"][1] == pytest.approx(vwap)
    assert at["dvwap"][1] == pytest.approx(math.log(x[22] / vwap) / (0.001 * math.sqrt(77)))
    assert at["newhi"][1] and not at["newlo"][1] and not at["newhi"][0]
    assert at["er6"][1] == pytest.approx(1.0)
    assert at["zday"][1] == pytest.approx(math.log(x[22] / 45.0) / 0.02)
    assert at["fresh"].all() and at["dollar3"][1] == pytest.approx(4000 * sum(x[20:23]))


def test_freshness_needs_three_printed_bars_with_volume():
    n = 12
    g = grid_from({"SPY": [100.0] * n, "X": [50.0] * n}, {"SPY": [1000] * n, "X": [1000] * 9 + [0, 1000, 1000]},
                  missing={"X": [5]})
    f = feats_for(g)
    assert not f.at(5)["fresh"][1] and not f.at(7)["fresh"][1] and f.at(8)["fresh"][1]
    assert not f.at(11)["fresh"][1] and f.at(11)["fresh"][0]


def test_direction_rule():
    z3 = np.array([3.0, -3.0, 1.0, 0.0, 2.6, np.nan])
    z6 = np.array([-1.0, 4.0, -4.0, 0.0, -3.0, np.nan])
    assert direction(z3, z6, ENTRY).tolist() == [1, 1, -1, 1, 1, 1]


def passing() -> dict[str, np.ndarray]:
    one = lambda v: np.array([v], dtype=float)
    return {"z3": one(2.6), "z6": one(3.1), "r6": one(0.01), "rvol3": one(2.5), "rvolc": one(1.6),
            "newhi": np.array([True]), "newlo": np.array([False]), "dvwap": one(0.5), "er6": one(0.6),
            "zday": one(2.6), "acc": one(0.5)}


@pytest.mark.parametrize("key,value", [("z3", 0.5), ("z6", 1.0), ("r6", 0.005), ("rvol3", 1.9), ("rvolc", 0.9),
                                       ("newhi", np.array([False])), ("dvwap", -0.1), ("er6", 0.4),
                                       ("zday", 2.4), ("rvolc", 1.4)])
def test_each_entry_gate_is_required(key, value):
    d = np.array([1])
    assert entry_gates(passing(), d, ENTRY, 0.0)[0]
    f = passing()
    f[key] = value if isinstance(value, np.ndarray) else np.array([value])
    if key == "z3":
        f["z6"] = np.array([2.9])
    if key == "z6":
        f["z3"] = np.array([2.4])
    assert not entry_gates(f, d, ENTRY, 0.0)[0]


def test_thrust_gate_takes_the_market_bump_and_needs_both_signs():
    f = passing()
    f["z3"], f["z6"] = np.array([2.7]), np.array([2.9])
    d = np.array([1])
    assert entry_gates(f, d, ENTRY, 0.0)[0] and not entry_gates(f, d, ENTRY, 0.5)[0]
    f["z6"] = np.array([-0.1])
    assert not entry_gates(f, d, ENTRY, 0.0)[0]


def test_down_direction_mirrors():
    f = {k: (-v if v.dtype == float and k not in ("rvol3", "rvolc", "er6") else v) for k, v in passing().items()}
    f["newhi"], f["newlo"] = np.array([False]), np.array([True])
    assert entry_gates(f, np.array([-1]), ENTRY, 0.0)[0]
    assert score(f, -1, ENTRY, WEIGHTS)[0] == pytest.approx(score(passing(), 1, ENTRY, WEIGHTS)[0])


def test_intensity_range_and_components():
    f = passing()
    s = score(f, 1, ENTRY, WEIGHTS)[0]
    assert 0 < s < 100
    top = dict(f, z3=np.array([10.0]), rvol3=np.array([8.0]), er6=np.array([0.9]), dvwap=np.array([2.0]),
               zday=np.array([4.0]), acc=np.array([3.0]))
    assert score(top, 1, ENTRY, WEIGHTS)[0] == pytest.approx(100.0)
    flat = dict(f, z3=np.array([0.0]), z6=np.array([0.0]), rvol3=np.array([1.0]), er6=np.array([0.1]),
                dvwap=np.array([-1.0]), zday=np.array([0.0]), acc=np.array([-1.0]), newhi=np.array([False]))
    assert score(flat, 1, ENTRY, WEIGHTS)[0] == pytest.approx(0.0)


def test_split_guard():
    g = grid_from({"SPY": [100.0] * 5, "HALF": [50.0] * 5, "GAP": [60.0] * 5, "ODD": [37.0] * 5})
    pc = np.array([100.0, 100.0, 100.0, 100.0])        # ODD opens at 0.37x: big but no split ratio
    flags = split_flags(g, pc, np.full(4, 0.02))
    assert dict(zip(g.symbols, flags.tolist())) == {"GAP": False, "HALF": True, "ODD": False, "SPY": False}
    assert not split_flags(g, pc, np.full(4, 0.2))[g.symbols.index("HALF")]      # not >= 8 sigma_d


def halts(g: Grid, sig5=0.001, ms_ok=True, reopen=2):
    S, n = g.c.shape
    return halt_flags(g, spy=g.symbols.index("SPY"), sig5=np.full(S, sig5), m=np.ones(n),
                      ms_ok=np.full(S, ms_ok), move_sigma=4.0, reopen_bars=reopen)


def test_isolated_missing_slot_is_not_a_halt():
    n = 20
    g = grid_from({"SPY": [100.0] * n, "X": [50.0] * n}, missing={"X": [8]})
    h = halts(g)
    assert not h.halted.any() and not h.reopen.any()


def test_halt_after_big_move_runs_and_reopens():
    n = 20
    x = [50.0] * 8 + [53.0] * (n - 8)                  # +5.8% bar at slot 8, then slots 9-12 missing
    g = grid_from({"SPY": [100.0] * n, "X": x}, missing={"X": [9, 10, 11, 12]})
    h = halts(g)
    xi = g.symbols.index("X")
    assert np.flatnonzero(h.halted[xi]).tolist() == [9, 10, 11, 12]
    assert h.run[xi, 9:14].tolist() == [1, 2, 3, 4, 0]
    assert np.flatnonzero(h.reopen[xi]).tolist() == [13, 14]
    assert not halts(g, ms_ok=False).halted.any()


def test_zero_volume_flat_bar_counts_as_missing():
    n = 16
    x = [50.0] * 6 + [55.0] * (n - 6)
    vol = [1000] * 7 + [0, 0] + [1000] * (n - 9)
    g = grid_from({"SPY": [100.0] * n, "X": x}, {"SPY": [1000] * n, "X": vol})
    assert np.flatnonzero(halts(g).halted[1]).tolist() == [7, 8]


def test_no_halt_when_spy_is_missing_too():
    n = 16
    x = [50.0] * 6 + [55.0] * (n - 6)
    g = grid_from({"SPY": [100.0] * n, "X": x}, missing={"X": [7], "SPY": [7]})
    assert not halts(g).halted.any()


def test_single_gap_then_big_reopen_print_opens_reopen_window():
    n = 16
    x = [50.0] * 8 + [56.0] * (n - 8)                  # slot 7 missing, slot 8 prints +12%
    g = grid_from({"SPY": [100.0] * n, "X": x}, missing={"X": [7]})
    h = halts(g)
    assert not h.halted.any() and np.flatnonzero(h.reopen[1]).tolist() == [8, 9]


def test_external_halt_evidence():
    n = 10
    g = grid_from({"SPY": [100.0] * n, "X": [50.0] * n})
    ext = np.zeros(g.c.shape, dtype=bool)
    ext[1, 4] = True
    h = halt_flags(g, spy=0, sig5=np.full(2, 0.001), m=np.ones(n), ms_ok=np.ones(2, dtype=bool), move_sigma=4.0,
                   reopen_bars=2, external=ext)
    assert np.flatnonzero(h.halted[1]).tolist() == [4] and np.flatnonzero(h.reopen[1]).tolist() == [5, 6]


def test_features_are_fast_on_a_full_universe():
    rng = np.random.default_rng(3)
    S = 600
    closes = {("SPY" if i == 0 else f"S{i:03d}"): list(50 * np.exp(np.cumsum(rng.normal(0, 0.001, N))))
              for i in range(S)}
    f = feats_for(grid_from(closes))
    f.at(40)
    t0 = time.perf_counter()
    for k in range(3, 76):
        f.at(k)
    per_tick = (time.perf_counter() - t0) / 73
    assert per_tick < 0.05, per_tick
