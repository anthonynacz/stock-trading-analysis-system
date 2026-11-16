import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { QuantVolHistory, QuantVolHistoryRow, QuantVolRow } from '../../types';
import { getApiErrorMessage } from '../../utils/api';
import { fmtPrice } from '../../utils/format';
import { SegmentedControl } from '../ui/SegmentedControl';
import { EmptyCard, ErrorBox, LoadingRow } from '../ui/feedback';
import { getQuantVolHistory } from './quantApi';
import { ReadingBadge } from './VolTable';
import { fmtDate, fmtRatio, fmtVol, flagText, ivSourceNote, readingWhy } from './model';

// Three series on one axis (all annualised vol). Validated as a categorical set on the card surface #161b22
// (dataviz validate_palette, dark: lightness band, CVD and contrast pass); line dashes are the second encoding.
const SERIES = [
  { key: 'forecast', label: 'Forecast (next 21 sessions)', color: '#3b82f6', dash: undefined },
  { key: 'realized', label: 'Realised (last 21 sessions)', color: '#d97706', dash: '5 3' },
  { key: 'implied', label: 'Implied (~30 days)', color: '#a855f7', dash: '2 2' },
] as const;

const RANGES = [
  { key: '90', label: '3M' },
  { key: '365', label: '1Y' },
  { key: '1825', label: '5Y' },
];

interface Point {
  date: string;
  forecast: number | null;
  realized: number | null;
  implied: number | null;
}

const toPct = (v: number | null) => (v === null || v === undefined ? null : Math.round(v * 1000) / 10);

