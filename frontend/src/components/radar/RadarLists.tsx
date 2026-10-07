import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { RadarExit, RadarHeating, RadarOptionMetrics } from '../../types';
import { fmtDuration, fmtEt, toMs } from './clock';
import { exitText, fmtIntensity, fmtMove, fmtRadarPrice, signClass, tickerHref } from './model';
import { DirArrow } from './DirArrow';
import { DEFAULT_EXIT_RULES, exitRuleText, type ExitRules } from './exitRules';
import { OptionChip } from './OptionInfo';

/** Card section with an uppercase heading, a count chip and optional tools / note. */
export function RadarSection({
  id,
  title,
  count,
  tools,
  note,
  children,
}: {
  id: string;
  title: string;
  count: number | null;
  tools?: ReactNode;
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="bg-card border border-border rounded-lg p-3 sm:p-4">
      <div className="flex items-center justify-between gap-x-3 gap-y-2 flex-wrap mb-2">
        <h2 id={id} className="text-sm font-bold text-text-secondary uppercase tracking-wider flex items-center">
          {title}
          <span className="ml-2 px-2 py-px rounded-full bg-border text-text-primary text-xs tabular-nums normal-case tracking-normal">
            {count ?? '–'}
          </span>
        </h2>
        {tools}
      </div>
      {note && <p className="text-xs text-text-secondary leading-snug mb-2">{note}</p>}
      {children}
    </section>
  );
}

export function RadarEmpty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-text-secondary leading-snug py-3">{children}</p>;
}

function TickerLink({ ticker, muted = false }: { ticker: string; muted?: boolean }) {
  return (
    <Link
      to={tickerHref(ticker)}
      title="Open in the ticker detail panel"
      className={`font-bold hover:text-accent-300 transition-colors ${muted ? 'text-text-secondary' : 'text-text-primary'}`}
    >
      {ticker}
    </Link>
  );
}

/** Heating names: passed the entry checks, waiting for one confirming bar. Muted on purpose. */
export function RadarHeatingList({
  items,
  options,
}: {
  items: RadarHeating[];
  options?: Record<string, RadarOptionMetrics>;
}) {
  const sorted = [...items].sort((a, b) => (b.intensity ?? 0) - (a.intensity ?? 0));
  return (
    <ul className="divide-y divide-border">
      {sorted.map((h) => {
        const since = toMs(h.since);
        return (
          <li key={`${h.ticker}|${h.direction}`} className="flex justify-between gap-3 py-2.5 text-sm text-text-secondary">
            <div className="min-w-0">
              <div className="leading-snug">
                <DirArrow dir={h.direction} /> <TickerLink ticker={h.ticker} muted />
                {h.name && <span className="text-text-secondary/80"> {h.name}</span>}
                <span className={`tabular-nums ${signClass(h.chg_day_pct)}`}> · {fmtMove(h.chg_day_pct)} today</span>
                {h.intensity !== null && h.intensity !== undefined && (
                  <span className="tabular-nums"> · intensity {fmtIntensity(h.intensity)}</span>
                )}
              </div>
              {h.reasons?.length > 0 && <div className="text-xs leading-snug mt-0.5">{h.reasons.join(' · ')}</div>}
              {options && (
                <div className="mt-1">
                  <OptionChip o={options[h.ticker]} />
                </div>
              )}
            </div>
            <div className="text-xs text-right whitespace-nowrap tabular-nums">
              {fmtRadarPrice(h.price)}
              {since !== null && <span className="block">since {fmtEt(since)}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Recently dropped off, newest first, with the engine's plain-English reason. */
export function RadarExitList({ items, rules = DEFAULT_EXIT_RULES }: { items: RadarExit[]; rules?: ExitRules }) {
  const sorted = [...items].sort((a, b) => (toMs(b.exited_at) ?? 0) - (toMs(a.exited_at) ?? 0));
  return (
    <ul className="divide-y divide-border">
      {sorted.map((x) => {
        const entered = toMs(x.entered_at);
        const exited = toMs(x.exited_at);
        const mins =
          x.minutes_on_radar ?? (entered !== null && exited !== null ? (exited - entered) / 60_000 : null);
        return (
          <li key={`${x.ticker}|${x.exited_at}`} className="flex justify-between gap-3 py-2.5 text-sm">
            <div className="min-w-0">
              <div className="leading-snug">
                <DirArrow dir={x.direction} /> <TickerLink ticker={x.ticker} />
                {x.name && <span className="text-text-secondary"> {x.name}</span>}
                <span className={`font-semibold tabular-nums ${signClass(x.move_since_entry_pct)}`}>
                  {' '}
                  {fmtMove(x.move_since_entry_pct)}
                </span>
                <span className="text-text-secondary">
                  {mins !== null ? ` in ${fmtDuration(mins)} on the radar` : ' while on the radar'}
                </span>
              </div>
              <div className="text-xs text-text-secondary leading-snug mt-0.5">{exitText(x)}</div>
              {exitRuleText(x.exit_reason, rules) && (
                <div className="text-[11px] text-text-secondary/70 leading-snug mt-0.5">{exitRuleText(x.exit_reason, rules)}</div>
              )}
            </div>
            <div className="text-xs text-text-secondary text-right whitespace-nowrap">left {fmtEt(exited)}</div>
          </li>
        );
      })}
    </ul>
  );
}
