import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Recommendation, SignalDetail } from '../types';
import { ACTION_COLORS, getActionLabel, shortSectorLabel } from '../utils/theme';
import { actionLabel, detectDemotion } from '../utils/recommendation';
import OptionsTable from './OptionsTable';

interface RecommendationCardProps {
  recommendation: Recommendation;
}

function ConvictionBar({ score }: { score: number }) {
  const abs = Math.min(Math.abs(score), 100);
  const isPositive = score >= 0;

  return (
    <div className="flex items-center gap-1.5">
      <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${isPositive ? 'bg-green-500' : 'bg-red-500'}`}
          style={{ width: `${abs}%` }}
        />
      </div>
      <span className="text-[10px] font-mono text-text-secondary w-6 text-right">
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
    <li className="flex items-start gap-1.5 text-xs">
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

function RiskBadge({ level }: { level: string }) {
  const colors: Record<string, string> = {
    LOW: 'bg-green-900/60 text-green-400',
    MEDIUM: 'bg-amber-900/60 text-amber-400',
    HIGH: 'bg-red-900/60 text-red-400',
  };
  return (
    <span className={`px-1.5 py-px rounded text-[10px] font-medium ${colors[level] ?? 'bg-gray-800 text-text-secondary'}`}>
      {level}
    </span>
  );
}

function formatRelativeTime(iso: string): string {
  const now = Date.now();
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return '';
  const diffMin = Math.max(0, Math.round((now - ts) / 60000));
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return `${Math.round(diffH / 24)}d ago`;
}

function RevisionBadge({ rec }: { rec: Recommendation }) {
  if (!rec.revision_number || rec.revision_number === 0) return null;
  const priorConv = rec.prior_conviction_score;
  const currConv = rec.conviction_score;
  const convDelta =
    priorConv != null && currConv != null ? currConv - priorConv : null;
  const actionFlipped =
    rec.prior_action != null && rec.prior_action !== rec.action;
  const isRecent = rec.revised_at
    ? Date.now() - new Date(rec.revised_at).getTime() < 4 * 60 * 60 * 1000
    : false;

  return (
    <span className="relative group inline-flex">
      <span
        className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase bg-accent-900/50 text-accent-300 border border-accent-500/40 flex items-center gap-1 cursor-help"
        aria-label="Revised recommendation"
      >
        <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
        REV{rec.revision_number > 1 ? ` ${rec.revision_number}` : ''}
        {isRecent && rec.revised_at && (
          <span className="font-normal normal-case opacity-80">
            · {formatRelativeTime(rec.revised_at)}
          </span>
        )}
      </span>
      {/* Hover tooltip with diff */}
      <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 hidden group-hover:block z-50 pointer-events-none">
        <div className="bg-gray-900 border border-accent-500/60 text-white text-[10px] px-2 py-1.5 rounded whitespace-nowrap shadow-lg space-y-0.5">
          <div className="text-accent-300 font-semibold uppercase tracking-wider text-[9px]">
            Revision {rec.revision_number}
          </div>
          {rec.prior_action != null && (
            <div>
              Action:{' '}
              <span className={actionFlipped ? 'text-amber-300 font-semibold' : 'text-text-secondary'}>
                {rec.prior_action.replace('_', ' ')} → {rec.action.replace('_', ' ')}
              </span>
            </div>
          )}
          {priorConv != null && currConv != null && convDelta != null && (
            <div>
              Conviction:{' '}
              <span className="font-mono">
                {priorConv > 0 ? '+' : ''}{priorConv} → {currConv > 0 ? '+' : ''}{currConv}
              </span>{' '}
              <span className={convDelta >= 0 ? 'text-green-400' : 'text-red-400'}>
                ({convDelta >= 0 ? '+' : ''}{convDelta.toFixed(0)})
              </span>
            </div>
          )}
          {rec.revised_at && (
            <div className="text-text-secondary">
              {new Date(rec.revised_at).toLocaleTimeString()}
            </div>
          )}
          {rec.revision_reason && (
            <div className="text-text-secondary max-w-xs whitespace-normal pt-1 border-t border-accent-500/30 mt-1">
              {rec.revision_reason}
            </div>
          )}
        </div>
      </div>
    </span>
  );
}

export default function RecommendationCard({ recommendation: rec }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const borderColor = ACTION_COLORS[rec.action] ?? '#21262d';
  const navigate = useNavigate();

  const openOptionsLab = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigate(`/options-lab?ticker=${encodeURIComponent(rec.ticker)}&auto=1`);
  };

  const isRevised = (rec.revision_number ?? 0) > 0;
  const sectorShort = shortSectorLabel(rec.sector);
  const demotion = detectDemotion(
    rec.conviction_score,
    rec.action,
    rec.signals,
  );
  const actionTooltip = demotion.isDemoted
    ? `Score ${Number(rec.conviction_score ?? 0).toFixed(0)}: natural band ${actionLabel(demotion.naturalAction)}. ` +
      `Demoted to ${actionLabel(demotion.finalAction)}` +
      (demotion.gateName ? ` by ${demotion.gateName}.` : '.') +
      (demotion.gateDetail ? ` ${demotion.gateDetail}` : '')
    : undefined;

  return (
    <div
      className={`bg-card rounded-lg border overflow-hidden cursor-pointer transition-colors hover:border-text-secondary/30 ${
        isRevised ? 'border-accent-500/40 ring-1 ring-accent-500/20' : 'border-border'
      }`}
      style={{ borderLeftWidth: '3px', borderLeftColor: borderColor }}
      onClick={() => setExpanded((v) => !v)}
    >
      {/* Collapsed view */}
      <div className="px-3 py-2 space-y-1.5">
        <div className="flex items-center gap-2">
          <span
            className={demotion.isDemoted ? 'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase cursor-help' : 'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase'}
            style={{ backgroundColor: borderColor + '22', color: borderColor }}
            title={actionTooltip}
          >
            {getActionLabel(rec.action)}
          </span>
          {demotion.isDemoted && (
            <span
              className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-px rounded bg-amber-900/40 text-amber-300 border border-amber-500/30 cursor-help"
              title={actionTooltip}
            >
              ↓ {actionLabel(demotion.naturalAction)}
            </span>
          )}
          <RevisionBadge rec={rec} />
          <span className="text-sm font-bold text-text-primary">{rec.ticker}</span>
          {sectorShort && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-blue-900/40 text-blue-300 border border-blue-500/30"
              title={rec.sector ?? undefined}
            >
              {sectorShort}
            </span>
          )}
          {rec.current_price != null && (
            <span className="text-xs font-mono text-text-secondary">
              ${rec.current_price.toFixed(2)}
            </span>
          )}
          {rec.signal_count != null && (
            <span className="text-[10px] text-text-secondary ml-auto">
              {rec.signal_count} sig{rec.signal_count !== 1 ? 's' : ''}
            </span>
          )}
          <button
            onClick={openOptionsLab}
            title={`Run deep options analysis for ${rec.ticker}`}
            className={`${rec.signal_count != null ? '' : 'ml-auto'} px-2 py-0.5 rounded text-[10px] font-semibold bg-accent-900/40 text-accent-300 hover:bg-accent-800/60 hover:text-accent-200 transition-colors flex items-center gap-1 shrink-0`}
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            Options Lab
          </button>
          <svg
            className={`w-3.5 h-3.5 text-text-secondary transition-transform shrink-0 ${expanded ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
        {rec.conviction_score != null && <ConvictionBar score={rec.conviction_score} />}
      </div>

      {/* Expanded view */}
      {expanded && (
        <div className="px-3 pb-2.5 pt-0 border-t border-border space-y-2" onClick={(e) => e.stopPropagation()}>
          {/* Revision diff line — when this row was overwritten by a same-day re-run */}
          {isRevised && (
            <div className="mt-2 px-2 py-1 rounded bg-accent-900/20 border border-accent-500/30 text-[11px] text-text-primary flex items-center gap-2 flex-wrap">
              <span className="text-accent-300 font-semibold uppercase tracking-wider text-[9px]">
                Revised
              </span>
              {rec.prior_action != null && rec.prior_action !== rec.action && (
                <span>
                  <span className="text-text-secondary">action </span>
                  <span className="font-mono">{rec.prior_action.replace('_', ' ')}</span>
                  <span className="text-text-secondary"> → </span>
                  <span className="font-mono text-amber-300">{rec.action.replace('_', ' ')}</span>
                </span>
              )}
              {rec.prior_conviction_score != null && rec.conviction_score != null && (
                <span>
                  <span className="text-text-secondary">conv </span>
                  <span className="font-mono">{rec.prior_conviction_score > 0 ? '+' : ''}{rec.prior_conviction_score}</span>
                  <span className="text-text-secondary"> → </span>
                  <span className="font-mono">{rec.conviction_score > 0 ? '+' : ''}{rec.conviction_score}</span>
                  <span
                    className={`font-mono ${
                      rec.conviction_score - rec.prior_conviction_score >= 0
                        ? 'text-green-400'
                        : 'text-red-400'
                    }`}
                  >
                    ({rec.conviction_score - rec.prior_conviction_score >= 0 ? '+' : ''}
                    {(rec.conviction_score - rec.prior_conviction_score).toFixed(0)})
                  </span>
                </span>
              )}
              {rec.revised_at && (
                <span className="text-text-secondary ml-auto">
                  {new Date(rec.revised_at).toLocaleTimeString()}
                </span>
              )}
              {rec.revision_reason && (
                <div className="w-full text-text-secondary italic leading-snug">
                  {rec.revision_reason}
                </div>
              )}
            </div>
          )}

          {/* Signals */}
          {rec.signals && rec.signals.length > 0 && (
            <ul className="space-y-0.5 mt-2">
              {rec.signals.map((sig, i) => (
                <SignalBullet key={i} signal={sig} />
              ))}
            </ul>
          )}

          {/* Price / Risk compact row */}
          <div className="flex items-center gap-3 text-xs flex-wrap">
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
              <span className="text-[10px] text-text-secondary bg-border/60 px-1.5 py-0.5 rounded">
                {rec.catalyst_type.replace(/_/g, ' ')}
              </span>
            )}
          </div>

          {/* Entry / Exit */}
          {rec.entry_strategy && rec.entry_strategy !== 'HOLD' && (
            <p className="text-xs text-text-secondary">
              Entry: <span className="text-text-primary">{rec.entry_strategy}</span>
            </p>
          )}
          {rec.exit_rules && (
            <p className="text-xs text-text-secondary">
              Exit: <span className="text-text-primary">{rec.exit_rules}</span>
            </p>
          )}

          {/* Options */}
          {rec.suggested_options.length > 0 && (
            <div>
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase mb-1">Suggested Options</h4>
              <OptionsTable options={rec.suggested_options} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
