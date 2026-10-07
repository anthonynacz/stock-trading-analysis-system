import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { DocTable, SectionHeading, StatTile } from './shared';

// Mirrors backend/radar/config.py PARAMS ("radar-sm-1") and the replay evidence
// in backend/radar/docs/signal-model.md. Update both when a threshold changes.
const RADAR = {
  scanMin: 5,
  universe: 550,
  entry: { z3: 2.5, z6: 3.0, absMovePct: 0.75, burstVol: 2.0, dayVol: 1.0, steadiness: 0.45, inPlayZ: 2.5, inPlayVol: 1.5 },
  confirm: { z6: 2.0, burstVol: 1.3 },
  hold: { fadeZ6: 1.0, dryVol: 0.6, stallMin: 30, stallZ6: 1.5, minDwellMin: 15, softFails: 2 },
  hard: { reversalZ3: 2.5, givebackPct: 70, staleBars: 3, haltLongMin: 30 },
  reentry: { sameMin: 30, oppositeMin: 15, maxPerDay: 3 },
  caps: { members: 12, newPerScan: 4, displaceMargin: 15, perSectorDir: 3 },
  market: { onSpyZ: 3.0, onBreadthPct: 60, offSpyZ: 2.0, offBreadthPct: 45, bump: 0.5, maxNewSameWay: 2 },
  liquidity: { priceMin: 5, adv20M: 25, medBarK: 150, last15K: 750, missingPct: 2, minSessions: 15 },
  weights: { thrust: 35, volume: 25, structure: 20, day: 10, accel: 10 },
} as const;

const E = RADAR.entry;
const H = RADAR.hold;
const X = RADAR.hard;

interface Row {
  name: string;
  meaning: ReactNode;
  rule: ReactNode;
}

const ENTRY_CHECKS: Row[] = [
  {
    name: 'Fast move',
    meaning: 'Moving much faster than the market (SPY) and than its own normal pace for this time of day.',
    rule: `≥ ${E.z3}σ vs market in 15 min, or ≥ ${E.z6}σ in 30 min, and both pointing the same way`,
  },
  {
    name: 'Real size',
    meaning: 'A visible move, so calm stocks such as utilities do not trigger on tiny wiggles.',
    rule: `≥ ${E.absMovePct}% over 30 min`,
  },
  {
    name: 'Heavy volume',
    meaning: 'More shares changing hands than usual at this time of day.',
    rule: `last 15 min ≥ ${E.burstVol}× normal, and the day so far ≥ ${E.dayVol}× normal`,
  },
  {
    name: 'Clean structure',
    meaning: 'A new high (or low) of the day, on the right side of VWAP, along a fairly straight path.',
    rule: `new extreme, right side of VWAP, steadiness ≥ ${E.steadiness}`,
  },
  {
    name: 'In play today',
    meaning:
      'The whole day is already unusual for this stock. This is the key filter: sharp moves in otherwise quiet stocks tend to snap back.',
    rule: `day move ≥ ${E.inPlayZ} daily σ vs market, day volume ≥ ${E.inPlayVol}× normal`,
  },
  {
    name: 'Tradable',
    meaning: 'Liquid, regular-session stocks only, and not blocked by a recent exit.',
    rule: `price ≥ $${RADAR.liquidity.priceMin}, ≥ $${RADAR.liquidity.adv20M}M a day, ≥ $${RADAR.liquidity.last15K}k in the last 15 min`,
  },
];

const STATES: Row[] = [
  { name: 'Warming up', meaning: 'Passed every entry check on the last bar; waits one more bar to confirm.', rule: 'Not on the radar yet' },
  { name: 'Racing', meaning: 'Confirmed, and the move still holds up.', rule: 'On the radar' },
  {
    name: 'Cooling',
    meaning: 'The last bar fell short on one of the hold checks. A clean bar puts it back to Racing.',
    rule: 'Drops off if the next bar also falls short',
  },
  {
    name: 'Halted',
    meaning: 'No new trades, possibly a trading halt. Its counters freeze instead of dropping it.',
    rule: `Kept until trading resumes, or the pause passes ${X.haltLongMin} min`,
  },
  {
    name: 'Late',
    meaning: 'Added during a catch-up scan, so it appeared more than 10 minutes after the move was confirmed.',
    rule: 'Badge',
  },
  { name: 'Back', meaning: 'On the radar again after dropping off earlier today.', rule: 'Badge' },
];

