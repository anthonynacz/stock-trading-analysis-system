import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuantStatus, useQuantVol } from '../hooks/useEdgeFlow';
import { useEntitlements } from '../contexts/EntitlementsContext';
import { EmptyCard, ErrorBox, LoadingRow } from '../components/ui/feedback';
import VolTable from '../components/quant/VolTable';
import TickerVolDrawer from '../components/quant/TickerVolDrawer';
import ModelPanel, { EarlyDaysNote } from '../components/quant/ModelPanel';
import {
  READING_META,
  READING_ORDER,
  countBy,
  fmtDate,
  filterRows,
  readingKey,
  sectorOf,
  sortRows,
  type Filters,
  type ReadingKey,
  type SortKey,
  type SortState,
} from '../components/quant/model';

const VIEW_KEY = 'vela.quant_view';
const DEFAULT_SORT: SortState = { key: 'reading', dir: 'asc' };
const FAST_POLL_MS = 15_000; // keep polling fast this long after an admin start, until `running` shows up

interface View {
  sort: SortState;
  sector: string;
  reading: ReadingKey | '';
}

function readView(): View {
  const v: View = { sort: DEFAULT_SORT, sector: '', reading: '' };
  try {
    const raw = JSON.parse(localStorage.getItem(VIEW_KEY) ?? 'null') as Partial<View> | null;
    if (raw?.sort?.key && (raw.sort.dir === 'asc' || raw.sort.dir === 'desc')) v.sort = raw.sort;
    if (typeof raw?.sector === 'string') v.sector = raw.sector;
    if (raw?.reading && (READING_ORDER as string[]).includes(raw.reading)) v.reading = raw.reading;
  } catch {
    /* private mode / bad JSON: defaults */
  }
  return v;
}

const DEFAULT_DISCLAIMER =
  'Statistical volatility forecast and a relative reading of option prices against it. Not a recommendation and not financial advice.';

function Disclaimer({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-start gap-1.5 sm:flex-row sm:gap-2 rounded border border-border bg-card px-3 py-2 text-xs text-text-secondary">
      <span className="shrink-0 rounded border border-amber-400/50 bg-amber-500/10 px-1.5 py-px text-[10px] font-bold uppercase tracking-wider text-amber-200">
        Measured pattern · not advice
      </span>
      <span className="leading-relaxed">
        {text} It forecasts how much a stock moves, never which way: not a forecast of price.
      </span>
    </div>
  );
}

function ReadingChips({
  counts,
  value,
  onChange,
}: {
  counts: Map<ReadingKey, number>;
  value: ReadingKey | '';
  onChange: (v: ReadingKey | '') => void;
}) {
  const total = [...counts.values()].reduce((a, b) => a + b, 0);
  const chip = (key: ReadingKey | '', label: string, n: number, cls: string) => {
    const active = value === key;
    return (
      <button
        key={key || 'all'}
        type="button"
        onClick={() => onChange(active && key ? '' : key)}
        aria-pressed={active}
        title={key ? READING_META[key].short : 'Every reading'}
        className={`inline-flex items-center gap-1 rounded border px-2 py-1 text-xs whitespace-nowrap transition-colors ${
          active ? `${cls} ring-1 ring-accent-500/60` : 'border-border bg-card text-text-secondary hover:text-text-primary'
        }`}
      >
        {label}
        <span className="tabular-nums opacity-70">{n}</span>
      </button>
    );
  };
  return (
    <div className="flex flex-wrap gap-1.5">
      {chip('', 'All', total, 'border-border bg-border/60 text-text-primary')}
      {READING_ORDER.filter((k) => counts.get(k)).map((k) => chip(k, READING_META[k].label, counts.get(k) ?? 0, READING_META[k].badge))}
    </div>
  );
}

