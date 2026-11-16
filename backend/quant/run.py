"""Quant jobs, one per invocation; the last stdout line is a JSON result (like radar.tick).

    python -m quant.run bars [--tickers A,B] [--backfill]   # daily bars: backfill new tickers, extend the rest
    python -m quant.run chains [--tickers A,B]              # option chain snapshot (run in the session, ~15:40 ET)
    python -m quant.run forecast                            # HAR fit + forecasts + readings (after bars)

Scheduled by services/quant_runner.py (APScheduler in the backend, each job a subprocess with a timeout).
Tickers: active universe + active watchlist + CORE_ETFS + config.SECTOR_ETFS; bars also take the names with a
radar event in the last RADAR_RECENT_DAYS days.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from .config import CORE_ETFS, RADAR_RECENT_DAYS

logger = logging.getLogger(__name__)
JOBS = ("bars", "chains", "forecast")


def resolve_tickers(store, job: str, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    try:
        from config import SECTOR_ETFS
        etfs = set(SECTOR_ETFS.values())
    except Exception:  # noqa: BLE001 - the app config is optional for the CLI
        etfs = set()
    names = set(CORE_ETFS) | etfs | set(store.universe_sectors()) | set(store.watchlist_sectors())
    if job == "bars":
        try:
            names |= set(store.radar_recent_tickers(now - timedelta(days=RADAR_RECENT_DAYS)))
        except Exception:  # noqa: BLE001 - radar tables are a bonus
            logger.info("radar tickers unavailable", exc_info=True)
    return sorted(t.strip().upper() for t in names if t and t.strip())


def run_job(job: str, tickers: list[str] | None = None, *, backfill: bool = False, store=None) -> dict:
    from .store import QuantStore

    own = store is None
    store = store or QuantStore()
    try:
        if job == "bars":
            from .bars import update
            return update(store, tickers or resolve_tickers(store, "bars"), backfill=backfill)
        if job == "chains":
            from .chains import collect
            return collect(store, tickers or resolve_tickers(store, "chains"))
        if job == "forecast":
            from .forecast import run
            return run(store, tickers)
        raise ValueError(f"unknown job {job!r}")
    finally:
        if own:
            store.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(prog="python -m quant.run", description=__doc__.splitlines()[0])
    ap.add_argument("job", choices=JOBS)
    ap.add_argument("--tickers", help="comma-separated tickers (default: the job's ticker set)")
    ap.add_argument("--backfill", action="store_true", help="bars: re-fetch the full history of every ticker")
    args = ap.parse_args(argv)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    try:
        res = run_job(args.job, tickers, backfill=args.backfill)
    except Exception as e:  # noqa: BLE001 - reported on stdout for the scheduler
        import traceback

        from .bars import mask
        logger.error("quant %s failed: %s", args.job, mask(traceback.format_exc()))   # key masked
        res = {"status": "error", "message": mask(f"{type(e).__name__}: {e}")[:500]}
    print(json.dumps(res, default=str), flush=True)
    return 0 if res.get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
