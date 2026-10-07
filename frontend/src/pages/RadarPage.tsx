import { useEffect, useState } from 'react';
import type { RadarCounts } from '../types';
import { useRadar } from '../hooks/useEdgeFlow';
import { LoadingRow } from '../components/ui/feedback';
import { SegmentedControl, type SegmentOption } from '../components/ui/SegmentedControl';
import {
  TICK_MS,
  fmtDayLabel,
  fmtEt,
  fmtEtDay,
  freshness,
  isLive,
  isRadarSnapshot,
  listsDay,
  radarClock,
  toMs,
} from '../components/radar/clock';
import { RADAR_SORTS, sortMembers, type RadarFilter, type RadarSort } from '../components/radar/model';
import { useNow } from '../components/radar/useNow';
import RadarMemberList from '../components/radar/RadarMemberList';
import { RadarContextBanners, RadarScanBanner, RadarStatusBar } from '../components/radar/RadarStatus';
import { RadarEmpty, RadarExitList, RadarHeatingList, RadarSection } from '../components/radar/RadarLists';
import { RadarDisclaimer, RadarHowItWorks, RadarScannerHealth } from '../components/radar/RadarAbout';

const VIEW_KEY = 'vela.radar_view';

const FILTER_OPTIONS: SegmentOption<RadarFilter>[] = [
  { key: 'all', label: 'All' },
  { key: 'up', label: '▲ Up', title: 'Stocks racing up' },
  { key: 'down', label: '▼ Down', title: 'Stocks racing down' },
];

interface RadarView {
  f: RadarFilter;
  sort: RadarSort;
}

/** Direction filter and sort, remembered per browser (`localStorage["vela.radar_view"]`). */
function readView(): RadarView {
  const view: RadarView = { f: 'all', sort: 'intensity' };
  try {
    const v = JSON.parse(localStorage.getItem(VIEW_KEY) ?? 'null') as Partial<RadarView> | null;
    if (v && FILTER_OPTIONS.some((o) => o.key === v.f)) view.f = v.f as RadarFilter;
    if (v && RADAR_SORTS.some((o) => o.key === v.sort)) view.sort = v.sort as RadarSort;
  } catch {
    // Corrupt or blocked storage: keep the defaults.
  }
  return view;
}

/**
 * Momentum Radar: US stocks racing up or down right now, from the 5-minute
 * scanner (backend/radar). The snapshot only describes the session it was
 * written in, so everything "live" (members, warming up, banners) is gated on
 * the client's ET market clock; exits and day counts carry over until the next
 * session's first scan.
 */
