import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { ResearchResult, SignalDetail } from '../types';
import { useTickerTrends } from '../hooks/useEdgeFlow';
import { ACTION_COLORS, getActionLabel, shortSectorLabel } from '../utils/theme';
import { actionLabel, detectDemotion } from '../utils/recommendation';
import StrikeRecommender from './StrikeRecommender';
import TrendChart from './TrendChart';
import DayComparison from './DayComparison';

interface ResearchDetailProps {
  result: ResearchResult;
  onClose: () => void;
  onReanalyze: (ticker: string) => void;
  analyzing: boolean;
}

function SignalBullet({ signal }: { signal: SignalDetail }) {
  const isPositive = signal.points > 0;
  const prefix = isPositive ? '+' : '';
  const pointColor = isPositive ? 'text-green-400' : 'text-red-400';

  return (
    <li className="flex items-start gap-2 text-xs">
      <span className={`font-mono font-bold mt-px w-7 shrink-0 text-right ${pointColor}`}>
        {prefix}{signal.points}
      </span>
      <span className="text-text-primary">
        {signal.signal}
        {signal.detail && (
          <span className="text-text-secondary"> — {signal.detail}</span>
        )}
      </span>
    </li>
  );
}

type Sentiment = 'bullish' | 'bearish' | 'neutral' | 'elevated';

const SENTIMENT_STYLES: Record<Sentiment, { bg: string; text: string; label: string }> = {
  bullish:  { bg: 'bg-green-900/40', text: 'text-green-400', label: 'Bullish' },
  bearish:  { bg: 'bg-red-900/40',   text: 'text-red-400',   label: 'Bearish' },
  neutral:  { bg: 'bg-gray-800/60',  text: 'text-text-secondary', label: 'Neutral' },
  elevated: { bg: 'bg-amber-900/40', text: 'text-amber-400', label: 'Elevated' },
};

function SentimentTag({ sentiment }: { sentiment: Sentiment }) {
  const s = SENTIMENT_STYLES[sentiment];
  return (
    <span className={`px-1.5 py-px rounded text-[9px] font-semibold uppercase ${s.bg} ${s.text}`}>
      {s.label}
    </span>
  );
}

function rateIvRank(v: number): Sentiment {
  if (v < 25) return 'bullish';
  if (v < 50) return 'neutral';
  if (v < 75) return 'elevated';
  return 'bearish';
}

function ratePutCallRatio(v: number): Sentiment {
  if (v < 0.7) return 'bullish';
  if (v <= 1.0) return 'neutral';
  if (v <= 1.5) return 'elevated';
  return 'bearish';
}

function rateVolumeDominance(calls: number, puts: number): Sentiment {
  if (calls + puts === 0) return 'neutral';
  const ratio = calls / (calls + puts);
  if (ratio > 0.6) return 'bullish';
  if (ratio < 0.4) return 'bearish';
  return 'neutral';
}

