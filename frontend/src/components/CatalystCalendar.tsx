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

/** Format "BMO" / "AMC" into readable labels, or null if unknown. */
function formatEarningsTime(time: string | null): string | null {
  if (!time) return null;
  const t = time.toUpperCase();
  if (t === 'BMO') return 'Pre-market';
  if (t === 'AMC') return 'After-close';
  if (t === 'TAS' || t === 'TNS') return null;
  return time;
}

/** Convert days_until into a "Xd Yh" style string relative to now. */
function formatCountdown(daysUntil: number, earningsTime: string | null): string {
  if (daysUntil < 0) return 'Passed';
  if (daysUntil === 0) return 'Today';

  // Approximate hours based on earnings timing
  // BMO ~ 9:30 ET, AMC ~ 16:00 ET, unknown ~ 12:00 ET
  const now = new Date();
  const targetDate = new Date(now);
  targetDate.setDate(targetDate.getDate() + daysUntil);

  const t = earningsTime?.toUpperCase();
  if (t === 'BMO') {
    targetDate.setHours(9, 30, 0);
  } else if (t === 'AMC') {
    targetDate.setHours(16, 0, 0);
  } else {
    targetDate.setHours(12, 0, 0);
  }

  const diffMs = targetDate.getTime() - now.getTime();
  if (diffMs <= 0) return 'Today';

  const totalHours = Math.floor(diffMs / (1000 * 60 * 60));
  const days = Math.floor(totalHours / 24);
  const hours = totalHours % 24;

  if (days === 0) return `${hours}h`;
  if (hours === 0) return `${days}d`;
  return `${days}d ${hours}h`;
}

/** Format earnings_date as "Mon Apr 14" */
function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
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
    <div className="space-y-2">
      {upcoming.map((event, i) => {
        const timeLabel = formatEarningsTime(event.earnings_time);
        const countdown = formatCountdown(event.days_until, event.earnings_time);

        return (
          <div
            key={`${event.ticker}-${event.earnings_date}-${i}`}
            className="bg-card/50 border border-border/50 rounded-lg px-3 py-2.5"
          >
            {/* Top row: ticker, countdown badge, window status */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold text-text-primary">
                  {event.ticker}
                </span>
                <span className="text-xs font-semibold text-amber-400 bg-amber-900/40 px-1.5 py-0.5 rounded font-mono">
                  {countdown}
                </span>
              </div>
              <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${statusBadgeClasses(event.window_status)}`}>
                {event.window_status}
              </span>
            </div>

            {/* Detail row: date, time, quarter, EPS */}
            <div className="flex items-center gap-2 mt-1.5 text-xs text-text-secondary flex-wrap">
              <span>Earnings {formatDate(event.earnings_date)}</span>
              {timeLabel && (
                <>
                  <span className="text-border">·</span>
                  <span>{timeLabel}</span>
                </>
              )}
              {event.fiscal_quarter && (
                <>
                  <span className="text-border">·</span>
                  <span>{event.fiscal_quarter}</span>
                </>
              )}
              {event.consensus_eps != null && (
                <>
                  <span className="text-border">·</span>
                  <span>
                    Est. EPS{' '}
                    <span className="font-mono text-text-primary">
                      ${event.consensus_eps.toFixed(2)}
                    </span>
                  </span>
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
