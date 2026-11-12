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

from utils.greeks import RISK_FREE_RATE

from . import calendar_nyse as cal
from .config import MOVERS, OPTIONS

logger = logging.getLogger(__name__)

YAHOO_OPTIONS = "https://query2.finance.yahoo.com/v7/finance/options/{}"
RETRY_STATUSES = (429, 500, 502, 503, 504)

Fetch = Callable[[str, dict, float], tuple[int, Any]]
# Yahoo reports near-zero IVs (0.00001, 0.016 ...) for unquoted contracts: below 5% an equity option IV is noise.
MIN_IV = 0.05

# IV visibility (what hit EBAY on 2026-10-15: a takeover rumour lifted the front-week IV to 1.7x the stock's
# normal volatility, then it bled from 44% to 36% while the stock drifted): the ~30-day IV for the term
# structure, the change since the day's first reading, the dollar cost per IV point and a crush-risk label.
IV = {
    "term_dte": 30,              # the "30-day" expiry: first one at least this many days out
    "rich_ratio": 1.5,           # IV / realised volatility above this: an event or rumour premium
    "term_inverted": 1.2,        # front IV / 30-day IV above this: the front week prices an event
    "crush_drop": 0.10,          # IV down this share since the day's first reading: crushing
    "crush_min_minutes": 30,     # ... measured over at least this long
}


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


def pick_swing_expiry(expirations: list[int], today: date, lo: int, hi: int) -> int | None:
    """The first expiry `lo`..`hi` calendar days out (2 weeks to a month): the "swing" premium of the Movers
    view. Without one in the window, the first expiry beyond it (monthlies only); None when all are nearer."""
    exps = sorted(int(e) for e in expirations or [])
    later = [e for e in exps if (exp_date(e) - today).days >= lo]
    return later[0] if later else None


def _time_value(kind: str, strike: float, spot: float, px: float) -> float:
    """The part of an option's price that is not intrinsic value, per share."""
    return px - (max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0))


def implausible_swing_sides(sides: dict[str, tuple[float, float] | None], spot: float, dte: int,
                            params: dict = OPTIONS) -> set[str]:
    """The sides ({"call" | "put": (strike, price per share)}) whose quote cannot be right, typically a
    stale quote on a thin contract left over from before a big move (Yahoo, 2026-11-11: a 50-strike put
    quoted at $28.50 on a $49 stock, 37 days out).

    1. Time value above what `swing_max_iv` (annualised volatility) would give an at-the-money option
       (0.4 x iv x sqrt(dte / 365) x price), or a price well below intrinsic value.
    2. Both sides at the same strike breaking put-call parity (call - put = price - strike) by more than
       `swing_parity_tol` of the stock price: the side with the larger time value is the stale one."""
    bad: set[str] = set()
    cap = 0.4 * float(params.get("swing_max_iv", 3.0)) * math.sqrt(max(dte, 1) / 365.0) * spot
    tol = float(params.get("swing_parity_tol", 0.08)) * spot
    tv = {k: _time_value(k, v[0], spot, v[1]) for k, v in sides.items() if v}
    bad.update(k for k, x in tv.items() if x > cap or x < -tol)
    c, p = sides.get("call"), sides.get("put")
    if c and p and not bad and c[0] == p[0] and abs((c[1] - p[1]) - (spot - c[0])) > tol:
        bad.add(max(tv, key=tv.get))
    return bad


