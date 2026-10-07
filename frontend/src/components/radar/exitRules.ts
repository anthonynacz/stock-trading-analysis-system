// Exit watch: how close a radar member is to each exit rule of the engine
// (backend/radar/engine.py `_exit_reason`, thresholds in radar/config.py
// PARAMS.hard_exit / PARAMS.hold). Display only: the engine decides.

import type { RadarMember } from '../../types';

/** radar-sm-1 thresholds (keep in sync with backend/radar/config.py). */
export const EXIT_RULES = {
  reversalZ15: 2.5, // REVERSAL: 15-min pace this many σ against the move
  giveback: 70, // GIVEBACK: % of the move from its base given back
  fadeZ30: 1.0, // FADE: 30-min pace under this…
  fadeZ15: 0.0, // …and 15-min pace under this
  dryRvol: 0.6, // DRY: volume under this multiple of normal
  stallMin: 30, // STALL: no new high/low for this long…
  stallZ30: 1.5, // …and 30-min pace under this
  softFails: 2, // soft rules exit after this many weak bars in a row…
  minDwellMin: 15, // …once the stock has been on the radar this long
} as const;

export type WatchLevel = 'ok' | 'watch' | 'near';

export interface HardCheck {
  key: 'reversal' | 'giveback' | 'vwap';
  label: string;
  /** Value on the gauge, oriented so that larger is safer except for giveback. */
  value: number | null;
  text: string;
  /** Exit line on the gauge. */
  line: number;
  lineText: string;
  min: number;
  max: number;
  /** The side of the line that exits. */
  exitsWhen: 'below' | 'above';
  level: WatchLevel;
  hint: string;
}

export interface SoftCheck {
  key: 'fade' | 'dry' | 'stall';
  label: string;
  failing: boolean | null;
  text: string;
  hint: string;
}

export interface ExitWatch {
  hard: HardCheck[];
  soft: SoftCheck[];
  softFails: number;
  level: WatchLevel;
  /** Short reasons for the pill tooltip, worst first. */
  notes: string[];
}

const num = (v: number | null | undefined): number | null =>
  v === null || v === undefined || Number.isNaN(v) ? null : v;

const sig = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}σ`;
const pct = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}%`;

const RANK: Record<WatchLevel, number> = { ok: 0, watch: 1, near: 2 };

