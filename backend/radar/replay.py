"""Momentum Radar replay and scorecard (signal-model.md section 8.2).

The replay drives the live `Engine` tick by tick: at each bar close + grace it builds the quotes a live
tick would have seen (last final close, regular-session volume so far), runs `stage_a`, then `step` with
that tick's Stage-B bars. Baselines for each session come from strictly earlier sessions, so nothing looks
ahead; only the scorecard reads later bars (forward returns).
"""
from __future__ import annotations

import copy
import csv
import gzip
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

from radar.baselines import BaselinePack, build_pack
from radar.calendar_nyse import ET, Session, session_for
from radar.config import REFERENCE_SYMBOLS
from radar.engine import Engine
from radar.features import Grid, build_grid
from radar.types import Bars, DailyBars, Quote

TARGETS = {"entries_per_day": [3.0, 12.0], "prec30_min": 0.53, "flap_rate_max": 0.06}


# ---------------------------------------------------------------------- loading
def _sessions_between(first_ts: int, last_ts: int) -> list[Session]:
    day = datetime.fromtimestamp(first_ts, tz=ET).date() - timedelta(days=1)
    end = datetime.fromtimestamp(last_ts, tz=ET).date()
    out = []
    while day <= end:
        s = session_for(day)
        if s is not None:
            out.append(s)
        day += timedelta(days=1)
    return out


def load_bars_csv(path: str) -> dict[str, dict[str, Bars]]:
    """symbol,ts,open,high,low,close,volume (ts = bar start, UTC epoch) -> session date -> symbol -> Bars.

    A bar belongs to the session whose [pre-open, post-close) window holds it; other rows are dropped."""
    import pandas as pd

    df = pd.read_csv(path, dtype={"symbol": str, "ts": np.int64, "volume": np.float64})
    sym = df["symbol"].to_numpy(dtype=str)
    ts = df["ts"].to_numpy(np.int64)
    cols = [df[c].to_numpy(np.float64) for c in ("open", "high", "low", "close")]
    vol = np.nan_to_num(df["volume"].to_numpy(np.float64)).astype(np.int64)
    if len(ts) == 0:
        return {}
    order = np.lexsort((ts, sym))
    sym, ts, vol, cols = sym[order], ts[order], vol[order], [c[order] for c in cols]
    last = np.ones(len(ts), dtype=bool)                  # duplicates: keep the last row
    last[:-1] = (sym[1:] != sym[:-1]) | (ts[1:] != ts[:-1])
    sessions = _sessions_between(int(ts.min()), int(ts.max()))
    starts = np.array([int(s.pre_open.timestamp()) for s in sessions], dtype=np.int64)
    ends = np.array([int(s.post_close.timestamp()) for s in sessions], dtype=np.int64)
    si = np.searchsorted(starts, ts, side="right") - 1
    keep = last & (si >= 0) & (ts < ends[np.maximum(si, 0)])
    sym, ts, vol, si, cols = sym[keep], ts[keep], vol[keep], si[keep], [c[keep] for c in cols]
    cut = np.flatnonzero((sym[1:] != sym[:-1]) | (si[1:] != si[:-1])) + 1
    out: dict[str, dict[str, Bars]] = {}
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(ts)]):
        day = sessions[si[a]].day.isoformat()
        out.setdefault(day, {})[str(sym[a])] = Bars(str(sym[a]), ts[a:b], *(c[a:b] for c in cols), vol[a:b])
    return dict(sorted(out.items()))


def load_daily_csv(path: str) -> dict[str, DailyBars]:
    """symbol,date,open,high,low,close,adjclose,volume -> DailyBars on the (split-adjusted) close."""
    import pandas as pd

    df = pd.read_csv(path, dtype={"symbol": str, "date": str})
    df = df.dropna(subset=["close"]).sort_values(["symbol", "date"], kind="stable")
    return {sym: DailyBars(sym, g["date"].to_numpy("datetime64[D]"), g["open"].to_numpy(float),
                           g["high"].to_numpy(float), g["low"].to_numpy(float), g["close"].to_numpy(float),
                           g["volume"].fillna(0).to_numpy(np.int64))
            for sym, g in df.groupby("symbol", sort=True)}


