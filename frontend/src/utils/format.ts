// Pure number/currency/percent formatters shared across pages. No React.

export const DASH = '—';

type Num = number | null | undefined;

const isMissing = (v: Num): v is null | undefined =>
  v === null || v === undefined || Number.isNaN(v);

export function fmtNum(v: Num, digits = 2, fallback = DASH): string {
  return isMissing(v) ? fallback : v.toFixed(digits);
}

export function fmtInt(v: Num, fallback = DASH): string {
  return isMissing(v) ? fallback : Math.round(v).toLocaleString('en-US');
}

/** `$1,234.56` — thousands separators, fixed decimals. */
export function fmtPrice(v: Num, digits = 2, fallback = DASH): string {
  if (isMissing(v)) return fallback;
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** Whole-dollar strikes render without decimals (`$150`), fractional ones as prices (`$152.50`). */
export function fmtStrike(v: Num, fallback = DASH): string {
  if (isMissing(v)) return fallback;
  return Number.isInteger(v) ? `$${v.toLocaleString('en-US')}` : fmtPrice(v);
}

/** PositionsPage.formatCurrency semantics (2dp, en-US grouping, negatives as `$-12.00`). */
export function fmtCurrency(v: Num): string {
  return fmtPrice(v);
}

/**
 * Explicit sign prefix: `+12`, `-3.4%`. Zero renders as `+0` — this matches
 * the `v >= 0 ? '+' : ''` convention used by most existing sites.
 */
export function fmtSigned(v: Num, digits = 0, suffix = '', fallback = DASH): string {
  if (isMissing(v)) return fallback;
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}${suffix}`;
}

/**
 * Percent display. By default `v` is already a percent (12.3 → `12.3%`).
 * Pass `{ fraction: true }` when `v` is a ratio (0.123 → `12.3%`) — Scanner
 * and Performance data arrive as fractions; Options Lab data as percents.
 */
export function fmtPct(
  v: Num,
  digits = 1,
  { fraction = false, fallback = DASH }: { fraction?: boolean; fallback?: string } = {},
): string {
  if (isMissing(v)) return fallback;
  return `${(fraction ? v * 100 : v).toFixed(digits)}%`;
}

/** `$1.23T` / `$45.6B` / `$789M`; non-positive or non-finite values render as DASH. */
export function fmtMarketCap(v: Num): string {
  if (isMissing(v) || !Number.isFinite(v) || v <= 0) return DASH;
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString('en-US')}`;
}

/** Option delta; 2dp by default (StrikeRecommender may pass 3). */
export function fmtDelta(v: Num, digits = 2, fallback = DASH): string {
  return fmtNum(v, digits, fallback);
}
