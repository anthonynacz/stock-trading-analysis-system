import { CSSProperties, ReactNode } from 'react';

export interface SegmentOption<K extends string> {
  key: K;
  label: ReactNode;
  title?: string;
  /** Renders the option non-interactive (tier-locked); `title` still shows on hover. */
  disabled?: boolean;
  /** Trailing node (count, status dot) that may depend on the active state. */
  badge?: (active: boolean) => ReactNode;
}

const SIZE = {
  xs: 'px-1.5 py-0.5 text-[10px]',
  sm: 'px-3 py-1.5 text-xs',
} as const;

const PILL_ACTIVE = 'bg-accent-900/60 text-accent-300 border border-accent-600/60';
const PILL_INACTIVE = 'bg-card border border-border text-text-secondary hover:text-text-primary';
const JOINED_ACTIVE = 'bg-border text-text-primary';
const JOINED_INACTIVE = 'bg-card text-text-secondary hover:text-text-primary';

export function SegmentedControl<K extends string>({
  options,
  value,
  onChange,
  size = 'sm',
  variant = 'pill',
  fullWidth = false,
  optionClassName,
  optionStyle,
  className = '',
}: {
  options: SegmentOption<K>[];
  value: K;
  onChange: (key: K) => void;
  size?: keyof typeof SIZE;
  variant?: 'pill' | 'joined';
  fullWidth?: boolean;
  /** Extra classes per option (e.g. a per-tier colour). */
  optionClassName?: (key: K, active: boolean) => string | undefined;
  optionStyle?: (key: K, active: boolean) => CSSProperties | undefined;
  className?: string;
}) {
  const joined = variant === 'joined';
  const root = joined
    ? 'relative inline-flex rounded-md border border-border overflow-hidden'
    : 'relative flex gap-1';
  return (
    <div className={`${root} ${fullWidth ? 'w-full' : ''} ${className}`}>
      {options.map((o) => {
        const active = o.key === value;
        const look = joined
          ? `border-l border-border first:border-l-0 ${active ? JOINED_ACTIVE : JOINED_INACTIVE}`
          : `rounded ${active ? PILL_ACTIVE : PILL_INACTIVE}`;
        return (
          <button
            key={o.key}
            type="button"
            onClick={() => {
              if (!o.disabled) onChange(o.key);
            }}
            title={o.title}
            disabled={o.disabled}
            aria-disabled={o.disabled || undefined}
            aria-pressed={active}
            className={`relative font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${SIZE[size]} ${look} ${
              fullWidth ? 'flex-1' : ''
            } ${optionClassName?.(o.key, active) ?? ''}`}
            style={optionStyle?.(o.key, active)}
          >
            {o.label}
            {o.badge?.(active)}
          </button>
        );
      })}
    </div>
  );
}

export default SegmentedControl;
