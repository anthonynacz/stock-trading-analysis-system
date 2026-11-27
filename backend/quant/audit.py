"""Read-only integrity audit of the daily bars the volatility model reads.

    python -m quant.audit                         # bars from quant_daily_bars (through quant/store.py)
    python -m quant.audit --csv bars.csv          # bars from a CSV (ticker,date,open,high,low,close,adj_close[,volume][,source])
    python -m quant.audit --json                  # the whole report as JSON (every ticker, not only the flagged)

Per ticker it counts what can make the variance estimator (quant/estimators.py) or QLIKE wrong:
- invalid OHLC (missing or non-positive), high < low, open / close outside [low, high]
- zero-range days (O = H = L = C), zero-volume days, runs of identical closes (stale prints)
- |close-to-close log return| above AUDIT.big_return, and moves that look like an unadjusted split
- jumps of the adjustment factor adj_close / close larger than a dividend: whether the split shows in the
  close and the open (consistent) or only in adj_close or only in part of the bar (the prime suspects: the
  estimator's dividend correction then adds the whole jump to that day's overnight variance)
- the largest squared overnight returns as the estimator computes them (dividend-corrected)
- calendar gaps against SPY's dates, and dates SPY does not have
- trailing 21-session vol near zero (a QLIKE denominator near zero), the ticker's estimator / close-to-close
  variance ratio, and which source wrote the bars (a ticker mixing fmp and yahoo rows is flagged)
- what the model's cleaning rules (quant/cleaning.py, config.ESTIMATOR) do to the ticker, on the same session
  calendar as the evaluation: the detected shell period and the first usable date, each split-ratio / factor
  jump with the rule's verdict (repaired, adj_only, adj_confirmed, ambiguous = day missing, left), the bars after
  a missing session, and the live `pinned_price` flag. Everything "after the rules" (trailing vol, scale) is
  what the model sees.
Nothing is written anywhere; thresholds are AUDIT in quant/config.py.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from typing import Any

import numpy as np

from . import har
from .cleaning import align_sessions, session_reference, variance_series
from .config import AUDIT, ESTIMATOR, TRADING_DAYS
from .estimators import daily_variance, no_trade_bars

logger = logging.getLogger(__name__)

# Flag -> severity weight (sum = the ticker's severity). Higher: more likely to corrupt the variance series.
WEIGHTS = {
    "ohlc_invalid": 5,          # missing / non-positive prices or high < low
    "adj_only_jump": 5,         # adj_close jumped by a split-sized factor, the prices did not
    "split_inconsistent": 5,    # a split shows in the close but not in the open / high / low of the same bar
    "unadjusted_split": 4,      # a split-ratio move in both close and adj_close that the split rule also sees as a split
    "ratio_move": 1,            # a close-to-close move near a split ratio that the split rule does not treat as one
    "split_ambiguous": 3,       # the split rule could not tell a split from a move: that day's variance is missing
    "split_repaired": 1,        # the split rule repaired an overnight return (adj_only or both series moved)
    "shell_history": 2,         # the shell rule cut a start-of-life pinned regime (blank-check trust)
    "pinned_now": 1,            # the live forecast is flagged pinned_price (reading "not comparable")
    "near_zero_trailing": 4,    # trailing 21-session vol below AUDIT.near_zero_vol on some days
    "stale_run": 3,             # AUDIT.flat_run_min+ identical closes in a row
    "zero_range": 2,            # O = H = L = C days
    "mixed_sources": 2,         # fmp and yahoo rows in one ticker
    "scale": 2,                 # estimator / close-to-close outside AUDIT.scale_band
    "gaps": 2,                  # missing sessions above AUDIT.gap_share of the span
    "extra_dates": 1,           # dates SPY does not have (holidays, foreign sessions)
    "price_outside_range": 1,   # open / close outside [low, high] by more than AUDIT.outside_gross
    "adj_missing": 1,           # rows without adj_close in a ticker that has them elsewhere
    "big_return": 1,            # |close-to-close log return| above AUDIT.big_return
    "zero_volume": 1,           # volume 0
}
# 3:2 (1.5) is left out: ln 1.5 = 0.405 sits on the big-move line and matched ordinary earnings moves on real bars.
SPLIT_RATIOS = (2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50)


def _a(x) -> np.ndarray:
    return np.asarray([np.nan if v is None else v for v in x], dtype=np.float64)


def _longest_run(c: np.ndarray) -> tuple[int, int]:
    """(longest run of identical consecutive finite closes, index where it ends)."""
    best, end, cur = 1 if len(c) else 0, 0, 1
    for i in range(1, len(c)):
        cur = cur + 1 if np.isfinite(c[i]) and c[i] == c[i - 1] else 1
        if cur > best:
            best, end = cur, i
    return best, end


def _split_like(x: float, tol: float = 0.02) -> float | None:
    """The split ratio a log move matches (2 for a 2:1 or a 1:2), else None."""
    r = math.exp(abs(x))
    for k in SPLIT_RATIOS:
        if abs(r / k - 1.0) < tol:
            return float(k)
    return None


def audit_ticker(ticker: str, r: dict[str, Any], ref_dates: np.ndarray | None = None,
                 cfg: dict = AUDIT, est: dict = ESTIMATOR, sessions: np.ndarray | None = None) -> dict[str, Any]:
    """One ticker's checks. ref_dates: SPY's dates (gaps / extra dates); sessions: the evaluation's session calendar
    (cleaning.session_reference of all tickers) the rules run on, else the ticker's own dates."""
    dates = np.asarray(r["date"], dtype="datetime64[D]")
    o, h, l, c = (_a(r[k]) for k in ("open", "high", "low", "close"))
    adj = _a(r["adj_close"]) if r.get("adj_close") is not None else np.full(len(c), np.nan)
    vol = r.get("volume")
    n = len(dates)
    rep: dict[str, Any] = {"ticker": ticker, "bars": n, "first": str(dates[0]) if n else None,
                           "last": str(dates[-1]) if n else None}
    with np.errstate(invalid="ignore", divide="ignore"):
        bad = ~(np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & (o > 0) & (h > 0) & (l > 0) & (c > 0))
        ok = ~bad
        tol = float(cfg["price_tol"])
        hl = ok & (h < l)
        lo_, hi_ = l * (1 - tol), h * (1 + tol)
        outside = ok & ~hl & ((o < lo_) | (o > hi_) | (c < lo_) | (c > hi_))
        g = float(cfg["outside_gross"])
        gross = ok & ~hl & ((o < l * (1 - g)) | (o > h * (1 + g)) | (c < l * (1 - g)) | (c > h * (1 + g)))
        zero_range = ok & (o == h) & (h == l) & (l == c)
    rep.update(invalid_ohlc=int(bad.sum()), high_lt_low=int(hl.sum()), price_outside=int(outside.sum()),
               price_outside_gross=int(gross.sum()), zero_range=int(zero_range.sum()))
    if vol is not None:
        v_arr = _a(vol)
        rep["zero_volume"] = int((v_arr == 0).sum())
        rep["volume_missing"] = int((~np.isfinite(v_arr)).sum())
    run, run_end = _longest_run(c)
    rep["longest_flat_run"] = int(run)
    rep["flat_run_ends"] = str(dates[run_end]) if run >= int(cfg["flat_run_min"]) else None

    has_adj = np.isfinite(adj) & (adj > 0)
    rep["adj_missing"] = int((~has_adj).sum()) if has_adj.any() else 0
    rep["adj_present"] = bool(has_adj.any())

    # close-to-close returns on the close and on adj_close
    with np.errstate(invalid="ignore", divide="ignore"):
        dc = np.full(n, np.nan)
        da = np.full(n, np.nan)
        don = np.full(n, np.nan)
        if n > 1:
            dc[1:] = np.log(c[1:] / c[:-1])
            da[1:] = np.log(adj[1:] / adj[:-1])
            don[1:] = np.log(o[1:] / c[:-1])
        f = np.where(has_adj & ok, adj / c, np.nan)
        df = np.full(n, np.nan)
        if n > 1:
            df[1:] = np.log(f[1:] / f[:-1])
        rng = np.log(np.maximum.reduce([h, o, c]) / np.minimum.reduce([l, o, c]))
    big = float(cfg["big_return"])
    ret = np.where(np.isfinite(da), da, dc)
    big_idx = np.where(np.abs(np.nan_to_num(ret)) > big)[0]
    rep["big_returns"] = int(len(big_idx))
    rep["big_return_days"] = [{"date": str(dates[i]), "ret": round(float(ret[i]), 4)}
                              for i in big_idx[np.argsort(-np.abs(ret[big_idx]))][:3]]
    unadj = []
    for i in big_idx:
        k = _split_like(dc[i])
        if k and np.isfinite(da[i]) and abs(da[i] - dc[i]) < float(cfg["move_match"]):
            unadj.append({"date": str(dates[i]), "ratio": k, "ret": round(float(dc[i]), 4)})
        elif k and not np.isfinite(da[i]):
            unadj.append({"date": str(dates[i]), "ratio": k, "ret": round(float(dc[i]), 4)})
    rep["unadjusted_splits"] = unadj[:5]

    # factor jumps: split-sized moves of adj_close / close
    mm = float(cfg["move_match"])
    jumps = []
    for i in np.where(np.abs(np.nan_to_num(df)) > float(cfg["factor_jump"]))[0]:
        j = {"date": str(dates[i]), "factor_log": round(float(df[i]), 4), "close_log": round(float(dc[i]), 4),
             "adj_log": round(float(da[i]), 4), "open_log": round(float(don[i]), 4)}
        if abs(dc[i] + df[i]) < mm:                       # the close carries the split, adj_close is continuous
            consistent = abs(don[i] - dc[i]) < mm and rng[i] < abs(df[i]) - mm
            j["kind"] = "in_ohlc" if consistent else "inconsistent"
        elif abs(dc[i]) < mm:                             # the close is continuous, adj_close jumped
            j["kind"] = "adj_only"
        else:
            j["kind"] = "partial"
        jumps.append(j)
    rep["factor_jumps"] = jumps[:6]
    rep["factor_jump_kinds"] = {k: sum(1 for j in jumps if j["kind"] == k) for k in
                                ("in_ohlc", "inconsistent", "adj_only", "partial") if any(j["kind"] == k for j in jumps)}

    # what the estimator sees: the cleaning rules on the evaluation's session calendar
    ra, inserted = align_sessions(r, sessions) if (sessions is not None and est.get("gaps")) else (r, 0)
    da = np.asarray(ra["date"], dtype="datetime64[D]")
    oa, ha, la, ca = (_a(ra[k]) for k in ("open", "high", "low", "close"))
    adj_in = ra.get("adj_close")
    v, ci = variance_series(oa, ha, la, ca, adj_in, ra.get("volume"), est, TRADING_DAYS)
    rep["no_trade_bars"] = int(no_trade_bars(o, h, l, c, vol).sum())
    rep["sessions_inserted"] = int(inserted)
    rep["after_missing_session"] = int(ci["post_gap"])
    rep["split_rule"] = [{"date": str(da[e["i"]]), "kind": e["kind"], "ratio": e["ratio"], "m": e["m"], "df": e["df"]}
                         for e in ci["split_events"]]
    rep["special_dividends"] = int(ci.get("special_dividends", 0))
    pin = ci["pinned"]
    pidx = np.flatnonzero(pin)
    L = int(ci["shell_end"])
    rep["shell"] = None if L < 0 else {
        "pinned_from": str(da[max(0, int(pidx[0]) - int(est["shell"]["window"]) + 1)]), "pinned_through": str(da[L]),
        "first_usable": str(da[L + 1]) if L + 1 < len(da) else None, "bars_cut": int(ci["shell_bars"])}
    rep["pinned_sessions"] = int(pin.sum())
    rep["pinned_now"] = bool(ci.get("pinned_now"))
    on = ci["on"]                            # the overnight return the estimator uses (after the split rule)
    on2 = np.where(np.isfinite(on) & np.isfinite(v), on * on, -1.0)
    top = np.argsort(-on2)[: int(cfg["top_overnight"])]
    rep["top_overnight"] = [{"date": str(da[i]), "on_log": round(float(on[i]), 4),
                             "on2_share_of_total_var": round(float(on2[i] / np.nansum(v)), 4) if np.nansum(v) > 0 else None}
                            for i in top if on2[i] >= 0]
    trailing = har.rolling_mean(v, 21)       # what the trailing_21d forecast sees (after the ESTIMATOR rules)
    tv = np.sqrt(trailing * TRADING_DAYS)
    nz = np.isfinite(tv) & (tv < float(cfg["near_zero_vol"]))
    rep["near_zero_trailing_days"] = int(nz.sum())
    rep["min_trailing_vol"] = round(float(np.nanmin(tv)), 5) if np.isfinite(tv).any() else None
    rep["min_trailing_vol_date"] = str(da[int(np.nanargmin(tv))]) if np.isfinite(tv).any() else None
    rep["floor_days"] = int((np.isfinite(v) & (v <= float(est["floor"]) * (1 + 1e-9))).sum())
    # the same bars without any cleaning rule (har-gk-1): what the rules change
    raw = daily_variance(o, h, l, c, r.get("adj_close"), floor=float(est["floor"]))
    tv_raw = np.sqrt(har.rolling_mean(raw, 21) * TRADING_DAYS)
    rep["near_zero_trailing_days_raw"] = int((np.isfinite(tv_raw) & (tv_raw < float(cfg["near_zero_vol"]))).sum())
    rr = ci["cc"]
    both = np.isfinite(v) & np.isfinite(rr)
    den = float((rr[both] ** 2).sum())
    rep["scale"] = round(float(v[both].sum()) / den, 4) if den > 0 else None

    src = r.get("source")
    if src:
        cnt: dict[str, int] = {}
        for s in src:
            cnt[str(s)] = cnt.get(str(s), 0) + 1
        rep["sources"] = cnt
        rep["source_switches"] = int(sum(1 for i in range(1, len(src)) if src[i] != src[i - 1]))

    if ref_dates is not None and n:
        span = ref_dates[(ref_dates >= dates[0]) & (ref_dates <= dates[-1])]
        mine = set(dates.tolist())
        missing = [d for d in span.tolist() if d not in mine]
        refset = set(span.tolist())
        extra = [d for d in dates.tolist() if d not in refset and ref_dates[0] <= np.datetime64(d) <= ref_dates[-1]]
        rep["missing_sessions"] = len(missing)
        rep["missing_share"] = round(len(missing) / max(len(span), 1), 4)
        rep["missing_examples"] = [str(d) for d in missing[:3]]
        rep["extra_dates"] = len(extra)
        rep["extra_examples"] = [str(d) for d in extra[:3]]

    rep["flags"] = _flags(rep, cfg)
    rep["severity"] = int(sum(WEIGHTS[k] for k in rep["flags"]))
    return rep


