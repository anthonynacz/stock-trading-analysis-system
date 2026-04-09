import type { WatchlistChange } from '../types';

interface WatchlistChangesProps {
  entrants: WatchlistChange[];
  exiters: WatchlistChange[];
}

function ChangeCard({
  item,
  type,
}: {
  item: WatchlistChange;
  type: 'entrant' | 'exiter';
}) {
  const isEntrant = type === 'entrant';
  return (
    <div
      className={`bg-card border rounded-lg p-3 ${
        isEntrant
          ? 'border-green-800/60'
          : 'border-red-800/60'
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <span
          className={`w-1.5 h-1.5 rounded-full shrink-0 ${
            isEntrant ? 'bg-green-500' : 'bg-red-500'
          }`}
        />
        <span className="text-sm font-bold text-text-primary">
          {item.ticker}
        </span>
        {item.sector && (
          <span className="text-[10px] text-text-secondary">
            {item.sector}
          </span>
        )}
      </div>
      {item.reason && (
        <p className="text-xs text-text-secondary ml-3.5 leading-relaxed">
          {item.reason}
        </p>
      )}
    </div>
  );
}

export default function WatchlistChanges({
  entrants,
  exiters,
}: WatchlistChangesProps) {
  if (entrants.length === 0 && exiters.length === 0) return null;

  return (
    <div className="space-y-3">
      {entrants.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-green-400 mb-2">
            + Entered ({entrants.length})
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {entrants.map((e) => (
              <ChangeCard key={e.ticker} item={e} type="entrant" />
            ))}
          </div>
        </div>
      )}
      {exiters.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-red-400 mb-2">
            - Removed ({exiters.length})
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {exiters.map((e) => (
              <ChangeCard key={e.ticker} item={e} type="exiter" />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
