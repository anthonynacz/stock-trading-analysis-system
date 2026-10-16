// The option side a radar stock would be traded with: calls for up moves, puts for down moves.
// Falls back to the call fields of older metrics rows (written before both sides were stored).

import type { RadarDirection, RadarOptionLiquidity, RadarOptionMetrics, RadarOptionSide } from '../../types';

export type OptionKind = 'call' | 'put';

export interface SideView {
  kind: OptionKind;
  /** "calls" / "puts" for sentences. */
  plural: string;
  liquidity: RadarOptionLiquidity;
  ntmOi: number | null;
  ntmVolume: number | null;
  spreadPct: number | null;
  /** Round-trip spread cost of one contract in $ (ask - bid) x 100. */
  spreadUsd: number | null;
  spreadMovePct: number | null;
  thetaDayUsd: number | null;
  thetaPct: number | null;
  mid: number | null;
  strike: number | null;
  bid: number | null;
  ask: number | null;
  breakevenPct: number | null;
  volume: number | null;
  oi: number | null;
}

export const kindFor = (direction: RadarDirection | undefined): OptionKind => (direction === 'down' ? 'put' : 'call');

export function sideOf(o: RadarOptionMetrics | undefined, direction: RadarDirection | undefined): SideView | null {
  if (!o || !o.has_options) return null;
  const kind = kindFor(direction);
  const s: RadarOptionSide | null | undefined = o.sides?.[kind];
  if (s) {
    const a = s.atm;
    return {
      kind,
      plural: `${kind}s`,
      liquidity: s.liquidity,
      ntmOi: s.ntm_oi,
      ntmVolume: s.ntm_volume,
      spreadPct: a.spread_pct,
      spreadUsd: a.bid !== null && a.ask !== null && a.ask > 0 ? Math.round((a.ask - a.bid) * 100) : null,
      spreadMovePct: a.spread_move_pct,
      thetaDayUsd: a.theta_day_usd,
      thetaPct: a.theta_pct,
      mid: a.mid,
      strike: a.strike,
      bid: a.bid,
      ask: a.ask,
      breakevenPct: a.breakeven_pct,
      volume: a.volume,
      oi: a.oi,
    };
  }
  if (kind === 'put') return null; // older rows only had calls: no put data yet
  const a = o.atm;
  return {
    kind,
    plural: 'calls',
    liquidity: o.liquidity,
    ntmOi: o.ntm_call_oi,
    ntmVolume: o.ntm_call_volume,
    spreadPct: a?.spread_pct ?? null,
    spreadUsd: a && a.bid !== null && a.ask !== null && a.ask > 0 ? Math.round((a.ask - a.bid) * 100) : null,
    spreadMovePct: null,
    thetaDayUsd: null,
    thetaPct: null,
    mid: a?.mid ?? null,
    strike: a?.strike ?? null,
    bid: a?.bid ?? null,
    ask: a?.ask ?? null,
    breakevenPct: a?.breakeven_pct ?? null,
    volume: a?.volume ?? null,
    oi: a?.oi ?? null,
  };
}