export default function RadarPage() {
  const radar = useRadar();
  const now = useNow(15_000, radar.data);
  const [view, setView] = useState<RadarView>(readView);
  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify(view));
    } catch {
      // Storage unavailable (private mode): the view just isn't remembered.
    }
  }, [view]);

  const st = isRadarSnapshot(radar.data) ? radar.data : null;
  const notPublished = radar.data !== null && st === null;
  const loading = radar.loading && !radar.data;
  const c = radarClock(now, st);
  const f = freshness(c, st);
  const live = isLive(c, st);

  // Leftovers of a scan that stopped early (or of yesterday) are not on the radar now.
  const all = live ? st.members ?? [] : [];
  const members = sortMembers(
    all.filter((m) => view.f === 'all' || m.direction === view.f),
    view.sort,
  );
  const heating = live ? st.heating ?? [] : [];
  const exits = st?.recent_exits ?? [];

  const summary: string[] = [];
  const day = st ? listsDay(st) : null;
  if (st) {
    const scan = st.status !== 'closed';
    const k: Partial<RadarCounts> = st.counts ?? {};
    const tick = toMs(st.tick_id);
    if (scan && !live && tick !== null) summary.push(`As of the scan of ${fmtEtDay(tick)}`);
    if (scan && k.universe) summary.push(live ? `Checking ${k.universe} stocks` : `${k.universe} stocks checked`);
    if (day && k.entered_today != null && k.exited_today != null) {
      summary.push(
        `${k.entered_today} entered and ${k.exited_today} dropped off ${day === c.today ? 'today' : `on ${fmtDayLabel(day)}`}`,
      );
    }
  }

  const resumes = c.upcoming
    ? c.upcoming.date === c.today
      ? `today at ${fmtEt(c.upcoming.open + TICK_MS)}`
      : `on ${fmtEtDay(c.upcoming.open + TICK_MS)}`
    : null;
  const membersEmpty = all.length
    ? 'Nothing racing in this direction right now.'
    : c.scanning
      ? 'Nothing is racing right now. Stocks appear here within a few minutes of taking off.'
      : `The radar is empty outside market hours.${resumes ? ` It starts again ${resumes}.` : ''}`;
  const nothingYet = radar.error || notPublished ? 'Nothing to show yet.' : null;

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-6xl mx-auto px-3 py-4 sm:px-4 sm:py-6 space-y-3 sm:space-y-4">
        <header>
          <h1 className="text-xl sm:text-2xl font-bold mb-1">
            <span aria-hidden="true">📡</span> Momentum Radar
          </h1>
          <p className="text-sm text-text-secondary max-w-3xl">
            US stocks racing up or down right now. The radar checks about 550 stocks every 5 minutes while the market
            is open, and a stock stays on it until its move is over.
          </p>
        </header>

        <RadarStatusBar c={c} st={st} f={f} />
        <RadarScanBanner c={c} st={st} f={f} error={radar.error} notPublished={notPublished} />
        {live && <RadarContextBanners st={st} />}

        <RadarSection
          id="radar-members"
          title="On the radar"
          count={st ? all.length : null}
          note={summary.length ? summary.join(' · ') : null}
          tools={
            <div className="flex items-center gap-2 flex-wrap">
              <SegmentedControl
                variant="joined"
                options={FILTER_OPTIONS}
                value={view.f}
                onChange={(fKey) => setView((v) => ({ ...v, f: fKey }))}
              />
              <select
                aria-label="Sort by"
                value={view.sort}
                onChange={(e) => setView((v) => ({ ...v, sort: e.target.value as RadarSort }))}
                className="bg-card border border-border rounded px-2 py-1.5 text-xs text-text-primary focus:border-text-secondary"
              >
                {RADAR_SORTS.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
          }
        >
          {loading ? (
            <LoadingRow />
          ) : !st ? (
            <RadarEmpty>{nothingYet ?? membersEmpty}</RadarEmpty>
          ) : members.length === 0 ? (
            <RadarEmpty>{membersEmpty}</RadarEmpty>
          ) : (
            <RadarMemberList members={members} tickId={st.tick_id} />
          )}
        </RadarSection>

        <RadarSection
          id="radar-heating"
          title="Warming up"
          count={st ? heating.length : null}
          note="Passed the entry checks on the last 5-minute bar and wait for one more bar to confirm. Not on the radar yet."
        >
          {loading ? (
            <LoadingRow py="py-4" />
          ) : !st ? (
            <RadarEmpty>{nothingYet ?? 'No stocks warming up right now.'}</RadarEmpty>
          ) : heating.length === 0 ? (
            <RadarEmpty>No stocks warming up right now.</RadarEmpty>
          ) : (
            <RadarHeatingList items={heating} />
          )}
        </RadarSection>

        <RadarSection
          id="radar-exits"
          title="Recently dropped off"
          count={st ? exits.length : null}
          note={
            st && exits.length > 0 && day !== c.today
              ? day
                ? `From the session of ${fmtDayLabel(day)}.`
                : 'From the last session.'
              : null
          }
        >
          {loading ? (
            <LoadingRow py="py-4" />
          ) : !st ? (
            <RadarEmpty>{nothingYet ?? 'Nothing has dropped off this session.'}</RadarEmpty>
          ) : exits.length === 0 ? (
            <RadarEmpty>Nothing has dropped off this session.</RadarEmpty>
          ) : (
            <RadarExitList items={exits} />
          )}
        </RadarSection>

        <RadarHowItWorks />
        {st && <RadarScannerHealth st={st} />}
        <RadarDisclaimer />
      </div>
    </div>
  );
}
