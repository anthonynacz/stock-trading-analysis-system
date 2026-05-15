import { useState, useMemo, useCallback, useEffect } from 'react';
import type { Recommendation, IndustryRecommendation } from '../types';
import {
  useWatchlist,
  useRecommendations,
  useNews,
  useCatalysts,
  useStatus,
  usePipelineDates,
  useWatchlistChanges,
} from '../hooks/useEdgeFlow';
import { addToWatchlist, removeFromWatchlist, toggleLockTicker, getIndustries } from '../utils/api';
import StatusBar from '../components/StatusBar';
import WatchlistChanges from '../components/WatchlistChanges';
import WatchlistGrid from '../components/WatchlistGrid';
import TickerDetail from '../components/TickerDetail';
import RecommendationCard from '../components/RecommendationCard';
import NewsTimeline from '../components/NewsTimeline';
import NewsModeSelector from '../components/NewsModeSelector';
import CatalystCalendar from '../components/CatalystCalendar';
import WatchlistStrikes from '../components/WatchlistStrikes';
import IndustryCard from '../components/IndustryCard';

function SectionLoading() {
  return (
    <div className="flex items-center justify-center py-8">
      <div className="w-5 h-5 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
      <span className="ml-2 text-sm text-text-secondary">Loading...</span>
    </div>
  );
}

function SectionError({ message }: { message: string }) {
  return (
    <div className="py-4 px-3 bg-red-900/20 border border-red-900/40 rounded text-sm text-red-400">
      {message}
    </div>
  );
}

