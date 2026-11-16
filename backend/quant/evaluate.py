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
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from . import har
from .config import ESTIMATOR, EVAL, HAR, MODEL_NAME, MODEL_VERSION, TRADING_DAYS
from .estimators import close_to_close_returns, daily_variance

logger = logging.getLogger(__name__)

MODELS = ("trailing_21d", "long_run_mean", "har_pooled", "har_ticker")
BASELINE = "trailing_21d"


@dataclass
class Series:
    """One ticker's daily variance series, ascending, with the pieces the models need precomputed."""
    ticker: str
    dates: np.ndarray        # datetime64[D]
    v: np.ndarray            # daily variance (NaN where the bar was invalid)
    X: np.ndarray            # HAR features (log lag means)
    y: np.ndarray            # log forward-mean target (uses the future: training/evaluation only)
    rv: np.ndarray           # forward mean variance (level), the evaluation truth
    trailing: np.ndarray     # mean variance of the last 21 sessions
    long_run: np.ndarray     # expanding mean variance


def make_series(ticker: str, dates, o, h, l, c, adj_c=None, cfg: dict = HAR, est: dict = ESTIMATOR) -> Series:
    dates = np.asarray(dates, dtype="datetime64[D]")
    v = daily_variance(o, h, l, c, adj_c, floor=float(est["floor"]))
    hzn = int(cfg["horizon"])
    X = har.features(v, tuple(cfg["lags"]), float(est["floor"]))
    y = har.target(v, hzn, float(est["floor"]))
    rv = har.forward_mean(v, hzn)
    ok = np.isfinite(v)
    cs = np.cumsum(np.where(ok, v, 0.0))
    cnt = np.cumsum(ok)
    with np.errstate(invalid="ignore", divide="ignore"):
        long_run = np.where(cnt >= 21, cs / np.maximum(cnt, 1), np.nan)
    return Series(ticker, dates, v, X, y, rv, har.rolling_mean(v, 21), long_run)


def panel_from_rows(rows: dict[str, dict[str, Any]], cfg: dict = HAR) -> dict[str, Series]:
    """{ticker: {"date": [...], "open": [...], ...}} -> {ticker: Series} (tickers under 2 horizons are dropped)."""
    out = {}
    for t, r in rows.items():
        if len(r["date"]) < 2 * int(cfg["horizon"]):
            continue
        out[t] = make_series(t, r["date"], r["open"], r["high"], r["low"], r["close"], r.get("adj_close"), cfg)
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


def run(rows: dict[str, dict[str, Any]], cfg: dict = HAR, ev: dict = EVAL) -> dict[str, Any]:
    panel = panel_from_rows(rows, cfg)
    res = walk_forward(panel, cfg, ev)
    card = scorecard(res, ev)
    cc = {t: close_to_close_returns(r["close"], r.get("adj_close")) for t, r in rows.items() if t in panel}
    card["estimator_to_close_close"] = estimator_bias(panel, cc)
    harm = {m: card["models"].get(m, {}).get("qlike") for m in ("har_pooled", "har_ticker")}
    best = min((m for m in harm if harm[m] is not None), key=lambda m: harm[m], default=None)
    card["best_har"] = best
    if best and card["models"].get(BASELINE):
        card["har_beats_trailing"] = card["models"][best]["qlike"] < card["models"][BASELINE]["qlike"]
    card["refits"] = len(res["refits"])
    first, last = res["refits"][:1], res["refits"][-1:]
    card["refit_coef_first_last"] = [r["coef"] for r in first + last]
    return card


# ---------------------------------------------------------------- I/O

def rows_from_csv(path: str) -> dict[str, dict[str, list]]:
    out: dict[str, dict[str, list]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            d = out.setdefault(r["ticker"].upper(), {k: [] for k in
                                                     ("date", "open", "high", "low", "close", "adj_close")})
            d["date"].append(r["date"][:10])
            for k in ("open", "high", "low", "close", "adj_close"):
                val = r.get(k)
                d[k].append(float(val) if val not in (None, "", "nan") else np.nan)
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
    verdict = card.get("har_beats_trailing")
    lines.append(f"  best HAR variant: {card.get('best_har')}; beats trailing 21-day realised vol out of sample: "
                 f"{'YES' if verdict else 'NO' if verdict is not None else 'n/a'}")
    lines.append("  (lower is better; NW t < -2: the model's QLIKE is lower than trailing's beyond noise)")
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
