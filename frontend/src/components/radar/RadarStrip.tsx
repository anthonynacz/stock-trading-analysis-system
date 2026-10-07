import { Link } from 'react-router-dom';
import { useRadar } from '../../hooks/useEdgeFlow';
import { fmtEt, freshness, isLive, isRadarSnapshot, radarClock, toMs } from './clock';
import { INTENSITY_HINT, sortMembers } from './model';
import { useNow } from './useNow';
import { DirArrow } from './DirArrow';
import IntensityMeter from './IntensityMeter';

const MAX_TICKERS = 6;

/**
 * Dashboard "On the radar now" strip: up to 6 live radar members (highest
 * intensity first) with direction and intensity, each linking to /radar.
 * Supplementary, so it renders nothing while loading, on error, outside the
 * live session or when the radar is empty. It owns its own polling (via
 * useRadar) so radar updates never re-render the rest of the Dashboard.
 */
export default function RadarStrip() {
  const radar = useRadar();
  const now = useNow(60_000, radar.data);
  const st = isRadarSnapshot(radar.data) ? radar.data : null;
  const c = radarClock(now, st);
  if (!isLive(c, st)) return null;
  const members = st.members ?? [];
  if (members.length === 0) return null;

  const top = sortMembers(members, 'intensity').slice(0, MAX_TICKERS);
  const stale = freshness(c, st)?.level === 'stale';
  const tick = toMs(st.tick_id);

  return (
    <section aria-labelledby="radar-strip-title" className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between gap-3 px-3 py-1.5 border-b border-border">
        <h2 id="radar-strip-title" className="text-xs font-bold uppercase tracking-wider text-text-secondary whitespace-nowrap">
          <span aria-hidden="true">📡</span> On the radar now
        </h2>
        <Link to="/radar" className="text-[11px] text-text-secondary hover:text-text-primary transition-colors whitespace-nowrap">
          {/* Scan time from sm up; always shown (amber) when the scanner has gone quiet. */}
          <span
            className={stale ? 'text-amber-300' : 'hidden sm:inline'}
            title={stale ? 'No recent scan: the radar may be restarting' : undefined}
          >
            scan of {fmtEt(tick)} ·{' '}
          </span>
          {members.length > top.length ? `${members.length} in all · ` : ''}Radar →
        </Link>
      </div>
      <ul className="flex flex-wrap gap-2 px-3 py-2">
        {top.map((m) => (
          <li key={`${m.ticker}|${m.entered_at}`}>
            <Link
              to="/radar"
              title={`${m.name || m.ticker}: racing ${m.direction}. ${INTENSITY_HINT}`}
              className="inline-flex items-center gap-1.5 rounded border border-border bg-page/40 px-2 py-1 text-xs hover:border-text-secondary/40 transition-colors"
            >
              <DirArrow dir={m.direction} className="text-[10px]" />
              <span className="font-bold text-text-primary">{m.ticker}</span>
              <IntensityMeter value={m.intensity} size="sm" className="text-[11px]" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
