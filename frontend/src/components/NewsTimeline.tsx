import { useState, useMemo } from 'react';
import type { NewsItem } from '../types';
import { SENTIMENT_COLOR, IMPACT_COLORS } from '../utils/theme';

interface NewsTimelineProps {
  items: NewsItem[];
  showTickers?: boolean;
  industries?: string[];
  industry?: string;
  onIndustryChange?: (industry: string) => void;
}

function formatTime(dateStr: string | null): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const PAGE_SIZE = 15;

export default function NewsTimeline({
  items,
  showTickers = false,
  industries,
  industry,
  onIndustryChange,
}: NewsTimelineProps) {
  const [categoryFilter, setCategoryFilter] = useState('');
  const [impactFilter, setImpactFilter] = useState('');
  const [page, setPage] = useState(0);

  const categories = useMemo(
    () => [...new Set(items.map((n) => n.category).filter(Boolean))] as string[],
    [items],
  );

  const filtered = useMemo(() => {
    let result = items;
    if (categoryFilter) result = result.filter((n) => n.category === categoryFilter);
    if (impactFilter) result = result.filter((n) => n.impact_level === impactFilter);
    return result;
  }, [items, categoryFilter, impactFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  // Reset page when filters change and current page is out of bounds
  const safePage = Math.min(page, totalPages - 1);
  if (safePage !== page) setPage(safePage);

  const paged = filtered.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  return (
    <div className="space-y-3">
      {/* Filters + Pagination */}
      <div className="flex items-center gap-2 flex-wrap">
        {industries && onIndustryChange && (
          <select
            value={industry ?? ''}
            onChange={(e) => { onIndustryChange(e.target.value); setPage(0); }}
            className="bg-card border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-text-secondary"
          >
            <option value="">All Industries</option>
            {industries.map((ind) => (
              <option key={ind} value={ind}>{ind}</option>
            ))}
          </select>
        )}
        <select
          value={categoryFilter}
          onChange={(e) => { setCategoryFilter(e.target.value); setPage(0); }}
          className="bg-card border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-text-secondary"
        >
          <option value="">All Categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select
          value={impactFilter}
          onChange={(e) => { setImpactFilter(e.target.value); setPage(0); }}
          className="bg-card border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-text-secondary"
        >
          <option value="">All Impact</option>
          <option value="HIGH">High</option>
          <option value="MEDIUM">Medium</option>
          <option value="LOW">Low</option>
        </select>
        {filtered.length > 0 && (
          <span className="text-[10px] text-text-secondary self-center">
            {filtered.length} article{filtered.length !== 1 ? 's' : ''}
          </span>
        )}
        {totalPages > 1 && (
          <div className="flex items-center gap-1 ml-auto">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage === 0}
              className="px-2 py-1 rounded text-xs text-text-secondary hover:text-text-primary hover:bg-border/60 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Prev
            </button>
            {Array.from({ length: totalPages }, (_, i) => (
              <button
                key={i}
                onClick={() => setPage(i)}
                className={`w-7 h-7 rounded text-xs font-medium transition-colors ${
                  i === safePage
                    ? 'bg-purple-500/20 text-purple-400'
                    : 'text-text-secondary hover:text-text-primary hover:bg-border/60'
                }`}
              >
                {i + 1}
              </button>
            ))}
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={safePage === totalPages - 1}
              className="px-2 py-1 rounded text-xs text-text-secondary hover:text-text-primary hover:bg-border/60 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              Next
            </button>
          </div>
        )}
      </div>

      {/* Items */}
      {filtered.length === 0 ? (
        <p className="text-text-secondary text-sm text-center py-6">No news available</p>
      ) : (
        <div className="space-y-1">
          {paged.map((item) => (
            <div
              key={item.id}
              className="flex items-start gap-3 py-2 border-b border-border/50 last:border-0"
            >
              {/* Sentiment dot + score */}
              <div className="mt-1 flex-shrink-0 flex items-center gap-1.5">
                <div
                  className="w-2 h-2 rounded-full"
                  style={{
                    backgroundColor: item.sentiment_score != null
                      ? SENTIMENT_COLOR(item.sentiment_score)
                      : '#8b949e',
                  }}
                />
                {item.sentiment_score != null && (
                  <span
                    className="text-[10px] font-mono font-medium tabular-nums"
                    style={{ color: SENTIMENT_COLOR(item.sentiment_score) }}
                  >
                    {item.sentiment_score > 0 ? '+' : ''}{item.sentiment_score.toFixed(2)}
                  </span>
                )}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  {item.published_at && (
                    <span className="text-text-secondary text-[10px] whitespace-nowrap">
                      {formatTime(item.published_at)}
                    </span>
                  )}
                  {item.source && (
                    <span className="px-1.5 py-0.5 rounded bg-border text-text-secondary text-[10px] font-medium">
                      {item.source}
                    </span>
                  )}
                  {item.impact_level && (
                    <span
                      className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                      style={{
                        backgroundColor: IMPACT_COLORS[item.impact_level] + '22',
                        color: IMPACT_COLORS[item.impact_level],
                      }}
                    >
                      {item.impact_level}
                    </span>
                  )}
                  {showTickers && item.related_tickers?.length > 0 && (
                    <>
                      {item.related_tickers
                        .sort((a, b) => b.relevance_score - a.relevance_score)
                        .slice(0, 5)
                        .map((r) => (
                          <span
                            key={r.ticker}
                            className="px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-purple-900/30 text-purple-400 border border-purple-700/30"
                            style={{ opacity: 0.4 + r.relevance_score * 0.6 }}
                            title={`${r.relevance_source} (${r.relevance_score})`}
                          >
                            {r.ticker}
                          </span>
                        ))}
                    </>
                  )}
                </div>
                {item.source_url ? (
                  <a
                    href={item.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-text-primary hover:text-new-entrant transition-colors leading-snug"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {item.headline}
                  </a>
                ) : (
                  <p className="text-sm text-text-primary leading-snug">{item.headline}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

    </div>
  );
}