function StatRow({ label, value, mono, sentiment }: {
  label: string;
  value: string;
  mono?: boolean;
  sentiment?: Sentiment;
}) {
  return (
    <div className="flex justify-between items-center text-xs gap-2">
      <span className="text-text-secondary">{label}</span>
      <div className="flex items-center gap-1.5">
        {sentiment && <SentimentTag sentiment={sentiment} />}
        <span className={`${sentiment ? SENTIMENT_STYLES[sentiment].text : 'text-text-primary'} ${mono ? 'font-mono' : ''}`}>
          {value}
        </span>
      </div>
    </div>
  );
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

  const borderColor = ACTION_COLORS[result.action] ?? '#21262d';
  const demotion = detectDemotion(
    result.conviction_score,
    result.action,
    result.signals,
  );
  const actionTooltip = demotion.isDemoted
    ? `Score ${Number(result.conviction_score ?? 0).toFixed(0)}: natural band ${actionLabel(demotion.naturalAction)}. ` +
      `Demoted to ${actionLabel(demotion.finalAction)}` +
      (demotion.gateName ? ` by ${demotion.gateName}.` : '.') +
      (demotion.gateDetail ? ` ${demotion.gateDetail}` : '')
    : undefined;

  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden sticky top-10">
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
          <span
            className={demotion.isDemoted ? 'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase cursor-help' : 'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase'}
            style={{ backgroundColor: borderColor + '22', color: borderColor }}
            title={actionTooltip}
          >
            {getActionLabel(result.action)}
          </span>
          {demotion.isDemoted && (
            <span
              className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-px rounded bg-amber-900/40 text-amber-300 border border-amber-500/30 cursor-help"
              title={actionTooltip}
            >
              ↓ {actionLabel(demotion.naturalAction)}
            </span>
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
            className="p-1 rounded hover:bg-border/60 transition-colors text-text-secondary"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <div className="px-4 py-3 space-y-4 max-h-[calc(100vh-8rem)] overflow-y-auto">
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
            <StatRow label="Current" value={`$${result.current_price.toFixed(2)}`} mono />
          )}
          {result.target_price != null && (
            <StatRow label="Target" value={`$${result.target_price.toFixed(2)}`} mono />
          )}
          {result.current_price != null && result.target_price != null && result.current_price > 0 && (() => {
            const discount = ((result.target_price - result.current_price) / result.target_price) * 100;
            const discountSentiment: Sentiment = discount > 15 ? 'bullish' : discount > 0 ? 'neutral' : 'bearish';
            return (
              <StatRow label="Discount to PT" value={`${discount.toFixed(1)}%`} mono sentiment={discountSentiment} />
            );
          })()}
          {result.stop_loss_price != null && (
            <StatRow label="Stop Loss" value={`$${result.stop_loss_price.toFixed(2)}`} mono />
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
            <div className="h-2 rounded-full bg-border overflow-hidden">
              <div
                className={`h-full rounded-full ${result.conviction_score >= 0 ? 'bg-green-500' : 'bg-red-500'}`}
                style={{ width: `${Math.min(Math.abs(result.conviction_score), 100)}%` }}
              />
            </div>
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
                const sent = h.sentiment_score ?? 0;
                const sentColor = sent > 0.1 ? 'text-green-400' : sent < -0.1 ? 'text-red-400' : 'text-text-secondary';
                const impactBg =
                  h.impact_level === 'HIGH' ? 'bg-amber-900/40 text-amber-400'
                    : h.impact_level === 'MEDIUM' ? 'bg-gray-800/60 text-text-secondary'
                    : 'bg-gray-900/40 text-text-secondary';
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
                              {h.sentiment_score >= 0 ? '+' : ''}{h.sentiment_score.toFixed(2)}
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
        {result.options_data && (() => {
          const od = result.options_data;
          const callVol = od.total_call_volume ?? 0;
          const putVol = od.total_put_volume ?? 0;
          const flowSentiment = rateVolumeDominance(callVol, putVol);
          return (
            <div className="space-y-1.5">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Options Flow</h4>
              {od.iv_rank != null && (
                <StatRow label="IV Rank" value={`${od.iv_rank.toFixed(1)}%`} mono sentiment={rateIvRank(od.iv_rank)} />
              )}
              {od.put_call_ratio != null && (
                <StatRow label="Put/Call Ratio" value={od.put_call_ratio.toFixed(2)} mono sentiment={ratePutCallRatio(od.put_call_ratio)} />
              )}
              {od.total_call_volume != null && (
                <StatRow
                  label="Call Volume"
                  value={od.total_call_volume.toLocaleString()}
                  mono
                  sentiment={flowSentiment === 'bullish' ? 'bullish' : 'neutral'}
                />
              )}
              {od.total_put_volume != null && (
                <StatRow
                  label="Put Volume"
                  value={od.total_put_volume.toLocaleString()}
                  mono
                  sentiment={flowSentiment === 'bearish' ? 'bearish' : 'neutral'}
                />
              )}
              {od.unusual_activity && (
                <div className="mt-1">
                  <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-900/60 text-amber-400">
                    UNUSUAL ACTIVITY
                  </span>
                </div>
              )}
            </div>
          );
        })()}

        {/* Suggested options */}
        {result.suggested_options && result.suggested_options.length > 0 && (
          <div className="space-y-1.5">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Suggested Options</h4>
            <div className="space-y-1">
              {result.suggested_options.map((opt, i) => (
                <div key={i} className="text-xs flex items-center gap-2">
                  <span className={opt.contract_type === 'CALL' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
                    {opt.contract_type}
                  </span>
                  <span className="font-mono text-text-primary">${opt.strike.toFixed(0)}</span>
                  <span className="text-text-secondary">{opt.expiry}</span>
                  {opt.premium_estimate != null && (
                    <span className="font-mono text-text-secondary ml-auto">${opt.premium_estimate.toFixed(2)}</span>
                  )}
                </div>
              ))}
            </div>
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
