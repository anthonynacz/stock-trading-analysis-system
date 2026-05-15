import { useState, useCallback, useEffect } from 'react';
import type { ResearchResult } from '../types';
import { getResearchResults, deleteResearchResult } from '../utils/api';
import { ACTION_COLORS, getActionLabel } from '../utils/theme';
import { useResearchContext } from '../contexts/ResearchContext';
import ResearchDetail from '../components/ResearchDetail';

function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleDateString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function ResearchCard({
  result,
  selected,
  onClick,
  onDelete,
}: {
  result: ResearchResult;
  selected: boolean;
  onClick: () => void;
  onDelete: () => void;
}) {
  const borderColor = ACTION_COLORS[result.action] ?? '#21262d';
  const conviction = result.conviction_score ?? 0;

  return (
    <div
      onClick={onClick}
      className={`bg-card border rounded-lg p-3 cursor-pointer transition-all hover:border-text-secondary group relative ${
        selected ? 'border-accent-500 ring-1 ring-accent-500/30' : 'border-border'
      }`}
    >
      {/* Delete button */}
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="absolute top-2 right-2 p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-red-900/40 transition-all text-text-secondary hover:text-red-400"
        title="Delete"
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>

      <div className="flex items-center gap-2 mb-2">
        <span className="text-sm font-bold text-text-primary">{result.ticker}</span>
        <span
          className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase"
          style={{ backgroundColor: borderColor + '22', color: borderColor }}
        >
          {getActionLabel(result.action)}
        </span>
      </div>

      {result.company_name && (
        <p className="text-[10px] text-text-secondary mb-1.5 truncate">{result.company_name}</p>
      )}

      {/* Conviction bar */}
      <div className="flex items-center gap-2 mb-1.5">
        <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
          <div
            className={`h-full rounded-full ${conviction >= 0 ? 'bg-green-500' : 'bg-red-500'}`}
            style={{ width: `${Math.min(Math.abs(conviction), 100)}%` }}
          />
        </div>
        <span className="text-[10px] font-mono text-text-secondary w-6 text-right">
          {conviction}
        </span>
      </div>

      <div className="flex items-center justify-between text-[10px] text-text-secondary">
        <span>
          {result.current_price != null ? `$${result.current_price.toFixed(2)}` : '—'}
        </span>
        <span>{formatTimestamp(result.analyzed_at)}</span>
      </div>
    </div>
  );
}

export default function ResearchPage() {
  const {
    analyzingTicker,
    analyzeError,
    lastResult,
    startAnalysis,
    consumeResult,
    clearError,
  } = useResearchContext();

  const [tickerInput, setTickerInput] = useState('');
  const [results, setResults] = useState<ResearchResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filterTicker, setFilterTicker] = useState('');

  const fetchResults = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getResearchResults(filterTicker || undefined);
      setResults(data);
    } catch {
      // Silently fail — empty grid is fine
    } finally {
      setLoading(false);
    }
  }, [filterTicker]);

  useEffect(() => {
    fetchResults();
  }, [fetchResults]);

  // When a new analysis completes, refresh grid and select the new result
  useEffect(() => {
    if (lastResult) {
      setSelectedId(lastResult.id);
      consumeResult();
      fetchResults();
    }
  }, [lastResult, consumeResult, fetchResults]);

  const handleAnalyze = () => {
    const ticker = tickerInput.trim().toUpperCase();
    if (!ticker || analyzingTicker) return;
    clearError();
    setTickerInput('');
    startAnalysis(ticker);
  };

  const handleReanalyze = (ticker: string) => {
    if (analyzingTicker) return;
    clearError();
    startAnalysis(ticker);
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteResearchResult(id);
      if (selectedId === id) setSelectedId(null);
      setResults((prev) => prev.filter((r) => r.id !== id));
    } catch {
      // Silently fail
    }
  };

  const selectedResult = results.find((r) => r.id === selectedId) ?? null;

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Analyze input */}
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold text-text-primary">Research</h1>
          <form
            onSubmit={(e) => { e.preventDefault(); handleAnalyze(); }}
            className="flex items-center gap-2 ml-auto"
          >
            <input
              type="text"
              value={tickerInput}
              onChange={(e) => { setTickerInput(e.target.value); clearError(); }}
              placeholder="Enter ticker..."
              disabled={!!analyzingTicker}
              className="w-32 px-3 py-1.5 text-sm bg-card border border-border rounded text-text-primary placeholder-text-secondary focus:outline-none focus:border-accent-500 disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!!analyzingTicker || !tickerInput.trim()}
              className="px-4 py-1.5 text-sm font-semibold rounded bg-accent-900/60 text-accent-400 hover:bg-accent-800/60 disabled:opacity-40 transition-colors flex items-center gap-2"
            >
              {analyzingTicker ? (
                <>
                  <div className="w-3.5 h-3.5 border-2 border-accent-400 border-t-transparent rounded-full animate-spin" />
                  Analyzing...
                </>
              ) : (
                'Analyze'
              )}
            </button>
          </form>
        </div>

        {analyzeError && (
          <p className="text-xs text-red-400">{analyzeError}</p>
        )}

        {analyzingTicker && (
          <div className="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
            <div className="w-4 h-4 border-2 border-accent-400 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm text-text-secondary">
              Running full analysis for <span className="text-text-primary font-semibold">{analyzingTicker}</span>...
              This may take 30-60 seconds.
            </span>
          </div>
        )}

        {/* Grid + Detail layout */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          {/* Left: Grid of results */}
          <div className="lg:col-span-3 space-y-3">
            {/* Filter */}
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={filterTicker}
                onChange={(e) => setFilterTicker(e.target.value)}
                placeholder="Filter by ticker..."
                className="w-36 px-2 py-1 text-xs bg-card border border-border rounded text-text-primary placeholder-text-secondary focus:outline-none focus:border-accent-500"
              />
              {filterTicker && (
                <button
                  onClick={() => setFilterTicker('')}
                  className="text-xs text-text-secondary hover:text-text-primary"
                >
                  Clear
                </button>
              )}
              <span className="text-xs text-text-secondary ml-auto">
                {results.length} result{results.length !== 1 ? 's' : ''}
              </span>
            </div>

            {loading ? (
              <div className="flex items-center justify-center py-12">
                <div className="w-5 h-5 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
              </div>
            ) : results.length === 0 ? (
              <div className="bg-card border border-border rounded-lg p-8 text-center text-text-secondary text-sm">
                {filterTicker
                  ? `No research results for "${filterTicker.toUpperCase()}"`
                  : 'No research results yet. Enter a ticker above to analyze.'}
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                {results.map((r) => (
                  <ResearchCard
                    key={r.id}
                    result={r}
                    selected={r.id === selectedId}
                    onClick={() => setSelectedId(r.id === selectedId ? null : r.id)}
                    onDelete={() => handleDelete(r.id)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Right: Detail panel */}
          <div className="lg:col-span-2">
            {selectedResult ? (
              <ResearchDetail
                result={selectedResult}
                onClose={() => setSelectedId(null)}
                onReanalyze={handleReanalyze}
                analyzing={analyzingTicker === selectedResult.ticker}
              />
            ) : (
              <div className="bg-card border border-border rounded-lg p-8 text-center text-text-secondary text-sm">
                Click a result to view details
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