export function exitWatch(m: RadarMember): ExitWatch {
  const d = m.direction === 'down' ? -1 : 1;
  const z15 = num(m.z15);
  const z30 = num(m.z30);
  const pace15 = z15 === null ? null : d * z15;
  const pace30 = z30 === null ? null : d * z30;
  const gb = num(m.giveback_pct);
  const vw = num(m.vwap_dist_pct) === null ? null : d * (m.vwap_dist_pct as number);
  const side = d > 0 ? 'above' : 'below';

  const hard: HardCheck[] = [
    {
      key: 'reversal',
      label: 'Sharp reversal',
      value: pace15,
      text: pace15 === null ? '—' : `${sig(pace15)} with the move (15 min)`,
      line: -EXIT_RULES.reversalZ15,
      lineText: `exits at ${sig(-EXIT_RULES.reversalZ15)}`,
      min: -4,
      max: 6,
      exitsWhen: 'below',
      level: pace15 === null ? 'ok' : pace15 <= -1.5 ? 'near' : pace15 < 0 ? 'watch' : 'ok',
      hint:
        'The last 15 minutes against the market, scaled by how much this stock normally moves. A move of 2.5σ ' +
        'the wrong way removes the stock at once: a calm stock reaches it with a small dip, a wild one needs a big one.',
    },
    {
      key: 'giveback',
      label: 'Gave back',
      value: gb,
      text: gb === null ? '—' : `${Math.max(0, gb).toFixed(0)}% of the move`,
      line: EXIT_RULES.giveback,
      lineText: `exits at ${EXIT_RULES.giveback}%`,
      min: 0,
      max: 100,
      exitsWhen: 'above',
      level: gb === null ? 'ok' : gb >= 50 ? 'near' : gb >= 30 ? 'watch' : 'ok',
      hint: 'How much of the run (from where it started to its best price) has been given back.',
    },
    {
      key: 'vwap',
      label: `Distance ${side} VWAP`,
      value: vw,
      text: vw === null ? '—' : pct(vw),
      line: 0,
      lineText: 'exits when it crosses VWAP',
      min: -2,
      max: 10,
      exitsWhen: 'below',
      level: vw === null ? 'ok' : vw <= 0.5 ? 'near' : vw <= 1.5 ? 'watch' : 'ok',
      hint: `VWAP is the day's volume-weighted average price. A stock racing ${d > 0 ? 'up' : 'down'} that falls back through it is dropped.`,
    },
  ];

  const fade = pace15 === null || pace30 === null ? null : pace30 < EXIT_RULES.fadeZ30 && pace15 < EXIT_RULES.fadeZ15;
  const dry = num(m.rvol) === null ? null : (m.rvol as number) < EXIT_RULES.dryRvol;
  const since = num(m.mins_since_extreme);
  const stall = since === null || pace30 === null ? null : since >= EXIT_RULES.stallMin && pace30 < EXIT_RULES.stallZ30;
  const soft: SoftCheck[] = [
    {
      key: 'fade',
      label: 'Momentum',
      failing: fade,
      text: pace30 === null ? '—' : `${sig(pace30)} over 30 min`,
      hint: 'Weak when the 30-minute pace is under 1σ and the last 15 minutes went against the move.',
    },
    {
      key: 'dry',
      label: 'Volume',
      failing: dry,
      text: num(m.rvol) === null ? '—' : `${(m.rvol as number).toFixed(1)}× normal`,
      hint: 'Weak when volume over the last 15 minutes is under 0.6× normal for the time of day.',
    },
    {
      key: 'stall',
      label: d > 0 ? 'New highs' : 'New lows',
      failing: stall,
      text: since === null ? '—' : since === 0 ? 'new one this bar' : `last one ${since} min ago`,
      hint: `Weak after 30 minutes without a new ${d > 0 ? 'high' : 'low'} while the 30-minute pace is under 1.5σ.`,
    },
  ];

  const softFails = m.soft_fails ?? 0;
  let level: WatchLevel = hard.reduce<WatchLevel>((w, h) => (RANK[h.level] > RANK[w] ? h.level : w), 'ok');
  const failing = soft.filter((s) => s.failing).length;
  if (softFails >= 1 || m.state === 'cooling') level = 'near';
  else if (failing > 0 && level === 'ok') level = 'watch';

  const notes = [
    ...hard.filter((h) => h.level !== 'ok').sort((a, b) => RANK[b.level] - RANK[a.level]).map((h) => `${h.label}: ${h.text} (${h.lineText})`),
    ...(softFails >= 1 ? [`${softFails} weak bar${softFails === 1 ? '' : 's'} in a row (drops off at ${EXIT_RULES.softFails})`] : []),
    ...soft.filter((s) => s.failing).map((s) => `${s.label} weak: ${s.text}`),
  ];
  return { hard, soft, softFails, level, notes };
}

/** One line per exit code: the rule that fired, for the dropped-off lists. */
export const EXIT_RULE_TEXT: Record<string, string> = {
  REVERSAL: 'Rule: removed at once when the last 15 minutes run 2.5σ or more against the move.',
  GIVEBACK: 'Rule: removed at once after giving back 70% of the run.',
  VWAP_CROSS: "Rule: removed when the price crosses back through the day's VWAP.",
  FADE: 'Rule: two weak bars in a row (30-min pace under 1σ and the last 15 minutes against the move).',
  DRY: 'Rule: two bars in a row with volume under 0.6× normal.',
  STALL: 'Rule: two bars in a row with no new high/low for 30 minutes and the 30-min pace under 1.5σ.',
  SESSION_END: 'Rule: the radar empties at the close.',
  HALT_LONG: 'Rule: removed after a trading pause of 30 minutes or more.',
  DATA_STALE: 'Rule: removed after three scans without fresh data.',
  DISPLACED: 'Rule: the radar holds at most 12 stocks; a much stronger mover took the place.',
};
