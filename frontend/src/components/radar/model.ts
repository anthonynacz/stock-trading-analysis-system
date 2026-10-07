// Momentum Radar display helpers: plain-English texts (github-spec §12.5),
// number formatting on top of utils/format.ts, and member sorting. Pure, no React.

import type {
  RadarDirection,
  RadarExit,
  RadarMember,
  RadarMemberState,
  RadarScanStatus,
  RadarSectorBanner,
  RadarSnapshot,
} from '../../types';
import { DASH, fmtInt, fmtNum, fmtPrice, fmtSigned } from '../../utils/format';
import { toMs } from './clock';

type Num = number | null | undefined;

// ── Texts ─────────────────────────────────────────────────────────────────

/** Fallback labels for exit codes; the engine's `exit_detail` wins when present. */
export const EXIT_TEXT: Record<string, string> = {
  FADE: 'Momentum faded',
  DRY: 'Volume dried up',
  STALL: 'Stalled – no new high/low',
  REVERSAL: 'Sharp reversal',
  GIVEBACK: 'Gave back most of the move',
  VWAP_CROSS: 'Fell back through VWAP',
  SESSION_END: 'Market closed',
  HALT_LONG: 'Long trading halt',
  DATA_STALE: 'No fresh data',
  DISPLACED: 'Replaced by a stronger mover',
};

export const STATE_TEXT: Record<RadarMemberState, string> = { racing: 'Racing', cooling: 'Cooling', halted: 'Halted' };

export const STATE_HINT: Record<RadarMemberState, string> = {
  racing: 'The move is still going strong.',
  cooling: 'The last bar fell short. It drops off if the next bar also falls short.',
  halted: 'No new trades: possibly a trading halt. It stays on the radar until trading resumes or the pause runs long.',
};

export const INTENSITY_HINT =
  "How strong the move's ingredients are right now (0-100). For sorting only: not a probability or a forecast.";

export const LATE_HINT = 'Added during a catch-up scan, more than 10 minutes after the move was confirmed.';

/** The engine's exit_detail is already a full sentence that starts with the reason: show it alone. */
export function exitText(x: Pick<RadarExit, 'exit_detail' | 'exit_reason'>): string {
  return x.exit_detail || EXIT_TEXT[x.exit_reason] || 'Dropped off';
}

const SCAN_PROBLEM: Partial<Record<RadarScanStatus, string>> = {
  degraded: 'The last scan ran into a data problem',
  no_data: 'The last scan received no market data',
  error: 'The last scan failed',
};

/** True for scan statuses that get a status banner (degraded / no_data / error). */
export const isScanProblem = (status: RadarScanStatus) => status in SCAN_PROBLEM;

/** The scanner's message already says what went wrong and that members are held: show it as is. */
export function scanProblem(st: Pick<RadarSnapshot, 'status' | 'message'>): string {
  return (
    st.message ||
    `${SCAN_PROBLEM[st.status] ?? 'The last scan had a problem'}. Stocks already on the radar are held, not dropped, until data returns.`
  );
}

/** Sector banner copy: "+N more held back (A, B)"; null when nothing was held back. */
export function heldBack(s: RadarSectorBanner): { head: string; tickers: string; tail: string } | null {
  const tickers = s.tickers ?? [];
  const n = s.count ?? tickers.length;
  if (!(n > 0)) return null;
  return {
    head: `+${n} more held back`,
    tickers: tickers.length ? `(${tickers.join(', ')})` : '',
    tail: `${n === 1 ? 'It is' : 'They are'} also ${s.direction === 'down' ? 'falling' : 'rising'}, but the radar lists at most 3 stocks per sector and direction.`,
  };
}

/** health.published_late: earlier scans delivered late. */
export function publishedLate(n: Num): string {
  if (n === null || n === undefined || n < 0) return DASH;
  return n === 0 ? 'none' : `${n} earlier scan${n === 1 ? '' : 's'}`;
}

