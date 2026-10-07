"""Baseline pack (signal-model.md 1.3 and 3) on small synthetic markets.

`synth_market` and `pack_for` are also used by test_engine.py and test_replay.py.
"""
from __future__ import annotations

import copy
import json
import math
from datetime import date

import numpy as np
import pytest

from radar.baselines import BaselinePack, base_sessions, build_pack, extend_pack
from radar.calendar_nyse import Session, next_sessions, previous_sessions, session_for
from radar.config import PARAMS
from radar.types import Bars, DailyBars

SPY_SIGMA = 0.0005
STOCK_SIGMA = 0.001


def synth_market(days: list[Session], spec: dict[str, dict], *, seed: int = 7, history: int = 30,
                 custom: dict[tuple[str, str], dict] | None = None
                 ) -> tuple[dict[str, dict[str, Bars]], dict[str, DailyBars]]:
    """Regular-session 5-minute bars for `days` and daily bars (with `history` daily-only sessions before them).

    spec[sym] = {"px", "vol" (shares per slot), "beta", "sigma"}. custom[(day, sym)] may hold "gap" (log gap
    added at the open), "ret" (per-slot log returns added to the noise), "vmult" (per-slot volume multiplier),
    "drop" (slots with no bar) and "zero_vol" (slots printed with zero volume and H > L). SPY's returns flow
    into every other symbol through its beta.
    """
    rng = np.random.default_rng(seed)
    custom = custom or {}
    px = {s: float(v.get("px", 50.0)) for s, v in spec.items()}
    daily: dict[str, list[tuple]] = {s: [] for s in spec}
    for ses in previous_sessions(days[0].day, history):
        spy_r = rng.normal(0, 0.008)
        for s, v in spec.items():
            r = spy_r if s == "SPY" else v.get("beta", 1.0) * spy_r + rng.normal(0, 0.012)
            o, c = px[s], px[s] * math.exp(r)
            daily[s].append((ses.day, o, max(o, c) * 1.002, min(o, c) * 0.998, c, v.get("vol", 100_000) * 78))
            px[s] = c
    bars: dict[str, dict[str, Bars]] = {}
    for ses in days:
        n, key = ses.n_slots, ses.day.isoformat()
        spy_c = custom.get((key, "SPY"), {})
        spy_gap = rng.normal(0, 0.004) + spy_c.get("gap", 0.0)
        spy_ret = rng.normal(0, SPY_SIGMA, n) + np.asarray(spy_c.get("ret", np.zeros(n)))[:n]
        day_bars = {}
        for s, v in spec.items():
            c_ = custom.get((key, s), {})
            if s == "SPY":
                gap, ret = spy_gap, spy_ret
            else:
                beta = v.get("beta", 1.0)
                gap = beta * spy_gap + rng.normal(0, 0.008) + c_.get("gap", 0.0)
                ret = beta * spy_ret + rng.normal(0, v.get("sigma", STOCK_SIGMA), n) \
                    + np.asarray(c_.get("ret", np.zeros(n)))[:n]
            close = px[s] * math.exp(gap) * np.exp(np.cumsum(ret))
            opens = np.r_[px[s] * math.exp(gap), close[:-1]]
            wick = 1.0 + 0.0003 * rng.random(n)
            high, low = np.maximum(opens, close) * wick, np.minimum(opens, close) / wick
            vol = np.round(v.get("vol", 100_000) * rng.uniform(0.8, 1.2, n)
                           * np.asarray(c_.get("vmult", np.ones(n)))[:n]).astype(np.int64)
            for j in c_.get("zero_vol", ()):
                vol[j] = 0
            keep = np.ones(n, dtype=bool)
            keep[list(c_.get("drop", ()))] = False
            ts = np.array([ses.slot_start(j) for j in range(n)], dtype=np.int64)
            day_bars[s] = Bars(s, ts[keep], opens[keep], high[keep], low[keep], close[keep], vol[keep])
            daily[s].append((ses.day, opens[0], high.max(), low.min(), close[-1], int(vol.sum())))
            px[s] = float(close[-1])
        bars[key] = day_bars
    out = {}
    for s, rows in daily.items():
        cols = list(zip(*rows))
        out[s] = DailyBars(s, np.array(cols[0], dtype="datetime64[D]"), *(np.array(c, dtype=float) for c in cols[1:5]),
                           np.array(cols[5], dtype=np.int64))
    return bars, out


