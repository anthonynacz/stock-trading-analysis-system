import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { RadarTickRow, RadarTrip } from '../../types';
import { getApiErrorMessage } from '../../utils/api';
import { LoadingRow } from '../ui/feedback';
import { SegmentedControl, type SegmentOption } from '../ui/SegmentedControl';
import { fmtDuration, fmtEt, toMs } from './clock';
import { EXIT_TEXT, fmtMove, fmtRadarPrice, signClass, tickerHref, type RadarFilter } from './model';
import { EXIT_RULE_TEXT } from './exitRules';
import { getRadarHistory, getRadarTickerTicks } from './radarApi';
import { DirArrow } from './DirArrow';

type DayKey = '1' | '5' | '30' | '92';
const DAY_OPTIONS: SegmentOption<DayKey>[] = [
  { key: '1', label: 'Today', title: 'The last 24 hours' },
  { key: '5', label: '5 days' },
  { key: '30', label: '30 days' },
  { key: '92', label: '3 months', title: 'Everything kept (3-month retention)' },
];
const DIR_OPTIONS: SegmentOption<RadarFilter>[] = [
  { key: 'all', label: 'All' },
  { key: 'up', label: '▲ Up' },
  { key: 'down', label: '▼ Down' },
];
const PAGE = 100;

const dirSign = (t: RadarTrip) => (t.direction === 'down' ? -1 : 1);
/** The move counted in the radar's direction: +3% for a stock that fell 3% while racing down. */
const withMove = (t: RadarTrip) => (t.move_since_entry_pct === null ? null : dirSign(t) * t.move_since_entry_pct);

function fmtDayTime(iso: string | null): string {
  const t = toMs(iso);
  if (t === null) return '—';
  const day = new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'America/New_York' });
  return `${day} ${fmtEt(t)}`;
}

function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** The session's price path of one stay: grey before entry and after exit, coloured while on the radar. */
function TripPath({ trip }: { trip: RadarTrip }) {
  const [rows, setRows] = useState<RadarTickRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    getRadarTickerTicks(trip.ticker, trip.session)
      .then((d) => live && setRows(d.ticks))
      .catch((e) => live && setError(getApiErrorMessage(e, 'Could not load the path')));
    return () => {
      live = false;
    };
  }, [trip.ticker, trip.session]);

  if (error) return <p className="text-xs text-red-300">{error}</p>;
  if (!rows) return <LoadingRow py="py-2" />;
  const pts = rows.filter((r) => r.price !== null && toMs(r.tick) !== null).map((r) => ({ t: toMs(r.tick) as number, p: r.price as number }));
  const tIn = toMs(trip.entered_at);
  const tOut = toMs(trip.exited_at);
  if (trip.exit_price !== null && tOut !== null && !pts.some((x) => x.t === tOut)) pts.push({ t: tOut, p: trip.exit_price });
  pts.sort((a, b) => a.t - b.t);
  if (pts.length < 2) return <p className="text-xs text-text-secondary">Not enough recorded bars to draw the path.</p>;

  const W = 560;
  const H = 120;
  const P = 10;
  const t0 = pts[0].t;
  const t1 = pts[pts.length - 1].t;
  const lo = Math.min(...pts.map((x) => x.p), trip.entry_price ?? Infinity);
  let hi = Math.max(...pts.map((x) => x.p), trip.entry_price ?? -Infinity);
  if (hi === lo) hi = lo + 1e-6;
  const x = (t: number) => P + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * P);
  const y = (p: number) => P + ((hi - p) / (hi - lo)) * (H - 2 * P);
  const line = (sel: typeof pts) => sel.map((q) => `${x(q.t).toFixed(1)},${y(q.p).toFixed(1)}`).join(' ');
  const on = pts.filter((q) => tIn !== null && tOut !== null && q.t >= tIn && q.t <= tOut);
  const stroke = trip.direction === 'down' ? 'stroke-red-400' : 'stroke-green-400';

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-xl h-28" role="img" aria-label={`${trip.ticker} price path on ${trip.session}`}>
      {trip.entry_price !== null && (
        <line x1={P} x2={W - P} y1={y(trip.entry_price)} y2={y(trip.entry_price)} className="stroke-gray-600" strokeDasharray="3 4" />
      )}
      <polyline points={line(pts)} fill="none" className="stroke-gray-500" strokeWidth={1.5} />
      {on.length > 1 && <polyline points={line(on)} fill="none" className={stroke} strokeWidth={2.5} strokeLinejoin="round" />}
      {tIn !== null && trip.entry_price !== null && (
        <circle cx={x(tIn)} cy={y(trip.entry_price)} r={4} className="fill-card stroke-gray-200" strokeWidth={1.5}>
          <title>{`Entered ${fmtEt(tIn)} at ${fmtRadarPrice(trip.entry_price)}`}</title>
        </circle>
      )}
      {tOut !== null && trip.exit_price !== null && (
        <g>
          <line x1={x(tOut)} x2={x(tOut)} y1={P} y2={H - P} className="stroke-red-400/60" strokeDasharray="2 3" />
          <circle cx={x(tOut)} cy={y(trip.exit_price)} r={4} className="fill-red-400 stroke-card" strokeWidth={1.5}>
            <title>{`Dropped off ${fmtEt(tOut)} at ${fmtRadarPrice(trip.exit_price)}`}</title>
          </circle>
        </g>
      )}
      <text x={P} y={H - 1} className="fill-text-secondary text-[10px]">
        {fmtEt(t0)}
      </text>
      <text x={W - P} y={H - 1} textAnchor="end" className="fill-text-secondary text-[10px]">
        {fmtEt(t1)}
      </text>
    </svg>
  );
}

