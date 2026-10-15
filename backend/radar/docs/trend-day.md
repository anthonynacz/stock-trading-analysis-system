# Trending today (radar/trend.py)

A display-only list of stocks that grind steadily one way from the open, usually on news, without the 15/30-minute
bursts the radar's entry engine waits for (EBAY 2026-10-15: about $5 of a $7 takeover-rumour move came before
any burst signal). It never adds radar members or changes entries and exits: the replay scorecard is identical
with and without it (204 entries, prec30 0.549, flap 4.4%).

## Rule (set "early", radar/trend.py `THRESHOLDS`)

At 5-minute slot k, using bars 0..k only, market-relative (log move minus beta x SPY), in the stock's daily
volatility sigma_d; direction = sign of the move since the open:

| Condition | Threshold |
|---|---|
| Move since the open | >= 1.5 sigma_d |
| Move since the prior close | >= 2.0 sigma_d |
| Bars closed on the trend side of session VWAP | >= 90%, including the last one |
| Path efficiency since the open | >= 0.45 |
| 45-minute regression slope | points the trend way |
| Session high (low) | set within the last 30 minutes |
| Day volume | >= 1.5x normal for the time of day |
| Window | new names 10:00-13:30 ET, at most one listing per name and day |

Exit: four consecutive 5-minute closes on the wrong side of VWAP (the name stays shown, faded).

## Backtest (2026-10-15, 40 sessions 2026-07-31..09-25, ~520 names)

| | early (live) | balanced | strict | naive: up >= 7.75% | radar entries |
|---|---|---|---|---|---|
| Names / day | 9.7 | 7.8 | 3.9 | 10.0 | 5.1 |
| Median time | 10:25 | 10:35 | 10:40 | 10:30 | 11:35 |
| Same direction at +30 / +60 min / close | .51 / .52 / .52 | .53 / .49 / .52 | .47 / .49 / .53 | .49 / .48 / .50 | .55 / - / .54 |
| Mean move to the close (SPY-relative) | +19.8 bp (cal +22.5 / val +17.7) | +21.4 | +21.6 | +11.2 | +17.8 |
| Median adverse move | 95 bp | 94 | 110 | 155 | - |

- Where the radar also entered the name, the list was first in most cases: a median 50 minutes earlier at a
  121 bp better price. 69% of listed names were never entered by the radar.
- Not a predictor: hit rates stay about 50% and the day-clustered 90% interval on the mean move to the close is
  +1..+39 bp. The VWAP condition was the only one that helped consistently in both halves.
- Exits: holding to the close +19.8 bp; 4-bar VWAP exit +17.0 bp (+6 bp left after it, so it leaves little);
  giveback exits cut names that recovered.
- Caveats: 40 sessions clustered by day (up to 32 names on 09-22, mostly financials short); thresholds chosen
  looking at both halves; short flags did better than long ones in Aug-Sep 2026.

Live-engine check (scratchpad verify_trend.py, same corpus): 9.7 names/day, +20.2 bp mean to the close, 52% hit.
