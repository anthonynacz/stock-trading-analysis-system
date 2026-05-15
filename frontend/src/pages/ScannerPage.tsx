import { useState, useEffect, useMemo, useCallback } from 'react';
import type { ScannerResult, ScannerTier, ScannerRunStatus } from '../types';
import {
  getScannerResults,
  getScannerDates,
  getScannerStatus,
  runScanner,
  addScannerUniverse,
  removeScannerUniverse,
} from '../utils/api';

const TIERS: { key: ScannerTier | 'ALL'; label: string; color: string }[] = [
  { key: 'ALL', label: 'All', color: '#8b949e' },
  { key: 'HOT', label: 'Hot', color: '#f85149' },
  { key: 'WATCH', label: 'Watch', color: '#d29922' },
  { key: 'MONITOR', label: 'Monitor', color: '#58a6ff' },
  { key: 'IGNORE', label: 'Ignore', color: '#484f58' },
];

const TIER_COLOR: Record<ScannerTier, string> = {
  HOT: '#f85149',
  WATCH: '#d29922',
  MONITOR: '#58a6ff',
  IGNORE: '#484f58',
};

const THEME_LABEL: Record<string, string> = {
  AI_MEMORY: 'AI Memory',
  AI_COMPUTE: 'AI Compute',
  AI_APPS: 'AI Apps',
  POWER_NUCLEAR: 'Power / Nuclear',
  ROBOTICS_SPACE: 'Robotics / Space',
  AUTONOMY_MOBILITY: 'Autonomy / Mobility',
  FINTECH_CRYPTO: 'Fintech / Crypto',
  BIOTECH_GLP1: 'Biotech / GLP-1',
  QUANTUM: 'Quantum',
  SAAS_GROWTH: 'SaaS / Growth',
  RECENT_IPO_SPINOFF: 'Recent IPO / Spinoff',
};