export default function QuantPage() {
  const { isAdmin } = useEntitlements();
  const [view, setView] = useState<View>(readView);
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [fastUntil, setFastUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  const vol = useQuantVol();
  const statusFastWanted = now < fastUntil;
  const [anyRunning, setAnyRunning] = useState(false);
  const status = useQuantStatus(anyRunning || statusFastWanted);

  // A job finished: the forecast table may have changed.
  const running = !!status.data && Object.values(status.data.running ?? {}).some(Boolean);
  const wasRunning = useRef(false);
  const volRefetch = vol.refetch;
  useEffect(() => {
    setAnyRunning(running);
    if (wasRunning.current && !running) volRefetch({ background: true });
    wasRunning.current = running;
  }, [running, volRefetch]);

  useEffect(() => {
    if (!statusFastWanted) return;
    const id = setTimeout(() => setNow(Date.now()), fastUntil - Date.now() + 50);
    return () => clearTimeout(id);
  }, [statusFastWanted, fastUntil]);

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify(view));
    } catch {
      /* ignore */
    }
  }, [view]);

  const statusRefetch = status.refetch;
  const onJobStarted = useCallback(() => {
    const t = Date.now();
    setNow(t);
    setFastUntil(t + FAST_POLL_MS);
    statusRefetch({ background: true });
  }, [statusRefetch]);

  const rows = useMemo(() => vol.data?.rows ?? [], [vol.data]);
  const filters: Filters = useMemo(
    () => ({ q, sector: view.sector, reading: view.reading }),
    [q, view.sector, view.reading],
  );
  const sectorCounts = useMemo(() => countBy(filterRows(rows, filters, 'sector'), sectorOf), [rows, filters]);
  const readingCounts = useMemo(
    () => countBy(filterRows(rows, filters, 'reading'), (r) => readingKey(r.reading)),
    [rows, filters],
  );
  const shown = useMemo(() => sortRows(filterRows(rows, filters), view.sort), [rows, filters, view.sort]);
  const allCounts = useMemo(() => countBy(rows, (r) => readingKey(r.reading)), [rows]);
  const sectors = useMemo(() => [...new Set(rows.map(sectorOf))].sort(), [rows]);
  const selectedRow = rows.find((r) => r.ticker === selected) ?? null;

  const onSort = (key: SortKey) =>
    setView((v) => ({
      ...v,
      sort: v.sort.key === key ? { key, dir: v.sort.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'ticker' || key === 'reading' ? 'asc' : 'desc' },
    }));

  const closeDrawer = useCallback(() => setSelected(null), []);
  const disclaimer = vol.data?.disclaimer ?? status.data?.disclaimer ?? DEFAULT_DISCLAIMER;
  const filtered = !!(q || view.sector || view.reading);

  let main: JSX.Element;
  if (vol.loading && !vol.data) {
    main = <LoadingRow label="Loading forecasts…" />;
  } else if (vol.error && !vol.data) {
    main = (
      <div className="space-y-2">
        <ErrorBox message={`Could not load the volatility forecasts: ${vol.error}`} />
        <button type="button" onClick={() => vol.refetch()} className="btn btn-secondary text-xs">
          Retry
        </button>
      </div>
    );
  } else if (rows.length === 0) {
    main = (
      <EmptyCard>
        <div className="text-text-primary font-semibold mb-1">No forecasts yet</div>
        <p className="max-w-xl mx-auto leading-relaxed">
          Forecasts are computed each weekday after the close (about 17:05 ET), once daily price history is stored. The
          first run needs the price bars job to have filled the history; option prices are compared from the day option
          chain snapshots start.{' '}
          {isAdmin ? 'You can start the jobs below: price bars first, then the forecast.' : 'Check back after the next close.'}
        </p>
      </EmptyCard>
    );
  } else {
    main = (
      <div className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:flex-wrap">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search ticker"
            aria-label="Search ticker"
            className="w-full sm:w-40 bg-card border border-border rounded px-2.5 py-1.5 text-sm text-text-primary placeholder:text-text-secondary"
          />
          <select
            value={view.sector}
            onChange={(e) => setView((v) => ({ ...v, sector: e.target.value }))}
            aria-label="Sector"
            className="w-full sm:w-auto bg-card border border-border rounded px-2 py-1.5 text-sm text-text-primary"
          >
            <option value="">All sectors ({rows.length})</option>
            {sectors.map((s) => (
              <option key={s} value={s}>
                {s} ({sectorCounts.get(s) ?? 0})
              </option>
            ))}
          </select>
          <ReadingChips counts={readingCounts} value={view.reading} onChange={(r) => setView((v) => ({ ...v, reading: r }))} />
        </div>
        {shown.length === 0 ? (
          <EmptyCard>
            No tickers match these filters.{' '}
            <button
              type="button"
              className="text-accent-300 hover:text-accent-200"
              onClick={() => {
                setQ('');
                setView((v) => ({ ...v, sector: '', reading: '' }));
              }}
            >
              Clear filters
            </button>
          </EmptyCard>
        ) : (
          <VolTable rows={shown} sort={view.sort} onSort={onSort} selected={selected} onSelect={setSelected} />
        )}
        <div className="text-[11px] text-text-secondary leading-relaxed">
          {filtered && `${shown.length} of ${rows.length} shown. `}
          Click a row for its history. Colours read for someone <span className="text-text-primary">buying</span> options now:{' '}
          <span className="text-emerald-300">Cheap</span> = options cost less than usual for the volatility expected,{' '}
          <span className="text-amber-300">Rich</span> = more than usual. Grey dashed readings make no comparison (hover for
          why). <span className="text-amber-300/90">⚠</span> marks an implied vol from a weaker source than the 30-day chain
          value.
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-6xl mx-auto px-3 py-4 sm:px-4 sm:py-6 space-y-4 sm:space-y-5">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-bold mb-1">Volatility vs option prices</h1>
            <p className="text-sm text-text-secondary max-w-3xl leading-relaxed">
              For each stock, a statistical forecast of how much it will swing over the next 21 trading sessions, next to
              what option prices imply (implied volatility, about 30 days out). The reading says whether options look
              cheap or rich <em>compared with what is usual</em> for that gap.{' '}
              <Link to="/knowledge?tab=quant" className="text-accent-300 hover:text-accent-200 whitespace-nowrap">
                How to read it
              </Link>
            </p>
          </div>
          {vol.data?.as_of && (
            <div className="text-xs text-text-secondary sm:text-right">
              <div>
                Close of <span className="text-text-primary font-semibold">{fmtDate(vol.data.as_of)}</span>
              </div>
              <div>
                {rows.length} tickers · model {vol.data.model_version}
              </div>
            </div>
          )}
        </div>

        <Disclaimer text={disclaimer} />

        {status.data && (
          <EarlyDaysNote
            st={status.data}
            counts={{ total: rows.length, insufficient: allCounts.get('insufficient history') ?? 0 }}
          />
        )}

        {main}

        {status.loading && !status.data ? (
          <LoadingRow label="Loading model status…" />
        ) : status.error && !status.data ? (
          <div className="space-y-2">
            <ErrorBox message={`Could not load the model status: ${status.error}`} />
            <button type="button" onClick={() => status.refetch()} className="btn btn-secondary text-xs">
              Retry
            </button>
          </div>
        ) : status.data ? (
          <ModelPanel st={status.data} isAdmin={isAdmin} onJobStarted={onJobStarted} />
        ) : null}
      </div>

      {selectedRow && <TickerVolDrawer row={selectedRow} onClose={closeDrawer} />}
    </div>
  );
}
