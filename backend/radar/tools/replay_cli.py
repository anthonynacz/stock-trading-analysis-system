"""Replay the radar over recorded bars and print the scorecard (signal-model.md section 8.2).

python -m radar.tools.replay_cli --bars bars_5m.csv.gz --daily bars_1d.csv.gz [--sessions N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from radar.config import PARAMS
from radar.replay import load_bars_csv, load_daily_csv, load_meta, replay_sessions, run, universe_filter

DEFAULT_META = Path(__file__).resolve().parents[1] / "data" / "universe.csv"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bars", required=True, help="5-minute bars CSV(.gz): symbol,ts,open,high,low,close,volume")
    ap.add_argument("--daily", required=True, help="daily bars CSV(.gz): symbol,date,open,high,low,close,adjclose,volume")
    ap.add_argument("--sessions", type=int, default=None, help="replay only the last N sessions")
    ap.add_argument("--meta", default=str(DEFAULT_META), help="universe CSV with name, sector, is_etf")
    ap.add_argument("--seed", type=int, default=PARAMS["baseline"]["sessions"],
                    help="leading sessions used only to seed baselines")
    ap.add_argument("--out", default=None, help="also write the scorecard JSON here")
    ap.add_argument("--no-stage-a", action="store_true", help="feed every symbol's bars (skip the quote prefilter)")
    ap.add_argument("--no-baselines", action="store_true", help="skip the all-ticks and naive-list baselines")
    ap.add_argument("--roundtrip", action="store_true", help="rebuild the engine from JSON state every tick")
    args = ap.parse_args(argv)

    t0 = time.monotonic()
    meta = load_meta(args.meta) if Path(args.meta).exists() else {}
    bars = universe_filter(load_bars_csv(args.bars), meta)
    daily = load_daily_csv(args.daily)
    sessions = replay_sessions(bars, args.seed, args.sessions)
    card = run(bars, daily, PARAMS, sessions, meta=meta, stage_a=not args.no_stage_a,
               baselines=not args.no_baselines, roundtrip=args.roundtrip)
    card["symbols"] = len({s for day in bars.values() for s in day})
    card["runtime_s"] = round(time.monotonic() - t0, 1)
    text = json.dumps(card, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
