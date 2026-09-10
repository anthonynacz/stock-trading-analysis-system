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
  useBreakingNews,
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
import BreakingNews, { type DetailFocus } from '../components/BreakingNews';
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

type RecGroup = 'flat' | 'entry';

const REC_GROUP_OPTIONS: SegmentOption<RecGroup>[] = [
  { key: 'flat', label: 'All', title: 'One flat list' },
  { key: 'entry', label: 'By entry', title: 'Group BUY / STRONG_BUY by entry strategy' },
];

const POSITIVE_ACTIONS = new Set(['STRONG_BUY', 'BUY']);

/** Display order + copy for the engine's entry strategies (see backend/services/CLAUDE.md § Entry Strategy). */
const ENTRY_GROUPS: { key: string; label: string; hint: string }[] = [
  { key: 'PRE_POSITION', label: 'Pre-position', hint: 'Enter ahead of the catalyst' },
  { key: 'REACTIVE', label: 'Reactive', hint: 'Catalyst already in motion — enter on confirmation' },
  { key: 'WAIT', label: 'Wait', hint: 'Bullish but waiting for confirmation' },
];

function groupByEntry(recs: Recommendation[]) {
  const positive = recs.filter((r) => POSITIVE_ACTIONS.has(r.action));
  const others = recs.filter((r) => !POSITIVE_ACTIONS.has(r.action));
  const known = new Set(ENTRY_GROUPS.map((g) => g.key));
  const groups = ENTRY_GROUPS.map((g) => ({
    ...g,
    recs: positive.filter((r) => (r.entry_strategy ?? 'WAIT') === g.key),
  }));
  const unknown = positive.filter((r) => r.entry_strategy && !known.has(r.entry_strategy));
  for (const r of unknown) {
    const key = r.entry_strategy as string;
    const existing = groups.find((g) => g.key === key);
    if (existing) existing.recs.push(r);
    else groups.push({ key, label: key.replace(/_/g, ' '), hint: '', recs: [r] });
  }
  return { groups: groups.filter((g) => g.recs.length > 0), others };
}

