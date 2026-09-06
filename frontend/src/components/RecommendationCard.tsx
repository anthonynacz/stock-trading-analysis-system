import { memo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Recommendation } from '../types';
import { ACTION_COLORS, getActionLabel, shortSectorLabel } from '../utils/theme';
import { buildDemotionTooltip, detectDemotion } from '../utils/recommendation';
import { fmtPrice, fmtSigned } from '../utils/format';
import { ActionBadge, DemotionChip, RiskBadge } from './ui/badges';
import { ConvictionBar } from './ui/ConvictionBar';
import { SignalBullet } from './SignalBullet';
import { SuggestedOptionsList } from './SuggestedOptionsList';

interface RecommendationCardProps {
  recommendation: Recommendation;
  /** When true, render a rotate-out selection checkbox in the header. */
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (ticker: string) => void;
  /** Opens the full TickerDetail panel for this ticker (Dashboard wires it to setSelectedTicker). */
  onOpenDetail?: (ticker: string) => void;
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

/** `REV · Xh ago` chip; the tooltip carries the reason plus the prior→current action/conviction diff. */
export function RevisionBadge({ rec }: { rec: Recommendation }) {
  if (!rec.revision_number || rec.revision_number === 0) return null;
  const isRecent = rec.revised_at
    ? Date.now() - new Date(rec.revised_at).getTime() < 4 * 60 * 60 * 1000
    : false;
  const tooltip = [rec.revision_reason ?? 'Revised'];
  if (rec.prior_action != null && rec.prior_action !== rec.action) {
    tooltip.push(`${getActionLabel(rec.prior_action)} → ${getActionLabel(rec.action)}`);
  }
  if (rec.prior_conviction_score != null && rec.conviction_score != null) {
    tooltip.push(`conv ${fmtSigned(rec.prior_conviction_score)} → ${fmtSigned(rec.conviction_score)}`);
  }

  return (
    <span
      className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase bg-accent-900/50 text-accent-300 border border-accent-500/40 inline-flex items-center gap-1 cursor-help"
      aria-label="Revised recommendation"
      title={tooltip.join(' · ')}
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
  );
}

function RecommendationCard({ recommendation: rec, selectable, selected, onToggleSelect, onOpenDetail }: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const borderColor = ACTION_COLORS[rec.action] ?? '#21262d';
  const navigate = useNavigate();

  const openOptionsLab = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigate(`/options-lab?ticker=${encodeURIComponent(rec.ticker)}&auto=1`);
  };

  const toggleExpanded = () => setExpanded((v) => !v);

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    // Only react to keys on the card itself — the inner Options Lab <button>
    // and checkbox handle their own Enter/Space and bubble here.
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      toggleExpanded();
    }
  };

  const isRevised = (rec.revision_number ?? 0) > 0;
  const sectorShort = shortSectorLabel(rec.sector);
  const demotion = detectDemotion(
    rec.conviction_score,
    rec.action,
    rec.signals,
  );
  const actionTooltip = buildDemotionTooltip(rec.conviction_score, demotion);

  return (
    <div
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      className={`bg-card rounded-lg border overflow-hidden cursor-pointer transition-colors hover:border-text-secondary/30 ${
        selected
          ? 'border-amber-500 ring-2 ring-amber-500/50'
          : isRevised
            ? 'border-accent-500/40 ring-1 ring-accent-500/20'
            : 'border-border'
      }`}
      style={{ borderLeftWidth: '3px', borderLeftColor: borderColor }}
      onClick={toggleExpanded}
      onKeyDown={onKeyDown}
    >
      {/* Collapsed view */}
      <div className="px-3 py-2 space-y-1.5">
        <div className="flex items-center gap-2">
          {selectable && (
            <span
              role="checkbox"
              aria-checked={selected}
              title={selected ? 'Unselect for rotation' : 'Select to rotate out'}
              onClick={(e) => { e.stopPropagation(); onToggleSelect?.(rec.ticker); }}
              className={`flex items-center justify-center w-4 h-4 rounded border text-[10px] leading-none shrink-0 transition-colors ${
                selected
                  ? 'bg-amber-500 border-amber-500 text-black'
                  : 'bg-card border-text-secondary/50 text-transparent hover:border-amber-500'
              }`}
            >
              ✓
            </span>
          )}
          <ActionBadge
            action={rec.action}
            className={actionTooltip ? 'cursor-help' : ''}
            title={actionTooltip}
          />
          {demotion.isDemoted && (
            <DemotionChip naturalAction={demotion.naturalAction} title={actionTooltip} />
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
              {fmtPrice(rec.current_price)}
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
        {rec.conviction_score != null && <ConvictionBar score={rec.conviction_score} showValue />}
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
                  <span className="font-mono">{getActionLabel(rec.prior_action)}</span>
                  <span className="text-text-secondary"> → </span>
                  <span className="font-mono text-amber-300">{getActionLabel(rec.action)}</span>
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
                    ({fmtSigned(rec.conviction_score - rec.prior_conviction_score)})
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
                Target <span className="font-mono text-text-primary">{fmtPrice(rec.target_price)}</span>
              </span>
            )}
            {rec.stop_loss_price != null && (
              <span className="text-text-secondary">
                Stop <span className="font-mono text-text-primary">{fmtPrice(rec.stop_loss_price)}</span>
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

          {/* Options — compact summary; the full detail lives in TickerDetail */}
          {rec.suggested_options.length > 0 && (
            <div>
              <h4 className="text-[10px] font-semibold text-text-secondary uppercase mb-1">Suggested Options</h4>
              <SuggestedOptionsList options={rec.suggested_options} />
            </div>
          )}

          {onOpenDetail && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onOpenDetail(rec.ticker); }}
              className="btn-secondary w-full justify-center"
            >
              View details
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(RecommendationCard);
