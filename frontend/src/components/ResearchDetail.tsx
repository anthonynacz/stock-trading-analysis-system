import { useState } from 'react';
import type { ResearchResult, SignalDetail } from '../types';
import { useTickerTrends } from '../hooks/useEdgeFlow';
import { ACTION_COLORS, getActionLabel } from '../utils/theme';
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

  const borderColor = ACTION_COLORS[result.action] ?? '#21262d';

  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden sticky top-10">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-text-primary">{result.ticker}</span>
          <span
            className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase"
            style={{ backgroundColor: borderColor + '22', color: borderColor }}
          >
            {getActionLabel(result.action)}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => onReanalyze(result.ticker)}
            disabled={analyzing}
            className="p-1 rounded hover:bg-purple-900/40 transition-colors text-text-secondary hover:text-purple-400 disabled:opacity-40"
            title="Re-analyze"
          >
            {analyzing ? (
              <div className="w-4 h-4 border-2 border-purple-400 border-t-transparent rounded-full animate-spin" />
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
