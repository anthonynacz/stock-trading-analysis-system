import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  LineChart,
  BarChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import type {
  ChartDatasetInfo,
  ChartDatasetKey,
  ChartResponse,
} from '../types';
import { getChartDatasets, runChartQuery } from '../utils/api';

// Distinguishable colors for multiple series; cycles past 10 series.
const COLORS = [
  '#58a6ff', '#56d364', '#d29922', '#f85149', '#bc8cff',
  '#39c5cf', '#ffa657', '#ff7b72', '#a5d6ff', '#79c0ff',
];

const DATASET_KEYS: ChartDatasetKey[] = [
  'ticker_time_series',
  'signal_breakdown',
  'industry_comparison',
];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}
function daysAgoIso(n: number) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

interface FormState {
  // Common
  from_date: string;
  to_date: string;
  // Ticker Time Series
  ticker: string;
  metrics: string[];
  smoothing_window: number;
  // Signal Breakdown
  tickers: string;
  aggregation: string;
  actions: string[];
  limit: number;
  // Industry Comparison
  view: 'trend' | 'snapshot';
  industries: string[];
  metric: string;
}

function defaultForm(): FormState {
  return {
    from_date: daysAgoIso(30),
    to_date: todayIso(),
    ticker: 'NVDA',
    metrics: ['conviction_score'],
    smoothing_window: 1,
    tickers: '',
    aggregation: 'count',
    actions: [],
    limit: 25,
    view: 'trend',
    industries: [],
    metric: 'conviction_score',
  };
}

// ── Form blocks (one per dataset) ────────────────────────────────────────

