import { useEffect, useMemo, useState } from 'react';
import {
  getOutcomesSummary,
  type OutcomeBucket,
  type OutcomesSummary,
} from '../utils/api';

const WINDOWS = [30, 90, 180] as const;
const HORIZON_LABEL: Record<string, string> = {
  t1: 'T+1',
  t5: 'T+5',
  t20: 'T+20',
};

const ACTION_COLORS: Record<string, string> = {
  STRONG_BUY: 'text-emerald-400',
  BUY: 'text-emerald-300',
  HOLD: 'text-amber-300',
  SELL: 'text-red-300',
  STRONG_SELL: 'text-red-400',
};

function pct(v: number | null | undefined, digits = 0): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

function ret(v: number | null | undefined): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function hitClass(rate: number | null | undefined): string {
  if (rate == null) return 'text-text-secondary';
  if (rate >= 0.55) return 'text-emerald-400';
  if (rate >= 0.45) return 'text-amber-300';
  return 'text-red-400';
}

function retClass(v: number | null | undefined): string {
  if (v == null) return 'text-text-secondary';
  return v >= 0 ? 'text-emerald-400' : 'text-red-400';
}

function HorizonTile({ label, bucket }: { label: string; bucket: OutcomeBucket }) {
  return (
    <div className="bg-card border border-border rounded-lg p-4 flex-1 min-w-[140px]">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-text-secondary mb-2">
        {label} · {bucket.directional_n} directional calls
      </div>
      <div className={`text-2xl font-bold tabular-nums ${hitClass(bucket.hit_rate)}`}>
        {pct(bucket.hit_rate)}
      </div>
      <div className="text-[11px] text-text-secondary mt-1">
        hit rate ·{' '}
        <span className={`font-semibold tabular-nums ${retClass(bucket.avg_adj_return_pct)}`}>
          {ret(bucket.avg_adj_return_pct)}
        </span>{' '}
        avg direction-adjusted return
      </div>
    </div>
  );
}

