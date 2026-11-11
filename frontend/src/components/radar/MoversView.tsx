import { Fragment, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { RadarMover, RadarOptionMetrics } from '../../types';
import { SegmentedControl, type SegmentOption } from '../ui/SegmentedControl';
import { LoadingRow } from '../ui/feedback';
import { DirArrow } from './DirArrow';
import { fmtMove, fmtRadarPrice, signClass, tickerHref } from './model';
import { LIQ_CLASS, failReason, fmtCompact, fmtUsdCompact, liqHint, liqText, type OptionFilters } from './optionFilters';
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
  MOVER_SORTS,
  MOVE_CHOICES,
  NO_MOVER_FILTERS,
  RVOL_CHOICES,
  moverFilterCount,
  moverPasses,
  moverSectors,
  sortMovers,
  type MoverDir,
  type MoverFilters,
  type MoverSort,
  type MoverSortKey,
} from './movers';

const DIR_OPTIONS: SegmentOption<MoverDir>[] = [
  { key: 'all', label: 'All' },
  { key: 'up', label: '▲ Up', title: 'Stocks up on the day (judged on calls)' },
  { key: 'down', label: '▼ Down', title: 'Stocks down on the day (judged on puts)' },
];

const SHOWN = 40;

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

/** Liquidity pill of the side the move is traded with, plus open interest and spread. */
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
  filters,
  onFilters,
  sort,
  onSort,
  opt,
  onOpt,
}: {
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

  const all = movers ?? [];
  const set = <K extends keyof MoverFilters>(k: K, v: MoverFilters[K]) => onFilters({ ...filters, [k]: v });
  const sectors = moverSectors(all);
  const passing = all.filter((m) => moverPasses(filters, m));
  // Option filters: hidden names are counted by reason and can be shown faded (as on the radar lists).
  const hidden = new Map<string, string>();
  for (const m of passing) {
    const why = failReason(opt, options?.[m.ticker], m.price, m.direction);
    if (why) hidden.set(m.ticker, why);
  }
  const visible = sortMovers(showHidden ? passing : passing.filter((m) => !hidden.has(m.ticker)), sort, options);
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
              onClick={() => onFilters({ ...NO_MOVER_FILTERS, sectors: [] })}
              className="text-xs text-text-secondary hover:text-text-primary underline-offset-2 hover:underline"
            >
              Clear stock filters
            </button>
          )}
        </div>
        {showFilters && (
          <div className="rounded-md border border-border bg-card/40 p-3 space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
              <Field label="Move today" hint="Smallest move since yesterday's close, up or down">
                <NumSelect label="Move today" value={filters.minMovePct} choices={MOVE_CHOICES} any="Any (2%+)" fmt={(v) => `${v}% or more`} onChange={(v) => set('minMovePct', v)} />
              </Field>
              <Field label="Price from" hint="Lowest stock price">
                <NumSelect label="Price from" value={filters.minPrice} choices={MIN_PRICE_CHOICES} any="Any" fmt={(v) => `$${v}+`} onChange={(v) => set('minPrice', v)} />
              </Field>
              <Field label="Price up to" hint="Highest stock price">
                <NumSelect label="Price up to" value={filters.maxPrice} choices={MAX_PRICE_CHOICES} any="Any" fmt={(v) => `$${v}`} onChange={(v) => set('maxPrice', v)} />
              </Field>
              <Field label="$ volume today" hint="Dollars traded in the stock so far today (price × shares)">
                <NumSelect label="$ volume today" value={filters.minDollarVolM} choices={DOLLAR_VOL_CHOICES} any="Any" fmt={(v) => (v >= 1000 ? `$${v / 1000}B+` : `$${v}M+`)} onChange={(v) => set('minDollarVolM', v)} />
              </Field>
              <Field label="Volume vs normal" hint="Today's volume against the stock's normal volume by this time of day">
                <NumSelect label="Volume vs normal" value={filters.minRvol} choices={RVOL_CHOICES} any="Any" fmt={(v) => `${v}× or more`} onChange={(v) => set('minRvol', v)} />
              </Field>
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
          {passing.length === 0 ? 'No mover matches the stock filters.' : 'Every matching mover is hidden by the option filters.'}
        </RadarEmpty>
      ) : (
        <>
          <div className="overflow-x-auto -mx-3 sm:mx-0">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="text-left text-[10px] text-text-secondary border-b border-border">
                  <SortTh k="ticker" sort={sort} onSort={onSort} className="pl-3 sm:pl-2">Stock</SortTh>
                  <SortTh k="move" sort={sort} onSort={onSort} className="text-right" title="Change since yesterday's close">Move</SortTh>
                  <SortTh k="price" sort={sort} onSort={onSort} className="text-right">Price</SortTh>
                  <SortTh k="dollar" sort={sort} onSort={onSort} className="text-right hidden sm:table-cell" title="Dollars traded so far today">$ volume</SortTh>
                  <SortTh k="rvol" sort={sort} onSort={onSort} className="text-right hidden sm:table-cell" title="Today's volume against normal by this time of day">vs normal</SortTh>
                  <SortTh k="liquidity" sort={sort} onSort={onSort} className="pr-3 sm:pr-2 hidden sm:table-cell" title="Calls for stocks that are up, puts for stocks that are down">Options</SortTh>
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
                            <DirArrow dir={m.direction} />
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
                          </div>
                          <div className="text-[11px] text-text-secondary leading-snug truncate">
                            {m.name ? `${m.name} · ` : ''}
                            {m.sector}
                          </div>
                          {/* Phones: the Options column does not fit, so its pill sits under the name. */}
                          <div className="mt-1 sm:hidden">
                            <OptionCell m={m} o={o} />
                          </div>
                        </td>
                        <td className={`px-2 py-2 text-right font-semibold tabular-nums whitespace-nowrap ${signClass(m.change_pct)}`}>
                          {fmtMove(m.change_pct)}
                        </td>
                        <td className="px-2 py-2 text-right tabular-nums whitespace-nowrap">{fmtRadarPrice(m.price)}</td>
                        <td className="px-2 py-2 text-right tabular-nums whitespace-nowrap hidden sm:table-cell">{fmtUsdCompact(m.dollar_volume)}</td>
                        <td className="px-2 py-2 text-right tabular-nums whitespace-nowrap hidden sm:table-cell">
                          {m.rvol !== null ? `${m.rvol.toFixed(1)}×` : '—'}
                        </td>
                        <td className="px-2 pr-3 sm:pr-2 py-2 whitespace-nowrap hidden sm:table-cell">
                          <OptionCell m={m} o={o} />
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-border/60 bg-card/40">
                          <td colSpan={6} className="px-3 py-3 space-y-2">
                            <div className="text-xs text-text-secondary tabular-nums sm:hidden">
                              {fmtUsdCompact(m.dollar_volume)} traded today
                              {m.rvol !== null ? ` · ${m.rvol.toFixed(1)}× normal volume` : ''}
                            </div>
                            <div className="text-xs text-text-secondary tabular-nums">
                              Yesterday's close {fmtRadarPrice(m.prev_close)}
                              {m.adv_usd !== null ? ` · typically ${fmtUsdCompact(m.adv_usd)} a day` : ''}
                              {m.market_cap !== null ? ` · market cap ${fmtUsdCompact(m.market_cap)}` : ''}
                            </div>
                            {o ? (
                              <>
                                <VerdictBadge o={o} ctx={{ direction: m.direction, role: 'mover', nowMs: now }} full />
                                <CatalystLine o={o} />
                                <OptionChip o={o} direction={m.direction} />
                                <OptionDetails o={o} direction={m.direction} />
                              </>
                            ) : (
                              <p className="text-xs text-text-secondary">
                                Option data for this stock arrives within a few scans (about 40 movers are read per scan).
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
