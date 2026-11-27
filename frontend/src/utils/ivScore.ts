// The IV cost score (backend/utils/iv_score.py) as text: labels, bands and the one-line summary. Pure: no React.

import type { IvCostLabel, IvScore } from '../types';

export const IV_COST_TEXT: Record<IvCostLabel, string> = {
  cheap: 'Cheap',
  fair: 'Fair',
  pricey: 'Pricey',
  expensive: 'Expensive',
};

/** Score bands, mirroring LABELS in backend/utils/iv_score.py. */
export const IV_COST_BANDS: { label: IvCostLabel; range: string }[] = [
  { label: 'cheap', range: 'under 35' },
  { label: 'fair', range: '35–59' },
  { label: 'pricey', range: '60–79' },
  { label: 'expensive', range: '80 and up' },
];

export const UNKNOWN_HINT = 'No clean IV reading yet: it needs live two-sided option quotes (market hours).';

export function costLabelFor(score: number): IvCostLabel {
  if (score < 35) return 'cheap';
  if (score < 60) return 'fair';
  if (score < 80) return 'pricey';
  return 'expensive';
}

const pct = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? '—' : `${v.toFixed(d)}%`);

/** One plain sentence: where IV sits against the stock's movement and against its own history. */
export function ivScoreSummary(s: IvScore): string {
  const bits: string[] = [];
  if (s.hv20_pct && s.iv_hv) {
    bits.push(`30-day IV ${pct(s.iv30_pct)} against the stock's recent ${pct(s.hv20_pct)} movement (${s.iv_hv.toFixed(1)}×)`);
  } else {
    bits.push(`30-day IV ${pct(s.iv30_pct)}`);
  }
  const h = s.history;
  if (h) {
    bits.push(`at or above ${h.percentile.toFixed(0)}% of the last ${h.window_days} days (median ${pct(h.median_pct, 1)})`);
  }
  return `${bits.join('; ')}.`;
}
