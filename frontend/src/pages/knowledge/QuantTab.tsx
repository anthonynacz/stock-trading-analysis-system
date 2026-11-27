import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { DocTable, SectionHeading, StatTile } from './shared';
import { READING_CFG } from '../../components/quant/model';

// Mirrors backend/quant/config.py (HAR horizon, READING) and the measured table in backend/quant/CLAUDE.md.
// Update both when a threshold or the evaluation changes.

interface Row {
  name: string;
  meaning: ReactNode;
  rule: ReactNode;
}

const READINGS: Row[] = [
  {
    name: 'Cheap',
    meaning: 'Options cost less than usual for the volatility expected. Better for a buyer.',
    rule: `Implied ÷ forecast at or below the ${Math.round(READING_CFG.cheapPctl * 100)}th percentile of its reference`,
  },
  { name: 'Fair', meaning: 'About the usual price.', rule: 'Between the two' },
  {
    name: 'Rich',
    meaning: 'Options cost more than usual for the volatility expected. Worse for a buyer.',
    rule: `At or above the ${Math.round(READING_CFG.richPctl * 100)}th percentile`,
  },
  {
    name: 'Not comparable',
    meaning:
      'Earnings fall inside the next 21 sessions, or the price is pinned (a cash shell before its merger, a pending cash deal, a cash fund). The option price includes something the forecast cannot, so no reading.',
    rule: 'Earnings date inside the window, or a pinned price',
  },
  {
    name: 'Not enough history',
    meaning: 'Nothing reliable to compare with yet. Common in the first months.',
    rule: `Fewer than ${READING_CFG.ownMinObs} own readings and fewer than ${READING_CFG.xsMinNames} comparable names today`,
  },
  { name: 'No option price', meaning: 'No implied volatility was available that day.', rule: '—' },
];

const COLUMNS: Row[] = [
  {
    name: 'Forecast vol',
    meaning: 'How much the stock is expected to swing over the next 21 trading sessions, from the HAR model.',
    rule: 'Yearly volatility (%)',
  },
  {
    name: 'Last 21d vol',
    meaning: 'How much it actually swung over the last 21 sessions: the naive "next month looks like last month" guess.',
    rule: 'Yearly volatility (%)',
  },
  {
    name: 'Implied vol',
    meaning: 'What option prices imply for about the next 30 days. Higher = pricier options.',
    rule: 'Yearly volatility (%)',
  },
  {
    name: 'Implied ÷ forecast',
    meaning: 'How the option price compares with the forecast. Usually above 1 (see below).',
    rule: 'Ratio (×)',
  },
];

const ROW_COLUMNS = (third: string) => [
  { key: 'name', header: 'Name', className: 'font-semibold text-text-primary whitespace-nowrap' },
  { key: 'meaning', header: 'Meaning', className: 'text-text-secondary', render: (r: Row) => r.meaning },
  { key: 'rule', header: third, className: 'text-text-secondary', hideMd: true, render: (r: Row) => r.rule },
];

