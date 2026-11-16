import type { KeyboardEvent, ReactNode } from 'react';
import type { QuantVolFields, QuantVolRow } from '../../types';
import { fmtPrice } from '../../utils/format';
import {
  IV_SOURCE_SHORT,
  READING_META,
  fmtDate,
  fmtRatio,
  fmtVol,
  ivSourceNote,
  readingKey,
  readingWhy,
  type SortKey,
  type SortState,
} from './model';

interface Col {
  key: SortKey;
  label: string;
  /** Shorter header on phones. */
  short?: string;
  title: string;
  align?: 'right';
  /** Hidden on phones (kept in the detail panel). */
  hideSm?: boolean;
}

const COLS: Col[] = [
  { key: 'ticker', label: 'Ticker', title: 'Ticker and sector' },
  { key: 'price', label: 'Price', title: 'Last close used by the forecast', align: 'right', hideSm: true },
  {
    key: 'forecast_vol',
    label: 'Forecast vol',
    short: 'Forecast',
    title: 'Model forecast of how much the stock will swing over the next 21 sessions, as a yearly volatility.',
    align: 'right',
  },
  {
    key: 'realized_vol_21d',
    label: 'Last 21d vol',
    title: 'How much it actually swung over the last 21 sessions (same yardstick). The naive "next month looks like last month" guess.',
    align: 'right',
    hideSm: true,
  },
  {
    key: 'implied_vol_30d',
    label: 'Implied vol',
    short: 'Implied',
    title: 'The volatility option prices imply for about the next 30 days. Higher implied vol = pricier options.',
    align: 'right',
  },
  {
    key: 'iv_to_forecast',
    label: 'Implied ÷ forecast',
    short: 'Ratio',
    title: 'Implied vol divided by the forecast. Usually above 1: option sellers charge a premium for risk, so the reading compares this ratio with what is usual, not with 1.',
    align: 'right',
  },
  {
    key: 'reading',
    label: 'Reading',
    title: 'For an option buyer: Cheap = options cost less than usual against the forecast (good), Rich = more than usual (bad). A relative statistic, not advice.',
    hideSm: true,
  },
  {
    key: 'earnings',
    label: 'Earnings',
    title: 'Earnings inside the next 21 sessions make the reading not comparable.',
    hideSm: true,
  },
];

