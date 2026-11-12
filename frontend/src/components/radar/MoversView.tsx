import { Fragment, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { RadarMover, RadarOptionMetrics } from '../../types';
import { SegmentedControl, type SegmentOption } from '../ui/SegmentedControl';
import { LoadingRow } from '../ui/feedback';
import { fmtEt, toMs } from './clock';
import { DirArrow } from './DirArrow';
import { fmtMove, fmtRadarPrice, signClass, tickerHref } from './model';
import { LIQ_CLASS, failReason, fmtCompact, fmtShortDate, fmtUsdCompact, liqHint, liqText, type OptionFilters } from './optionFilters';
import OptionFilterBar from './OptionFilterBar';
import { CatalystLine, OptionChip, OptionDetails } from './OptionInfo';
import { sideOf } from './optionSide';
import { RadarEmpty, RadarSection } from './RadarLists';
import VerdictBadge from './VerdictBadge';
import {
  DOLLAR_VOL_CHOICES,
  MAX_PRICE_CHOICES,
  MCAP_CHOICES,
  MIN_PRICE_CHOICES,
  MOVER_PERIODS,
  MOVER_SORTS,
  MOVE_CHOICES,
  NO_MOVER_FILTERS,
  PERIOD_FLOOR,
  RVOL_CHOICES,
  SETUP_KEYS,
  SETUP_META,
  moveOf,
  moverFilterCount,
  moverPasses,
  moverRows,
  moverSectors,
  sortMovers,
  swingOf,
  type MoverDir,
  type MoverFilters,
  type MoverPeriod,
  type MoverRow,
  type MoverSetup,
  type MoverSide,
  type MoverSort,
  type MoverSortKey,
} from './movers';

const DIR_OPTIONS: SegmentOption<MoverDir>[] = [
  { key: 'all', label: 'All' },
  { key: 'up', label: '▲ Up', title: 'Stocks up on the day (judged on calls)' },
  { key: 'down', label: '▼ Down', title: 'Stocks down on the day (judged on puts)' },
];

const SHOWN = 40;

/** Which list the view shows: the regular session's movers or the pre-market / after-hours ones. */
export type MoversMode = 'regular' | 'extended';

const MODE_TITLE: Record<MoversMode, string> = {
  regular: 'Moves of the regular session (09:30-16:00 ET) against the prior close',
  extended: 'Moves of the last pre-market / after-hours trade against the last regular close',
};

const SIDE_OPTIONS: { key: MoverSide; label: string }[] = [
  { key: 'follow', label: 'Options: with the move' },
  { key: 'call', label: 'Options: always calls' },
  { key: 'put', label: 'Options: always puts' },
];

const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : fmtMove(v));

/** The multi-day numbers of a row, for the extra columns and the expanded details. */
const MULTI: { key: string; label: string; title: string; period?: MoverPeriod; get: (m: MoverRow) => number | null | undefined }[] = [
  { key: 'd1', label: 'Today', title: "Change since yesterday's close", period: 'd1', get: (m) => m.today_pct },
  { key: 'd5', label: '5 days', title: 'Change over the last 5 sessions, today included', period: 'd5', get: (m) => m.chg_5d },
  { key: 'd20', label: '20 days', title: 'Change over the last 20 sessions, today included', period: 'd20', get: (m) => m.chg_20d },
  { key: 'sma50', label: 'vs 50d avg', title: 'Distance from the 50-day average price: above it, the longer trend is still up', get: (m) => m.vs_sma50 },
  { key: 'high', label: 'Off high', title: 'Distance from the highest close of the last 20 sessions', get: (m) => m.off_high_20d },
];

const fmtUsd = (v: number) => `$${Math.round(v).toLocaleString('en-US')}`;

/** What one at-the-money contract 2 weeks to a month out costs (the side the row is judged on). */
function PremiumCell({ m, o }: { m: RadarMover; o: RadarOptionMetrics | undefined }) {
  const s = swingOf(m, o);
  if (!s) return <span className="text-text-secondary/70">—</span>;
  return (
    <span
      className="cursor-help"
      title={`One at-the-money ${s.kind}, strike ${s.strike}, expiring ${fmtShortDate(s.expiry)} (${s.dte} days): bid ${s.bid ?? '—'} / ask ${s.ask ?? '—'} per share, about ${fmtUsd(s.cost_usd)} per contract. Yahoo quotes, delayed.`}
    >
      <span className="font-semibold text-text-primary">{fmtUsd(s.cost_usd)}</span>
      <span className="text-[11px] text-text-secondary">
        {' '}
        {s.kind} · {fmtShortDate(s.expiry)}
      </span>
    </span>
  );
}

