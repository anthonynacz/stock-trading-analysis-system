"""Momentum Radar parameters. Changing a signal parameter means bumping PARAMS['params_version'].

Signal defaults come from the replay calibration in radar/docs/github-spec.md section 5 (39 sessions,
Aug-Sep 2026): about 5-7 entries per session on ~500 names, 58% of entries still moving the same way
(vs SPY) 30 minutes later, median 40 minutes on the radar. The intensity score ranks and displays; it
is not a probability and must never be presented as confidence.

In Vela the radar's tables live in Postgres (radar/store.py), so the GitHub version's repository,
data-branch and file-path settings are gone; only the monthly backup directory is a filesystem path.
"""
from __future__ import annotations

import copy
import os

REFERENCE_SYMBOLS = ("SPY", "QQQ", "IWM")
US_EXCHANGES = ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS")

PARAMS: dict = {
    # radar-sm-2 (2026-10-07): the REVERSAL exit reads the 15-minute pace in units of today's realised
    # volatility (session so far, capped at 2x normal) instead of the baseline's. Replay over the 40-session
    # corpus vs radar-sm-1: prec30 0.544 -> 0.549, flap rate 5.3% -> 4.4%, REVERSAL exits 104 -> 27 (the
    # remaining ones are followed by a -49 bp move vs -6 bp before), mean capture 5.5 -> 6.0 bp.
    "params_version": "radar-sm-2",
    "session": {"first_entry_slot": 3, "last_entry_slot": 65, "last_confirm_slot": 66,
                "session_end_slot": 76, "bar_final_grace_s": 45},
    "prefilter": {"zday_abs": 1.5, "rvolc": 1.2},
    "universe": {"price_min": 5.0, "adv20_usd_min": 25_000_000, "median_bar_usd_min": 150_000,
                 "missing_share_max": 0.02, "min_baseline_sessions": 15, "tick_dollar3_min": 750_000},
    "dynamic": {"enabled": True, "max_adds_per_day": 150, "market_cap_min": 2_000_000_000,
                "price_min": 5.0, "abs_change_pct_min": 3.0},
    "baseline": {"sessions": 20, "daily_sessions": 21, "tod_clamp": [0.6, 4.0], "beta_shrink": 0.5,
                 "beta_clamp": [0.3, 2.5], "sigma5_floor": 0.0002},
    "entry": {"z3": 2.5, "z6": 3.0, "abs_r6": 0.0075, "rvol3": 2.0, "rvolc": 1.0, "er6": 0.45,
              "inplay_zday": 2.5, "inplay_rvolc": 1.5},
    "confirm": {"z6": 2.0, "rvol3": 1.3, "fast_path_score": None},
    "hold": {"fade_z6": 1.0, "fade_z3": 0.0, "dry_rvol3": 0.6, "stall_bars": 6, "stall_z6": 1.5,
             "min_dwell": 3, "soft_fails": 2},
    "hard_exit": {"reversal_z3": 2.5, "reversal_vol": "session", "reversal_vol_cap": 2.0, "reversal_vol_min_bars": 6,
                  "giveback": 0.7, "vwap_cross": 0.0, "stale_bars": 3,
                  "halt_long_bars": 6},
    "reentry": {"cool_same_bars": 6, "cool_opp_bars": 3, "require_new_peak": True, "max_episodes": 3},
    "caps": {"max_members": 12, "max_new_per_tick": 4, "displace_margin": 15, "max_per_sector_dir": 3},
    "market": {"on_spy_z6": 3.0, "on_breadth": 0.60, "off_spy_z6": 2.0, "off_breadth": 0.45,
               "off_bars": 2, "bump": 0.5, "max_new_mkt_dir": 2},
    "halt": {"reopen_block_bars": 2, "move_sigma": 4.0},
    "score_weights": {"thrust": 0.35, "burst_vol": 0.25, "structure": 0.20, "day": 0.10, "accel": 0.10},
}

# "Busy" preset: more names on the radar, lower continuation (~53% at 30 min in replay).
BUSY_OVERRIDES = {"entry": {"inplay_zday": 2.0}}

RUNTIME: dict = {
    "warmup_et": "09:10",          # worker builds the day's baseline pack from here
    "tick_offset_s": 50,           # scan at each 5-minute boundary + 50 s (bar final at +45 s)
    "last_tick_after_close_s": 50, # final tick at close + 50 s processes the last bars
    "tick_timeout_s": 270,
    "loop_retire_min": 330,        # GitHub runner job budget; the Vela worker does not retire (PORT_SPEC section 5)
    "fetch_workers": 16,
    "fetch_timeout_s": 12,
    "stale_warning_min": 12,       # the page and GET /api/radar flag a stale scan after this many minutes
}

