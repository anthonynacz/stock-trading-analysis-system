"""Walk-forward, out-of-sample evaluation of the HAR volatility forecast against naive baselines.

    python -m quant.evaluate                      # bars from quant_daily_bars, scorecard printed and recorded
    python -m quant.evaluate --csv bars.csv       # bars from a CSV (ticker,date,open,high,low,close,adj_close)
    python -m quant.evaluate --no-record --json   # print only (JSON), nothing written

Models (declared up front; the registry row lists them all):
- trailing_21d: mean daily variance of the last 21 sessions (the naive forecast to beat)
- long_run_mean: the ticker's mean daily variance over all history up to t
- har_pooled: one HAR regression across all tickers
- har_ticker: one HAR regression per ticker (pooled when a ticker has < HAR.min_obs_ticker samples)
Target: mean daily variance (same estimator) over the next 21 sessions. Losses: QLIKE on variance
(rv/f - ln(rv/f) - 1, robust to noise in the target) and RMSE on annualised volatility (vol points).

Walk-forward: every EVAL.refit_every sessions from EVAL.min_train_days on, each HAR model is refit on the
samples whose 21-session target window has closed by the refit date, then forecasts the following block of
sessions. A forecast made at the close of day t uses data through t only.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from . import har
from .config import BREAKDOWN, ESTIMATOR, EVAL, HAR, MODEL_NAME, MODEL_VERSION, TRADING_DAYS, VERDICT_T
from .cleaning import align_sessions, session_reference, variance_series

logger = logging.getLogger(__name__)

MODELS = ("trailing_21d", "long_run_mean", "har_pooled", "har_ticker")
BASELINE = "trailing_21d"


@dataclass
class Series:
    """One ticker's daily variance series, ascending, with the pieces the models need precomputed."""
    ticker: str
    dates: np.ndarray        # datetime64[D] (the panel's session calendar inside the ticker's span: ESTIMATOR.gaps)
    v: np.ndarray            # daily variance (NaN where the bar was invalid or a cleaning rule made it missing)
    X: np.ndarray            # HAR features (log lag means)
    y: np.ndarray            # log forward-mean target (uses the future: training/evaluation only)
    rv: np.ndarray           # forward mean variance (level), the evaluation truth
    trailing: np.ndarray     # mean variance of the last 21 sessions
    long_run: np.ndarray     # expanding mean variance
    clean: dict = field(default_factory=dict)    # what the cleaning rules did (cleaning.variance_series info)


def make_series(ticker: str, dates, o, h, l, c, adj_c=None, cfg: dict = HAR, est: dict = ESTIMATOR,
                volume=None, inserted: int = 0) -> Series:
    dates = np.asarray(dates, dtype="datetime64[D]")
    v, info = variance_series(o, h, l, c, adj_c, volume, est, TRADING_DAYS)
    info["inserted"] = int(inserted)
    hzn = int(cfg["horizon"])
    X = har.features(v, tuple(cfg["lags"]), float(est["floor"]))
    y = har.target(v, hzn, float(est["floor"]))
    rv = har.forward_mean(v, hzn)
    ok = np.isfinite(v)
    cs = np.cumsum(np.where(ok, v, 0.0))
    cnt = np.cumsum(ok)
    with np.errstate(invalid="ignore", divide="ignore"):
        long_run = np.where(cnt >= 21, cs / np.maximum(cnt, 1), np.nan)
    return Series(ticker, dates, v, X, y, rv, har.rolling_mean(v, 21), long_run, info)


def panel_from_rows(rows: dict[str, dict[str, Any]], cfg: dict = HAR, est: dict = ESTIMATOR) -> dict[str, Series]:
    """{ticker: {"date": [...], "open": [...], ...}} -> {ticker: Series} (tickers under 2 horizons are dropped).
    The forecast job builds its panel here too, so a cleaning rule in ESTIMATOR applies to both alike."""
    out = {}
    gaps = est.get("gaps")
    ref = session_reference(rows, float(gaps.get("ref_share", 0.5))) if gaps else None
    for t, r in rows.items():
        if len(r["date"]) < 2 * int(cfg["horizon"]):
            continue
        inserted = 0
        if ref is not None:
            r, inserted = align_sessions(r, ref)
        out[t] = make_series(t, r["date"], r["open"], r["high"], r["low"], r["close"], r.get("adj_close"), cfg,
                             est, r.get("volume"), inserted)
    return out


