import { useState } from 'react';
import type { Recommendation } from '../types';
import { ACTION_COLORS, getActionLabel } from '../utils/theme';
import SignalBadge from './SignalBadge';
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
        <div className="px-4 pb-4 pt-0 border-t border-border space-y-4" onClick={(e) => e.stopPropagation()}>
          {/* Rationale */}
          <p className="text-sm text-text-primary leading-relaxed mt-3">{rec.rationale}</p>

          {/* Signals */}
          {rec.signals && rec.signals.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-text-secondary uppercase mb-2">Signals</h4>
              <div className="flex flex-wrap gap-1.5">
                {rec.signals.map((sig, i) => (
                  <SignalBadge key={i} signal={sig.signal} points={sig.points} />
                ))}
              </div>
            </div>
          )}

          {/* Price targets */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            {rec.target_price != null && (
              <div>
                <span className="text-text-secondary text-xs">Target</span>
                <p className="text-text-primary font-mono">${rec.target_price.toFixed(2)}</p>
              </div>
            )}
            {rec.stop_loss_price != null && (
              <div>
                <span className="text-text-secondary text-xs">Stop Loss</span>
                <p className="text-text-primary font-mono">${rec.stop_loss_price.toFixed(2)}</p>
              </div>
            )}
            {rec.risk_level && (
              <div>
                <span className="text-text-secondary text-xs block mb-1">Risk</span>
                <RiskBadge level={rec.risk_level} />
              </div>
            )}
          </div>

          {/* Entry strategy */}
          {rec.entry_strategy && (
            <div>
              <h4 className="text-xs font-semibold text-text-secondary uppercase mb-1">Entry Strategy</h4>
              <p className="text-sm text-text-primary">{rec.entry_strategy}</p>
            </div>
          )}

          {/* Exit rules */}
          {rec.exit_rules && (
            <div>
              <h4 className="text-xs font-semibold text-text-secondary uppercase mb-1">Exit Rules</h4>
              <p className="text-sm text-text-primary">{rec.exit_rules}</p>
            </div>
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
