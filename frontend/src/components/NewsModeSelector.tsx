interface NewsModeSelectorProps {
  mode: 'general' | 'watchlist' | 'ticker';
  onModeChange: (mode: 'general' | 'watchlist' | 'ticker') => void;
  ticker: string;
  onTickerChange: (ticker: string) => void;
  watchlistTickers: string[];
}

const MODES = [
  { key: 'general' as const, label: 'All' },
  { key: 'watchlist' as const, label: 'Watchlist' },
  { key: 'ticker' as const, label: 'Ticker' },
];

export default function NewsModeSelector({
  mode,
  onModeChange,
  ticker,
  onTickerChange,
  watchlistTickers,
}: NewsModeSelectorProps) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-0.5">
        {MODES.map((m) => (
          <button
            key={m.key}
            onClick={() => onModeChange(m.key)}
            className={`px-2.5 py-1 text-[11px] font-semibold rounded transition-colors ${
              mode === m.key
                ? 'bg-purple-900/60 text-purple-300 border border-purple-600/60'
                : 'bg-card border border-border text-text-secondary hover:text-text-primary'
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {mode === 'ticker' && (
        <select
          value={ticker}
          onChange={(e) => onTickerChange(e.target.value)}
          className="bg-card border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-purple-600/60"
        >
          <option value="">Select ticker...</option>
          {watchlistTickers.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      )}
    </div>
  );
}