export default function Dashboard() {
  const [selectedDate, setSelectedDate] = useState(
    new Date().toISOString().slice(0, 10),
  );
  const [selectedTicker, setSelectedTicker] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get('ticker')?.toUpperCase() || null,
  );
  const [detailFocus, setDetailFocus] = useState<DetailFocus>(null);
  const openDetail = useCallback((ticker: string, focus: DetailFocus = null) => {
    setDetailFocus(focus);
    setSelectedTicker(ticker);
  }, []);
  // Rotate-out selection — a set of tickers checked across the watchlist grid
  // and the recommendations list (keyed by ticker so the two views stay in sync).
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [rotateModalTickers, setRotateModalTickers] = useState<string[] | null>(null);
  const [newsMode, setNewsMode] = useState<'general' | 'watchlist' | 'ticker'>('general');
  const [newsTicker, setNewsTicker] = useState('');
  const [newsIndustry, setNewsIndustry] = useState('');
  const [watchlistOpen, setWatchlistOpen] = useState<boolean>(
    () => localStorage.getItem('vela.watchlist_open') === '1',
  );
  useEffect(() => {
    localStorage.setItem('vela.watchlist_open', watchlistOpen ? '1' : '0');
  }, [watchlistOpen]);
  const detailRef = useRef<HTMLElement | null>(null);
  const [recSort, setRecSort] = useState<RecSort>(() => {
    const stored = localStorage.getItem('vela.rec_sort');
    return stored === 'revised_at' ? 'revised_at' : 'conviction';
  });
  useEffect(() => {
    localStorage.setItem('vela.rec_sort', recSort);
  }, [recSort]);
  const [recGroup, setRecGroup] = useState<RecGroup>(() =>
    localStorage.getItem('vela.rec_group') === 'entry' ? 'entry' : 'flat',
  );
  useEffect(() => {
    localStorage.setItem('vela.rec_group', recGroup);
  }, [recGroup]);

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
  useEffect(() => {
    if (!selectedTicker || !window.matchMedia('(min-width: 1024px)').matches) return;
    const el = detailRef.current;
    if (el && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }, [selectedTicker]);

  const pipelineDates = usePipelineDates();
  const watchlist = useWatchlist(undefined, selectedDate);
  const recommendations = useRecommendations(undefined, undefined, selectedDate, recSort);
  const watchlistChanges = useWatchlistChanges(selectedDate);
  const breaking = useBreakingNews(4, 12);
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
  const recGroups = useMemo(
    () => groupByEntry(recommendations.data ?? []),
    [recommendations.data],
  );
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

  const closeDetail = useCallback(() => {
    setSelectedTicker(null);
    setDetailFocus(null);
  }, []);

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
        {/* Breaking news — material headlines for watchlist + held tickers */}
        <BreakingNews
          items={breaking.data}
          loading={breaking.loading}
          error={breaking.error}
          hours={4}
          onOpenDetail={openDetail}
        />

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

        {/* Recommendations + Ticker Detail — two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          <section className="lg:col-span-3">
            <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
              <h2 className="text-xl font-bold text-text-primary">Recommendations</h2>
              <div className="flex items-center gap-2 flex-wrap">
                <SegmentedControl
                  variant="joined"
                  options={REC_GROUP_OPTIONS}
                  value={recGroup}
                  onChange={setRecGroup}
                />
                <SegmentedControl
                  variant="joined"
                  options={REC_SORT_OPTIONS}
                  value={recSort}
                  onChange={setRecSort}
                />
              </div>
            </div>
            {recommendations.loading && !recommendations.data ? (
              <LoadingRow />
            ) : recommendations.error ? (
              <ErrorBox message={recommendations.error} />
            ) : recommendations.data?.length === 0 ? (
              <EmptyCard>No recommendations available</EmptyCard>
            ) : recGroup === 'flat' ? (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-2.5">
                {(recommendations.data ?? []).map((rec) => (
                  <RecommendationCard
                    key={rec.id}
                    recommendation={rec}
                    selectable={selectableTickers.has(rec.ticker)}
                    selected={selected.has(rec.ticker)}
                    onToggleSelect={toggleSelect}
                    onOpenDetail={openDetail}
                  />
                ))}
              </div>
            ) : (
              <div className="space-y-5">
                {recGroups.groups.map((g) => (
                  <div key={g.key}>
                    <div className="flex items-baseline gap-2 mb-2">
                      <h3 className="text-xs font-bold uppercase tracking-wider text-accent-300">
                        {g.label}
                      </h3>
                      <span className="text-[10px] text-text-secondary">
                        {g.recs.length}
                        {g.hint ? ` · ${g.hint}` : ''}
                      </span>
                    </div>
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-2.5">
                      {g.recs.map((rec) => (
                        <RecommendationCard
                          key={rec.id}
                          recommendation={rec}
                          selectable={selectableTickers.has(rec.ticker)}
                          selected={selected.has(rec.ticker)}
                          onToggleSelect={toggleSelect}
                          onOpenDetail={openDetail}
                        />
                      ))}
                    </div>
                  </div>
                ))}
                {recGroups.groups.length === 0 && (
                  <EmptyCard>No BUY / STRONG_BUY recommendations today</EmptyCard>
                )}
                {recGroups.others.length > 0 && (
                  <details className="group">
                    <summary className="cursor-pointer text-xs font-bold uppercase tracking-wider text-text-secondary hover:text-text-primary transition-colors mb-2">
                      Hold / Sell ({recGroups.others.length})
                    </summary>
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-2.5">
                      {recGroups.others.map((rec) => (
                        <RecommendationCard
                          key={rec.id}
                          recommendation={rec}
                          selectable={selectableTickers.has(rec.ticker)}
                          selected={selected.has(rec.ticker)}
                          onToggleSelect={toggleSelect}
                          onOpenDetail={openDetail}
                        />
                      ))}
                    </div>
                  </details>
                )}
              </div>
            )}
          </section>

          <section className="lg:col-span-2" ref={detailRef}>
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
                  focus={detailFocus}
                />
              </div>
            ) : (
              <EmptyCard className="hidden lg:block mt-11">
                Open a recommendation's details or pick a watchlist ticker
              </EmptyCard>
            )}
          </section>
        </div>

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
        {/* Watchlist — collapsed by default; recommendations are the primary view */}
        <section>
          <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
            <button
              type="button"
              onClick={() => setWatchlistOpen((v) => !v)}
              aria-expanded={watchlistOpen}
              className="flex items-center gap-2 text-xl font-bold text-text-primary hover:text-accent-300 transition-colors"
            >
              <span
                className={`text-sm text-text-secondary transition-transform ${watchlistOpen ? 'rotate-90' : ''}`}
                aria-hidden="true"
              >
                ▶
              </span>
              Watchlist
              {watchlist.data && (
                <span className="text-sm font-normal text-text-secondary">
                  ({watchlist.data.length})
                </span>
              )}
            </button>
            <AddTickerForm onAdd={handleAddTicker} />
          </div>
          {watchlistOpen &&
            (watchlist.loading && !watchlist.data ? (
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
            ))}
        </section>

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
