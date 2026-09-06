import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
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
import SelectionActionBar from '../components/SelectionActionBar';
import RotationPreviewModal from '../components/RotationPreviewModal';
import NewsTimeline from '../components/NewsTimeline';
import NewsModeSelector from '../components/NewsModeSelector';
import CatalystCalendar from '../components/CatalystCalendar';
import WatchlistStrikes from '../components/WatchlistStrikes';
import IndustryCard from '../components/IndustryCard';
import { AddTickerForm } from '../components/AddTickerForm';
import { LoadingRow, ErrorBox, EmptyCard } from '../components/ui/feedback';
import { SegmentedControl, type SegmentOption } from '../components/ui/SegmentedControl';

type RecSort = 'conviction' | 'revised_at';

const REC_SORT_OPTIONS: SegmentOption<RecSort>[] = [
  { key: 'conviction', label: 'Top conviction', title: 'Sort by conviction score' },
  {
    key: 'revised_at',
    label: 'Recently revised',
    title: 'Sort by most recently rescored (intraday news triggers)',
  },
];

export default function Dashboard() {
  const [selectedDate, setSelectedDate] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  // Rotate-out selection — a set of tickers checked across the watchlist grid
  // and the recommendations list (keyed by ticker so the two views stay in sync).
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [rotateModalTickers, setRotateModalTickers] = useState<string[] | null>(null);
  const [newsMode, setNewsMode] = useState<'general' | 'watchlist' | 'ticker'>('general');
  const [newsTicker, setNewsTicker] = useState('');
  const [newsIndustry, setNewsIndustry] = useState('');
  const [recSort, setRecSort] = useState<RecSort>(() => {
    const stored = localStorage.getItem('vela.rec_sort');
    return stored === 'revised_at' ? 'revised_at' : 'conviction';
  });
  useEffect(() => {
    localStorage.setItem('vela.rec_sort', recSort);
  }, [recSort]);

  // Lock body scroll while the TickerDetail mobile overlay is open. Desktop
  // (lg+) still keeps the panel in-grid; this is purely a no-op there.
  useEffect(() => {
    if (!selectedTicker) return;
    const prev = document.body.style.overflow;
    if (window.matchMedia('(max-width: 1023px)').matches) {
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.body.style.overflow = prev;
    };
  }, [selectedTicker]);

  const pipelineDates = usePipelineDates();
  const watchlist = useWatchlist(undefined, selectedDate);
  const recommendations = useRecommendations(undefined, undefined, selectedDate, recSort);
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

  // Tickers eligible for rotate-out selection: on the active watchlist, not
  // removed, and not locked. Drives the checkbox on both the watchlist grid
  // and the recommendation cards.
  const selectableTickers = useMemo(() => {
    const s = new Set<string>();
    for (const w of watchlist.data ?? []) {
      if (w.status !== 'REMOVED' && !w.is_locked) s.add(w.ticker);
    }
    return s;
  }, [watchlist.data]);

  const toggleSelect = useCallback((ticker: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(ticker)) next.delete(ticker);
      else next.add(ticker);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelected(new Set()), []);

  // The hooks' refetch identities change with selectedDate/recSort; route the
  // stable StatusBar callback through a ref so it never calls a stale closure.
  const refetchAll = () => {
    watchlist.refetch();
    recommendations.refetch();
    watchlistChanges.refetch();
    news.refetch();
    catalysts.refetch();
    status.refetch();
    pipelineDates.refetch();
  };
  const refetchAllRef = useRef(refetchAll);
  useEffect(() => {
    refetchAllRef.current = refetchAll;
  });
  const handlePipelineComplete = useCallback(() => refetchAllRef.current(), []);

  const handleAddTicker = useCallback(
    async (ticker: string) => {
      await addToWatchlist(ticker);
      watchlist.refetch();
    },
    [watchlist.refetch],
  );

  const handleRemoveTicker = useCallback(
    async (ticker: string) => {
      try {
        await removeFromWatchlist(ticker);
        watchlist.refetch();
        if (selectedTicker === ticker) setSelectedTicker(null);
      } catch {
        // Silently fail — ticker may already be removed
      }
    },
    [watchlist.refetch, selectedTicker],
  );

  const handleToggleLock = useCallback(
    async (ticker: string) => {
      try {
        await toggleLockTicker(ticker);
        watchlist.refetch();
      } catch {
        // Silently fail
      }
    },
    [watchlist.refetch],
  );

  const closeDetail = useCallback(() => setSelectedTicker(null), []);

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
              <Link
                to="/industries"
                className="text-xs text-text-secondary hover:text-text-primary transition-colors"
              >
                Detail view →
              </Link>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
              {industries.map((i) => (
                <IndustryCard
                  key={i.id}
                  item={i}
                  compact
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
              <AddTickerForm onAdd={handleAddTicker} />
            </div>
            {watchlist.loading && !watchlist.data ? (
              <LoadingRow />
            ) : watchlist.error ? (
              <ErrorBox message={watchlist.error} />
            ) : (
              <WatchlistGrid
                items={watchlist.data ?? []}
                onTickerClick={setSelectedTicker}
                onRemove={handleRemoveTicker}
                onToggleLock={handleToggleLock}
                selectedTicker={selectedTicker}
                recommendations={recMap}
                selected={selected}
                onToggleSelect={toggleSelect}
              />
            )}
          </section>

          <section className="lg:col-span-2">
            {selectedTicker ? (
              <div
                className="fixed inset-0 z-40 bg-page overflow-y-auto p-3
                           lg:static lg:inset-auto lg:z-auto lg:bg-transparent lg:overflow-visible lg:p-0"
              >
                <TickerDetail
                  ticker={selectedTicker}
                  companyName={selectedCompany}
                  selectedDate={selectedDate}
                  onClose={closeDetail}
                  rotationProtected={selectedItem?.rotation_protected}
                  protectionReasons={selectedItem?.protection_reasons}
                />
              </div>
            ) : (
              <EmptyCard className="hidden lg:block mt-9">Click a ticker to view details</EmptyCard>
            )}
          </section>
        </div>

        {/* Recommendations */}
        <section>
          <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
            <h2 className="text-xl font-bold text-text-primary">Recommendations</h2>
            <SegmentedControl
              variant="joined"
              options={REC_SORT_OPTIONS}
              value={recSort}
              onChange={setRecSort}
            />
          </div>
          {recommendations.loading && !recommendations.data ? (
            <LoadingRow />
          ) : recommendations.error ? (
            <ErrorBox message={recommendations.error} />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
              {(recommendations.data ?? []).map((rec) => (
                <RecommendationCard
                  key={rec.id}
                  recommendation={rec}
                  selectable={selectableTickers.has(rec.ticker)}
                  selected={selected.has(rec.ticker)}
                  onToggleSelect={toggleSelect}
                  onOpenDetail={setSelectedTicker}
                />
              ))}
              {recommendations.data?.length === 0 && (
                <EmptyCard className="md:col-span-2">No recommendations available</EmptyCard>
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
              {news.loading && !news.data ? (
                <LoadingRow />
              ) : news.error ? (
                <ErrorBox message={news.error} />
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
              {catalysts.loading && !catalysts.data ? (
                <LoadingRow />
              ) : catalysts.error ? (
                <ErrorBox message={catalysts.error} />
              ) : (
                <CatalystCalendar events={catalysts.data ?? []} />
              )}
            </div>
          </section>
        </div>
      </div>

      {/* Rotate-out selection bar + preview/confirm modal */}
      <SelectionActionBar
        count={selected.size}
        onRotateOut={() => setRotateModalTickers([...selected])}
        onClear={clearSelection}
      />
      {rotateModalTickers && (
        <RotationPreviewModal
          tickers={rotateModalTickers}
          onClose={() => setRotateModalTickers(null)}
          onCommitted={() => {
            setRotateModalTickers(null);
            clearSelection();
            watchlist.refetch();
            recommendations.refetch();
            watchlistChanges.refetch();
          }}
        />
      )}
    </div>
  );
}
