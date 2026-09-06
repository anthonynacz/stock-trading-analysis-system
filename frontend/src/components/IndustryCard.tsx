import { memo } from 'react';
import { Link } from 'react-router-dom';
import type { IndustryRecommendation } from '../types';
import { ACTION_COLORS, PALETTE } from '../utils/theme';
import { fmtSigned } from '../utils/format';
import { ActionBadge } from './ui/badges';
import { ConvictionBar } from './ui/ConvictionBar';

interface Props {
  item: IndustryRecommendation;
  selected?: boolean;
  linkTo?: string;
  /** Receives the industry name so the parent can pass one stable callback to every card. */
  onSelect?: (industry: string) => void;
  compact?: boolean;
}

function IndustryCard({ item, selected, linkTo, onSelect, compact }: Props) {
  const rawConv = Number(item.conviction_score);
  const weight = item.industry_weight != null ? Number(item.industry_weight) : 1;
  const weighted = item.weighted_conviction_score != null
    ? Number(item.weighted_conviction_score)
    : rawConv;
  // Display the weighted score by default (it's what drives ordering); show
  // raw on hover. When weight=1, weighted === raw and we hide the badge.
  const conv = weighted;
  const isMuted = weight === 0;
  const isBoosted = weight > 1.05;
  const weightChanged = Math.abs(weight - 1) > 0.01;
  const reps = item.representative_tickers ?? [];

  const body = (
    <div
      className={`bg-card border rounded-lg p-3 text-left transition-colors min-w-0 ${
        selected ? 'border-accent-500' : 'border-border hover:border-text-secondary'
      } ${onSelect || linkTo ? 'cursor-pointer' : ''} ${isMuted ? 'opacity-50' : ''}`}
    >
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-text-primary truncate">{item.industry}</h3>
          {item.etf_symbol && (
            <p className="text-[10px] text-text-secondary">
              ETF: <span className="font-mono">{item.etf_symbol}</span>
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {weightChanged && (
            <span
              className={`text-[9px] font-bold tracking-wider uppercase px-1.5 py-px rounded ${
                isMuted
                  ? 'bg-gray-800 text-text-secondary'
                  : isBoosted
                    ? 'bg-emerald-900/40 text-emerald-300 border border-emerald-500/30'
                    : 'bg-amber-900/40 text-amber-300 border border-amber-500/30'
              }`}
              title={`Your industry weight: ${weight.toFixed(2)}× (raw conviction ${fmtSigned(rawConv)})`}
            >
              {weight.toFixed(2)}×
            </span>
          )}
          <ActionBadge action={item.action} bordered className="tracking-wider" />
        </div>
      </div>

      <div className="flex items-center gap-2 mb-2">
        <ConvictionBar score={conv} centered />
        <span
          className="text-xs font-mono text-text-primary tabular-nums"
          title={
            weightChanged
              ? `Weighted ${fmtSigned(conv)} = raw ${fmtSigned(rawConv)} × ${weight.toFixed(2)}`
              : undefined
          }
        >
          {fmtSigned(conv)}
        </span>
      </div>

      {!compact && (
        <>
          <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px] text-text-secondary mb-2">
            <span>
              Members: <span className="text-text-primary font-mono">{item.member_count ?? 0}</span>
            </span>
            <span>
              Signals: <span className="text-text-primary font-mono">{item.signal_count}</span>
            </span>
            {item.breadth_positive_pct !== null && (
              <span>
                Bullish: <span className="text-text-primary font-mono">{Number(item.breadth_positive_pct).toFixed(0)}%</span>
              </span>
            )}
            {item.etf_rsi_14 !== null && (
              <span>
                RSI: <span className="text-text-primary font-mono">{Number(item.etf_rsi_14).toFixed(0)}</span>
              </span>
            )}
          </div>

          {reps.length > 0 && (
            <div className="flex flex-wrap gap-1 pt-2 border-t border-border">
              {reps.map((t) => {
                const tc = ACTION_COLORS[t.action] ?? PALETTE.gray;
                return (
                  <span
                    key={t.ticker}
                    className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                    style={{ color: tc, background: `${tc}1a` }}
                    title={`${t.ticker}: ${t.action} (${fmtSigned(t.conviction)})`}
                  >
                    {t.ticker}
                  </span>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );

  if (linkTo) {
    return (
      <Link to={linkTo} className="block min-w-0">
        {body}
      </Link>
    );
  }
  if (onSelect) {
    return (
      <button type="button" onClick={() => onSelect(item.industry)} className="block w-full min-w-0 text-left">
        {body}
      </button>
    );
  }
  return body;
}

export default memo(IndustryCard);
