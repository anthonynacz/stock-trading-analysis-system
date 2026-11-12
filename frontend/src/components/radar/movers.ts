// The Movers view of the radar page: plain percentage moves since the prior close (backend/radar/movers.py),
// with stock-level filters and sorting. Pure: no React.

import type { RadarDirection, RadarMover, RadarOptionLiquidity, RadarOptionMetrics } from '../../types';
import { sideOf } from './optionSide';

export type MoverDir = 'all' | 'up' | 'down';

// ── Period, option side and setups ────────────────────────────────────────

/** The move the list is ranked and filtered on: today, or the last N sessions with today as the last one. */
export type MoverPeriod = 'd1' | 'd3' | 'd5' | 'd10' | 'd20';

export const MOVER_PERIODS: { key: MoverPeriod; label: string; short: string }[] = [
  { key: 'd1', label: 'Today', short: 'Today' },
  { key: 'd3', label: '3 days', short: '3d' },
  { key: 'd5', label: '5 days', short: '5d' },
  { key: 'd10', label: '10 days', short: '10d' },
  { key: 'd20', label: '20 days', short: '20d' },
];

/** Smallest move listed for a period, in % (the backend keeps moves from these sizes up). */
export const PERIOD_FLOOR: Record<MoverPeriod, number> = { d1: 2, d3: 5, d5: 5, d10: 5, d20: 5 };

export function moveOf(m: RadarMover, period: MoverPeriod): number | null {
  const v = { d1: m.change_pct, d3: m.chg_3d, d5: m.chg_5d, d10: m.chg_10d, d20: m.chg_20d }[period];
  return v === null || v === undefined || Number.isNaN(v) ? null : v;
}

/** Which options a row is judged on: the side of the move (calls up, puts down), or always calls / puts. */
export type MoverSide = 'follow' | 'call' | 'put';

export type MoverSetup = NonNullable<RadarMover['setup']>;

/** Multi-day patterns. The numbers are from a daily-bar study (536 stocks, 156 sessions, Mar-Nov 2026). */
export const SETUP_META: Record<MoverSetup, { label: string; chip: string; hint: string; cls: string }> = {
  dip_uptrend: {
    label: 'Dip in uptrend',
    chip: 'Dips in an uptrend',
    hint:
      'Down 8% or more over the last 5 sessions and still above its 50-day average. In a backtest of 2026 such ' +
      'stocks beat the market by 1.6% on average over the next 5 sessions and were right 54% of the time (48% for ' +
      'any stock on any day). A pattern, not a forecast: nearly half still lost.',
    cls: 'border-emerald-400/70 bg-emerald-500/20 text-emerald-200',
  },
  falling: {
    label: 'Falling',
    chip: 'Falling',
    hint:
      'Down 8% or more over the last 5 sessions and below its 50-day average. In the same backtest the bounce was ' +
      'less than half as strong (+0.7% over 5 sessions, right 51% of the time).',
    cls: 'border-amber-400/60 bg-amber-500/15 text-amber-200',
  },
  stretched: {
    label: 'Stretched',
    chip: 'Stretched',
    hint:
      'Up 15% or more over the last 5 sessions. No edge in the backtest (right 45% of the time over the next 5 ' +
      'sessions); in the more recent half of the sample these gave back 0.8% on average.',
    cls: 'border-orange-400/60 bg-orange-500/15 text-orange-200',
  },
};

export const SETUP_KEYS = Object.keys(SETUP_META) as MoverSetup[];

/** A mover as the view shows it: `change_pct` is the selected period's move, `direction` the option side judged. */
export interface MoverRow extends RadarMover {
  /** Today's move, whatever the period. */
  today_pct: number;
}

/**
 * The list for a period: each row's `change_pct` becomes that period's move (rows without one are left out)
 * and `direction` the side its options are judged on (`follow`: calls for an up move, puts for a down move).
 */
export function moverRows(list: RadarMover[], period: MoverPeriod, side: MoverSide): MoverRow[] {
  const out: MoverRow[] = [];
  for (const m of list) {
    const move = moveOf(m, period);
    if (move === null) continue;
    const direction: RadarDirection = side === 'call' ? 'up' : side === 'put' ? 'down' : move >= 0 ? 'up' : 'down';
    out.push({ ...m, change_pct: move, direction, today_pct: m.change_pct });
  }
  return out;
}

export interface MoverFilters {
  /** The move ranked and filtered on. */
  period: MoverPeriod;
  /** Only rows with this multi-day pattern ('all' = any). */
  setup: 'all' | MoverSetup;
  side: MoverSide;
  /** Show the multi-day columns on every row. */
  multi: boolean;
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
  period: 'd1',
  setup: 'all',
  side: 'follow',
  multi: false,
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
    Number(f.setup !== 'all') +
    Number(f.side !== 'follow') +
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
  if (f.setup !== 'all' && m.setup !== f.setup) return false;
  // Direction of the move itself (the row's `direction` may be a forced option side).
  if (f.dir !== 'all' && (m.change_pct >= 0 ? 'up' : 'down') !== f.dir) return false;
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
  if (MOVER_PERIODS.some((p) => p.key === r.period)) out.period = r.period as MoverPeriod;
  if (SETUP_KEYS.includes(r.setup as MoverSetup)) out.setup = r.setup as MoverSetup;
  if (r.side === 'call' || r.side === 'put') out.side = r.side;
  out.multi = r.multi === true;
  if (Array.isArray(r.sectors)) out.sectors = r.sectors.filter((s): s is string => typeof s === 'string');
  return out;
}

// ── Sorting ───────────────────────────────────────────────────────────────

export type MoverSortKey = 'liquidity' | 'move' | 'dollar' | 'rvol' | 'price' | 'ticker' | 'premium';

/** What one at-the-money contract 2 weeks to a month out costs, on the side the row is judged on. */
export function swingOf(m: RadarMover, o: RadarOptionMetrics | undefined) {
  const s = o?.swing;
  const kind = m.direction === 'down' ? 'put' : 'call';
  const q = s?.[kind];
  return s && q ? { kind, expiry: s.expiry, dte: s.dte, ...q } : null;
}

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
  { key: 'premium', label: 'Option premium (2-4 weeks)' },
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

export function sortMovers<T extends RadarMover>(
  list: T[],
  sort: MoverSort,
  options: Record<string, RadarOptionMetrics> | undefined,
): T[] {
  const dollars = (m: RadarMover) => num(m.dollar_volume);
  const cmp: Record<MoverSortKey, (a: RadarMover, b: RadarMover) => number> = {
    // Best option liquidity first; inside a grade the stock trading the most dollars today leads.
    liquidity: (a, b) =>
      liquidityRank(b, options?.[b.ticker]) - liquidityRank(a, options?.[a.ticker]) || dollars(b) - dollars(a),
    move: (a, b) => Math.abs(b.change_pct) - Math.abs(a.change_pct),
    dollar: (a, b) => dollars(b) - dollars(a),
    // Extended-hours rows have no volume-vs-normal: their column is the last trade time (newest first).
    rvol: (a, b) => num(b.rvol) - num(a.rvol) || (b.traded_at ?? '').localeCompare(a.traded_at ?? ''),
    price: (a, b) => b.price - a.price,
    ticker: (a, b) => b.ticker.localeCompare(a.ticker),
    premium: (a, b) => num(swingOf(b, options?.[b.ticker])?.cost_usd) - num(swingOf(a, options?.[a.ticker])?.cost_usd),
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
