import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { RadarSnapshot } from '../../types';
import { fmtInt, fmtNum } from '../../utils/format';
import { fmtEt, fmtEtDay, toMs } from './clock';
import { publishedLate } from './model';

const SUMMARY = 'cursor-pointer text-sm font-semibold text-text-secondary hover:text-text-primary transition-colors';

function Collapsible({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="bg-card border border-border rounded-lg px-3 py-2.5 sm:px-4">
      <details>
        <summary className={SUMMARY}>{title}</summary>
        <div className="mt-2 text-sm text-text-secondary leading-relaxed space-y-2">{children}</div>
      </details>
    </section>
  );
}

const B = ({ children }: { children: ReactNode }) => <b className="text-text-primary font-semibold">{children}</b>;

/** Plain-language explanation of entries, exits and the columns (from the GitHub radar page). */
export function RadarHowItWorks() {
  return (
    <Collapsible title="How the radar works">
      <p>
        Every 5 minutes from 09:35 to 16:00 ET on market days, the scanner checks the S&amp;P 500, the Nasdaq-100 and
        the day's large movers. A stock <B>enters</B> when, over the last 15 to 30 minutes, it moves much faster than
        the market and than its own normal pace, on heavy volume for that time of day, on the right side of VWAP, and
        the next 5-minute bar confirms the move. Stocks waiting for that confirming bar are listed under{' '}
        <B>Warming up</B>. New entries are possible from about 09:55 to 15:05 ET, so the list is often empty in the
        first minutes of the day.
      </p>
      <p>
        A stock <B>drops off</B> when the move is over: momentum fades, volume dries up, it stalls, reverses sharply,
        gives back most of the move or falls back through VWAP. Everything drops off at the close. Missing data never
        removes a stock right away: a stock that stops trading is marked <B>Halted</B> and kept.
      </p>
      <p>
        Moves are measured against the market (SPY), so a broad rally does not list every stock. When the whole market
        moves sharply, a market-wide banner appears and the radar gets stricter with stocks moving the same way as the
        market. At most 3 stocks per sector and direction are listed; the rest are summed up in a sector banner.
      </p>
      <ul className="list-disc pl-5 space-y-1">
        <li>
          <B>Since entry</B>: price change since the stock entered. The line shows the half hour before entry (grey)
          and the move since (colour); the dashed line and the ring mark the entry price.
        </li>
        <li>
          <B>5 / 15 / 30 min, Today</B>: price change over the last 5, 15 and 30 minutes, and since yesterday's close.
        </li>
        <li>
          <B>RVol</B>: recent volume compared with what is normal for this stock at this time of day (3× means three
          times normal).
        </li>
        <li>
          <B>vs VWAP</B>: distance from today's volume-weighted average price.
        </li>
        <li>
          <B>Intensity</B> (0 to 100): how strong the move's ingredients are right now (pace against the market,
          volume, steadiness). It is used for sorting only. It is not a probability or a forecast, and in testing a
          higher intensity did not mean the move was more likely to continue.
        </li>
        <li>
          <B>Cooling</B>: the last bar fell short; the stock drops off if the next one does too. <B>Late</B>: added
          during a catch-up scan, so it appeared here more than 10 minutes after the move was confirmed. <B>Back</B>:
          on the radar again after dropping off earlier today.
        </li>
      </ul>
      <p>
        More detail in{' '}
        <Link to="/knowledge?tab=radar" className="text-accent-300 hover:text-accent-200">
          Knowledge › Radar
        </Link>
        . To get a Discord message when a stock enters, turn on <B>Momentum Radar entry</B> in{' '}
        <Link to="/settings" className="text-accent-300 hover:text-accent-200">
          Settings › Alerts
        </Link>
        .
      </p>
    </Collapsible>
  );
}

/** What the last scan reported about itself (all from the snapshot). */
export function RadarScannerHealth({ st }: { st: RadarSnapshot }) {
  const h = st.health ?? ({} as Partial<RadarSnapshot['health']>);
  const src = st.source ?? ({} as Partial<RadarSnapshot['source']>);
  const k = st.counts ?? ({} as Partial<RadarSnapshot['counts']>);
  const tick = toMs(st.tick_id);
  const lastOk = toMs(src.last_ok_at);
  const loopStart = toMs(h.loop_started_at);
  const rows: [string, string][] = [
    ['Last scan', `${tick !== null ? fmtEtDay(tick) : '—'} · ${st.status}${st.message ? ` (${st.message})` : ''}`],
    ['Scan took', h.last_tick_ms !== null && h.last_tick_ms !== undefined ? `${fmtNum(h.last_tick_ms / 1000, 1)} s` : '—'],
    [
      'Stocks checked',
      k.universe !== null && k.universe !== undefined
        ? `${fmtInt(k.universe)}${k.stage_b !== null && k.stage_b !== undefined ? ` (${fmtInt(k.stage_b)} looked at closely)` : ''}`
        : '—',
    ],
    [
      'Data source',
      [
        src.name ? src.name.charAt(0).toUpperCase() + src.name.slice(1) : '—',
        src.status,
        src.consecutive_failures ? `${src.consecutive_failures} failed calls in a row` : null,
        lastOk !== null ? `last good answer ${fmtEt(lastOk)}` : null,
      ]
        .filter(Boolean)
        .join(' · '),
    ],
    [
      'Scans today',
      h.ticks_today !== null && h.ticks_today !== undefined
        ? `${fmtInt(h.ticks_today)}${h.ticks_skipped ? ` (${fmtInt(h.ticks_skipped)} missed)` : ''}`
        : '—',
    ],
    ['Scanner run', loopStart !== null ? `started ${fmtEt(loopStart)}${h.loop_run_id ? ` · ${h.loop_run_id}` : ''}` : '—'],
    ['Signal settings', st.params_version || '—'],
  ];
  // Always 0 in Vela (no git publishing); only worth a line when it is not.
  if (h.published_late) rows.push(['Published late', publishedLate(h.published_late)]);

  return (
    <Collapsible title="Scanner health">
      <dl className="text-xs leading-relaxed">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="inline font-semibold text-text-primary">{label}: </dt>
            <dd className="inline">{value}</dd>
          </div>
        ))}
      </dl>
    </Collapsible>
  );
}

export function RadarDisclaimer() {
  return (
    <p className="text-[11px] text-text-secondary leading-relaxed pb-4">
      Educational analysis of what is moving now, not a forecast and not financial advice. Prices come from free data
      sources and can be delayed or missing; confirm live quotes elsewhere. Times are US Eastern (ET).
    </p>
  );
}
