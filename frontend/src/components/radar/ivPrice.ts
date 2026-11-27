// How expensive an option is to BUY now, from its implied volatility, and how to read today's IV change for the
// trade's direction. Pure: no React.
//
// Lower IV is cheaper for a buyer of calls and puts alike. The day's IV drop ("crush") hurts contracts bought
// earlier at the higher IV; for a new entry it means the option is cheaper than this morning. Direction only
// changes how to read the change: rallies usually come with IV drifting down, selloffs with IV rising.

import type { IvCostLabel, RadarDirection, RadarOptionMetrics } from '../../types';
import { UNKNOWN_HINT, ivScoreSummary } from '../../utils/ivScore';

/** The IV cost score's label (backend/utils/iv_score.py), or unknown without a clean IV reading. */
export type IvPriceLevel = IvCostLabel | 'unknown';

export interface IvPrice {
  level: IvPriceLevel;
  /** "Options cheap", "Options pricey"... */
  label: string;
  /** One plain sentence on why. */
  why: string;
}

export const IV_PRICE_LABEL: Record<IvPriceLevel, string> = {
  cheap: 'Options cheap',
  fair: 'Options fairly priced',
  pricey: 'Options pricey',
  expensive: 'Options expensive',
  unknown: 'IV unknown',
};

/** What the option costs to buy now, from the IV cost score (IV against its own history and realised vol). */
export function ivPrice(o: RadarOptionMetrics | undefined): IvPrice {
  const s = o?.iv_score;
  if (!s) return { level: 'unknown', label: IV_PRICE_LABEL.unknown, why: UNKNOWN_HINT };
  const why = `IV cost ${s.score}/100: ${ivScoreSummary(s)}${s.event ? ` ${s.event.note}` : ''}`;
  return { level: s.label, label: IV_PRICE_LABEL[s.label], why };
}

/** IV moved at least this much (% of its level) since the day's first reading: worth a note. */
export const IV_CHANGE_NOTE = 3;

/** Today's IV change read for the trade's direction, or null when IV barely moved. */
export function ivChangeNote(o: RadarOptionMetrics | undefined, direction?: RadarDirection): string | null {
  const chg = o?.iv_change_pct;
  if (!o || chg === null || chg === undefined || Math.abs(chg) < IV_CHANGE_NOTE) return null;
  const pct = `${Math.abs(chg).toFixed(0)}%`;
  const holderUsd =
    o.vega_per_pt_usd && o.iv_change_pts ? Math.round(Math.abs(o.iv_change_pts) * o.vega_per_pt_usd) : null;
  const holders = holderUsd ? ` (about $${holderUsd} per contract)` : '';
  if (chg < 0) {
    const context =
      direction === 'up'
        ? 'common while a stock rallies'
        : direction === 'down'
          ? 'unusual in a selloff: fear is easing'
          : 'the premium is deflating';
    return `IV down ${pct} since the morning, ${context}. New options are cheaper now; contracts bought earlier lost value from it${holders}.`;
  }
  const context =
    direction === 'up'
      ? 'unusual on a rally: buyers are paying up for calls'
      : direction === 'down'
        ? 'normal in a selloff'
        : 'the premium is growing';
  return `IV up ${pct} since the morning, ${context}. New options cost more now; contracts bought earlier gained from it${holders}.`;
}
