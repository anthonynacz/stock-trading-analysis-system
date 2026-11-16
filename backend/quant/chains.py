"""Per-strike option chain snapshots into quant_chain_snapshots (~15:40 ET, once a session).

Reads Yahoo's v7 options endpoint through radar.options (`_get` with the crumb-guarded transport the radar's
option refresh uses), so there is one Yahoo options client in the codebase. Per ticker: one request for the
expiry list (it also returns the nearest expiry's chain and the quote), then one per selected expiry
(`select_expiries`: the two bracketing 30 days plus the nearest to each CHAINS.target_dtes, at most
CHAINS.max_expiries, out to CHAINS.max_dte).

Stored per (ticker, ET date, expiry): the contracts within CHAINS.strike_band of spot as parallel arrays, plus the
expiry's ATM IV and parity forward (quant/iv.py). Nothing is stored without quotes: a contract needs an ask
> 0 (an offer), an expiry needs at least one bid > 0, so a pre-09:46 or after-hours read (Yahoo serves zeros
then) writes nothing and the day's earlier snapshot, if any, stays.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any, Callable

from radar import options as ropt
from utils.greeks import RISK_FREE_RATE

from .bars import Pacer, fetch_symbol
from .config import CHAINS, IV
from .iv import expiry_atm_iv, year_fraction

logger = logging.getLogger(__name__)

FIELDS = ("strike", "side", "bid", "ask", "last", "iv", "volume", "oi")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _count(v: Any) -> int | None:
    f = _num(v)
    return int(f) if f is not None and f >= 0 else None


def select_expiries(expirations: list[int], today: date, cfg: dict = CHAINS, iv_cfg: dict = IV) -> list[int]:
    """Yahoo expiry epochs to snapshot: within cfg.max_dte, the pair bracketing iv_cfg.target_days (with
    min_dte respected) always, then the nearest to each target DTE, capped at cfg.max_expiries, ascending."""
    exps = sorted({int(e) for e in expirations or []})
    dte = {e: (ropt.exp_date(e) - today).days for e in exps}
    pool = [e for e in exps if 0 <= dte[e] <= int(cfg["max_dte"])]
    if not pool:
        return []
    chosen: list[int] = []
    usable = [e for e in pool if dte[e] >= int(iv_cfg["min_dte"])]
    tgt = int(iv_cfg["target_days"])
    below = [e for e in usable if dte[e] <= tgt]
    above = [e for e in usable if dte[e] >= tgt]
    for e in ([below[-1]] if below else []) + ([above[0]] if above else []):
        if e not in chosen:
            chosen.append(e)
    for t in cfg["target_dtes"]:
        if len(chosen) >= int(cfg["max_expiries"]):
            break
        e = min(pool, key=lambda x: (abs(dte[x] - t), dte[x]))
        if e not in chosen:
            chosen.append(e)
    return sorted(chosen[: int(cfg["max_expiries"])])


def filter_contracts(chain: dict | None, spot: float, cfg: dict = CHAINS) -> dict[str, list] | None:
    """One Yahoo expiry chain -> the columnar contracts document, or None when no contract carries a bid.

    Kept: strikes within cfg.strike_band of spot with an ask > 0. Every number is sanitised (NaN / inf / negative
    -> None; IV outside [min_iv, max_iv] -> None, Yahoo's placeholder for an unquoted contract)."""
    if not chain or not spot or spot <= 0:
        return None
    lo, hi = spot * (1 - float(cfg["strike_band"])), spot * (1 + float(cfg["strike_band"]))
    cols: dict[str, list] = {k: [] for k in FIELDS}
    any_bid = False
    for side, key in (("C", "calls"), ("P", "puts")):
        for c in chain.get(key) or []:
            k = _num(c.get("strike"))
            ask = _num(c.get("ask"))
            if k is None or not (lo <= k <= hi) or ask is None or ask <= 0:
                continue
            bid = _num(c.get("bid"))
            bid = bid if bid is not None and bid >= 0 else None
            if bid is not None and bid > ask:
                continue                       # crossed quote: not a reading
            any_bid = any_bid or (bid or 0) > 0
            iv = _num(c.get("impliedVolatility"))
            last = _num(c.get("lastPrice"))
            cols["strike"].append(k)
            cols["side"].append(side)
            cols["bid"].append(bid)
            cols["ask"].append(ask)
            cols["last"].append(last if last is not None and last >= 0 else None)
            cols["iv"].append(round(iv, 5) if iv is not None and cfg["min_iv"] <= iv <= cfg["max_iv"] else None)
            cols["volume"].append(_count(c.get("volume")))
            cols["oi"].append(_count(c.get("openInterest")))
    if not any_bid or not cols["strike"]:
        return None
    return cols


def snapshot_rows(ticker: str, result: dict, chains: dict[int, dict | None], snapshot_at: datetime,
                  cfg: dict = CHAINS) -> list[dict]:
    """Rows for quant_chain_snapshots from one ticker's Yahoo reads ({expiry epoch: chain})."""
    quote = result.get("quote") or {}
    spot = _num(quote.get("regularMarketPrice"))
    if not spot or spot <= 0:
        return []
    day = snapshot_at.astimezone(ropt.cal.ET).date()
    earn, _ = ropt.next_earnings(quote, snapshot_at.timestamp())
    rows = []
    for e, chain in sorted(chains.items()):
        cols = filter_contracts(chain, spot, cfg)
        if cols is None:
            continue
        expiry = ropt.exp_date(e)
        T = year_fraction(snapshot_at, expiry)
        atm = expiry_atm_iv(cols, spot, T, RISK_FREE_RATE) if T > 0 else None
        rows.append({"ticker": ticker, "snapshot_date": day, "expiry": expiry, "snapshot_at": snapshot_at,
                     "underlying_price": spot, "dte": (expiry - day).days, "n_contracts": len(cols["strike"]),
                     "atm_iv": round(atm["iv"], 6) if atm else None,
                     "forward": round(atm["forward"], 4) if atm and atm.get("forward") else None,
                     "iv_method": atm["method"] if atm else None, "earnings_date": earn, "contracts": cols})
    return rows


def ticker_snapshot(fetch: Callable, ticker: str, now: datetime, pacer: Callable[[], None],
                    cfg: dict = CHAINS) -> list[dict] | None:
    """All rows of one ticker, None when Yahoo could not be read."""
    sym = fetch_symbol(ticker)
    timeout = float(cfg["timeout_s"])
    pacer()
    first = ropt._get(fetch, sym, {}, timeout)
    if first is None:
        return None
    exps = first.get("expirationDates") or []
    wanted = select_expiries(exps, now.astimezone(ropt.cal.ET).date(), cfg)
    got: dict[int, dict | None] = {}
    for ch in first.get("options") or []:
        e = int(ch.get("expirationDate") or 0)
        if e in wanted:
            got[e] = ch
    result = first
    for e in wanted:
        if e in got:
            continue
        pacer()
        r = ropt._get(fetch, sym, {"date": e}, timeout)
        chs = (r or {}).get("options") or []
        got[e] = chs[0] if chs else None
        if r and r.get("quote"):
            result = {**result, "quote": r["quote"]}
    return snapshot_rows(ticker, result, got, now, cfg)


def collect(store: Any, tickers: list[str], *, fetch: Callable | None = None, now: datetime | None = None,
            cfg: dict = CHAINS) -> dict:
    t0 = time.monotonic()
    now = now or datetime.now(timezone.utc)
    fetch = fetch or ropt._yahoo_fetch()
    tickers = tickers[: int(cfg["max_tickers"])]
    lock = threading.Lock()
    pacer_obj = Pacer(float(cfg["max_rps"]))

    def pace() -> None:
        with lock:
            pacer_obj.wait()

    def one(t: str) -> tuple[str, list[dict] | None]:
        try:
            return t, ticker_snapshot(fetch, t, now, pace, cfg)
        except Exception as e:  # noqa: BLE001 - one ticker never stops the run
            logger.warning("chains %s: %s: %s", t, type(e).__name__, str(e)[:200])
            return t, None

    stats = {"tickers": len(tickers), "stored": 0, "rows": 0, "no_quotes": [], "failed": []}
    with ThreadPoolExecutor(max_workers=int(cfg["workers"])) as pool:
        for t, rows in pool.map(one, tickers):
            if rows is None:
                stats["failed"].append(t)
            elif not rows:
                stats["no_quotes"].append(t)
            else:
                stats["rows"] += store.upsert_chain_rows(rows)
                stats["stored"] += 1
    stats["snapshot_date"] = now.astimezone(ropt.cal.ET).date().isoformat()
    stats["duration_ms"] = int((time.monotonic() - t0) * 1000)
    stats["status"] = "ok" if stats["stored"] and not stats["failed"] else ("degraded" if stats["stored"] else "error")
    return stats