def _eligible(s: Series, first: np.datetime64) -> int:
    """Ticker-days the walk-forward can score from `first` on (the same mask as walk_forward)."""
    m = (s.dates >= first) & np.isfinite(s.rv) & np.all(np.isfinite(s.X), axis=1) & np.isfinite(s.trailing) \
        & np.isfinite(s.long_run)
    return int(m.sum())


RULES = ("no_trade_missing", "gaps", "splits", "shell")


def _rule_counts(panel: dict[str, Series]) -> dict[str, dict[str, Any]]:
    """Per rule: bars it made missing (or repaired) and the tickers it touched, from the panel's clean info."""
    out: dict[str, dict[str, Any]] = {
        "no_trade_missing": {"bars_removed": 0, "tickers": 0},
        "gaps": {"sessions_inserted": 0, "bars_removed": 0, "tickers": 0},
        "splits": {"repaired": 0, "adj_only": 0, "adj_confirmed": 0, "ambiguous_missing": 0, "left": 0,
                   "special_dividends": 0, "tickers": 0, "events": {}},
        "shell": {"bars_removed": 0, "tickers": 0, "first_usable": {}},
    }
    for t, s in panel.items():
        ci = s.clean
        if ci.get("no_trade"):
            out["no_trade_missing"]["bars_removed"] += ci["no_trade"]
            out["no_trade_missing"]["tickers"] += 1
        if ci.get("inserted") or ci.get("post_gap"):
            out["gaps"]["sessions_inserted"] += ci.get("inserted", 0)
            out["gaps"]["bars_removed"] += ci.get("post_gap", 0)
            out["gaps"]["tickers"] += 1
        out["splits"]["special_dividends"] += ci.get("special_dividends", 0)
        ev = list(ci.get("split_events", []))
        for e in ci.get("split_events", []):
            key = "ambiguous_missing" if e["kind"] == "ambiguous" else e["kind"]
            out["splits"][key] += 1
        if ev:
            out["splits"]["tickers"] += 1
            out["splits"]["events"][t] = [{"date": str(s.dates[e["i"]]), "kind": e["kind"], "ratio": e["ratio"]}
                                          for e in ev]
        if ci.get("shell_end", -1) >= 0:
            L = ci["shell_end"]
            out["shell"]["bars_removed"] += ci.get("shell_bars", 0)
            out["shell"]["tickers"] += 1
            out["shell"]["first_usable"][t] = {"shell_last": str(s.dates[L]),
                                               "first_usable": str(s.dates[L + 1]) if L + 1 < len(s.dates) else None}
    return out


def cleaning_report(rows: dict[str, dict[str, Any]], panel: dict[str, Series], cfg: dict = HAR,
                    est: dict = ESTIMATOR, ev: dict = EVAL) -> dict[str, Any]:
    """What the ESTIMATOR cleaning rules did: per rule, the bars made missing or repaired, the tickers affected and
    how many out-of-sample ticker-days the evaluation lost (or gained) against the same bars with only that rule
    off; totals against the bars with every rule off."""
    rules = [k for k in RULES if est.get(k)]
    adj_missing = 0
    for t in panel:
        a = np.asarray([np.nan if x is None else x for x in rows[t].get("adj_close") or []], dtype=np.float64)
        if len(a) and np.isfinite(a).any():
            adj_missing += int((~np.isfinite(a)).sum())
    per = _rule_counts(panel)
    nt = per["no_trade_missing"]
    out: dict[str, Any] = {"rules": rules, "bars_removed": nt["bars_removed"], "tickers_affected": nt["tickers"],
                           "adj_missing_rows": adj_missing, "by_rule": {k: per[k] for k in rules}}
    if rules and panel:
        master = np.unique(np.concatenate([s.dates for s in panel.values()]))
        if len(master) > int(ev["min_train_days"]):
            first = master[int(ev["min_train_days"])]
            sub = {t: rows[t] for t in panel}
            with_rules = sum(_eligible(s, first) for s in panel.values())

            def eligible_without(off: list[str]) -> int:
                p = panel_from_rows(sub, cfg, {**est, **{k: False for k in off}})
                return sum(_eligible(p[t], first) for t in panel if t in p)

            for k in rules:
                out["by_rule"][k]["oos_ticker_days_removed"] = int(eligible_without([k]) - with_rules)
            out["oos_ticker_days_removed"] = int(eligible_without(rules) - with_rules)
    return out