def _flags(rep: dict, cfg: dict) -> list[str]:
    fl = []
    kinds = rep.get("factor_jump_kinds", {})
    if rep["invalid_ohlc"] or rep["high_lt_low"]:
        fl.append("ohlc_invalid")
    if kinds.get("adj_only"):
        fl.append("adj_only_jump")
    if kinds.get("inconsistent") or kinds.get("partial"):
        fl.append("split_inconsistent")
    rule_days = {e["date"] for e in rep.get("split_rule", []) if e["kind"] in ("repaired", "ambiguous")}
    if any(u["date"] in rule_days for u in rep["unadjusted_splits"]):
        fl.append("unadjusted_split")
    elif rep["unadjusted_splits"]:
        fl.append("ratio_move")
    kinds_rule = {e["kind"] for e in rep.get("split_rule", [])}
    if "ambiguous" in kinds_rule:
        fl.append("split_ambiguous")
    if kinds_rule & {"repaired", "adj_only"}:
        fl.append("split_repaired")
    if rep.get("shell"):
        fl.append("shell_history")
    if rep.get("pinned_now"):
        fl.append("pinned_now")
    if rep["near_zero_trailing_days"]:
        fl.append("near_zero_trailing")
    if rep["longest_flat_run"] >= int(cfg["flat_run_min"]):
        fl.append("stale_run")
    if rep["zero_range"]:
        fl.append("zero_range")
    if len(rep.get("sources") or {}) > 1:
        fl.append("mixed_sources")
    lo, hi = cfg["scale_band"]
    if rep["scale"] is not None and not (lo <= rep["scale"] <= hi):
        fl.append("scale")
    if rep.get("missing_share", 0) > float(cfg["gap_share"]):
        fl.append("gaps")
    if rep.get("extra_dates"):
        fl.append("extra_dates")
    if rep["price_outside_gross"]:
        fl.append("price_outside_range")
    if rep["adj_missing"]:
        fl.append("adj_missing")
    if rep["big_returns"]:
        fl.append("big_return")
    if rep.get("zero_volume"):
        fl.append("zero_volume")
    return fl


