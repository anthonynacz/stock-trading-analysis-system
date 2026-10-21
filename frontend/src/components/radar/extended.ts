// "Extended — don't chase": an up move far above VWAP that is still accelerating, the shape of a blow-off top.
// Provisional rule (2026-10-21, after CRL entered within a minute of its high): in the last 30 days of live
// entries, up entries with VWAP distance >= 1.5 daily sigmas and acceleration >= 1 went 1 for 6 (mean -0.44%),
// against 28% winners for the other up entries. The same shape on down moves kept falling (79% winners), so the
// tag is up-side only. Small sample: the thresholds are being re-tested on the replay corpus.

import type { RadarDirection } from '../../types';

export const EXT_VWAP_SIGMA = 1.5;   // distance above VWAP in the stock's daily sigmas (engine signal dvwap)
export const EXT_ACCEL = 1.0;        // bar-on-bar acceleration (engine signal acc)

export function isExtended(
  direction: RadarDirection | undefined,
  dvwapSigma: number | null | undefined,
  accel: number | null | undefined,
): boolean {
  if (direction !== 'up' || dvwapSigma === null || dvwapSigma === undefined || accel === null || accel === undefined) {
    return false;
  }
  return dvwapSigma >= EXT_VWAP_SIGMA && accel >= EXT_ACCEL;
}

export const EXTENDED_LABEL = "Extended — don't chase";

export const EXTENDED_TEXT =
  "Extended — don't chase: the stock is far above VWAP and still accelerating, the shape of a blow-off top. " +
  'Recently most up entries like this reversed within minutes. Wait for a pullback toward VWAP.';

export const EXTENDED_HINT =
  `Up move at least ${EXT_VWAP_SIGMA}× its normal daily move above VWAP and still accelerating. ` +
  'In the last 30 days of radar entries, 5 of 6 like this lost. Provisional rule, being re-tested.';