def load_meta(path: str) -> dict[str, dict]:
    """universe.csv -> {symbol: {name, sector, is_etf}}."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as fh:
        return {r["symbol"]: {"name": r.get("name"), "sector": r.get("sector") or None,
                              "is_etf": str(r.get("is_etf", "")).lower() == "true"} for r in csv.DictReader(fh)}


# ---------------------------------------------------------------------- simulation
@dataclass
class _Day:
    session: Session
    pack: BaselinePack
    bars: dict[str, Bars]
    grid: Grid
    row: dict[str, int]
    beta: np.ndarray
    spy: int


def _concat(chunks: list[Bars]) -> Bars:
    return Bars(chunks[0].symbol, *(np.concatenate([getattr(b, a) for b in chunks])
                                    for a in ("ts", "o", "h", "l", "c", "v")))


def _prepare(bars_by_session: dict[str, dict[str, Bars]], daily: dict[str, DailyBars], params: dict,
             sessions: list[str], meta: dict[str, dict]) -> list[_Day]:
    """Per replay session: a pack from earlier bars only, and that session's full-day grid for scoring."""
    ordered = sorted(bars_by_session)
    days = []
    for d in sessions:
        session = session_for(date.fromisoformat(d))
        past = [s for s in ordered if s < d]
        history: dict[str, list[Bars]] = {}
        for s in past[-2 * params["baseline"]["sessions"]:]:
            for sym, b in bars_by_session[s].items():
                history.setdefault(sym, []).append(b)
        cutoff = np.datetime64(session.day)
        daily_past = {sym: DailyBars(sym, *(getattr(x, a)[x.day < cutoff] for a in ("day", "o", "h", "l", "c", "v")))
                      for sym, x in daily.items()}
        pack = build_pack(session, {s: _concat(v) for s, v in history.items()}, daily_past, meta, params)
        today = {s: b for s, b in bars_by_session[d].items() if s in pack.symbols}
        syms = sorted(pack.symbols)
        grid = build_grid(today, syms, session.open_epoch, session.n_slots)
        beta = np.array([pack.symbols[s].beta for s in syms])
        days.append(_Day(session, pack, today, grid, {s: i for i, s in enumerate(syms)}, beta, syms.index("SPY")))
    return days


def _final(b: Bars, last_start: int) -> Bars:
    """The bars a live fetch has once the bar starting at `last_start` is final (no later bars)."""
    n = int(np.searchsorted(b.ts, last_start, side="right"))
    return b if n == len(b) else Bars(b.symbol, *(getattr(b, a)[:n] for a in ("ts", "o", "h", "l", "c", "v")))


def _quotes(day: _Day, k: int, cumv: np.ndarray) -> dict[str, Quote]:
    """What a quote snapshot shows right after bar k is final: its close and the session volume so far."""
    g = day.grid
    return {s: Quote(s, price=float(g.c[i, k]), day_volume=int(cumv[i, k]))
            for s, i in day.row.items() if g.first[i] <= k}


