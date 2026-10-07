"""Option-chain and liquidity metrics for the names on the radar (display and filtering only).

    python -m radar.options [--tickers AAA,BBB]

Run by the worker as a subprocess after each successful scan (and after a "Scan now"), for the current
members and warming-up names (or `--tickers`). For each ticker it reads Yahoo's v7 options endpoint (the
crumb-guarded transport of radar.fetch): the expiry list, the stock quote (price, next earnings date) and the
chain of the first expiry at least OPTIONS.min_dte days out. It stores one row per ticker in
radar_option_metrics; a ticker whose fetch failed keeps its previous row. The last stdout line is a JSON
result, like radar.tick.

The engine never reads these metrics: they feed the radar page's call-option filters and the option chip on
each member. Nothing here is a recommendation.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any, Callable

from . import calendar_nyse as cal
from .config import OPTIONS

logger = logging.getLogger(__name__)

YAHOO_OPTIONS = "https://query2.finance.yahoo.com/v7/finance/options/{}"
RETRY_STATUSES = (429, 500, 502, 503, 504)

Fetch = Callable[[str, dict, float], tuple[int, Any]]


# ---------------------------------------------------------------- pure helpers

def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _int(v: Any) -> int:
    f = _num(v)
    return int(f) if f is not None and f > 0 else 0


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(v, nd)


def et_date(epoch: float) -> date:
    return datetime.fromtimestamp(epoch, cal.UTC).astimezone(cal.ET).date()


def exp_date(epoch: int) -> date:
    """Yahoo stamps an expiry at 00:00 UTC of its date."""
    return datetime.fromtimestamp(int(epoch), timezone.utc).date()


def pick_expiry(expirations: list[int], today: date, min_dte: int) -> int | None:
    """The first expiry at least min_dte calendar days out; the farthest one when all are nearer."""
    exps = sorted(int(e) for e in expirations or [])
    if not exps:
        return None
    for e in exps:
        if (exp_date(e) - today).days >= min_dte:
            return e
    return exps[-1]


def next_earnings(quote: dict, now: float) -> tuple[date | None, bool]:
    """(next earnings date on or after today ET, whether Yahoo marks it as an estimate)."""
    today = et_date(now)
    days = []
    for key in ("earningsTimestampStart", "earningsTimestamp", "earningsTimestampEnd"):
        t = _num(quote.get(key))
        if t and t > 0 and et_date(t) >= today:
            days.append(et_date(t))
    return (min(days) if days else None), bool(quote.get("isEarningsDateEstimate"))


def spread_pct(bid: float | None, ask: float | None) -> float | None:
    if not bid or not ask or bid <= 0 or ask < bid:
        return None
    return (ask - bid) / ((ask + bid) / 2.0) * 100.0


def grade(oi: int, spread: float | None, ntm_volume: int, limits: dict) -> str:
    """good / fair / thin (OPTIONS.grade): `oi` and `ntm_volume` are near-the-money call totals, `spread` the
    at-the-money call's bid-ask spread in % of mid."""
    for name in ("good", "fair"):
        g = limits[name]
        if oi >= g["oi"] and spread is not None and spread <= g["spread_pct"] and ntm_volume >= g["ntm_volume"]:
            return name
    return "thin"


