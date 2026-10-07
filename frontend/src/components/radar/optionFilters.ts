// Call-option filters for the radar lists (stock liquidity, option liquidity,
// option cost, event risk). Pure: no React. The metrics come from
// backend/radar/options.py via GET /api/radar `options`.

import type { RadarOptionLiquidity, RadarOptionMetrics } from '../../types';

export interface OptionFilters {
  /** Typical daily dollar volume of the stock, in $ millions (0 = any). */
  minAdvM: number;
  /** Minimum stock price in $ (0 = any). */
  minPrice: number;
  /** Minimum call liquidity grade. */
  liquidity: 'any' | 'fair' | 'good';
  /** Near-the-money call open interest (0 = any). */
  minNtmOi: number;
  /** Maximum at-the-money call bid-ask spread in % of mid (0 = any). */
  maxSpreadPct: number;
  /** Maximum implied / realised volatility ratio (0 = any). */
  maxIvHv: number;
  /** Hide names with earnings on or before the option's expiry. */
  noEarnings: boolean;
  /** Only names with weekly options. */
  weekliesOnly: boolean;
}

export const NO_FILTERS: OptionFilters = {
  minAdvM: 0,
  minPrice: 0,
  liquidity: 'any',
  minNtmOi: 0,
  maxSpreadPct: 0,
  maxIvHv: 0,
  noEarnings: false,
  weekliesOnly: false,
};

/** "Call-friendly": liquid stock, tradable calls, premium not inflated, no earnings in the window. */
export const CALL_PRESET: OptionFilters = {
  minAdvM: 20,
  minPrice: 5,
  liquidity: 'fair',
  minNtmOi: 500,
  maxSpreadPct: 15,
  maxIvHv: 2,
  noEarnings: true,
  weekliesOnly: false,
};

export const ADV_CHOICES = [0, 10, 20, 50, 100, 500];
export const PRICE_CHOICES = [0, 2, 5, 10, 20];
export const OI_CHOICES = [0, 100, 500, 1000, 5000];
export const SPREAD_CHOICES = [0, 5, 10, 15, 25];
export const IVHV_CHOICES = [0, 1.25, 1.5, 2, 3];

const LIQ_RANK: Record<RadarOptionLiquidity, number> = { none: 0, thin: 1, fair: 2, good: 3 };

const sameFilters = (a: OptionFilters, b: OptionFilters) =>
  (Object.keys(a) as (keyof OptionFilters)[]).every((k) => a[k] === b[k]);

export const filtersActive = (f: OptionFilters) => !sameFilters(f, NO_FILTERS);
export const isCallPreset = (f: OptionFilters) => sameFilters(f, CALL_PRESET);

/** How many settings differ from "no filters" (for the button badge). */
export const activeCount = (f: OptionFilters) =>
  (Object.keys(f) as (keyof OptionFilters)[]).filter((k) => f[k] !== NO_FILTERS[k]).length;

/** Filters that need option metrics (a name without them fails these). */
const needsOptions = (f: OptionFilters) =>
  f.liquidity !== 'any' || f.minNtmOi > 0 || f.maxSpreadPct > 0 || f.maxIvHv > 0 || f.noEarnings || f.weekliesOnly;

/**
 * Why a name fails the filters (first failing check), or null when it passes.
 * `price` is the radar's last price, used when the metrics are missing.
 */
export function failReason(f: OptionFilters, o: RadarOptionMetrics | undefined, price: number | null): string | null {
  const px = o?.price ?? price;
  if (f.minPrice > 0 && (px === null || px === undefined || px < f.minPrice)) return `price under $${f.minPrice}`;
  if (f.minAdvM > 0) {
    if (!o || o.adv_usd === null) return 'no dollar-volume data yet';
    if (o.adv_usd < f.minAdvM * 1e6) return `trades under $${f.minAdvM}M a day`;
  }
  if (!needsOptions(f)) return null;
  if (!o) return 'no option data yet';
  if (!o.has_options) return 'no listed options';
  if (f.liquidity !== 'any' && LIQ_RANK[o.liquidity] < LIQ_RANK[f.liquidity]) return `${o.liquidity} call liquidity`;
  if (f.minNtmOi > 0 && (o.ntm_call_oi ?? 0) < f.minNtmOi) return `call open interest under ${f.minNtmOi}`;
  if (f.maxSpreadPct > 0) {
    const sp = o.atm?.spread_pct;
    if (sp === null || sp === undefined) return 'no live call quote';
    if (sp > f.maxSpreadPct) return `call spread over ${f.maxSpreadPct}%`;
  }
  if (f.maxIvHv > 0 && o.iv_hv !== null && o.iv_hv > f.maxIvHv) return `IV over ${f.maxIvHv}× realised`;
  if (f.noEarnings && o.earnings_before_expiry) return 'earnings before expiry';
  if (f.weekliesOnly && !o.weeklies) return 'no weekly options';
  return null;
}

/** Restore saved filters, keeping only known keys with the right types. */
export function readFilters(raw: unknown): OptionFilters {
  const out: OptionFilters = { ...NO_FILTERS };
  if (!raw || typeof raw !== 'object') return out;
  const r = raw as Record<string, unknown>;
  for (const k of Object.keys(NO_FILTERS) as (keyof OptionFilters)[]) {
    const v = r[k];
    if (typeof v === typeof NO_FILTERS[k]) (out as unknown as Record<string, unknown>)[k] = v;
  }
  if (!['any', 'fair', 'good'].includes(out.liquidity)) out.liquidity = 'any';
  return out;
}

// ── Formatting ────────────────────────────────────────────────────────────

export function fmtCompact(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
  return `${Math.round(v)}`;
}

export const fmtUsdCompact = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `$${fmtCompact(v)}`);

/** "Oct 16" from "2026-10-16". */
export function fmtShortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
}

export const LIQ_TEXT: Record<RadarOptionLiquidity, string> = {
  good: 'Liquid calls',
  fair: 'Tradable calls',
  thin: 'Thin calls',
  none: 'No options',
};

export const LIQ_HINT: Record<RadarOptionLiquidity, string> = {
  good: 'Near-the-money calls: open interest 2,000+, 500+ traded today, at-the-money spread ≤ 10%.',
  fair: 'Near-the-money calls: open interest 300+, at-the-money spread ≤ 25%.',
  thin: 'Few near-the-money calls open or a wide bid-ask spread: fills can be costly.',
  none: 'No listed options found for this stock.',
};

// Literal class maps (Tailwind purge).
export const LIQ_CLASS: Record<RadarOptionLiquidity, string> = {
  good: 'bg-green-900/40 text-green-300 border-green-500/30',
  fair: 'bg-sky-900/40 text-sky-300 border-sky-500/30',
  thin: 'bg-amber-900/40 text-amber-300 border-amber-500/30',
  none: 'bg-gray-800 text-text-secondary border-border',
};
