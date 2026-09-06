import type { SignalDetail } from '../types';

interface SignalBulletProps {
  signal: SignalDetail;
  /** Smaller type (text-[10px], w-6 points column); hides `detail` unless `detail` is set. */
  compact?: boolean;
  /** Underline the signal name (DayComparison: signal is new vs the previous day). */
  highlight?: boolean;
  /** Override detail visibility. Defaults to `!compact`. In compact mode detail renders on a second line. */
  detail?: boolean;
}

export function SignalBullet({ signal, compact = false, highlight = false, detail = !compact }: SignalBulletProps) {
  // Gate signals carry 0 points and must render as "0", not "+0".
  const isPositive = signal.points > 0;
  const points = `${isPositive ? '+' : ''}${signal.points}`;
  const pointColor = isPositive ? 'text-green-400' : 'text-red-400';
  const nameClass = `text-text-primary ${highlight ? 'underline decoration-accent-500/60' : ''}`;

  if (compact) {
    return (
      <li className="text-[10px] leading-tight">
        <div className="flex items-start gap-1">
          <span className={`font-mono font-bold shrink-0 w-6 text-right ${pointColor}`}>{points}</span>
          <span className={nameClass}>{signal.signal}</span>
        </div>
        {detail && signal.detail && (
          <p className="text-text-secondary ml-7 leading-tight">{signal.detail}</p>
        )}
      </li>
    );
  }

  return (
    <li className="flex items-start gap-2 text-xs">
      <span className={`font-mono font-bold mt-px w-7 shrink-0 text-right ${pointColor}`}>{points}</span>
      <span className={nameClass}>
        {signal.signal}
        {detail && signal.detail && (
          <span className="text-text-secondary"> — {signal.detail}</span>
        )}
      </span>
    </li>
  );
}
