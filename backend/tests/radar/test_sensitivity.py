"""Scan sensitivity (radar/config.py SENSITIVITY): the radar page's dials and the params they produce.

Notch 3 everywhere must be the calibrated PARAMS exactly (the replay calibration only covers it); every dial
moves its thresholds one way only; Stage A (the prefilter) never becomes stricter than the in-play gate it
feeds; bad levels fall back to the calibrated notch; PARAMS is never mutated.
"""
from __future__ import annotations

import copy
import json

import pytest

from radar.config import (
    PARAMS,
    SENSITIVITY,
    SENSITIVITY_DEFAULT,
    SENSITIVITY_LEVELS,
    _slot_close_et,
    effective_params,
    sensitivity_dials,
    sensitivity_levels,
    sensitivity_summary,
)

DIALS = ("thrust", "volume", "day", "cutoff", "exit")
NOTCHES = range(SENSITIVITY_LEVELS)
ALL_DEFAULT = dict.fromkeys(DIALS, SENSITIVITY_DEFAULT)


def at(dial: str, level: int) -> dict:
    return effective_params({dial: level})


def series(dial: str, get) -> list:
    return [get(at(dial, lv)) for lv in NOTCHES]


def strictly_down(xs: list) -> bool:
    return all(a > b for a, b in zip(xs, xs[1:]))


def non_decreasing(xs: list) -> bool:
    return all(a <= b for a, b in zip(xs, xs[1:]))


# ---------------------------------------------------------------- the dial table

def test_five_dials_of_seven_notches_with_the_calibrated_middle():
    assert tuple(SENSITIVITY) == DIALS
    assert SENSITIVITY_LEVELS == 7 and SENSITIVITY_DEFAULT == 3
    for key, d in SENSITIVITY.items():
        assert all(isinstance(d[k], str) and d[k] for k in ("label", "left", "right", "hint")), key
        tables = {k: v for k, v in d.items() if isinstance(v, list)}
        assert tables and all(len(v) == SENSITIVITY_LEVELS for v in tables.values()), key
    # The middle notch of every table is the calibrated value.
    e, hx = PARAMS["entry"], PARAMS["hard_exit"]
    assert SENSITIVITY["thrust"]["scale"][3] == 1.0
    assert SENSITIVITY["volume"]["rvol3"][3] == e["rvol3"]
    assert SENSITIVITY["volume"]["inplay_rvolc"][3] == e["inplay_rvolc"]
    assert SENSITIVITY["day"]["inplay_zday"][3] == e["inplay_zday"]
    assert SENSITIVITY["cutoff"]["last_entry_slot"][3] == PARAMS["session"]["last_entry_slot"]
    assert SENSITIVITY["exit"]["reversal_z3"][3] == hx["reversal_z3"]
    assert SENSITIVITY["exit"]["giveback"][3] == hx["giveback"]
    assert SENSITIVITY["exit"]["soft_fails"][3] == PARAMS["hold"]["soft_fails"]


# ---------------------------------------------------------------- calibration parity

@pytest.mark.parametrize("levels", [None, {}, ALL_DEFAULT, "junk", [3, 3], {"unknown": 0}])
def test_the_calibrated_notch_is_params_exactly(levels):
    p = effective_params(levels)
    assert p == PARAMS
    assert json.dumps(p, sort_keys=True) == json.dumps(PARAMS, sort_keys=True)   # same types too (no 2.5 vs 2.50001)
    assert p is not PARAMS and p["entry"] is not PARAMS["entry"]                   # a deep copy


def test_effective_params_never_mutates_params():
    before = copy.deepcopy(PARAMS)
    for dial in DIALS:
        for lv in NOTCHES:
            p = at(dial, lv)
            p["entry"]["z3"] = -1.0                       # the caller may edit its copy freely
            p["session"]["last_entry_slot"] = -1
    effective_params(dict.fromkeys(DIALS, 0))
    effective_params(dict.fromkeys(DIALS, 6))
    assert PARAMS == before


def test_one_dial_changes_only_its_own_params():
    owned = {
        "thrust": {("entry", "z3"), ("entry", "z6"), ("entry", "abs_r6"), ("confirm", "z6")},
        "volume": {("entry", "rvol3"), ("entry", "inplay_rvolc"), ("confirm", "rvol3"), ("prefilter", "rvolc")},
        "day": {("entry", "inplay_zday"), ("prefilter", "zday_abs")},
        "cutoff": {("session", "last_entry_slot"), ("session", "last_confirm_slot")},
        "exit": {("hard_exit", "reversal_z3"), ("hard_exit", "giveback"), ("hold", "soft_fails")},
    }
    for dial in DIALS:
        for lv in NOTCHES:
            p = at(dial, lv)
            changed = {(g, k) for g, block in PARAMS.items() if isinstance(block, dict)
                       for k in block if p[g][k] != block[k]}
            assert changed <= owned[dial], (dial, lv, changed - owned[dial])
            assert p["params_version"] == PARAMS["params_version"]     # a dial never rebuilds the baselines


