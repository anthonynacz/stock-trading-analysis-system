import { useState, useMemo } from 'react';
import type { NewsItem } from '../types';
import { SENTIMENT_COLOR, IMPACT_COLORS } from '../utils/theme';

interface NewsTimelineProps {
  items: NewsItem[];
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

export default function NewsTimeline({ items }: NewsTimelineProps) {
  const [categoryFilter, setCategoryFilter] = useState('');
  const [impactFilter, setImpactFilter] = useState('');

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

  return (
    <div className="space-y-3">
      {/* Filters */}
      <div className="flex gap-2">
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="bg-card border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-text-secondary"
        >
          <option value="">All Categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <select
          value={impactFilter}
          onChange={(e) => setImpactFilter(e.target.value)}
          className="bg-card border border-border rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-text-secondary"
        >
          <option value="">All Impact</option>
          <option value="HIGH">High</option>
          <option value="MEDIUM">Medium</option>
          <option value="LOW">Low</option>
        </select>
      </div>

      {/* Items */}
      {filtered.length === 0 ? (
        <p className="text-text-secondary text-sm text-center py-6">No news available</p>
      ) : (
        <div className="space-y-1">
          {filtered.map((item) => (
            <div
              key={item.id}
              className="flex items-start gap-3 py-2 border-b border-border/50 last:border-0"
            >
              {/* Sentiment dot */}
              <div className="mt-1.5 flex-shrink-0">
                <div
                  className="w-2 h-2 rounded-full"
                  style={{
                    backgroundColor: item.sentiment_score != null
                      ? SENTIMENT_COLOR(item.sentiment_score)
                      : '#8b949e',
                  }}
                />
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
