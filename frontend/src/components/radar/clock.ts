// Market clock for the Momentum Radar (US Eastern time). Pure functions, no React.
//
// A radar snapshot only describes the session it was written in, so the page
// needs its own NYSE calendar to tell weekends, holidays and the next session
// apart, and to judge freshness against the scan schedule. Ported from the
// GitHub radar.html; the tables mirror backend/radar/calendar_nyse.py
// (HOLIDAYS + EXTRA_CLOSURES, EARLY_CLOSES) — update both together. Past the
// table every weekday counts as a session.

import type { RadarResponse, RadarSnapshot } from '../../types';

const HOLIDAYS = new Set([
  '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19', '2026-07-03', '2026-09-07',
  '2026-11-26', '2026-12-25',
  '2027-01-01', '2027-01-18', '2027-02-15', '2027-03-26', '2027-05-31', '2027-06-18', '2027-07-05', '2027-09-06',
  '2027-11-25', '2027-12-24',
  '2028-01-17', '2028-02-21', '2028-04-14', '2028-05-29', '2028-06-19', '2028-07-04', '2028-09-04', '2028-11-23',
  '2028-12-25',
]);
const EARLY_CLOSES = new Set(['2026-11-27', '2026-12-24', '2027-11-26', '2028-07-03', '2028-11-24']);

/** RUNTIME.stale_warning_min: no scan for this long while scanning reads as stale. */
export const STALE_MIN = 12;
/** Amber after one missed scan. */
export const LATE_MIN = 7;
/** Scan cadence: every 5-minute bar. */
export const TICK_MS = 300_000;
/** Each scan starts 50 s after the bar closes (RUNTIME.tick_offset_s). */
export const TICK_OFFSET_MS = 50_000;
/** The final scan lands just after the close; keep treating the session as scanning this long. */
const FINAL_GRACE_MS = 5 * 60_000;

/** `/api/radar` poll interval while the radar is scanning. */
export const RADAR_POLL_LIVE_MS = 30_000;
/** `/api/radar` poll interval while the session is closed. */
export const RADAR_POLL_IDLE_MS = 5 * 60_000;