function SetupPill({ setup }: { setup: MoverSetup | null | undefined }) {
  if (!setup) return null;
  const meta = SETUP_META[setup];
  return (
    <span className={`px-1.5 py-px rounded border text-[10px] font-bold cursor-help whitespace-nowrap ${meta.cls}`} title={meta.hint}>
      {meta.label}
    </span>
  );
}

const SELECT = 'bg-card border rounded px-2 py-1 text-xs text-text-primary focus:border-text-secondary w-full transition-colors';
const selectClass = (active: boolean) =>
  `${SELECT} ${active ? 'border-accent-600/70 bg-accent-900/30 font-semibold' : 'border-border'}`;

function Field({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <label className="block space-y-1" title={hint}>
      <span className="block text-[11px] font-medium text-text-secondary">{label}</span>
      {children}
    </label>
  );
}

function NumSelect({
  label,
  value,
  choices,
  any,
  fmt,
  onChange,
}: {
  label: string;
  value: number;
  choices: number[];
  any: string;
  fmt: (v: number) => string;
  onChange: (v: number) => void;
}) {
  const opts = choices.includes(value) ? choices : [...choices, value].sort((a, b) => a - b);
  return (
    <select aria-label={label} className={selectClass(value !== 0)} value={value} onChange={(e) => onChange(Number(e.target.value))}>
      {opts.map((c) => (
        <option key={c} value={c}>
          {c === 0 ? any : fmt(c)}
        </option>
      ))}
    </select>
  );
}

