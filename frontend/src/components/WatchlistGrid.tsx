import type { Recommendation, WatchlistItem } from '../types';
import { ACTION_COLORS } from '../utils/theme';

interface WatchlistGridProps {
  items: WatchlistItem[];
  onTickerClick?: (ticker: string) => void;
  onRemove?: (ticker: string) => void;
  onToggleLock?: (ticker: string) => void;
  selectedTicker?: string | null;
  recommendations?: Map<string, Recommendation>;
}

/** Numeric rank for sorting: higher = more bullish (sorted first / left). */
function actionRank(action: string): number {
  switch (action) {
    case 'STRONG_BUY': return 5;
    case 'BUY': return 4;
    case 'HOLD': return 3;
    case 'SELL': return 2;
    case 'STRONG_SELL': return 1;
    default: return 0;
  }
}

function statusClasses(status: WatchlistItem['status']): string {
  switch (status) {
    case 'NEW_ENTRANT':
      return 'border-new-entrant';
    case 'REMOVED':
      return 'border-sell';
    default:
      return 'border-border';
  }
}

function StatusBadge({ status }: { status: WatchlistItem['status'] }) {
  if (status === 'NEW_ENTRANT') {
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-900/60 text-new-entrant">
        NEW
      </span>
    );
  }
  if (status === 'REMOVED') {
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-red-900/60 text-sell">
        REMOVED
      </span>
    );
  }
  return null;
}

export default function WatchlistGrid({ items, onTickerClick, onRemove, onToggleLock, selectedTicker, recommendations }: WatchlistGridProps) {
  if (items.length === 0) {
    return (
      <p className="text-text-secondary text-sm text-center py-8">
        No stocks on watchlist
      </p>
    );
  }

  const grouped = items.reduce<Record<string, WatchlistItem[]>>((acc, item) => {
    const sector = item.sector ?? 'Other';
    if (!acc[sector]) acc[sector] = [];
    acc[sector].push(item);
    return acc;
  }, {});

  // Sort each sector: greenest (STRONG_BUY / highest conviction) first
  for (const sectorItems of Object.values(grouped)) {
    sectorItems.sort((a, b) => {
      const recA = recommendations?.get(a.ticker);
      const recB = recommendations?.get(b.ticker);
      const rankA = recA ? actionRank(recA.action) : 0;
      const rankB = recB ? actionRank(recB.action) : 0;
      if (rankA !== rankB) return rankB - rankA; // higher rank first
      // Tie-break by conviction score within same action
      const convA = recA?.conviction_score ?? 0;
      const convB = recB?.conviction_score ?? 0;
      return convB - convA;
    });
  }

  return (
    <div className="space-y-6">
      {Object.entries(grouped).map(([sector, sectorItems]) => (
        <div key={sector}>
          <h3 className="text-text-secondary text-xs font-semibold uppercase tracking-wider mb-2">
            {sector}
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {sectorItems.map((item) => {
              const isSelected = selectedTicker === item.ticker;
              const rec = recommendations?.get(item.ticker);
              const indicatorColor = rec ? (ACTION_COLORS[rec.action] ?? '#21262d') : '#21262d';
              return (
                <button
                  key={item.id}
                  onClick={() => onTickerClick?.(item.ticker)}
                  className={`bg-card border ${isSelected ? 'border-new-entrant ring-1 ring-new-entrant/40' : item.rotation_protected && !isSelected ? 'border-cyan-700/60 ring-1 ring-cyan-500/20' : statusClasses(item.status)} rounded-lg p-3 text-left hover:bg-border/40 transition-colors relative group`}
                >
                  {/* Conviction indicator bar */}
                  <div
                    className="absolute top-0 left-2.5 w-8 h-[3px] rounded-br rounded-bl"
                    style={{ backgroundColor: indicatorColor }}
                  />

                  {/* Top-right action buttons on hover */}
                  {item.status !== 'REMOVED' && (
                    <div className="absolute top-1 right-1 hidden group-hover:flex items-center gap-0.5">
                      {onToggleLock && (
                        <span
                          role="button"
                          title={item.is_locked ? 'Unlock from rotation' : 'Lock from rotation'}
                          onClick={(e) => { e.stopPropagation(); onToggleLock(item.ticker); }}
                          className={`flex items-center justify-center w-5 h-5 rounded-full text-[11px] leading-none transition-colors ${
                            item.is_locked
                              ? 'bg-amber-900/60 text-amber-400 hover:bg-amber-800'
                              : 'bg-gray-800/80 text-text-secondary hover:bg-gray-700 hover:text-text-primary'
                          }`}
                        >
                          {item.is_locked ? '🔒' : '🔓'}
                        </span>
                      )}
                      {item.is_manual && onRemove && (
                        <span
                          role="button"
                          onClick={(e) => { e.stopPropagation(); onRemove(item.ticker); }}
                          className="flex items-center justify-center w-5 h-5 rounded-full bg-red-900/60 text-red-400 hover:bg-red-800 text-[10px] leading-none"
                        >
                          ×
                        </span>
                      )}
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-1">
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`text-sm font-bold text-text-primary ${item.status === 'REMOVED' ? 'line-through opacity-60' : ''}`}
                      >
                        {item.ticker}
                      </span>
                      {item.is_locked && (
                        <span className="text-[10px] text-amber-400" title="Locked from rotation">🔒</span>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      {item.rotation_protected && !item.is_manual && !item.is_locked && (
                        <span
                          className="text-[10px] text-cyan-400"
                          title={item.protection_reasons.join('; ')}
                        >
                          <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
                            <path d="M12 2L4 5v6.09c0 5.05 3.41 9.76 8 10.91 4.59-1.15 8-5.86 8-10.91V5l-8-3zm-1 15l-4-4 1.41-1.41L11 14.17l6.59-6.59L19 9l-8 8z" />
                          </svg>
                        </span>
                      )}
                      {item.is_manual && (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-purple-900/60 text-purple-400">
                          MANUAL
                        </span>
                      )}
                      <StatusBadge status={item.status} />
                    </div>
                  </div>
                  {item.company_name && (
                    <p className="text-text-secondary text-xs mt-1 truncate">
                      {item.company_name}
                    </p>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
