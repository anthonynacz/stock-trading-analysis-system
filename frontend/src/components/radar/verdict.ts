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
import { ivPrice } from './ivPrice';
import { extendedText } from './extended';
import { sideOf, type SideView } from './optionSide';
import type { WatchLevel } from './exitRules';

export type Verdict = 'favorable' | 'caution' | 'avoid' | 'unknown';

export interface VerdictResult {
  verdict: Verdict;
  /** The main reason, one sentence. */
  headline: string;
  /** Every point considered, worst first; `good` points never change the verdict. */
  points: VerdictPoint[];
  /** The points not used as the headline. */
  details: VerdictPoint[];
  /** "Costs: about $76 round trip on the spread · time decay about $18/day" when known. */
  costs: string | null;
}

export interface VerdictPoint {
  level: 'avoid' | 'caution' | 'good';
  text: string;
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
  /** Far beyond VWAP and still accelerating in the move's direction (extended.ts): don't chase. */
  extended?: boolean;
  nowMs: number;
}

export const SPREAD_OK = 10;      // % of mid
export const SPREAD_MAX = 15;

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
    return { verdict: 'unknown', headline: 'Option data arrives after the next scan.', points: [], details: [], costs: null };
  }
  if (!o.has_options) {
    const none: VerdictPoint = { level: 'avoid', text: 'No listed options.' };
    return { verdict: 'avoid', headline: 'No listed options on this stock.', points: [none], details: [], costs: null };
  }
  if (!side) {
    return { verdict: 'unknown', headline: 'Put data arrives after the next scan.', points: [], details: [], costs: null };
  }
  const pts: VerdictPoint[] = [];
  const add = (level: VerdictPoint['level'], text: string) => pts.push({ level, text });
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

  // 2. Implied volatility: what the option costs to buy NOW (low IV is cheaper, for calls and puts alike).
  // A crush earlier today hurt contracts bought before it; for a new entry it only matters if IV is still rich.
  const price = ivPrice(o);
  const crushing = o.iv_risk?.level === 'crushing';
  const times = o.iv_hv ? ` ${o.iv_hv.toFixed(1)}× the stock's normal` : ' above normal';
  if (o.earnings_before_expiry) add('caution', `Earnings ${fmtShortDate(o.earnings_date)} before this expiry: close the trade the same day; holding through the report usually loses a big part of the price.`);
  else if (price.level === 'expensive' || price.level === 'rich') {
    if (crushing) add('caution', `IV is falling and still${times}: the premium has room to deflate further.`);
    else add('caution', `Options are ${price.level === 'expensive' ? 'expensive' : 'pricey'} (IV${times}): if the story fades, the premium deflates.`);
  } else if (crushing) add('good', 'IV already reset today: options are cheaper now than this morning.');
  else if (price.level === 'cheap') add('good', "Options are cheap: IV is close to the stock's normal swings.");
  if (o.iv_risk?.reasons?.some((r) => r.startsWith('Reported earnings'))) add('caution', 'Earnings were just reported: moves can reverse sharply.');

  // 3. Momentum and timing. A blow-off shape comes first: it is the reason not to enter right now.
  const extText = extendedText(ctx.direction);
  if (ctx.extended) add('avoid', extText);
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

  const RANK = { avoid: 0, caution: 1, good: 2 } as const;
  pts.sort((a, b) => RANK[a.level] - RANK[b.level] || Number(b.text === extText) - Number(a.text === extText));
  const verdict: Verdict = pts.some((p) => p.level === 'avoid')
    ? 'avoid'
    : pts.some((p) => p.level === 'caution')
      ? 'caution'
      : 'favorable';
  if (verdict === 'favorable') {
    const liq = side.liquidity === 'good' ? 'Liquid' : 'Tradable';
    const iv = price.level === 'cheap' ? ', cheap options' : price.level === 'fair' ? ', fairly priced options' : '';
    return { verdict, headline: `${liq} ${what}${iv} and the move is still going.`, points: pts, details: pts, costs: costLine(side) };
  }
  return { verdict, headline: pts[0].text, points: pts, details: pts.slice(1), costs: costLine(side) };
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