const EXITS: Row[] = [
  { name: 'Sharp reversal', meaning: 'A fast move the other way.', rule: `≤ −${X.reversalZ3}σ vs market in 15 min (immediate)` },
  {
    name: 'Gave back most of the move',
    meaning: 'Price has handed back most of the run that put it on the radar.',
    rule: `≥ ${X.givebackPct}% of the move from base to peak (immediate)`,
  },
  { name: 'Fell back through VWAP', meaning: "Back on the wrong side of the day's average price.", rule: 'Immediate' },
  {
    name: 'Momentum faded',
    meaning: 'The pace against the market has run out.',
    rule: `30-min pace < ${H.fadeZ6}σ and 15-min pace below zero, ${H.softFails} bars in a row`,
  },
  {
    name: 'Volume dried up',
    meaning: 'Trading has gone quiet.',
    rule: `last 15 min < ${H.dryVol}× normal, ${H.softFails} bars in a row`,
  },
  {
    name: 'Stalled',
    meaning: 'No new high (or low) for a while and little pace left.',
    rule: `no new extreme for ${H.stallMin} min and 30-min pace < ${H.stallZ6}σ, ${H.softFails} bars in a row`,
  },
  { name: 'Long trading halt', meaning: 'Halted for too long to keep.', rule: `${X.haltLongMin} min without trades` },
  { name: 'No fresh data', meaning: 'The data source stopped sending bars for this stock.', rule: `${X.staleBars} bars in a row` },
  {
    name: 'Replaced by a stronger mover',
    meaning: 'The radar was full and a much stronger mover took the weakest slot.',
    rule: `intensity at least ${RADAR.caps.displaceMargin} points higher`,
  },
  { name: 'Market closed', meaning: 'Everything drops off before the closing auction.', rule: '15:55 ET' },
];

const COLUMNS: Row[] = [
  { name: 'Since entry', meaning: 'Price change since the stock entered the radar.', rule: 'Percent' },
  { name: '5 / 15 / 30 min, Today', meaning: "Price change over the last 5, 15 and 30 minutes, and since yesterday's close.", rule: 'Percent' },
  { name: 'RVol', meaning: 'Recent volume compared with what is normal for this stock at this time of day.', rule: '3× = three times normal' },
  { name: 'vs VWAP', meaning: "Distance from today's volume-weighted average price.", rule: 'Percent' },
  {
    name: 'σ (sigma)',
    meaning:
      "The move measured in the stock's own typical move size, after removing what the market did. +3σ is an unusually large move for that stock.",
    rule: 'Details panel',
  },
  { name: 'Path', meaning: 'The half hour before entry (grey), the move since (colour); the dashed line marks the entry price.', rule: 'Sparkline' },
  { name: 'Intensity', meaning: 'How hard the stock is moving right now. For sorting only (see below).', rule: '0 to 100' },
];

const WEIGHTS = [
  { part: 'Pace against the market (15 and 30 min)', weight: RADAR.weights.thrust },
  { part: 'Volume burst', weight: RADAR.weights.volume },
  { part: 'Structure (new extreme, steadiness, VWAP distance)', weight: RADAR.weights.structure },
  { part: 'How unusual the whole day is', weight: RADAR.weights.day },
  { part: 'Acceleration', weight: RADAR.weights.accel },
];

const ROW_COLUMNS = (ruleHeader: string) => [
  { key: 'name', header: '', headerClass: 'w-28 sm:w-44', className: 'font-semibold text-text-primary align-top' },
  { key: 'meaning', header: 'In plain words', className: 'text-text-secondary align-top', render: (r: Row) => r.meaning },
  { key: 'rule', header: ruleHeader, hideMd: true, className: 'text-text-secondary font-mono text-[11px] align-top', render: (r: Row) => r.rule },
];

