import type { RadarMember } from '../../types';
import { DEFAULT_EXIT_RULES, exitWatch, type ExitRules, type HardCheck, type WatchLevel } from './exitRules';

// Literal class maps (Tailwind purge).
const LEVEL_DOT: Record<WatchLevel, string> = { ok: 'bg-green-400', watch: 'bg-amber-400', near: 'bg-red-400' };
const LEVEL_TEXT: Record<WatchLevel, string> = { ok: 'text-green-300', watch: 'text-amber-300', near: 'text-red-300' };
const PILL_CLASS: Record<WatchLevel, string> = {
  ok: '',
  watch: 'bg-amber-900/30 text-amber-300 border-amber-500/30',
  near: 'bg-red-900/40 text-red-300 border-red-500/40',
};
const PILL_LABEL: Record<WatchLevel, string> = { ok: '', watch: 'Watch', near: 'Near exit' };

/** Small pill beside the state chip when a member is getting close to an exit rule. */
export function ExitWatchPill({ m, rules = DEFAULT_EXIT_RULES }: { m: RadarMember; rules?: ExitRules }) {
  const w = exitWatch(m, rules);
  if (w.level === 'ok') return null;
  return (
    <span
      className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-px rounded whitespace-nowrap border cursor-help ${PILL_CLASS[w.level]}`}
      title={`Close to an exit rule:\n${w.notes.join('\n')}`}
    >
      {PILL_LABEL[w.level]}
    </span>
  );
}

/** One gauge: the bar spans min..max, the red zone is the side of the line that exits. */
function Gauge({ h }: { h: HardCheck }) {
  const span = h.max - h.min;
  const pos = (v: number) => Math.max(0, Math.min(100, ((v - h.min) / span) * 100));
  const line = pos(h.line);
  const val = h.value === null ? null : pos(h.value);
  const zone = h.exitsWhen === 'below' ? { left: 0, width: line } : { left: line, width: 100 - line };
  return (
    <div className="grid grid-cols-[7.5rem_1fr] sm:grid-cols-[9rem_1fr_11rem] items-center gap-x-3 gap-y-0.5" title={h.hint}>
      <span className="flex items-center gap-1.5 text-text-secondary">
        <span className={`inline-block w-1.5 h-1.5 rounded-full ${LEVEL_DOT[h.level]}`} aria-hidden="true" />
        {h.label}
      </span>
      <div className="relative h-2.5 rounded-full bg-border/60" role="img" aria-label={`${h.label}: ${h.text}, ${h.lineText}`}>
        <div className="absolute inset-y-0 rounded-full bg-red-500/25" style={{ left: `${zone.left}%`, width: `${zone.width}%` }} />
        <div className="absolute -inset-y-0.5 w-0.5 bg-red-400" style={{ left: `calc(${line}% - 1px)` }} />
        {val !== null && (
          <div
            className={`absolute top-1/2 w-3 h-3 -mt-1.5 -ml-1.5 rounded-full border-2 border-card ${LEVEL_DOT[h.level]}`}
            style={{ left: `${val}%` }}
          />
        )}
      </div>
      <span className="col-start-2 sm:col-start-auto text-[11px] tabular-nums">
        <span className={`font-semibold ${LEVEL_TEXT[h.level]}`}>{h.text}</span>
        <span className="text-text-secondary"> · {h.lineText}</span>
      </span>
    </div>
  );
}

/**
 * How close a member is to each exit rule: the three instant exits as gauges
 * (dot = now, red line = exit), the three "weak bar" checks as chips, and the
 * count of weak bars in a row. Mirrors the engine's rules; the engine decides.
 */
export default function ExitWatch({ m, rules = DEFAULT_EXIT_RULES }: { m: RadarMember; rules?: ExitRules }) {
  const EXIT_RULES = rules;
  const w = exitWatch(m, rules);
  const minutes = m.minutes_on_radar ?? 0;
  return (
    <div className="rounded border border-border/70 bg-card/40 px-3 py-2.5 space-y-2">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <span className="text-[10px] font-bold uppercase tracking-wider text-text-secondary">Exit watch</span>
        <span className="text-[11px] text-text-secondary">Dot = now · red line = drops off at once</span>
      </div>
      <div className="space-y-2 text-xs">
        {w.hard.map((h) => (
          <Gauge key={h.key} h={h} />
        ))}
      </div>
      <div className="pt-1 border-t border-border/50 text-xs space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          {w.soft.map((s) => (
            <span
              key={s.key}
              title={s.hint}
              className={`px-1.5 py-0.5 rounded border text-[11px] cursor-help ${
                s.failing ? 'border-amber-500/40 bg-amber-900/30 text-amber-200' : 'border-border text-text-secondary'
              }`}
            >
              {s.failing ? '⚠' : '✓'} {s.label}: <span className="tabular-nums">{s.text}</span>
            </span>
          ))}
        </div>
        <p className="text-[11px] text-text-secondary leading-snug">
          Weak bars in a row:{' '}
          <span className={`font-semibold tabular-nums ${w.softFails > 0 ? 'text-amber-300' : 'text-text-primary'}`}>
            {w.softFails} of {EXIT_RULES.softFails}
          </span>
          . A bar is weak when any check above is weak; {EXIT_RULES.softFails} in a row drop the stock
          {minutes < EXIT_RULES.minDwellMin ? ` (only after ${EXIT_RULES.minDwellMin} minutes on the radar)` : ''}. One
          strong bar resets the count.
        </p>
      </div>
    </div>
  );
}
