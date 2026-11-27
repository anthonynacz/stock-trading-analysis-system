"""One-off IV history backfill from EODHD end-of-day option chains (`iv_daily`, source "eodhd").

Yahoo serves only today's chain, so the clean IV history starts on the day the
15:00 ET capture went live. EODHD's US options product keeps about two years
of end-of-day chains (bid, ask, IV per contract). For each ticker and trading
day this fetches the near-the-money chain 7-60 days out, runs the same IV30
code as the live capture (utils/iv_measure.py) and stores one row. Rows that
already exist (live captures or an earlier run) are never overwritten, so the
run is resumable: start it again and it continues where it stopped.

    docker compose exec backend python -m services.iv_backfill --days 365
    docker compose exec backend python -m services.iv_backfill --tickers AAPL,AMZN --days 30

Needs EODHD_API_KEY: the "US Stock Options Data API" marketplace add-on alone
(no EODHD price plan). One request per ticker and day, about 150k requests for
600 tickers over a year. Daily closes (strike window, realised vol) come from
Yahoo, one call per ticker. Yahoo's closes are split-adjusted while EODHD keeps
the strikes as listed, so days before a split find no strikes and are skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date, timedelta
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from db.connection import async_session
from db.models import IvDaily
from services.iv_collector import et_today, iv_universe, reading_values
from utils.data_sources import DataSourceClient
from utils.iv_measure import MIN_DTE, iv_from_chains, realised_vol

logger = logging.getLogger(__name__)

BASE = "https://eodhd.com/api"
CHAIN_DTE_MAX = 60              # expiries fetched: MIN_DTE..60 days out (IV30 needs the ones around 30)
STRIKE_BAND = 0.07              # strikes within +-7% of the close
PAUSE_S = 0.07                  # ~850 requests a minute, under EODHD's 1000/min
HV_LOOKBACK_DAYS = 120          # extra calendar days of closes so HV60 exists on the first backfilled day

Fetch = Callable[[str, dict], Any]


def http_fetch(token: str) -> Fetch:
    client = httpx.Client(timeout=60)

    def fetch(path: str, params: dict) -> Any:
        for attempt in range(4):
            r = client.get(f"{BASE}{path}", params={**params, "api_token": token})
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 5)
                continue
            if r.status_code in (403, 404):
                return None                            # symbol not covered by the plan / unknown
            raise RuntimeError(f"EODHD {path}: HTTP {r.status_code}")   # never the URL: it carries the key
        return None

    return fetch


def day_chains(fetch: Fetch, ticker: str, day: date, spot: float) -> dict[str, dict]:
    """{expiry: {"calls": [...], "puts": [...]}} of `day`'s end-of-day snapshot near the money.

    The API filters on the contract's last trade date, so a contract that last traded on `day` also comes back
    in later snapshots (record id "<contract>-<snapshot date>"); only the snapshot of `day` is kept.
    """
    data = fetch("/mp/unicornbay/options/eod", {
        "filter[underlying_symbol]": ticker,
        "filter[tradetime_eq]": day.isoformat(),
        "filter[exp_date_from]": (day + timedelta(days=MIN_DTE)).isoformat(),
        "filter[exp_date_to]": (day + timedelta(days=CHAIN_DTE_MAX)).isoformat(),
        "filter[strike_from]": round(spot * (1 - STRIKE_BAND), 2),
        "filter[strike_to]": round(spot * (1 + STRIKE_BAND), 2),
        "page[limit]": 1000,
        "fields[options-eod]": "exp_date,type,strike,bid,ask,volatility",
    }) or {}
    chains: dict[str, dict] = {}
    for rec in data.get("data") or []:
        if str(rec.get("id", ""))[-10:] != day.isoformat():
            continue
        a = rec.get("attributes") or {}
        if a.get("exp_date") is None or a.get("type") not in ("call", "put"):
            continue
        row = {"strike": a.get("strike"), "bid": a.get("bid"), "ask": a.get("ask"),
               "impliedVolatility": a.get("volatility")}
        side = chains.setdefault(a["exp_date"], {"calls": [], "puts": []})
        side["calls" if a["type"] == "call" else "puts"].append(row)
    return chains


def ticker_readings(fetch: Fetch, ticker: str, start: date, end: date, skip: set[date], closes: list[dict],
                    pause_s: float = PAUSE_S) -> list[dict]:
    """iv_daily rows for the trading days in [start, end] not in `skip`.

    `closes` is [{date, close, adjusted_close}] ascending from HV_LOOKBACK_DAYS before `start`: the traded close
    centres the strike window, the adjusted close feeds realised vol.
    """
    out: list[dict] = []
    for i, c in enumerate(closes):
        d = date.fromisoformat(c["date"])
        if d < start or d > end or d in skip:
            continue
        spot = float(c["close"])
        chains = day_chains(fetch, ticker, d, spot)
        if pause_s:
            time.sleep(pause_s)
        iv = iv_from_chains(chains, spot, d)
        if not iv["iv30"]:
            continue
        adj = [float(x.get("adjusted_close") or x["close"]) for x in closes[: i + 1]]
        out.append({"ticker": ticker, "day": d, "iv30": iv["iv30"], "iv_front": iv["iv_front"],
                    "front_dte": iv["front_dte"], "hv20": realised_vol(adj, 20), "hv60": realised_vol(adj, 60),
                    "spot": spot, "source": "eodhd"})
    return out


async def run_backfill(tickers: list[str] | None, days: int, fetch: Fetch | None = None,
                       prices: DataSourceClient | None = None) -> dict:
    from services.run_log import STATUS_FAILED, STATUS_SUCCESS, record_run_finish, record_run_start

    if fetch is None:
        if not settings.EODHD_API_KEY:
            raise SystemExit("EODHD_API_KEY is not set")
        fetch = http_fetch(settings.EODHD_API_KEY)
    prices = prices or DataSourceClient()
    end = et_today() - timedelta(days=1)
    start = end - timedelta(days=days)
    run_id = await record_run_start(phase="iv_backfill", user_id=None, meta={"days": days})
    summary: dict[str, Any] = {"tickers": 0, "stored": 0, "errors": 0}
    loop = asyncio.get_event_loop()
    error: str | None = None
    session = async_session()
    try:
        names = tickers or await iv_universe(session)
        summary["tickers"] = len(names)
        for n, ticker in enumerate(names, 1):
            have = set((await session.execute(
                select(IvDaily.day).where(IvDaily.ticker == ticker, IvDaily.day >= start, IvDaily.day <= end)
            )).scalars())
            try:
                closes = await loop.run_in_executor(
                    None, prices.get_daily_prices, ticker, start - timedelta(days=HV_LOOKBACK_DAYS), end)
                rows = await loop.run_in_executor(None, ticker_readings, fetch, ticker, start, end, have, closes)
            except Exception:
                summary["errors"] += 1
                logger.warning("iv backfill failed for %s", ticker, exc_info=True)
                continue
            for row in rows:
                await session.execute(
                    pg_insert(IvDaily).values(**reading_values(row)).on_conflict_do_nothing(
                        index_elements=["ticker", "day"])
                )
            await session.commit()
            summary["stored"] += len(rows)
            logger.info("iv backfill %d/%d %s: %d days", n, len(names), ticker, len(rows))
    except Exception as exc:  # noqa: BLE001 - recorded on the run row
        error = str(exc)
        logger.exception("iv backfill failed")
        await session.rollback()
    finally:
        await session.close()
    await record_run_finish(run_id, status=STATUS_FAILED if error else STATUS_SUCCESS,
                            error_message=error, meta=summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)          # request URLs carry the API key
    ap = argparse.ArgumentParser(prog="python -m services.iv_backfill", description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=365, help="calendar days back from yesterday (default 365)")
    ap.add_argument("--tickers", help="comma-separated tickers; default: the iv_capture universe")
    args = ap.parse_args(argv)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    print(asyncio.run(run_backfill(tickers, args.days)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
