import { useCallback, useState, type MouseEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { RadarMember, RadarOptionMetrics } from '../../types';
import { fmtEt, fmtDuration, toMs } from './clock';
import {
  LATE_HINT,
  STATE_HINT,
  STATE_TEXT,
  backText,
  fmtMove,
  fmtRadarPrice,
  fmtSigma,
  fmtTimes,
  memberKey,
  minutesOnRadar,
  signClass,
  tickerHref,
} from './model';
import IntensityMeter from './IntensityMeter';
import RadarSparkline from './RadarSparkline';
import { DirArrow } from './DirArrow';
import ExitWatch, { ExitWatchPill } from './ExitWatch';
import { DEFAULT_EXIT_RULES, type ExitRules } from './exitRules';
import { OptionChip, OptionDetails } from './OptionInfo';

// Literal class maps (Tailwind purge).
const STATE_CHIP = {
  racing: { up: 'bg-green-900/40 text-green-300', down: 'bg-red-900/40 text-red-300' },
  cooling: { up: 'bg-amber-900/40 text-amber-300', down: 'bg-amber-900/40 text-amber-300' },
  halted: { up: 'bg-gray-800 text-text-secondary', down: 'bg-gray-800 text-text-secondary' },
} as const;

const PILL = 'text-[9px] font-bold uppercase tracking-wider px-1.5 py-px rounded whitespace-nowrap';
const BADGE = `${PILL} bg-blue-900/40 text-blue-300 border border-blue-500/30 cursor-help`;

const stop = (e: MouseEvent) => e.stopPropagation();

interface RowProps {
  m: RadarMember;
  o: RadarOptionMetrics | undefined;
  rules: ExitRules;
  tickId: string;
  open: boolean;
  onToggle: (key: string) => void;
  /** Distinguishes the card and table copies of the same member (both stay in the DOM). */
  idSuffix: 'card' | 'row';
}

const detailsId = (m: RadarMember, suffix: string) => `radar-more-${m.ticker.replace(/[^A-Za-z0-9]/g, '_')}-${suffix}`;

function MemberHead({ m, rules, open, onToggle, idSuffix }: Omit<RowProps, 'tickId' | 'o'>) {
  const key = memberKey(m);
  return (
    <div className="flex items-center gap-x-2 gap-y-1 flex-wrap">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onToggle(key);
        }}
        aria-expanded={open}
        aria-controls={detailsId(m, idSuffix)}
        aria-label={`${open ? 'Hide' : 'Show'} details for ${m.ticker}`}
        className="w-4 text-[10px] text-text-secondary hover:text-text-primary transition-colors"
      >
        <span className={`inline-block transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden="true">
          ▶
        </span>
      </button>
      <DirArrow dir={m.direction} />
      <Link
        to={tickerHref(m.ticker)}
        onClick={stop}
        title="Open in the ticker detail panel"
        className="text-base font-extrabold tracking-wide text-text-primary hover:text-accent-300 transition-colors"
      >
        {m.ticker}
      </Link>
      <span className={`${PILL} ${(STATE_CHIP[m.state] ?? STATE_CHIP.racing)[m.direction]}`} title={STATE_HINT[m.state]}>
        {STATE_TEXT[m.state] ?? 'Racing'}
      </span>
      <ExitWatchPill m={m} rules={rules} />
      {m.late && (
        <span className={BADGE} title={LATE_HINT}>
          Late
        </span>
      )}
      {m.episode > 1 && (
        <span className={BADGE} title={backText(m.episode)}>
          Back
        </span>
      )}
    </div>
  );
}

function Kv({ k, children, className = '' }: { k: string; children: ReactNode; className?: string }) {
  return (
    <span className="whitespace-nowrap">
      <span className="text-text-secondary mr-1">{k}</span>
      <span className={`font-semibold tabular-nums ${className}`}>{children}</span>
    </span>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="inline text-text-secondary">{label} </dt>
      <dd className="inline text-text-primary tabular-nums">{children}</dd>
    </div>
  );
}

function MemberDetails({ m, o, rules, id }: { m: RadarMember; o: RadarOptionMetrics | undefined; rules: ExitRules; id: string }) {
  const lastBar = toMs(m.last_bar_at);
  return (
    <div
      id={id}
      onClick={stop}
      className="mt-2 cursor-auto rounded border border-border bg-page/60 px-3 py-2.5 text-xs text-text-secondary leading-relaxed space-y-1.5"
    >
      <dl className="flex flex-wrap gap-x-5 gap-y-1">
        <Fact label="Entered">
          {fmtEt(toMs(m.entered_at))}
          {m.entry_price !== null && m.entry_price !== undefined ? ` at ${fmtRadarPrice(m.entry_price)}` : ''}
        </Fact>
        <Fact label="Last">
          {fmtRadarPrice(m.last_price)}
          {lastBar !== null ? ` (${fmtEt(lastBar)})` : ''}
        </Fact>
        <Fact label="Best since entry">{fmtMove(m.peak_since_entry_pct)}</Fact>
        <Fact label="Volume today">{m.rvol_day !== null && m.rvol_day !== undefined ? `${fmtTimes(m.rvol_day)} normal` : '—'}</Fact>
        <Fact label="Pace vs market">
          15 min {fmtSigma(m.z15)} · 30 min {fmtSigma(m.z30)} · today {fmtSigma(m.zday)}
        </Fact>
        <Fact label="Sector">{m.sector || '—'}</Fact>
      </dl>
      <ExitWatch m={m} rules={rules} />
      <OptionDetails o={o} />
      {m.state === 'halted' && (
        <p>
          No new trades since {fmtEt(lastBar)}: possibly a trading halt. It stays on the radar until trading resumes
          or the pause runs long.
        </p>
      )}
      {m.state === 'cooling' && <p>{STATE_HINT.cooling}</p>}
      {m.late && <p>Added during a catch-up scan, so it appeared here more than 10 minutes after the move was confirmed.</p>}
      {m.episode > 1 && <p>{backText(m.episode)}</p>}
      <p>
        <Link to={tickerHref(m.ticker)} className="text-accent-300 hover:text-accent-200 transition-colors">
          Open {m.ticker} in the ticker detail panel →
        </Link>
      </p>
    </div>
  );
}

function MemberCard({ m, o, rules, tickId, open, onToggle, dim }: Omit<RowProps, 'idSuffix'> & { dim?: boolean }) {
  const mins = minutesOnRadar(m, tickId);
  return (
    <li className={`py-3 cursor-pointer ${dim ? 'opacity-40' : ''}`} onClick={() => onToggle(memberKey(m))}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <MemberHead m={m} rules={rules} open={open} onToggle={onToggle} idSuffix="card" />
          <div className="text-xs text-text-secondary truncate mt-0.5 pl-6">{m.name || m.sector}</div>
        </div>
        <RadarSparkline m={m} />
      </div>
      <div className="flex items-end justify-between gap-3 mt-1.5">
        <span className="text-xs text-text-secondary" title={`On the radar since ${fmtEt(toMs(m.entered_at))}`}>
          <span className="tabular-nums text-text-primary">{mins === null ? '—' : fmtDuration(mins)}</span> on radar
        </span>
        <span className="text-right leading-tight">
          <span className={`text-lg font-bold tabular-nums ${signClass(m.move_since_entry_pct)}`}>
            {fmtMove(m.move_since_entry_pct)}
          </span>
          <span className="block text-[11px] text-text-secondary">since entry</span>
        </span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs mt-2">
        <Kv k="5m" className={signClass(m.chg_5m_pct)}>{fmtMove(m.chg_5m_pct)}</Kv>
        <Kv k="15m" className={signClass(m.chg_15m_pct)}>{fmtMove(m.chg_15m_pct)}</Kv>
        <Kv k="30m" className={signClass(m.chg_30m_pct)}>{fmtMove(m.chg_30m_pct)}</Kv>
        <Kv k="Today" className={signClass(m.chg_day_pct)}>{fmtMove(m.chg_day_pct)}</Kv>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs mt-1">
        <Kv k="RVol">{fmtTimes(m.rvol)}</Kv>
        <Kv k="vs VWAP">{fmtMove(m.vwap_dist_pct)}</Kv>
        <span className="whitespace-nowrap">
          <span className="text-text-secondary mr-1.5">Intensity</span>
          <IntensityMeter value={m.intensity} />
        </span>
      </div>
      {m.reasons?.length > 0 && <p className="text-xs text-text-secondary mt-2 leading-snug">{m.reasons.join(' · ')}</p>}
      <div className="mt-1.5">
        <OptionChip o={o} />
      </div>
      {open && <MemberDetails m={m} o={o} rules={rules} id={detailsId(m, 'card')} />}
    </li>
  );
}

const NUM_TD = 'px-1.5 py-2 text-right tabular-nums whitespace-nowrap';

function MemberRows({ m, o, rules, tickId, open, onToggle, dim }: Omit<RowProps, 'idSuffix'> & { dim?: boolean }) {
  const mins = minutesOnRadar(m, tickId);
  const hasReasons = true; // the second row always carries the option chip
  return (
    <tbody
      className={`group border-t border-border/60 first-of-type:border-t-0 cursor-pointer ${dim ? 'opacity-40' : ''}`}
      onClick={() => onToggle(memberKey(m))}
    >
      <tr className="group-hover:bg-border/20 transition-colors">
        <td className={`pl-1 pr-2 pt-2 ${hasReasons ? '' : 'pb-2'} align-middle`}>
          <MemberHead m={m} rules={rules} open={open} onToggle={onToggle} idSuffix="row" />
          <div className="text-xs text-text-secondary truncate max-w-[16rem] pl-6">{m.name || m.sector}</div>
        </td>
        <td className={`${NUM_TD} text-text-secondary`} title={`On the radar since ${fmtEt(toMs(m.entered_at))}`}>
          {mins === null ? '—' : fmtDuration(mins)}
        </td>
        <td className={`${NUM_TD} font-bold ${signClass(m.move_since_entry_pct)}`}>{fmtMove(m.move_since_entry_pct)}</td>
        <td className={`${NUM_TD} ${signClass(m.chg_5m_pct)}`}>{fmtMove(m.chg_5m_pct)}</td>
        <td className={`${NUM_TD} ${signClass(m.chg_15m_pct)}`}>{fmtMove(m.chg_15m_pct)}</td>
        <td className={`${NUM_TD} ${signClass(m.chg_30m_pct)}`}>{fmtMove(m.chg_30m_pct)}</td>
        <td className={`${NUM_TD} ${signClass(m.chg_day_pct)}`}>{fmtMove(m.chg_day_pct)}</td>
        <td className={NUM_TD}>{fmtTimes(m.rvol)}</td>
        <td className={NUM_TD}>{fmtMove(m.vwap_dist_pct)}</td>
        <td className={NUM_TD}>
          <IntensityMeter value={m.intensity} />
        </td>
        <td className="pl-2 pr-1 py-2">
          <div className="flex justify-end">
            <RadarSparkline m={m} />
          </div>
        </td>
      </tr>
      {hasReasons && (
        <tr className="group-hover:bg-border/20 transition-colors">
          <td colSpan={11} className="pl-7 pr-2 pb-2 text-xs text-text-secondary leading-snug">
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
              <span>{m.reasons?.join(' · ')}</span>
              <OptionChip o={o} />
            </div>
          </td>
        </tr>
      )}
      {open && (
        <tr>
          <td colSpan={11} className="px-1 pb-3">
            <MemberDetails m={m} o={o} rules={rules} id={detailsId(m, 'row')} />
          </td>
        </tr>
      )}
    </tbody>
  );
}

const COLUMNS = ['On radar', 'Since entry', '5 min', '15 min', '30 min', 'Today', 'RVol', 'vs VWAP', 'Intensity', 'Path'];

/**
 * Radar members: cards below `lg`, a table from `lg`. Clicking a row (or its
 * chevron) expands the details; the ticker links to the Dashboard detail
 * panel. Open rows are keyed by ticker + entry time, so they survive polls.
 */
export default function RadarMemberList({
  members,
  tickId,
  options,
  dimmed,
  rules = DEFAULT_EXIT_RULES,
}: {
  members: RadarMember[];
  tickId: string;
  options?: Record<string, RadarOptionMetrics>;
  /** Keys (memberKey) shown faded: filtered out but revealed on request. */
  dimmed?: Set<string>;
  /** Exit thresholds in effect (state.sensitivity). */
  rules?: ExitRules;
}) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const toggle = useCallback((key: string) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  return (
    <>
      <ul className="lg:hidden divide-y divide-border" aria-label="Stocks on the radar">
        {members.map((m) => (
          <MemberCard
            key={memberKey(m)}
            m={m}
            o={options?.[m.ticker]}
            rules={rules}
            tickId={tickId}
            open={open.has(memberKey(m))}
            onToggle={toggle}
            dim={dimmed?.has(memberKey(m))}
          />
        ))}
      </ul>
      <table className="hidden lg:table w-full text-sm">
        <caption className="sr-only">Stocks on the radar</caption>
        <thead>
          <tr className="border-b border-border text-[10px] uppercase tracking-wider text-text-secondary">
            <th scope="col" className="pl-7 pr-2 pb-2 text-left font-medium">
              Stock
            </th>
            {COLUMNS.map((c) => (
              <th key={c} scope="col" className="px-1.5 pb-2 text-right font-medium whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        {members.map((m) => (
          <MemberRows
            key={memberKey(m)}
            m={m}
            o={options?.[m.ticker]}
            rules={rules}
            tickId={tickId}
            open={open.has(memberKey(m))}
            onToggle={toggle}
            dim={dimmed?.has(memberKey(m))}
          />
        ))}
      </table>
    </>
  );
}