def concat_bars(bars_by_session: dict[str, dict[str, Bars]], before: str | None = None) -> dict[str, Bars]:
    chunks: dict[str, list[Bars]] = {}
    for day in sorted(bars_by_session):
        if before is None or day < before:
            for s, b in bars_by_session[day].items():
                chunks.setdefault(s, []).append(b)
    return {s: Bars(s, *(np.concatenate([getattr(b, a) for b in bs]) for a in ("ts", "o", "h", "l", "c", "v")))
            for s, bs in chunks.items()}


def pack_for(bars_by_session, daily, session: Session, meta: dict | None = None, params: dict = PARAMS) -> BaselinePack:
    return build_pack(session, concat_bars(bars_by_session, session.day.isoformat()), daily, meta or {}, params)


SPEC = {"SPY": {"px": 600.0, "vol": 400_000, "sigma": SPY_SIGMA}, "QQQ": {"px": 500.0, "vol": 300_000},
        "IWM": {"px": 220.0, "vol": 200_000}, "AAA": {"px": 50.0}, "BBB": {"px": 80.0, "beta": 2.0},
        "LOW": {"px": 3.0, "vol": 5_000_000}, "THIN": {"px": 40.0, "vol": 2_000}}


@pytest.fixture(scope="module")
def market():
    days = next_sessions(date(2026, 8, 3), 22)
    bars, daily = synth_market(days, SPEC)
    return days, bars, daily


def test_base_sessions_skip_half_days():
    s = session_for(date(2026, 12, 1))
    base = base_sessions(s, 20)
    assert len(base) == 20 and all(not b.early_close for b in base)
    assert date(2026, 11, 27) not in {b.day for b in base} and base[-1].day == date(2026, 11, 30)
    assert all(b.day < s.day for b in base)


def test_pack_values(market):
    days, bars, daily = market
    target = days[20]
    pack = pack_for(bars, daily, target, {"QQQ": {"is_etf": True}})
    assert pack.asof == target.day.isoformat() and pack.params_version == PARAMS["params_version"]
    assert pack.tod_m.shape == (78,) and pack.tod_m[0] == pack.tod_m[1]
    lo, hi = PARAMS["baseline"]["tod_clamp"]
    assert np.all((pack.tod_m >= lo) & (pack.tod_m <= hi)) and abs(np.median(pack.tod_m[1:]) - 1.0) < 0.1
    spy, aaa, bbb = pack.symbols["SPY"], pack.symbols["AAA"], pack.symbols["BBB"]
    assert spy.beta == 1.0 and pack.sigma5_spy == spy.sigma5
    assert spy.sigma5 == pytest.approx(SPY_SIGMA, rel=0.2)
    assert aaa.beta == pytest.approx(1.0, abs=0.1)                     # shrunk halfway to 1
    assert bbb.beta == pytest.approx(0.5 * 2.0 + 0.5, abs=0.1)
    assert aaa.sigma5 == pytest.approx(STOCK_SIGMA, rel=0.2)
    assert aaa.vm.shape == (78,) and aaa.cvm.shape == (78,)
    assert aaa.vm[10] == pytest.approx(100_000, rel=0.1) and aaa.cvm[-1] == pytest.approx(78 * 100_000, rel=0.05)
    assert np.all(np.diff(aaa.cvm) > 0)
    assert aaa.missing_share == 0.0 and aaa.n_base == 20
    prev = daily["AAA"].c[daily["AAA"].day < np.datetime64(target.day)]
    assert aaa.prev_close == pytest.approx(prev[-1])
    assert aaa.sigma_d == pytest.approx(np.std(np.diff(np.log(prev[-21:])), ddof=1))
    assert aaa.adv20_usd > 25e6 and aaa.medbar_usd > 150e3
    assert aaa.eligible and aaa.ineligible_reason is None
    reasons = {s: b.ineligible_reason for s, b in pack.symbols.items() if not b.eligible}
    assert reasons == {"SPY": "reference index", "QQQ": "reference index", "IWM": "reference index",
                       "LOW": "price under $5", "THIN": "low dollar volume"}


def test_pack_has_no_look_ahead(market):
    days, bars, daily = market
    target = days[20]
    pack = pack_for(bars, daily, target)
    later = copy.deepcopy(bars)
    for day in (days[20], days[21]):
        for b in later[day.day.isoformat()].values():
            b.c *= 3.0
            b.v *= 50
    cut = np.datetime64(target.day)
    daily_later = {s: DailyBars(s, d.day, d.o, d.h, d.l, np.where(d.day >= cut, d.c * 3, d.c), d.v)
                   for s, d in daily.items()}
    assert pack_for(later, daily_later, target).to_json() == pack.to_json()