/**
 * History of stocks that dropped off the radar (GET /api/radar/history): one row
 * per stay, with entry and exit, time held, move and the rule that removed it.
 * Loaded only when the section is opened; reads the events table, never the scanner.
 */
export default function RadarHistory() {
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState<DayKey>('5');
  const [dir, setDir] = useState<RadarFilter>('all');
  const [reason, setReason] = useState('all');
  const [query, setQuery] = useState('');
  const [trips, setTrips] = useState<RadarTrip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getRadarHistory(Number(days))
      .then((d) => setTrips(d.trips))
      .catch((e) => setError(getApiErrorMessage(e, 'Could not load the history')))
      .finally(() => setLoading(false));
  }, [days]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const q = query.trim().toUpperCase();
  const list = useMemo(
    () =>
      (trips ?? []).filter(
        (t) =>
          (dir === 'all' || t.direction === dir) &&
          (reason === 'all' || t.exit_reason === reason) &&
          (!q || t.ticker.includes(q)),
      ),
    [trips, dir, reason, q],
  );
  const reasons = useMemo(() => [...new Set((trips ?? []).map((t) => t.exit_reason))].sort(), [trips]);

  const moves = list.map(withMove).filter((v): v is number => v !== null);
  const held = list.map((t) => t.held_min).filter((v): v is number => v !== null);
  const kept = moves.filter((v) => v > 0).length;
  const byReason = list.reduce<Record<string, number>>((acc, t) => ({ ...acc, [t.exit_reason]: (acc[t.exit_reason] ?? 0) + 1 }), {});

  return (
    <section aria-labelledby="radar-history" className="bg-card border border-border rounded-lg p-3 sm:p-4">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full flex items-center justify-between gap-3 text-left"
      >
        <h2 id="radar-history" className="text-sm font-bold text-text-secondary uppercase tracking-wider">
          History of dropped stocks
        </h2>
        <span className={`text-xs text-text-secondary transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden="true">
          ▶
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <SegmentedControl variant="joined" options={DAY_OPTIONS} value={days} onChange={(d) => { setDays(d); setShown(PAGE); }} />
            <SegmentedControl variant="joined" options={DIR_OPTIONS} value={dir} onChange={setDir} />
            <select
              aria-label="Exit reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="bg-card border border-border rounded px-2 py-1.5 text-xs text-text-primary"
            >
              <option value="all">All reasons</option>
              {reasons.map((r) => (
                <option key={r} value={r}>
                  {EXIT_TEXT[r] ?? r}
                </option>
              ))}
            </select>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ticker"
              aria-label="Filter by ticker"
              className="bg-card border border-border rounded px-2 py-1.5 text-xs text-text-primary w-24"
            />
            <button type="button" onClick={load} className="text-xs text-text-secondary hover:text-text-primary" title="Reload">
              ⟳
            </button>
          </div>

          {loading && !trips ? (
            <LoadingRow />
          ) : error ? (
            <p className="text-sm text-red-300">{error}</p>
          ) : list.length === 0 ? (
            <p className="text-sm text-text-secondary py-2">No stocks dropped off in this window.</p>
          ) : (
            <>
              <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-secondary">
                <span>
                  <span className="text-text-primary font-semibold tabular-nums">{list.length}</span> stays
                </span>
                <span title="Price change from entry to exit, counted in the radar's direction (a stock racing down that fell 3% counts +3%)">
                  Median move with the trend{' '}
                  <span className={`font-semibold tabular-nums ${signClass(median(moves))}`}>{fmtMove(median(moves))}</span>
                </span>
                <span title="Share of stays that ended further along in their direction than where they entered">
                  Ended ahead <span className="text-text-primary font-semibold tabular-nums">{moves.length ? Math.round((100 * kept) / moves.length) : 0}%</span>
                </span>
                <span>
                  Median time on radar{' '}
                  <span className="text-text-primary font-semibold tabular-nums">{median(held) === null ? '—' : fmtDuration(median(held) as number)}</span>
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(byReason)
                  .sort((a, b) => b[1] - a[1])
                  .map(([r, n]) => (
                    <button
                      key={r}
                      type="button"
                      onClick={() => setReason(reason === r ? 'all' : r)}
                      title={EXIT_RULE_TEXT[r]}
                      className={`px-1.5 py-0.5 rounded border text-[11px] ${reason === r ? 'border-accent-500/50 text-accent-200' : 'border-border text-text-secondary hover:text-text-primary'}`}
                    >
                      {EXIT_TEXT[r] ?? r} <span className="tabular-nums">{n}</span>
                    </button>
                  ))}
              </div>

              <div className="overflow-x-auto -mx-1">
                <table className="w-full text-sm min-w-[40rem]">
                  <caption className="sr-only">Stocks that dropped off the radar</caption>
                  <thead>
                    <tr className="border-b border-border text-[10px] uppercase tracking-wider text-text-secondary">
                      <th scope="col" className="px-1 pb-2 text-left font-medium">Dropped off</th>
                      <th scope="col" className="px-1 pb-2 text-left font-medium">Stock</th>
                      <th scope="col" className="px-1 pb-2 text-right font-medium">On radar</th>
                      <th scope="col" className="px-1 pb-2 text-right font-medium">Entry → exit</th>
                      <th scope="col" className="px-1 pb-2 text-right font-medium">Move</th>
                      <th scope="col" className="px-1 pb-2 text-left font-medium pl-3">Why</th>
                    </tr>
                  </thead>
                  {list.slice(0, shown).map((t) => {
                    const key = `${t.ticker}|${t.exited_at}`;
                    const isOpen = expanded === key;
                    return (
                      <tbody key={key} className="border-t border-border/60 first-of-type:border-t-0">
                        <tr className="cursor-pointer hover:bg-border/20" onClick={() => setExpanded(isOpen ? null : key)}>
                          <td className="px-1 py-2 whitespace-nowrap text-text-secondary tabular-nums">{fmtDayTime(t.exited_at)}</td>
                          <td className="px-1 py-2 whitespace-nowrap">
                            <DirArrow dir={t.direction} />{' '}
                            <Link to={tickerHref(t.ticker)} onClick={(e) => e.stopPropagation()} className="font-bold hover:text-accent-300">
                              {t.ticker}
                            </Link>
                            {t.episode && t.episode > 1 ? <span className="text-[10px] text-text-secondary"> #{t.episode}</span> : null}
                          </td>
                          <td className="px-1 py-2 text-right tabular-nums text-text-secondary">{t.held_min === null ? '—' : fmtDuration(t.held_min)}</td>
                          <td className="px-1 py-2 text-right tabular-nums whitespace-nowrap">
                            {fmtRadarPrice(t.entry_price)} → {fmtRadarPrice(t.exit_price)}
                          </td>
                          <td className={`px-1 py-2 text-right tabular-nums font-semibold ${signClass(t.move_since_entry_pct)}`}>
                            {fmtMove(t.move_since_entry_pct)}
                          </td>
                          <td className="pl-3 pr-1 py-2 text-xs text-text-secondary">{t.exit_detail || EXIT_TEXT[t.exit_reason] || t.exit_reason}</td>
                        </tr>
                        {isOpen && (
                          <tr>
                            <td colSpan={6} className="px-1 pb-3">
                              <div className="rounded border border-border bg-page/60 px-3 py-2.5 space-y-2 text-xs text-text-secondary">
                                <TripPath trip={t} />
                                <p>
                                  <span className="text-text-primary">Entered {fmtDayTime(t.entered_at)}:</span> {t.entry_detail || '—'}
                                  {t.late ? ' (added during a catch-up scan)' : ''}
                                </p>
                                <p>
                                  <span className="text-text-primary">Dropped off {fmtDayTime(t.exited_at)}:</span>{' '}
                                  {t.exit_detail || EXIT_TEXT[t.exit_reason] || t.exit_reason}
                                </p>
                                {EXIT_RULE_TEXT[t.exit_reason] && <p>{EXIT_RULE_TEXT[t.exit_reason]}</p>}
                              </div>
                            </td>
                          </tr>
                        )}
                      </tbody>
                    );
                  })}
                </table>
              </div>
              {list.length > shown && (
                <button type="button" onClick={() => setShown((n) => n + PAGE)} className="text-xs text-accent-300 hover:text-accent-200">
                  Show {Math.min(PAGE, list.length - shown)} more of {list.length - shown}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
