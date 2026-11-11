// The Movers view of the radar page: plain percentage moves since the prior close (backend/radar/movers.py),
// with stock-level filters and sorting. Pure: no React.

import type { RadarMover, RadarOptionLiquidity, RadarOptionMetrics } from '../../types';
import { sideOf } from './optionSide';

export type MoverDir = 'all' | 'up' | 'down';

export interface MoverFilters {
  dir: MoverDir;
  /** Minimum absolute move today in % (0 = any; the list itself starts at 2%). */
  minMovePct: number;
  /** Price range in $ (0 = no limit). */
  minPrice: number;
  maxPrice: number;
  /** Sectors shown (empty = all). */
  sectors: string[];
  /** Dollar volume traded so far today, in $ millions (0 = any). */
  minDollarVolM: number;
  /** Today's volume against normal by this time of day (0 = any). */
  minRvol: number;
  /** Market cap in $ billions (0 = any). */
  minMcapB: number;
}

export const NO_MOVER_FILTERS: MoverFilters = {
  dir: 'all',
  minMovePct: 0,
  minPrice: 0,
  maxPrice: 0,
  sectors: [],
  minDollarVolM: 0,
  minRvol: 0,
  minMcapB: 0,
};

export const MOVE_CHOICES = [0, 3, 5, 8, 10, 15, 20];
export const MIN_PRICE_CHOICES = [0, 5, 10, 20, 50, 100];
export const MAX_PRICE_CHOICES = [0, 20, 50, 100, 200, 500];
export const DOLLAR_VOL_CHOICES = [0, 10, 50, 100, 500, 1000];
export const RVOL_CHOICES = [0, 1, 1.5, 2, 3, 5];
export const MCAP_CHOICES = [0, 2, 10, 50, 200];

/** How many settings differ from "no filters" (for the button badge). */
export function moverFilterCount(f: MoverFilters): number {
  return (
    Number(f.dir !== 'all') +
    Number(f.minMovePct > 0) +
    Number(f.minPrice > 0) +
    Number(f.maxPrice > 0) +
    Number(f.sectors.length > 0) +
    Number(f.minDollarVolM > 0) +
    Number(f.minRvol > 0) +
    Number(f.minMcapB > 0)
  );
}

/** Whether a mover passes the stock-level filters. A filter on a value the mover lacks hides it. */
export function moverPasses(f: MoverFilters, m: RadarMover): boolean {
  if (f.dir !== 'all' && m.direction !== f.dir) return false;
  if (f.minMovePct > 0 && Math.abs(m.change_pct) < f.minMovePct) return false;
  if (f.minPrice > 0 && m.price < f.minPrice) return false;
  if (f.maxPrice > 0 && m.price > f.maxPrice) return false;
  if (f.sectors.length > 0 && !f.sectors.includes(m.sector)) return false;
  if (f.minDollarVolM > 0 && (m.dollar_volume ?? 0) < f.minDollarVolM * 1e6) return false;
  if (f.minRvol > 0 && (m.rvol ?? 0) < f.minRvol) return false;
  if (f.minMcapB > 0 && (m.market_cap ?? 0) < f.minMcapB * 1e9) return false;
  return true;
}

/** Restore saved filters, keeping only known keys with the right types. */
export function readMoverFilters(raw: unknown): MoverFilters {
  const out: MoverFilters = { ...NO_MOVER_FILTERS, sectors: [] };
  if (!raw || typeof raw !== 'object') return out;
  const r = raw as Record<string, unknown>;
  for (const k of ['minMovePct', 'minPrice', 'maxPrice', 'minDollarVolM', 'minRvol', 'minMcapB'] as const) {
    const v = r[k];
    if (typeof v === 'number' && Number.isFinite(v) && v >= 0) out[k] = v;
  }
  if (r.dir === 'up' || r.dir === 'down') out.dir = r.dir;
  if (Array.isArray(r.sectors)) out.sectors = r.sectors.filter((s): s is string => typeof s === 'string');
  return out;
}

// ── Sorting ───────────────────────────────────────────────────────────────

export type MoverSortKey = 'liquidity' | 'move' | 'dollar' | 'rvol' | 'price' | 'ticker';

export interface MoverSort {
  key: MoverSortKey;
  /** Ascending (default is the natural "most first" order of the key). */
  asc: boolean;
}

export const DEFAULT_MOVER_SORT: MoverSort = { key: 'liquidity', asc: false };

export const MOVER_SORTS: { key: MoverSortKey; label: string }[] = [
  { key: 'liquidity', label: 'Option liquidity, then $ volume' },
  { key: 'move', label: 'Biggest move' },
  { key: 'dollar', label: '$ volume today' },
  { key: 'rvol', label: 'Volume vs normal' },
  { key: 'price', label: 'Price' },
  { key: 'ticker', label: 'Ticker' },
];

const LIQ_RANK: Record<RadarOptionLiquidity, number> = { none: 0, thin: 1, fair: 2, good: 3 };

/**
 * Liquidity of the options the move is traded with (calls up, puts down): 3 good, 2 fair, 1 thin, 0 no
 * options, -1 not read yet.
 */
export function liquidityRank(m: RadarMover, o: RadarOptionMetrics | undefined): number {
  if (!o) return -1;
  if (!o.has_options) return 0;
  const side = sideOf(o, m.direction);
  return side ? LIQ_RANK[side.liquidity] : -1;
}

const num = (v: number | null | undefined) => (v === null || v === undefined || Number.isNaN(v) ? -Infinity : v);

export function sortMovers(
  list: RadarMover[],
  sort: MoverSort,
  options: Record<string, RadarOptionMetrics> | undefined,
): RadarMover[] {
  const dollars = (m: RadarMover) => num(m.dollar_volume);
  const cmp: Record<MoverSortKey, (a: RadarMover, b: RadarMover) => number> = {
    // Best option liquidity first; inside a grade the stock trading the most dollars today leads.
    liquidity: (a, b) =>
      liquidityRank(b, options?.[b.ticker]) - liquidityRank(a, options?.[a.ticker]) || dollars(b) - dollars(a),
    move: (a, b) => Math.abs(b.change_pct) - Math.abs(a.change_pct),
    dollar: (a, b) => dollars(b) - dollars(a),
    rvol: (a, b) => num(b.rvol) - num(a.rvol),
    price: (a, b) => b.price - a.price,
    ticker: (a, b) => b.ticker.localeCompare(a.ticker),
  };
  const by = cmp[sort.key];
  const out = [...list].sort((a, b) => by(a, b) || Math.abs(b.change_pct) - Math.abs(a.change_pct) || a.ticker.localeCompare(b.ticker));
  return sort.asc ? out.reverse() : out;
}

export function readMoverSort(raw: unknown): MoverSort {
  if (!raw || typeof raw !== 'object') return DEFAULT_MOVER_SORT;
  const r = raw as Record<string, unknown>;
  const key = MOVER_SORTS.find((s) => s.key === r.key)?.key;
  return key ? { key, asc: r.asc === true } : DEFAULT_MOVER_SORT;
}

/** Sectors present in the list, most names first. */
export function moverSectors(list: RadarMover[]): { sector: string; n: number }[] {
  const n = new Map<string, number>();
  for (const m of list) n.set(m.sector, (n.get(m.sector) ?? 0) + 1);
  return [...n.entries()].map(([sector, count]) => ({ sector, n: count })).sort((a, b) => b.n - a.n || a.sector.localeCompare(b.sector));
}
