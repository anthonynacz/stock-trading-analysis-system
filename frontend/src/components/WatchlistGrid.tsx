import type { WatchlistItem } from '../types';

interface WatchlistGridProps {
  items: WatchlistItem[];
  onTickerClick?: (ticker: string) => void;
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

export default function WatchlistGrid({ items, onTickerClick }: WatchlistGridProps) {
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

  return (
    <div className="space-y-6">
      {Object.entries(grouped).map(([sector, sectorItems]) => (
        <div key={sector}>
          <h3 className="text-text-secondary text-xs font-semibold uppercase tracking-wider mb-2">
            {sector}
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
            {sectorItems.map((item) => (
              <button
                key={item.id}
                onClick={() => onTickerClick?.(item.ticker)}
                className={`bg-card border ${statusClasses(item.status)} rounded-lg p-3 text-left hover:bg-border/40 transition-colors`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span
                    className={`text-sm font-bold text-text-primary ${item.status === 'REMOVED' ? 'line-through opacity-60' : ''}`}
                  >
                    {item.ticker}
                  </span>
                  <StatusBadge status={item.status} />
                </div>
                {item.company_name && (
                  <p className="text-text-secondary text-xs mt-1 truncate">
                    {item.company_name}
                  </p>
                )}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
