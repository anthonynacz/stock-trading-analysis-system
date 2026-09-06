import type { StrikeRecommendation } from '../types';
import { fmtDelta, fmtInt, fmtNum, fmtPrice, fmtStrike } from '../utils/format';
import { isHeavyTheta } from '../utils/options';

export function StrikeCard({
  rec,
  type,
  variant = 'full',
}: {
  rec: StrikeRecommendation;
  type: 'CALL' | 'PUT';
  variant?: 'full' | 'mini';
}) {
  const isCall = type === 'CALL';
  const mini = variant === 'mini';
  const labelColor = isCall ? 'text-green-400' : 'text-red-400';
  const bgColor = isCall ? 'bg-green-900/10' : 'bg-red-900/10';
  // The mini card sits inside a bordered ticker card, so its own border is softer.
  const borderColor = mini
    ? isCall ? 'border-green-600/40' : 'border-red-600/40'
    : isCall ? 'border-green-600/60' : 'border-red-600/60';

  if (mini) {
    return (
      <div className={`${bgColor} border ${borderColor} rounded p-2`}>
        <div className="flex items-center justify-between mb-1">
          <span className={`text-[10px] font-bold uppercase ${labelColor}`}>{type}</span>
          <span className="text-[10px] text-text-secondary">{rec.days_to_expiry}d</span>
        </div>
        <div className="grid grid-cols-3 gap-x-2 text-[11px]">
          <div>
            <span className="text-text-secondary">Strike </span>
            <span className="font-mono text-text-primary">{fmtStrike(rec.strike)}</span>
          </div>
          <div>
            <span className="text-text-secondary">Prem </span>
            <span className="font-mono text-text-primary">{fmtPrice(rec.premium_estimate)}</span>
          </div>
          <div>
            <span className="text-text-secondary">Delta </span>
            <span className="font-mono text-text-primary">{fmtDelta(rec.delta_estimate)}</span>
          </div>
        </div>
        <div className="text-[10px] text-text-secondary mt-1">
          Breakeven {fmtPrice(rec.breakeven)} &middot; OI {fmtInt(rec.open_interest)} &middot; Exp {rec.expiry}
        </div>
      </div>
    );
  }

  const heavyTheta = isHeavyTheta(rec.theta_estimate, rec.premium_estimate);

  return (
    <div className={`${bgColor} border ${borderColor} rounded-lg p-3 space-y-2`}>
      <div className="flex items-center justify-between">
        <span className={`text-xs font-bold uppercase ${labelColor}`}>{type}</span>
        <span className="text-xs text-text-secondary">{rec.days_to_expiry}d to expiry</span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <span className="text-text-secondary">Strike</span>
        <span className="text-text-primary font-mono text-right">{fmtPrice(rec.strike)}</span>
        <span className="text-text-secondary">Expiry</span>
        <span className="text-text-primary text-right">{rec.expiry}</span>
        <span className="text-text-secondary">Premium</span>
        <span className="text-text-primary font-mono text-right">{fmtPrice(rec.premium_estimate)}</span>
        <span className="text-text-secondary">Delta</span>
        <span className="text-text-primary font-mono text-right">{fmtDelta(rec.delta_estimate, 3)}</span>
        {rec.theta_estimate != null && (
          <>
            <span className="text-text-secondary">Theta</span>
            <span className={`font-mono text-right ${heavyTheta ? 'text-amber-400' : 'text-text-primary'}`}>
              {fmtNum(rec.theta_estimate, 4)}/day
            </span>
          </>
        )}
        {rec.vega_estimate != null && (
          <>
            <span className="text-text-secondary">Vega</span>
            <span className="text-text-primary font-mono text-right">{fmtNum(rec.vega_estimate, 4)}</span>
          </>
        )}
        <span className="text-text-secondary">Breakeven</span>
        <span className="text-text-primary font-mono text-right">{fmtPrice(rec.breakeven)}</span>
        <span className="text-text-secondary">Open Interest</span>
        <span className="text-text-primary font-mono text-right">{fmtInt(rec.open_interest)}</span>
      </div>
      {heavyTheta && rec.theta_estimate != null && (
        <div className="flex items-center gap-1.5 text-[10px] text-amber-400 mt-1">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
          Heavy theta decay — {fmtNum((Math.abs(rec.theta_estimate) / rec.premium_estimate) * 100, 1)}% premium/day
        </div>
      )}
      <p className="text-[11px] text-text-secondary leading-relaxed border-t border-border/40 pt-2 mt-1">
        {rec.explanation}
      </p>
    </div>
  );
}

export default StrikeCard;
