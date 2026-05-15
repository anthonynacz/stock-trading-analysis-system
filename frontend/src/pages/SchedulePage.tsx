import { useEffect, useMemo, useState } from 'react';
import {
  getScheduleRuns,
  getScheduleUpcoming,
  type ScheduleRun,
  type ScheduleUpcoming,
} from '../utils/api';

const PHASE_LABEL: Record<string, string> = {
  discovery: 'Universe Discovery',
  watchlist: 'Watchlist Rotation',
  ratings: 'Analyst Ratings',
  earnings: 'Earnings Calendar',
  news: 'News + Sentiment',
  options: 'Options Snapshots',
  recommendations: 'Recommendations',
  industries: 'Industry Recs',
  digest_dispatch: 'AM Digest',
  alerts_scan: 'Alerts Scan',
  retention_sweep: 'Retention Sweep',
};

const STATUS_STYLES: Record<string, string> = {
  RUNNING: 'bg-amber-900/40 text-amber-300 border-amber-500/30',
  SUCCESS: 'bg-emerald-900/40 text-emerald-300 border-emerald-500/30',
  FAILED:  'bg-red-900/40 text-red-300 border-red-500/30',
};

function StatusChip({ status }: { status: string }) {
  const cls = STATUS_STYLES[status] ?? 'bg-gray-800/60 text-text-secondary border-gray-700/60';
  return (
    <span className={`text-[10px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border ${cls}`}>
      {status}
    </span>
  );
}

