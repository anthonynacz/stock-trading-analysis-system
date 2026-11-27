// Pure helpers for the Quant page: reading labels / colours / plain explanations, IV source wording, flags,
// sorting and filtering. No React. Thresholds mirror backend/quant/config.py READING; update both together.

import type { QuantVolFields, QuantVolRow } from '../../types';
import { fmtPct } from '../../utils/format';

export const READING_CFG = { richPctl: 0.8, cheapPctl: 0.2, ownMinObs: 60, xsMinNames: 30 } as const;
export const HORIZON_SESSIONS = 21;

/** Annualised fraction -> "28.4%". */
export const fmtVol = (v: number | null | undefined, digits = 1) => fmtPct(v, digits, { fraction: true });
export const fmtRatio = (v: number | null | undefined) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(2)}×`;

export type ReadingKey = 'cheap' | 'fair' | 'rich' | 'not comparable' | 'insufficient history' | 'no implied vol';
export const READING_ORDER: ReadingKey[] = [
  'cheap',
  'fair',
  'rich',
  'not comparable',
  'insufficient history',
  'no implied vol',
];

interface ReadingMeta {
  label: string;
  /** Badge classes: coloured by what the reading means for someone BUYING options now. */
  badge: string;
  /** Neutral readings (no comparison possible) look the same and never green / amber. */
  neutral: boolean;
  short: string;
}

// Literal class maps (Tailwind purge). Same tones as the radar IV badge (OptionInfo.tsx PRICE_CLASS).
const NEUTRAL_BADGE = 'bg-gray-500/10 text-text-secondary border-border border-dashed';
export const READING_META: Record<ReadingKey, ReadingMeta> = {
  cheap: {
    label: 'Cheap',
    badge: 'bg-emerald-500/15 text-emerald-200 border-emerald-400/50',
    neutral: false,
    short: 'Options cheaper than usual against the forecast: better for a buyer.',
  },
  fair: {
    label: 'Fair',
    badge: 'bg-gray-500/10 text-text-primary border-border',
    neutral: false,
    short: 'Options priced about as usual against the forecast.',
  },
  rich: {
    label: 'Rich',
    badge: 'bg-amber-500/15 text-amber-200 border-amber-400/50',
    neutral: false,
    short: 'Options pricier than usual against the forecast: worse for a buyer.',
  },
  'not comparable': {
    label: 'Not comparable',
    badge: NEUTRAL_BADGE,
    neutral: true,
    short: 'Earnings fall inside the next 21 sessions; the expected earnings jump is priced into the options.',
  },
  'insufficient history': {
    label: 'Not enough history',
    badge: NEUTRAL_BADGE,
    neutral: true,
    short: 'Too few earlier readings to say what is usual for this name yet.',
  },
  'no implied vol': {
    label: 'No option price',
    badge: NEUTRAL_BADGE,
    neutral: true,
    short: 'No implied volatility was available for this name today.',
  },
};

export const readingKey = (r: string | null | undefined): ReadingKey =>
  (READING_ORDER as string[]).includes(r ?? '') ? (r as ReadingKey) : 'no implied vol';

const ordinal = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`;
};

/** One or two plain sentences on why the row reads the way it does (tooltip + detail panel). */
export function readingWhy(row: QuantVolFields): string {
  const key = readingKey(row.reading);
  const ratio = fmtRatio(row.iv_to_forecast);
  if (key === 'not comparable') {
    const when = row.earnings_date ? ` (earnings ${fmtDate(row.earnings_date)})` : '';
    return `Earnings fall inside the next ${HORIZON_SESSIONS} sessions${when}. Option prices then include the expected earnings jump, so comparing them with a normal-day forecast says nothing. No reading.`;
  }
  if (key === 'insufficient history') {
    return `Implied vol is ${ratio} the forecast, but there is nothing reliable to compare that with yet: the reading needs ${READING_CFG.ownMinObs} earlier readings of this ticker, or ${READING_CFG.xsMinNames} names with a 30-day implied vol today. It fills in as daily data accumulates.`;
  }
  if (key === 'no implied vol') {
    return 'No implied volatility (option price) was available for this name today, so there is nothing to compare the forecast with.';
  }
  const pct = row.ratio_pctl !== null && row.ratio_pctl !== undefined ? Math.round(row.ratio_pctl * 100) : null;
  const ref =
    row.reading_basis === 'own_history'
      ? "this ticker's own earlier readings (past year, earnings excluded)"
      : "today's other names with a 30-day implied vol";
  const where = pct !== null ? `at the ${ordinal(pct)} percentile of ${ref}` : `compared with ${ref}`;
  const lead = `Implied vol is ${ratio} the forecast, ${where}.`;
  if (key === 'cheap') return `${lead} Options cost less than usual for the volatility expected. Lower is better for a buyer.`;
  if (key === 'rich') return `${lead} Options cost more than usual for the volatility expected. Worse for a buyer, better for a seller.`;
  return `${lead} About the usual price.`;
}