function ordinal(n: number): string {
  const tens = n % 100;
  if (n % 10 === 1 && tens !== 11) return `${n}st`;
  if (n % 10 === 2 && tens !== 12) return `${n}nd`;
  if (n % 10 === 3 && tens !== 13) return `${n}rd`;
  return `${n}th`;
}

export const backText = (episode: number) => `Back on the radar for the ${ordinal(episode)} time today.`;

// ── Numbers ───────────────────────────────────────────────────────────────

/** Signed percent with one decimal: `+4.9%`. Inputs are already percents. */
export const fmtMove = (v: Num) => fmtSigned(v, 1, '%');

/** Signed sigma units: `+2.9σ`. */
export const fmtSigma = (v: Num) => fmtSigned(v, 1, 'σ');

/** Multiple of normal: `4.1×`. */
export const fmtTimes = (v: Num) => (v === null || v === undefined ? DASH : `${fmtNum(v, 1)}×`);

/** Sub-dollar prices keep four decimals. */
export const fmtRadarPrice = (v: Num) => fmtPrice(v, v !== null && v !== undefined && Math.abs(v) < 1 ? 4 : 2);

/** Intensity clamped to 0-100 and rounded; null when missing. */
export function intensityValue(v: Num): number | null {
  return v === null || v === undefined || Number.isNaN(v) ? null : Math.max(0, Math.min(100, Math.round(v)));
}

export const fmtIntensity = (v: Num) => fmtInt(intensityValue(v));

/** Tailwind text colour for a signed value (green up, red down). */
export function signClass(v: Num): string {
  if (v === null || v === undefined || v === 0 || Number.isNaN(v)) return 'text-text-primary';
  return v > 0 ? 'text-green-400' : 'text-red-400';
}

export const DIR_TEXT_CLASS: Record<RadarDirection, string> = { up: 'text-green-400', down: 'text-red-400' };
export const dirArrow = (d: RadarDirection) => (d === 'down' ? '▼' : '▲');

// ── Members ───────────────────────────────────────────────────────────────

export type RadarFilter = 'all' | 'up' | 'down';
export type RadarSort = 'intensity' | 'newest' | 'longest' | 'move';

export const RADAR_SORTS: { key: RadarSort; label: string }[] = [
  { key: 'intensity', label: 'Highest intensity' },
  { key: 'newest', label: 'Newest' },
  { key: 'longest', label: 'Longest on radar' },
  { key: 'move', label: 'Biggest move' },
];

/** Stable identity of one stay on the radar (a stock can come back later in the day). */
export const memberKey = (m: Pick<RadarMember, 'ticker' | 'entered_at'>) => `${m.ticker}|${m.entered_at}`;

export function sortMembers(list: RadarMember[], by: RadarSort): RadarMember[] {
  const entered = (m: RadarMember) => toMs(m.entered_at) ?? 0;
  return [...list].sort((a, b) => {
    if (by === 'newest') return entered(b) - entered(a);
    if (by === 'longest') return entered(a) - entered(b);
    if (by === 'move') return Math.abs(b.move_since_entry_pct ?? 0) - Math.abs(a.move_since_entry_pct ?? 0);
    return (b.intensity ?? 0) - (a.intensity ?? 0);
  });
}

/** Minutes on the radar; derived from entered_at when the engine left it out. */
export function minutesOnRadar(m: RadarMember, tickId: string): number | null {
  if (m.minutes_on_radar !== null && m.minutes_on_radar !== undefined) return m.minutes_on_radar;
  const entered = toMs(m.entered_at);
  const tick = toMs(tickId);
  return entered !== null && tick !== null ? (tick - entered) / 60_000 : null;
}

/** Ticker deep link into the Dashboard's detail panel (same as Discord alert links). */
export const tickerHref = (ticker: string) => `/?ticker=${encodeURIComponent(ticker)}`;
