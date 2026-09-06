export const BRAND = 'Vela';

// Named hex tokens. Mirrors tailwind.config.js colours (kept in sync by hand —
// the config is outside tsconfig `include`, so it cannot import this module).
export const PALETTE = {
  green: '#2ea043',
  greenLight: '#56d364',
  amber: '#d29922',
  red: '#f85149',
  redDark: '#da3633',
  blue: '#58a6ff',
  gray: '#8b949e',
  grayMuted: '#6e7681',
  grayDark: '#484f58',
  purple: '#a371f7',
  violet: '#8b5cf6',
  border: '#21262d',
  textPrimary: '#e6edf3',
} as const;

export const ACTION_COLORS: Record<string, string> = {
  STRONG_BUY: PALETTE.green,
  BUY: PALETTE.greenLight,
  HOLD: PALETTE.amber,
  SELL: PALETTE.red,
  STRONG_SELL: PALETTE.redDark,
};

// Tailwind class equivalents of ACTION_COLORS (tokens defined in tailwind.config.js).
export const ACTION_TEXT_CLASS: Record<string, string> = {
  STRONG_BUY: 'text-strong-buy',
  BUY: 'text-buy',
  HOLD: 'text-hold',
  SELL: 'text-sell',
  STRONG_SELL: 'text-strong-sell',
};

export const PNL_COLOR = (v: number | null | undefined): string =>
  v == null || v === 0 ? PALETTE.gray : v > 0 ? PALETTE.green : PALETTE.red;

export const POSITION_TYPE_COLORS: Record<string, string> = {
  CALL: PALETTE.green,
  PUT: PALETTE.red,
  STOCK: PALETTE.blue,
};

// Multibagger scanner tiers (not subscription tiers — those live in AppNav).
export const TIER_COLORS = {
  HOT: PALETTE.red,
  WATCH: PALETTE.amber,
  MONITOR: PALETTE.blue,
  IGNORE: PALETTE.grayDark,
} as const;

export const VERDICT_COLORS: Record<string, string> = {
  BUY_CALL: PALETTE.green,
  BUY_CALL_SPREAD: PALETTE.green,
  BUY_PUT: PALETTE.redDark,
  BUY_PUT_SPREAD: PALETTE.redDark,
  SELL_PUT_SPREAD: PALETTE.greenLight,
  SELL_CALL_SPREAD: PALETTE.red,
  SELL_IRON_CONDOR: PALETTE.amber,
  BUY_STRADDLE: PALETTE.violet,
  NO_TRADE: PALETTE.grayMuted,
};

export const BIAS_COLORS: Record<string, string> = {
  BULLISH: PALETTE.green,
  BEARISH: PALETTE.redDark,
  NEUTRAL: PALETTE.gray,
};

export const PHASE_COLORS: Record<string, string> = {
  pending: PALETTE.border,
  running: PALETTE.blue,
  done: PALETTE.green,
  failed: PALETTE.red,
};

export const IMPACT_COLORS: Record<string, string> = {
  HIGH: PALETTE.red,
  MEDIUM: PALETTE.amber,
  LOW: PALETTE.gray,
};

// Tailwind-class variant for chips (amber HIGH, gray otherwise).
export const IMPACT_CLASSES: Record<string, string> = {
  HIGH: 'bg-amber-900/40 text-amber-400',
  MEDIUM: 'bg-gray-800/60 text-text-secondary',
  LOW: 'bg-gray-900/40 text-text-secondary',
};

export const SENTIMENT_COLOR = (score: number): string => {
  if (score > 0.3) return PALETTE.greenLight;
  if (score < -0.3) return PALETTE.red;
  return PALETTE.amber;
};

// Tailwind-class variant. Headline lists use a tighter ±0.1 threshold on
// purpose (ResearchDetail); pass it explicitly rather than changing the default.
export const SENTIMENT_CLASS = (score: number, threshold = 0.3): string => {
  if (score > threshold) return 'text-green-400';
  if (score < -threshold) return 'text-red-400';
  return 'text-text-secondary';
};

export function getActionLabel(action: string): string {
  return action.replace('_', ' ');
}

export const SECTOR_SHORT_LABELS: Record<string, string> = {
  'AI/Semiconductors': 'AI/SEMI',
  'Fintech/Payments': 'FINTECH',
  'Energy/Commodities': 'ENERGY',
  'Healthcare/Biotech': 'HEALTH',
  'Consumer/Cloud/Enterprise': 'CLOUD',
  'Industrials/Defense': 'DEFENSE',
  'Power/Utilities/Nuclear': 'POWER',
  'Communications/Media': 'COMMS',
};

export const SECTORS = Object.keys(SECTOR_SHORT_LABELS);

export function shortSectorLabel(sector: string | null | undefined): string | null {
  if (!sector) return null;
  if (SECTOR_SHORT_LABELS[sector]) return SECTOR_SHORT_LABELS[sector];
  // Fallback: take token before the first '/' and uppercase, capped at 8 chars
  const head = sector.split('/')[0]?.trim() ?? sector;
  return head.slice(0, 8).toUpperCase();
}