export default function RadarTab() {
  return (
    <div className="space-y-8">
      <div>
        <SectionHeading
          size="lg"
          title="Momentum Radar"
          blurb={
            <>
              A live list of US stocks racing up or down right now, on the{' '}
              <Link to="/radar" className="text-accent-300 hover:text-accent-200">
                Radar
              </Link>{' '}
              page. It describes what is moving; it does not forecast and it never places trades. Educational analysis,
              not financial advice.
            </>
          }
        />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatTile label="Scans" value={`Every ${RADAR.scanMin} min`} sub="09:35 to 16:00 ET" />
          <StatTile label="Stocks checked" value={`≈ ${RADAR.universe}`} sub="S&P 500, Nasdaq-100 + big movers" />
          <StatTile label="New entries" value="09:55–15:05 ET" sub="After a confirming bar" />
          <StatTile label="Typical day" value="4–10 entries" sub="Often empty: by design" />
        </div>
      </div>

      <div>
        <SectionHeading
          title="How a stock gets on the radar"
          blurb="Every 5 minutes the scanner first screens all stocks cheaply on price and volume, then looks closely at the few that stand out. A stock needs all of these on the same 5-minute bar. Every move is measured against SPY, so a broad rally does not list every high-beta stock."
        />
        <DocTable<Row> rows={ENTRY_CHECKS} rowKey={(r) => r.name} size="xs" columns={ROW_COLUMNS('Rule')} />
        <p className="text-xs text-text-secondary mt-3">
          <span className="font-semibold text-text-primary">Confirmation.</span> Passing once only makes a stock{' '}
          <span className="font-semibold text-text-primary">Warming up</span>. It joins the radar when the next bar
          confirms: price has not slipped back below where it was flagged, the 30-minute pace is still ≥{' '}
          {RADAR.confirm.z6}σ, volume is still ≥ {RADAR.confirm.burstVol}× normal and it stays on the right side of
          VWAP. A single extra bar costs 5 minutes but filters out many one-bar spikes.
        </p>
      </div>

      <div>
        <SectionHeading title="States and badges" blurb="Every member is re-checked on every new 5-minute bar." />
        <DocTable<Row> rows={STATES} rowKey={(r) => r.name} size="xs" columns={ROW_COLUMNS('Effect')} />
      </div>

      <div>
        <SectionHeading
          title="Why a stock drops off"
          blurb={`Rules marked immediate fire on the bar they happen. The fading checks (momentum, volume, stall) only count after ${H.minDwellMin} minutes on the radar and need ${H.softFails} weak bars in a row (the first weak bar shows Cooling), so a stock does not flicker on and off. Missing data never removes a stock at once.`}
        />
        <DocTable<Row> rows={EXITS} rowKey={(r) => r.name} size="xs" columns={ROW_COLUMNS('Rule')} />
        <p className="text-xs text-text-secondary mt-3">
          <span className="font-semibold text-text-primary">Coming back.</span> After dropping off, a stock can return
          in the same direction only after {RADAR.reentry.sameMin} minutes and beyond its previous peak, or in the other
          direction after {RADAR.reentry.oppositeMin} minutes, at most {RADAR.reentry.maxPerDay} times a day. It then
          shows a <span className="font-semibold text-text-primary">Back</span> badge.
        </p>
      </div>

      <div>
        <SectionHeading
          title="Crowding and market-wide moves"
          blurb="Limits that keep the list readable when many stocks move together."
        />
        <ul className="list-disc pl-5 space-y-1.5 text-xs text-text-secondary">
          <li>
            At most <span className="text-text-primary font-semibold">{RADAR.caps.members}</span> stocks on the radar and{' '}
            {RADAR.caps.newPerScan} new ones per scan, strongest first. When it is full, a newcomer replaces the weakest
            member only if its intensity is at least {RADAR.caps.displaceMargin} points higher.
          </li>
          <li>
            At most <span className="text-text-primary font-semibold">{RADAR.caps.perSectorDir}</span> stocks per sector
            and direction. The rest are summed up in a sector banner (&ldquo;+N more held back&rdquo;), because a whole
            sector moving together is one story, not ten.
          </li>
          <li>
            <span className="text-text-primary font-semibold">Market-wide mode</span> turns on when SPY moves ≥{' '}
            {RADAR.market.onSpyZ}σ in 30 minutes or {RADAR.market.onBreadthPct}% of stocks move the same way. Stocks
            moving with the market then need {RADAR.market.bump}σ more pace and at most {RADAR.market.maxNewSameWay}{' '}
            join per scan; stocks moving against the market are unaffected. It turns off below {RADAR.market.offSpyZ}σ
            and {RADAR.market.offBreadthPct}% for two bars.
          </li>
        </ul>
      </div>

      <div>
        <SectionHeading title="Reading the columns" />
        <DocTable<Row> rows={COLUMNS} rowKey={(r) => r.name} size="xs" columns={ROW_COLUMNS('Unit')} />
      </div>

      <div>
        <SectionHeading
          title="Intensity is not a probability"
          blurb="Intensity (0 to 100) blends how strong the move's ingredients are right now. It ranks the list and nothing else."
        />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <DocTable<(typeof WEIGHTS)[number]>
            rows={WEIGHTS}
            rowKey={(w) => w.part}
            size="xs-dense"
            columns={[
              { key: 'part', header: 'Ingredient', className: 'text-text-secondary' },
              { key: 'weight', header: 'Weight', align: 'center', className: 'font-mono text-text-primary', render: (w) => `${w.weight}%` },
            ]}
          />
          <div className="bg-amber-900/20 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-200 leading-relaxed">
            In the replay a higher intensity did <span className="font-semibold">not</span> mean the move was more likely
            to continue: the rank correlation with the next 30 minutes was about −0.09, and the top quarter by intensity
            kept going less often (54%) than the bottom quarter (65%). Very fast, very hot moves are often close to
            exhausted. Never read it as confidence.
          </div>
        </div>
      </div>

      <div>
        <SectionHeading
          title="What to expect"
          blurb="Evidence from a replay of 491 stocks over 39 sessions (Aug 3 to Sep 25, 2026) with these exact rules."
        />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
          <StatTile label="Still moving the same way 30 min later" value="≈ 58%" sub="vs 47% for a plain fast-move list" />
          <StatTile label="Entries per day" value="≈ 5.7" sub="Median 4; usually none listed at a given moment" />
          <StatTile label="Time on the radar" value="≈ 40 min" sub="Middle half 25–55 min" />
          <StatTile label="Flicker (off and back on)" value="≈ 3.6%" sub="Of entries, thanks to the hold rules" />
        </div>
        <ul className="list-disc pl-5 space-y-1.5 text-xs text-text-secondary">
          <li>
            The list is <span className="text-text-primary font-semibold">empty most of the time</span>. That is the
            price of the &ldquo;in play&rdquo; filter; loosening it brings back moves that snap back (below 50%).
          </li>
          <li>
            58% has a wide margin (about 52–65%) and comes from a calm period with no crash or melt-up day. The gain over
            a plain fast-move list is more reliable than the exact number.
          </li>
          <li>
            Data comes from free sources and can be delayed, revised or missing. Extended hours are not used: pre- and
            post-market 5-minute bars carried no volume. Confirm live quotes elsewhere.
          </li>
          <li>
            Everything resets each morning; nothing carries overnight. An overnight gap reaches the radar through the
            &ldquo;in play today&rdquo; check once the session opens.
          </li>
        </ul>
      </div>

      <div>
        <SectionHeading title="Alerts" />
        <p className="text-xs text-text-secondary">
          Turn on <span className="font-semibold text-text-primary">Momentum Radar entry</span> in{' '}
          <Link to="/settings" className="text-accent-300 hover:text-accent-200">
            Settings › Alerts
          </Link>{' '}
          to get a Discord message when a stock enters the radar (off by default). The message links back to the Radar
          page.
        </p>
      </div>
    </div>
  );
}