# ---------------------------------------------------------------- direction of every dial

def test_thrust_higher_notch_lowers_every_thrust_threshold():
    for path in (("entry", "z3"), ("entry", "z6"), ("entry", "abs_r6"), ("confirm", "z6")):
        xs = series("thrust", lambda p: p[path[0]][path[1]])
        assert strictly_down(xs), (path, xs)
        assert xs[3] == PARAMS[path[0]][path[1]]


def test_thrust_scales_the_thresholds_by_its_factor():
    for lv, f in enumerate(SENSITIVITY["thrust"]["scale"]):
        p = at("thrust", lv)
        assert p["entry"]["z3"] == pytest.approx(PARAMS["entry"]["z3"] * f, abs=1e-3)
        assert p["entry"]["abs_r6"] == pytest.approx(PARAMS["entry"]["abs_r6"] * f, abs=1e-5)
        assert p["confirm"]["z6"] == pytest.approx(PARAMS["confirm"]["z6"] * f, abs=1e-3)


def test_volume_higher_notch_lowers_the_volume_thresholds_and_confirm_follows():
    for path in (("entry", "rvol3"), ("entry", "inplay_rvolc"), ("confirm", "rvol3")):
        xs = series("volume", lambda p: p[path[0]][path[1]])
        assert strictly_down(xs), (path, xs)
        assert xs[3] == PARAMS[path[0]][path[1]]
    for lv in NOTCHES:       # confirm.rvol3 keeps its calibrated ratio to entry.rvol3
        p = at("volume", lv)
        assert p["confirm"]["rvol3"] / p["entry"]["rvol3"] == pytest.approx(
            PARAMS["confirm"]["rvol3"] / PARAMS["entry"]["rvol3"], abs=1e-3)
        assert p["confirm"]["rvol3"] < p["entry"]["rvol3"]           # confirming stays easier than entering


def test_day_higher_notch_lowers_the_in_play_day_move():
    xs = series("day", lambda p: p["entry"]["inplay_zday"])
    assert strictly_down(xs) and xs == SENSITIVITY["day"]["inplay_zday"]


def test_cutoff_higher_notch_allows_later_entries_and_confirmation_one_bar_later():
    end = PARAMS["session"]["session_end_slot"]
    xs = series("cutoff", lambda p: p["session"]["last_entry_slot"])
    assert all(a < b for a, b in zip(xs, xs[1:]))
    for lv in NOTCHES:
        s = at("cutoff", lv)["session"]
        assert s["last_confirm_slot"] == s["last_entry_slot"] + 1
        assert s["first_entry_slot"] < s["last_entry_slot"] and s["last_confirm_slot"] < end
        assert s["session_end_slot"] == end


def test_exit_higher_notch_is_more_patient():
    rev = series("exit", lambda p: p["hard_exit"]["reversal_z3"])
    give = series("exit", lambda p: p["hard_exit"]["giveback"])
    soft = series("exit", lambda p: p["hold"]["soft_fails"])
    assert strictly_down(rev[::-1]) and strictly_down(give[::-1])      # strictly larger with the notch
    assert non_decreasing(soft) and soft[0] < soft[-1]
    assert all(0 < g < 1 for g in give) and min(soft) >= 1


def test_higher_entry_notches_never_raise_any_entry_threshold():
    """Whatever the other dials say, moving one entry dial right never makes entering harder."""
    keys = [("entry", k) for k in ("z3", "z6", "abs_r6", "rvol3", "inplay_rvolc", "inplay_zday")] + \
        [("confirm", "z6"), ("confirm", "rvol3"), ("prefilter", "zday_abs"), ("prefilter", "rvolc")]
    for base in (0, 3, 6):
        for dial in ("thrust", "volume", "day"):
            ps = [effective_params({**dict.fromkeys(DIALS, base), dial: lv}) for lv in NOTCHES]
            for g, k in keys:
                assert non_decreasing([p[g][k] for p in ps][::-1]), (base, dial, g, k)


# ---------------------------------------------------------------- the prefilter (Stage A)

@pytest.mark.parametrize("day", NOTCHES)
@pytest.mark.parametrize("volume", NOTCHES)
def test_the_prefilter_is_never_stricter_than_the_in_play_gate(day, volume):
    p = effective_params({"day": day, "volume": volume})
    e, pf = p["entry"], p["prefilter"]
    assert pf["zday_abs"] <= e["inplay_zday"] - 1.0 + 1e-9
    assert pf["rvolc"] <= e["inplay_rvolc"] - 0.3 + 1e-9
    assert pf["zday_abs"] <= PARAMS["prefilter"]["zday_abs"] and pf["rvolc"] <= PARAMS["prefilter"]["rvolc"]
    assert pf["zday_abs"] > 0 and pf["rvolc"] > 0


