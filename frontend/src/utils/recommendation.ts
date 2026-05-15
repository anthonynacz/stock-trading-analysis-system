// Helpers for reasoning about a recommendation's score / action / gate state.
//
// The backend recommendation engine separates "how strong is the setup"
// (conviction_score) from "what should you do right now" (action). Most of
// the time the action is just the score's natural band. But certain GATE
// signals (currently `Stale Move Gate`) demote the action without reducing
// the score — e.g. a +100 score with action=BUY because the stock has
// already run on the catalyst and chasing isn't the right move.
//
// Without a UI cue, that looks like a contradiction. These helpers detect
// the demotion and surface the gate that caused it.

import type { SignalDetail } from '../types';

interface Band {
  min: number;
  action: 'STRONG_BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG_SELL';
}

// Mirrors recommendation_engine._classify and preferences.py._classify on the
// backend. If those bands ever change, update here in lockstep.
const BANDS: Band[] = [
  { min: 60,    action: 'STRONG_BUY' },
  { min: 30,    action: 'BUY' },
  { min: -15,   action: 'HOLD' },
  { min: -30,   action: 'SELL' },
  { min: -100,  action: 'STRONG_SELL' },
];

export function classifyConviction(score: number): Band['action'] {
  for (const b of BANDS) {
    if (score >= b.min) return b.action;
  }
  return 'STRONG_SELL';
}

const BAND_RANK: Record<Band['action'], number> = {
  STRONG_SELL: 0,
  SELL: 1,
  HOLD: 2,
  BUY: 3,
  STRONG_BUY: 4,
};

export interface DemotionInfo {
  /** True iff the displayed action is in a lower band than the conviction score
   *  alone would imply — meaning a gate has demoted the action. */
  isDemoted: boolean;
  /** The band classification implied by the raw conviction score. */
  naturalAction: Band['action'];
  /** The action actually surfaced (post-gate). */
  finalAction: string;
  /** Name of the gate signal that explains the demotion, when found. */
  gateName: string | null;
  /** Detail text from the gate signal — typically explains why. */
  gateDetail: string | null;
}

/**
 * Detect whether a recommendation's action has been demoted from its
 * natural score-derived band by a gate signal. A "gate" signal is one that
 * carries 0 points (so it doesn't move the score) but explains the
 * demotion in its detail text — e.g. "Stale Move Gate".
 *
 * Returns an `isDemoted` flag plus the gate signal name and detail when
 * one is found; rendering can then show a chip + tooltip.
 */
export function detectDemotion(
  conviction: number | null | undefined,
  action: string,
  signals: SignalDetail[] | null | undefined,
): DemotionInfo {
  const score = Number(conviction ?? 0);
  const natural = classifyConviction(score);
  const isDemoted =
    (BAND_RANK[natural] ?? 2) > (BAND_RANK[action as Band['action']] ?? 2);

  if (!isDemoted) {
    return {
      isDemoted: false,
      naturalAction: natural,
      finalAction: action,
      gateName: null,
      gateDetail: null,
    };
  }

  // Find the signal that explains the demotion. Heuristics, in order:
  //   1. Signal name ends with "Gate" (most explicit).
  //   2. Detail text contains "downgrade" or "demot" (catches future gates
  //      that don't follow the naming convention).
  const gate =
    (signals ?? []).find((s) => /\bgate\b/i.test(s.signal)) ??
    (signals ?? []).find((s) => s.detail && /downgrad|demot/i.test(s.detail));

  return {
    isDemoted: true,
    naturalAction: natural,
    finalAction: action,
    gateName: gate?.signal ?? null,
    gateDetail: gate?.detail ?? null,
  };
}

export function actionLabel(action: string): string {
  return action.replace('_', ' ');
}
