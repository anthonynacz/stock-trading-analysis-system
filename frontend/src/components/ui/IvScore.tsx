import { useState } from 'react';
import type { IvCostLabel, IvScore } from '../../types';
import { IV_COST_BANDS, IV_COST_TEXT, UNKNOWN_HINT, costLabelFor, ivScoreSummary } from '../../utils/ivScore';

// The IV cost score (backend/utils/iv_score.py): what a buyer pays for volatility now, 0-100. One badge for the
// dashboard, Research, the Radar and the Options Lab, with the "why" a click away. Coloured by the cost to buy
// now; holder-side warnings (IV crushing today) stay with the Radar's exits list.

// Literal class maps (Tailwind purge).
export const IV_COST_CLASS: Record<IvCostLabel | 'unknown', string> = {
  cheap: 'bg-emerald-500/15 text-emerald-200 border-emerald-400/50',
  fair: 'bg-gray-500/10 text-text-secondary border-border',
  pricey: 'bg-amber-500/15 text-amber-200 border-amber-400/50',
  expensive: 'bg-red-500/20 text-red-200 border-red-400/60',
  unknown: 'bg-gray-500/10 text-text-secondary border-border',
};
const BAR_CLASS: Record<IvCostLabel, string> = {
  cheap: 'bg-emerald-400',
  fair: 'bg-gray-400',
  pricey: 'bg-amber-400',
  expensive: 'bg-red-400',
};
const SIZE_CLASS = { xs: 'text-[11px]', sm: 'text-xs' } as const;

const CONFIDENCE_TEXT: Record<IvScore['confidence'], string> = {
  high: 'High confidence: a full history of clean daily IV readings plus the stock’s realised volatility.',
  medium: 'Medium confidence: some clean IV history (20–119 days) plus the stock’s realised volatility.',
  low: 'Low confidence: judged on one part only, usually IV against realised volatility (not enough clean IV history yet).',
};
const DOTS: Record<IvScore['confidence'], string> = { high: '●●●', medium: '●●○', low: '●○○' };

const pct = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? '—' : `${v.toFixed(d)}%`);

function eventTag(s: IvScore): string {
  if (s.event?.kind === 'earnings' && s.event.date) {
    const d = new Date(`${s.event.date}T12:00:00`);
    return `Earnings ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} priced`;
  }
  return 'Event priced';
}

/** "IV Pricey 68 ●●○", plus the event tag. Click opens the why-panel when `expandable`. */
export function IvScoreBadge({
  score,
  expandable = true,
  size = 'xs',
  extraTitle,
}: {
  score: IvScore | null | undefined;
  expandable?: boolean;
  size?: keyof typeof SIZE_CLASS;
  /** Lines added to the hover text (e.g. the Radar's IV change). */
  extraTitle?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!score) {
    return (
      <span className={`inline-block px-1.5 py-px rounded border font-semibold cursor-help whitespace-nowrap ${SIZE_CLASS[size]} ${IV_COST_CLASS.unknown}`} title={UNKNOWN_HINT}>
        IV unknown
      </span>
    );
  }
  const title = [
    `IV cost ${score.score}/100: ${IV_COST_TEXT[score.label]}${score.floored ? ' (raised: an event is priced in)' : ''}.`,
    ivScoreSummary(score),
    score.event?.note ?? '',
    CONFIDENCE_TEXT[score.confidence],
    extraTitle ?? '',
    expandable ? 'Click for the breakdown.' : '',
  ]
    .filter(Boolean)
    .join('\n');
  const pill = (
    <>
      <span className="font-semibold">IV {IV_COST_TEXT[score.label]}</span>
      <span className="tabular-nums">{score.score}</span>
      <span className="text-[9px] tracking-tighter opacity-70" aria-label={`${score.confidence} confidence`}>
        {DOTS[score.confidence]}
      </span>
    </>
  );
  const tag = score.event ? (
    <span className={`px-1.5 py-px rounded border border-amber-400/50 text-amber-200 cursor-help whitespace-nowrap ${SIZE_CLASS[size]}`} title={score.event.note}>
      ⚑ {eventTag(score)}
    </span>
  ) : null;
  const pillClass = `inline-flex items-center gap-1 px-1.5 py-px rounded border whitespace-nowrap ${SIZE_CLASS[size]} ${IV_COST_CLASS[score.label]}`;
  if (!expandable) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className={`${pillClass} cursor-help`} title={title}>
          {pill}
        </span>
        {tag}
      </span>
    );
  }
  return (
    <div className="inline-block align-top max-w-full">
      <span className="inline-flex items-center gap-1.5">
        <button
          type="button"
          className={`${pillClass} hover:brightness-125`}
          title={title}
          aria-expanded={open}
          onClick={(e) => {
            e.stopPropagation();
            setOpen((v) => !v);
          }}
        >
          {pill}
          <span aria-hidden="true" className="opacity-70">{open ? '▴' : '▾'}</span>
        </button>
        {tag}
      </span>
      {open && <IvScorePanel score={score} className="mt-1.5 text-left" />}
    </div>
  );
}

