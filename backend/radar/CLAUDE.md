# Momentum Radar: working notes

What the code does is in `docs/` (signal model, runtime, storage) and in the root `CLAUDE.md`. This file holds
what is not derivable from the code: the rules for changing the radar, what was tried and measured, and the
traps. Dates are when the result was obtained; re-measure before leaning on an old number.

## Rules for changing it

- **Replay before any signal change.** A change to `PARAMS` (or to anything the engine reads, baselines
  included) needs a replay against the current version on the same sessions, and a new `params_version`.
  Targets (`radar/replay.py` `TARGETS`): 3-12 entries a day, 53%+ still moving the same way against SPY
  30 minutes later ("prec30"), flap rate 6% or less. Ship only what is at least as good; say so when it is not.
- **Display lists never feed the engine.** `trending`, `movers`, option metrics and catalysts are read by
  the page only. The replay scorecard must not move when they change.
- **`radar/tick.py` stays import-light:** no numpy at import time (a test enforces it). A module it imports
  (`movers.py`) takes `radar.types` under `TYPE_CHECKING` only.
- **The snapshot's keys are pinned** (`STATE_KEYS` in `tests/radar/test_tick.py`). A new key must also be
  carried by the three places that build a snapshot without a scan: `Tick._carry`, `Tick._closed_state`
  and the worker's failure snapshot (`worker.py`).
- **`radar_events.move_since_entry_pct` is the raw price change.** Multiply by the direction (+1 up, -1
  down) before calling an entry a winner. Misreading it once produced "up entries win 28%, down 77%"; adjusted,
  both were about 25% (October 2026).
- **A sensitivity change recomputes the day** (`--rebuild`): it replaces that session's member rows and events.
  It replays the names Stage A lists now plus every name that was on the radar or warming up earlier.
- **Wording:** verdicts and setups are rule checklists and patterns, always labelled "not advice" / "not a
  forecast". Colour an option metric by what it means for someone buying now (lower IV = cheaper = good), and
  show holder-side risks (IV already crushed) apart, labelled as such.

## What was measured

Replays run the real engine (`radar.replay.run`) on FMP 1-minute history rebuilt into 5-minute bars.

| When | Question | Result | Decision |
|---|---|---|---|
| 2026-11-11 | `radar-sm-2` on 60 sessions (Aug 18 - Nov 10) | 7.2 entries/day, prec30 52.1%, +6.6 bp at 30 min, flap 3.5%. By period: Aug-Sep 57%, Oct 1-21 27% (-23 bp), Oct 22 - Nov 10 58% | the baseline to beat |
| 2026-11-11 | In-play gate at 1.5 or 2.0 instead of 2.5 | 1.5: 14.1/day, 47.0%, +3.0 bp. 2.0: 10.2/day, 48.9%, +3.9 bp | looser gates add losers; keep 2.5 |
| 2026-11-11 | Robust daily yardstick (market-adjusted returns, 60 or 21 sessions, extreme days clipped) | 60 sessions: 48.5%, +2.6 bp. 21 sessions: 50.2%, +8.3 bp, 8.4/day; every variant added November entries that lost 14-17 bp | not shipped; the inflated yardstick acts as a brake in choppy markets |
| 2026-10-21 | Does extension (day move, distance from VWAP) predict a top? | No (AUC 0.41-0.47). Only signal: up entries whose entry or heating bar accelerated 1.5+ topped within 30 min 33% of the time against 14% | the "Extended, don't chase" tag (display); an engine gate only after 20+ live tagged entries confirm it |
| 2026-10-21 | Catching slow grinds earlier (looser trend list, trend entries, lower in-play gate) | no variant had a robust edge on 54 sessions | trend list set "earlier" for display; no trend entries |
| 2026-10-16 | Every entry traded as an at-the-money option | +$17 a contract at mid, -$170 after spread and commissions; only the spread (15% or less) and liquidity filters helped, by avoiding costs | verdict rules: spread and liquidity first |
| 2026-10-16 | How stale are the 15-minute-delayed option quotes? | the option verdict differs from the one 15 minutes later 26% of the time | readings are logged in `radar_option_history` (`python -m radar.option_lag`) |
| 2026-10-15 | 1-minute detection | 2-5 minutes earlier, more weak entries, differences inside grid-phase noise | not shipped |
| 2026-11-11 | Multi-day setups, daily bars, 536 names, 156 sessions, next 5 sessions against SPY | down 8%+ over 5 days and above the 50-day average: +1.6%, right 54% (48% for any stock-day; halves +1.7% / +1.3%). Same drop below the average: +0.7%, 51%. Up 15%+ over 5 days: 0.0%, 45% | the Movers setups (`config.MOVERS`) |

