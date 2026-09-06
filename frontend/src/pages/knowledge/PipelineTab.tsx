import { Link } from 'react-router-dom';
import { DocTable, SectionHeading, WATCHLIST_LIMITS } from './shared';

interface DataSource {
  source: string;
  usage: string;
  limit: string;
}

// Times mirror backend/services/scheduler.py cron jobs (ET, Mon–Fri unless noted).
const PHASES: { name: string; time: string; desc: string }[] = [
  {
    name: 'Retention Sweep',
    time: '04:00 (daily)',
    desc: 'Prunes expired per-user data and old run history according to retention windows.',
  },
  {
    name: 'Universe Discovery',
    time: '05:00',
    desc: 'Scans FMP market lists (most active, gainers, losers) and news-trending tickers. Surfaces candidates for user approval.',
  },
  {
    name: 'Watchlist Rotation',
    time: '05:30',
    desc: `Scores universe stocks by composite score, rotates the active watchlist (max ${WATCHLIST_LIMITS.maxDailyChanges} changes). Snapshot saved for historical viewing.`,
  },
  {
    name: 'Analyst Ratings',
    time: '06:00, 11:00, 14:00',
    desc: 'Scans for analyst rating changes since prior close. Classifies as TIER_CHANGE, PT_CHANGE, INITIATION, or REITERATION.',
  },
  {
    name: 'AM Digest',
    time: '06:00–13:30 every 30 min',
    desc: 'Dispatches the morning digest to each user at their configured delivery time.',
  },
  {
    name: 'Alerts Scan',
    time: '06:00–17:30 every 30 min',
    desc: 'Checks configured alert triggers and delivers any that fired.',
  },
  {
    name: 'News + Sentiment',
    time: '06:30, 10:00, 16:45',
    desc: 'Fetches and categorizes news. Sentiment scored via FinBERT. Tracks per-ticker relevance via junction table.',
  },
  {
    name: 'Earnings Calendar',
    time: '07:00, 17:00',
    desc: 'Refreshes earnings calendar with EPS estimates, manages catalyst windows (T-14 to T+10).',
  },
  {
    name: 'Intraday News',
    time: '08:15–16:15 hourly',
    desc: 'Polls for fresh headlines on watchlist tickers and rescores affected recommendations.',
  },
  {
    name: 'Options Snapshots',
    time: '09:35, 16:15',
    desc: 'Pulls options chains via yfinance. Calculates IV rank, detects unusual volume activity.',
  },
  {
    name: 'Recommendations',
    time: '10:30, 16:30',
    desc: 'Stacks all signals into conviction scores. Generates rationale, selects contracts, classifies action.',
  },
  {
    name: 'Industry Recs',
    time: '11:15, 17:15',
    desc: "Aggregates the day's recommendations into industry-level views.",
  },
  {
    name: 'P&L Snapshot',
    time: '16:45',
    desc: 'Records a nightly portfolio P&L snapshot for open positions.',
  },
  {
    name: 'Multibagger Scan',
    time: 'Fri 17:30',
    desc: 'Weekly scan for long-horizon multi-bagger candidates.',
  },
  {
    name: 'Outcome Scoring',
    time: '18:00',
    desc: 'Scores recommendation outcomes at T+1 / T+5 / T+20 for the Performance page.',
  },
];

const DATA_SOURCES: DataSource[] = [
  { source: 'FMP (Financial Modeling Prep)', usage: 'Analyst ratings (primary), earnings with EPS, insider trades, news, discovery lists', limit: '300 req/min' },
  { source: 'Finnhub', usage: 'News (primary), analyst ratings (fallback)', limit: '60 calls/min (free)' },
  { source: 'yfinance', usage: 'Prices, fundamentals, options chains, technicals', limit: '~2 req/sec (rate limited)' },
  { source: 'NewsAPI', usage: 'News (last fallback)', limit: '100 calls/day (free)' },
  { source: 'FinBERT', usage: 'Sentiment scoring (ProsusAI/finbert, runs locally on CPU)', limit: 'No external limit' },
];

export default function PipelineTab() {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Daily Pipeline Schedule</h3>
        <p className="text-xs text-text-secondary mb-3">
          All times US Eastern; jobs run Mon–Fri unless noted.{' '}
          <Link to="/schedule" className="text-blue-400 hover:underline">
            See the Schedule page for the live upcoming-run list
          </Link>
          .
        </p>
        <div className="space-y-2">
          {PHASES.map((p) => (
            <div
              key={p.name}
              className="flex items-start gap-3 bg-card border border-border rounded-lg px-4 py-3"
            >
              <div className="w-32 shrink-0">
                <span className="font-mono text-xs text-text-secondary">{p.time}</span>
              </div>
              <div className="flex-1">
                <span className="text-sm font-semibold text-text-primary">{p.name}</span>
                <p className="text-xs text-text-secondary mt-0.5">{p.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <SectionHeading title="Manual Refresh" />
        <p className="text-sm text-text-secondary">
          Refresh (Pro+) runs the intraday phases; Premium/Admin can run the full pipeline, where
          ratings/earnings/news/options execute concurrently.
        </p>
      </div>

      <div>
        <SectionHeading title="Data Sources" />
        <DocTable<DataSource>
          rows={DATA_SOURCES}
          rowKey={(d) => d.source}
          columns={[
            { key: 'source', header: 'Source', className: 'text-text-primary font-medium whitespace-nowrap' },
            { key: 'usage', header: 'Usage', className: 'text-text-secondary' },
            { key: 'limit', header: 'Rate Limit', hideMd: true, headerClass: 'w-36', className: 'text-text-secondary font-mono text-xs' },
          ]}
        />
      </div>

      <div>
        <SectionHeading title="Idempotency" />
        <p className="text-sm text-text-secondary">
          Re-running a phase on the same day replaces that day's rows; manual and locked tickers are never touched.
        </p>
      </div>
    </div>
  );
}
