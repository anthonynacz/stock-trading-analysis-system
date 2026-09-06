import { SegmentedControl } from './ui/SegmentedControl';

type NewsMode = 'general' | 'watchlist' | 'ticker';

interface NewsModeSelectorProps {
  mode: NewsMode;
  onModeChange: (mode: NewsMode) => void;
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
      <SegmentedControl<NewsMode> options={MODES} value={mode} onChange={onModeChange} />

      {mode === 'ticker' && (
        <select
          value={ticker}
          onChange={(e) => onTickerChange(e.target.value)}
          className="bg-card border border-border rounded px-2 py-1 text-xs text-text-primary focus:border-accent-600/60"
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