def swing_quote(chain: dict | None, spot: float | None, expiry: int, today: date,
                params: dict = OPTIONS) -> dict | None:
    """What one at-the-money contract of `chain` costs: {"expiry", "dte", "call": {...}, "put": {...}} with
    strike, bid, ask, mid and cost_usd (mid x 100, the ask when there is no bid) per side; a side without a
    quote, or with a quote that cannot be right (implausible_swing_sides), is None. None when neither side
    is left."""
    if not chain or not spot:
        return None
    out: dict = {"expiry": exp_date(expiry).isoformat(), "dte": (exp_date(expiry) - today).days}
    priced: dict[str, tuple[float, float] | None] = {}
    for kind in ("call", "put"):
        rows = [c for c in chain.get(f"{kind}s") or [] if _num(c.get("strike")) is not None]
        out[kind] = None
        if not rows:
            continue
        atm = min(rows, key=lambda c: (abs(float(c["strike"]) - spot), -float(c["strike"]) if kind == "call" else float(c["strike"])))
        bid, ask = _num(atm.get("bid")), _num(atm.get("ask"))
        mid = (bid + ask) / 2.0 if bid and ask and bid > 0 and ask >= bid else None
        px = mid if mid else ask if ask and ask > 0 else None
        if px is None:
            continue
        priced[kind] = (float(atm["strike"]), px)
        out[kind] = {"strike": float(atm["strike"]), "bid": _r(bid), "ask": _r(ask), "mid": _r(mid),
                     "cost_usd": _r(px * 100.0, 0)}
    for kind in implausible_swing_sides(priced, spot, out["dte"], params):
        out[kind] = None
    return out if out["call"] or out["put"] else None


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


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def vega_per_point(spot: float | None, strike: float | None, iv_pct: float | None, dte: int | None,
                   rate: float = RISK_FREE_RATE) -> float | None:
    """Dollar change of ONE contract (100 shares) for a 1-point IV move: S * phi(d1) * sqrt(T)
    (Black-Scholes vega per 1.00 of volatility, x100 shares / 100 points)."""
    if not spot or not strike or not iv_pct or iv_pct <= 0 or dte is None:
        return None
    t = max(dte, 0.5) / 365.0
    sig = iv_pct / 100.0
    d1 = (math.log(spot / strike) + (rate + sig * sig / 2) * t) / (sig * math.sqrt(t))
    return spot * _npdf(d1) * math.sqrt(t)


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def greeks(spot: float | None, strike: float | None, iv_pct: float | None, dte: int | None, kind: str,
           rate: float = RISK_FREE_RATE) -> dict:
    """Black-Scholes delta and time decay of ONE contract (100 shares): theta in dollars per calendar day."""
    if not spot or not strike or not iv_pct or iv_pct <= 0 or dte is None:
        return {"delta": None, "theta_day_usd": None}
    t = max(dte, 0.5) / 365.0
    sig = iv_pct / 100.0
    d1 = (math.log(spot / strike) + (rate + sig * sig / 2) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    decay = -spot * _npdf(d1) * sig / (2 * math.sqrt(t))
    if kind == "call":
        delta, theta = _ncdf(d1), decay - rate * strike * math.exp(-rate * t) * _ncdf(d2)
    else:
        delta, theta = _ncdf(d1) - 1.0, decay + rate * strike * math.exp(-rate * t) * _ncdf(-d2)
    return {"delta": round(delta, 3), "theta_day_usd": round(theta / 365.0 * 100.0, 2)}


def side_metrics(contracts: list[dict], spot: float, dte: int, kind: str, params: dict = OPTIONS) -> dict | None:
    """The at-the-money contract and near-the-money liquidity of one side (calls or puts) of an expiry.

    Beyond the quote: delta, time decay per day and per cent of the price, and `spread_move_pct`, the stock
    move needed just to pay the bid-ask spread (spread / |delta| / price): with ~40-minute holds the spread,
    not the direction call, decided whether a trade made money in the backtest (2026-10-16)."""
    rows = [c for c in contracts or [] if _num(c.get("strike")) is not None]
    if not rows or not spot:
        return None
    band = params["ntm_band"]
    ntm = [c for c in rows if abs(float(c["strike"]) / spot - 1.0) <= band]
    # Calls: the higher strike on a tie (slightly out of the money); puts: the lower one.
    atm = min(rows, key=lambda c: (abs(float(c["strike"]) - spot), -float(c["strike"]) if kind == "call" else float(c["strike"])))
    strike, bid, ask = float(atm["strike"]), _num(atm.get("bid")), _num(atm.get("ask"))
    iv = _num(atm.get("impliedVolatility"))
    iv = iv * 100.0 if iv is not None and iv >= MIN_IV else None
    sp = spread_pct(bid, ask)
    mid = (bid + ask) / 2.0 if bid and ask and bid > 0 and ask >= bid else None
    g = greeks(spot, strike, iv, dte, kind)
    ntm_oi = sum(_int(c.get("openInterest")) for c in ntm)
    ntm_vol = sum(_int(c.get("volume")) for c in ntm)
    spread_move = ((ask - bid) / (abs(g["delta"]) * spot) * 100.0
                   if mid and g["delta"] and abs(g["delta"]) > 0.05 else None)
    if ask and ask > 0:
        be = ((strike + ask) / spot - 1.0) * 100.0 if kind == "call" else ((strike - ask) / spot - 1.0) * 100.0
    else:
        be = None
    return {
        "atm": {"strike": strike, "bid": _r(bid), "ask": _r(ask), "mid": _r(mid), "spread_pct": _r(sp, 1),
                "volume": _int(atm.get("volume")), "oi": _int(atm.get("openInterest")), "iv_pct": _r(iv, 1),
                "breakeven_pct": _r(be, 1), "contract": atm.get("contractSymbol"), "delta": g["delta"],
                "theta_day_usd": g["theta_day_usd"],
                "theta_pct": _r(abs(g["theta_day_usd"]) / (mid * 100.0) * 100.0, 1) if mid and g["theta_day_usd"] else None,
                "spread_move_pct": _r(spread_move, 2)},
        "ntm_oi": ntm_oi, "ntm_volume": ntm_vol,
        "liquidity": grade(ntm_oi, sp, ntm_vol, params["grade"]),
    }


def atm_iv(chain: dict | None, spot: float | None) -> float | None:
    """ATM call IV (%) of one expiry's chain, None when unquoted."""
    calls = [c for c in (chain or {}).get("calls") or [] if _num(c.get("strike")) is not None]
    if not calls or not spot:
        return None
    atm = min(calls, key=lambda c: (abs(float(c["strike"]) - spot), -float(c["strike"])))
    iv = _num(atm.get("impliedVolatility"))
    return iv * 100.0 if iv is not None and iv >= MIN_IV else None


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
        "liquidity": "none", "sides": {"call": None, "put": None},
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
    iv = iv * 100.0 if iv is not None and iv >= MIN_IV else None        # Yahoo shows ~0 for unquoted contracts
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
        # Both sides, the same shape: up moves are traded with calls, down moves with puts.
        sides={"call": side_metrics(calls, spot, dte, "call", params), "put": side_metrics(puts, spot, dte, "put", params)},
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
    doc = chain_metrics(result, chain, now, baseline=baseline, params=params)
    by_expiry = {chosen: chain}          # chains already read, for the swing premium below
    # The ~30-day expiry for the term structure (one more request; skipped when it is the same expiry).
    term = pick_expiry(exps, et_date(now), int(IV["term_dte"]))
    if term is not None and term != chosen and doc.get("price"):
        third = _get(fetch, symbol, {"date": term}, timeout)
        chains3 = (third or {}).get("options") or []
        by_expiry[term] = chains3[0] if chains3 else None
        doc["iv_30d_pct"] = _r(atm_iv(chains3[0] if chains3 else None, doc["price"]), 1)
        doc["iv_30d_expiry"] = exp_date(term).isoformat()
    elif term is not None and term == chosen:
        doc["iv_30d_pct"], doc["iv_30d_expiry"] = doc.get("iv_pct"), doc.get("expiry")
    # The swing premium: one at-the-money contract 2 weeks to a month out (a request more when that expiry is
    # neither of the two above, which is the usual case for names with weekly options).
    swing = pick_swing_expiry(exps, et_date(now), *params.get("swing_dte", (14, 31)))
    if swing is not None and doc.get("price"):
        if swing not in by_expiry:
            fourth = _get(fetch, symbol, {"date": swing}, timeout)
            chains4 = (fourth or {}).get("options") or []
            by_expiry[swing] = chains4[0] if chains4 else None
        doc["swing"] = swing_quote(by_expiry[swing], doc["price"], swing, et_date(now), params)
    atm = doc.get("atm") or {}
    doc["vega_per_pt_usd"] = _r(vega_per_point(doc.get("price"), atm.get("strike"), doc.get("iv_pct"),
                                               doc.get("dte")), 2)
    front, back = doc.get("iv_pct"), doc.get("iv_30d_pct")
    doc["term_ratio"] = _r(front / back, 2) if front and back else None
    return doc


def empty_quotes(doc: dict) -> bool:
    """A chain read with no live market: listed options but the ATM call has no bid and no ask and nothing
    near the money has open interest (Yahoo outside market hours). Storing it would turn every name "thin"
    with no IV; the previous row is kept instead."""
    if not doc.get("has_options"):
        return False
    atm = doc.get("atm") or {}
    return not atm.get("bid") and not atm.get("ask") and not doc.get("ntm_call_oi")


def carry_iv_first(doc: dict, prev: dict | None, now: float) -> dict:
    """Keep the day's first IV reading (same ET day and same front expiry) and the change since then."""
    today = et_date(now).isoformat()
    first = (prev or {}).get("iv_first") if isinstance(prev, dict) else None
    if not first and isinstance(prev, dict) and prev.get("iv_pct") and prev.get("as_of"):
        # A row written before iv_first existed: its own reading is the earliest one known today.
        at = str(prev["as_of"])
        if et_date(datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()).isoformat() == today:
            first = {"day": today, "expiry": prev.get("expiry"), "pct": prev["iv_pct"], "at": at}
    keep = (isinstance(first, dict) and first.get("day") == today and first.get("expiry") == doc.get("expiry")
            and first.get("pct"))
    if not keep:
        first = ({"day": today, "expiry": doc.get("expiry"), "pct": doc.get("iv_pct"), "at": doc.get("as_of")}
                 if doc.get("iv_pct") else None)
    doc["iv_first"] = first
    if first and doc.get("iv_pct"):
        doc["iv_change_pts"] = _r(doc["iv_pct"] - first["pct"], 1)
        doc["iv_change_pct"] = _r((doc["iv_pct"] / first["pct"] - 1.0) * 100.0, 1)
    else:
        doc["iv_change_pts"] = doc["iv_change_pct"] = None
    return doc


def _minutes_between(a: str | None, b: str | None) -> float | None:
    try:
        ta = datetime.strptime(a, "%Y-%m-%dT%H:%M:%SZ")
        tb = datetime.strptime(b, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return (tb - ta).total_seconds() / 60.0


def iv_risk(doc: dict, now: float, params: dict = IV) -> dict:
    """How exposed a long option is to an IV drop: crushing (IV already falling today), high (earnings
    before expiry, or just reported), elevated (rumour / event premium), normal. Reasons in plain words."""
    reasons: list[tuple[int, str]] = []
    iv, hv, ratio = doc.get("iv_pct"), doc.get("hv_pct"), doc.get("iv_hv")
    first = doc.get("iv_first") or {}
    mins = _minutes_between(first.get("at"), doc.get("as_of"))
    chg = doc.get("iv_change_pct")
    vega = doc.get("vega_per_pt_usd")
    if chg is not None and mins is not None and mins >= params["crush_min_minutes"] and chg <= -100 * params["crush_drop"]:
        cost = f", about ${abs(doc['iv_change_pts']) * vega:,.0f} per contract" if vega and doc.get("iv_change_pts") else ""
        reasons.append((3, f"IV crushing: {first.get('pct'):.0f}% → {iv:.0f}% since {first.get('at', '')[11:16]} UTC{cost}"))
    today = et_date(now)
    last_e = doc.get("last_earnings_date")
    if last_e:
        try:
            reported = date.fromisoformat(last_e)
        except ValueError:
            reported = None
        # Today, or the previous trading session (a Friday after-close report still resets IV on Monday).
        prev_session = cal.previous_sessions(today, 1)[0].day
        if reported is not None and prev_session <= reported <= today:
            when = "today" if reported == today else "in the last session"
            reasons.append((2, f"Reported earnings {when}: IV has just reset"))
    if doc.get("earnings_before_expiry"):
        reasons.append((2, f"Earnings {doc.get('earnings_date')} before this expiry: IV usually collapses after the report"))
    if ratio is not None and ratio >= params["rich_ratio"]:
        reasons.append((1, f"IV {iv:.0f}% is {ratio:.1f}× the stock's normal {hv:.0f}%: a news or rumour premium that "
                           "fades if the story does"))
    term = doc.get("term_ratio")
    if term is not None and term >= params["term_inverted"]:
        reasons.append((1, f"Front-week IV {iv:.0f}% vs {doc.get('iv_30d_pct'):.0f}% for ~30 days: an event is priced "
                           "into this week"))
    level = {3: "crushing", 2: "high", 1: "elevated"}.get(max((r for r, _ in reasons), default=0), "normal")
    label = {"crushing": "IV crushing", "high": "IV crush risk", "elevated": "IV rich", "normal": "IV normal"}[level]
    return {"level": level, "label": label, "reasons": [t for _, t in sorted(reasons, key=lambda x: -x[0])]}


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


# The fields of a metrics doc that the staleness check (radar/option_lag.py) compares; the rest (catalysts,
# float, IV-risk reasons) stays in radar_option_metrics only.
HISTORY_KEYS = ("as_of", "price", "has_options", "expiry", "dte", "iv_pct", "hv_pct", "iv_hv", "term_ratio",
                "iv_change_pct", "earnings_before_expiry", "liquidity", "atm", "ntm_call_oi", "sides")


def history_doc(doc: dict) -> dict:
    out = {k: doc.get(k) for k in HISTORY_KEYS}
    out["iv_risk"] = (doc.get("iv_risk") or {}).get("level")
    return out


def radar_directions(state: dict) -> dict[str, str]:
    """{ticker: up/down} of the radar names now (members first, then heating, trending, today's exits)."""
    out: dict[str, str] = {}
    for key in ("members", "heating", "trending", "recent_exits"):
        for row in state.get(key) or []:
            t, d = row.get("ticker"), row.get("direction")
            if isinstance(t, str) and d in ("up", "down"):
                out.setdefault(t, d)
    return out


def radar_tickers(state: dict, limit: int) -> list[str]:
    """The members (highest intensity first), the warming-up names, then the names that left the radar this
    session (positions opened on them are often still open: their IV keeps being tracked)."""
    members = sorted(state.get("members") or [], key=lambda m: -(m.get("intensity") or 0))
    names = [m.get("ticker") for m in members] + [h.get("ticker") for h in state.get("heating") or []]
    names += [t.get("ticker") for t in state.get("trending") or [] if t.get("active")]
    session = (state.get("session") or {}).get("date")
    names += [x.get("ticker") for x in state.get("recent_exits") or []
              if session and str(x.get("exited_at") or "")[:10] == session]
    return list(dict.fromkeys(t for t in names if isinstance(t, str) and t))[:limit]


CONTEXT_KEYS = ("catalysts", "float", "earnings_date", "earnings_time", "earnings_estimate", "days_to_earnings",
                "earnings_before_expiry", "last_earnings_date", "last_earnings_time", "context_source", "context_at")


def mover_tickers(state: dict, have: list[str], rows: dict[str, dict], limit: int) -> list[str]:
    """The plain movers (state["movers"], radar/movers.py) to refresh now, besides the radar names `have`:
    the ones never read first, then the oldest reading; ties go to the larger move (the list's order). With
    `limit` per refresh the whole list is covered every few scans."""
    skip = set(have)
    names = [m.get("ticker") for m in state.get("movers") or [] if isinstance(m, dict)]
    names = [t for t in dict.fromkeys(names) if isinstance(t, str) and t and t not in skip]
    order = sorted(range(len(names)), key=lambda i: (str((rows.get(names[i]) or {}).get("as_of") or ""), i))
    return [names[i] for i in order[:max(limit, 0)]]


def context_fresh(prev: dict | None, now: float, ttl_min: float) -> bool:
    """True when the previous reading's market context (catalysts, float, earnings dates) is recent enough
    to carry over instead of asking FMP again."""
    at = (prev or {}).get("context_at")
    if not at:
        return False
    try:
        then = datetime.strptime(str(at), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return False
    return 0 <= now - then <= ttl_min * 60


def carry_context(doc: dict, prev: dict) -> None:
    for k in CONTEXT_KEYS:
        if k in prev:
            doc[k] = prev[k]


def refresh(store: Any, tickers: list[str] | None = None, *, fetch: Fetch | None = None,
            clock: Callable[[], float] = time.time, params: dict = OPTIONS, movers: dict = MOVERS) -> dict:
    t0 = time.monotonic()
    state = store.load_state() or {}
    syms = tickers if tickers is not None else radar_tickers(state, int(params["max_tickers"]))
    radar_names = set(syms)
    if tickers is None and state.get("movers"):
        # The plain movers ride along, a few per refresh: they are a display list, the radar names come first.
        try:
            rows = store.load_option_metrics([m.get("ticker") for m in state["movers"] if isinstance(m, dict)])
        except Exception:  # noqa: BLE001 - no rows: every mover counts as never read
            rows = {}
        syms = syms + mover_tickers(state, syms, rows, int(movers["options_per_refresh"]))
    if not syms:
        return {"status": "ok", "tickers": 0, "ok": 0, "failed": [], "duration_ms": 0}
    session_date = ((state.get("session") or {}).get("date"))
    base = baselines_for(store, session_date)
    fetch = fetch or _yahoo_fetch()
    now = clock()
    with ThreadPoolExecutor(max_workers=int(params["workers"])) as pool:
        docs = list(pool.map(lambda s: ticker_metrics(fetch, s, now, base.get(s), params), syms))
    got = {s: d for s, d in zip(syms, docs) if d is not None and not empty_quotes(d)}
    stale = [s for s, d in zip(syms, docs) if d is not None and empty_quotes(d)]
    failed = [s for s, d in zip(syms, docs) if d is None]
    if got:
        try:
            prev = store.load_option_metrics(list(got))
        except Exception as e:  # noqa: BLE001 - no history: the first reading starts today's IV trail
            logger.info("options: previous metrics unreadable: %s", e)
            prev = {}
        # Radar names get fresh context every refresh; plain movers at most every context_ttl_min (4 FMP calls
        # a name), carrying the previous reading's in between.
        ttl = float(movers["context_ttl_min"])
        fresh = {s: d for s, d in got.items() if s in radar_names or not context_fresh(prev.get(s), now, ttl)}
        try:
            from .context import add_context
            add_context(fresh, now)          # FMP: catalysts, exact earnings dates, float (when a key is set)
        except Exception:  # noqa: BLE001 - context is a bonus; the option metrics are stored regardless
            logger.warning("options: market context failed", exc_info=True)
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for sym, doc in got.items():
            if sym not in fresh:
                carry_context(doc, prev[sym])
            elif doc.get("context_source"):
                doc["context_at"] = stamp
        for sym, doc in got.items():
            carry_iv_first(doc, prev.get(sym), now)
            doc["iv_risk"] = iv_risk(doc, now)
        as_of = datetime.fromtimestamp(now, timezone.utc)
        store.put_option_metrics(got, as_of, int(params["keep_days"]))
        try:                                 # the staleness log is a bonus: never fails the refresh
            dirs = radar_directions(state)
            # The staleness log is about the radar's own names: the ride-along movers stay out of it.
            store.put_option_history([(s, dirs.get(s), history_doc(d)) for s, d in got.items() if s in radar_names],
                                     as_of, int(params.get("history_days", 21)))
        except Exception:  # noqa: BLE001
            logger.warning("options: history not written", exc_info=True)
    status = "ok" if not failed else "degraded" if got else "error"
    return {"status": status, "tickers": len(syms), "ok": len(got), "failed": failed, "kept_previous": stale,
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