const ET = 'America/New_York';
const fmtParts = new Intl.DateTimeFormat('en-US', {
  timeZone: ET, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const fmtHM = new Intl.DateTimeFormat('en-US', { timeZone: ET, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const fmtDay = new Intl.DateTimeFormat('en-US', { timeZone: ET, weekday: 'short', month: 'short', day: 'numeric' });

/** True once the radar has written a snapshot (the no-data reply has no schema / tick_id). */
export function isRadarSnapshot(d: RadarResponse | null | undefined): d is RadarSnapshot {
  return !!d && d.schema === 1 && typeof d.tick_id === 'string';
}

/** Epoch ms of an ISO timestamp, or null when missing / unparseable. */
export function toMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

export function etParts(t: number): { date: string; h: number; m: number } {
  const p: Record<string, string> = {};
  for (const x of fmtParts.formatToParts(new Date(t))) p[x.type] = x.value;
  return { date: `${p.year}-${p.month}-${p.day}`, h: Number(p.hour) % 24, m: Number(p.minute) };
}

/** UTC ms of an ET wall-clock time on `date` (EDT or EST). */
export function etAt(date: string, hh: number, mm: number): number {
  const y = Number(date.slice(0, 4));
  const mo = Number(date.slice(5, 7)) - 1;
  const d = Number(date.slice(8, 10));
  for (let off = 4; off <= 5; off++) {
    const t = Date.UTC(y, mo, d, hh + off, mm);
    const p = etParts(t);
    if (p.h === hh && p.m === mm) return t;
  }
  return Date.UTC(y, mo, d, hh + 5, mm);
}

const weekday = (date: string) => new Date(`${date}T12:00:00Z`).getUTCDay();

function addDays(date: string, n: number): string {
  const t = new Date(`${date}T12:00:00Z`);
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
}

export interface MarketSession {
  date: string;
  open: number;
  close: number;
  half: boolean;
}

export function sessionFor(date: string): MarketSession | null {
  const wd = weekday(date);
  if (wd === 0 || wd === 6 || HOLIDAYS.has(date)) return null;
  const half = EARLY_CLOSES.has(date);
  return { date, open: etAt(date, 9, 30), close: etAt(date, half ? 13 : 16, 0), half };
}

export function nextSession(date: string): MarketSession | null {
  for (let i = 1; i <= 14; i++) {
    const s = sessionFor(addDays(date, i));
    if (s) return s;
  }
  return null;
}

export type SessionKind = 'pre' | 'open' | 'closed' | 'weekend' | 'holiday';

export interface RadarClock {
  t: number;
  /** Today's ET date (YYYY-MM-DD). */
  today: string;
  session: MarketSession | null;
  kind: SessionKind;
  /** The scanner should be publishing: from the first scan (open + 5 min) to just after the close. */
  scanning: boolean;
  /** Bar close of the session's first scan (09:35 ET on a regular day). */
  firstScan: number | null;
  /** The session whose first scan comes next (today before 09:35, else the next trading day). */
  upcoming: MarketSession | null;
}

export function radarClock(t: number, st: RadarSnapshot | null): RadarClock {
  const today = etParts(t).date;
  let s = sessionFor(today);
  // The scanner's calendar wins for the day it describes (unscheduled closures, half days).
  const open = toMs(st?.session.open);
  const close = toMs(st?.session.close);
  if (st && st.session.date === today && open !== null && close !== null) {
    s = { date: today, open, close, half: st.session.half_day === true };
  }
  if (!s) {
    return {
      t, today, session: null, kind: weekday(today) % 6 === 0 ? 'weekend' : 'holiday',
      scanning: false, firstScan: null, upcoming: nextSession(today),
    };
  }
  const firstScan = s.open + TICK_MS;
  return {
    t,
    today,
    session: s,
    kind: t < s.open ? 'pre' : t < s.close ? 'open' : 'closed',
    scanning: t >= firstScan && t < s.close + TICK_OFFSET_MS + FINAL_GRACE_MS,
    firstScan,
    upcoming: t < firstScan ? s : nextSession(today),
  };
}

export type FreshnessLevel = 'ok' | 'late' | 'stale';

export interface Freshness {
  /** ms since the last scan of this session (or since the first one was due). */
  age: number;
  /** A scan of today's session has arrived. */
  today: boolean;
  level: FreshnessLevel;
}

/**
 * Freshness while scanning; null otherwise. No upper bound on tick_id: a
 * device clock running a minute or two slow must not read a fresh scan as
 * missing. The server's `stale` flag also counts, so a device clock running
 * fast or slow cannot hide a stopped scanner.
 */
export function freshness(c: RadarClock, st: RadarSnapshot | null): Freshness | null {
  if (!c.scanning || c.firstScan === null) return null;
  const tick = toMs(st?.tick_id);
  const today = tick !== null && tick >= c.firstScan;
  const last = today ? toMs(st?.generated_at) ?? tick! : c.firstScan + TICK_OFFSET_MS;
  const age = c.t - last;
  const stale = age > STALE_MIN * 60_000 || (today && st?.stale === true);
  return { age, today, level: stale ? 'stale' : age > LATE_MIN * 60_000 || !today ? 'late' : 'ok' };
}

/**
 * A closed heartbeat names today's (or the next) session but still carries the
 * last session's exits and counts, so only a scan written during today's
 * session is today's data.
 */
export function isTodayScan(c: RadarClock, st: RadarSnapshot | null): st is RadarSnapshot {
  return !!st && st.status !== 'closed' && st.session.date === c.today;
}

/** Members, heating names and banners describe the live session only. */
export function isLive(c: RadarClock, st: RadarSnapshot | null): st is RadarSnapshot {
  return c.scanning && isTodayScan(c, st);
}

/** The ET day the exits and day counts belong to (a closed heartbeat: the day of its latest exit). */
export function listsDay(st: RadarSnapshot): string | null {
  if (st.status !== 'closed') return st.session.date;
  let latest = 0;
  for (const x of st.recent_exits ?? []) latest = Math.max(latest, toMs(x.exited_at) ?? 0);
  return latest ? etParts(latest).date : null;
}

/**
 * 30 s while the radar is scanning (or its first scan of the day is less than
 * one idle interval away, so it shows promptly); 5 min while closed.
 */
export function radarPollMs(data: RadarResponse | null, now: number): number {
  const c = radarClock(now, isRadarSnapshot(data) ? data : null);
  if (c.scanning) return RADAR_POLL_LIVE_MS;
  const first = c.upcoming ? c.upcoming.open + TICK_MS + TICK_OFFSET_MS : Infinity;
  return first - now <= RADAR_POLL_IDLE_MS ? RADAR_POLL_LIVE_MS : RADAR_POLL_IDLE_MS;
}

// ── ET time formatting ────────────────────────────────────────────────────

/** `11:20 ET`. */
export function fmtEt(t: number | null): string {
  return t === null ? '—' : `${fmtHM.format(new Date(t))} ET`;
}

/** `Mon, Sep 28, 11:20 ET`. */
export function fmtEtDay(t: number): string {
  return `${fmtDay.format(new Date(t))}, ${fmtEt(t)}`;
}

/** `Mon, Sep 28` for an ET date string. */
export function fmtDayLabel(date: string): string {
  return fmtDay.format(new Date(`${date}T12:00:00Z`));
}

/** `35 min`, `1 h 15 min`. */
export function fmtDuration(min: number): string {
  const m = Math.max(0, Math.round(min));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ''}`;
}

/** `just now`, `3 min ago`, `2 h ago`, `1 d ago`. */
export function fmtAgo(ms: number): string {
  const m = Math.floor(ms / 60_000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  if (m < 1440) return `${Math.floor(m / 60)} h ago`;
  return `${Math.floor(m / 1440)} d ago`;
}