def chain_metrics(result: dict, chain: dict | None, now: float, *, baseline: dict | None = None,
                  params: dict = OPTIONS) -> dict:
    """The metrics document of one ticker from Yahoo's optionChain.result[0] (`result`, for the expiry list
    and the quote) and the chain of the chosen expiry (`chain`, one entry of result["options"])."""
    quote = result.get("quote") or {}
    spot = _num(quote.get("regularMarketPrice"))
    today = et_date(now)
    exps = sorted(int(e) for e in result.get("expirationDates") or [])
    earn, earn_est = next_earnings(quote, now)
    sigma_d = _num((baseline or {}).get("sigma_d"))
    hv = sigma_d * math.sqrt(252) * 100.0 if sigma_d and sigma_d > 0 else None
    adv = _num((baseline or {}).get("adv20_usd"))
    if adv is None and spot and _num(quote.get("averageDailyVolume3Month")):
        adv = spot * float(quote["averageDailyVolume3Month"])
    doc: dict = {
        "as_of": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "price": _r(spot, 4), "adv_usd": _r(adv, 0), "hv_pct": _r(hv, 1),
        "has_options": bool(exps),
        "weeklies": sum(1 for e in exps if 0 <= (exp_date(e) - today).days <= params["weekly_window_days"]) >= 4,
        "earnings_date": earn.isoformat() if earn else None, "earnings_estimate": earn_est if earn else None,
        "days_to_earnings": (earn - today).days if earn else None,
        "expiry": None, "dte": None, "earnings_before_expiry": None,
        "atm": None, "ntm_call_oi": None, "ntm_call_volume": None, "call_volume": None, "put_volume": None,
        "put_call_volume": None, "iv_pct": None, "iv_hv": None, "expected_move_pct": None,
        "liquidity": "none",
    }
    if not exps or not chain or not spot:
        return doc
    exp = exp_date(int(chain.get("expirationDate") or 0))
    dte = (exp - today).days
    calls = [c for c in chain.get("calls") or [] if _num(c.get("strike")) is not None]
    puts = chain.get("puts") or []
    doc.update(expiry=exp.isoformat(), dte=dte, earnings_before_expiry=bool(earn and earn <= exp))
    if not calls:
        return doc
    band = params["ntm_band"]
    ntm = [c for c in calls if abs(float(c["strike"]) / spot - 1.0) <= band]
    atm = min(calls, key=lambda c: (abs(float(c["strike"]) - spot), -float(c["strike"])))
    strike, bid, ask = float(atm["strike"]), _num(atm.get("bid")), _num(atm.get("ask"))
    iv = _num(atm.get("impliedVolatility"))
    iv = iv * 100.0 if iv is not None and iv >= 0.01 else None        # Yahoo shows ~0 for unquoted contracts
    sp = spread_pct(bid, ask)
    call_vol = sum(_int(c.get("volume")) for c in calls)
    put_vol = sum(_int(p.get("volume")) for p in puts)
    ntm_vol = sum(_int(c.get("volume")) for c in ntm)
    ntm_oi = sum(_int(c.get("openInterest")) for c in ntm)
    mid = (bid + ask) / 2.0 if bid and ask and bid > 0 and ask >= bid else None
    doc.update(
        atm={"strike": strike, "bid": _r(bid), "ask": _r(ask), "mid": _r(mid), "spread_pct": _r(sp, 1),
             "volume": _int(atm.get("volume")), "oi": _int(atm.get("openInterest")), "iv_pct": _r(iv, 1),
             "breakeven_pct": _r(((strike + ask) / spot - 1.0) * 100.0, 1) if ask and ask > 0 else None,
             "contract": atm.get("contractSymbol")},
        ntm_call_oi=ntm_oi, ntm_call_volume=ntm_vol,
        call_volume=call_vol, put_volume=put_vol,
        put_call_volume=_r(put_vol / call_vol, 2) if call_vol else None,
        iv_pct=_r(iv, 1), iv_hv=_r(iv / hv, 2) if iv and hv else None,
        expected_move_pct=_r(iv * math.sqrt(max(dte, 1) / 365.0), 1) if iv else None,
        liquidity=grade(ntm_oi, sp, ntm_vol, params["grade"]),
    )
    return doc


# ---------------------------------------------------------------- fetching

def _get(fetch: Fetch, symbol: str, params: dict, timeout: float) -> dict | None:
    """optionChain.result[0], with one retry on a rate limit, server error or network error."""
    for attempt in (0, 1):
        try:
            status, body = fetch(YAHOO_OPTIONS.format(symbol), params, timeout)
        except Exception as e:  # noqa: BLE001 - network errors are retried once, then the ticker is skipped
            logger.info("options %s: %s: %s", symbol, type(e).__name__, e)
            status, body = 0, None
        if status == 200:
            result = ((body or {}).get("optionChain") or {}).get("result") or []
            return result[0] if result else {}
        if attempt == 0 and (status in RETRY_STATUSES or status == 0):
            time.sleep(1.0)
            continue
        return None
    return None


