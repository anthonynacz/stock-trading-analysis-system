// How expensive an option is to BUY now, from its implied volatility, and how to read today's IV change for the
// trade's direction. Pure: no React.
//
// Lower IV is cheaper for a buyer of calls and puts alike. The day's IV drop ("crush") hurts contracts bought
// earlier at the higher IV; for a new entry it means the option is cheaper than this morning. Direction only
// changes how to read the change: rallies usually come with IV drifting down, selloffs with IV rising.

import type { RadarDirection, RadarOptionMetrics } from '../../types';
import { fmtShortDate } from './optionFilters';

export type IvPriceLevel = 'cheap' | 'fair' | 'rich' | 'expensive' | 'unknown';

// IV / the stock's realised volatility (20 days). Rich and term thresholds match backend/radar/options.py IV.
export const IV_CHEAP_MAX = 1.2;
export const IV_RICH = 1.5;
export const IV_EXPENSIVE = 2.0;
export const TERM_INVERTED = 1.2;

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
  rich: 'Options pricey',
  expensive: 'Options expensive',
  unknown: 'IV unknown',
};

export function ivPrice(o: RadarOptionMetrics | undefined): IvPrice {
  if (!o || !o.iv_pct) return { level: 'unknown', label: IV_PRICE_LABEL.unknown, why: 'No implied volatility quoted yet.' };
  const ratio = o.iv_hv;
  const vs =
    ratio && o.hv_pct
      ? `IV ${o.iv_pct.toFixed(0)}% against the stock's normal ${o.hv_pct.toFixed(0)}% (${ratio.toFixed(1)}×)`
      : `IV ${o.iv_pct.toFixed(0)}%`;
  let level: IvPriceLevel;
  let why: string;
  if (o.earnings_before_expiry) {
    level = 'expensive';
    why = `${vs}; earnings ${fmtShortDate(o.earnings_date)} are priced into this expiry and the premium usually collapses after the report.`;
  } else if (ratio !== null && ratio !== undefined && ratio >= IV_EXPENSIVE) {
    level = 'expensive';
    why = `${vs}: a large news or rumour premium that deflates if the story fades.`;
  } else if ((ratio ?? 0) >= IV_RICH || (o.term_ratio ?? 0) >= TERM_INVERTED) {
    level = 'rich';
    why =
      (ratio ?? 0) >= IV_RICH
        ? `${vs}: some news premium in the price.`
        : `${vs}; this week's IV is above the ~30-day IV, so an event is priced into this expiry.`;
  } else if (ratio === null || ratio === undefined || ratio > IV_CHEAP_MAX) {
    level = 'fair';
    why = `${vs}: about what the stock's own swings justify.`;
  } else {
    level = 'cheap';
    why = `${vs}: options cost little more than the stock's normal swings.`;
  }
  return { level, label: IV_PRICE_LABEL[level], why };
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