def simulate(days: list[_Day], params: dict, *, stage_a: bool = True, roundtrip: bool = False):
    """Episodes (paired ENTER/EXIT events) and one row per processed slot."""
    grace = params["session"]["bar_final_grace_s"]
    episodes, ticks = [], []
    for day in days:
        ss = day.session
        engine = Engine(params, day.pack, ss)
        cumv = np.cumsum(day.grid.v, axis=1)
        entries: dict[tuple, dict] = {}
        for k in range(ss.n_slots):
            now = ss.slot_start(k) + 300 + grace
            wanted = engine.stage_a(_quotes(day, k, cumv), now) if stage_a else day.bars
            out = engine.step({s: _final(day.bars[s], ss.slot_start(k)) for s in wanted if s in day.bars}, now)
            for e in out.events:
                key = (e["ticker"], e["episode"])
                if e["type"] == "ENTER":
                    entries[key] = {"session": ss.day.isoformat(), "day": day, "sym": e["ticker"],
                                    "dir": 1 if e["dir"] == "up" else -1, "entry_k": e["slot"],
                                    "entry_px": float(day.grid.c[day.row[e["ticker"]], e["slot"]]),
                                    "late": e["late"], "intensity": e["intensity"]}
                else:
                    ep = entries.pop(key)
                    ep.update(exit_k=e["slot"], reason=e["reason"])
                    episodes.append(ep)
            members = sum(1 for r in out.member_rows if r["role"] == "member")
            for slot in out.processed_slots:
                ticks.append({"session": ss.day.isoformat(), "k": slot, "members": members,
                              "market": out.snapshot["market"]["mode"] == "market",
                              "stage_b": out.snapshot["counts"]["stage_b"],
                              "universe": out.snapshot["counts"]["universe"]})
            if roundtrip:
                engine = Engine(params, day.pack, ss, json.loads(json.dumps(engine.state_dict())))
    return episodes, ticks


# ---------------------------------------------------------------------- scorecard
def _rel(day: _Day, i: int, k0: int, k1: int) -> float:
    c, s = day.grid.c[i], day.grid.c[day.spy]
    return float(np.log(c[k1] / c[k0]) - day.beta[i] * np.log(s[k1] / s[k0]))


def wilson(hits: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = hits / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(centre - half, 4), round(centre + half, 4)]


def _share(v: list[bool]) -> float | None:
    return round(float(np.mean(v)), 4) if v else None


def _forward(ep: dict, lag: int) -> dict:
    """Forward SPY-relative returns in the entry direction from the entry bar (+lag), h = 3, 6, 12 bars."""
    day, d = ep["day"], ep["dir"]
    i, last = day.row[ep["sym"]], day.session.n_slots - 1
    k = min(ep["entry_k"] + lag, last)
    return {h: d * _rel(day, i, k, min(k + h, last)) if min(k + h, last) > k else math.nan for h in (3, 6, 12)}


