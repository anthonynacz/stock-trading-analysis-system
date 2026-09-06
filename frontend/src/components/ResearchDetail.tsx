import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { ResearchResult } from '../types';
import { useTickerTrends } from '../hooks/useEdgeFlow';
import { IMPACT_CLASSES, SENTIMENT_CLASS, shortSectorLabel } from '../utils/theme';
import { buildDemotionTooltip, detectDemotion } from '../utils/recommendation';
import { fmtPrice, fmtSigned } from '../utils/format';
import { ActionBadge, DemotionChip } from './ui/badges';
import { ConvictionBar } from './ui/ConvictionBar';
import { SignalBullet } from './SignalBullet';
import { OptionsFlowSection, StatRow, type Sentiment } from './OptionsFlowSection';
import { SuggestedOptionsList } from './SuggestedOptionsList';
import StrikeRecommender from './StrikeRecommender';
import TrendChart from './TrendChart';
import DayComparison from './DayComparison';

interface ResearchDetailProps {
  result: ResearchResult;
  onClose: () => void;
  onReanalyze: (ticker: string) => void;
  analyzing: boolean;
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

export default function ResearchDetail({ result, onClose, onReanalyze, analyzing }: ResearchDetailProps) {
  const [smaWindow, setSmaWindow] = useState(5);
  const trends = useTickerTrends(result.ticker, 20, smaWindow);
  const navigate = useNavigate();

  const demotion = detectDemotion(
    result.conviction_score,
    result.action,
    result.signals,
  );
  const actionTooltip = buildDemotionTooltip(result.conviction_score, demotion);

  return (
    <div className="detail-panel overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-text-primary">{result.ticker}</span>
          {result.sector && shortSectorLabel(result.sector) && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-blue-900/40 text-blue-300 border border-blue-500/30"
              title={result.sector}
            >
              {shortSectorLabel(result.sector)}
            </span>
          )}
          <ActionBadge
            action={result.action}
            className={actionTooltip ? 'cursor-help' : ''}
            title={actionTooltip}
          />
          {demotion.isDemoted && (
            <DemotionChip naturalAction={demotion.naturalAction} title={actionTooltip} />
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => navigate(`/options-lab?ticker=${encodeURIComponent(result.ticker)}&auto=1`)}
            title={`Run deep options analysis for ${result.ticker}`}
            className="px-2 py-0.5 rounded text-[10px] font-semibold bg-accent-900/40 text-accent-300 hover:bg-accent-800/60 hover:text-accent-200 transition-colors flex items-center gap-1"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            Options Lab
          </button>
          <button
            onClick={() => onReanalyze(result.ticker)}
            disabled={analyzing}
            className="p-1 rounded hover:bg-accent-900/40 transition-colors text-text-secondary hover:text-accent-400 disabled:opacity-40"
            title="Re-analyze"
          >
            {analyzing ? (
              <div className="w-4 h-4 border-2 border-accent-400 border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            )}
          </button>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-1 rounded hover:bg-border/60 transition-colors text-text-secondary"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* .detail-panel owns the lg scroll region — no nested max-h here */}
      <div className="px-4 py-3 space-y-4">
        {result.company_name && (
          <p className="text-xs text-text-secondary -mt-1">{result.company_name}</p>
        )}
        <p className="text-[10px] text-text-secondary">
          Analyzed {formatTimestamp(result.analyzed_at)}
        </p>

        {/* Price info */}
        <div className="space-y-1.5">
          <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Price</h4>
          {result.current_price != null && (
            <StatRow label="Current" value={fmtPrice(result.current_price)} mono />
          )}
          {result.target_price != null && (
            <StatRow label="Target" value={fmtPrice(result.target_price)} mono />
          )}
          {result.current_price != null && result.target_price != null && result.current_price > 0 && (() => {
            const discount = ((result.target_price - result.current_price) / result.target_price) * 100;
            const discountSentiment: Sentiment = discount > 15 ? 'bullish' : discount > 0 ? 'neutral' : 'bearish';
            return (
              <StatRow label="Discount to PT" value={`${discount.toFixed(1)}%`} mono sentiment={discountSentiment} />
            );
          })()}
          {result.stop_loss_price != null && (
            <StatRow label="Stop Loss" value={fmtPrice(result.stop_loss_price)} mono />
          )}
          {result.risk_level && (
            <StatRow label="Risk" value={result.risk_level} />
          )}
        </div>

        {/* Conviction */}
        {result.conviction_score != null && (
          <div className="space-y-1.5">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
              Conviction: {result.conviction_score}
            </h4>
            <ConvictionBar score={result.conviction_score} thickness="h-2" />
          </div>
        )}

        {/* Signals */}
        {result.signals && result.signals.length > 0 && (
          <div className="space-y-1.5">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
              Signals ({result.signal_count})
            </h4>
            <ul className="space-y-1">
              {result.signals.map((sig, i) => (
                <SignalBullet key={i} signal={sig} />
              ))}
            </ul>
          </div>
        )}

        {/* Rationale */}
        {result.rationale && (
          <div className="space-y-1">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Rationale</h4>
            <p className="text-xs text-text-primary">{result.rationale}</p>
          </div>
        )}

        {/* Deep-news narrative summary */}
        {result.news_summary && (
          <div className="space-y-1 border-l-2 border-blue-500/60 pl-3 py-1 bg-blue-500/5">
            <h4 className="text-[10px] font-semibold text-blue-400 uppercase tracking-wider">
              Narrative Summary
            </h4>
            <p className="text-xs text-text-primary leading-relaxed whitespace-pre-line">
              {result.news_summary}
            </p>
          </div>
        )}

        {/* Bull / Bear / Watch synthesis */}
        {(result.bull_case || result.bear_case || result.watch_text) && (
          <div className="space-y-2">
            {result.bull_case && (
              <div className="space-y-1 border-l-2 border-green-500/60 pl-3 py-1 bg-green-500/5">
                <h4 className="text-[10px] font-semibold text-green-400 uppercase tracking-wider">
                  Bull Case
                </h4>
                <p className="text-xs text-text-primary leading-relaxed whitespace-pre-line">
                  {result.bull_case}
                </p>
              </div>
            )}
            {result.bear_case && (
              <div className="space-y-1 border-l-2 border-red-500/60 pl-3 py-1 bg-red-500/5">
                <h4 className="text-[10px] font-semibold text-red-400 uppercase tracking-wider">
                  Bear Case
                </h4>
                <p className="text-xs text-text-primary leading-relaxed whitespace-pre-line">
                  {result.bear_case}
                </p>
              </div>
            )}
            {result.watch_text && (
              <div className="space-y-1 border-l-2 border-amber-500/60 pl-3 py-1 bg-amber-500/5">
                <h4 className="text-[10px] font-semibold text-amber-400 uppercase tracking-wider">
                  What To Watch
                </h4>
                <p className="text-xs text-text-primary leading-relaxed whitespace-pre-line">
                  {result.watch_text}
                </p>
              </div>
            )}
          </div>
        )}

        {/* News activity — category + source-quality breakdown */}
        {result.news_clusters && result.news_clusters.article_count_14d > 0 && (
          <div className="space-y-1.5">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
              News Activity ({result.news_clusters.article_count_14d} articles · 14d)
            </h4>
            <div className="flex flex-wrap gap-1">
              {Object.entries(result.news_clusters.by_category)
                .sort((a, b) => b[1] - a[1])
                .map(([cat, count]) => (
                  <span
                    key={cat}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800/60 text-text-secondary"
                  >
                    {cat.toLowerCase()} <span className="text-text-primary font-mono">{count}</span>
                  </span>
                ))}
            </div>
            <div className="flex flex-wrap gap-1 text-[10px]">
              {Object.entries(result.news_clusters.by_source_quality)
                .sort((a, b) => b[1] - a[1])
                .map(([q, count]) => {
                  const colorMap: Record<string, string> = {
                    PRIMARY: 'text-green-400',
                    MAJOR_PRESS: 'text-blue-400',
                    ANALYST: 'text-amber-400',
                    AGGREGATOR: 'text-text-secondary',
                    OTHER: 'text-text-secondary',
                  };
                  return (
                    <span key={q} className={`${colorMap[q] ?? 'text-text-secondary'}`}>
                      {q.toLowerCase().replace('_', ' ')} <span className="font-mono">{count}</span>
                    </span>
                  );
                })}
            </div>
          </div>
        )}

        {/* Sentiment timeline */}
        {result.sentiment_timeline && result.sentiment_timeline.length > 0 && (
          <div className="space-y-1.5">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
              Sentiment Timeline (FinBERT, 14d)
            </h4>
            <div className="flex items-end gap-1 h-12">
              {result.sentiment_timeline.map((p) => {
                const mag = Math.min(Math.abs(p.mean_sentiment), 1);
                const heightPct = mag * 100;
                const positive = p.mean_sentiment >= 0;
                return (
                  <div
                    key={p.date}
                    className="flex-1 flex flex-col items-center justify-end"
                    title={`${p.date}: sent=${p.mean_sentiment.toFixed(2)} (n=${p.article_count})`}
                  >
                    <div
                      className={`w-full rounded-sm ${positive ? 'bg-green-500/60' : 'bg-red-500/60'}`}
                      style={{ height: `${Math.max(heightPct, 6)}%` }}
                    />
                  </div>
                );
              })}
            </div>
            <div className="flex justify-between text-[9px] text-text-secondary font-mono">
              <span>{result.sentiment_timeline[0]?.date.slice(5)}</span>
              <span>{result.sentiment_timeline[result.sentiment_timeline.length - 1]?.date.slice(5)}</span>
            </div>
          </div>
        )}

        {/* Top headlines */}
        {result.top_headlines && result.top_headlines.length > 0 && (
          <div className="space-y-1.5">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
              Top Headlines
            </h4>
            <ul className="space-y-1.5">
              {result.top_headlines.map((h, i) => {
                // Headlines deliberately use the tighter ±0.1 threshold (see theme.ts).
                const sentColor = SENTIMENT_CLASS(h.sentiment_score ?? 0, 0.1);
                const impactBg = IMPACT_CLASSES[h.impact_level ?? 'LOW'];
                return (
                  <li key={i} className="text-xs leading-snug">
                    <div className="flex items-start gap-1.5">
                      <span className={`shrink-0 text-[9px] px-1 py-px rounded font-semibold ${impactBg}`}>
                        {(h.impact_level ?? 'LOW').slice(0, 1)}
                      </span>
                      <div className="flex-1 min-w-0">
                        {h.url ? (
                          <a href={h.url} target="_blank" rel="noreferrer" className="text-text-primary hover:text-blue-400">
                            {h.headline}
                          </a>
                        ) : (
                          <span className="text-text-primary">{h.headline}</span>
                        )}
                        <div className="text-[10px] text-text-secondary mt-0.5 flex items-center gap-1.5 flex-wrap">
                          <span>{h.source ?? 'unknown'}</span>
                          {h.published_at && <span>· {h.published_at.slice(0, 10)}</span>}
                          <span>· {h.category.toLowerCase()}</span>
                          {h.sentiment_score != null && (
                            <span className={`font-mono ${sentColor}`}>
                              {fmtSigned(h.sentiment_score, 2)}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {/* Enrichment status footer */}
        {result.enrichment_status && result.enrichment_status !== 'COMPLETE' && (
          <div className="text-[10px] text-text-secondary italic">
            Enrichment: {result.enrichment_status}
            {result.enrichment_error ? ` — ${result.enrichment_error}` : ''}
          </div>
        )}

        {/* Entry / Exit */}
        {result.entry_strategy && result.entry_strategy !== 'HOLD' && (
          <div className="space-y-1">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Entry</h4>
            <p className="text-xs text-text-primary">{result.entry_strategy}</p>
          </div>
        )}
        {result.exit_rules && (
          <div className="space-y-1">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Exit</h4>
            <p className="text-xs text-text-primary">{result.exit_rules}</p>
          </div>
        )}

        {/* Options flow */}
        {result.options_data && <OptionsFlowSection data={result.options_data} />}

        {/* Suggested options */}
        {result.suggested_options && result.suggested_options.length > 0 && (
          <div className="space-y-1.5">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Suggested Options</h4>
            <SuggestedOptionsList options={result.suggested_options} />
          </div>
        )}

        {/* Day-by-Day comparison */}
        <div className="border-t border-border/40 pt-3">
          <DayComparison ticker={result.ticker} />
        </div>

        {/* Strike Recommender */}
        <div className="border-t border-border/40 pt-3">
          <StrikeRecommender ticker={result.ticker} />
        </div>

        {/* Trend Charts */}
        <div className="border-t border-border/40 pt-3">
          <TrendChart
            data={trends.data}
            loading={trends.loading}
            error={trends.error}
            sma={smaWindow}
            onSmaChange={setSmaWindow}
          />
        </div>
      </div>
    </div>
  );
}
