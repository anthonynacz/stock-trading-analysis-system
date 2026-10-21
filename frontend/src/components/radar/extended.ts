// "Extended — don't chase": a move far beyond VWAP that is still accelerating, the shape of a blow-off.
// Provisional rule (2026-10-21, after CRL entered within a minute of its high): in the last 30 days of live
// entries, those far beyond VWAP (>= 1.5 daily sigmas, in the move's direction) and still accelerating
// (acceleration >= 1, in the move's direction) won 20% of the time with a mean of -0.40% at exit (25 entries,
// up and down), against 25% and -0.15% for the rest. Small sample: the thresholds are being re-tested on the
// replay corpus. Note: the engine's dvwap and acc signals are signed; the move's direction is applied here.

import type { RadarDirection } from '../../types';

export const EXT_VWAP_SIGMA = 1.5;   // distance beyond VWAP in the stock's daily sigmas (engine signal dvwap)
export const EXT_ACCEL = 1.0;        // bar-on-bar acceleration (engine signal acc)

export function isExtended(
  direction: RadarDirection | undefined,
  dvwapSigma: number | null | undefined,
  accel: number | null | undefined,
): boolean {
  if (!direction || dvwapSigma === null || dvwapSigma === undefined || accel === null || accel === undefined) {
    return false;
  }
  const d = direction === 'down' ? -1 : 1;
  return d * dvwapSigma >= EXT_VWAP_SIGMA && d * accel >= EXT_ACCEL;
}

export const EXTENDED_LABEL = "Extended — don't chase";

export function extendedText(direction: RadarDirection | undefined): string {
  const where = direction === 'down' ? 'far below VWAP and still falling faster' : 'far above VWAP and still accelerating';
  const back = direction === 'down' ? 'a bounce back toward VWAP' : 'a pullback toward VWAP';
  return (
    `Extended — don't chase: the stock is ${where}, the shape of a blow-off. ` +
    `Recently most radar entries like this reversed within minutes. Wait for ${back}.`
  );
}

export const EXTENDED_HINT =
  `At least ${EXT_VWAP_SIGMA}× its normal daily move beyond VWAP and still accelerating in the same direction. ` +
  'In the last 30 days of radar entries, 1 in 5 like this won (mean -0.4% at exit). Provisional rule, being re-tested.';