def test_gappy_and_short_history_are_ineligible(market):
    days, bars, daily = market
    target = days[20]
    gappy = copy.deepcopy(bars)
    for day in days[:20]:
        b = gappy[day.day.isoformat()]["AAA"]
        keep = np.arange(len(b)) % 20 != 5                      # 4 of 78 slots missing every day (5%)
        gappy[day.day.isoformat()]["AAA"] = Bars("AAA", b.ts[keep], b.o[keep], b.h[keep], b.l[keep], b.c[keep],
                                                 b.v[keep])
    for day in days[:8]:
        del gappy[day.day.isoformat()]["BBB"]
    pack = pack_for(gappy, daily, target)
    assert pack.symbols["AAA"].missing_share == pytest.approx(4 / 78, abs=1e-9)
    assert pack.symbols["AAA"].ineligible_reason == "gappy 5-minute data"
    assert pack.symbols["BBB"].n_base == 12 and pack.symbols["BBB"].ineligible_reason == "short 5-minute history"


def test_stale_daily_close_is_not_a_prev_close(market):
    days, bars, daily = market
    target = days[20]
    d = daily["AAA"]
    keep = d.day < np.datetime64(days[19].day)                   # the previous session's daily bar is missing
    trimmed = {**daily, "AAA": DailyBars("AAA", *(getattr(d, a)[keep] for a in ("day", "o", "h", "l", "c", "v")))}
    b = pack_for(bars, trimmed, target).symbols["AAA"]
    assert b.prev_close is None and b.ineligible_reason == "no prior close"


def test_json_roundtrip(market):
    days, bars, daily = market
    pack = pack_for(bars, daily, days[20], {"AAA": {"name": "Alpha Inc", "sector": "Industrials"}})
    doc = json.loads(json.dumps(pack.to_json()))
    back = BaselinePack.from_json(doc)
    assert back.to_json() == pack.to_json()
    a = back.symbols["AAA"]
    assert (a.name, a.sector) == ("Alpha Inc", "Industrials") and a.vm.dtype == np.float64
    assert len(json.dumps(doc)) < 2 * 1024 * 1024


def test_extend_pack_matches_a_full_build(market):
    days, bars, daily = market
    target = days[20]
    full = pack_for(bars, daily, target)
    no_bbb = {d: {s: b for s, b in x.items() if s != "BBB"} for d, x in bars.items()}
    base = pack_for(no_bbb, daily, target)
    assert "BBB" not in base.symbols
    extended = extend_pack(base, target, {"BBB": concat_bars(bars, target.day.isoformat())["BBB"]},
                           daily, {"BBB": {"name": "Beta Corp", "sector": "Energy"}}, PARAMS)
    got, want = extended.symbols["BBB"].to_json(), full.symbols["BBB"].to_json()
    assert got.pop("name") == "Beta Corp" and got.pop("sector") == "Energy"
    want.pop("name"), want.pop("sector")
    for key, value in want.items():
        assert got[key] == pytest.approx(value, rel=0.08) if isinstance(value, float) else got[key] == value, key
    assert extended.symbols["AAA"] is base.symbols["AAA"]
    assert extend_pack(extended, target, {"BBB": bars[days[0].day.isoformat()]["BBB"]}, daily, {}, PARAMS) is extended


def test_build_pack_needs_spy(market):
    days, bars, daily = market
    with pytest.raises(ValueError):
        build_pack(days[20], {s: b for s, b in concat_bars(bars).items() if s != "SPY"}, daily, {}, PARAMS)


def test_build_pack_needs_spy_prior_close(market):
    """ENG-1: without SPY's prior-session close every zday is NaN and nothing could enter all session, so the
    pack is refused (the tick retries) instead of being cached for the day."""
    days, bars, daily = market
    target = days[20]
    with pytest.raises(ValueError, match="prior-session close"):
        pack_for(bars, {s: d for s, d in daily.items() if s != "SPY"}, target)
    d = daily["SPY"]
    keep = d.day < np.datetime64(days[19].day)                   # SPY's previous-session daily bar is missing
    stale = {**daily, "SPY": DailyBars("SPY", *(getattr(d, a)[keep] for a in ("day", "o", "h", "l", "c", "v")))}
    with pytest.raises(ValueError, match="prior-session close"):
        pack_for(bars, stale, target)
    no_aaa = {s: x for s, x in daily.items() if s != "AAA"}       # any other name only becomes ineligible
    assert pack_for(bars, no_aaa, target).symbols["AAA"].ineligible_reason == "no prior close"
