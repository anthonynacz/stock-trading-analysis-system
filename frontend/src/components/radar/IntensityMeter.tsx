import { INTENSITY_HINT, fmtIntensity, intensityValue } from './model';

// Literal class map so Tailwind's content scan keeps the widths.
const WIDTH = { sm: 'w-6', md: 'w-9' } as const;

/**
 * Intensity (0-100) as a number plus a small bar. Violet on purpose: it ranks
 * how hard a stock is moving and must not read as an up/down signal, a
 * probability or a confidence level (signal-model.md §4.4).
 */
export function IntensityMeter({
  value,
  size = 'md',
  className = '',
}: {
  value: number | null | undefined;
  size?: keyof typeof WIDTH;
  className?: string;
}) {
  const v = intensityValue(value);
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`} title={INTENSITY_HINT}>
      <span className="font-semibold tabular-nums text-text-primary">{fmtIntensity(value)}</span>
      <span className={`inline-block h-1.5 ${WIDTH[size]} rounded-full bg-violet-400/20 overflow-hidden`} aria-hidden="true">
        <span className="block h-full rounded-full bg-violet-400" style={{ width: `${v ?? 0}%` }} />
      </span>
    </span>
  );
}

export default IntensityMeter;