# Monthly gzip backups of archived radar rows (radar/housekeeping.py); a docker volume in production.
BACKUP_DIR = os.environ.get("RADAR_BACKUP_DIR", "/backups/radar")
# Nightly housekeeping (US Eastern) and the row-level retention of hot rows, backups and manifest entries.
HOUSEKEEPING = {"schedule_et": "03:30", "retention_months": 3}

# Option-chain metrics for radar names (radar/options.py): display and the page's call-option filters only,
# never read by the engine. Fetched after each scan for the members and warming-up names.
OPTIONS: dict = {
    "min_dte": 7,                  # the first expiry at least this many calendar days out (skips 0-6 DTE)
    "ntm_band": 0.10,              # "near the money": strikes within ±10% of the stock price
    "weekly_window_days": 35,      # >= 4 expiries inside this window counts as weekly options
    "max_tickers": 30,
    "workers": 4,
    "fetch_timeout_s": 10,
    "timeout_s": 75,               # hard cap on the whole refresh (worker subprocess timeout)
    "keep_days": 7,                # rows older than this are pruned on write
    # Call liquidity grade: near-the-money call open interest and volume today (summed over strikes within
    # ntm_band: one strike's numbers are noisy) and the at-the-money call's bid-ask spread as % of mid
    # (Yahoo quotes are delayed, so the limits are loose). Good when all "good" limits hold, fair when the
    # "fair" ones hold, else thin.
    "grade": {"good": {"oi": 2000, "spread_pct": 10.0, "ntm_volume": 500},
              "fair": {"oi": 300, "spread_pct": 25.0, "ntm_volume": 0}},
}


# ---------------------------------------------------------------- scan sensitivity (radar page sliders)
# Five dials of 7 notches each (0..6). Notch 3 is the calibrated PARAMS above, exactly; lower notches are
# stricter (fewer, stronger entries; for "exit": quicker exits), higher ones more sensitive (more and earlier
# entries, more false starts; for "exit": more patient holds). Saved in radar_runtime.settings and applied by
# the tick through effective_params(); the replay calibration only covers notch 3. The pack's
# params_version is unchanged, so moving a dial never rebuilds the baselines.
SENSITIVITY_LEVELS = 7
SENSITIVITY_DEFAULT = 3
SENSITIVITY: dict[str, dict] = {
    "thrust": {
        "label": "Move strength", "left": "Bigger moves only", "right": "Smaller moves too",
        "hint": "How fast the stock must be moving against the market (15 and 30 minutes) to enter.",
        "scale": [1.4, 1.25, 1.1, 1.0, 0.9, 0.8, 0.7],         # x entry.z3, entry.z6, entry.abs_r6, confirm.z6
    },
    "volume": {
        "label": "Volume surge", "left": "Heavy volume only", "right": "Lighter volume too",
        "hint": "How unusual the volume must be: the last 15 minutes and the day so far, against normal.",
        "rvol3": [3.0, 2.5, 2.25, 2.0, 1.75, 1.5, 1.25],         # entry.rvol3 (confirm.rvol3 scales with it)
        "inplay_rvolc": [2.0, 1.8, 1.65, 1.5, 1.35, 1.2, 1.1],  # entry.inplay_rvolc
    },
    "day": {
        "label": "Day move", "left": "Big day movers only", "right": "Quieter days too",
        "hint": "How unusual the stock's whole day must be (in its own daily volatility) to count as in play.",
        "inplay_zday": [3.5, 3.0, 2.75, 2.5, 2.25, 2.0, 1.5],
    },
    "cutoff": {
        "label": "Late-day entries", "left": "Stop early", "right": "Until near the close",
        "hint": "Until when new stocks may enter. Later entries have less time left in the session.",
        # last_entry_slot (bar closing 13:30 .. 15:45 ET on a full day; half days keep the same distance
        # from the close); confirmation is allowed one bar later.
        "last_entry_slot": [47, 53, 59, 65, 69, 72, 74],
    },
    "exit": {
        "label": "Exit patience", "left": "Quick exits", "right": "Patient holds",
        "hint": "How much of a pullback a stock on the radar may take before it is dropped.",
        "reversal_z3": [1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5],
        "giveback": [0.5, 0.55, 0.6, 0.7, 0.75, 0.8, 0.85],
        "soft_fails": [1, 2, 2, 2, 2, 3, 3],
    },
}


def _slot_close_et(slot: int) -> str:
    """Close time (ET, full day) of 5-minute slot `slot` (slot 0 = 09:30-09:35)."""
    m = 9 * 60 + 30 + 5 * (slot + 1)
    return f"{m // 60:02d}:{m % 60:02d}"