function PhaseChip({ phase, userId }: { phase: string; userId: number | null }) {
  const label = PHASE_LABEL[phase] ?? phase;
  return (
    <span className="text-xs font-semibold text-text-primary">
      {label}
      {userId !== null && (
        <span className="ml-1 text-[10px] font-normal text-text-secondary">(user #{userId})</span>
      )}
    </span>
  );
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function fmtTimeOnly(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

function dayKey(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
  });
}

export default function SchedulePage() {
  const [runs, setRuns] = useState<ScheduleRun[] | null>(null);
  const [upcoming, setUpcoming] = useState<ScheduleUpcoming[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phaseFilter, setPhaseFilter] = useState<string>('all');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [r, u] = await Promise.all([
          getScheduleRuns(72),
          getScheduleUpcoming(24),
        ]);
        if (!cancelled) {
          setRuns(r);
          setUpcoming(u);
          setError(null);
        }
      } catch (e: unknown) {
        if (!cancelled) {
          const message = e instanceof Error ? e.message : 'Failed to load schedule';
          setError(message);
        }
      }
    }
    load();
    const interval = setInterval(load, 60_000); // refresh once a minute
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const phases = useMemo(() => {
    const set = new Set<string>();
    runs?.forEach((r) => set.add(r.phase));
    upcoming?.forEach((u) => set.add(u.phase));
    return Array.from(set).sort();
  }, [runs, upcoming]);

  const filteredRuns = useMemo(() => {
    if (!runs) return null;
    return phaseFilter === 'all' ? runs : runs.filter((r) => r.phase === phaseFilter);
  }, [runs, phaseFilter]);

  const filteredUpcoming = useMemo(() => {
    if (!upcoming) return null;
    return phaseFilter === 'all' ? upcoming : upcoming.filter((u) => u.phase === phaseFilter);
  }, [upcoming, phaseFilter]);

  // Group upcoming by day for display
  const upcomingByDay = useMemo(() => {
    const grouped: Record<string, ScheduleUpcoming[]> = {};
    (filteredUpcoming ?? []).forEach((u) => {
      const k = dayKey(u.fires_at);
      if (!grouped[k]) grouped[k] = [];
      grouped[k].push(u);
    });
    return grouped;
  }, [filteredUpcoming]);

  // Group runs by day
  const runsByDay = useMemo(() => {
    const grouped: Record<string, ScheduleRun[]> = {};
    (filteredRuns ?? []).forEach((r) => {
      if (!r.started_at) return;
      const k = dayKey(r.started_at);
      if (!grouped[k]) grouped[k] = [];
      grouped[k].push(r);
    });
    return grouped;
  }, [filteredRuns]);

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-5xl mx-auto px-3 py-4 sm:px-4 sm:py-6 space-y-5 sm:space-y-6">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold mb-1">Schedule</h1>
          <p className="text-sm text-text-secondary">
            Recent pipeline runs (last 72h) and upcoming fires (next 24h). Per-user rows
            (AM Digest, Alerts, Retention) are scoped to you; everything else is global.
          </p>
        </div>

        {error && (
          <div className="py-3 px-3 bg-red-900/20 border border-red-900/40 rounded text-sm text-red-400">
            {error}
          </div>
        )}

        {/* Phase filter */}
        {phases.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setPhaseFilter('all')}
              className={`px-3 py-1.5 sm:px-2.5 sm:py-1 text-[11px] font-semibold rounded transition-colors ${
                phaseFilter === 'all'
                  ? 'bg-accent-900/60 text-accent-300 border border-accent-600/60'
                  : 'bg-card border border-border text-text-secondary hover:text-text-primary'
              }`}
            >
              All
            </button>
            {phases.map((p) => (
              <button
                key={p}
                onClick={() => setPhaseFilter(p)}
                className={`px-3 py-1.5 sm:px-2.5 sm:py-1 text-[11px] font-semibold rounded transition-colors ${
                  phaseFilter === p
                    ? 'bg-accent-900/60 text-accent-300 border border-accent-600/60'
                    : 'bg-card border border-border text-text-secondary hover:text-text-primary'
                }`}
              >
                {PHASE_LABEL[p] ?? p}
              </button>
            ))}
          </div>
        )}

        {/* Upcoming */}
        <section className="space-y-2">
          <h2 className="text-sm font-bold text-text-secondary uppercase tracking-wider">
            Upcoming (next 24h)
          </h2>
          {upcoming === null && <p className="text-sm text-text-secondary">Loading…</p>}
          {upcoming !== null && (filteredUpcoming?.length ?? 0) === 0 && (
            <p className="text-sm text-text-secondary">Nothing scheduled in the next 24h for this filter.</p>
          )}
          {Object.entries(upcomingByDay).map(([day, fires]) => (
            <div key={day} className="space-y-1">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-text-secondary">
                {day}
              </div>
              <ul className="bg-card border border-border rounded-lg divide-y divide-border overflow-hidden">
                {fires.map((u, idx) => (
                  <li key={`${u.job_id}-${u.fires_at}-${idx}`} className="px-3 py-2 flex items-center gap-3">
                    <span className="font-mono text-xs text-text-primary tabular-nums w-14 shrink-0">
                      {fmtTimeOnly(u.fires_at)}
                    </span>
                    <PhaseChip phase={u.phase} userId={null} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>

        {/* Recent runs */}
        <section className="space-y-2">
          <h2 className="text-sm font-bold text-text-secondary uppercase tracking-wider">
            Recent (last 72h)
          </h2>
          {runs === null && <p className="text-sm text-text-secondary">Loading…</p>}
          {runs !== null && (filteredRuns?.length ?? 0) === 0 && (
            <p className="text-sm text-text-secondary">No runs in the last 72h for this filter.</p>
          )}
          {Object.entries(runsByDay).map(([day, dayRuns]) => (
            <div key={day} className="space-y-1">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-text-secondary">
                {day}
              </div>
              <ul className="bg-card border border-border rounded-lg divide-y divide-border overflow-hidden">
                {dayRuns.map((r) => (
                  <li key={r.id} className="px-3 py-2 flex items-start gap-3">
                    <span className="font-mono text-xs text-text-primary tabular-nums w-14 shrink-0 pt-0.5">
                      {r.started_at ? fmtTimeOnly(r.started_at) : '—'}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <PhaseChip phase={r.phase} userId={r.user_id} />
                        <StatusChip status={r.status} />
                        <span className="text-[11px] text-text-secondary font-mono tabular-nums">
                          {fmtDuration(r.duration_ms)}
                        </span>
                      </div>
                      {r.error_message && (
                        <p className="mt-1 text-[11px] text-red-400 break-words">{r.error_message}</p>
                      )}
                      {r.meta && Object.keys(r.meta).length > 0 && (
                        <details className="mt-1">
                          <summary className="cursor-pointer text-[10px] text-text-secondary hover:text-text-primary">
                            details
                          </summary>
                          <pre className="mt-1 text-[10px] text-text-secondary bg-page/60 rounded p-2 overflow-x-auto">
                            {JSON.stringify(r.meta, null, 2)}
                          </pre>
                        </details>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>

        <p className="text-[10px] text-text-secondary text-center pt-2">
          Last refreshed {fmtTime(new Date().toISOString())}. Page polls once per minute.
        </p>
      </div>
    </div>
  );
}