function ChipsInput({
  options,
  selected,
  onChange,
  emptyLabel = 'Select…',
}: {
  options: { key: string; label: string; group?: string }[];
  selected: string[];
  onChange: (next: string[]) => void;
  emptyLabel?: string;
}) {
  const toggle = (k: string) => {
    onChange(selected.includes(k) ? selected.filter((s) => s !== k) : [...selected, k]);
  };
  return (
    <div className="flex flex-wrap gap-1">
      {options.length === 0 ? (
        <span className="text-[11px] text-text-secondary italic">{emptyLabel}</span>
      ) : (
        options.map((o) => (
          <button
            key={o.key}
            type="button"
            onClick={() => toggle(o.key)}
            className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
              selected.includes(o.key)
                ? 'bg-accent-900/60 border-accent-500/60 text-accent-200'
                : 'bg-page border-border text-text-secondary hover:border-text-secondary'
            }`}
            title={o.group}
          >
            {o.label}
          </button>
        ))
      )}
    </div>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="block text-[10px] uppercase tracking-wider text-text-secondary mb-1">
      {children}
    </label>
  );
}

function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`bg-page border border-border rounded px-2 py-1 text-xs text-text-primary placeholder-text-secondary focus:outline-none focus:border-accent-500 ${props.className ?? ''}`}
    />
  );
}

function SelectInput(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={`bg-page border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-accent-500 ${props.className ?? ''}`}
    />
  );
}

function TickerTimeSeriesForm({
  form,
  setForm,
  info,
}: {
  form: FormState;
  setForm: (f: Partial<FormState>) => void;
  info: ChartDatasetInfo | null;
}) {
  const metricOptions =
    info?.metrics?.map((m) => ({ key: m.key, label: m.label, group: m.source })) ?? [];
  return (
    <>
      <div>
        <FieldLabel>Ticker</FieldLabel>
        <TextInput
          value={form.ticker}
          onChange={(e) => setForm({ ticker: e.target.value.toUpperCase() })}
          placeholder="NVDA"
          className="w-32"
        />
      </div>
      <div className="flex-1 min-w-[260px]">
        <FieldLabel>Metrics (multi-select)</FieldLabel>
        <ChipsInput
          options={metricOptions}
          selected={form.metrics}
          onChange={(next) => setForm({ metrics: next })}
          emptyLabel="Loading metrics…"
        />
      </div>
      <div>
        <FieldLabel>From</FieldLabel>
        <TextInput
          type="date"
          value={form.from_date}
          onChange={(e) => setForm({ from_date: e.target.value })}
        />
      </div>
      <div>
        <FieldLabel>To</FieldLabel>
        <TextInput
          type="date"
          value={form.to_date}
          onChange={(e) => setForm({ to_date: e.target.value })}
        />
      </div>
      <div>
        <FieldLabel>SMA Window</FieldLabel>
        <TextInput
          type="number"
          min={1}
          max={50}
          value={form.smoothing_window}
          onChange={(e) =>
            setForm({ smoothing_window: Math.max(1, Number(e.target.value) || 1) })
          }
          className="w-16"
        />
      </div>
    </>
  );
}

function SignalBreakdownForm({
  form,
  setForm,
  info,
}: {
  form: FormState;
  setForm: (f: Partial<FormState>) => void;
  info: ChartDatasetInfo | null;
}) {
  const actionOptions = (info?.actions ?? []).map((a) => ({ key: a, label: a.replace('_', ' ') }));
  const aggOptions = info?.aggregations ?? ['count', 'sum', 'avg', 'min', 'max'];
  return (
    <>
      <div>
        <FieldLabel>Aggregation</FieldLabel>
        <SelectInput
          value={form.aggregation}
          onChange={(e) => setForm({ aggregation: e.target.value })}
        >
          {aggOptions.map((a) => (
            <option key={a} value={a}>
              {a.toUpperCase()}
            </option>
          ))}
        </SelectInput>
      </div>
      <div>
        <FieldLabel>Tickers (CSV, optional)</FieldLabel>
        <TextInput
          value={form.tickers}
          onChange={(e) => setForm({ tickers: e.target.value.toUpperCase() })}
          placeholder="NVDA, AAPL  (blank = all)"
          className="w-56"
        />
      </div>
      <div className="flex-1 min-w-[200px]">
        <FieldLabel>Action filter</FieldLabel>
        <ChipsInput
          options={actionOptions}
          selected={form.actions}
          onChange={(next) => setForm({ actions: next })}
          emptyLabel="(none = all)"
        />
      </div>
      <div>
        <FieldLabel>From</FieldLabel>
        <TextInput
          type="date"
          value={form.from_date}
          onChange={(e) => setForm({ from_date: e.target.value })}
        />
      </div>
      <div>
        <FieldLabel>To</FieldLabel>
        <TextInput
          type="date"
          value={form.to_date}
          onChange={(e) => setForm({ to_date: e.target.value })}
        />
      </div>
      <div>
        <FieldLabel>Top N</FieldLabel>
        <TextInput
          type="number"
          min={1}
          max={100}
          value={form.limit}
          onChange={(e) => setForm({ limit: Math.max(1, Number(e.target.value) || 25) })}
          className="w-16"
        />
      </div>
    </>
  );
}

function IndustryComparisonForm({
  form,
  setForm,
  info,
  industriesAll,
}: {
  form: FormState;
  setForm: (f: Partial<FormState>) => void;
  info: ChartDatasetInfo | null;
  industriesAll: string[];
}) {
  const metricOptions = info?.metrics ?? [];
  const indOptions = industriesAll.map((i) => ({ key: i, label: i }));
  return (
    <>
      <div>
        <FieldLabel>View</FieldLabel>
        <SelectInput
          value={form.view}
          onChange={(e) => setForm({ view: e.target.value as 'trend' | 'snapshot' })}
        >
          <option value="trend">Trend (over time)</option>
          <option value="snapshot">Snapshot (latest)</option>
        </SelectInput>
      </div>
      <div>
        <FieldLabel>Metric</FieldLabel>
        <SelectInput value={form.metric} onChange={(e) => setForm({ metric: e.target.value })}>
          {metricOptions.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </SelectInput>
      </div>
      <div className="flex-1 min-w-[260px]">
        <FieldLabel>Industries (none = all)</FieldLabel>
        <ChipsInput
          options={indOptions}
          selected={form.industries}
          onChange={(next) => setForm({ industries: next })}
          emptyLabel="No industries available"
        />
      </div>
      {form.view === 'trend' && (
        <>
          <div>
            <FieldLabel>From</FieldLabel>
            <TextInput
              type="date"
              value={form.from_date}
              onChange={(e) => setForm({ from_date: e.target.value })}
            />
          </div>
          <div>
            <FieldLabel>To</FieldLabel>
            <TextInput
              type="date"
              value={form.to_date}
              onChange={(e) => setForm({ to_date: e.target.value })}
            />
          </div>
        </>
      )}
    </>
  );
}

// ── Chart canvas ──────────────────────────────────────────────────────────

interface UnifiedRow {
  x: string | number;
  [seriesName: string]: string | number | null;
}

function unifyRows(result: ChartResponse): UnifiedRow[] {
  // Collect every x value across all series; build merged rows.
  const xs: (string | number)[] = [];
  const seen = new Set<string>();
  for (const s of result.series) {
    for (const p of s.data) {
      const key = String(p.x);
      if (!seen.has(key)) {
        seen.add(key);
        xs.push(p.x);
      }
    }
  }
  // Sort ascending if dates / numbers
  xs.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return xs.map((x) => {
    const row: UnifiedRow = { x };
    for (const s of result.series) {
      const found = s.data.find((p) => p.x === x);
      row[s.name] = found ? (found.y as number | null) : null;
    }
    return row;
  });
}

function ChartCanvas({ result }: { result: ChartResponse | null }) {
  const rows = useMemo(() => (result ? unifyRows(result) : []), [result]);
  if (!result) {
    return (
      <div className="bg-card border border-border rounded-lg p-12 text-center text-text-secondary text-sm">
        Configure the form on the left and click <span className="text-text-primary font-semibold">Build</span> to render a chart.
      </div>
    );
  }
  if (result.series.length === 0 || rows.length === 0) {
    return (
      <div className="bg-card border border-border rounded-lg p-12 text-center text-text-secondary text-sm">
        No data points returned for this query. Try widening the date range or removing filters.
      </div>
    );
  }

  const sharedAxes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
      <XAxis
        dataKey="x"
        tick={{ fontSize: 10, fill: '#8b949e' }}
        stroke="#21262d"
      />
      <YAxis tick={{ fontSize: 10, fill: '#8b949e' }} stroke="#21262d" />
      <Tooltip
        contentStyle={{ background: '#0d1117', border: '1px solid #30363d', fontSize: 11 }}
        labelStyle={{ color: '#e6edf3' }}
      />
      <Legend wrapperStyle={{ fontSize: 11 }} />
      <ReferenceLine y={0} stroke="#4b5563" strokeDasharray="3 3" />
    </>
  );

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <div className="text-xs text-text-secondary">
          <span className="font-semibold text-text-primary">{result.x_label}</span> ×{' '}
          <span className="font-semibold text-text-primary">{result.y_label}</span>
        </div>
        <div className="text-[10px] text-text-secondary">
          {result.series.length} series · {rows.length} pts
        </div>
      </div>
      <ResponsiveContainer width="100%" height={420}>
        {result.chart_type === 'bar' ? (
          <BarChart data={rows} margin={{ top: 10, right: 16, bottom: 10, left: 0 }}>
            {sharedAxes}
            {result.series.map((s, i) => (
              <Bar key={s.name} dataKey={s.name} fill={COLORS[i % COLORS.length]} />
            ))}
          </BarChart>
        ) : (
          <LineChart data={rows} margin={{ top: 10, right: 16, bottom: 10, left: 0 }}>
            {sharedAxes}
            {result.series.map((s, i) => (
              <Line
                key={s.name}
                type="monotone"
                dataKey={s.name}
                stroke={COLORS[i % COLORS.length]}
                strokeWidth={1.6}
                dot={false}
                connectNulls
              />
            ))}
          </LineChart>
        )}
      </ResponsiveContainer>
      {result.meta && Object.keys(result.meta).length > 0 && (
        <details className="mt-3">
          <summary className="text-[10px] text-text-secondary cursor-pointer hover:text-text-primary">
            Query meta
          </summary>
          <pre className="mt-1 text-[10px] text-text-secondary bg-page p-2 rounded overflow-x-auto">
            {JSON.stringify(result.meta, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function ChartsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [datasets, setDatasets] = useState<ChartDatasetInfo[]>([]);
  const [dataset, setDataset] = useState<ChartDatasetKey>(
    (searchParams.get('dataset') as ChartDatasetKey) || 'ticker_time_series',
  );
  const [form, setFormState] = useState<FormState>(() => {
    const base = defaultForm();
    return {
      ...base,
      ticker: searchParams.get('ticker')?.toUpperCase() || base.ticker,
      metrics: searchParams.get('metrics')?.split(',').filter(Boolean) || base.metrics,
      from_date: searchParams.get('from') || base.from_date,
      to_date: searchParams.get('to') || base.to_date,
      smoothing_window: Number(searchParams.get('smoothing')) || base.smoothing_window,
      aggregation: searchParams.get('agg') || base.aggregation,
      tickers: searchParams.get('tickers')?.toUpperCase() || base.tickers,
      actions: searchParams.get('actions')?.split(',').filter(Boolean) || base.actions,
      limit: Number(searchParams.get('limit')) || base.limit,
      view: (searchParams.get('view') as 'trend' | 'snapshot') || base.view,
      industries: searchParams.get('industries')?.split(',').filter(Boolean) || base.industries,
      metric: searchParams.get('metric') || base.metric,
    };
  });
  const [result, setResult] = useState<ChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setForm = (patch: Partial<FormState>) =>
    setFormState((f) => ({ ...f, ...patch }));

  const datasetInfo = useMemo(
    () => datasets.find((d) => d.key === dataset) ?? null,
    [datasets, dataset],
  );

  // Fetch dataset metadata once
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await getChartDatasets();
        if (!cancelled) setDatasets(d.datasets);
      } catch {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch industries list (for the Industry Comparison form chip picker)
  const [industriesAll, setIndustriesAll] = useState<string[]>([]);
  useEffect(() => {
    if (dataset !== 'industry_comparison') return;
    (async () => {
      try {
        const r = await fetch('/api/industries');
        if (!r.ok) return;
        const data = await r.json();
        setIndustriesAll((data as { industry: string }[]).map((i) => i.industry));
      } catch {
        // ignore
      }
    })();
  }, [dataset]);

  // Build the query spec from current form state
  const buildSpec = useCallback((): Record<string, unknown> => {
    if (dataset === 'ticker_time_series') {
      return {
        ticker: form.ticker,
        metrics: form.metrics,
        from_date: form.from_date,
        to_date: form.to_date,
        smoothing_window: form.smoothing_window,
      };
    }
    if (dataset === 'signal_breakdown') {
      const tickers = form.tickers
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
      return {
        tickers,
        from_date: form.from_date,
        to_date: form.to_date,
        aggregation: form.aggregation,
        actions: form.actions,
        limit: form.limit,
      };
    }
    return {
      view: form.view,
      industries: form.industries,
      metric: form.metric,
      from_date: form.from_date,
      to_date: form.to_date,
    };
  }, [dataset, form]);

  // Sync form state to URL params for shareability
  const syncUrl = useCallback(() => {
    const next = new URLSearchParams();
    next.set('dataset', dataset);
    if (dataset === 'ticker_time_series') {
      next.set('ticker', form.ticker);
      if (form.metrics.length) next.set('metrics', form.metrics.join(','));
      next.set('from', form.from_date);
      next.set('to', form.to_date);
      if (form.smoothing_window > 1) next.set('smoothing', String(form.smoothing_window));
    } else if (dataset === 'signal_breakdown') {
      if (form.tickers) next.set('tickers', form.tickers);
      next.set('agg', form.aggregation);
      if (form.actions.length) next.set('actions', form.actions.join(','));
      next.set('from', form.from_date);
      next.set('to', form.to_date);
      next.set('limit', String(form.limit));
    } else {
      next.set('view', form.view);
      next.set('metric', form.metric);
      if (form.industries.length) next.set('industries', form.industries.join(','));
      if (form.view === 'trend') {
        next.set('from', form.from_date);
        next.set('to', form.to_date);
      }
    }
    setSearchParams(next, { replace: true });
  }, [dataset, form, setSearchParams]);

  const runBuild = async () => {
    setLoading(true);
    setError(null);
    syncUrl();
    try {
      const r = await runChartQuery(dataset, buildSpec());
      setResult(r);
    } catch (e: unknown) {
      const msg =
        e && typeof e === 'object' && 'response' in e
          ? (e as { response: { data: { detail: string } } }).response?.data?.detail
          : e instanceof Error
            ? e.message
            : 'Query failed';
      setError(msg || 'Query failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        <div>
          <h1 className="text-2xl font-bold mb-1">Charts</h1>
          <p className="text-sm text-text-secondary">
            Dynamic visualizations across EdgeFlow data. Pick a dataset, configure
            dimensions / aggregations / filters, click Build. URL updates so you
            can bookmark or share a chart.
          </p>
        </div>

        {/* Dataset tabs */}
        <div className="flex gap-1 border-b border-border">
          {DATASET_KEYS.map((k) => {
            const info = datasets.find((d) => d.key === k);
            return (
              <button
                key={k}
                onClick={() => setDataset(k)}
                className={`px-3 py-2 text-xs font-medium transition-colors border-b-2 ${
                  dataset === k
                    ? 'border-accent-500 text-text-primary'
                    : 'border-transparent text-text-secondary hover:text-text-primary'
                }`}
                title={info?.description}
              >
                {info?.label ?? k}
              </button>
            );
          })}
        </div>

        {/* Form */}
        <div className="bg-card border border-border rounded-lg p-3">
          <div className="flex flex-wrap items-end gap-3">
            {dataset === 'ticker_time_series' && (
              <TickerTimeSeriesForm form={form} setForm={setForm} info={datasetInfo} />
            )}
            {dataset === 'signal_breakdown' && (
              <SignalBreakdownForm form={form} setForm={setForm} info={datasetInfo} />
            )}
            {dataset === 'industry_comparison' && (
              <IndustryComparisonForm
                form={form}
                setForm={setForm}
                info={datasetInfo}
                industriesAll={industriesAll}
              />
            )}
            <div className="ml-auto">
              <button
                onClick={runBuild}
                disabled={loading}
                className="px-4 py-1.5 rounded text-xs font-semibold bg-accent-600/80 hover:bg-accent-600 text-white transition-colors disabled:opacity-50"
              >
                {loading ? 'Building…' : 'Build'}
              </button>
            </div>
          </div>
          {datasetInfo && (
            <p className="text-[10px] text-text-secondary mt-2 italic">
              {datasetInfo.description}
            </p>
          )}
        </div>

        {error && (
          <div className="bg-red-900/20 border border-red-900/40 rounded p-3 text-xs text-red-300">
            {error}
          </div>
        )}

        <ChartCanvas result={result} />
      </div>
    </div>
  );
}