def audit(rows: dict[str, dict[str, Any]], cfg: dict = AUDIT) -> dict[str, Any]:
    ref = np.unique(np.asarray(rows["SPY"]["date"], dtype="datetime64[D]")) if "SPY" in rows else None
    gaps = ESTIMATOR.get("gaps")
    sessions = session_reference(rows, float(gaps.get("ref_share", 0.5))) if gaps else None
    reps = [audit_ticker(t, r, ref, cfg, ESTIMATOR, sessions) for t, r in sorted(rows.items()) if len(r["date"])]
    reps.sort(key=lambda x: (-x["severity"], -x["near_zero_trailing_days"], x["ticker"]))
    flag_counts = {k: sum(1 for x in reps if k in x["flags"]) for k in WEIGHTS}
    src: dict[str, int] = {}
    for x in reps:
        for s, k in (x.get("sources") or {}).items():
            src[s] = src.get(s, 0) + k
    scales = [x["scale"] for x in reps if x["scale"] is not None]
    return {"tickers": len(reps), "bars": int(sum(x["bars"] for x in reps)),
            "first": min((x["first"] for x in reps), default=None), "last": max((x["last"] for x in reps), default=None),
            "calendar_reference": "SPY" if ref is not None else None, "rows_by_source": src,
            "flag_counts": {k: v for k, v in flag_counts.items() if v},
            "flagged": sum(1 for x in reps if x["flags"]),
            "scale_median": round(float(np.median(scales)), 4) if scales else None,
            "near_zero_trailing_ticker_days": int(sum(x["near_zero_trailing_days"] for x in reps)),
            "zero_range_days": int(sum(x["zero_range"] for x in reps)),
            "no_trade_bars": int(sum(x["no_trade_bars"] for x in reps)),
            "near_zero_trailing_ticker_days_raw": int(sum(x["near_zero_trailing_days_raw"] for x in reps)),
            "shells": {x["ticker"]: x["shell"] for x in reps if x.get("shell")},
            "split_rule": {x["ticker"]: x["split_rule"] for x in reps if x.get("split_rule")},
            "pinned_now": [x["ticker"] for x in reps if x.get("pinned_now")],
            "sessions_inserted": int(sum(x.get("sessions_inserted", 0) for x in reps)),
            "tickers_detail": reps}


