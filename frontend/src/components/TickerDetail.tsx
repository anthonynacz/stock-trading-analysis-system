import { memo, useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Recommendation, OptionsSnapshot } from '../types';
import { getTickerRecommendations, getOptions } from '../utils/api';
import { useTickerTrends } from '../hooks/useEdgeFlow';
import { shortSectorLabel } from '../utils/theme';
import { buildDemotionTooltip, detectDemotion } from '../utils/recommendation';
import { fmtPrice } from '../utils/format';
import { ActionBadge, DemotionChip } from './ui/badges';
import { ConvictionBar } from './ui/ConvictionBar';
import { EmptyCard } from './ui/feedback';
import { SignalBullet } from './SignalBullet';
import { OptionsFlowSection, StatRow, type Sentiment } from './OptionsFlowSection';
import { SuggestedOptionsList } from './SuggestedOptionsList';
import { RevisionBadge } from './RecommendationCard';
import StrikeRecommender from './StrikeRecommender';
import TrendChart from './TrendChart';
import DayComparison from './DayComparison';

interface TickerDetailProps {
  ticker: string;
  companyName?: string;
  selectedDate?: string;
  onClose: () => void;
  rotationProtected?: boolean;
  protectionReasons?: string[];
}

function TickerDetail({ ticker, companyName, selectedDate, onClose, rotationProtected, protectionReasons }: TickerDetailProps) {
  const navigate = useNavigate();
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [options, setOptions] = useState<OptionsSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [smaWindow, setSmaWindow] = useState(5);
  const trends = useTickerTrends(ticker, 20, smaWindow);
  // Request sequence: a slow response for a previous ticker/date must not overwrite a newer one.
  const seq = useRef(0);

  const fetchData = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    try {
      const [recs, opts] = await Promise.allSettled([
        getTickerRecommendations(ticker, selectedDate),
        getOptions(ticker),
      ]);
      if (id !== seq.current) return;
      setRec(recs.status === 'fulfilled' && recs.value.length > 0 ? recs.value[0] : null);
      setOptions(opts.status === 'fulfilled' ? opts.value : null);
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [ticker, selectedDate]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => () => { seq.current++; }, []);

  const demotion = rec
    ? detectDemotion(rec.conviction_score, rec.action, rec.signals)
    : null;
  const actionTooltip = buildDemotionTooltip(rec?.conviction_score, demotion);

  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden lg:sticky lg:top-14">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-text-primary">{ticker}</span>
          {rec?.sector && shortSectorLabel(rec.sector) && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-blue-900/40 text-blue-300 border border-blue-500/30"
              title={rec.sector}
            >
              {shortSectorLabel(rec.sector)}
            </span>
          )}
          {rec && (
            <ActionBadge
              action={rec.action}
              className={actionTooltip ? 'cursor-help' : ''}
              title={actionTooltip}
            />
          )}
          {demotion?.isDemoted && (
            <DemotionChip naturalAction={demotion.naturalAction} title={actionTooltip} />
          )}
          {rec && <RevisionBadge rec={rec} />}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate(`/options-lab?ticker=${encodeURIComponent(ticker)}&auto=1`)}
            title={`Run deep options analysis for ${ticker}`}
            className="px-2 py-0.5 rounded text-[10px] font-semibold bg-accent-900/40 text-accent-300 hover:bg-accent-800/60 hover:text-accent-200 transition-colors flex items-center gap-1"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            Options Lab
          </button>
          <button
            onClick={onClose}
            aria-label="Close ticker detail"
            className="p-2 sm:p-1 rounded hover:bg-border/60 transition-colors text-text-secondary"
          >
            <svg className="w-5 h-5 sm:w-4 sm:h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-4 h-4 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
        </div>
      ) : (
        // Phones scroll the single fixed overlay in Dashboard; only at lg does the body scroll
        // inside the sticky panel (header stays visible). 7.5rem = top-14 + header + bottom gap.
        <div className="px-4 py-3 space-y-4 lg:max-h-[calc(100vh-7.5rem)] lg:overflow-y-auto">
          {companyName && (
            <p className="text-xs text-text-secondary -mt-1">{companyName}</p>
          )}

          {/* No data state — leads the panel so it is not buried under the tools */}
          {!rec && !options && (
            <EmptyCard>No recommendation or options data available for {ticker}</EmptyCard>
          )}

          {/* Rotation protection banner */}
          {rotationProtected && protectionReasons && protectionReasons.length > 0 && (
            <div className="flex items-start gap-2 px-2.5 py-2 rounded-md bg-cyan-950/40 border border-cyan-800/40">
              <svg className="w-3.5 h-3.5 text-cyan-400 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 2L4 5v6.09c0 5.05 3.41 9.76 8 10.91 4.59-1.15 8-5.86 8-10.91V5l-8-3zm-1 15l-4-4 1.41-1.41L11 14.17l6.59-6.59L19 9l-8 8z" />
              </svg>
              <div className="space-y-0.5">
                <span className="text-[10px] font-semibold text-cyan-400 uppercase tracking-wider">Rotation Protected</span>
                {protectionReasons.map((r, i) => (
                  <p key={i} className="text-[11px] text-cyan-300/70 leading-snug">{r}</p>
                ))}
              </div>
            </div>
          )}

          {/* Price info */}
          {rec && (
            <div className="space-y-1.5">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Price</h4>
              {rec.current_price != null && (
                <StatRow label="Current" value={fmtPrice(rec.current_price)} mono />
              )}
              {rec.target_price != null && (
                <StatRow label="Target" value={fmtPrice(rec.target_price)} mono />
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
                <StatRow label="Stop Loss" value={fmtPrice(rec.stop_loss_price)} mono />
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
              <ConvictionBar score={rec.conviction_score} thickness="h-2" />
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
          {options && <OptionsFlowSection data={options} />}

          {/* Suggested options */}
          {rec?.suggested_options && rec.suggested_options.length > 0 && (
            <div className="space-y-1.5">
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Suggested Options</h4>
              <SuggestedOptionsList options={rec.suggested_options} />
            </div>
          )}

          {/* Day-by-Day comparison */}
          <div className="border-t border-border/40 pt-3">
            <DayComparison ticker={ticker} />
          </div>

          {/* Strike Recommender — always visible */}
          <div className="border-t border-border/40 pt-3">
            <StrikeRecommender ticker={ticker} />
          </div>

          {/* Open Position — pre-fills from the recommendation when one exists */}
          <div className="border-t border-border/40 pt-3">
            <button
              onClick={() => {
                const params = new URLSearchParams({ open: 'true', ticker });
                if (rec) params.set('rec', String(rec.id));
                if (rec?.current_price != null) params.set('price', String(rec.current_price));
                const opt = rec?.suggested_options[0];
                if (opt?.strike != null && opt.expiry && opt.contract_type) {
                  params.set('type', opt.contract_type);
                  params.set('strike', String(opt.strike));
                  params.set('expiry', opt.expiry);
                  if (opt.premium_estimate != null) params.set('premium', String(opt.premium_estimate));
                }
                navigate(`/positions?${params.toString()}`);
              }}
              className="w-full py-2 px-3 rounded-md text-xs font-semibold bg-accent-600 hover:bg-accent-500 text-white transition-colors"
            >
              Open Position
            </button>
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
      )}
    </div>
  );
}

export default memo(TickerDetail);
