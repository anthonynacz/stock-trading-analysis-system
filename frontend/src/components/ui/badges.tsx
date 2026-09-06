import { ACTION_COLORS, PALETTE, getActionLabel } from '../../utils/theme';
import { fmtSigned } from '../../utils/format';

// Literal class maps (not template strings) so Tailwind's content scan keeps them.
const PILL_SIZE = {
  xxs: 'px-1.5 py-px text-[9px]',
  xs: 'px-1.5 py-0.5 text-[10px]',
  sm: 'px-2 py-0.5 text-xs',
} as const;

export type PillSize = keyof typeof PILL_SIZE;

export interface ColorPillProps {
  label: string;
  /** Hex colour; background is `color + '22'`, optional border `color + '55'`. */
  color: string;
  size?: PillSize;
  bordered?: boolean;
  className?: string;
  title?: string;
}

export function ColorPill({ label, color, size = 'xs', bordered = false, className = '', title }: ColorPillProps) {
  return (
    <span
      className={`rounded font-bold uppercase ${PILL_SIZE[size]} ${className}`}
      style={{
        backgroundColor: `${color}22`,
        color,
        border: bordered ? `1px solid ${color}55` : undefined,
      }}
      title={title}
    >
      {label}
    </span>
  );
}

/** Action pill (STRONG BUY / BUY / …). Unknown actions fall back to gray. */
export function ActionBadge({ action, ...rest }: { action: string } & Omit<ColorPillProps, 'label' | 'color'>) {
  return <ColorPill label={getActionLabel(action)} color={ACTION_COLORS[action] ?? PALETTE.gray} {...rest} />;
}

/** Amber "↓ NATURAL BAND" chip shown next to a gate-demoted action. */
export function DemotionChip({ naturalAction, title }: { naturalAction: string; title?: string }) {
  return (
    <span
      className="text-[9px] font-bold uppercase tracking-wider px-1.5 py-px rounded bg-amber-900/40 text-amber-300 border border-amber-500/30 cursor-help"
      title={title}
    >
      ↓ {getActionLabel(naturalAction)}
    </span>
  );
}

const RISK_CLASSES: Record<string, string> = {
  LOW: 'bg-green-900/60 text-green-400',
  MEDIUM: 'bg-amber-900/60 text-amber-400',
  HIGH: 'bg-red-900/60 text-red-400',
};

const RISK_SIZE = {
  xxs: 'px-1 py-px text-[9px]',
  xs: 'px-1.5 py-px text-[10px] font-medium',
} as const;

export function RiskBadge({ level, size = 'xs' }: { level: string; size?: keyof typeof RISK_SIZE }) {
  return (
    <span className={`rounded ${RISK_SIZE[size]} ${RISK_CLASSES[level] ?? 'bg-gray-800 text-text-secondary'}`}>
      {level}
    </span>
  );
}

/** Signed number, green when >= 0 and red otherwise. */
export function SignedScore({ value, digits = 0, className = '' }: { value: number; digits?: number; className?: string }) {
  return (
    <span className={`font-mono ${value >= 0 ? 'text-green-400' : 'text-red-400'} ${className}`}>
      {fmtSigned(value, digits)}
    </span>
  );
}
