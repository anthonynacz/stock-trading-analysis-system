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

export default function StatusBar({
  status,
  onComplete,
  selectedDate,
  availableDates,
  onDateChange,
}: StatusBarProps) {
  const [downloading, setDownloading] = useState(false);
  const lastRefresh = status?.last_refresh?.recommendations;
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
    <div className="sticky top-0 z-50 bg-card border-b border-border px-4 py-3 flex items-center gap-4">
      {/* Title */}
      <h1 className="text-lg font-bold text-text-primary">EdgeFlow</h1>

      {/* Date stepper */}
      <div className="flex items-center gap-2 mx-auto">
        <button
          onClick={goOlder}
          disabled={!canGoOlder}
          className="p-1 rounded hover:bg-border/60 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          title="Previous day"
        >
          <svg className="w-4 h-4 text-text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        <span className={`text-sm select-none ${isToday ? 'text-text-secondary' : 'text-amber-400'}`}>
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
          className="p-1 rounded hover:bg-border/60 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          title="Next day"
        >
          <svg className="w-4 h-4 text-text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </button>

        {!isToday && (
          <button
            onClick={goToday}
            className="ml-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-blue-900/60 text-new-entrant hover:bg-blue-800/60 transition-colors"
          >
            Today
          </button>
        )}
      </div>

      {/* Status indicators */}
      <div className="flex items-center gap-3">
        {/* Export PDF */}
        <button
          onClick={handleExportPDF}
          disabled={downloading}
          className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium bg-border/60 text-text-primary hover:bg-border transition-colors disabled:opacity-40"
          title={`Export PDF report for ${formatDate(selectedDate)}`}
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3M3 17V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
          </svg>
          {downloading ? 'Generating...' : 'Export PDF'}
        </button>

        {/* Last refresh */}
        {lastRefresh && (
          <span className="text-xs text-text-secondary">
            Updated {new Date(lastRefresh).toLocaleTimeString()}
          </span>
        )}

        {/* DB indicator */}
        {status && (
          <div className="flex items-center gap-1" title={status.db_connected ? 'DB connected' : 'DB disconnected'}>
            <div
              className={`w-2 h-2 rounded-full ${status.db_connected ? 'bg-green-500' : 'bg-red-500'}`}
            />
            <span className="text-[10px] text-text-secondary">DB</span>
          </div>
        )}

        {/* Scheduler indicator */}
        {status && (
          <div className="flex items-center gap-1" title={status.scheduler_running ? 'Scheduler running' : 'Scheduler stopped'}>
            <div
              className={`w-2 h-2 rounded-full ${status.scheduler_running ? 'bg-green-500' : 'bg-red-500'}`}
            />
            <span className="text-[10px] text-text-secondary">Sched</span>
          </div>
        )}

        {/* Pipeline runner */}
        {isToday && <PipelineRunner onComplete={onComplete} />}
      </div>
    </div>
  );
}