def ticker_metrics(fetch: Fetch, symbol: str, now: float, baseline: dict | None,
                   params: dict = OPTIONS) -> dict | None:
    """The metrics document of `symbol`, or None when Yahoo could not be read."""
    timeout = float(params["fetch_timeout_s"])
    result = _get(fetch, symbol, {}, timeout)
    if result is None:
        return None
    exps = result.get("expirationDates") or []
    chosen = pick_expiry(exps, et_date(now), int(params["min_dte"]))
    chains = result.get("options") or []
    chain = chains[0] if chains else None
    if chosen is not None and (chain is None or int(chain.get("expirationDate") or 0) != chosen):
        second = _get(fetch, symbol, {"date": chosen}, timeout)
        if second is None:
            return None
        chains = second.get("options") or []
        chain = chains[0] if chains else None
        result = {**result, "quote": second.get("quote") or result.get("quote")}
    return chain_metrics(result, chain, now, baseline=baseline, params=params)


def _yahoo_fetch() -> Fetch:
    from .fetch import HttpTransport
    return HttpTransport._crumbed


# ---------------------------------------------------------------- run

def _unpack(blob: bytes | None) -> dict:
    if not blob:
        return {}
    try:
        doc = json.loads(gzip.decompress(blob))
    except (OSError, EOFError, ValueError):
        return {}
    return doc.get("symbols") or {} if isinstance(doc, dict) else {}


def baselines_for(store: Any, session_date: str | None) -> dict[str, dict]:
    """{symbol: baseline json} of the session's main and extra packs (sigma_d, adv20_usd)."""
    if not session_date:
        return {}
    out: dict[str, dict] = {}
    for kind in ("extra", "main"):            # main wins for a symbol in both
        try:
            _, blob = store.load_pack(session_date, kind)
        except Exception as e:  # noqa: BLE001 - metrics without the baseline still help
            logger.info("options: pack %s/%s unreadable: %s", session_date, kind, e)
            continue
        out.update(_unpack(blob))
    return out


def radar_tickers(state: dict, limit: int) -> list[str]:
    """The members (highest intensity first), then the warming-up names."""
    members = sorted(state.get("members") or [], key=lambda m: -(m.get("intensity") or 0))
    names = [m.get("ticker") for m in members] + [h.get("ticker") for h in state.get("heating") or []]
    return list(dict.fromkeys(t for t in names if isinstance(t, str) and t))[:limit]


def refresh(store: Any, tickers: list[str] | None = None, *, fetch: Fetch | None = None,
            clock: Callable[[], float] = time.time, params: dict = OPTIONS) -> dict:
    t0 = time.monotonic()
    state = store.load_state() or {}
    syms = tickers if tickers is not None else radar_tickers(state, int(params["max_tickers"]))
    if not syms:
        return {"status": "ok", "tickers": 0, "ok": 0, "failed": [], "duration_ms": 0}
    session_date = ((state.get("session") or {}).get("date"))
    base = baselines_for(store, session_date)
    fetch = fetch or _yahoo_fetch()
    now = clock()
    with ThreadPoolExecutor(max_workers=int(params["workers"])) as pool:
        docs = list(pool.map(lambda s: ticker_metrics(fetch, s, now, base.get(s), params), syms))
    got = {s: d for s, d in zip(syms, docs) if d is not None}
    failed = [s for s, d in zip(syms, docs) if d is None]
    if got:
        store.put_option_metrics(got, datetime.fromtimestamp(now, timezone.utc), int(params["keep_days"]))
    status = "ok" if not failed else "degraded" if got else "error"
    return {"status": status, "tickers": len(syms), "ok": len(got), "failed": failed,
            "duration_ms": int((time.monotonic() - t0) * 1000)}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="python -m radar.options", description=__doc__.splitlines()[0])
    ap.add_argument("--tickers", help="comma-separated tickers; default: the radar's members and warming-up names")
    args = ap.parse_args(argv)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    from .store import RadarStore
    store = RadarStore()
    try:
        res = refresh(store, tickers)
    except Exception as e:  # noqa: BLE001 - reported on stdout for the worker
        logger.exception("option metrics refresh failed")
        res = {"status": "error", "message": f"{type(e).__name__}: {e}"}
    finally:
        store.close()
    print(json.dumps(res), flush=True)
    return 0 if res.get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
