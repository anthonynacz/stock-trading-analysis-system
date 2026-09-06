import { memo, useMemo } from 'react';
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

interface CatalystGroup {
  date: string;
  fiscalQuarter: string | null;
  earningsTime: string | null;
  status: CatalystEvent['window_status'];
  daysUntil: number;
  events: CatalystEvent[];
}

/** Order earnings_time slots within the same date: pre-market → during → after-close → unknown. */
function timeSortKey(t: string | null): number {
  const u = (t || '').toUpperCase();
  if (u === 'BMO') return 0;
  if (u === 'AMC') return 2;
  if (!u) return 3;
  return 1;
}

function CatalystCalendar({ events }: CatalystCalendarProps) {
  const groups = useMemo<CatalystGroup[]>(() => {
    const upcoming = events.filter((e) => e.days_until <= 14);
    const map = new Map<string, CatalystGroup>();
    for (const e of upcoming) {
      const key = `${e.earnings_date}|${e.fiscal_quarter ?? ''}|${e.earnings_time ?? ''}`;
      const existing = map.get(key);
      if (existing) {
        existing.events.push(e);
      } else {
        map.set(key, {
          date: e.earnings_date,
          fiscalQuarter: e.fiscal_quarter,
          earningsTime: e.earnings_time,
          status: e.window_status,
          daysUntil: e.days_until,
          events: [e],
        });
      }
    }
    // Sort tickers within each group alphabetically for stable layout
    for (const g of map.values()) {
      g.events.sort((a, b) => a.ticker.localeCompare(b.ticker));
    }
    return Array.from(map.values()).sort((a, b) => {
      if (a.date !== b.date) return a.date.localeCompare(b.date);
      return timeSortKey(a.earningsTime) - timeSortKey(b.earningsTime);
    });
  }, [events]);

  if (groups.length === 0) {
    return (
      <p className="text-text-secondary text-sm text-center py-6">
        No upcoming catalysts
      </p>
    );
  }

  return (
    <div className="space-y-1.5">
      {groups.map((group, gi) => {
        const timeLabel = formatEarningsTime(group.earningsTime);
        const countdown = formatCountdown(group.daysUntil, group.earningsTime);
        return (
          <div
            key={`${group.date}-${group.fiscalQuarter}-${group.earningsTime}-${gi}`}
            className="bg-card/50 border border-border/50 rounded-lg px-3 py-2"
          >
            {/* Header: date · quarter · period — countdown — status */}
            <div className="flex items-center gap-2 flex-wrap mb-1.5">
              <span className="text-xs font-semibold text-text-primary">
                {formatDate(group.date)}
              </span>
              {group.fiscalQuarter && (
                <>
                  <span className="text-text-secondary/50">·</span>
                  <span className="text-xs text-text-secondary">{group.fiscalQuarter}</span>
                </>
              )}
              {timeLabel && (
                <>
                  <span className="text-text-secondary/50">·</span>
                  <span className="text-xs text-text-secondary">{timeLabel}</span>
                </>
              )}
              <span className="text-[10px] font-semibold text-amber-400 bg-amber-900/40 px-1.5 py-0.5 rounded font-mono ml-auto">
                {countdown}
              </span>
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${statusBadgeClasses(group.status)}`}>
                {group.status}
              </span>
            </div>

            {/* Tickers stacked horizontally with their EPS estimates */}
            <div className="flex flex-wrap gap-x-2 gap-y-1">
              {group.events.map((e) => (
                <span
                  key={`${e.ticker}-${e.earnings_date}`}
                  className="inline-flex items-baseline gap-1 text-xs bg-page rounded px-1.5 py-0.5 border border-border/40"
                  title={
                    e.consensus_eps != null
                      ? `${e.ticker} — Est. EPS $${e.consensus_eps.toFixed(2)}`
                      : `${e.ticker} — no EPS estimate`
                  }
                >
                  <span className="font-bold text-text-primary">{e.ticker}</span>
                  {e.consensus_eps != null ? (
                    <span className="text-[10px] font-mono text-text-secondary">
                      ${e.consensus_eps.toFixed(2)}
                    </span>
                  ) : (
                    <span className="text-[10px] text-text-secondary italic">—</span>
                  )}
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default memo(CatalystCalendar);