def _ev(items: list[dict], keys: tuple[str, ...]) -> str:
    return ", ".join(" ".join(str(it[k]) for k in keys if k in it) for it in items)


def format_audit(rep: dict, cfg: dict = AUDIT) -> str:
    L = [f"Daily bars audit: {rep['tickers']} tickers, {rep['bars']} bars, {rep['first']} .. {rep['last']}; "
         f"rows by source: {rep['rows_by_source'] or 'not recorded'}; calendar reference: {rep['calendar_reference']}",
         f"  flagged tickers: {rep['flagged']}; median estimator / close-to-close scale {rep['scale_median']}; "
         f"zero-range days {rep['zero_range_days']} (no-trade {rep['no_trade_bars']}); ticker-days with trailing 21d vol < "
         f"{cfg['near_zero_vol']:.0%}: {rep['near_zero_trailing_ticker_days']} "
         f"({rep['near_zero_trailing_ticker_days_raw']} without the cleaning rules)",
         f"  cleaning rules: shell history cut in {len(rep.get('shells', {}))} tickers; split rule verdicts in "
         f"{len(rep.get('split_rule', {}))} tickers; {rep.get('sessions_inserted', 0)} missing sessions; live "
         f"pinned_price: {', '.join(rep.get('pinned_now', [])[:20]) or 'none'}",
         "  flag counts (weight): " + ", ".join(f"{k}={v} ({WEIGHTS[k]})" for k, v in rep["flag_counts"].items()),
         "",
         f"  {'ticker':<8}{'sev':>4}{'bars':>6}{'scale':>7}{'minTV':>7}{'nzTV':>6}{'0rng':>5}{'flat':>5}"
         f"{'big':>4}{'fjmp':>5}{'miss':>6}{'src':>12}  flags"]
    flagged = [x for x in rep["tickers_detail"] if x["flags"]]
    for x in flagged[: int(cfg["max_listed"])]:
        srcs = "/".join(f"{k}:{v}" for k, v in (x.get("sources") or {}).items()) or "-"
        mtv = "-" if x["min_trailing_vol"] is None else f"{x['min_trailing_vol']:.3f}"
        L.append(f"  {x['ticker']:<8}{x['severity']:>4}{x['bars']:>6}{(x['scale'] or float('nan')):>7.2f}{mtv:>7}"
                 f"{x['near_zero_trailing_days']:>6}{x['zero_range']:>5}{x['longest_flat_run']:>5}{x['big_returns']:>4}"
                 f"{len(x['factor_jumps']):>5}{x.get('missing_sessions', 0):>6}{srcs[:12]:>12}  {','.join(x['flags'])}")
        det = []
        if x["factor_jumps"]:
            det.append("factor jumps: " + _ev(x["factor_jumps"], ("date", "kind", "factor_log")))
        if x["unadjusted_splits"]:
            det.append("split-ratio moves: " + _ev(x["unadjusted_splits"], ("date", "ratio")))
        if x.get("split_rule"):
            det.append("split rule: " + _ev(x["split_rule"], ("date", "kind", "ratio")))
        if x.get("shell"):
            sh = x["shell"]
            det.append(f"shell {sh['pinned_from']} .. {sh['pinned_through']} cut ({sh['bars_cut']} bars), usable from "
                       f"{sh['first_usable']}")
        if x["near_zero_trailing_days"]:
            det.append(f"min trailing vol {x['min_trailing_vol']} on {x['min_trailing_vol_date']}")
        if x["flat_run_ends"]:
            det.append(f"flat run of {x['longest_flat_run']} ending {x['flat_run_ends']}")
        if x["big_return_days"] and not x["unadjusted_splits"]:
            det.append("big moves: " + _ev(x["big_return_days"], ("date", "ret")))
        if x["top_overnight"] and ({"adj_only_jump", "split_inconsistent", "scale"} & set(x["flags"])):
            det.append("top overnight: " + _ev(x["top_overnight"], ("date", "on_log")))
        if x.get("extra_dates"):
            det.append(f"{x['extra_dates']} dates not in SPY (e.g. {', '.join(x['extra_examples'])})")
        if x.get("missing_sessions") and "gaps" in x["flags"]:
            det.append(f"{x['missing_sessions']} sessions missing (e.g. {', '.join(x['missing_examples'])})")
        if det:
            L.append("      " + "; ".join(det)[:300])
    if len(flagged) > int(cfg["max_listed"]):
        L.append(f"  ... {len(flagged) - int(cfg['max_listed'])} more flagged tickers (see --json)")
    L += ["", "  columns: sev = severity (sum of flag weights), scale = estimator / close-to-close variance, minTV = "
              "lowest trailing 21d vol (annualised), nzTV = days it was below the near-zero line, 0rng = O=H=L=C days, "
              "flat = longest run of identical closes, big = big close-to-close moves, fjmp = split-sized "
              "adj_close/close jumps, miss = sessions missing vs SPY"]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="python -m quant.audit", description=__doc__.splitlines()[0])
    ap.add_argument("--csv", help="read bars from this CSV instead of quant_daily_bars")
    ap.add_argument("--tickers", help="comma-separated subset (SPY is kept as the calendar reference)")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)
    want = {t.strip().upper() for t in args.tickers.split(",")} if args.tickers else None
    if args.csv:
        from .evaluate import rows_from_csv
        rows = rows_from_csv(args.csv)
    else:
        from .store import QuantStore
        store = QuantStore()
        try:
            rows = store.load_bars(sorted(want | {"SPY"}) if want else None)
        finally:
            store.close()
    if want:
        ref = rows.get("SPY")
        rows = {t: r for t, r in rows.items() if t in want}
        rep = audit({**rows, **({"SPY": ref} if ref is not None else {})})
        if "SPY" not in want:
            rep["tickers_detail"] = [x for x in rep["tickers_detail"] if x["ticker"] != "SPY"]
    else:
        rep = audit(rows)
    print(json.dumps(rep, default=str) if args.json else format_audit(rep), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
