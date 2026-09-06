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
  { filter: 'Options Volume', threshold: '≥ 1,000 contracts/day' },
  { filter: 'Analyst Coverage', threshold: '≥ 5 analysts' },
];

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
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
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
          sub="Limits turnover per rotation"
        />
        <StatTile
          size="lg"
          label="Toxic Removal Threshold"
          value={`${WATCHLIST_LIMITS.toxicDays} days`}
          valueClass="text-red-400"
          sub="Consecutive SELL/STRONG_SELL recs — removed at the next rotation regardless of composite score; recommendation history is preserved"
        />
      </div>

      <div>
        <SectionHeading title="Manual & Locked Tickers" />
        <p className="text-sm text-text-secondary">
          <span className="text-accent-400 font-semibold">Manual</span> tickers are added by hand and are
          not scored — they remain on the watchlist until removed.{' '}
          <span className="text-amber-400 font-semibold">Locked</span> tickers participate in scoring but
          are protected from automatic rotation, ensuring they stay on the watchlist regardless of rank.
        </p>
      </div>
    </div>
  );
}
