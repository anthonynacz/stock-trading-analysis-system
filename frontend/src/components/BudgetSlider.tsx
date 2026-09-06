import { fmtPrice } from '../utils/format';

const SIZE = {
  sm: 'text-[10px]',
  md: 'text-xs',
} as const;

export const BUDGET_MIN = 50;
export const BUDGET_MAX = 10000;

export function BudgetSlider({
  value,
  onChange,
  label = 'Max Budget',
  size = 'sm',
  className = '',
}: {
  value: number;
  onChange: (value: number) => void;
  label?: string;
  size?: keyof typeof SIZE;
  className?: string;
}) {
  return (
    <div className={`space-y-1 ${className}`}>
      <div className={`flex justify-between text-text-secondary ${SIZE[size]}`}>
        <span>{label}</span>
        <span className="font-mono text-text-primary">{fmtPrice(value, 0)}</span>
      </div>
      <input
        type="range"
        min={BUDGET_MIN}
        max={BUDGET_MAX}
        step={50}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={label}
        className="w-full h-1.5 rounded-full appearance-none bg-border cursor-pointer accent-accent-500"
      />
      <div className="flex justify-between text-[9px] text-text-secondary">
        <span>{fmtPrice(BUDGET_MIN, 0)}</span>
        <span>{fmtPrice(BUDGET_MAX, 0)}</span>
      </div>
    </div>
  );
}

export default BudgetSlider;
