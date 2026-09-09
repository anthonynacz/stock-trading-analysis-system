import { memo } from 'react';
import type { BreakingNewsItem, BreakingNewsTicker } from '../types';
import { fmtSigned, formatRelativeTime } from '../utils/format';
import { ACTION_COLORS, getActionLabel } from '../utils/theme';
import { LoadingRow } from './ui/feedback';

export type DetailFocus = 'strikes' | null;

interface BreakingNewsProps {
  items: BreakingNewsItem[] | null;
  loading: boolean;
  error: string | null;
  hours: number;
  /** Opens the ticker detail panel; `focus === 'strikes'` auto-runs the strike recommender. */
  onOpenDetail: (ticker: string, focus?: DetailFocus) => void;
}

function sentimentClasses(score: number | null): string {
  if (score == null) return 'bg-border/60 text-text-secondary';
  if (score >= 0.3) return 'bg-green-900/40 text-green-300 border-green-500/30';
  if (score <= -0.3) return 'bg-red-900/40 text-red-300 border-red-500/30';
  return 'bg-amber-900/30 text-amber-300 border-amber-500/30';
}

function RevisionDelta({ t }: { t: BreakingNewsTicker }) {
  const rec = t.recommendation;
  if (!rec?.triggered_revision) return null;
  const changed = rec.prior_action != null && rec.prior_action !== rec.action;
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] font-mono text-text-secondary"
      title="This headline triggered an intraday rescore"
    >
      <span className="text-accent-300 font-semibold uppercase tracking-wider text-[9px]">REV</span>
      {changed ? (
        <>
          <span>{getActionLabel(rec.prior_action!)}</span>
          <span>→</span>
          <span style={{ color: ACTION_COLORS[rec.action] }} className="font-semibold">
            {getActionLabel(rec.action)}
          </span>
        </>
      ) : (
        <span style={{ color: ACTION_COLORS[rec.action] }} className="font-semibold">
          {getActionLabel(rec.action)}
        </span>
      )}
      {rec.prior_conviction_score != null && rec.conviction_score != null && (
        <span>
          {fmtSigned(rec.prior_conviction_score)} → {fmtSigned(rec.conviction_score)}
        </span>
      )}
    </span>
  );
}

function BreakingNews({ items, loading, error, hours, onOpenDetail }: BreakingNewsProps) {
  if (loading && !items) return <LoadingRow label="Checking for breaking news…" py="py-2" size={3} />;
  // Supplementary strip: a failed fetch must not shout at the top of the page.
  if (error) {
    console.warn('breaking news unavailable:', error);
    return null;
  }
  if (!items || items.length === 0) return null;

  return (
    <section aria-label="Breaking news" className="rounded-lg border border-accent-500/30 bg-accent-900/10">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-accent-500/20">
        <h2 className="text-xs font-bold uppercase tracking-wider text-accent-300 flex items-center gap-2">
          <span className="relative flex h-2 w-2" aria-hidden="true">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent-400 opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-accent-400" />
          </span>
          Breaking · last {hours}h
        </h2>
        <span className="text-[10px] text-text-secondary">
          {items.length} material headline{items.length === 1 ? '' : 's'}
        </span>
      </div>
      <ul className="divide-y divide-border/60">
        {items.map((item) => {
          const primary = item.tickers[0];
          const held = item.tickers.some((t) => t.held);
          return (
            <li key={item.id} className="px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1">
              <span
                className={`px-1.5 py-px rounded border text-[10px] font-mono font-semibold shrink-0 ${sentimentClasses(item.sentiment_score)}`}
                title="FinBERT sentiment"
              >
                {item.sentiment_score == null ? '·' : fmtSigned(item.sentiment_score, 2)}
              </span>
              {item.impact_level === 'HIGH' && (
                <span className="px-1.5 py-px rounded text-[9px] font-bold uppercase tracking-wider bg-red-900/40 text-red-300 border border-red-500/30 shrink-0">
                  High impact
                </span>
              )}
              <span className="flex items-center gap-1 shrink-0">
                {item.tickers.slice(0, 3).map((t) => (
                  <button
                    key={t.ticker}
                    type="button"
                    onClick={() => onOpenDetail(t.ticker)}
                    title={`${t.ticker}${t.held ? ' · you hold an open position' : ''} — open details`}
                    className={`px-1.5 py-px rounded text-[11px] font-bold border transition-colors ${
                      t.held
                        ? 'bg-amber-900/40 text-amber-300 border-amber-500/40 hover:bg-amber-800/50'
                        : 'bg-border/60 text-text-primary border-border hover:bg-border'
                    }`}
                  >
                    {t.ticker}
                  </button>
                ))}
                {item.tickers.length > 3 && (
                  <span className="text-[10px] text-text-secondary">+{item.tickers.length - 3}</span>
                )}
              </span>
              {item.source_url ? (
                <a
                  href={item.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-text-primary hover:text-accent-300 transition-colors min-w-0 flex-1 basis-64 truncate"
                  title={item.headline}
                >
                  {item.headline}
                </a>
              ) : (
                <span className="text-sm text-text-primary min-w-0 flex-1 basis-64 truncate" title={item.headline}>
                  {item.headline}
                </span>
              )}
              <span className="text-[10px] text-text-secondary shrink-0">
                {item.source ? `${item.source} · ` : ''}
                {item.published_at ? formatRelativeTime(item.published_at) : ''}
              </span>
              {primary && <RevisionDelta t={primary} />}
              {primary?.recommendation && (
                <button
                  type="button"
                  onClick={() => onOpenDetail(primary.ticker, 'strikes')}
                  className="btn-primary px-2 py-0.5 text-[10px] shrink-0"
                  title={`Open ${primary.ticker} and find strikes now`}
                >
                  Strikes
                </button>
              )}
              {held && !primary?.recommendation && (
                <span className="text-[10px] text-amber-300 shrink-0">Held position</span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export default memo(BreakingNews);