def _metrics(episodes: list[dict], ticks: list[dict], n_sessions: int, params: dict) -> dict:
    out: dict = {"sessions": n_sessions, "entries": len(episodes),
                 "entries_per_day": round(len(episodes) / n_sessions, 3) if n_sessions else None}
    per_day: dict[str, int] = {}
    for e in episodes:
        per_day[e["session"]] = per_day.get(e["session"], 0) + 1
    counts = [per_day.get(t, 0) for t in sorted({t["session"] for t in ticks})]
    if counts:
        out["entries_per_day_dist"] = {"median": float(np.median(counts)), "p90": float(np.percentile(counts, 90)),
                                       "max": int(max(counts)), "days_without_entries": int(sum(c == 0 for c in counts))}
    cap = params["caps"]["max_members"]
    lo, hi = params["session"]["first_entry_slot"], params["session"]["session_end_slot"]
    n_mem = [t["members"] for t in ticks if lo <= t["k"] <= hi]
    if n_mem:
        out["concurrency"] = {"median": float(np.median(n_mem)), "p95": float(np.percentile(n_mem, 95)),
                              "max": int(max(n_mem)), "cap_reached_share": _share([n >= cap for n in n_mem])}
        out["market_mode_tick_share"] = _share([t["market"] for t in ticks if lo <= t["k"] <= hi])
    stage = [t["stage_b"] / t["universe"] for t in ticks if t["universe"]]
    if stage:
        out["stage_b_share_mean"] = round(float(np.mean(stage)), 4)
    if not episodes:
        return out
    fw = [_forward(e, 0) for e in episodes]
    fw_late = [_forward(e, 1) for e in episodes]
    for h, name in ((3, "prec15"), (6, "prec30"), (12, "prec60")):
        hits = [f[h] > 0 for f in fw if not math.isnan(f[h])]
        out[name] = _share(hits)
        if h == 6:
            out["prec30_n"] = len(hits)
            out["prec30_wilson95"] = wilson(sum(hits), len(hits))
            out["mean_f30_bp"] = round(float(np.nanmean([f[6] for f in fw])) * 1e4, 2)
    out["latency_1bar"] = {name: _share([f[h] > 0 for f in fw_late if not math.isnan(f[h])])
                           for h, name in ((3, "prec15"), (6, "prec30"), (12, "prec60"))}
    hold, mfe_mae, post = [], [], []
    for e in episodes:
        day, d, k = e["day"], e["dir"], e["entry_k"]
        i, last = day.row[e["sym"]], day.session.n_slots - 1
        c, hh, ll = day.grid.c[i], day.grid.h[i], day.grid.l[i]
        win = c[max(k - 6, 0):k + 1]
        base = win.min() if d > 0 else win.max()
        j = min(k + 6, last)
        thrust = d * (e["entry_px"] - base)
        if j > k and thrust > 0:
            hold.append(d * (c[j] - e["entry_px"]) > -0.5 * thrust)
        if j > k:
            up, dn = hh[k + 1:j + 1].max() / c[k] - 1, 1 - ll[k + 1:j + 1].min() / c[k]
            mfe_mae.append((up if d > 0 else dn) > (dn if d > 0 else up))
        x = e["exit_k"]
        jx = min(x + 6, last)
        if jx > x:
            post.append(d * _rel(day, i, x, jx) > 0)
    out["hold30"] = _share(hold)
    out["mfe_gt_mae"] = _share(mfe_mae)
    out["post_exit_continuation"] = _share(post)
    dwell = np.array([e["exit_k"] - e["entry_k"] for e in episodes])
    out["dwell_bars"] = {"median": float(np.median(dwell)), "p25": float(np.percentile(dwell, 25)),
                         "p75": float(np.percentile(dwell, 75))}
    gaps = []
    last_exit: dict[tuple, int] = {}
    for e in sorted(episodes, key=lambda x: (x["session"], x["sym"], x["entry_k"])):
        key = (e["session"], e["sym"])
        if key in last_exit:
            gaps.append(e["entry_k"] - last_exit[key])
        last_exit[key] = e["exit_k"]
    flaps = sum(g <= 6 for g in gaps) + int((dwell <= 2).sum())
    out.update(flaps=flaps, flap_rate=round(flaps / len(episodes), 4),
               flaps_per_day=round(flaps / n_sessions, 3), short_episode_share=_share(list(dwell <= 2)),
               reentries_within_30min=int(sum(g <= 6 for g in gaps)),
               up_share=_share([e["dir"] > 0 for e in episodes]),
               distinct_symbol_days=len({(e["session"], e["sym"]) for e in episodes}),
               late_entries=int(sum(e["late"] for e in episodes)))
    reasons: dict[str, int] = {}
    for e in episodes:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    out["exit_reasons"] = {r: round(n / len(episodes), 3) for r, n in sorted(reasons.items(), key=lambda x: -x[1])}
    return out


def _all_ticks_baseline(days: list[_Day], params: dict) -> dict:
    """Baseline (i): every eligible symbol-tick, direction = sign(z3), continuation 30 minutes later."""
    u = params["universe"]
    lo, hi = params["session"]["first_entry_slot"], params["session"]["last_entry_slot"]
    hits, big = [], []
    for day in days:
        engine = Engine(params, day.pack, day.session)
        ctx = engine.context(day.bars)
        n = day.session.n_slots
        c, spy, beta = ctx.grid.c, ctx.spy, engine.beta[ctx.pix]
        for k in range(lo, min(hi, n - 2) + 1):
            f = ctx.feats.at(k)
            j = min(k + 6, n - 1)
            d = np.sign(f["z3"])
            with np.errstate(divide="ignore", invalid="ignore"):
                v = d * (np.log(c[:, j] / c[:, k]) - beta * np.log(c[spy, j] / c[spy, k]))
            ok = ctx.eligible & f["fresh"] & (f["dollar3"] >= u["tick_dollar3_min"]) & np.isfinite(v) & (d != 0)
            hits.append(v[ok] > 0)
            big.append(v[ok & (np.abs(f["z3"]) >= 3.0)] > 0)
    h, b3 = np.concatenate(hits), np.concatenate(big)
    return {"prec30": round(float(h.mean()), 4), "n": int(h.size),
            "prec30_after_z3_ge_3": round(float(b3.mean()), 4) if b3.size else None, "n_z3_ge_3": int(b3.size)}


