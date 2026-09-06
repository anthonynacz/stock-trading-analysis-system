import { useState } from 'react';
import type { WatchlistChange } from '../types';

interface WatchlistChangesProps {
  entrants: WatchlistChange[];
  exiters: WatchlistChange[];
}

export default function WatchlistChanges({
  entrants,
  exiters,
}: WatchlistChangesProps) {
  const [expanded, setExpanded] = useState(false);

  if (entrants.length === 0 && exiters.length === 0) return null;

  return (
    <div className="bg-card border border-border rounded-lg">
      {/* Collapsed band — always visible */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-left hover:bg-border/30 transition-colors rounded-lg"
      >
        {/* Entrant badges */}
        {entrants.length > 0 && (
          <div className="flex items-start gap-1.5 min-w-0">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-green-400">
              +{entrants.length}
            </span>
            <div className="flex items-center gap-1 flex-wrap">
              {entrants.map((e) => (
                <span
                  key={e.ticker}
                  className="px-1.5 py-0.5 text-[11px] font-semibold rounded bg-green-900/40 text-green-400 border border-green-800/50"
                >
                  {e.ticker}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Separator dot when both exist */}
        {entrants.length > 0 && exiters.length > 0 && (
          <span className="w-1 h-1 rounded-full bg-border shrink-0" />
        )}

        {/* Exiter badges */}
        {exiters.length > 0 && (
          <div className="flex items-start gap-1.5 min-w-0">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-red-400">
              &minus;{exiters.length}
            </span>
            <div className="flex items-center gap-1 flex-wrap">
              {exiters.map((e) => (
                <span
                  key={e.ticker}
                  className="px-1.5 py-0.5 text-[11px] font-semibold rounded bg-red-900/40 text-red-400 border border-red-800/50"
                >
                  {e.ticker}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Expand chevron — pushed right */}
        <svg
          className={`w-3.5 h-3.5 text-text-secondary ml-auto shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="px-3 pb-2 pt-1 border-t border-border/60 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
          {entrants.map((e) => (
            <div key={e.ticker} className="flex items-start gap-2 py-1">
              <span className="w-1.5 h-1.5 mt-1.5 rounded-full bg-green-500 shrink-0" />
              <div className="min-w-0">
                <span className="text-xs font-bold text-text-primary">{e.ticker}</span>
                {e.sector && (
                  <span className="text-[10px] text-text-secondary ml-1.5">{e.sector}</span>
                )}
                {e.reason && (
                  <p className="text-[11px] text-text-secondary leading-snug truncate">
                    {e.reason}
                  </p>
                )}
              </div>
            </div>
          ))}
          {exiters.map((e) => (
            <div key={e.ticker} className="flex items-start gap-2 py-1">
              <span className="w-1.5 h-1.5 mt-1.5 rounded-full bg-red-500 shrink-0" />
              <div className="min-w-0">
                <span className="text-xs font-bold text-text-primary">{e.ticker}</span>
                {e.sector && (
                  <span className="text-[10px] text-text-secondary ml-1.5">{e.sector}</span>
                )}
                {e.reason && (
                  <p className="text-[11px] text-text-secondary leading-snug truncate">
                    {e.reason}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
