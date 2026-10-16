// The radar's entry verdict for beginner / intermediate option traders: Favorable / Caution / Avoid with one
// plain sentence. Rules-based (no model), built from what the options backtest (2026-10-16, 40 sessions,
// backend/radar/docs + scratchpad optpnl) showed matters for short holds (~40 min):
//   - the bid-ask spread decided the outcome: all entries lost ~$170/contract after the spread (+$17 before);
//     ATM spread <= 15% cut that to about -$44, <= 10% to about break-even; thin chains lost ~$270;
//   - IV crush, rich IV and earnings mattered little for same-day trades, a lot for anything held overnight;
//   - momentum that is fading (cooling, near an exit rule) is a worse entry than one still racing.
// A checklist, not advice: even "Favorable" setups only roughly broke even after costs in the backtest.

import type { RadarDirection, RadarMemberState, RadarOptionMetrics } from '../../types';
import { etParts } from './clock';
import { fmtShortDate } from './optionFilters';
import { sideOf, type SideView } from './optionSide';
import type { WatchLevel } from './exitRules';

export type Verdict = 'favorable' | 'caution' | 'avoid' | 'unknown';

export interface VerdictResult {
  verdict: Verdict;
  /** The main reason, one sentence. */
  headline: string;
  /** Every point considered, worst first. */
  points: { level: Exclude<Verdict, 'unknown'>; text: string }[];
  /** "Costs: about $76 round trip on the spread · time decay about $18/day" when known. */
  costs: string | null;
}

export interface VerdictContext {
  direction: RadarDirection;
  /** member: on the radar; heating: not confirmed yet; trending: the Trending today list. */
  role: 'member' | 'heating' | 'trending';
  /** Member state and exit-watch level (members only). */
  state?: RadarMemberState;
  exitLevel?: WatchLevel;
  /** Trending list: still listed. */
  active?: boolean;
  nowMs: number;
}

export const SPREAD_OK = 10;      // % of mid
export const SPREAD_MAX = 15;
// Same thresholds as backend/radar/options.py IV rich_ratio / term_inverted.
const IV_RICH = 1.5;
const TERM_INVERTED = 1.2;

const usd = (v: number) => `$${Math.round(Math.abs(v)).toLocaleString('en-US')}`;

function costLine(s: SideView): string | null {
  const parts: string[] = [];
  if (s.spreadUsd !== null) parts.push(`about ${usd(s.spreadUsd)} per contract lost to the bid-ask spread on a round trip`);
  if (s.thetaDayUsd !== null) parts.push(`time decay about ${usd(s.thetaDayUsd)} per contract per day`);
  return parts.length ? `Costs: ${parts.join(' · ')}.` : null;
}

