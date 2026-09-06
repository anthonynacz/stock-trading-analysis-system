export const RISK_LEVELS = ['conservative', 'moderate', 'aggressive'] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

/** Daily theta above 2% of premium is flagged as heavy decay. */
export const isHeavyTheta = (
  theta: number | null | undefined,
  premium: number | null | undefined,
): boolean => theta != null && premium != null && premium > 0 && Math.abs(theta) / premium > 0.02;
