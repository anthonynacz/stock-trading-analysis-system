import type { ReactNode } from 'react';
import { ActionBadge } from '../../components/ui/badges';

export { ACTION_COLORS, BRAND } from '../../utils/theme';

// Mirrors backend/config.py (MAX_WATCHLIST / MAX_PER_SECTOR / SECTOR_ETFS /
// MAX_CHANGES_PER_DAY) and the toxic-ticker streak in watchlist rotation.
export const WATCHLIST_LIMITS = { max: 60, perSector: 12, sectors: 8, maxDailyChanges: 10, toxicDays: 3 } as const;

// IV-rank buckets used by backend/services/deep_options_analyzer.py.
export const IV_BUCKETS = { low: '<30', mid: '30–69', high: '>=70' } as const;

// DTE spread buckets from deep_options_analyzer.py (up to 8 expirations).
export const DTE_BUCKETS = '0–7d, 7–21d, 21–45d, 45–90d, 90–180d, 180+d';

/* ── DocTable ───────────────────────────────────────────────────────────── */

export interface DocColumn<T> {
  key: string;
  header: ReactNode;
  align?: 'left' | 'center';
  /** Extra classes on the `<th>` (widths, etc.). */
  headerClass?: string;
  /** Extra classes on each `<td>` (colour, font, etc.). */
  className?: string;
  /** Hide the column below the `md` breakpoint. */
  hideMd?: boolean;
  /** Custom cell content; defaults to `String(row[key])`. */
  render?: (row: T) => ReactNode;
}

// 'xs' keeps the sm chrome at text-xs; 'xs-dense' is the tighter in-card variant.
const TABLE_SIZE = {
  sm: { wrap: 'rounded-lg', table: 'text-sm', cell: 'px-3 py-2' },
  xs: { wrap: 'rounded-lg', table: 'text-xs', cell: 'px-3 py-2' },
  'xs-dense': { wrap: 'rounded', table: 'text-xs', cell: 'px-2 py-1.5' },
} as const;

export function DocTable<T>({
  columns,
  rows,
  rowKey,
  size = 'sm',
  className = '',
  rowClassName = '',
}: {
  columns: DocColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  size?: keyof typeof TABLE_SIZE;
  className?: string;
  rowClassName?: string;
}) {
  const s = TABLE_SIZE[size];
  const colClass = (c: DocColumn<T>) =>
    `${s.cell} ${c.align === 'center' ? 'text-center' : ''} ${c.hideMd ? 'hidden md:table-cell' : ''}`;
  return (
    <div className={`overflow-hidden border border-border ${s.wrap} ${className}`}>
      <table className={`w-full ${s.table}`}>
        <thead>
          <tr className="bg-border/40">
            {columns.map((c) => (
              <th
                key={c.key}
                className={`${c.align === 'center' ? '' : 'text-left'} ${colClass(c)} text-text-secondary font-medium ${c.headerClass ?? ''}`}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className={`border-t border-border/60 ${rowClassName}`}>
              {columns.map((c) => (
                <td key={c.key} className={`${colClass(c)} ${c.className ?? ''}`}>
                  {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── StatTile ───────────────────────────────────────────────────────────── */

export function StatTile({
  label,
  value,
  sub,
  valueClass = 'text-text-primary',
  size = 'sm',
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  valueClass?: string;
  /** 'sm' = compact centred tile; 'lg' = big-number tile with optional sub line. */
  size?: 'sm' | 'lg';
}) {
  const lg = size === 'lg';
  return (
    <div className={`bg-card border border-border rounded-lg ${lg ? 'p-4' : 'p-3 text-center'}`}>
      <div className="text-xs text-text-secondary mb-1">{label}</div>
      <div className={`${lg ? 'text-2xl font-bold' : 'text-sm font-semibold'} ${valueClass}`}>{value}</div>
      {sub && <div className="text-xs text-text-secondary mt-1">{sub}</div>}
    </div>
  );
}

/* ── SectionHeading ─────────────────────────────────────────────────────── */

export function SectionHeading({
  title,
  blurb,
  size = 'base',
}: {
  title: string;
  blurb?: ReactNode;
  size?: 'base' | 'lg';
}) {
  const lg = size === 'lg';
  return (
    <>
      <h3 className={`text-text-primary ${lg ? 'text-lg font-bold mb-1' : 'text-base font-semibold mb-2'}`}>{title}</h3>
      {blurb && <p className={`text-xs text-text-secondary ${lg ? 'mb-4' : 'mb-3'}`}>{blurb}</p>}
    </>
  );
}

/* ── PlaybookCard ───────────────────────────────────────────────────────── */

export type PlaybookBadge =
  | { kind: 'action'; action: string }
  | { kind: 'class'; className: string; label: string };

export interface Playbook {
  badge: PlaybookBadge;
  iv?: 'LOW' | 'HIGH';
  risk?: 'LOW' | 'MEDIUM' | 'HIGH';
  /** Free-text tag shown in place of an IV/risk pill (e.g. "Any IV"). */
  note?: string;
  title: string;
  fields: [label: string, text: ReactNode][];
}

const IV_PILL = {
  LOW: 'bg-green-900/30 text-green-400',
  HIGH: 'bg-red-900/30 text-red-400',
} as const;

const RISK_TEXT = {
  LOW: 'text-green-400',
  MEDIUM: 'text-amber-400',
  HIGH: 'text-red-400',
} as const;

export function PlaybookCard({ badge, iv, risk, note, title, fields }: Playbook) {
  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-3">
      <div className="flex items-center gap-2">
        {badge.kind === 'action' ? (
          <ActionBadge action={badge.action} size="sm" />
        ) : (
          <span className={`px-2 py-0.5 rounded text-xs font-bold ${badge.className}`}>{badge.label}</span>
        )}
        {iv && <span className={`px-2 py-0.5 rounded text-xs font-bold ${IV_PILL[iv]}`}>{iv} IV</span>}
        {risk && <span className={`text-[10px] font-medium ${RISK_TEXT[risk]}`}>RISK: {risk}</span>}
        {note && <span className="text-[10px] text-text-secondary font-medium">{note}</span>}
      </div>
      <h4 className="text-sm font-semibold text-text-primary">{title}</h4>
      <div className="text-xs text-text-secondary space-y-1.5">
        {fields.map(([label, text]) => (
          <p key={label}>
            <span className="font-semibold text-text-primary">{label}:</span> {text}
          </p>
        ))}
      </div>
    </div>
  );
}
