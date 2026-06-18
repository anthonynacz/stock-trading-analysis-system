interface SelectionActionBarProps {
  count: number;
  onRotateOut: () => void;
  onClear: () => void;
}

/**
 * Floating action bar shown when one or more tickers are selected for
 * rotate-out (from the watchlist grid and/or the recommendations list).
 */
export default function SelectionActionBar({ count, onRotateOut, onClear }: SelectionActionBarProps) {
  if (count <= 0) return null;
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 bg-card border border-amber-500/40 rounded-full shadow-lg px-4 py-2">
      <span className="text-sm text-text-primary font-semibold whitespace-nowrap">
        {count} selected
      </span>
      <button
        type="button"
        onClick={onRotateOut}
        className="px-3 py-1.5 text-xs font-semibold rounded-full bg-amber-500 text-black hover:bg-amber-400 transition-colors whitespace-nowrap"
      >
        Rotate out ({count})
      </button>
      <button
        type="button"
        onClick={onClear}
        className="px-2 py-1.5 text-xs font-semibold rounded-full bg-border text-text-secondary hover:text-text-primary transition-colors"
      >
        Clear
      </button>
    </div>
  );
}
