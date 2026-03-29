import { useMemo } from 'react';
import type { CatalystEvent } from '../types';

interface CatalystCalendarProps {
  events: CatalystEvent[];
}

function statusBadgeClasses(status: CatalystEvent['window_status']): string {
  switch (status) {
    case 'APPROACHING':
      return 'bg-amber-900/60 text-amber-400';
    case 'ACTIVE':
      return 'bg-green-900/60 text-green-400';
    case 'POST':
      return 'bg-gray-800 text-text-secondary';
  }
}

export default function CatalystCalendar({ events }: CatalystCalendarProps) {
  const upcoming = useMemo(() => {
    return [...events]
      .filter((e) => e.days_until <= 14)
      .sort((a, b) => a.earnings_date.localeCompare(b.earnings_date));
  }, [events]);

  if (upcoming.length === 0) {
    return (
      <p className="text-text-secondary text-sm text-center py-6">
        No upcoming catalysts
      </p>
    );
  }

  return (
    <div className="space-y-1">
      {upcoming.map((event, i) => (
        <div
          key={`${event.ticker}-${event.earnings_date}-${i}`}
          className="flex items-center gap-3 py-2 border-b border-border/50 last:border-0"
        >
          <span className="text-xs text-text-secondary w-20 flex-shrink-0">
            {event.earnings_date}
          </span>
          <span className="text-sm font-bold text-text-primary w-14 flex-shrink-0">
            {event.ticker}
          </span>
          {event.earnings_time && (
            <span className="text-xs text-text-secondary w-8 flex-shrink-0">
              {event.earnings_time}
            </span>
          )}
          <span className="text-xs font-mono text-text-secondary">
            {event.days_until === 0
              ? 'Today'
              : event.days_until === 1
                ? '1 day'
                : `${event.days_until} days`}
          </span>
          <span className={`ml-auto px-2 py-0.5 rounded text-[10px] font-medium ${statusBadgeClasses(event.window_status)}`}>
            {event.window_status}
          </span>
        </div>
      ))}
    </div>
  );
}
