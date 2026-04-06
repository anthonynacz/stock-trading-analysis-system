import type { SystemStatus } from '../types';

interface StatusBarProps {
  status: SystemStatus | null;
  onRefresh: () => void;
  refreshing: boolean;
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
  onRefresh,
  refreshing,
  selectedDate,
  availableDates,
  onDateChange,
}: StatusBarProps) {
  const lastRefresh = status?.last_refresh?.recommendations;
  const todayStr = new Date().toISOString().slice(0, 10);
  const isToday = selectedDate === todayStr;

  // Find position in sorted (descending) date list
  const idx = availableDates.indexOf(selectedDate);
  const canGoNewer = idx > 0;
  const canGoOlder = idx >= 0 && idx < availableDates.length - 1;

  const goOlder = () => {
    if (canGoOlder) onDateChange(availableDates[idx + 1]);
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

        {/* Refresh button */}
        <button
          onClick={onRefresh}
          disabled={refreshing || !isToday}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-border text-text-primary text-xs font-medium hover:bg-text-secondary/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          title={!isToday ? 'Refresh only available for today' : undefined}
        >
          <svg
            className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>
    </div>
  );
}