**What the radar is not built for** (2026-11-11, CRWV / NBIS / SNDK / AMD): gap-and-hold days in high-beta
names on a strong market day. The thrust is measured against the market, so a +1.3% SPY morning leaves little;
CRWV did heat up and missed the next-bar confirmation by 8 cents. The Movers view exists for these.

## Movers view (display only)

- `movers.py` `plain_movers`: plain percentage change since the prior close for every eligible name, no
  market adjustment. Kept in the snapshot (`movers`, `movers_as_of`): the 60 largest gainers and losers of
  2%+ today, the 25 largest per side over each of 3 / 5 / 10 / 20 sessions (5%+), and up to 40 dips in an
  uptrend. It stays listed after the close and across failed scans.
- Multi-day numbers come from `SymbolBaseline.closes` (the last 60 daily closes, stored in the day's baseline
  pack). A pack built before a field existed simply lacks it: new pack fields must default to empty and show
  up with the next morning's pack.
- `options.py` refreshes 50 movers per scan after the radar's own names (oldest reading first), carries their
  FMP context for 30 minutes, and keeps them out of `radar_option_history`.
- `swing`: what one at-the-money call / put costs for the first expiry 14-31 days out (one more chain request
  per ticker). `implausible_swing_sides` drops stale quotes (time value above 300% annualised volatility,
  price well under intrinsic, put-call parity off by more than 8% of the price).

## Data and experiments

- **FMP** (primary: quotes, daily bars, movers, 1-minute history) allows about 300 requests a minute and
  production shares the key: keep experiments at 3 requests a second or less. It has no US option chains.
- **Yahoo** serves the option chains: about 15 minutes delayed, no bid/ask until about 09:46 ET and empty
  after hours (such readings are not stored; the previous row is kept). Thin contracts can carry absurd stale
  quotes.
- **Replay inputs:** a 5-minute CSV (`symbol,ts,open,high,low,close,volume`, `ts` = bar start in UTC epoch
  seconds, regular session only) and a daily CSV; `tools/replay_cli.py` shows the calls. Convert timestamps
  with an explicit seconds division: pandas' default resolution is not always nanoseconds.
- **A change that needs live data** can be dry-run inside a one-off container that reads production and
  writes nothing (replace `Tick._finish`), before any deploy.

## Traps

- **Mounting a file into `/app` creates it on the host.** `docker compose run -v x.py:/app/radar/new.py`
  leaves an empty `backend/radar/new.py` in the server checkout (the service bind-mounts `./backend`), which
  then blocks `git pull`. Mount under another path, or delete the stub.
- **Pulled code runs before the rebuild:** scans are subprocesses reading the bind-mounted checkout, so the
  next scan after a `git pull` already runs the new code.
- **The daily yardstick (`sigma_d`) inflates after a volatile fortnight** and the radar goes quiet (0
  entries on 2026-11-11). That is known and, so far, better than the alternatives measured above.
- **Do not poll the server over SSH in a loop:** a connection every 30 seconds for 10 minutes got further
  connections reset. Wait inside one session.