def naive_params(params: dict) -> dict:
    """Baseline (ii): thrust list with E1 + E2 and the structure check only, one tick, no hysteresis."""
    p = copy.deepcopy(params)
    p["entry"].update(rvol3=0.0, rvolc=0.0, er6=0.0, inplay_zday=-99.0, inplay_rvolc=0.0)
    p["confirm"].update(fast_path_score=0.0, fast_path_rvol3=0.0)
    p["hold"].update(min_dwell=1, soft_fails=1)
    p["reentry"].update(cool_same_bars=0, cool_opp_bars=0, require_new_peak=False, max_episodes=99)
    p["caps"].update(max_per_sector_dir=99)
    p["market"].update(max_new_mkt_dir=99)
    return p


def run(bars_by_session: dict[str, dict[str, Bars]], daily: dict[str, DailyBars], params: dict,
        sessions: list[str], *, meta: dict[str, dict] | None = None, stage_a: bool = True,
        baselines: bool = True, roundtrip: bool = False) -> dict:
    """Scorecard per signal-model.md 8.2 for `sessions` (each needs its base sessions in bars_by_session)."""
    days = _prepare(bars_by_session, daily, params, sorted(sessions), meta or {})
    episodes, ticks = simulate(days, params, stage_a=stage_a, roundtrip=roundtrip)
    card = {"params_version": params["params_version"], "first_session": days[0].session.day.isoformat(),
            "last_session": days[-1].session.day.isoformat(),
            "eligible_mean": round(float(np.mean([sum(b.eligible for b in d.pack.symbols.values()) for d in days])), 1),
            "stage_a": stage_a, "rec": _metrics(episodes, ticks, len(days), params)}
    half = len(days) // 2
    split = {}
    for name, part in (("calibration", days[:half]), ("validation", days[half:])):
        names = {d.session.day.isoformat() for d in part}
        m = _metrics([e for e in episodes if e["session"] in names], [t for t in ticks if t["session"] in names],
                     len(part), params)
        split[name] = {"sessions": len(part), "entries": m["entries"], "prec30": m.get("prec30"),
                       "prec30_n": m.get("prec30_n", 0)}
    card["splits"] = split
    if baselines:
        card["baseline_all_ticks"] = _all_ticks_baseline(days, params)
        naive = naive_params(params)
        n_eps, n_ticks = simulate(days, naive, stage_a=False)
        card["baseline_naive"] = _metrics(n_eps, n_ticks, len(days), naive)
    rec = card["rec"]
    card["targets"] = TARGETS
    card["meets_targets"] = {
        "entries_per_day": TARGETS["entries_per_day"][0] <= (rec["entries_per_day"] or 0) <= TARGETS["entries_per_day"][1],
        "prec30": (rec.get("prec30") or 0) >= TARGETS["prec30_min"],
        "flap_rate": rec.get("flap_rate", 1.0) <= TARGETS["flap_rate_max"]}
    return card


def replay_sessions(bars_by_session: dict[str, dict[str, Bars]], seed: int, last_n: int | None = None) -> list[str]:
    """Sessions after the first `seed` (which only seed baselines), optionally the last `last_n` of them."""
    ordered = sorted(bars_by_session)[seed:]
    return ordered[-last_n:] if last_n else ordered


def universe_filter(bars_by_session: dict[str, dict[str, Bars]], meta: dict[str, dict]) -> dict[str, dict[str, Bars]]:
    """Drop ETFs other than the reference symbols, as the live scan does."""
    drop = {s for s, x in meta.items() if x.get("is_etf") and s not in REFERENCE_SYMBOLS}
    return {d: {s: b for s, b in day.items() if s not in drop} for d, day in bars_by_session.items()}