/** Plain words for the IV source when it is not the 30-day chain value; null for chain_cm30. */
export function ivSourceNote(src: string | null | undefined): string | null {
  if (!src || src === 'chain_cm30') return null;
  if (src === 'radar_iv30') return 'From the radar’s option data (about 30 days), not our own chain snapshot: a weaker comparison.';
  if (src === 'snapshot_iv30')
    return 'From the daily 30-day IV reading behind the IV cost score (a different method from our chain value): a weaker comparison.';
  if (src === 'options_snapshot_atm')
    return 'From the nearest expiry only, not 30 days: short-dated options swing more, so this is a weak comparison.';
  return `Source: ${src} (a weaker comparison).`;
}

export const IV_SOURCE_SHORT: Record<string, string> = {
  chain_cm30: '30-day, from option chain',
  radar_iv30: 'radar ~30-day',
  snapshot_iv30: 'daily 30-day reading',
  options_snapshot_atm: 'nearest expiry',
};

const FLAG_TEXT: Record<string, string> = {
  earnings_in_horizon: 'Earnings fall inside the forecast window.',
  earnings_unknown: 'No earnings date on file: an earnings jump could be hiding in the option price.',
  iv_fallback: 'Implied vol is from a fallback source, not our 30-day chain value.',
  iv_not_30d: 'Implied vol is from the nearest expiry, not 30 days.',
  iv_extrapolated: 'No expiries on both sides of 30 days: the 30-day implied vol is taken flat from the nearest one.',
  pooled_fit: 'Too little history for its own model: uses the model fitted across all tickers.',
};

export const flagText = (f: string) => FLAG_TEXT[f] ?? f.replace(/_/g, ' ');

/** "Nov 14, 2026" for an ISO date / datetime; dates without time are read as calendar dates (no TZ shift). */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T12:00:00`) : new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/* ── table sort / filter ─────────────────────────────────────────────────── */

export type SortKey =
  | 'ticker'
  | 'price'
  | 'forecast_vol'
  | 'realized_vol_21d'
  | 'implied_vol_30d'
  | 'iv_to_forecast'
  | 'reading'
  | 'earnings';

export interface SortState {
  key: SortKey;
  dir: 'asc' | 'desc';
}

// cheap -> rich, then the neutral readings: sorting by reading groups buyer-friendly names first.
const READING_RANK: Record<ReadingKey, number> = {
  cheap: 0,
  fair: 1,
  rich: 2,
  'not comparable': 3,
  'insufficient history': 4,
  'no implied vol': 5,
};

function sortValue(r: QuantVolRow, key: SortKey): number | string | null {
  switch (key) {
    case 'ticker':
      return r.ticker;
    case 'reading':
      // Within a reading, a lower percentile sorts first (cheaper).
      return READING_RANK[readingKey(r.reading)] + (r.ratio_pctl ?? 0.5) / 10;
    case 'earnings':
      return r.earnings_in_horizon ? 0 : 1;
    default:
      return r[key];
  }
}

export function sortRows(rows: QuantVolRow[], s: SortState): QuantVolRow[] {
  const sign = s.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = sortValue(a, s.key);
    const vb = sortValue(b, s.key);
    // Missing values always last, whatever the direction.
    if (va === null || va === undefined) return vb === null || vb === undefined ? a.ticker.localeCompare(b.ticker) : 1;
    if (vb === null || vb === undefined) return -1;
    const c = typeof va === 'string' ? va.localeCompare(String(vb)) : (va as number) - (vb as number);
    return c !== 0 ? c * sign : a.ticker.localeCompare(b.ticker);
  });
}

export interface Filters {
  q: string;
  sector: string; // '' = all
  reading: ReadingKey | ''; // '' = all
}

export const sectorOf = (r: QuantVolRow) => r.sector ?? 'Other';

export function filterRows(rows: QuantVolRow[], f: Filters, skip?: 'sector' | 'reading'): QuantVolRow[] {
  const q = f.q.trim().toUpperCase();
  return rows.filter(
    (r) =>
      (!q || r.ticker.includes(q)) &&
      (skip === 'sector' || !f.sector || sectorOf(r) === f.sector) &&
      (skip === 'reading' || !f.reading || readingKey(r.reading) === f.reading),
  );
}

export function countBy<T extends string>(rows: QuantVolRow[], key: (r: QuantVolRow) => T): Map<T, number> {
  const m = new Map<T, number>();
  for (const r of rows) m.set(key(r), (m.get(key(r)) ?? 0) + 1);
  return m;
}
