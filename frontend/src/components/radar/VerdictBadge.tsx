import { useState } from 'react';
import type { RadarOptionMetrics } from '../../types';
import { VERDICT_HINT, VERDICT_TEXT, verdictFor, type Verdict, type VerdictContext } from './verdict';
import { EXTENDED_HINT, EXTENDED_LABEL } from './extended';

// Literal class maps (Tailwind purge).
const PILL: Record<Verdict, string> = {
  favorable: 'bg-emerald-500/20 text-emerald-200 border-emerald-400/60',
  caution: 'bg-amber-500/20 text-amber-200 border-amber-400/60',
  avoid: 'bg-red-500/20 text-red-200 border-red-400/60',
  unknown: 'bg-gray-500/10 text-text-secondary border-border',
};
const ICON: Record<Verdict, string> = { favorable: '✓', caution: '!', avoid: '✕', unknown: '·' };
const DOT: Record<string, string> = { avoid: 'text-red-300', caution: 'text-amber-300', good: 'text-emerald-300' };

/**
 * "Enter with options?" verdict: a coloured pill (Favorable / Caution / Avoid) and the main reason in one
 * sentence. `full` adds the cost line and every point (expanded member details).
 */
export default function VerdictBadge({
  o,
  ctx,
  full = false,
}: {
  o: RadarOptionMetrics | undefined;
  ctx: VerdictContext;
  full?: boolean;
}) {
  const [more, setMore] = useState(false);
  const v = verdictFor(o, ctx);
  const kind = ctx.direction === 'down' ? 'puts' : 'calls';
  const showAll = full || more;
  const showCosts = v.costs !== null && (showAll || v.verdict === 'favorable');
  return (
    <div className="text-[11px] leading-snug">
      {ctx.extended && (
        <div className="mb-1">
          <span
            className="inline-block px-1.5 py-px rounded border font-bold cursor-help whitespace-nowrap bg-orange-500/25 text-orange-100 border-orange-400/70"
            title={EXTENDED_HINT}
          >
            <span aria-hidden="true">⚠ </span>
            {EXTENDED_LABEL}
          </span>
        </div>
      )}
      <div className="flex items-start gap-2">
        <span
          className={`shrink-0 px-1.5 py-px rounded border font-bold uppercase tracking-wider cursor-help whitespace-nowrap ${PILL[v.verdict]}`}
          title={VERDICT_HINT}
        >
          <span aria-hidden="true">{ICON[v.verdict]} </span>
          {VERDICT_TEXT[v.verdict]}
          <span className="font-semibold normal-case tracking-normal opacity-80"> · {kind}</span>
        </span>
        <span className="text-text-primary min-w-0">
          {v.headline}
          {!full && v.details.length > 0 && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setMore((x) => !x);
              }}
              className="ml-1.5 text-accent-300 hover:text-accent-200"
              aria-expanded={more}
            >
              {more ? 'less' : `+${v.details.length} more`}
            </button>
          )}
        </span>
      </div>
      {((showAll && v.details.length > 0) || showCosts || full) && (
        <div className="mt-1 pl-1 space-y-0.5">
          {(showAll ? v.details : []).map((p) => (
            <div key={p.text} className="text-text-secondary">
              <span aria-hidden="true" className={DOT[p.level]}>● </span>
              {p.text}
            </div>
          ))}
          {showCosts && <div className="text-text-secondary">{v.costs}</div>}
          {full && <div className="text-text-secondary/70 italic">Checklist, not advice: even favorable setups only roughly broke even after costs in backtests.</div>}
        </div>
      )}
    </div>
  );
}
