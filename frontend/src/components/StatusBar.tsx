import { useState } from 'react';
import type { SystemStatus } from '../types';
import { downloadReport } from '../utils/api';
import PipelineRunner from './PipelineRunner';

interface StatusBarProps {
  status: SystemStatus | null;
  onComplete: () => void;
  selectedDate: string;
  availableDates: string[];
  onDateChange: (date: string) => void;
}

function formatDate(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number);
  const d = new Date(year, month - 1, day);
  return d.toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

function formatRelative(iso: string): string {
  const diffMin = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  return `${Math.round(diffMin / 60)}h ago`;
}

function SystemHealthPill({ status }: { status: SystemStatus | null }) {
  if (!status) return null;

  const lastRefresh = status.last_refresh?.recommendations;
  const lastNews = status.last_refresh?.intraday_news;
  const db = status.db_connected;
  const sched = status.scheduler_running;

  // Aggregate: green if both core systems up, amber if partial, red if both down.
  const healthy = db && sched;
  const degraded = (db && !sched) || (!db && sched);
  const tone = healthy
    ? { dot: 'bg-green-500', text: 'text-green-400', label: 'OK' }
    : degraded
      ? { dot: 'bg-amber-500', text: 'text-amber-400', label: 'DEGRADED' }
      : { dot: 'bg-red-500', text: 'text-red-400', label: 'DOWN' };

  // Tooltip rows: aligned label / value pairs. Each row degrades gracefully
  // when the underlying value is null (haven't run yet).
  const rows: { label: string; value: string; ok: boolean }[] = [
    {
      label: 'Updated',
      value: lastRefresh ? new Date(lastRefresh).toLocaleTimeString() : '—',
      ok: lastRefresh != null,
    },
    {
      label: 'News scan',
      value: lastNews ? formatRelative(lastNews) : '—',
      ok: lastNews != null,
    },
    {
      label: 'Database',
      value: db ? 'connected' : 'disconnected',
      ok: db,
    },
    {
      label: 'Scheduler',
      value: sched ? 'running' : 'stopped',
      ok: sched,
    },
  ];

  return (
    <span className="relative group inline-flex items-center">
      <span
        className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-full bg-border/40 hover:bg-border/70 cursor-default transition-colors`}
        aria-label={`System status: ${tone.label}`}
      >
        <span className={`w-2 h-2 rounded-full ${tone.dot}`} />
        <span className={`hidden sm:inline text-[10px] font-bold tracking-wider ${tone.text}`}>
          {tone.label}
        </span>
      </span>

      {/* Hover tooltip — same pattern used elsewhere (RecommendationCard) */}
      <div className="absolute top-full right-0 mt-2 hidden group-hover:block z-50 pointer-events-none">
        <div className="bg-gray-900 border border-border text-text-primary text-[11px] px-3 py-2 rounded shadow-lg min-w-[200px]">
          <div className="text-text-secondary uppercase tracking-wider text-[9px] mb-1.5">
            System status
          </div>
          <div className="space-y-1">
            {rows.map((r) => (
              <div key={r.label} className="flex items-center justify-between gap-3">
                <span className="text-text-secondary">{r.label}</span>
                <span className={`font-mono ${r.ok ? 'text-text-primary' : 'text-amber-400'}`}>
                  {r.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </span>
  );
}

export default function StatusBar({
  status,
  onComplete,
  selectedDate,
  availableDates,
  onDateChange,
}: StatusBarProps) {
  const [downloading, setDownloading] = useState(false);
  const todayStr = new Date().toISOString().slice(0, 10);
  const isToday = selectedDate === todayStr;

  const handleExportPDF = async () => {
    setDownloading(true);
    try {
      await downloadReport(selectedDate);
    } catch (err) {
      console.error('PDF export failed:', err);
    } finally {
      setDownloading(false);
    }
  };

  // Find position in sorted (descending) date list.
  // idx === -1 means the selected date isn't in availableDates — typically
  // happens when the user lands on today but today's pipeline hasn't run yet.
  // In that case treat it as "newer than all available" so the left arrow
  // can jump back to the most recent available run.
  const idx = availableDates.indexOf(selectedDate);
  const isUnknownDate = idx === -1;
  const canGoNewer = idx > 0;
  const canGoOlder =
    (isUnknownDate && availableDates.length > 0) ||
    (idx >= 0 && idx < availableDates.length - 1);

  const goOlder = () => {
    if (!canGoOlder) return;
    if (isUnknownDate) onDateChange(availableDates[0]);
    else onDateChange(availableDates[idx + 1]);
  };
  const goNewer = () => {
    if (canGoNewer) onDateChange(availableDates[idx - 1]);
  };
  const goToday = () => onDateChange(todayStr);

  return (
    <div className="sticky top-0 z-50 bg-card border-b border-border px-3 py-2 sm:px-4 sm:py-2.5 flex items-center gap-3 sm:gap-4">
      {/* LEFT: Date stepper — anchors left, was previously centered with EdgeFlow */}
      {/*       chrome competing for the left edge. */}
      <div className="flex items-center gap-1 sm:gap-2">
        <button
          onClick={goOlder}
          disabled={!canGoOlder}
          className="p-2 sm:p-1 rounded hover:bg-border/60 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          title="Previous day"
          aria-label="Previous day"
        >
          <svg className="w-4 h-4 text-text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        <span className={`text-xs sm:text-sm select-none ${isToday ? 'text-text-primary font-medium' : 'text-amber-400'}`}>
          {formatDate(selectedDate)}
          {!isToday && (
            <span className="ml-1.5 text-[10px] uppercase tracking-wider opacity-70">
              (historical)
            </span>
          )}
        </span>

        <button
          onClick={goNewer}
          disabled={!canGoNewer}
          className="p-2 sm:p-1 rounded hover:bg-border/60 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          title="Next day"
          aria-label="Next day"
        >
          <svg className="w-4 h-4 text-text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </button>

        {!isToday && (
          <button
            onClick={goToday}
            className="ml-1 px-2 py-1 sm:py-0.5 rounded text-[10px] font-semibold bg-blue-900/60 text-new-entrant hover:bg-blue-800/60 transition-colors"
          >
            Today
          </button>
        )}
      </div>

      {/* Spacer pushes the action zone to the right edge */}
      <div className="flex-1" />

      {/* RIGHT: Action zone — Export, Run, then a single grouped health pill. */}
      <div className="flex items-center gap-2 sm:gap-3">
        <button
          onClick={handleExportPDF}
          disabled={downloading}
          className="flex items-center gap-1 p-1.5 sm:px-2.5 sm:py-1 rounded text-xs font-medium bg-border/60 text-text-primary hover:bg-border transition-colors disabled:opacity-40"
          title={`Export PDF report for ${formatDate(selectedDate)}`}
          aria-label="Export PDF report"
        >
          <svg className="w-4 h-4 sm:w-3.5 sm:h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3M3 17V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
          </svg>
          <span className="hidden sm:inline">{downloading ? 'Generating...' : 'Export PDF'}</span>
        </button>

        {isToday && <PipelineRunner onComplete={onComplete} />}

        <SystemHealthPill status={status} />
      </div>
    </div>
  );
}