def test_the_prefilter_is_the_default_at_the_calibrated_notch_and_widens_with_the_dials():
    assert effective_params({"day": 3, "volume": 3})["prefilter"] == PARAMS["prefilter"]
    assert effective_params({"day": 0, "volume": 0})["prefilter"] == PARAMS["prefilter"]   # stricter gates: kept
    wide = effective_params({"day": 6, "volume": 6})["prefilter"]
    assert wide["zday_abs"] == pytest.approx(SENSITIVITY["day"]["inplay_zday"][6] - 1.0)
    assert wide["rvolc"] == pytest.approx(SENSITIVITY["volume"]["inplay_rvolc"][6] - 0.3)


# ---------------------------------------------------------------- level validation

@pytest.mark.parametrize("value", [True, False, 3.0, 2.5, -1, 7, 99, "3", None, [3], {"v": 3}])
def test_an_invalid_level_falls_back_to_the_calibrated_notch(value):
    lv = sensitivity_levels({"thrust": value, "exit": 5})
    assert lv == {**ALL_DEFAULT, "exit": 5}
    assert effective_params({"thrust": value}) == PARAMS


@pytest.mark.parametrize("raw", [None, "levels", 3, [1, 2], ()])
def test_non_dict_levels_are_all_default(raw):
    assert sensitivity_levels(raw) == ALL_DEFAULT


def test_levels_keep_every_valid_notch_and_drop_unknown_dials():
    raw = {"thrust": 0, "volume": 6, "day": 1, "cutoff": 5, "exit": 2, "bogus": 4}
    lv = sensitivity_levels(raw)
    assert lv == {k: raw[k] for k in DIALS} and list(lv) == list(DIALS)
    assert sensitivity_levels({}) == ALL_DEFAULT


# ---------------------------------------------------------------- the page's summary and dials

def test_slot_close_et():
    assert _slot_close_et(0) == "09:35"
    assert _slot_close_et(PARAMS["session"]["last_entry_slot"]) == "15:00"
    assert _slot_close_et(47) == "13:30" and _slot_close_et(74) == "15:45" and _slot_close_et(77) == "16:00"


def test_summary_at_the_calibrated_notch():
    s = sensitivity_summary()
    assert s == sensitivity_summary(ALL_DEFAULT) == sensitivity_summary("junk")
    e, x = PARAMS["entry"], PARAMS["hard_exit"]
    assert s["levels"] == ALL_DEFAULT and s["default"] == 3 and s["calibrated"] is True
    assert s["entry"] == {"z15": e["z3"], "z30": e["z6"], "move30_pct": 0.75, "rvol15": e["rvol3"],
                          "day_rvol": e["inplay_rvolc"], "day_z": e["inplay_zday"], "cutoff_et": "15:00",
                          "cutoff_min_before_close": 60}
    assert s["exit"]["reversal_z15"] == x["reversal_z3"] and s["exit"]["giveback_pct"] == 70
    assert s["exit"]["soft_fails"] == PARAMS["hold"]["soft_fails"]
    assert s["exit"]["stall_min"] == 5 * PARAMS["hold"]["stall_bars"]
    assert s["exit"]["min_dwell_min"] == 5 * PARAMS["hold"]["min_dwell"]
    json.dumps(s, allow_nan=False)                                   # stored in state.json as is


def test_summary_follows_the_levels():
    s = sensitivity_summary({"cutoff": 0, "thrust": 6, "exit": 6})
    p = effective_params({"cutoff": 0, "thrust": 6, "exit": 6})
    assert s["calibrated"] is False and s["levels"]["cutoff"] == 0
    assert s["entry"]["cutoff_et"] == "13:30" and s["entry"]["cutoff_min_before_close"] == 150
    assert s["entry"]["z15"] == p["entry"]["z3"] and s["entry"]["move30_pct"] == round(100 * p["entry"]["abs_r6"], 2)
    assert s["exit"]["reversal_z15"] == 3.5 and s["exit"]["giveback_pct"] == 85 and s["exit"]["soft_fails"] == 3
    late = sensitivity_summary({"cutoff": 6})["entry"]
    assert late["cutoff_et"] == "15:45" and late["cutoff_min_before_close"] == 15


def test_dials_describe_every_notch_in_plain_words():
    dials = sensitivity_dials()
    assert [d["key"] for d in dials] == list(DIALS)
    for d in dials:
        src = SENSITIVITY[d["key"]]
        assert {k: d[k] for k in ("label", "left", "right", "hint")} == {k: src[k] for k in ("label", "left", "right", "hint")}
        assert [n["level"] for n in d["notches"]] == list(NOTCHES)
        texts = [n["text"] for n in d["notches"]]
        assert all(isinstance(t, str) and t.strip() for t in texts), d["key"]
        assert all("{" not in t and "None" not in t for t in texts), texts
        assert len(set(texts)) > 1, d["key"]                       # the notches are distinguishable
    exit_texts = [n["text"] for n in dials[-1]["notches"]]
    assert "1 weak bar" in exit_texts[0] and "3 weak bars in a row" in exit_texts[-1]
    cutoff = [n["text"] for n in dials[3]["notches"]]
    assert cutoff[3].startswith("new entries until 15:00 ET, 60 min before the close")
    json.dumps(dials, allow_nan=False)

