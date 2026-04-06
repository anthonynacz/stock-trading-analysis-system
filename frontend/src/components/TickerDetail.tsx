import { useState, useEffect, useCallback } from 'react';
import type { Recommendation, OptionsSnapshot, SignalDetail } from '../types';
import { getTickerRecommendations, getOptions } from '../utils/api';
import { ACTION_COLORS, getActionLabel } from '../utils/theme';
import StrikeRecommender from './StrikeRecommender';

interface TickerDetailProps {
  ticker: string;
  companyName?: string;
  onClose: () => void;
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
  if (v < 25) return 'bullish';   // cheap options — good for buyers
  if (v < 50) return 'neutral';
  if (v < 75) return 'elevated';  // expensive
  return 'bearish';               // very expensive — sellers' market
}

function ratePutCallRatio(v: number): Sentiment {
  if (v < 0.7) return 'bullish';  // call-heavy flow
  if (v <= 1.0) return 'neutral';
  if (v <= 1.5) return 'elevated'; // moderately put-heavy
  return 'bearish';                // strongly put-heavy
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

export default function TickerDetail({ ticker, companyName, onClose }: TickerDetailProps) {
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [options, setOptions] = useState<OptionsSnapshot | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [recs, opts] = await Promise.allSettled([
        getTickerRecommendations(ticker),
        getOptions(ticker),
      ]);
      setRec(recs.status === 'fulfilled' && recs.value.length > 0 ? recs.value[0] : null);
      setOptions(opts.status === 'fulfilled' ? opts.value : null);
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const borderColor = rec ? (ACTION_COLORS[rec.action] ?? '#21262d') : '#21262d';

  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden sticky top-16">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-text-primary">{ticker}</span>
          {rec && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase"
              style={{ backgroundColor: borderColor + '22', color: borderColor }}
            >
              {getActionLabel(rec.action)}
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-border/60 transition-colors text-text-secondary"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-4 h-4 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        <div className="px-4 py-3 space-y-4 max-h-[calc(100vh-10rem)] overflow-y-auto">
          {companyName && (
            <p className="text-xs text-text-secondary -mt-1">{companyName}</p>
          )}

          {/* Price info */}
          {rec && (
            <div className="space-y-1.5">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Price</h4>
              {rec.current_price != null && (
                <StatRow label="Current" value={`$${rec.current_price.toFixed(2)}`} mono />
              )}
              {rec.target_price != null && (
                <StatRow label="Target" value={`$${rec.target_price.toFixed(2)}`} mono />
              )}
              {rec.current_price != null && rec.target_price != null && rec.current_price > 0 && (() => {
                const discount = ((rec.target_price - rec.current_price) / rec.target_price) * 100;
                const discountSentiment: Sentiment = discount > 15 ? 'bullish' : discount > 0 ? 'neutral' : 'bearish';
                return (
                  <StatRow
                    label="Discount to PT"
                    value={`${discount.toFixed(1)}%`}
                    mono
                    sentiment={discountSentiment}
                  />
                );
              })()}
              {rec.stop_loss_price != null && (
                <StatRow label="Stop Loss" value={`$${rec.stop_loss_price.toFixed(2)}`} mono />
              )}
              {rec.risk_level && (
                <StatRow label="Risk" value={rec.risk_level} />
              )}
            </div>
          )}

          {/* Conviction */}
          {rec?.conviction_score != null && (
            <div className="space-y-1.5">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
                Conviction: {rec.conviction_score}
              </h4>
              <div className="h-2 rounded-full bg-border overflow-hidden">
                <div
                  className={`h-full rounded-full ${rec.conviction_score >= 0 ? 'bg-green-500' : 'bg-red-500'}`}
                  style={{ width: `${Math.min(Math.abs(rec.conviction_score), 100)}%` }}
                />
              </div>
            </div>
          )}

          {/* Signals */}
          {rec?.signals && rec.signals.length > 0 && (
            <div className="space-y-1.5">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Signals</h4>
              <ul className="space-y-1">
                {rec.signals.map((sig, i) => (
                  <SignalBullet key={i} signal={sig} />
                ))}
              </ul>
            </div>
          )}

          {/* Entry / Exit */}
          {rec?.entry_strategy && rec.entry_strategy !== 'HOLD' && (
            <div className="space-y-1">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Entry</h4>
              <p className="text-xs text-text-primary">{rec.entry_strategy}</p>
            </div>
          )}
          {rec?.exit_rules && (
            <div className="space-y-1">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Exit</h4>
              <p className="text-xs text-text-primary">{rec.exit_rules}</p>
            </div>
          )}

          {/* Options flow */}
          {options && (() => {
            const callVol = options.total_call_volume ?? 0;
            const putVol = options.total_put_volume ?? 0;
            const flowSentiment = rateVolumeDominance(callVol, putVol);
            return (
              <div className="space-y-1.5">
                <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Options Flow</h4>
                {options.iv_rank != null && (
                  <StatRow
                    label="IV Rank"
                    value={`${options.iv_rank.toFixed(1)}%`}
                    mono
                    sentiment={rateIvRank(options.iv_rank)}
                  />
                )}
                {options.put_call_ratio != null && (
                  <StatRow
                    label="Put/Call Ratio"
                    value={options.put_call_ratio.toFixed(2)}
                    mono
                    sentiment={ratePutCallRatio(options.put_call_ratio)}
                  />
                )}
                {options.total_call_volume != null && (
                  <StatRow
                    label="Call Volume"
                    value={options.total_call_volume.toLocaleString()}
                    mono
                    sentiment={flowSentiment === 'bullish' ? 'bullish' : 'neutral'}
                  />
                )}
                {options.total_put_volume != null && (
                  <StatRow
                    label="Put Volume"
                    value={options.total_put_volume.toLocaleString()}
                    mono
                    sentiment={flowSentiment === 'bearish' ? 'bearish' : 'neutral'}
                  />
                )}
                {options.unusual_activity && (
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
          {rec?.suggested_options && rec.suggested_options.length > 0 && (
            <div className="space-y-1.5">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Suggested Options</h4>
              <div className="space-y-1">
                {rec.suggested_options.map((opt) => (
                  <div key={opt.id} className="text-xs flex items-center gap-2">
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

          {/* Strike Recommender — always visible */}
          <div className="border-t border-border/40 pt-3">
            <StrikeRecommender ticker={ticker} />
          </div>

          {/* No data state */}
          {!rec && !options && (
            <p className="text-text-secondary text-xs text-center py-4">
              No recommendation or options data available for {ticker}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