/** Column header that sorts on click (a second click reverses the order). */
function SortTh({
  k,
  sort,
  onSort,
  children,
  className = '',
  title,
}: {
  k: MoverSortKey;
  sort: MoverSort;
  onSort: (s: MoverSort) => void;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  const on = sort.key === k;
  return (
    <th scope="col" aria-sort={on ? (sort.asc ? 'ascending' : 'descending') : 'none'} className={`px-2 py-1.5 font-semibold ${className}`}>
      <button
        type="button"
        title={title}
        onClick={() => onSort({ key: k, asc: on ? !sort.asc : false })}
        className={`inline-flex items-center gap-1 uppercase tracking-wider hover:text-text-primary ${on ? 'text-text-primary' : ''}`}
      >
        {children}
        <span aria-hidden="true" className="text-[9px] w-2">{on ? (sort.asc ? '▲' : '▼') : ''}</span>
      </button>
    </th>
  );
}

/** Liquidity pill of the side the row is judged on (`m.direction`), plus open interest and spread. */
function OptionCell({ m, o }: { m: RadarMover; o: RadarOptionMetrics | undefined }) {
  if (!o) {
    return (
      <span className="text-[11px] text-text-secondary/70" title="Option chains are read for about 40 movers per scan: every name is covered within a few scans">
        Reading…
      </span>
    );
  }
  if (!o.has_options) return <span className={`px-1.5 py-px rounded border text-[11px] font-semibold ${LIQ_CLASS.none}`}>No options</span>;
  const s = sideOf(o, m.direction);
  if (!s) return <span className="text-[11px] text-text-secondary/70">Put data after the next scan</span>;
  return (
    <span className="inline-flex items-center gap-x-2 gap-y-0.5 flex-wrap sm:flex-nowrap text-[11px] text-text-secondary">
      <span className={`px-1.5 py-px rounded border font-semibold cursor-help whitespace-nowrap ${LIQ_CLASS[s.liquidity]}`} title={liqHint(s.liquidity, s.plural)}>
        {liqText(s.liquidity, s.plural)}
      </span>
      <span className="tabular-nums whitespace-nowrap" title={`Open interest near the money · bid-ask spread of the at-the-money ${s.kind}`}>
        OI {fmtCompact(s.ntmOi)}
        {s.spreadPct !== null ? ` · ${s.spreadPct.toFixed(0)}% spread` : ''}
      </span>
    </span>
  );
}

/**
 * Movers: the day's largest plain percentage moves (no market adjustment, no volatility scaling), ordered by
 * option liquidity then dollar volume, with stock-level filters and the radar's option filters. A list to
 * browse, not a signal.
 */
export default function MoversView({
  movers,
  options,
  loading,
  emptyText,
  note,
  onRadar,
  now,
  filters: saved,
  onFilters,
  sort,
  onSort,
  opt,
  onOpt,
  mode = 'regular',
  onMode,
  extendedLabel = null,
}: {
  /** The list being shown; `extendedLabel` ("Pre-market" / "After hours") is null when there is no such list. */
  mode?: MoversMode;
  onMode?: (m: MoversMode) => void;
  extendedLabel?: string | null;
  /** null: no snapshot to show. */
  movers: RadarMover[] | null;
  options?: Record<string, RadarOptionMetrics>;
  loading: boolean;
  emptyText: string;
  note: string;
  /** Tickers on the radar right now (they get a pill). */
  onRadar: Set<string>;
  now: number;
  filters: MoverFilters;
  onFilters: (f: MoverFilters) => void;
  sort: MoverSort;
  onSort: (s: MoverSort) => void;
  opt: OptionFilters;
  onOpt: (f: OptionFilters) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [showFilters, setShowFilters] = useState(true);

  const raw = movers ?? [];
  // Extended-hours rows carry no multi-day context and no volume-vs-normal: those settings are switched off for
  // this list only (the saved ones come back with the regular session's list).
  const ext = mode === 'extended';
  const filters: MoverFilters = ext ? { ...saved, period: 'd1', setup: 'all', multi: false, minRvol: 0 } : saved;
  // Multi-day moves come from the day's baselines: a list written before they carried daily closes has none.
  const hasHistory = raw.some((m) => m.chg_5d !== null && m.chg_5d !== undefined);
  const period: MoverPeriod = hasHistory ? filters.period : 'd1';
  const periodLabel = MOVER_PERIODS.find((p) => p.key === period)?.label ?? 'Today';
  // Rows as shown: `change_pct` is the selected period's move, `direction` the option side they are judged on.
  const all = moverRows(raw, period, filters.side);
  const set = <K extends keyof MoverFilters>(k: K, v: MoverFilters[K]) => onFilters({ ...saved, [k]: v });
  // A setup chip lists every name with that pattern; otherwise the period's own floor applies (2% today, 5% over days).
  const passing = all.filter(
    (m) => moverPasses(filters, m) && (filters.setup !== 'all' || Math.abs(m.change_pct) >= PERIOD_FLOOR[period]),
  );
  // Setup chips count what a click would list: the 5-day view with the other stock filters, any direction.
  const setupPool = moverRows(raw, 'd5', 'follow').filter((m) => moverPasses({ ...filters, setup: 'all', dir: 'all' }, m));
  const setupCount = (k: MoverSetup) => setupPool.filter((m) => m.setup === k).length;
  const multiCols = MULTI.filter((c) => c.period !== period);
  /** One click: the dips, ranked on their 5-day move and judged on calls. A second click goes back. */
  const pickSetup = (k: MoverSetup) =>
    onFilters(
      filters.setup === k
        ? { ...filters, setup: 'all', side: 'follow' }
        : { ...filters, setup: k, period: 'd5', dir: 'all', multi: true, side: k === 'dip_uptrend' ? 'call' : 'follow' },
    );
  // Option filters: hidden names are counted by reason and can be shown faded (as on the radar lists).
  const hidden = new Map<string, string>();
  for (const m of passing) {
    const why = failReason(opt, options?.[m.ticker], m.price, m.direction);
    if (why) hidden.set(m.ticker, why);
  }
  const visible = sortMovers(showHidden ? passing : passing.filter((m) => !hidden.has(m.ticker)), sort, options);
  // Sector chips count what picking the sector would show: every other filter applied, the sector one left out.
  const sectorPool = all.filter(
    (m) =>
      moverPasses({ ...filters, sectors: [] }, m) &&
      (filters.setup !== 'all' || Math.abs(m.change_pct) >= PERIOD_FLOOR[period]) &&
      (showHidden || !failReason(opt, options?.[m.ticker], m.price, m.direction)),
  );
  const sectors = [
    ...moverSectors(sectorPool),
    ...filters.sectors.filter((s) => !sectorPool.some((m) => m.sector === s)).map((sector) => ({ sector, n: 0 })),
  ];
  const rows = showAll ? visible : visible.slice(0, SHOWN);
  const nFilters = moverFilterCount(filters);
  const toggle = (t: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (!next.delete(t)) next.add(t);
      return next;
    });
  const toggleSector = (s: string) =>
    set('sectors', filters.sectors.includes(s) ? filters.sectors.filter((x) => x !== s) : [...filters.sectors, s]);

  return (
    <RadarSection
      id="radar-movers"
      title="Movers"
      count={movers ? visible.length : null}
      note={note}
      tools={
        <div className="flex items-center gap-2 flex-wrap w-full sm:w-auto">
          <SegmentedControl variant="joined" options={DIR_OPTIONS} value={filters.dir} onChange={(d) => set('dir', d)} />
          <select
            aria-label="Sort by"
            value={sort.key}
            onChange={(e) => onSort({ key: e.target.value as MoverSortKey, asc: false })}
            className="bg-card border border-border rounded px-2 py-1.5 text-xs text-text-primary focus:border-text-secondary"
          >
            {MOVER_SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      }
    >
      <div className="mb-2 space-y-2">
        {extendedLabel && onMode && (
          <SegmentedControl
            options={[
              { key: 'regular', label: 'Regular session', title: MODE_TITLE.regular },
              { key: 'extended', label: extendedLabel, title: MODE_TITLE.extended },
            ]}
            value={mode}
            onChange={onMode}
          />
        )}
        {ext && (
          <p className="text-xs leading-snug rounded border border-amber-500/30 bg-amber-500/10 text-amber-100 px-2 py-1.5">
            <span className="font-semibold">{extendedLabel ?? 'Extended hours'}: volume unconfirmed.</span> Few shares trade
            outside regular hours, so a move here can be a handful of trades and can vanish at the open. Options do not
            trade now: the option columns show each stock&apos;s last regular-session reading, and spreads and IV are
            usually worse in the first minutes after the open.
          </p>
        )}
        {ext && (
          <select
            aria-label="Which options to judge"
            value={filters.side}
            onChange={(e) => set('side', e.target.value as MoverSide)}
            title="With the move: calls for stocks that are up, puts for stocks that are down."
            className={`${selectClass(filters.side !== 'follow')} !w-auto`}
          >
            {SIDE_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
        )}
        <div className={`rounded-md border border-border bg-card/40 p-2.5 space-y-2 ${ext ? 'hidden' : ''}`}>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] font-medium text-text-secondary">Move over</span>
            <SegmentedControl
              variant="joined"
              options={MOVER_PERIODS.map((p) => ({
                key: p.key,
                label: p.label,
                disabled: !hasHistory && p.key !== 'd1',
                title: p.key === 'd1' ? "Change since yesterday's close" : `Change over the last ${p.label.replace(' days', '')} sessions, today included`,
              }))}
              value={period}
              onChange={(k) => set('period', k)}
            />
            <button
              type="button"
              aria-pressed={filters.multi}
              disabled={!hasHistory}
              onClick={() => set('multi', !filters.multi)}
              title="Show today, 5-day and 20-day moves, the distance from the 50-day average and from the 20-day high on every row"
              className={`px-3 py-1.5 rounded-md text-xs font-semibold border transition-colors disabled:opacity-50 ${
                filters.multi
                  ? 'border-accent-600/60 bg-accent-900/40 text-accent-300'
                  : 'border-border bg-card text-text-secondary hover:text-text-primary'
              }`}
            >
              {filters.multi ? '▣' : '▢'} Multi-day columns
            </button>
            <select
              aria-label="Which options to judge"
              value={filters.side}
              onChange={(e) => set('side', e.target.value as MoverSide)}
              title="With the move: calls for stocks that are up over the period, puts for stocks that are down. Buying a dip means calls on a stock that is down."
              className={`${selectClass(filters.side !== 'follow')} !w-auto`}
            >
              {SIDE_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          {hasHistory ? (
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] font-medium text-text-secondary">Setups</span>
              {SETUP_KEYS.map((k) => {
                const on = filters.setup === k;
                const n = setupCount(k);
                return (
                  <button
                    key={k}
                    type="button"
                    aria-pressed={on}
                    disabled={n === 0 && !on}
                    onClick={() => pickSetup(k)}
                    title={SETUP_META[k].hint}
                    className={`px-2.5 py-1 rounded-full border text-xs font-semibold transition-colors disabled:opacity-40 ${
                      on ? `${SETUP_META[k].cls} ring-1 ring-current` : `${SETUP_META[k].cls} opacity-80 hover:opacity-100`
                    }`}
                  >
                    {SETUP_META[k].chip} <span className="tabular-nums">{n}</span>
                  </button>
                );
              })}
              <span className="text-[11px] text-text-secondary/80">Patterns from a 2026 backtest, not forecasts. Hover a chip for the numbers.</span>
            </div>
          ) : (
            raw.length > 0 && (
              <p className="text-[11px] text-text-secondary/80">
                Multi-day moves and setups arrive with the next session's first scan (they are read from that day's baselines).
              </p>
            )
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => setShowFilters((x) => !x)}
            aria-expanded={showFilters}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border transition-colors ${
              nFilters || showFilters
                ? 'border-accent-600/60 bg-accent-900/40 text-accent-300'
                : 'border-border bg-card text-text-secondary hover:text-text-primary'
            }`}
          >
            <span aria-hidden="true">⚙</span> Stock filters
            {nFilters > 0 && <span className="rounded-full bg-accent-300 px-1.5 text-[10px] font-bold text-gray-900">{nFilters}</span>}
            <span aria-hidden="true" className={`text-[9px] transition-transform ${showFilters ? 'rotate-180' : ''}`}>▼</span>
          </button>
          {nFilters > 0 && (
            <button
              type="button"
              onClick={() => onFilters({ ...NO_MOVER_FILTERS, sectors: [], period: saved.period, multi: saved.multi })}
              className="text-xs text-text-secondary hover:text-text-primary underline-offset-2 hover:underline"
            >
              Clear stock filters
            </button>
          )}
        </div>
        {showFilters && (
          <div className="rounded-md border border-border bg-card/40 p-3 space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
              <Field label={ext ? 'Move' : period === 'd1' ? 'Move today' : `Move over ${periodLabel}`} hint={ext ? 'Smallest move since the last regular close, up or down' : 'Smallest move over the selected period, up or down'}>
                <NumSelect label="Smallest move" value={filters.minMovePct} choices={MOVE_CHOICES} any={`Any (${PERIOD_FLOOR[period]}%+)`} fmt={(v) => `${v}% or more`} onChange={(v) => set('minMovePct', v)} />
              </Field>
              <Field label="Price from" hint="Lowest stock price">
                <NumSelect label="Price from" value={filters.minPrice} choices={MIN_PRICE_CHOICES} any="Any" fmt={(v) => `$${v}+`} onChange={(v) => set('minPrice', v)} />
              </Field>
              <Field label="Price up to" hint="Highest stock price">
                <NumSelect label="Price up to" value={filters.maxPrice} choices={MAX_PRICE_CHOICES} any="Any" fmt={(v) => `$${v}`} onChange={(v) => set('maxPrice', v)} />
              </Field>
              <Field
                label={ext ? '$ volume (extended)' : '$ volume today'}
                hint={
                  ext
                    ? 'Dollars traded in extended hours since the last close. Known for the 30 largest gainers and losers only: this filter hides the rest.'
                    : 'Dollars traded in the stock so far today (price × shares)'
                }
              >
                <NumSelect label="$ volume today" value={filters.minDollarVolM} choices={DOLLAR_VOL_CHOICES} any="Any" fmt={(v) => (v >= 1000 ? `$${v / 1000}B+` : `$${v}M+`)} onChange={(v) => set('minDollarVolM', v)} />
              </Field>
              {!ext && (
                <Field label="Volume vs normal" hint="Today's volume against the stock's normal volume by this time of day">
                  <NumSelect label="Volume vs normal" value={filters.minRvol} choices={RVOL_CHOICES} any="Any" fmt={(v) => `${v}× or more`} onChange={(v) => set('minRvol', v)} />
                </Field>
              )}
              <Field label="Company size" hint="Market capitalisation">
                <NumSelect label="Company size" value={filters.minMcapB} choices={MCAP_CHOICES} any="Any" fmt={(v) => `$${v}B+`} onChange={(v) => set('minMcapB', v)} />
              </Field>
            </div>
            {sectors.length > 0 && (
              <div>
                <div className="text-[11px] font-medium text-text-secondary mb-1">
                  Sector{filters.sectors.length ? ` (${filters.sectors.length} selected)` : ' (all)'}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {sectors.map(({ sector, n }) => {
                    const on = filters.sectors.includes(sector);
                    return (
                      <button
                        key={sector}
                        type="button"
                        aria-pressed={on}
                        onClick={() => toggleSector(sector)}
                        className={`px-2 py-0.5 rounded-full border text-[11px] transition-colors ${
                          on
                            ? 'border-accent-600/70 bg-accent-900/50 text-accent-300 font-semibold'
                            : 'border-border bg-card text-text-secondary hover:text-text-primary'
                        }`}
                      >
                        {sector} <span className="tabular-nums opacity-70">{n}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
        <OptionFilterBar value={opt} onChange={onOpt} onCallPreset={() => set('dir', 'up')} />
      </div>

      {hidden.size > 0 && (
        <p className="text-xs text-sky-200/90 leading-snug mb-2 rounded border border-sky-500/20 bg-sky-500/5 px-2 py-1">
          {hidden.size} of {passing.length} hidden by the option filters.{' '}
          <button type="button" onClick={() => setShowHidden((x) => !x)} className="font-semibold text-sky-300 hover:text-sky-100 underline-offset-2 hover:underline">
            {showHidden ? 'Hide them' : 'Show them faded'}
          </button>
        </p>
      )}

      {loading ? (
        <LoadingRow />
      ) : !movers || all.length === 0 ? (
        <RadarEmpty>{emptyText}</RadarEmpty>
      ) : visible.length === 0 ? (
        <RadarEmpty>
          {passing.length === 0
            ? filters.setup !== 'all'
              ? `No stock shows the "${SETUP_META[filters.setup].label}" pattern right now with these filters.`
              : 'No mover matches the stock filters.'
            : 'Every matching mover is hidden by the option filters.'}
        </RadarEmpty>
      ) : (
        <>
          <div className="overflow-x-auto -mx-3 sm:mx-0">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="text-left text-[10px] text-text-secondary border-b border-border">
                  <SortTh k="ticker" sort={sort} onSort={onSort} className="pl-3 sm:pl-2">Stock</SortTh>
                  <SortTh k="move" sort={sort} onSort={onSort} className="text-right" title={ext ? 'Last extended-hours trade against the last regular close' : period === 'd1' ? "Change since yesterday's close" : `Change over the last ${periodLabel}, today included`}>
                    {period === 'd1' ? 'Move' : periodLabel}
                  </SortTh>
                  {filters.multi &&
                    multiCols.map((c) => (
                      <th key={c.key} scope="col" title={c.title} className="px-2 py-1.5 font-semibold text-right uppercase tracking-wider whitespace-nowrap hidden md:table-cell">
                        {c.label}
                      </th>
                    ))}
                  <SortTh k="price" sort={sort} onSort={onSort} className="text-right">Price</SortTh>
                  <SortTh k="dollar" sort={sort} onSort={onSort} className="text-right hidden sm:table-cell" title={ext ? 'Dollars traded in extended hours since the last close (largest movers only)' : 'Dollars traded so far today'}>$ volume</SortTh>
                  <SortTh k="rvol" sort={sort} onSort={onSort} className="text-right hidden sm:table-cell" title={ext ? 'When the last extended-hours trade printed (ET)' : "Today's volume against normal by this time of day"}>{ext ? 'Last trade' : 'vs normal'}</SortTh>
                  <SortTh k="liquidity" sort={sort} onSort={onSort} className="hidden sm:table-cell" title="Calls for stocks that are up, puts for stocks that are down">Options</SortTh>
                  <SortTh k="premium" sort={sort} onSort={onSort} className="pr-3 sm:pr-2 text-right hidden sm:table-cell" title="What one at-the-money contract costs for the first expiry 2 weeks to a month out">2-4 wk premium</SortTh>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => {
                  const o = options?.[m.ticker];
                  const isOpen = open.has(m.ticker);
                  return (
                    <Fragment key={m.ticker}>
                      <tr
                        onClick={() => toggle(m.ticker)}
                        className={`border-b border-border/60 cursor-pointer hover:bg-border/30 ${hidden.has(m.ticker) ? 'opacity-40' : ''}`}
                      >
                        <td className="px-2 pl-3 sm:pl-2 py-2 w-full max-w-0">
                          <div className="flex items-baseline gap-1.5 flex-wrap">
                            <DirArrow dir={m.change_pct >= 0 ? 'up' : 'down'} />
                            <Link
                              to={tickerHref(m.ticker)}
                              onClick={(e) => e.stopPropagation()}
                              title="Open in the ticker detail panel"
                              className="font-bold text-text-primary hover:text-accent-300 transition-colors"
                            >
                              {m.ticker}
                            </Link>
                            {onRadar.has(m.ticker) && (
                              <span className="px-1.5 py-px rounded border border-accent-600/60 bg-accent-900/50 text-accent-300 text-[10px] font-semibold" title="Also on the Momentum Radar right now">
                                On the radar
                              </span>
                            )}
                            <SetupPill setup={m.setup} />
                          </div>
                          <div className="text-[11px] text-text-secondary leading-snug truncate">
                            {m.name ? `${m.name} · ` : ''}
                            {m.sector}
                          </div>
                          {/* Narrow screens: the multi-day columns do not fit, so they sit under the name. */}
                          {filters.multi && (
                            <div className="mt-0.5 flex flex-wrap gap-x-2 text-[11px] text-text-secondary tabular-nums md:hidden">
                              {multiCols.map((c) => (
                                <span key={c.key} className="whitespace-nowrap">
                                  {c.label} <span className={signClass(c.get(m) ?? null)}>{fmtPct(c.get(m))}</span>
                                </span>
                              ))}
                            </div>
                          )}
                          {/* Phones: the Options column does not fit, so its pill sits under the name. */}
                          <div className="mt-1 sm:hidden flex items-center gap-x-2 gap-y-0.5 flex-wrap text-xs tabular-nums">
                            <OptionCell m={m} o={o} />
                            {swingOf(m, o) && <PremiumCell m={m} o={o} />}
                          </div>
                        </td>
                        <td className={`px-2 py-2 text-right font-semibold tabular-nums whitespace-nowrap ${signClass(m.change_pct)}`}>
                          {fmtMove(m.change_pct)}
                        </td>
                        {filters.multi &&
                          multiCols.map((c) => (
                            <td key={c.key} className={`px-2 py-2 text-right tabular-nums whitespace-nowrap hidden md:table-cell ${signClass(c.get(m) ?? null)}`}>
                              {fmtPct(c.get(m))}
                            </td>
                          ))}
                        <td className="px-2 py-2 text-right tabular-nums whitespace-nowrap">{fmtRadarPrice(m.price)}</td>
                        <td className="px-2 py-2 text-right tabular-nums whitespace-nowrap hidden sm:table-cell">{fmtUsdCompact(m.dollar_volume)}</td>
                        <td className="px-2 py-2 text-right tabular-nums whitespace-nowrap hidden sm:table-cell">
                          {ext ? fmtEt(toMs(m.traded_at)) : m.rvol !== null ? `${m.rvol.toFixed(1)}×` : '—'}
                        </td>
                        <td className="px-2 py-2 whitespace-nowrap hidden sm:table-cell">
                          <OptionCell m={m} o={o} />
                        </td>
                        <td className="px-2 pr-3 sm:pr-2 py-2 text-right tabular-nums whitespace-nowrap hidden sm:table-cell">
                          <PremiumCell m={m} o={o} />
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-border/60 bg-card/40">
                          <td colSpan={7 + (filters.multi ? multiCols.length : 0)} className="px-3 py-3 space-y-2">
                            {ext ? (
                              <div className="text-xs text-text-secondary tabular-nums">
                                Last trade {fmtEt(toMs(m.traded_at))}
                                {m.dollar_volume !== null
                                  ? ` · ${fmtUsdCompact(m.dollar_volume)} traded in extended hours (${fmtCompact(m.day_volume)} shares)`
                                  : ' · extended-hours volume not read for this stock'}
                              </div>
                            ) : (
                              <div className="text-xs text-text-secondary tabular-nums sm:hidden">
                                {fmtUsdCompact(m.dollar_volume)} traded today
                                {m.rvol !== null ? ` · ${m.rvol.toFixed(1)}× normal volume` : ''}
                              </div>
                            )}
                            {moveOf(m, 'd5') !== null && (
                              <div className="flex flex-wrap gap-x-3 text-xs text-text-secondary tabular-nums leading-relaxed">
                                {[
                                  ['Today', m.today_pct],
                                  ['3 days', m.chg_3d],
                                  ['5 days', m.chg_5d],
                                  ['10 days', m.chg_10d],
                                  ['20 days', m.chg_20d],
                                  ['vs 20-day average', m.vs_sma20],
                                  ['vs 50-day average', m.vs_sma50],
                                  ['off the 20-day high', m.off_high_20d],
                                ].map(([label, v]) => (
                                  <span key={label as string} className="whitespace-nowrap">
                                    {label} <span className={`font-semibold ${signClass(v as number | null)}`}>{fmtPct(v as number | null)}</span>
                                  </span>
                                ))}
                              </div>
                            )}
                            {m.setup && (
                              <p className="text-xs leading-snug text-text-primary">
                                <SetupPill setup={m.setup} /> {SETUP_META[m.setup].hint}
                              </p>
                            )}
                            <div className="text-xs text-text-secondary tabular-nums">
                              {ext ? 'Last close' : "Yesterday's close"} {fmtRadarPrice(m.prev_close)}
                              {m.adv_usd !== null ? ` · typically ${fmtUsdCompact(m.adv_usd)} a day` : ''}
                              {m.market_cap !== null ? ` · market cap ${fmtUsdCompact(m.market_cap)}` : ''}
                            </div>
                            {o?.swing && (o.swing.call || o.swing.put) && (
                              <div className="text-xs text-text-secondary tabular-nums">
                                One at-the-money contract expiring {fmtShortDate(o.swing.expiry)} ({o.swing.dte} days):
                                {o.swing.call && (
                                  <span className="whitespace-nowrap">
                                    {' '}
                                    call <span className="font-semibold text-text-primary">{fmtUsd(o.swing.call.cost_usd)}</span> (strike {o.swing.call.strike})
                                  </span>
                                )}
                                {o.swing.call && o.swing.put ? ' ·' : ''}
                                {o.swing.put && (
                                  <span className="whitespace-nowrap">
                                    {' '}
                                    put <span className="font-semibold text-text-primary">{fmtUsd(o.swing.put.cost_usd)}</span> (strike {o.swing.put.strike})
                                  </span>
                                )}
                              </div>
                            )}
                            {o ? (
                              <>
                                <VerdictBadge o={o} ctx={{ direction: m.direction, role: 'mover', nowMs: now }} full />
                                <CatalystLine o={o} />
                                <OptionChip o={o} direction={m.direction} />
                                <OptionDetails o={o} direction={m.direction} />
                              </>
                            ) : (
                              <p className="text-xs text-text-secondary">
                                {ext
                                  ? 'No option data for this stock yet: about 30 movers are read after each extended-hours scan, and pre-market quotes are often empty until the open.'
                                  : 'Option data for this stock arrives within a few scans (about 40 movers are read per scan).'}
                              </p>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {visible.length > SHOWN && (
            <button type="button" onClick={() => setShowAll((x) => !x)} className="mt-2 text-xs font-semibold text-accent-300 hover:text-accent-200">
              {showAll ? `Show the first ${SHOWN}` : `Show all ${visible.length} (first ${SHOWN} shown)`}
            </button>
          )}
        </>
      )}
    </RadarSection>
  );
}