export default function Dashboard() {
  const [selectedDate, setSelectedDate] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [addTickerInput, setAddTickerInput] = useState('');
  const [addingTicker, setAddingTicker] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [newsMode, setNewsMode] = useState<'general' | 'watchlist' | 'ticker'>('general');
  const [newsTicker, setNewsTicker] = useState('');
  const [newsIndustry, setNewsIndustry] = useState('');

  const pipelineDates = usePipelineDates();
  const watchlist = useWatchlist(undefined, selectedDate);
  const recommendations = useRecommendations(undefined, undefined, selectedDate);
  const watchlistChanges = useWatchlistChanges(selectedDate);
  const news = useNews({
    mode: newsMode,
    ticker: newsMode === 'ticker' && newsTicker ? newsTicker : undefined,
    industry: newsIndustry || undefined,
  });
  const catalysts = useCatalysts();
  const status = useStatus();

  const [industries, setIndustries] = useState<IndustryRecommendation[]>([]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await getIndustries(
          selectedDate === new Date().toISOString().slice(0, 10) ? undefined : selectedDate,
        );
        if (!cancelled) setIndustries(d);
      } catch {
        if (!cancelled) setIndustries([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedDate]);

  const industryNames = useMemo(
    () =>
      [...new Set(industries.map((i) => i.industry).filter(Boolean) as string[])].sort(),
    [industries],
  );

  // Find watchlist item for selected ticker
  const selectedItem = selectedTicker
    ? watchlist.data?.find((w) => w.ticker === selectedTicker)
    : undefined;
  const selectedCompany = selectedItem?.company_name ?? undefined;

  // Build ticker → recommendation map for watchlist card sorting/coloring
  const recMap = useMemo(() => {
    const map = new Map<string, Recommendation>();
    for (const rec of recommendations.data ?? []) {
      if (!map.has(rec.ticker)) map.set(rec.ticker, rec);
    }
    return map;
  }, [recommendations.data]);

  const handlePipelineComplete = useCallback(() => {
    watchlist.refetch();
    recommendations.refetch();
    watchlistChanges.refetch();
    news.refetch();
    catalysts.refetch();
    status.refetch();
    pipelineDates.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAddTicker = async () => {
    const ticker = addTickerInput.trim().toUpperCase();
    if (!ticker) return;
    setAddingTicker(true);
    setAddError(null);
    try {
      await addToWatchlist(ticker);
      setAddTickerInput('');
      watchlist.refetch();
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response: { data: { detail: string } } }).response?.data?.detail
          : 'Failed to add ticker';
      setAddError(msg || 'Failed to add ticker');
    } finally {
      setAddingTicker(false);
    }
  };

  const handleRemoveTicker = async (ticker: string) => {
    try {
      await removeFromWatchlist(ticker);
      watchlist.refetch();
      if (selectedTicker === ticker) setSelectedTicker(null);
    } catch {
      // Silently fail — ticker may already be removed
    }
  };

  const handleToggleLock = async (ticker: string) => {
    try {
      await toggleLockTicker(ticker);
      watchlist.refetch();
    } catch {
      // Silently fail
    }
  };

  return (
    <div className="min-h-screen bg-page text-text-primary">
      {/* Status Bar with Date Stepper */}
      <StatusBar
        status={status.data}
        onComplete={handlePipelineComplete}
        selectedDate={selectedDate}
        availableDates={pipelineDates.data?.dates ?? []}
        onDateChange={setSelectedDate}
      />

      <div className="max-w-7xl mx-auto px-3 py-4 sm:px-4 sm:py-6 space-y-6 sm:space-y-8">
        {/* Watchlist Changes (Entrants / Exiters) */}
        {watchlistChanges.data &&
          (watchlistChanges.data.entrants.length > 0 ||
            watchlistChanges.data.exiters.length > 0) && (
          <section>
            <h2 className="text-xl font-bold text-text-primary mb-4">
              Watchlist Changes
            </h2>
            <WatchlistChanges
              entrants={watchlistChanges.data.entrants}
              exiters={watchlistChanges.data.exiters}
            />
          </section>
        )}

        {/* Industry Recommendations */}
        {industries.length > 0 && (
          <section>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold text-text-primary">Industries</h2>
              <a
                href="/industries"
                className="text-xs text-text-secondary hover:text-text-primary transition-colors"
              >
                Detail view →
              </a>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
              {industries.map((i) => (
                <IndustryCard
                  key={i.id}
                  item={i}
                  linkTo={`/industries?industry=${encodeURIComponent(i.industry)}`}
                />
              ))}
            </div>
          </section>
        )}

        {/* Watchlist + Ticker Detail — two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          <section className="lg:col-span-3">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold text-text-primary">Watchlist</h2>
              <form
                onSubmit={(e) => { e.preventDefault(); handleAddTicker(); }}
                className="flex items-center gap-2"
              >
                <input
                  type="text"
                  value={addTickerInput}
                  onChange={(e) => { setAddTickerInput(e.target.value); setAddError(null); }}
                  placeholder="Add ticker..."
                  className="w-28 px-2 py-1 text-xs bg-card border border-border rounded text-text-primary placeholder-text-secondary focus:outline-none focus:border-new-entrant"
                />
                <button
                  type="submit"
                  disabled={addingTicker || !addTickerInput.trim()}
                  className="px-2 py-1 text-xs font-semibold rounded bg-accent-900/60 text-accent-400 hover:bg-accent-800/60 disabled:opacity-40 transition-colors"
                >
                  {addingTicker ? '...' : 'Add'}
                </button>
              </form>
            </div>
            {addError && (
              <p className="text-xs text-red-400 mb-2">{addError}</p>
            )}
            {watchlist.loading ? (
              <SectionLoading />
            ) : watchlist.error ? (
              <SectionError message={watchlist.error} />
            ) : (
              <WatchlistGrid
                items={watchlist.data ?? []}
                onTickerClick={setSelectedTicker}
                onRemove={handleRemoveTicker}
                onToggleLock={handleToggleLock}
                selectedTicker={selectedTicker}
                recommendations={recMap}
              />
            )}
          </section>

          <section className="lg:col-span-2">
            {selectedTicker ? (
              <TickerDetail
                ticker={selectedTicker}
                companyName={selectedCompany}
                selectedDate={selectedDate}
                onClose={() => setSelectedTicker(null)}
                rotationProtected={selectedItem?.rotation_protected}
                protectionReasons={selectedItem?.protection_reasons}
              />
            ) : (
              <div className="bg-card border border-border rounded-lg p-8 text-center text-text-secondary text-sm mt-9">
                Click a ticker to view details
              </div>
            )}
          </section>
        </div>

        {/* Recommendations */}
        <section>
          <h2 className="text-xl font-bold text-text-primary mb-4">Recommendations</h2>
          {recommendations.loading ? (
            <SectionLoading />
          ) : recommendations.error ? (
            <SectionError message={recommendations.error} />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
              {(recommendations.data ?? []).map((rec) => (
                <RecommendationCard key={rec.id} recommendation={rec} />
              ))}
              {recommendations.data?.length === 0 && (
                <p className="text-text-secondary text-sm text-center py-6 col-span-2">
                  No recommendations available
                </p>
              )}
            </div>
          )}
        </section>

        {/* Strike Scanner */}
        <section>
          <h2 className="text-xl font-bold text-text-primary mb-4">Strike Scanner</h2>
          <WatchlistStrikes selectedDate={selectedDate} />
        </section>

        {/* News + Catalysts two-column */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          <section className="lg:col-span-3">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-bold text-text-primary">News</h2>
              <NewsModeSelector
                mode={newsMode}
                onModeChange={setNewsMode}
                ticker={newsTicker}
                onTickerChange={setNewsTicker}
                watchlistTickers={(watchlist.data ?? []).map((w) => w.ticker)}
              />
            </div>
            <div className="bg-card border border-border rounded-lg p-4">
              {news.loading ? (
                <SectionLoading />
              ) : news.error ? (
                <SectionError message={news.error} />
              ) : (
                <NewsTimeline
                  items={news.data ?? []}
                  showTickers={newsMode !== 'general'}
                  industries={industryNames}
                  industry={newsIndustry}
                  onIndustryChange={setNewsIndustry}
                />
              )}
            </div>
          </section>

          <section className="lg:col-span-2">
            <h2 className="text-xl font-bold text-text-primary mb-4">Upcoming Catalysts</h2>
            <div className="bg-card border border-border rounded-lg p-4">
              {catalysts.loading ? (
                <SectionLoading />
              ) : catalysts.error ? (
                <SectionError message={catalysts.error} />
              ) : (
                <CatalystCalendar events={catalysts.data ?? []} />
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
