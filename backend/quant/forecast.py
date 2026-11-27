"""The daily volatility forecast into quant_vol_forecasts (after the close, after the bars job).

1. Fit HAR (pooled across tickers, or per ticker: HAR.pooled) on every sample whose 21-session target has
   closed; the fit is recorded in quant_model_runs (kind "fit").
2. For each ticker whose last bar is the run's as-of session: forecast the mean variance of the next 21 sessions
   (annualised), the trailing 21-session realised vol (same estimator), and the ~30-day implied vol:
   chain_cm30 (that day's quant_chain_snapshots), else radar_iv30 (radar_option_metrics iv_30d_pct), else
   options_snapshot_atm (options_snapshots.atm_iv, the call nearest the money across the stored expiries, not a 30-day value: flagged iv_not_30d).
3. Earnings inside the horizon (earnings_calendar, else the Yahoo quote's date stored with the chain snapshot)
   are flagged and make the reading "not comparable"; so does a price pinned within the last shell window
   (flag `pinned_price`, config.ESTIMATOR["shell"]: a trust, a pending cash deal or a cash fund).
4. The reading (quant/reading.py) compares IV / forecast with the ticker's own earlier ratios, else today's
   cross-section of 30-day-IV names.
Display only: nothing here feeds conviction scores, the radar engine or any verdict.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from radar import calendar_nyse as cal
from utils.greeks import RISK_FREE_RATE

from . import har, reading
from .config import ESTIMATOR, HAR, IV, MODEL_NAME, MODEL_VERSION, READING, TRADING_DAYS
from .estimators import annualise
from .evaluate import Series, panel_from_rows
from .iv import cm30_from_snapshots

logger = logging.getLogger(__name__)

CM_SOURCES = ("chain_cm30", "radar_iv30")      # ~30-day IVs: the cross-section reference uses only these


def fit_models(panel: dict[str, Series], cfg: dict = HAR) -> tuple[har.HarFit | None, dict[str, har.HarFit]]:
    """Pooled fit on all complete samples, plus per-ticker fits when HAR.pooled is off."""
    xs, ys, own = [], [], {}
    for t, s in panel.items():
        m = np.isfinite(s.y) & np.all(np.isfinite(s.X), axis=1)
        if m.any():
            xs.append(s.X[m])
            ys.append(s.y[m])
            if not cfg["pooled"] and m.sum() >= int(cfg["min_obs_ticker"]):
                f = har.fit(s.X[m], s.y[m])
                if f is not None:
                    own[t] = f
    pooled = har.fit(np.vstack(xs), np.concatenate(ys)) if ys else None
    return pooled, own


def horizon_end(as_of: date, sessions: int, iv_days: int) -> date:
    """The later of the horizon's last session and the IV's calendar horizon."""
    last_session = cal.next_sessions(as_of + timedelta(days=1), sessions)[-1].day
    return max(last_session, as_of + timedelta(days=iv_days))


def earnings_in_window(as_of: date, end: date, dates: list[tuple[date, str | None]]) -> date | None:
    """The first earnings date that falls inside the forecast horizon. An as-of-day report counts unless it was
    before the open (BMO): the forecast is taken at the close, an after-close report moves tomorrow."""
    hits = []
    for d, timing in dates:
        t = (timing or "").upper()
        if as_of < d <= end or (d == as_of and "BMO" not in t and "BEFORE" not in t):
            hits.append(d)
    return min(hits) if hits else None


def implied_for(ticker: str, chains: dict[str, list[dict]], radar: dict[str, dict],
                snaps: dict[str, dict]) -> tuple[float | None, str | None, dict | None]:
    rows = chains.get(ticker)
    if rows:
        cm = cm30_from_snapshots(rows, RISK_FREE_RATE)
        if cm is not None:
            detail = {"method": cm["method"], "t_days": cm["t_days"],
                      "snapshot_at": max(r["snapshot_at"] for r in rows).isoformat(),
                      "expiry_methods": sorted({r.get("iv_method") for r in rows if r.get("iv_method")})}
            return cm["iv"], "chain_cm30", detail
    if ticker in radar:
        r = radar[ticker]
        return r["iv"], "radar_iv30", {"as_of": r["as_of"], "expiry": r.get("expiry")}
    if ticker in snaps:
        s = snaps[ticker]
        return s["iv"], "options_snapshot_atm", {"as_of": s["as_of"]}
    return None, None, None


def run(store: Any, tickers: list[str] | None = None, *, now: datetime | None = None, cfg: dict = HAR) -> dict:
    t0 = time.monotonic()
    now = now or datetime.now(timezone.utc)
    rows = store.load_bars(tickers)
    panel = panel_from_rows(rows, cfg)
    if not panel:
        return {"status": "error", "message": "no daily bars (run: python -m quant.run bars)"}
    pooled, own = fit_models(panel, cfg)
    if pooled is None:
        return {"status": "error", "message": "not enough complete samples to fit HAR"}
    last_dates = {t: s.dates[-1].astype(object) for t, s in panel.items()}
    as_of = max(last_dates.values())
    hzn = int(cfg["horizon"])
    first_bar = min(s.dates[0] for s in panel.values()).astype(object)
    run_id = store.record_model_run(
        kind="fit", model=MODEL_NAME, version=MODEL_VERSION,
        params={"har": cfg, "estimator": ESTIMATOR, "pooled": bool(cfg["pooled"])},
        window_start=first_bar, window_end=as_of, n_obs=pooled.n, n_tickers=len(panel),
        metrics={"pooled": pooled.to_json(), "own_fits": len(own)})

    current = [t for t, d in last_dates.items() if d == as_of]
    stale = sorted(t for t in panel if t not in current)
    end = horizon_end(as_of, hzn, int(IV["target_days"]))
    chains = store.load_chain_summaries(as_of, current)
    fallback_since = now - timedelta(hours=float(IV["fallback_max_age_hours"]))
    radar_iv = store.radar_iv30(current, fallback_since)
    snap_iv = store.options_snapshot_iv(current, fallback_since)
    cal_earn = store.earnings_between(current, as_of, end)
    own_hist = store.own_ratio_history(current, as_of - timedelta(days=int(READING["own_window_days"])), as_of,
                                       MODEL_VERSION)

    pending = []
    for t in current:
        s = panel[t]
        if int(np.isfinite(s.v).sum()) < int(cfg["min_bars_forecast"]) or not np.all(np.isfinite(s.X[-1])):
            continue
        model = own.get(t) or pooled
        fvar = float(model.predict_var(s.X[-1:])[0])
        fvol = float(annualise(fvar, TRADING_DAYS))
        rvol = float(annualise(s.trailing[-1], TRADING_DAYS)) if np.isfinite(s.trailing[-1]) else None
        iv, src, detail = implied_for(t, chains, radar_iv, snap_iv)
        earn_dates = list(cal_earn.get(t, []))
        earn_src = "earnings_calendar" if earn_dates else None
        if not earn_dates:
            yahoo = [r.get("earnings_date") for r in chains.get(t, []) if r.get("earnings_date")]
            if yahoo:
                earn_dates = [(min(yahoo), None)]
                earn_src = "yahoo_quote"
        ed = earnings_in_window(as_of, end, earn_dates)
        flags = []
        if ed is not None:
            flags.append("earnings_in_horizon")
        if earn_src is None:
            flags.append("earnings_unknown")
        if src and src != "chain_cm30":
            flags.append("iv_fallback")
        if src == "options_snapshot_atm":
            flags.append("iv_not_30d")
        if detail and detail.get("method") == "nearest":
            flags.append("iv_extrapolated")
        if t not in own and not cfg["pooled"]:
            flags.append("pooled_fit")
        pinned = bool(s.clean.get("pinned_now"))
        if pinned:
            flags.append("pinned_price")
        ratio = (iv / fvol) if iv and fvol > 0 else None
        price = rows[t]["close"][-1]
        pending.append({"ticker": t, "as_of_date": as_of, "model_version": MODEL_VERSION, "horizon": hzn,
                        "price": price, "forecast_vol": round(fvol, 6), "realized_vol_21d": _r(rvol),
                        "implied_vol_30d": _r(iv), "iv_source": src,
                        "iv_detail": {**(detail or {}), "earnings_source": earn_src} if (detail or earn_src) else None,
                        "iv_to_forecast": _r(ratio, 4), "earnings_in_horizon": ed is not None, "earnings_date": ed,
                        "flags": flags, "model_run_id": run_id, "_pinned": pinned})

    xs_ref = [p["iv_to_forecast"] for p in pending
              if p["iv_to_forecast"] is not None and not p["earnings_in_horizon"] and not p["_pinned"]
              and p["iv_source"] in CM_SOURCES]
    counts: dict[str, int] = {}
    for p in pending:
        rd, basis, pctl = reading.classify(p["iv_to_forecast"], own_ref=own_hist.get(p["ticker"], []),
                                           xs_ref=xs_ref, earnings_in_horizon=p["earnings_in_horizon"],
                                           pinned=p.pop("_pinned"))
        p.update(reading=rd, reading_basis=basis, ratio_pctl=_r(pctl, 4))
        counts[rd] = counts.get(rd, 0) + 1
    written = store.upsert_forecasts(pending)
    by_src: dict[str, int] = {}
    for p in pending:
        by_src[p["iv_source"] or "none"] = by_src.get(p["iv_source"] or "none", 0) + 1
    return {"status": "ok" if written else "error", "as_of": as_of.isoformat(), "forecasts": written,
            "stale_tickers": stale[:50], "stale_count": len(stale), "readings": counts, "iv_sources": by_src,
            "model_run_id": run_id, "coef": [round(float(c), 4) for c in pooled.coef],
            "duration_ms": int((time.monotonic() - t0) * 1000)}


def _r(v: float | None, nd: int = 6) -> float | None:
    return None if v is None or not np.isfinite(v) else round(float(v), nd)