function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(2)}`;
}

function TierBadge({ tier }: { tier: ScannerTier }) {
  const c = TIER_COLOR[tier];
  return (
    <span
      className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded"
      style={{ color: c, background: `${c}22`, border: `1px solid ${c}55` }}
    >
      {tier}
    </span>
  );
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.min(100, (score / 120) * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-border rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-accent-500 to-red-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-mono text-text-primary tabular-nums">
        {score.toFixed(0)}
      </span>
    </div>
  );
}

function ResultCard({
  result,
  selected,
  onClick,
}: {
  result: ScannerResult;
  selected: boolean;
  onClick: () => void;
}) {
  const themeLabel = result.theme ? THEME_LABEL[result.theme] ?? result.theme : null;
  const ret12 = result.return_12m;
  const retColor =
    ret12 === null ? 'text-text-secondary' : ret12 >= 0 ? 'text-green-400' : 'text-red-400';

  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-left bg-card border rounded-lg p-3 transition-colors ${
        selected ? 'border-accent-500' : 'border-border hover:border-text-secondary'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2">
          <span className="font-bold text-text-primary">{result.ticker}</span>
          <TierBadge tier={result.tier} />
        </div>
        <span className="text-[10px] text-text-secondary">
          {result.signals_fired} signals
        </span>
      </div>
      {themeLabel && (
        <div className="text-[10px] text-text-secondary mb-2">{themeLabel}</div>
      )}
      <ScoreBar score={result.composite_score} />
      <div className="flex items-center justify-between gap-2 mt-2 text-[11px]">
        <span className="text-text-secondary">
          {fmtMoney(result.price)} · {fmtMoney(result.market_cap)}
        </span>
        <span className={`font-semibold tabular-nums ${retColor}`}>
          12m {fmtPct(ret12)}
        </span>
      </div>
    </button>
  );
}

function DetailPanel({ result }: { result: ScannerResult | null }) {
  if (!result) {
    return (
      <div className="bg-card border border-border rounded-lg p-8 text-center text-text-secondary text-sm">
        Select a ticker to view signal breakdown.
      </div>
    );
  }

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-4 sticky top-4">
      <div>
        <div className="flex items-center justify-between gap-2 mb-1">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-bold text-text-primary">{result.ticker}</h3>
            <TierBadge tier={result.tier} />
          </div>
          <span className="text-xs text-text-secondary">
            Composite {result.composite_score.toFixed(0)}
          </span>
        </div>
        {result.company_name && (
          <p className="text-xs text-text-secondary">{result.company_name}</p>
        )}
        {result.theme && (
          <p className="text-[10px] text-text-secondary mt-1">
            {THEME_LABEL[result.theme] ?? result.theme}
          </p>
        )}
      </div>

      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-1">Rationale</div>
        <p className="text-xs text-text-primary leading-snug">{result.rationale}</p>
      </div>

      {/* Raw observations */}
      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-2">Observations</div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
          <Stat label="Price" value={fmtMoney(result.price)} />
          <Stat label="Market cap" value={fmtMoney(result.market_cap)} />
          <Stat
            label="12m return"
            value={fmtPct(result.return_12m, 0)}
            positive={result.return_12m ? result.return_12m >= 0 : undefined}
          />
          <Stat
            label="6m return"
            value={fmtPct(result.return_6m, 0)}
            positive={result.return_6m ? result.return_6m >= 0 : undefined}
          />
          <Stat
            label="Momentum pctile"
            value={
              result.momentum_percentile !== null
                ? `${result.momentum_percentile.toFixed(0)}%`
                : '—'
            }
          />
          <Stat
            label="Stock age"
            value={
              result.stock_age_months !== null
                ? `${result.stock_age_months.toFixed(0)}mo`
                : '—'
            }
          />
          <Stat
            label="Rev YoY (latest Q)"
            value={fmtPct(result.rev_growth_latest, 0)}
          />
          <Stat
            label="Rev YoY (prior Q)"
            value={fmtPct(result.rev_growth_prior, 0)}
          />
          <Stat
            label="Rev accel"
            value={
              result.rev_accel_pp !== null
                ? `${(result.rev_accel_pp * 100).toFixed(0)}pp`
                : '—'
            }
            positive={result.rev_accel_pp ? result.rev_accel_pp >= 0 : undefined}
          />
          <Stat
            label="Margin Δ"
            value={
              result.margin_delta_pp !== null
                ? `${(result.margin_delta_pp * 100).toFixed(1)}pp`
                : '—'
            }
            positive={
              result.margin_delta_pp ? result.margin_delta_pp >= 0 : undefined
            }
          />
          <Stat
            label="Price / avg PT"
            value={
              result.pt_chase_ratio !== null
                ? `${(result.pt_chase_ratio * 100).toFixed(0)}%`
                : '—'
            }
          />
          <Stat
            label="90d revisions"
            value={(result.revisions_90d ?? 0).toString()}
          />
        </div>
      </div>

      {/* Signals */}
      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-2">
          Signals Fired ({result.signals?.length ?? 0})
        </div>
        {!result.signals || result.signals.length === 0 ? (
          <p className="text-xs text-text-secondary italic">No positional signals firing.</p>
        ) : (
          <ul className="space-y-1.5">
            {result.signals.map((s, i) => (
              <li key={i} className="text-[11px]">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-text-primary">{s.signal}</span>
                  <span className="font-mono text-green-400">+{s.points}</span>
                </div>
                <p className="text-text-secondary leading-snug">{s.detail}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  positive,
}: {
  label: string;
  value: string;
  positive?: boolean;
}) {
  const color =
    positive === undefined
      ? 'text-text-primary'
      : positive
      ? 'text-green-400'
      : 'text-red-400';
  return (
    <>
      <span className="text-text-secondary">{label}</span>
      <span className={`font-mono text-right tabular-nums ${color}`}>{value}</span>
    </>
  );
}

export default function ScannerPage() {
  const [results, setResults] = useState<ScannerResult[]>([]);
  const [dates, setDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [tier, setTier] = useState<ScannerTier | 'ALL'>('ALL');
  const [theme, setTheme] = useState<string>('ALL');
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [runStatus, setRunStatus] = useState<ScannerRunStatus | null>(null);
  const [addInput, setAddInput] = useState('');
  const [addError, setAddError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchDates = useCallback(async () => {
    try {
      const d = await getScannerDates();
      setDates(d.dates);
      if (!selectedDate && d.dates.length > 0) {
        setSelectedDate(d.dates[0]);
      }
    } catch {
      // Non-fatal
    }
  }, [selectedDate]);

  const fetchResults = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await getScannerResults({
        date: selectedDate ?? undefined,
        tier: tier === 'ALL' ? undefined : tier,
        theme: theme === 'ALL' ? undefined : theme,
      });
      setResults(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch scanner results');
    } finally {
      setLoading(false);
    }
  }, [selectedDate, tier, theme]);

  useEffect(() => {
    fetchDates();
  }, [fetchDates]);

  useEffect(() => {
    fetchResults();
  }, [fetchResults]);

  // Poll run status when a run is active
  useEffect(() => {
    if (!runStatus?.running) return;
    const iv = setInterval(async () => {
      const s = await getScannerStatus();
      setRunStatus(s);
      if (!s.running) {
        clearInterval(iv);
        fetchDates();
        fetchResults();
      }
    }, 3000);
    return () => clearInterval(iv);
  }, [runStatus?.running, fetchDates, fetchResults]);

  const handleRun = async () => {
    const { status } = await runScanner();
    if (status === 'started' || status === 'already_running') {
      const s = await getScannerStatus();
      setRunStatus(s);
    }
  };

  const handleAdd = async () => {
    const t = addInput.trim().toUpperCase();
    if (!t) return;
    setAddError(null);
    try {
      await addScannerUniverse(t);
      setAddInput('');
      await fetchResults();
    } catch (e: unknown) {
      const msg =
        e && typeof e === 'object' && 'response' in e
          ? (e as { response: { data: { detail?: string } } }).response?.data?.detail
          : 'Failed to add ticker';
      setAddError(msg || 'Failed to add ticker');
    }
  };

  const handleRemove = async (ticker: string) => {
    if (!confirm(`Remove ${ticker} from scanner universe?`)) return;
    try {
      await removeScannerUniverse(ticker);
      await fetchResults();
    } catch {
      // Silent
    }
  };

  const themes = useMemo(() => {
    const set = new Set<string>();
    for (const r of results) if (r.theme) set.add(r.theme);
    return Array.from(set).sort();
  }, [results]);

  const tierCounts = useMemo(() => {
    const out: Record<string, number> = { HOT: 0, WATCH: 0, MONITOR: 0, IGNORE: 0 };
    for (const r of results) out[r.tier] = (out[r.tier] ?? 0) + 1;
    return out;
  }, [results]);

  const selected = results.find((r) => r.ticker === selectedTicker) ?? null;

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-7xl mx-auto px-4 py-6 space-y-5">
        {/* Header */}
        <div>
          <h1 className="text-2xl font-bold mb-1">Multi-bagger Scanner</h1>
          <p className="text-sm text-text-secondary">
            Positional signal stacker (3–12 month horizon). Scores a broader growth universe
            against six signals: revenue acceleration, margin expansion, long-horizon momentum,
            analyst chase, recent structural events, and 90-day revision clusters.
          </p>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-secondary">
            <span><span className="font-semibold text-[#f85149]">HOT</span> — 4+ signals AND composite ≥ 60 (strong confluence)</span>
            <span><span className="font-semibold text-[#d29922]">WATCH</span> — 3+ signals OR composite ≥ 40 (meaningful evidence)</span>
            <span><span className="font-semibold text-[#58a6ff]">MONITOR</span> — 2 signals fired (early / faint)</span>
            <span><span className="font-semibold text-[#484f58]">IGNORE</span> — 0–1 signals</span>
          </div>
        </div>

        {/* Controls */}
        <div className="bg-card border border-border rounded-lg p-3 flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-text-secondary">Run date</label>
            <select
              value={selectedDate ?? ''}
              onChange={(e) => setSelectedDate(e.target.value || null)}
              className="bg-page border border-border rounded text-xs px-2 py-1 text-text-primary"
            >
              {dates.length === 0 ? (
                <option value="">— none yet —</option>
              ) : (
                dates.map((d) => <option key={d} value={d}>{d}</option>)
              )}
            </select>
          </div>

          <div className="flex items-center gap-1">
            {TIERS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTier(t.key)}
                className={`text-[11px] font-medium px-2 py-0.5 rounded transition-colors ${
                  tier === t.key
                    ? 'bg-border text-text-primary'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
                style={tier === t.key ? { color: t.color } : undefined}
              >
                {t.label}
                {t.key !== 'ALL' && ` (${tierCounts[t.key] ?? 0})`}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <label className="text-[11px] text-text-secondary">Theme</label>
            <select
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
              className="bg-page border border-border rounded text-xs px-2 py-1 text-text-primary"
            >
              <option value="ALL">All themes</option>
              {themes.map((t) => (
                <option key={t} value={t}>
                  {THEME_LABEL[t] ?? t}
                </option>
              ))}
            </select>
          </div>

          <div className="ml-auto flex items-center gap-2">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleAdd();
              }}
              className="flex items-center gap-1"
            >
              <input
                type="text"
                value={addInput}
                onChange={(e) => {
                  setAddInput(e.target.value);
                  setAddError(null);
                }}
                placeholder="Add ticker..."
                className="w-28 bg-page border border-border rounded text-xs px-2 py-1 text-text-primary placeholder-text-secondary"
              />
              <button
                type="submit"
                disabled={!addInput.trim()}
                className="text-[11px] font-semibold px-2 py-1 rounded bg-accent-900/60 text-accent-300 hover:bg-accent-800/60 disabled:opacity-40 transition-colors"
              >
                Add
              </button>
            </form>
            <button
              onClick={handleRun}
              disabled={runStatus?.running}
              className="text-xs font-semibold px-3 py-1 rounded bg-green-900/60 text-green-300 hover:bg-green-800/60 disabled:opacity-50 transition-colors"
            >
              {runStatus?.running ? 'Scanning…' : 'Run Scan'}
            </button>
          </div>
        </div>

        {addError && (
          <div className="text-xs text-red-400">{addError}</div>
        )}

        {runStatus?.running && (
          <div className="bg-blue-900/20 border border-blue-900/40 rounded px-3 py-2 text-xs text-blue-300">
            Scanner running — this scores ~100 tickers and takes 2–5 minutes on a cold cache.
            Started at {runStatus.started_at}.
          </div>
        )}
        {runStatus?.last_result && !runStatus.running && runStatus.last_result.status === 'ok' && (
          <div className="bg-green-900/20 border border-green-900/40 rounded px-3 py-2 text-xs text-green-300">
            Last run: scored {runStatus.last_result.scored} · HOT {runStatus.last_result.hot} · WATCH {runStatus.last_result.watch}
          </div>
        )}
        {runStatus?.last_result?.status === 'error' && (
          <div className="bg-red-900/20 border border-red-900/40 rounded px-3 py-2 text-xs text-red-300">
            Error: {runStatus.last_result.error}
          </div>
        )}

        {/* Grid + detail — results widened to 5/7 so 4 cards fit per row at lg+ */}
        <div className="grid grid-cols-1 lg:grid-cols-7 gap-4">
          <div className="lg:col-span-5">
            {loading ? (
              <div className="flex items-center justify-center py-16 text-sm text-text-secondary">
                <div className="w-4 h-4 border-2 border-text-secondary border-t-transparent rounded-full animate-spin mr-2" />
                Loading…
              </div>
            ) : error ? (
              <div className="bg-red-900/20 border border-red-900/40 rounded p-3 text-sm text-red-300">
                {error}
              </div>
            ) : results.length === 0 ? (
              <div className="bg-card border border-border rounded-lg p-8 text-center text-sm text-text-secondary">
                {dates.length === 0 ? (
                  <>
                    No scanner runs yet. Click <span className="text-text-primary">Run Scan</span> to score the universe.
                  </>
                ) : (
                  'No results match the current filters.'
                )}
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-2.5">
                {results.map((r) => (
                  <div key={r.id} className="relative group">
                    <ResultCard
                      result={r}
                      selected={selectedTicker === r.ticker}
                      onClick={() => setSelectedTicker(r.ticker)}
                    />
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRemove(r.ticker);
                      }}
                      className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity text-[10px] text-red-400 bg-page/80 rounded px-1 hover:bg-red-900/40"
                      title={`Remove ${r.ticker} from scanner universe`}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="lg:col-span-2">
            <DetailPanel result={selected} />
          </div>
        </div>
      </div>
    </div>
  );
}
