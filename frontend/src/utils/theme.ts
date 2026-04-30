export const ACTION_COLORS: Record<string, string> = {
  STRONG_BUY: '#2ea043',
  BUY: '#56d364',
  HOLD: '#d29922',
  SELL: '#f85149',
  STRONG_SELL: '#da3633',
};

export const STATUS_COLORS: Record<string, string> = {
  NEW_ENTRANT: '#58a6ff',
  EXISTING: '#8b949e',
  REMOVED: '#f85149',
};

export const IMPACT_COLORS: Record<string, string> = {
  HIGH: '#f85149',
  MEDIUM: '#d29922',
  LOW: '#8b949e',
};

export const SENTIMENT_COLOR = (score: number): string => {
  if (score > 0.3) return '#56d364';
  if (score < -0.3) return '#f85149';
  return '#d29922';
};

export function getActionLabel(action: string): string {
  return action.replace('_', ' ');
}

const SECTOR_SHORT_LABELS: Record<string, string> = {
  'AI/Semiconductors': 'AI/SEMI',
  'Fintech/Payments': 'FINTECH',
  'Energy/Commodities': 'ENERGY',
  'Healthcare/Biotech': 'HEALTH',
  'Consumer/Cloud/Enterprise': 'CLOUD',
  'Industrials/Defense': 'DEFENSE',
  'Power/Utilities/Nuclear': 'POWER',
  'Communications/Media': 'COMMS',
};

export function shortSectorLabel(sector: string | null | undefined): string | null {
  if (!sector) return null;
  if (SECTOR_SHORT_LABELS[sector]) return SECTOR_SHORT_LABELS[sector];
  // Fallback: take token before the first '/' and uppercase, capped at 8 chars
  const head = sector.split('/')[0]?.trim() ?? sector;
  return head.slice(0, 8).toUpperCase();
}
