import type { WatchlistChange } from '../types';

interface WatchlistChangesProps {
  entrants: WatchlistChange[];
  exiters: WatchlistChange[];
}

export default function WatchlistChanges({ entrants, exiters }: WatchlistChangesProps) {
  if (entrants.length === 0 && exiters.length === 0) {
    return (
      <div className="bg-card border border-border rounded-lg px-4 py-2.5 text-text-secondary text-sm text-center">
        No watchlist changes
      </div>
    );
  }

  return (
    <div className="bg-card border border-border rounded-lg px-4 py-2.5 flex items-center gap-4 text-sm flex-wrap">
      {entrants.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-green-500 font-bold text-xs">+</span>
          <span className="text-text-secondary">Entered:</span>
          <div className="flex items-center gap-1.5">
            {entrants.map((e) => (
              <span
                key={e.ticker}
                className="px-1.5 py-0.5 rounded bg-green-900/30 text-green-400 font-semibold text-xs"
              >
                {e.ticker}
              </span>
            ))}
          </div>
        </div>
      )}

      {entrants.length > 0 && exiters.length > 0 && (
        <div className="w-px h-4 bg-border" />
      )}

      {exiters.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-red-500 font-bold text-xs">-</span>
          <span className="text-text-secondary">Removed:</span>
          <div className="flex items-center gap-1.5">
            {exiters.map((e) => (
              <span
                key={e.ticker}
                className="px-1.5 py-0.5 rounded bg-red-900/30 text-red-400 font-semibold text-xs"
              >
                {e.ticker}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
