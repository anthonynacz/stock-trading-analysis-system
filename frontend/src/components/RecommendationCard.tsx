import { useState } from 'react';
import type { Recommendation, SignalDetail } from '../types';
import { ACTION_COLORS, getActionLabel } from '../utils/theme';
import OptionsTable from './OptionsTable';

interface RecommendationCardProps {
  recommendation: Recommendation;
}

function ConvictionBar({ score }: { score: number }) {
  const abs = Math.min(Math.abs(score), 100);
  const isPositive = score >= 0;

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 rounded-full bg-border overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${isPositive ? 'bg-green-500' : 'bg-red-500'}`}
          style={{ width: `${abs}%` }}
        />
      </div>
      <span className="text-xs font-mono text-text-secondary w-8 text-right">
        {score}
      </span>
    </div>
  );
}

function SignalBullet({ signal }: { signal: SignalDetail }) {
  const isPositive = signal.points > 0;
  const prefix = isPositive ? '+' : '';
  const pointColor = isPositive ? 'text-green-400' : 'text-red-400';

  return (
    <li className="flex items-start gap-2 text-sm">
      <span className={`font-mono text-xs font-bold mt-0.5 w-8 shrink-0 text-right ${pointColor}`}>
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

function RiskBadge({ level }: { level: string }) {
  const colors: Record<string, string> = {
    LOW: 'bg-green-900/60 text-green-400',
    MEDIUM: 'bg-amber-900/60 text-amber-400',
    HIGH: 'bg-red-900/60 text-red-400',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors[level] ?? 'bg-gray-800 text-text-secondary'}`}>
      {level}
    </span>
  );
}

export default function RecommendationCard({ recommendation: rec }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const borderColor = ACTION_COLORS[rec.action] ?? '#21262d';

  return (
    <div
      className="bg-card rounded-lg border border-border overflow-hidden cursor-pointer transition-colors hover:border-text-secondary/30"
      style={{ borderLeftWidth: '4px', borderLeftColor: borderColor }}
      onClick={() => setExpanded((v) => !v)}
    >
      {/* Collapsed view */}
      <div className="p-4 flex items-center gap-4">
        <span
          className="px-2 py-1 rounded text-xs font-bold uppercase"
          style={{ backgroundColor: borderColor + '22', color: borderColor }}
        >
          {getActionLabel(rec.action)}
        </span>

        <span className="text-lg font-bold text-text-primary">{rec.ticker}</span>

        {rec.current_price != null && (
          <span className="text-sm font-mono text-text-secondary">
            ${rec.current_price.toFixed(2)}
          </span>
        )}

        <div className="flex-1 max-w-[200px] ml-auto">
          {rec.conviction_score != null && (
            <ConvictionBar score={rec.conviction_score} />
          )}
        </div>

        {rec.signal_count != null && (
          <span className="text-xs text-text-secondary whitespace-nowrap">
            {rec.signal_count} signal{rec.signal_count !== 1 ? 's' : ''}
          </span>
        )}

        <svg
          className={`w-4 h-4 text-text-secondary transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </div>

      {/* Expanded view */}
      {expanded && (
        <div className="px-4 pb-4 pt-0 border-t border-border space-y-3" onClick={(e) => e.stopPropagation()}>
          {/* Signals as bullet list */}
          {rec.signals && rec.signals.length > 0 && (
            <ul className="space-y-1.5 mt-3">
              {rec.signals.map((sig, i) => (
                <SignalBullet key={i} signal={sig} />
              ))}
            </ul>
          )}

          {/* Price / Risk compact row */}
          <div className="flex items-center gap-4 text-sm flex-wrap">
            {rec.target_price != null && (
              <span className="text-text-secondary">
                Target <span className="font-mono text-text-primary">${rec.target_price.toFixed(2)}</span>
              </span>
            )}
            {rec.stop_loss_price != null && (
              <span className="text-text-secondary">
                Stop <span className="font-mono text-text-primary">${rec.stop_loss_price.toFixed(2)}</span>
              </span>
            )}
            {rec.risk_level && <RiskBadge level={rec.risk_level} />}
            {rec.catalyst_type && (
              <span className="text-xs text-text-secondary bg-border/60 px-2 py-0.5 rounded">
                {rec.catalyst_type.replace(/_/g, ' ')}
              </span>
            )}
          </div>

          {/* Entry / Exit — only if meaningful */}
          {rec.entry_strategy && rec.entry_strategy !== 'HOLD' && (
            <p className="text-sm text-text-secondary">
              Entry: <span className="text-text-primary">{rec.entry_strategy}</span>
            </p>
          )}
          {rec.exit_rules && (
            <p className="text-sm text-text-secondary">
              Exit: <span className="text-text-primary">{rec.exit_rules}</span>
            </p>
          )}

          {/* Options */}
          {rec.suggested_options.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-text-secondary uppercase mb-2">Suggested Options</h4>
              <OptionsTable options={rec.suggested_options} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
