import { DocTable, SectionHeading, StatTile, WATCHLIST_LIMITS } from './shared';

const WEIGHTS = [
  { factor: 'Catalyst Proximity', weight: '25%', desc: 'Days to next earnings: 100 pts within 7d, 70 within 14d, 30 within 30d' },
  { factor: 'Analyst Momentum', weight: '20%', desc: 'Recent rating upgrades/downgrades and PT changes' },
  { factor: 'Recommendation Conviction', weight: '15%', desc: 'Feedback loop: SELL/STRONG_SELL recs deprioritize the stock' },
  { factor: 'Sector Momentum', weight: '15%', desc: 'Relative strength of the stock\'s sector' },
  { factor: 'Options Liquidity', weight: '10%', desc: 'Daily options volume and open interest' },
  { factor: 'Volatility Profile', weight: '5%', desc: 'IV rank and historical volatility' },
  { factor: 'Institutional Flow', weight: '5%', desc: 'Insider buying/selling activity' },
  { factor: 'Price vs Consensus PT', weight: '5%', desc: 'Discount to average analyst price target' },
];
type Weight = (typeof WEIGHTS)[number];

const FILTERS = [
  { filter: 'Market Cap', threshold: '≥ $5B' },
  { filter: 'Avg Daily Volume', threshold: '≥ 2M shares' },
];

const ROTATION_RULES = [
  {
    rule: 'Entry',
    desc: `Ranks in the top ${WATCHLIST_LIMITS.enterRank} of its sector, or beats the weakest replaceable member of the sector by ${WATCHLIST_LIMITS.enterMargin} points. Catalyst (earnings ≤ 14d) and rating-change names go first.`,
  },
  {
    rule: 'Exit',
    desc: `Ranks worse than ${WATCHLIST_LIMITS.exitRank} in its sector (or fails the filters) on ${WATCHLIST_LIMITS.exitDays} consecutive rotations.`,
  },
  {
    rule: 'Minimum stay',
    desc: `No automatic exit before ${WATCHLIST_LIMITS.minStayDays} trading days on the list.`,
  },
  {
    rule: 'Caps',
    desc: `${WATCHLIST_LIMITS.max} names in total, ${WATCHLIST_LIMITS.perSector} per sector. At a cap an entrant needs a free slot or must beat the weakest replaceable member of its sector by ${WATCHLIST_LIMITS.enterMargin} points (one in, one out). Over a cap the list shrinks by its lowest scores, ${WATCHLIST_LIMITS.maxDailyChanges} changes a day at most.`,
  },
  {
    rule: 'Once a day',
    desc: 'The rotation runs once per day (05:30 ET). A second run the same day, including the Full pipeline preset, is skipped.',
  },
  {
    rule: 'Protected',
    desc: `Manual names, locked names, tickers with an open position, and unusual options activity (last session's call volume ≥ 3x its 20-session average). A grace lock from a manual rotate-in expires after ${WATCHLIST_LIMITS.lockDays} trading days.`,
  },
  {
    rule: 'Not candidates',
    desc: 'ETFs and index products (SPY, QQQ, XLE, XLF, HYG, EWZ) are never rotated in or proposed as replacements.',
  },
];
type RotationRule = (typeof ROTATION_RULES)[number];

export default function WatchlistTab() {
  return (
    <div className="space-y-6">
      <div>
        <SectionHeading
          title="Composite Scoring Weights"
          blurb="Each stock in the universe is scored daily. The top stocks per sector are selected for the active watchlist."
        />
        <DocTable<Weight>
          rows={WEIGHTS}
          rowKey={(w) => w.factor}
          columns={[
            { key: 'factor', header: 'Factor', className: 'text-text-primary' },
            { key: 'weight', header: 'Weight', align: 'center', headerClass: 'w-20', className: 'font-mono font-semibold text-accent-400' },
            { key: 'desc', header: 'Description', hideMd: true, className: 'text-text-secondary' },
          ]}
        />
      </div>

      <div>
        <SectionHeading
          title="Liquidity Filters"
          blurb="Stocks must pass all filters before scoring. This ensures tradable, liquid names only."
        />
        <div className="grid grid-cols-2 gap-3">
          {FILTERS.map((f) => (
            <StatTile key={f.filter} label={f.filter} value={f.threshold} />
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <StatTile
          size="lg"
          label="Max Watchlist Size"
          value={WATCHLIST_LIMITS.max}
          sub={`${WATCHLIST_LIMITS.perSector} per sector, ${WATCHLIST_LIMITS.sectors} sectors`}
        />
        <StatTile
          size="lg"
          label="Max Daily Changes"
          value={WATCHLIST_LIMITS.maxDailyChanges}
          sub="Additions + removals per rotation; exits first while over a cap"
        />
        <StatTile
          size="lg"
          label="Toxic Removal"
          value={WATCHLIST_LIMITS.dropToxic ? `${WATCHLIST_LIMITS.toxicDays} days` : 'Off'}
          valueClass={WATCHLIST_LIMITS.dropToxic ? 'text-red-400' : 'text-text-secondary'}
          sub={`When on: ${WATCHLIST_LIMITS.toxicDays} consecutive SELL/STRONG_SELL recs remove a name (after the minimum stay). Off by default: bearish names are put candidates`}
        />
      </div>

      <div>
        <SectionHeading
          title="Rotation Rules"
          blurb="Hysteresis keeps names on the list long enough to act on them: entering is harder than staying."
        />
        <DocTable<RotationRule>
          rows={ROTATION_RULES}
          rowKey={(r) => r.rule}
          columns={[
            { key: 'rule', header: 'Rule', headerClass: 'w-36', className: 'text-text-primary' },
            { key: 'desc', header: 'Description', className: 'text-text-secondary' },
          ]}
        />
      </div>

      <div>
        <SectionHeading title="Manual & Locked Tickers" />
        <p className="text-sm text-text-secondary">
          <span className="text-accent-400 font-semibold">Manual</span> tickers are added by hand and are
          not scored — they remain on the watchlist until removed.{' '}
          <span className="text-amber-400 font-semibold">Locked</span> tickers participate in scoring but
          are protected from automatic rotation, ensuring they stay on the watchlist regardless of rank. A
          ticker rotated in by hand is grace-locked for {WATCHLIST_LIMITS.lockDays} trading days.
        </p>
      </div>
    </div>
  );
}