# ---------------------------------------------------------------- losses

def qlike(rv: np.ndarray, f: np.ndarray) -> np.ndarray:
    x = np.asarray(rv, dtype=np.float64) / np.asarray(f, dtype=np.float64)
    return x - np.log(x) - 1.0


def vol_sq_err(rv: np.ndarray, f: np.ndarray) -> np.ndarray:
    a = np.sqrt(np.asarray(rv) * TRADING_DAYS)
    b = np.sqrt(np.asarray(f) * TRADING_DAYS)
    return (a - b) ** 2


def newey_west_t(d: np.ndarray, lag: int) -> float:
    """t statistic of mean(d) with a Newey-West (Bartlett) long-run variance."""
    d = np.asarray(d, dtype=np.float64)
    d = d[np.isfinite(d)]
    n = len(d)
    if n < 3:
        return float("nan")
    e = d - d.mean()
    lrv = float(e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        w = 1.0 - k / (lag + 1.0)
        lrv += 2.0 * w * float(e[k:] @ e[:-k]) / n
    if lrv <= 0:
        return float("nan")
    return float(d.mean() / math.sqrt(lrv / n))


# ---------------------------------------------------------------- walk-forward

def walk_forward(panel: dict[str, Series], cfg: dict = HAR, ev: dict = EVAL) -> dict[str, Any]:
    """Out-of-sample forecasts of every model. Returns {"ticker", "date", "rv", <model>: forecast} arrays
    (one entry per ticker-day with a realised target), plus the refit log."""
    hzn = int(cfg["horizon"])
    master = np.unique(np.concatenate([s.dates for s in panel.values()])) if panel else np.zeros(0, "datetime64[D]")
    start = int(ev["min_train_days"])
    step = int(ev["refit_every"])
    refits = master[start::step]
    out: dict[str, list] = {"ticker": [], "date": [], "rv": [], **{m: [] for m in MODELS}}
    log: list[dict] = []
    min_obs = int(cfg["min_obs_ticker"])
    for i, D in enumerate(refits):
        D_next = refits[i + 1] if i + 1 < len(refits) else master[-1] + np.timedelta64(1, "D")
        train_x, train_y, per_ticker = [], [], {}
        for t, s in panel.items():
            T = int(np.searchsorted(s.dates, D, side="right")) - 1     # last session index <= D
            if T < 0:
                continue
            m = har.usable_at(len(s.v), T, hzn) & np.isfinite(s.y) & np.all(np.isfinite(s.X), axis=1)
            if m.any():
                train_x.append(s.X[m])
                train_y.append(s.y[m])
                if m.sum() >= min_obs:
                    per_ticker[t] = har.fit(s.X[m], s.y[m])
        if not train_y:
            continue
        pooled = har.fit(np.vstack(train_x), np.concatenate(train_y))
        if pooled is None:
            continue
        log.append({"refit": str(D), "n": pooled.n, "coef": [round(float(c), 4) for c in pooled.coef],
                    "tickers_own_fit": len(per_ticker)})
        for t, s in panel.items():
            sel = (s.dates >= D) & (s.dates < D_next) & np.isfinite(s.rv) & np.all(np.isfinite(s.X), axis=1) \
                & np.isfinite(s.trailing) & np.isfinite(s.long_run)
            if not sel.any():
                continue
            X = s.X[sel]
            own = per_ticker.get(t) or pooled
            out["ticker"].extend([t] * int(sel.sum()))
            out["date"].extend(s.dates[sel].tolist())
            out["rv"].extend(s.rv[sel].tolist())
            out["trailing_21d"].extend(s.trailing[sel].tolist())
            out["long_run_mean"].extend(s.long_run[sel].tolist())
            out["har_pooled"].extend(pooled.predict_var(X).tolist())
            out["har_ticker"].extend(own.predict_var(X).tolist())
    res = {k: np.asarray(v) for k, v in out.items()}
    res["refits"] = log
    return res


def scorecard(res: dict[str, Any], ev: dict = EVAL) -> dict[str, Any]:
    """QLIKE and RMSE per model, each model's QLIKE against the trailing baseline (ratio, Newey-West t on the
    daily cross-sectional mean difference; negative t = the model's loss is lower) and the share of tickers
    where the model beat it."""
    n = len(res.get("rv", []))
    if not n:
        return {"n": 0, "models": {}}
    rv = res["rv"].astype(np.float64)
    dates = res["date"]
    tick = res["ticker"]
    uniq_dates, inv = np.unique(dates, return_inverse=True)
    base_q = qlike(rv, res[BASELINE].astype(np.float64))
    models: dict[str, dict] = {}
    for m in MODELS:
        f = res[m].astype(np.float64)
        q = qlike(rv, f)
        se = vol_sq_err(rv, f)
        entry = {"qlike": round(float(q.mean()), 5), "rmse_vol": round(float(math.sqrt(se.mean())), 5)}
        if m != BASELINE:
            d = q - base_q
            daily = np.bincount(inv, weights=d) / np.bincount(inv)
            entry["qlike_vs_trailing"] = round(float(q.mean() / base_q.mean()), 4)
            entry["nw_t_vs_trailing"] = round(newey_west_t(daily, int(ev["nw_lag"])), 2)
            wins, total = 0, 0
            for t in np.unique(tick):
                k = tick == t
                total += 1
                wins += int(q[k].mean() < base_q[k].mean())
            entry["ticker_win_share"] = round(wins / total, 3) if total else None
        models[m] = entry
    return {"n": int(n), "tickers": int(len(np.unique(tick))), "days": int(len(uniq_dates)),
            "oos_first": str(uniq_dates[0]), "oos_last": str(uniq_dates[-1]), "models": models}


def _trimmed_mean(x: np.ndarray, share: float) -> float:
    x = np.sort(np.asarray(x, dtype=np.float64))
    k = int(len(x) * share)
    return float(x[k:len(x) - k].mean()) if len(x) > 2 * k else float("nan")


def _tickers_for_share(per_ticker_sum: np.ndarray, share: float) -> int:
    """Fewest tickers whose summed loss reaches `share` of the total."""
    s = np.sort(np.asarray(per_ticker_sum, dtype=np.float64))[::-1]
    tot = s.sum()
    if tot <= 0:
        return 0
    return int(np.searchsorted(np.cumsum(s) / tot, share) + 1)


def breakdown(res: dict[str, Any], top: int = BREAKDOWN["top_tickers"], top_days: int = BREAKDOWN["top_days"],
              trim: float = BREAKDOWN["trim"]) -> dict[str, Any]:
    """Where the QLIKE comes from: per model, the median and trimmed mean of the per-ticker mean QLIKE and how
    many tickers hold 50% / 90% of the total loss; the worst tickers (by the baseline's loss) with every model's
    mean and share; the worst single ticker-days of the baseline and of each HAR variant, with the forecast and
    the realised target (a near-zero forecast shows here)."""
    n = len(res.get("rv", []))
    if not n:
        return {}
    rv = res["rv"].astype(np.float64)
    tick = np.asarray(res["ticker"])
    names, inv = np.unique(tick, return_inverse=True)
    cnt = np.bincount(inv)
    out: dict[str, Any] = {"models": {}}
    q_by: dict[str, np.ndarray] = {}
    for m in MODELS:
        q = qlike(rv, res[m].astype(np.float64))
        q_by[m] = q
        tsum = np.bincount(inv, weights=q)
        tmean = tsum / cnt
        out["models"][m] = {"ticker_median": round(float(np.median(tmean)), 4),
                            "ticker_trimmed_mean": round(_trimmed_mean(tmean, trim), 4),
                            "tickers_for_50pct": _tickers_for_share(tsum, 0.5),
                            "tickers_for_90pct": _tickers_for_share(tsum, 0.9)}
    base_sum = np.bincount(inv, weights=q_by[BASELINE])
    order = np.argsort(-base_sum)[:top]
    worst = []
    for i in order:
        row = {"ticker": str(names[i]), "n": int(cnt[i])}
        for m in MODELS:
            tsum_m = float(np.bincount(inv, weights=q_by[m])[i])
            row[m] = round(tsum_m / cnt[i], 4)
            row[f"{m}_share"] = round(tsum_m / float(q_by[m].sum()), 4) if q_by[m].sum() > 0 else None
        worst.append(row)
    out["worst_tickers"] = worst
    days = []
    for m in (BASELINE, "har_pooled", "har_ticker"):
        if m not in q_by:
            continue
        k = top_days if m == BASELINE else max(1, top_days // 2)
        for j in np.argsort(-q_by[m])[:k]:
            days.append({"model": m, "ticker": str(tick[j]), "date": str(res["date"][j]),
                         "forecast_var": float(f"{float(res[m][j]):.4g}"), "target_var": float(f"{rv[j]:.4g}"),
                         "forecast_vol": round(math.sqrt(float(res[m][j]) * TRADING_DAYS), 4),
                         "target_vol": round(math.sqrt(rv[j] * TRADING_DAYS), 4), "qlike": round(float(q_by[m][j]), 2)})
    out["worst_days"] = days
    return out


def verdict(card: dict[str, Any], t_crit: float = VERDICT_T) -> dict[str, Any]:
    """Three-state verdict of the best HAR variant against the trailing baseline, from a scorecard's models block:
    better_significant (lower QLIKE and Newey-West t < -t_crit), lower_not_significant (lower QLIKE, t not
    below -t_crit or unknown), not_better (QLIKE not lower). Also whether the long-run mean, a naive baseline,
    has a lower QLIKE than that HAR variant. Works on old recorded scorecards (only `models` is needed)."""
    models = card.get("models") or {}
    harm = {m: models[m]["qlike"] for m in ("har_pooled", "har_ticker") if m in models and models[m].get("qlike") is not None}
    best = card.get("best_har") if card.get("best_har") in harm else min(harm, key=harm.get, default=None)
    base = models.get(BASELINE, {}).get("qlike")
    if best is None or base is None:
        return {"best_har": best, "har_verdict": None, "har_beats_trailing": None, "har_lower_qlike": None,
                "har_t_vs_trailing": None, "long_run_beats_har": None}
    t = models[best].get("nw_t_vs_trailing")
    lower = harm[best] < base
    if not lower:
        state = "not_better"
    elif t is not None and isinstance(t, (int, float)) and math.isfinite(t) and t < -t_crit:
        state = "better_significant"
    else:
        state = "lower_not_significant"
    lr = models.get("long_run_mean", {}).get("qlike")
    return {"best_har": best, "har_verdict": state, "har_beats_trailing": state == "better_significant",
            "har_lower_qlike": bool(lower), "har_t_vs_trailing": t,
            "long_run_beats_har": (lr < harm[best]) if lr is not None else None}


VERDICT_TEXT = {
    "better_significant": "YES: lower QLIKE than trailing 21-day realised vol, beyond noise (NW t < -2)",
    "lower_not_significant": "NOT ESTABLISHED: lower QLIKE than trailing 21-day realised vol, but not "
                             "distinguishable from noise (NW t not below -2)",
    "not_better": "NO: QLIKE not lower than trailing 21-day realised vol",
}


def estimator_bias(panel: dict[str, Series], adj: dict[str, np.ndarray] | None = None) -> float | None:
    """mean(daily estimator) / mean(squared close-to-close return), pooled: 1.0 means the range-based estimator
    has the same scale as close-to-close variance."""
    num, den = 0.0, 0.0
    for t, s in panel.items():
        r = adj.get(t) if adj else None
        if r is None:
            continue
        ok = np.isfinite(s.v) & np.isfinite(r)
        num += float(s.v[ok].sum())
        den += float((r[ok] ** 2).sum())
    return round(num / den, 4) if den > 0 else None


def run(rows: dict[str, dict[str, Any]], cfg: dict = HAR, ev: dict = EVAL, est: dict = ESTIMATOR) -> dict[str, Any]:
    panel = panel_from_rows(rows, cfg, est)
    res = walk_forward(panel, cfg, ev)
    card = scorecard(res, ev)
    cc = {t: s.clean["cc"] for t, s in panel.items()}      # close-to-close returns with the same repairs
    card["estimator_to_close_close"] = estimator_bias(panel, cc)
    harm = {m: card["models"].get(m, {}).get("qlike") for m in ("har_pooled", "har_ticker")}
    card["best_har"] = min((m for m in harm if harm[m] is not None), key=lambda m: harm[m], default=None)
    card.update(verdict(card))
    card["breakdown"] = breakdown(res)
    card["cleaning"] = cleaning_report(rows, panel, cfg, est, ev)
    card["refits"] = len(res["refits"])
    first, last = res["refits"][:1], res["refits"][-1:]
    card["refit_coef_first_last"] = [r["coef"] for r in first + last]
    return card


# ---------------------------------------------------------------- I/O

def rows_from_csv(path: str) -> dict[str, dict[str, list]]:
    """CSV (ticker,date,open,high,low,close,adj_close[,volume][,source]) -> the same shape as QuantStore.load_bars."""
    out: dict[str, dict[str, list]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            d = out.setdefault(r["ticker"].upper(), {k: [] for k in ("date", "open", "high", "low", "close",
                                                                     "adj_close", "volume", "source")})
            d["date"].append(r["date"][:10])
            for k in ("open", "high", "low", "close", "adj_close"):
                val = r.get(k)
                d[k].append(float(val) if val not in (None, "", "nan") else np.nan)
            vol = r.get("volume")
            d["volume"].append(int(float(vol)) if vol not in (None, "", "nan") else None)
            d["source"].append(r.get("source") or "csv")
    for d in out.values():          # ascending by date
        order = np.argsort(np.asarray(d["date"], dtype="datetime64[D]"))
        for k in d:
            d[k] = [d[k][i] for i in order]
    return out


def format_scorecard(card: dict) -> str:
    lines = [f"Volatility forecast scorecard ({MODEL_NAME} {MODEL_VERSION}), walk-forward out of sample",
             f"  sample: {card.get('tickers')} tickers, {card.get('days')} days, {card.get('n')} ticker-days, "
             f"{card.get('oos_first')} .. {card.get('oos_last')}, {card.get('refits')} refits",
             f"  target: mean daily variance over the next {HAR['horizon']} sessions "
             f"(overnight^2 + Garman-Klass); estimator / close-to-close scale: {card.get('estimator_to_close_close')}",
             "",
             f"  {'model':<15}{'QLIKE':>9}{'RMSE vol':>10}{'QLIKE/trail':>13}{'NW t':>8}{'tickers won':>13}"]
    for m, e in card.get("models", {}).items():
        lines.append(f"  {m:<15}{e['qlike']:>9.4f}{e['rmse_vol']:>10.4f}"
                     f"{e.get('qlike_vs_trailing', 1.0):>13.3f}{(e.get('nw_t_vs_trailing') or 0.0):>8.2f}"
                     f"{(e.get('ticker_win_share') if e.get('ticker_win_share') is not None else float('nan')):>13.2f}")
    lines.append("")
    state = card.get("har_verdict")
    lines.append(f"  best HAR variant: {card.get('best_har')}; against trailing 21-day realised vol out of sample: "
                 f"{VERDICT_TEXT.get(state, 'n/a')}")
    if card.get("long_run_beats_har"):
        lines.append("  NOTE: the long-run mean (a naive baseline) has a lower QLIKE than the best HAR variant")
    lines.append("  (lower is better; NW t < -2: the model's QLIKE is lower than trailing's beyond noise)")
    cl = card.get("cleaning") or {}
    if cl:
        lines.append(f"  cleaning ({', '.join(cl.get('rules', []))}): {cl.get('oos_ticker_days_removed', 0)} "
                     f"out-of-sample ticker-days dropped for every model by all rules together; "
                     f"{cl.get('adj_missing_rows', 0)} rows without adj_close (no dividend correction on those days)")
        br = cl.get("by_rule") or {}
        oos = lambda k: br[k].get("oos_ticker_days_removed", 0)      # noqa: E731
        if "no_trade_missing" in br:
            lines.append(f"    no-trade bars: {br['no_trade_missing']['bars_removed']} missing in "
                         f"{br['no_trade_missing']['tickers']} tickers ({oos('no_trade_missing')} OOS ticker-days)")
        if "gaps" in br:
            g = br["gaps"]
            lines.append(f"    missing sessions: {g['sessions_inserted']} inserted as missing, {g['bars_removed']} bars "
                         f"after one without an overnight return, {g['tickers']} tickers ({oos('gaps')} OOS ticker-days)")
        if "splits" in br:
            sp = br["splits"]
            lines.append(f"    split defects: {sp['repaired']} repaired, {sp['adj_only']} adj-only split jumps ignored, "
                         f"{sp['ambiguous_missing']} ambiguous days missing, {sp['left']} ratio-sized news-day moves "
                         f"left, {sp['adj_confirmed']} splits already corrected by adj_close, "
                         f"{sp['special_dividends']} special dividends left to the correction; {sp['tickers']} tickers "
                         f"({oos('splits')} OOS ticker-days)")
            for t, evs in sorted(sp.get("events", {}).items()):
                lines.append(f"      {t}: " + ", ".join(f"{e['date']} {e['kind']}"
                                                       + (f" {e['ratio']:g}" if e.get("ratio") else "") for e in evs[:6])
                             + (f" (+{len(evs) - 6})" if len(evs) > 6 else ""))
        if "shell" in br:
            sh = br["shell"]
            lines.append(f"    shell history: {sh['bars_removed']} bars cut in {sh['tickers']} tickers "
                         f"({oos('shell')} OOS ticker-days)")
            for t, d in sorted(sh.get("first_usable", {}).items()):
                lines.append(f"      {t}: pinned through {d['shell_last']}, usable from {d['first_usable']}")
    bd = card.get("breakdown") or {}
    if bd:
        lines += ["", "  per-ticker QLIKE (each ticker's mean):",
                  f"  {'model':<15}{'median':>9}{'trim mean':>11}{'tickers for 50%':>17}{'for 90%':>9}"]
        for m, e in bd["models"].items():
            lines.append(f"  {m:<15}{e['ticker_median']:>9.4f}{e['ticker_trimmed_mean']:>11.4f}"
                         f"{e['tickers_for_50pct']:>17}{e['tickers_for_90pct']:>9}")
        lines += ["", f"  worst tickers by {BASELINE} loss (mean QLIKE, share of that model's total loss):",
                  f"  {'ticker':<8}{'n':>6}" + "".join(f"{m:>22}" for m in MODELS)]
        for w in bd["worst_tickers"]:
            lines.append(f"  {w['ticker']:<8}{w['n']:>6}" + "".join(
                f"{w[m]:>12.3f} ({(w[m + '_share'] or 0) * 100:>5.1f}%)" for m in MODELS))
        lines += ["", "  worst ticker-days (forecast vs realised 21-session target, annualised vol):"]
        for d in bd["worst_days"]:
            lines.append(f"  {d['model']:<13}{d['ticker']:<8}{d['date']}  forecast {d['forecast_vol']:.4f} "
                         f"(var {d['forecast_var']:.3g})  target {d['target_vol']:.4f} (var {d['target_var']:.3g})"
                         f"  QLIKE {d['qlike']:.1f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="python -m quant.evaluate", description=__doc__.splitlines()[0])
    ap.add_argument("--csv", help="read bars from this CSV instead of quant_daily_bars")
    ap.add_argument("--tickers", help="comma-separated subset")
    ap.add_argument("--no-record", action="store_true", help="do not write a quant_model_runs row")
    ap.add_argument("--json", action="store_true", help="print the scorecard as JSON")
    ap.add_argument("--source-label", default=None, help="data source recorded with the run (default: db or csv)")
    args = ap.parse_args(argv)
    want = {t.strip().upper() for t in args.tickers.split(",")} if args.tickers else None
    store = None
    if args.csv:
        rows = rows_from_csv(args.csv)
    else:
        from .store import QuantStore
        store = QuantStore()
        rows = store.load_bars(sorted(want) if want else None)
    if want:
        rows = {t: r for t, r in rows.items() if t in want}
    card = run(rows)
    card["data_source"] = args.source_label or ("csv" if args.csv else "quant_daily_bars")
    print(json.dumps(card, default=str) if args.json else format_scorecard(card), flush=True)
    if not args.no_record:
        store = store or __import__("quant.store", fromlist=["QuantStore"]).QuantStore()
        store.record_model_run(kind="evaluation", model=MODEL_NAME, version=MODEL_VERSION,
                               params={"har": HAR, "eval": EVAL, "estimator": ESTIMATOR, "models": list(MODELS)},
                               window_start=_d(card.get("oos_first")), window_end=_d(card.get("oos_last")),
                               n_obs=card.get("n"), n_tickers=card.get("tickers"), metrics=card)
    if store is not None:
        store.close()
    return 0


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


if __name__ == "__main__":
    sys.exit(main())