export default function PerformancePage() {
  const [days, setDays] = useState<number>(90);
  const [data, setData] = useState<OutcomesSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [signalSort, setSignalSort] = useState<'hit_rate' | 'n' | 'return'>('hit_rate');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getOutcomesSummary(days)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load outcomes');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days]);

  const sortedSignals = useMemo(() => {
    if (!data) return [];
    const sigs = [...data.signals];
    if (signalSort === 'n') sigs.sort((a, b) => b.t5.n - a.t5.n);
    else if (signalSort === 'return')
      sigs.sort((a, b) => (b.t5.avg_adj_return_pct ?? -999) - (a.t5.avg_adj_return_pct ?? -999));
    else sigs.sort((a, b) => (b.t5.hit_rate ?? 0) - (a.t5.hit_rate ?? 0));
    return sigs;
  }, [data, signalSort]);

  const hasData = data !== null && data.rows > 0;

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-5xl mx-auto px-3 py-4 sm:px-4 sm:py-6 space-y-5 sm:space-y-6">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold mb-1">Performance</h1>
            <p className="text-sm text-text-secondary max-w-2xl">
              Forward-return scoring of past recommendations at 1, 5, and 20 trading days.
              A directional call is a "hit" when the spot return matches its direction
              (BUYs up, SELLs down). Per-signal rows attribute each rec's outcome to the
              signals that fired, sign-adjusted by the signal's points.
            </p>
          </div>
          <div className="flex gap-1.5">
            {WINDOWS.map((w) => (
              <button
                key={w}
                onClick={() => setDays(w)}
                className={`px-3 py-1.5 text-[11px] font-semibold rounded transition-colors ${
                  days === w
                    ? 'bg-accent-900/60 text-accent-300 border border-accent-600/60'
                    : 'bg-card border border-border text-text-secondary hover:text-text-primary'
                }`}
              >
                {w}d
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div className="py-3 px-3 bg-red-900/20 border border-red-900/40 rounded text-sm text-red-400">
            {error}
          </div>
        )}

        {loading && data === null && <p className="text-sm text-text-secondary">Loading…</p>}

        {data !== null && !hasData && (
          <div className="bg-card border border-border rounded-lg p-6 text-sm text-text-secondary">
            No scored outcomes yet. The nightly scoring job runs at 18:00 ET on weekdays and
            backfills the last 180 days of recommendations on its first run — check back after
            the next run, or trigger it from the backend.
          </div>
        )}

        {hasData && (
          <>
            {/* Overall */}
            <section className="space-y-2">
              <h2 className="text-sm font-bold text-text-secondary uppercase tracking-wider">
                Overall · {data.rows} scored recommendations
              </h2>
              <div className="flex gap-3 flex-wrap">
                {Object.entries(data.overall).map(([h, bucket]) => (
                  <HorizonTile key={h} label={HORIZON_LABEL[h] ?? h} bucket={bucket} />
                ))}
              </div>
            </section>

            {/* By action */}
            <section className="space-y-2">
              <h2 className="text-sm font-bold text-text-secondary uppercase tracking-wider">
                By action
              </h2>
              <div className="bg-card border border-border rounded-lg overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-text-secondary border-b border-border">
                      <th className="px-3 py-2">Action</th>
                      {['t1', 't5', 't20'].map((h) => (
                        <th key={h} className="px-3 py-2 text-right">
                          {HORIZON_LABEL[h]} hit / avg ret
                        </th>
                      ))}
                      <th className="px-3 py-2 text-right">N</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {Object.entries(data.by_action).map(([action, buckets]) => (
                      <tr key={action}>
                        <td className={`px-3 py-2 font-semibold ${ACTION_COLORS[action] ?? ''}`}>
                          {action}
                        </td>
                        {['t1', 't5', 't20'].map((h) => {
                          const b = buckets[h];
                          return (
                            <td key={h} className="px-3 py-2 text-right tabular-nums">
                              <span className={hitClass(b?.hit_rate)}>{pct(b?.hit_rate)}</span>
                              <span className="text-text-secondary"> · </span>
                              <span className={retClass(b?.avg_return_pct)}>
                                {ret(b?.avg_return_pct)}
                              </span>
                            </td>
                          );
                        })}
                        <td className="px-3 py-2 text-right tabular-nums text-text-secondary">
                          {buckets.t20?.n ?? buckets.t5?.n ?? buckets.t1?.n ?? 0}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] text-text-secondary">
                HOLD rows show raw returns but no hit rate (no direction to be right about).
              </p>
            </section>

            {/* Per-signal */}
            <section className="space-y-2">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <h2 className="text-sm font-bold text-text-secondary uppercase tracking-wider">
                  Signal hit rates
                </h2>
                <div className="flex gap-1.5">
                  {(
                    [
                      ['hit_rate', 'By hit rate'],
                      ['return', 'By avg return'],
                      ['n', 'By sample size'],
                    ] as const
                  ).map(([key, label]) => (
                    <button
                      key={key}
                      onClick={() => setSignalSort(key)}
                      className={`px-2.5 py-1 text-[11px] font-semibold rounded transition-colors ${
                        signalSort === key
                          ? 'bg-accent-900/60 text-accent-300 border border-accent-600/60'
                          : 'bg-card border border-border text-text-secondary hover:text-text-primary'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {sortedSignals.length === 0 ? (
                <p className="text-sm text-text-secondary">
                  No signals with enough matured samples yet (min 3 at T+5).
                </p>
              ) : (
                <div className="bg-card border border-border rounded-lg overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-[10px] uppercase tracking-wider text-text-secondary border-b border-border">
                        <th className="px-3 py-2">Signal</th>
                        <th className="px-3 py-2 text-right">Avg pts</th>
                        <th className="px-3 py-2 text-right">T+5 hit</th>
                        <th className="px-3 py-2 text-right">T+5 avg adj ret</th>
                        <th className="px-3 py-2 text-right">T+20 hit</th>
                        <th className="px-3 py-2 text-right">T+20 avg adj ret</th>
                        <th className="px-3 py-2 text-right">N (T+5)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {sortedSignals.map((s) => (
                        <tr key={s.name}>
                          <td className="px-3 py-2 font-semibold text-text-primary">{s.name}</td>
                          <td
                            className={`px-3 py-2 text-right tabular-nums ${
                              s.avg_points >= 0 ? 'text-emerald-300' : 'text-red-300'
                            }`}
                          >
                            {s.avg_points >= 0 ? '+' : ''}
                            {s.avg_points}
                          </td>
                          <td className={`px-3 py-2 text-right tabular-nums ${hitClass(s.t5.hit_rate)}`}>
                            {pct(s.t5.hit_rate)}
                          </td>
                          <td
                            className={`px-3 py-2 text-right tabular-nums ${retClass(s.t5.avg_adj_return_pct)}`}
                          >
                            {ret(s.t5.avg_adj_return_pct)}
                          </td>
                          <td className={`px-3 py-2 text-right tabular-nums ${hitClass(s.t20.hit_rate)}`}>
                            {pct(s.t20.hit_rate)}
                          </td>
                          <td
                            className={`px-3 py-2 text-right tabular-nums ${retClass(s.t20.avg_adj_return_pct)}`}
                          >
                            {ret(s.t20.avg_adj_return_pct)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-text-secondary">
                            {s.t5.n}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="text-[10px] text-text-secondary">
                A signal is "right" when the forward return has the same sign as its points —
                bearish signals score on drops. Use these to tune Signal Weights in Settings:
                boost groups whose signals sustain &gt;55% at T+5, mute persistent &lt;45% ones.
              </p>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