export default function QuantTab() {
  return (
    <div className="space-y-8">
      <div>
        <SectionHeading
          title="Volatility vs option prices"
          size="lg"
          blurb={
            <>
              The{' '}
              <Link to="/quant" className="text-accent-300 hover:text-accent-200">
                Volatility page
              </Link>{' '}
              forecasts how much each stock will move over the next 21 trading sessions and sets that beside what options
              cost. It is a measured statistic shown on its own page: it does not feed recommendations, conviction, the radar
              or any entry verdict. Not advice, and not a forecast of price: it says how much, never which way.
            </>
          }
        />
      </div>

      <div>
        <SectionHeading title="Reading the columns" />
        <DocTable<Row> rows={COLUMNS} rowKey={(r) => r.name} size="xs" columns={ROW_COLUMNS('Unit')} />
      </div>

      <div>
        <SectionHeading
          title="Why the reading is relative"
          blurb="Option sellers charge a premium for taking risk, so implied vol normally sits above the volatility that follows. Implied above forecast is the usual state, not a sign that options are overpriced."
        />
        <ul className="list-disc pl-5 space-y-1.5 text-xs text-text-secondary">
          <li>
            The reading places today&rsquo;s implied ÷ forecast ratio among the ticker&rsquo;s own earlier ratios (past year,
            earnings periods left out), once it has {READING_CFG.ownMinObs} of them.
          </li>
          <li>
            Before that, it is compared with today&rsquo;s other names that have a 30-day implied vol, if there are at least{' '}
            {READING_CFG.xsMinNames}. That says &ldquo;cheap compared with other stocks today&rdquo;, a weaker statement.
          </li>
          <li>Otherwise the reading is &ldquo;not enough history&rdquo;. It fills in as daily data accumulates.</li>
        </ul>
      </div>

      <div>
        <SectionHeading title="Readings" blurb="Colours are read for someone buying options now." />
        <DocTable<Row> rows={READINGS} rowKey={(r) => r.name} size="xs" columns={ROW_COLUMNS('Rule')} />
      </div>

      <div>
        <SectionHeading
          title="Where implied vol comes from"
          blurb="The source is recorded with every reading; a ⚠ in the table marks the weaker ones."
        />
        <ul className="list-disc pl-5 space-y-1.5 text-xs text-text-secondary">
          <li>
            <span className="text-text-primary font-semibold">30-day, from option chain</span> (best): our own daily
            snapshot, interpolated to exactly 30 days.
          </li>
          <li>
            <span className="text-text-primary font-semibold">Radar ~30-day</span>: the radar&rsquo;s option data, about 30
            days. Close, but not the same snapshot.
          </li>
          <li>
            <span className="text-text-primary font-semibold">Daily 30-day reading</span>: the clean 30-day IV taken at
            15:00 ET for the IV cost score. A 30-day value, but measured a different way and at a different time.
          </li>
          <li>
            <span className="text-text-primary font-semibold">Nearest expiry</span> (older pipeline snapshots only): short-dated options only. They swing
            more than 30-day ones, so the comparison is weak.
          </li>
        </ul>
      </div>

      <div>
        <SectionHeading
          title="What was measured"
          blurb="Out-of-sample tests: every forecast used only data available on its date, scored against the naive guess (last 21 sessions carried forward). The tests so far do not agree, so the model's edge is not established."
        />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
          <StatTile label="Yahoo bars, 56 names" value="≈ 23% lower error" sub="QLIKE 0.286 vs 0.373, t −3.6 (Nov 2026)" />
          <StatTile label="Yahoo bars, 180 names" value="≈ 22% lower error" sub="QLIKE 0.303 vs 0.390, t −3.7 (Nov 2026)" />
          <StatTile label="This server's bars, 270 names" value="not confirmed" sub="lower error, but not beyond luck; long-run average lower still" />
          <StatTile label="Model" value="HAR" sub="fitted across all stocks" />
        </div>
        <ul className="list-disc pl-5 space-y-1.5 text-xs text-text-secondary">
          <li>
            <span className="text-text-primary font-semibold">Not settled:</span> on the server&rsquo;s own price data
            (a different vendor and more names) the error scores came out much higher, most of all for the naive guess,
            and the model&rsquo;s lead over it was within noise. Re-run on free price data with the same kind of names, most
            of that gap came from cash shells: before a merger the ticker is a trust pinned near $10, and its quiet
            history makes every forecast far too low once the real company trades. Since model har-gk-3 that history is
            left out (and missing days and unadjusted splits are handled); until a test on the server&rsquo;s data agrees,
            read the forecast as one model&rsquo;s estimate, not as a proven edge.
          </li>
          <li>
            <span className="text-text-primary font-semibold">Survivorship:</span> every test uses names listed today.
            Companies that failed are missing, which flatters any forecast somewhat.
          </li>
          <li>
            The page&rsquo;s &ldquo;How good is this model&rdquo; panel shows the latest scorecard measured on this
            server&rsquo;s own data, with its verdict: better beyond noise, lower but not distinguishable from noise, or
            not better.
          </li>
          <li>
            A cheap reading does not mean the option will make money: the stock still has to move enough, in the right
            direction, before expiry.
          </li>
        </ul>
      </div>
    </div>
  );
}