function Fact({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div title={hint} className={hint ? 'cursor-help' : undefined}>
      <dt className="inline text-text-secondary">{label} </dt>
      <dd className="inline text-text-primary tabular-nums">{children}</dd>
    </div>
  );
}

/** The breakdown: each part's sub-score and share, the inputs, notes and the event. `full` adds how it is built. */
export function IvScorePanel({ score, full = false, className = '' }: { score: IvScore; full?: boolean; className?: string }) {
  const h = score.history;
  return (
    <div className={`rounded border px-2.5 py-2 space-y-2 text-[11px] leading-snug max-w-xl ${IV_COST_CLASS[score.label]} ${className}`}>
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <span className="font-bold uppercase tracking-wider">
          IV cost {score.score}/100 · {IV_COST_TEXT[score.label]}
          {score.floored ? ' (raised for the event)' : ''}
        </span>
        <span className="text-text-secondary" title={CONFIDENCE_TEXT[score.confidence]}>
          {DOTS[score.confidence]} {score.confidence} confidence
        </span>
      </div>
      <p className="text-text-primary">{ivScoreSummary(score)}</p>

      <div className="space-y-1">
        {score.parts.map((p) => {
          const lbl = costLabelFor(p.sub_score);
          return (
            <div key={p.key}>
              <div className="flex justify-between gap-2 text-text-primary">
                <span>{p.label}</span>
                <span className="tabular-nums text-text-secondary">
                  {p.sub_score}/100 · {Math.round(p.share * 100)}% of the score
                </span>
              </div>
              <div className="h-1.5 rounded bg-black/30 overflow-hidden" aria-hidden="true">
                <div className={`h-full ${BAR_CLASS[lbl]}`} style={{ width: `${Math.max(2, p.sub_score)}%` }} />
              </div>
            </div>
          );
        })}
      </div>

      {score.event && <p className="text-amber-200">⚑ {score.event.note}</p>}
      {score.notes.length > 0 && (
        <ul className="list-disc pl-4 space-y-0.5 text-text-secondary">
          {score.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}

      <dl className="flex flex-wrap gap-x-5 gap-y-1">
        <Fact label="30-day IV" hint="Constant-maturity 30-day at-the-money IV (call/put average, live two-sided quotes only)">
          {pct(score.iv30_pct, 1)}
        </Fact>
        {score.iv_front_pct !== null && (
          <Fact label="Front expiry" hint="IV of the first expiry at least 7 days out; 1.2× the 30-day IV or more means an event is priced into it">
            {pct(score.iv_front_pct, 1)}
            {score.term_ratio ? ` (${score.term_ratio.toFixed(2)}×)` : ''}
          </Fact>
        )}
        <Fact label="Realised vol" hint="How much the stock actually moved: 20 and 60 trading days, annualised">
          {pct(score.hv20_pct, 1)} / {pct(score.hv60_pct, 1)}
        </Fact>
        {h && (
          <Fact
            label="History"
            hint="Percentile: share of days at or below today's IV. Rank: position between the window's low and high, which one spike can distort."
          >
            {h.days} days · percentile {h.percentile.toFixed(0)} · rank {h.rank.toFixed(0)} · range {pct(h.low_pct)}–{pct(h.high_pct)}
          </Fact>
        )}
      </dl>

      {full && (
        <div className="space-y-1 text-text-secondary border-t border-current/20 pt-1.5">
          <p>
            <span className="font-semibold text-text-primary">How it is built.</span> Two parts, each 50 when normal and
            higher when options are dearer: where the 30-day IV sits in its own clean daily history (the percentile,
            counted in full only once IV is 20% or more away from its median), and the IV against the stock&apos;s
            realised volatility (1.2× scores 35, 1.5× scores 60, 2× scores 80). With 120+ days of history they weigh
            50/50; with less, the history part weighs less. An earnings date or other event priced into the option
            raises the label to at least Pricey.
          </p>
          <p>
            Bands: {IV_COST_BANDS.map((b) => `${IV_COST_TEXT[b.label]} ${b.range}`).join(' · ')}.
          </p>
          {h && (
            <p>
              <span className="font-semibold text-text-primary">Rank vs percentile.</span> Rank {h.rank.toFixed(0)}{' '}
              places today between the window&apos;s low ({pct(h.low_pct)}) and high ({pct(h.high_pct)}); percentile{' '}
              {h.percentile.toFixed(0)} counts the days at or below today. When they differ by 25+ points, a single spike
              stretched the range and the percentile is the truer read.
            </p>
          )}
        </div>
      )}
      {score.as_of && <p className="text-text-secondary">As of {score.as_of.replace('T', ' ').replace('Z', ' UTC')}</p>}
    </div>
  );
}
