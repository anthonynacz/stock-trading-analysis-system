import type { ReactNode } from 'react';
import type { RadarSnapshot } from '../../types';
import { ErrorBox } from '../ui/feedback';
import { fmtPct } from '../../utils/format';
import {
  TICK_MS,
  etParts,
  fmtAgo,
  fmtDuration,
  fmtEt,
  fmtEtDay,
  isLive,
  toMs,
  type Freshness,
  type RadarClock,
  type SessionKind,
} from './clock';
import { fmtMove, heldBack, isScanProblem, scanProblem } from './model';
import { DirArrow } from './DirArrow';

// Literal class maps (Tailwind purge).
const SESSION: Record<SessionKind, { label: string; pill: string }> = {
  pre: { label: 'Before the open', pill: 'bg-amber-900/40 text-amber-300' },
  open: { label: 'Market open', pill: 'bg-green-900/40 text-green-300' },
  closed: { label: 'Market closed', pill: 'bg-gray-800 text-text-secondary' },
  weekend: { label: 'Weekend', pill: 'bg-gray-800 text-text-secondary' },
  holiday: { label: 'Market holiday', pill: 'bg-gray-800 text-text-secondary' },
};

const DOT = {
  ok: 'bg-green-400 shadow-[0_0_6px_rgba(74,222,128,0.7)]',
  late: 'bg-amber-400',
  stale: 'bg-red-400',
  none: 'bg-gray-500',
} as const;

const DOT_TITLE = {
  ok: 'Up to date',
  late: 'A scan is late',
  stale: 'No recent scan',
  none: 'Outside the scan window',
} as const;

const BANNER_TONE = {
  warn: 'bg-amber-900/20 border-amber-500/30 text-amber-200',
  info: 'bg-blue-900/20 border-blue-500/30 text-blue-200',
  market: 'bg-violet-900/20 border-violet-500/40 text-text-primary',
  sector: 'bg-border/30 border-border text-text-secondary',
} as const;

export function Banner({
  tone,
  children,
  title,
}: {
  tone: keyof typeof BANNER_TONE;
  children: ReactNode;
  title?: string;
}) {
  return (
    <div role="status" title={title} className={`rounded border px-3 py-2 text-sm leading-snug ${BANNER_TONE[tone]}`}>
      {children}
    </div>
  );
}

interface StatusProps {
  c: RadarClock;
  st: RadarSnapshot | null;
  f: Freshness | null;
}

/** Session state, last / next scan and a freshness dot. */
export function RadarStatusBar({ c, st, f }: StatusProps) {
  const session = SESSION[c.kind];
  let info: string | null = null;
  if (c.session && c.firstScan !== null && c.t < c.firstScan) {
    info = `Radar starts at ${fmtEt(c.firstScan)}${c.session.half ? ` · half day, closes ${fmtEt(c.session.close)}` : ''}`;
  } else if (c.scanning && c.session && c.t < c.session.close) {
    info = `Closes ${fmtEt(c.session.close)}${c.session.half ? ' (half day)' : ''}`;
  } else if (c.upcoming) {
    info = `Radar resumes ${fmtEtDay(c.upcoming.open + TICK_MS)}`;
  }

  const tick = toMs(st?.tick_id);
  const next = toMs(st?.next_tick_at);
  const level = f ? f.level : 'none';

  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs sm:text-sm text-text-secondary">
      <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full ${session.pill}`}>
        {session.label}
      </span>
      {info && <span>{info}</span>}
      {st && tick !== null && (
        <span>
          <span
            className={`inline-block w-2 h-2 rounded-full mr-1.5 align-[1px] ${DOT[level]}`}
            title={DOT_TITLE[level]}
            aria-hidden="true"
          />
          <span className="font-semibold text-text-primary">Updated {fmtAgo(c.t - (toMs(st.generated_at) ?? tick))}</span>
          {st.status === 'closed' ? ' · checked at ' : ' · scan of '}
          {etParts(tick).date === c.today ? fmtEt(tick) : fmtEtDay(tick)}
        </span>
      )}
      {st && c.scanning && next !== null && next > c.t && <span>Next scan ≈ {fmtEt(next)}</span>}
    </div>
  );
}

/**
 * At most one status banner, most important first: load errors, missing or
 * late scans, a problem scan (the scanner's own message, as is), then a failed
 * refresh. A clean scan shows none.
 */
export function RadarScanBanner({
  c,
  st,
  f,
  error,
  notPublished,
}: StatusProps & { error: string | null; notPublished: boolean }) {
  if (notPublished && !st) {
    return <Banner tone="info">The radar has not published any data yet. It fills in after its first scan of a market session.</Banner>;
  }
  if (error && !st) return <ErrorBox message={`Couldn't load the radar (${error}). Retrying…`} />;
  if (f && f.level === 'stale' && !f.today && c.firstScan !== null) {
    return (
      <ErrorBox
        message={`No scan yet today, although the radar should have started at ${fmtEt(c.firstScan)}. The scanner may be starting late.`}
      />
    );
  }
  if (f && !f.today && c.firstScan !== null) {
    return <Banner tone="info">Waiting for today's first scan (due at {fmtEt(c.firstScan)}).</Banner>;
  }
  if (f && f.level === 'stale' && st) {
    const mins = Math.max(Math.floor(f.age / 60_000), 0);
    return (
      <ErrorBox
        message={`No new scan for ${fmtDuration(mins)} while the market is open. The scanner may be restarting; what you see is from the scan of ${fmtEt(toMs(st.tick_id))}.`}
      />
    );
  }
  if (st && isLive(c, st) && isScanProblem(st.status)) return <Banner tone="warn">{scanProblem(st)}</Banner>;
  if (error) return <Banner tone="warn">Couldn't refresh ({error}). Showing the last loaded scan; retrying.</Banner>;
  return null;
}

/** Market-wide banner first, then one banner per sector cap. Live session only. */
export function RadarContextBanners({ st }: { st: RadarSnapshot }) {
  const mk = st.market;
  const sectors = (st.sector_banners ?? []).flatMap((s) => {
    const t = heldBack(s);
    return t && s.sector && (s.direction === 'up' || s.direction === 'down') ? [{ s, t }] : [];
  });
  if (mk?.mode !== 'market' && sectors.length === 0) return null;
  const dir = mk?.dir === 'up' ? 'rising' : mk?.dir === 'down' ? 'falling' : 'moving';
  return (
    <div className="space-y-2">
      {mk?.mode === 'market' && (
        <Banner
          tone="market"
          title="Breadth: how one-sided the market is over the last 30 minutes (net share of stocks moving the same way)."
        >
          <span className="font-semibold">🌐 Market-wide move: </span>
          the whole market is {dir} (SPY {fmtMove(mk.spy_chg_day_pct)} today
          {mk.breadth30 !== null && mk.breadth30 !== undefined
            ? `, breadth ${fmtPct(mk.breadth30, 0, { fraction: true })} over 30 min`
            : ''}
          ). The radar is stricter with stocks moving the same way, so the names below stand out from the market.
        </Banner>
      )}
      {sectors.map(({ s, t }) => (
        <Banner key={`${s.sector}|${s.direction}`} tone="sector">
          <DirArrow dir={s.direction} className="text-xs mr-1.5" />
          <span className="font-semibold text-text-primary">{s.sector}: </span>
          {t.head}
          {t.tickers && <span className="font-semibold text-text-primary"> {t.tickers}</span>}. {t.tail}
        </Banner>
      ))}
    </div>
  );
}
