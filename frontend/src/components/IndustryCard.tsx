import { Link } from 'react-router-dom';
import type { IndustryRecommendation } from '../types';

const ACTION_COLOR: Record<string, string> = {
  STRONG_BUY: '#2ea043',
  BUY: '#56d364',
  HOLD: '#d29922',
  SELL: '#f85149',
  STRONG_SELL: '#da3633',
};

function ActionBadge({ action }: { action: string }) {
  const c = ACTION_COLOR[action] ?? '#8b949e';
  return (
    <span
      className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded"
      style={{ color: c, background: `${c}22`, border: `1px solid ${c}55` }}
    >
      {action.replace('_', ' ')}
    </span>
  );
}

function ConvictionBar({ score }: { score: number }) {
  const width = Math.min(100, (Math.abs(score) / 100) * 100);
  const color = score >= 0 ? '#2ea043' : '#f85149';
  const side = score >= 0 ? 'left-1/2' : 'right-1/2';
  return (
    <div className="relative w-full h-1.5 bg-border rounded-full overflow-hidden">
      <div className="absolute top-0 left-1/2 w-px h-full bg-text-secondary/40" />
      <div
        className={`absolute top-0 ${side} h-full`}
        style={{ width: `${width / 2}%`, background: color }}
      />
    </div>
  );
}

interface Props {
  item: IndustryRecommendation;
  selected?: boolean;
  linkTo?: string;
  onClick?: () => void;
  compact?: boolean;
}

export default function IndustryCard({ item, selected, linkTo, onClick, compact }: Props) {
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
      className={`bg-card border rounded-lg p-3 text-left transition-colors ${
        selected ? 'border-accent-500' : 'border-border hover:border-text-secondary'
      } ${onClick || linkTo ? 'cursor-pointer' : ''} ${isMuted ? 'opacity-50' : ''}`}
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
              title={`Your industry weight: ${weight.toFixed(2)}× (raw conviction ${rawConv >= 0 ? '+' : ''}${rawConv.toFixed(0)})`}
            >
              {weight.toFixed(2)}×
            </span>
          )}
          <ActionBadge action={item.action} />
        </div>
      </div>

      <div className="flex items-center gap-2 mb-2">
        <ConvictionBar score={conv} />
        <span
          className="text-xs font-mono text-text-primary tabular-nums"
          title={
            weightChanged
              ? `Weighted ${conv >= 0 ? '+' : ''}${conv.toFixed(0)} = raw ${rawConv >= 0 ? '+' : ''}${rawConv.toFixed(0)} × ${weight.toFixed(2)}`
              : undefined
          }
        >
          {conv >= 0 ? '+' : ''}
          {conv.toFixed(0)}
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
                const tc = ACTION_COLOR[t.action] ?? '#8b949e';
                return (
                  <span
                    key={t.ticker}
                    className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                    style={{ color: tc, background: `${tc}1a` }}
                    title={`${t.ticker}: ${t.action} (${t.conviction > 0 ? '+' : ''}${t.conviction.toFixed(0)})`}
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
      <Link to={linkTo} className="block">
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className="block w-full text-left">
        {body}
      </button>
    );
  }
  return body;
}
