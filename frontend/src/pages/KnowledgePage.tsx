import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { TabBar } from '../components/ui/TabBar';
import { LoadingRow } from '../components/ui/feedback';
import { BRAND } from './knowledge/shared';

type Section = 'guide' | 'signals' | 'classification' | 'watchlist' | 'strikes' | 'optionslab' | 'strategies' | 'pipeline';

const TABS: { key: Section; label: string }[] = [
  { key: 'guide', label: 'Trading Guide' },
  { key: 'signals', label: 'Signal Stacking' },
  { key: 'classification', label: 'Classification' },
  { key: 'watchlist', label: 'Watchlist Scoring' },
  { key: 'strikes', label: 'Strike Profiles' },
  { key: 'optionslab', label: 'Options Lab' },
  { key: 'strategies', label: 'Strategies' },
  { key: 'pipeline', label: 'Pipeline & Data' },
];

const DEFAULT_TAB: Section = 'guide';

const TAB_COMPONENTS: Record<Section, React.LazyExoticComponent<() => JSX.Element>> = {
  guide: lazy(() => import('./knowledge/TradingGuideTab')),
  signals: lazy(() => import('./knowledge/SignalsTab')),
  classification: lazy(() => import('./knowledge/ClassificationTab')),
  watchlist: lazy(() => import('./knowledge/WatchlistTab')),
  strikes: lazy(() => import('./knowledge/StrikesTab')),
  optionslab: lazy(() => import('./knowledge/OptionsLabTab')),
  strategies: lazy(() => import('./knowledge/StrategiesTab')),
  pipeline: lazy(() => import('./knowledge/PipelineTab')),
};

export default function KnowledgePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get('tab');
  const activeTab: Section = TABS.some((t) => t.key === raw) ? (raw as Section) : DEFAULT_TAB;

  const setActiveTab = (key: Section) =>
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (key === DEFAULT_TAB) next.delete('tab');
      else next.set('tab', key);
      return next;
    });

  const ActiveTab = TAB_COMPONENTS[activeTab];

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-5xl mx-auto px-3 py-4 sm:px-4 sm:py-8">
        <h1 className="text-2xl font-bold mb-1">Knowledge Base</h1>
        <p className="text-sm text-text-secondary mb-6">
          Reference guide for {BRAND}'s signal stacking, scoring, and pipeline mechanics.
        </p>

        <TabBar tabs={TABS} active={activeTab} onChange={setActiveTab} size="md" className="mb-6" />

        <div role="tabpanel">
          <Suspense fallback={<LoadingRow />}>
            <ActiveTab />
          </Suspense>
        </div>
      </div>
    </div>
  );
}
