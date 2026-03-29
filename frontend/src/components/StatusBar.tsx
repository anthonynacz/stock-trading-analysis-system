import type { SystemStatus } from '../types';

interface StatusBarProps {
  status: SystemStatus | null;
  onRefresh: () => void;
  refreshing: boolean;
}

export default function StatusBar({ status, onRefresh, refreshing }: StatusBarProps) {
  const lastRefresh = status?.last_refresh?.recommendations;

  return (
    <div className="sticky top-0 z-50 bg-card border-b border-border px-4 py-3 flex items-center gap-4">
      {/* Title */}
      <h1 className="text-lg font-bold text-text-primary">EdgeFlow</h1>

      {/* Date */}
      <span className="text-sm text-text-secondary mx-auto">
        {new Date().toLocaleDateString(undefined, {
          weekday: 'long',
          year: 'numeric',
          month: 'long',
          day: 'numeric',
        })}
      </span>

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
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-border text-text-primary text-xs font-medium hover:bg-text-secondary/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