export function ReadingBadge({ row, size = 'sm' }: { row: QuantVolFields; size?: 'sm' | 'md' }) {
  const meta = READING_META[readingKey(row.reading)];
  return (
    <span
      title={readingWhy(row)}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border font-semibold ${meta.badge} ${
        size === 'md' ? 'px-2 py-0.5 text-xs' : 'px-1.5 py-px text-[11px]'
      }`}
    >
      {meta.label}
      {meta.neutral && (
        <span aria-hidden="true" className="opacity-70">
          ⓘ
        </span>
      )}
    </span>
  );
}

function SortHeader({ col, sort, onSort }: { col: Col; sort: SortState; onSort: (k: SortKey) => void }) {
  const active = sort.key === col.key;
  const arrow = active ? (sort.dir === 'asc' ? '▲' : '▼') : '';
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={`px-1.5 sm:px-2.5 py-2 font-medium text-text-secondary whitespace-nowrap ${col.align === 'right' ? 'text-right' : 'text-left'} ${
        col.hideSm ? 'hidden sm:table-cell' : ''
      } ${col.key === 'ticker' ? 'sticky left-0 z-10 bg-[#1b2129]' : ''}`}
    >
      <button
        type="button"
        onClick={() => onSort(col.key)}
        title={col.title}
        className={`inline-flex items-center gap-1 hover:text-text-primary ${active ? 'text-text-primary' : ''}`}
      >
        {col.short ? (
          <>
            <span className="sm:hidden">{col.short}</span>
            <span className="hidden sm:inline">{col.label}</span>
          </>
        ) : (
          col.label
        )}
        <span className="text-[9px] w-2">{arrow}</span>
      </button>
    </th>
  );
}

function Cell({ children, right, hideSm, className = '' }: { children: ReactNode; right?: boolean; hideSm?: boolean; className?: string }) {
  return (
    <td
      className={`px-1.5 sm:px-2.5 py-2 whitespace-nowrap tabular-nums ${right ? 'text-right' : ''} ${hideSm ? 'hidden sm:table-cell' : ''} ${className}`}
    >
      {children}
    </td>
  );
}

function ImpliedCell({ row }: { row: QuantVolRow }) {
  const note = ivSourceNote(row.iv_source);
  if (row.implied_vol_30d === null) return <span className="text-text-secondary">—</span>;
  return (
    <span className="inline-flex flex-col items-end leading-tight" title={note ?? '30-day implied vol from our own option chain snapshot.'}>
      <span>{fmtVol(row.implied_vol_30d)}</span>
      {note && (
        <span className="text-[10px] text-amber-300/90 font-normal">
          ⚠ {IV_SOURCE_SHORT[row.iv_source ?? ''] ?? 'other source'}
        </span>
      )}
    </span>
  );
}

function EarningsCell({ row }: { row: QuantVolRow }) {
  if (row.earnings_in_horizon)
    return (
      <span
        className="inline-flex items-center rounded border border-border px-1.5 py-px text-[11px] text-text-primary"
        title="Earnings inside the next 21 sessions: the reading is not comparable."
      >
        {row.earnings_date ? fmtDate(row.earnings_date).replace(/, \d{4}$/, '') : 'in window'}
      </span>
    );
  if (row.flags?.includes('earnings_unknown'))
    return (
      <span className="text-[11px] text-text-secondary" title="No earnings date on file: an earnings jump could be hiding in the option price.">
        unknown
      </span>
    );
  return <span className="text-text-secondary">—</span>;
}

export default function VolTable({
  rows,
  sort,
  onSort,
  selected,
  onSelect,
}: {
  rows: QuantVolRow[];
  sort: SortState;
  onSort: (k: SortKey) => void;
  selected: string | null;
  onSelect: (ticker: string) => void;
}) {
  const onKey = (e: KeyboardEvent, t: string) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect(t);
    }
  };
  return (
    <div className="overflow-x-auto border border-border rounded-lg bg-card">
      <table className="w-full text-xs sm:text-sm">
        <thead>
          <tr className="bg-[#1b2129] border-b border-border">
            {COLS.map((c) => (
              <SortHeader key={c.key} col={c} sort={sort} onSort={onSort} />
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const active = r.ticker === selected;
            return (
              <tr
                key={r.ticker}
                tabIndex={0}
                onClick={() => onSelect(r.ticker)}
                onKeyDown={(e) => onKey(e, r.ticker)}
                aria-label={`${r.ticker}: open volatility history`}
                className={`group border-t border-border/60 cursor-pointer ${active ? 'bg-accent-900/20' : 'hover:bg-border/30'}`}
              >
                <td
                  className={`px-2 sm:px-2.5 py-2 sticky left-0 z-10 whitespace-nowrap ${
                    active ? 'bg-[#13261f]' : 'bg-card group-hover:bg-[#1a1f27]'
                  }`}
                >
                  <div className="font-semibold text-text-primary">{r.ticker}</div>
                  <div className="hidden sm:block text-[10px] text-text-secondary truncate max-w-[110px]">{r.sector ?? 'Other'}</div>
                  {/* Phones: the reading sits under the ticker (its column is hidden). */}
                  <div className="sm:hidden mt-0.5">
                    <ReadingBadge row={r} />
                  </div>
                </td>
                <Cell right hideSm>
                  {fmtPrice(r.price)}
                </Cell>
                <Cell right className="font-semibold text-text-primary">
                  {fmtVol(r.forecast_vol)}
                </Cell>
                <Cell right hideSm className="text-text-secondary">
                  {fmtVol(r.realized_vol_21d)}
                </Cell>
                <Cell right>
                  <ImpliedCell row={r} />
                </Cell>
                <Cell right>{fmtRatio(r.iv_to_forecast)}</Cell>
                <Cell hideSm>
                  <ReadingBadge row={r} />
                </Cell>
                <Cell hideSm>
                  <EarningsCell row={r} />
                </Cell>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
