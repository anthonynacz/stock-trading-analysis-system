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
import { RADAR_SORTS, memberKey, sortMembers, type RadarFilter, type RadarSort } from '../components/radar/model';
import { failReason, readFilters, type OptionFilters } from '../components/radar/optionFilters';
import OptionFilterBar from '../components/radar/OptionFilterBar';
import ScanNowButton, { requestActive } from '../components/radar/ScanNowButton';
import SensitivityPanel from '../components/radar/SensitivityPanel';
import { exitRulesFrom } from '../components/radar/exitRules';
import RadarHistory from '../components/radar/RadarHistory';
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
  opt: OptionFilters;
}

/** Direction filter, sort and call filters, remembered per browser (`localStorage["vela.radar_view"]`). */
function readView(): RadarView {
  const view: RadarView = { f: 'all', sort: 'intensity', opt: readFilters(null) };
  try {
    const v = JSON.parse(localStorage.getItem(VIEW_KEY) ?? 'null') as Partial<RadarView> | null;
    view.opt = readFilters(v?.opt);
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
  const [showHidden, setShowHidden] = useState(false);
  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify(view));
    } catch {
      // Storage unavailable (private mode): the view just isn't remembered.
    }
  }, [view]);

  const st = isRadarSnapshot(radar.data) ? radar.data : null;
  const scanRequest = st ? st.scan_request : radar.data?.scan_request;
  const rules = exitRulesFrom(st?.sensitivity);
  const notPublished = radar.data !== null && st === null;
  const loading = radar.loading && !radar.data;
  const c = radarClock(now, st);
  const f = freshness(c, st);
  const live = isLive(c, st);

  // Leftovers of a scan that stopped early (or of yesterday) are not on the radar now.
  const all = live ? st.members ?? [] : [];
  const options = st?.options;
  const inDir = sortMembers(
    all.filter((m) => view.f === 'all' || m.direction === view.f),
    view.sort,
  );
  // Call filters: hidden names are listed by reason and can be shown faded.
  const hidden = new Map<string, { ticker: string; why: string }>();
  for (const m of inDir) {
    const why = failReason(view.opt, options?.[m.ticker], m.last_price);
    if (why) hidden.set(memberKey(m), { ticker: m.ticker, why });
  }
  const members = showHidden ? inDir : inDir.filter((m) => !hidden.has(memberKey(m)));
  const dimmed = showHidden ? new Set(hidden.keys()) : undefined;
  const heatingAll = live ? st.heating ?? [] : [];
  const heating = heatingAll.filter(
    (h) => (view.f === 'all' || h.direction === view.f) && !failReason(view.opt, options?.[h.ticker], h.price),
  );
  const heatingHidden = heatingAll.length - heating.length;
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
        <ScanNowButton
          request={scanRequest}
          canScan={c.scanning}
          closedHint={`The radar scans only while the market is open${resumes ? `; it starts again ${resumes}` : ''}.`}
          refetch={radar.refetch}
        />
        <SensitivityPanel applied={st?.sensitivity} busy={requestActive(scanRequest, now)} onApplied={radar.refetch} />
        <RadarScanBanner c={c} st={st} f={f} error={radar.error} notPublished={notPublished} />
        {live && <RadarContextBanners st={st} />}

        <RadarSection
          id="radar-members"
          title="On the radar"
          count={st ? all.length : null}
          note={summary.length ? summary.join(' · ') : null}
          tools={
            <div className="flex items-center gap-2 flex-wrap w-full sm:w-auto">
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
          <div className="mb-2">
            <OptionFilterBar
              value={view.opt}
              onChange={(opt) => setView((v) => ({ ...v, opt }))}
              onCallPreset={() => setView((v) => ({ ...v, f: 'up' }))}
            />
          </div>
          {hidden.size > 0 && (
            <p className="text-xs text-text-secondary leading-snug mb-2">
              {hidden.size} hidden by the call filters:{' '}
              {[...hidden.values()].map((h) => `${h.ticker} (${h.why})`).join(', ')}.{' '}
              <button type="button" onClick={() => setShowHidden((x) => !x)} className="text-accent-300 hover:text-accent-200">
                {showHidden ? 'Hide them' : 'Show them faded'}
              </button>
            </p>
          )}
          {loading ? (
            <LoadingRow />
          ) : !st ? (
            <RadarEmpty>{nothingYet ?? membersEmpty}</RadarEmpty>
          ) : members.length === 0 ? (
            <RadarEmpty>{hidden.size ? 'Every stock on the radar is hidden by the call filters.' : membersEmpty}</RadarEmpty>
          ) : (
            <RadarMemberList members={members} tickId={st.tick_id} options={options} dimmed={dimmed} rules={rules} />
          )}
        </RadarSection>

        <RadarSection
          id="radar-heating"
          title="Warming up"
          count={st ? heating.length : null}
          note={`Passed the entry checks on the last 5-minute bar and wait for one more bar to confirm. Not on the radar yet.${
            heatingHidden ? ` ${heatingHidden} hidden by the direction or call filters.` : ''
          }`}
        >
          {loading ? (
            <LoadingRow py="py-4" />
          ) : !st ? (
            <RadarEmpty>{nothingYet ?? 'No stocks warming up right now.'}</RadarEmpty>
          ) : heating.length === 0 ? (
            <RadarEmpty>No stocks warming up right now.</RadarEmpty>
          ) : (
            <RadarHeatingList items={heating} options={options} />
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
            <RadarExitList items={exits} rules={rules} />
          )}
        </RadarSection>

        <RadarHistory rules={rules} />

        <RadarHowItWorks />
        {st && <RadarScannerHealth st={st} />}
        <RadarDisclaimer />
      </div>
    </div>
  );
}