export function verdictFor(o: RadarOptionMetrics | undefined, ctx: VerdictContext): VerdictResult {
  const side = sideOf(o, ctx.direction);
  if (!o) {
    return { verdict: 'unknown', headline: 'Option data arrives after the next scan.', points: [], costs: null };
  }
  if (!o.has_options) {
    return { verdict: 'avoid', headline: 'No listed options on this stock.', points: [{ level: 'avoid', text: 'No listed options.' }], costs: null };
  }
  if (!side) {
    return { verdict: 'unknown', headline: 'Put data arrives after the next scan.', points: [], costs: null };
  }
  const pts: VerdictResult['points'] = [];
  const add = (level: 'avoid' | 'caution', text: string) => pts.push({ level, text });
  const what = side.plural;

  // 1. Cost of trading: liquidity and spread (the deciding factor in the backtest).
  if (side.liquidity === 'none' || side.liquidity === 'thin') {
    add('avoid', `Thin ${what}: few contracts trade near the price, so fills are costly${side.spreadUsd !== null ? ` (about ${usd(side.spreadUsd)} lost on the spread per contract)` : ''}.`);
  }
  if (side.spreadPct === null || side.spreadPct === undefined) {
    add('avoid', `No live ${side.kind} quote right now: you cannot see what you would pay.`);
  } else if (side.spreadPct > SPREAD_MAX) {
    add('avoid', `The ${side.kind}'s bid-ask spread is ${side.spreadPct.toFixed(0)}% of its price${side.spreadMovePct ? `: the stock must move ${side.spreadMovePct.toFixed(1)}% just to cover it` : ''}.`);
  } else if (side.spreadPct > SPREAD_OK) {
    add('caution', `The spread is ${side.spreadPct.toFixed(0)}% of the ${side.kind}'s price${side.spreadMovePct ? ` (the stock must move ${side.spreadMovePct.toFixed(1)}% to cover it)` : ''}: use a limit order near the middle.`);
  }

  // 2. Implied volatility and earnings.
  // Read the fields, not only iv_risk.level: the level keeps just the worst reason (backend options.iv_risk).
  if (o.iv_risk?.level === 'crushing') add('avoid', 'IV is falling fast today: the option loses value even if the stock holds still.');
  if (o.earnings_before_expiry) add('caution', `Earnings ${fmtShortDate(o.earnings_date)} before this expiry: close the trade the same day; holding through the report usually loses a big part of the price.`);
  if (o.iv_risk?.reasons?.some((r) => r.startsWith('Reported earnings'))) add('caution', 'Earnings were just reported: IV has reset and moves can reverse sharply.');
  const rich = (o.iv_hv ?? 0) >= IV_RICH || (o.term_ratio ?? 0) >= TERM_INVERTED;
  if (rich) add('caution', `Options are pricey${o.iv_hv ? ` (IV ${o.iv_hv.toFixed(1)}× the stock's normal)` : ''}: if the story fades, the premium deflates.`);

  // 3. Momentum and timing.
  if (ctx.role === 'member') {
    if (ctx.state === 'halted') add('avoid', 'Trading looks halted: you may not be able to get out.');
    else if (ctx.state === 'cooling' || ctx.exitLevel === 'near') add('avoid', 'The move is stalling and may drop off the radar soon.');
    else if (ctx.exitLevel === 'watch') add('caution', 'Momentum is softening: watch the exit signals.');
  } else if (ctx.role === 'heating') {
    add('caution', 'Not confirmed yet: wait for the next scan to confirm the move.');
  } else if (ctx.role === 'trending') {
    if (ctx.active === false) add('avoid', 'No longer trending: it fell back through VWAP.');
    else add('caution', 'Trend list is informational: about half of these keep going to the close.');
  }
  const { h, m } = etParts(ctx.nowMs);
  const minutes = h * 60 + m;
  if (minutes >= 15 * 60 && minutes < 16 * 60) add('caution', 'Less than an hour of trading left: little time for the move to pay for the spread.');
  if (o.dte !== null && o.dte !== undefined && o.dte <= 3) add('caution', `This expiry is ${o.dte} day${o.dte === 1 ? '' : 's'} out: time decay is fast.`);

  pts.sort((a, b) => (a.level === b.level ? 0 : a.level === 'avoid' ? -1 : 1));
  const verdict: Verdict = pts.some((p) => p.level === 'avoid') ? 'avoid' : pts.length ? 'caution' : 'favorable';
  const headline =
    verdict === 'favorable'
      ? `Liquid ${what}, normal IV and the move is still going.`
      : pts[0].text;
  return { verdict, headline, points: pts, costs: costLine(side) };
}

export const VERDICT_TEXT: Record<Verdict, string> = {
  favorable: 'Favorable',
  caution: 'Caution',
  avoid: 'Avoid',
  unknown: 'Not rated',
};

export const VERDICT_HINT =
  'A checklist for short option trades (calls when racing up, puts when racing down): spread and liquidity, IV and earnings, ' +
  'time decay, and whether the move is still going. Not advice. In backtests every radar entry lost about $170 per contract ' +
  'after the bid-ask spread on average; "Favorable" setups roughly broke even.';
