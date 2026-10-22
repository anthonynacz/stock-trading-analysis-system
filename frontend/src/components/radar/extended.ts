// "Extended — don't chase": an up move entered on a sharp acceleration, the shape of a blow-off.
// Rule from the top-entry study (2026-10-21, 58 sessions 2026-07-31..10-21 through the real engine): up entries
// whose entry bar or heating bar had acceleration >= 1.5 topped out within 30 minutes 33% of the time against
// 14% for the other up entries, with -19 bp vs +8 bp at 30 minutes (both date halves and the newest sessions
// agree). How far the stock already ran (day move, distance from VWAP) did not predict tops, and acceleration
// says nothing on down entries, so the tag is up-only. Display only: entries are unchanged.

import type { RadarDirection } from '../../types';

export const EXT_ACCEL = 1.5;        // engine signal acc, on the entry bar or the heating bar

const num = (v: number | null | undefined): number | null =>
  v === null || v === undefined || Number.isNaN(v) ? null : v;

/** Up entries (or heating names) accelerating sharply: the higher of the bars' accelerations >= EXT_ACCEL. */
export function isExtended(direction: RadarDirection | undefined, ...accels: (number | null | undefined)[]): boolean {
  if (direction !== 'up') return false;
  const xs = accels.map(num).filter((x): x is number => x !== null);
  return xs.length > 0 && Math.max(...xs) >= EXT_ACCEL;
}

export const EXTENDED_LABEL = "Extended — don't chase";

export function extendedText(): string {
  return (
    "Extended — don't chase: the move jumped on a sharp acceleration, the shape of a blow-off top. " +
    'In backtests a third of up entries like this topped out within 30 minutes. Wait for a pullback (0.15-0.3%) before entering.'
  );
}

export const EXTENDED_HINT =
  `Up move whose entry or heating bar accelerated sharply (acceleration >= ${EXT_ACCEL}). In backtests (58 sessions) ` +
  '33% of these topped out within 30 minutes vs 14% of other up entries (-0.19% vs +0.08% at 30 min).';
