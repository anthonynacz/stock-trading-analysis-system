import { useState } from 'react';
import {
  useWatchlist,
  useRecommendations,
  useNews,
  useCatalysts,
  useStatus,
} from '../hooks/useEdgeFlow';
import { triggerRefresh } from '../utils/api';
import StatusBar from '../components/StatusBar';
import WatchlistGrid from '../components/WatchlistGrid';
import RecommendationCard from '../components/RecommendationCard';
import NewsTimeline from '../components/NewsTimeline';
import CatalystCalendar from '../components/CatalystCalendar';

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
  const [refreshing, setRefreshing] = useState(false);

  const watchlist = useWatchlist();
  const recommendations = useRecommendations();
  const news = useNews();
  const catalysts = useCatalysts();
  const status = useStatus();

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await triggerRefresh();
      // Refetch all data after refresh
      watchlist.refetch();
      recommendations.refetch();
      news.refetch();
      catalysts.refetch();
      status.refetch();
    } catch {
      // Status bar will reflect errors via status hook
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="min-h-screen bg-page text-text-primary">
      {/* Status Bar */}
      <StatusBar
        status={status.data}
        onRefresh={handleRefresh}
        refreshing={refreshing}
      />

      <div className="max-w-7xl mx-auto px-4 py-6 space-y-8">
        {/* Watchlist */}
        <section>
          <h2 className="text-xl font-bold text-text-primary mb-4">Watchlist</h2>
          {watchlist.loading ? (
            <SectionLoading />
          ) : watchlist.error ? (
            <SectionError message={watchlist.error} />
          ) : (
            <WatchlistGrid items={watchlist.data ?? []} />
          )}
        </section>

        {/* Recommendations */}
        <section>
          <h2 className="text-xl font-bold text-text-primary mb-4">Recommendations</h2>
          {recommendations.loading ? (
            <SectionLoading />
          ) : recommendations.error ? (
            <SectionError message={recommendations.error} />
          ) : (
            <div className="space-y-3">
              {(recommendations.data ?? []).map((rec) => (
                <RecommendationCard key={rec.id} recommendation={rec} />
              ))}
              {recommendations.data?.length === 0 && (
                <p className="text-text-secondary text-sm text-center py-6">
                  No recommendations available
                </p>
              )}
            </div>
          )}
        </section>

        {/* News + Catalysts two-column */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          <section className="lg:col-span-3">
            <h2 className="text-xl font-bold text-text-primary mb-4">News</h2>
            <div className="bg-card border border-border rounded-lg p-4">
              {news.loading ? (
                <SectionLoading />
              ) : news.error ? (
                <SectionError message={news.error} />
              ) : (
                <NewsTimeline items={news.data ?? []} />
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