def sensitivity_levels(raw: object) -> dict[str, int]:
    """Valid levels for every dial (missing or bad values -> the calibrated notch)."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in SENSITIVITY:
        v = raw.get(key)
        out[key] = v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < SENSITIVITY_LEVELS             else SENSITIVITY_DEFAULT
    return out


def effective_params(levels: object = None) -> dict:
    """PARAMS with the sensitivity levels applied (a deep copy; notch 3 everywhere returns PARAMS' values)."""
    lv = sensitivity_levels(levels)
    p = copy.deepcopy(PARAMS)
    e, c, hx, hold, pf = p["entry"], p["confirm"], p["hard_exit"], p["hold"], p["prefilter"]
    f = SENSITIVITY["thrust"]["scale"][lv["thrust"]]
    if f != 1.0:
        e["z3"], e["z6"] = round(e["z3"] * f, 3), round(e["z6"] * f, 3)
        e["abs_r6"], c["z6"] = round(e["abs_r6"] * f, 5), round(c["z6"] * f, 3)
    vol = SENSITIVITY["volume"]
    if lv["volume"] != SENSITIVITY_DEFAULT:
        c["rvol3"] = round(c["rvol3"] * vol["rvol3"][lv["volume"]] / e["rvol3"], 3)
        e["rvol3"], e["inplay_rvolc"] = vol["rvol3"][lv["volume"]], vol["inplay_rvolc"][lv["volume"]]
    e["inplay_zday"] = SENSITIVITY["day"]["inplay_zday"][lv["day"]]
    slot = SENSITIVITY["cutoff"]["last_entry_slot"][lv["cutoff"]]
    p["session"]["last_entry_slot"], p["session"]["last_confirm_slot"] = slot, slot + 1
    ex = SENSITIVITY["exit"]
    hx["reversal_z3"], hx["giveback"] = ex["reversal_z3"][lv["exit"]], ex["giveback"][lv["exit"]]
    hold["soft_fails"] = ex["soft_fails"][lv["exit"]]
    # Stage A must still let through every name the in-play gate can accept (defaults: 1.5 and 1.2).
    pf["zday_abs"] = min(pf["zday_abs"], round(e["inplay_zday"] - 1.0, 3))
    pf["rvolc"] = min(pf["rvolc"], round(e["inplay_rvolc"] - 0.3, 3))
    return p


def sensitivity_dials() -> list[dict]:
    """The dials for the radar page: label, ends, hint, and what every notch sets (plain words)."""
    out = []
    for key, d in SENSITIVITY.items():
        notches = []
        for lv in range(SENSITIVITY_LEVELS):
            s = sensitivity_summary({key: lv})
            e, x = s["entry"], s["exit"]
            text = {
                "thrust": f"{e['z15']:g}σ in 15 min or {e['z30']:g}σ in 30 min, and a {e['move30_pct']:g}% move",
                "volume": f"last 15 min {e['rvol15']:g}× normal volume, day so far {e['day_rvol']:g}×",
                "day": f"the day's move at least {e['day_z']:g}σ of its usual daily range",
                "cutoff": (f"new entries until {e['cutoff_et']} ET, {e['cutoff_min_before_close']} min before the close "
                           "(half days: the same time before the close)"),
                "exit": (f"drops at {x['reversal_z15']:g}σ against the move, {x['giveback_pct']}% given back, or "
                         + ("1 weak bar" if x["soft_fails"] == 1 else f"{x['soft_fails']} weak bars in a row")),
            }[key]
            notches.append({"level": lv, "text": text})
        out.append({"key": key, "label": d["label"], "left": d["left"], "right": d["right"], "hint": d["hint"],
                    "notches": notches})
    return out


def sensitivity_summary(levels: object = None) -> dict:
    """The levels and the thresholds they produce, for the page (and the exit-watch gauges)."""
    lv = sensitivity_levels(levels)
    p = effective_params(lv)
    e, hx, hold = p["entry"], p["hard_exit"], p["hold"]
    return {
        "levels": lv,
        "default": SENSITIVITY_DEFAULT,
        "calibrated": all(v == SENSITIVITY_DEFAULT for v in lv.values()),
        "entry": {"z15": e["z3"], "z30": e["z6"], "move30_pct": round(100 * e["abs_r6"], 2), "rvol15": e["rvol3"],
                  "day_rvol": e["inplay_rvolc"], "day_z": e["inplay_zday"],
                  "cutoff_et": _slot_close_et(p["session"]["last_entry_slot"]),
                  "cutoff_min_before_close": 5 * (78 - 1 - p["session"]["last_entry_slot"])},
        "exit": {"reversal_z15": hx["reversal_z3"], "giveback_pct": round(100 * hx["giveback"]),
                 "soft_fails": hold["soft_fails"], "fade_z30": hold["fade_z6"], "fade_z15": hold["fade_z3"],
                 "dry_rvol": hold["dry_rvol3"], "stall_min": 5 * hold["stall_bars"], "stall_z30": hold["stall_z6"],
                 "min_dwell_min": 5 * hold["min_dwell"]},
    }
