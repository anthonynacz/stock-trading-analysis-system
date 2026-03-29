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