function ChartTooltip({ active, payload, label }: { active?: boolean; payload?: { dataKey: string; value: number | null }[]; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-page border border-border rounded px-2.5 py-2 text-xs shadow-lg">
      <div className="text-text-secondary mb-1">{fmtDate(label)}</div>
      {SERIES.map((s) => {
        const p = payload.find((x) => x.dataKey === s.key);
        return (
          <div key={s.key} className="flex items-center gap-2">
            <span className="inline-block w-3 h-0.5" style={{ background: s.color }} aria-hidden="true" />
            <span className="text-text-secondary">{s.label.split(' (')[0]}</span>
            <span className="ml-auto pl-3 tabular-nums text-text-primary">
              {p?.value === null || p?.value === undefined ? '—' : `${p.value.toFixed(1)}%`}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function VolChart({ rows }: { rows: QuantVolHistoryRow[] }) {
  const data: Point[] = useMemo(
    () =>
      rows.map((r) => ({
        date: r.as_of,
        forecast: toPct(r.forecast_vol),
        realized: toPct(r.realized_vol_21d),
        implied: toPct(r.implied_vol_30d),
      })),
    [rows],
  );
  const single = data.length === 1;
  return (
    <div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 mb-2 text-[11px] text-text-secondary" aria-label="Legend">
        {SERIES.map((s) => (
          <li key={s.key} className="inline-flex items-center gap-1.5">
            <svg width="18" height="6" aria-hidden="true">
              <line x1="0" y1="3" x2="18" y2="3" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} />
            </svg>
            {s.label}
          </li>
        ))}
      </ul>
    <div className="h-56 sm:h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={(d: string) => fmtDate(d).replace(/, \d{4}$/, '')}
            tick={{ fill: '#8b949e', fontSize: 10 }}
            axisLine={{ stroke: '#21262d' }}
            tickLine={false}
            minTickGap={24}
          />
          <YAxis
            tickFormatter={(v: number) => `${v}%`}
            tick={{ fill: '#8b949e', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={44}
            domain={[0, 'auto']}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: '#8b949e', strokeDasharray: '3 3' }} />
          {SERIES.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              strokeDasharray={s.dash}
              dot={single ? { r: 4, fill: s.color, stroke: '#161b22', strokeWidth: 2 } : false}
              activeDot={{ r: 4, stroke: '#161b22', strokeWidth: 2 }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-page/60 border border-border rounded p-2 min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-text-secondary truncate">{label}</div>
      <div className="text-sm font-semibold tabular-nums text-text-primary">{value}</div>
      {sub && <div className="text-[10px] text-text-secondary truncate">{sub}</div>}
    </div>
  );
}

/**
 * Right-hand drawer (full screen on phones) with one ticker's forecast, realised and implied vol over time
 * from GET /api/quant/vol/{ticker}. `row` is the table's latest row, shown while the history loads.
 */
export default function TickerVolDrawer({ row, onClose }: { row: QuantVolRow; onClose: () => void }) {
  const [days, setDays] = useState('365');
  const [hist, setHist] = useState<QuantVolHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const closeRef = useRef<HTMLButtonElement>(null);
  const ticker = row.ticker;

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getQuantVolHistory(ticker, Number(days))
      .then((d) => !cancelled && setHist(d))
      .catch((e: unknown) => !cancelled && setError(getApiErrorMessage(e, 'Could not load the history')))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [ticker, days]);

  useEffect(() => load(), [load]);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, ticker]);

  const rows = hist?.ticker === ticker ? hist.rows : [];
  const note = ivSourceNote(row.iv_source);
  const flags = (row.flags ?? []).filter((f) => f !== 'iv_fallback' || !note);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`${ticker} volatility`}>
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/50 cursor-default" />
      <aside className="relative w-full sm:w-[560px] h-full overflow-y-auto bg-card border-l border-border shadow-2xl">
        <div className="sticky top-0 z-10 bg-card border-b border-border px-4 py-3 flex items-start gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-lg font-bold">{ticker}</h2>
              <ReadingBadge row={row} size="md" />
            </div>
            <div className="text-xs text-text-secondary">
              {row.sector ?? 'Other'} · {fmtPrice(row.price)}
            </div>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} className="btn btn-secondary ml-auto text-xs">
            Close
          </button>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-sm text-text-primary leading-relaxed">{readingWhy(row)}</p>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Stat label="Forecast" value={fmtVol(row.forecast_vol)} sub="next 21 sessions" />
            <Stat label="Last 21d" value={fmtVol(row.realized_vol_21d)} sub="realised" />
            <Stat label="Implied" value={fmtVol(row.implied_vol_30d)} sub="~30 days" />
            <Stat label="Implied ÷ fcst" value={fmtRatio(row.iv_to_forecast)} sub={row.ratio_pctl !== null ? `${Math.round(row.ratio_pctl * 100)}th pct` : 'no reference'} />
          </div>

          {(note || flags.length > 0) && (
            <ul className="text-xs text-text-secondary space-y-1 bg-page/60 border border-border rounded p-3">
              {note && <li>⚠ {note}</li>}
              {flags.map((f) => (
                <li key={f}>• {flagText(f)}</li>
              ))}
            </ul>
          )}

          <div>
            <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
              <h3 className="text-sm font-semibold">Volatility over time</h3>
              <SegmentedControl options={RANGES} value={days} onChange={setDays} size="xs" />
            </div>
            {loading && !hist ? (
              <LoadingRow label="Loading history…" />
            ) : error ? (
              <div className="space-y-2">
                <ErrorBox message={error} />
                <button type="button" onClick={load} className="btn btn-secondary text-xs">
                  Retry
                </button>
              </div>
            ) : rows.length === 0 ? (
              <EmptyCard className="!p-4">No forecast history for {ticker} in this range.</EmptyCard>
            ) : (
              <>
                <VolChart rows={rows} />
                <p className="text-[11px] text-text-secondary mt-2 leading-relaxed">
                  {rows.length < 20
                    ? `Only ${rows.length} daily reading${rows.length === 1 ? '' : 's'} so far: the history starts when the daily forecast job first ran and grows by one point per trading day. `
                    : ''}
                  All three are yearly volatilities. Implied vol usually sits above the forecast: option sellers charge a premium for
                  risk, so the gap alone is not a signal; the reading compares it with what is usual.
                  {rows.some((r) => r.earnings_in_horizon) && ' Days with earnings inside the window carry the earnings jump in the implied line.'}
                </p>
              </>
            )}
          </div>

          <p className="text-[11px] text-text-secondary border-t border-border pt-3">
            {hist?.disclaimer ?? 'Statistical volatility forecast. Not a recommendation and not financial advice.'} It forecasts how
            much the stock moves, not which way.
            {hist?.model_version ? ` Model ${hist.model_version}.` : ''}
          </p>
        </div>
      </aside>
    </div>
  );
}
