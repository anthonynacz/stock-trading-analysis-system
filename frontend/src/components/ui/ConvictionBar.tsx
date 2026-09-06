import { PALETTE } from '../../utils/theme';

// Literal class map so Tailwind's content scan keeps the height utilities.
const THICKNESS = {
  'h-1': 'h-1',
  'h-1.5': 'h-1.5',
  'h-2': 'h-2',
} as const;

interface ConvictionBarProps {
  score: number;
  thickness?: keyof typeof THICKNESS;
  /** Render the numeric score to the right of the track. */
  showValue?: boolean;
  /** Bipolar rendering: zero at the centre, negatives fill leftwards (IndustryCard). */
  centered?: boolean;
  /** Extra classes for the track (e.g. `mx-1`). */
  className?: string;
}

export function ConvictionBar({
  score,
  thickness = 'h-1.5',
  showValue = false,
  centered = false,
  className = '',
}: ConvictionBarProps) {
  const abs = Math.min(Math.abs(score), 100);
  const isPositive = score >= 0;

  const track = centered ? (
    <div className={`relative w-full ${THICKNESS[thickness]} bg-border rounded-full overflow-hidden ${className}`}>
      <div className="absolute top-0 left-1/2 w-px h-full bg-text-secondary/40" />
      <div
        className={`absolute top-0 ${isPositive ? 'left-1/2' : 'right-1/2'} h-full`}
        style={{ width: `${abs / 2}%`, background: isPositive ? PALETTE.green : PALETTE.red }}
      />
    </div>
  ) : (
    <div className={`${THICKNESS[thickness]} rounded-full bg-border overflow-hidden ${className}`}>
      <div
        className={`h-full rounded-full transition-all ${isPositive ? 'bg-green-500' : 'bg-red-500'}`}
        style={{ width: `${abs}%` }}
      />
    </div>
  );

  if (!showValue) return track;

  return (
    <div className="flex items-center gap-1.5">
      <div className="flex-1 min-w-0">{track}</div>
      <span className="text-[10px] font-mono text-text-secondary w-6 text-right">{score}</span>
    </div>
  );
}
