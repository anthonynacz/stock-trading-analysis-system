import { useState } from 'react';

type Section = 'guide' | 'signals' | 'classification' | 'watchlist' | 'strikes' | 'pipeline';

const TABS: { key: Section; label: string }[] = [
  { key: 'guide', label: 'Trading Guide' },
  { key: 'signals', label: 'Signal Stacking' },
  { key: 'classification', label: 'Classification' },
  { key: 'watchlist', label: 'Watchlist Scoring' },
  { key: 'strikes', label: 'Strike Profiles' },
  { key: 'pipeline', label: 'Pipeline & Data' },
];

const ACTION_COLORS: Record<string, string> = {
  STRONG_BUY: '#2ea043',
  BUY: '#56d364',
  HOLD: '#d29922',
  SELL: '#f85149',
  STRONG_SELL: '#da3633',
};

/* ── Signal data ────────────────────────────────────────────────────────── */

interface Signal {
  name: string;
  points: string;
  detail: string;
}

interface SignalCategory {
  title: string;
  description: string;
  signals: Signal[];
}

const SIGNAL_CATEGORIES: SignalCategory[] = [
  {
    title: 'Analyst Ratings',
    description:
      'Rating changes from Wall Street firms, weighted by firm tier. T1 = bulge bracket (Goldman, JPMorgan, etc.), T2 = mid-tier (Evercore, Jefferies, etc.), T3 = boutique/other.',
    signals: [
      { name: 'T1 Upgrade', points: '+40', detail: 'Bulge bracket upgrade (e.g., Goldman Sachs Buy)' },
      { name: 'T2 Upgrade', points: '+25', detail: 'Mid-tier firm upgrade' },
      { name: 'T3 Upgrade', points: '+15', detail: 'Boutique firm upgrade' },
      { name: 'T1 Downgrade', points: '-40', detail: 'Bulge bracket downgrade' },
      { name: 'T2 Downgrade', points: '-25', detail: 'Mid-tier firm downgrade' },
      { name: 'T3 Downgrade', points: '-15', detail: 'Boutique firm downgrade' },
      { name: 'PT Raise >10%', points: '+15', detail: 'Price target raised by more than 10%' },
      { name: 'PT Raise 5-10%', points: '+10', detail: 'Price target raised by 5-10%' },
      { name: 'PT Cut >10%', points: '-15', detail: 'Price target cut by more than 10%' },
      { name: 'PT Cut 5-10%', points: '-10', detail: 'Price target cut by 5-10%' },
    ],
  },
  {
    title: 'Earnings',
    description: 'Signals from the most recent quarterly earnings report and upcoming catalysts.',
    signals: [
      { name: 'Beat + Raised Guidance', points: '+30', detail: 'EPS beat with raised forward guidance' },
      { name: 'Beat', points: '+20', detail: 'EPS beat without guidance change' },
      { name: 'Miss', points: '-20', detail: 'EPS miss without guidance change' },
      { name: 'Miss + Lowered Guidance', points: '-30', detail: 'EPS miss with lowered guidance' },
      { name: 'Active Catalyst Window', points: '+5', detail: 'Within T-7 to T+10 of earnings date' },
    ],
  },
  {
    title: 'Technical / RSI',
    description: 'RSI-14 and moving average signals from 2-month price history.',
    signals: [
      { name: 'RSI < 20 (Extreme Oversold)', points: '+15', detail: 'Deeply oversold — high reversal probability' },
      { name: 'RSI < 30 (Oversold)', points: '+10', detail: 'Oversold territory' },
      { name: 'RSI > 80 (Extreme Overbought)', points: '-15', detail: 'Extremely overbought — pullback risk' },
      { name: 'RSI > 70 (Overbought)', points: '-10', detail: 'Overbought territory' },
      { name: 'Above 50d SMA', points: '+10', detail: 'Price trading above 50-day simple moving average' },
      { name: 'Below 50d SMA', points: '-10', detail: 'Price trading below 50-day SMA' },
      { name: 'Above 200d SMA', points: '+5', detail: 'Long-term uptrend confirmation' },
      { name: 'Below 200d SMA', points: '-5', detail: 'Long-term downtrend' },
    ],
  },
  {
    title: 'Momentum',
    description: '5-day and 20-day price momentum (percentage change).',
    signals: [
      { name: '5d Momentum > 5%', points: '+10', detail: 'Strong short-term upward momentum' },
      { name: '5d Momentum < -5%', points: '-10', detail: 'Strong short-term downward momentum' },
      { name: '20d Momentum > 15%', points: '+10', detail: 'Strong medium-term upward trend' },
      { name: '20d Momentum < -15%', points: '-10', detail: 'Strong medium-term downward trend' },
    ],
  },
  {
    title: 'Drawdown',
    description:
      'Rapid price declines measured over 1-3 day windows. These signals fire independently and stack.',
    signals: [
      { name: '1d Drop > 5%', points: '-15', detail: 'Sharp single-day decline' },
      { name: '2d Drop > 7%', points: '-20', detail: 'Rapid 2-day drawdown' },
      { name: '3d Drop > 10%', points: '-15', detail: 'Extended 3-day drawdown' },
      { name: 'Distribution / Heavy Selling', points: '-10', detail: 'Above-average volume on down days (distribution)' },
    ],
  },
  {
    title: 'Reversal',
    description:
      'Fires only when drawdown is active AND RSI < 30 AND price is near support (200d SMA or 52-week low). Selling exhaustion (low down-day volume) adds confidence.',
    signals: [
      {
        name: 'Strong Reversal Setup',
        points: '+20 to +25',
        detail: 'RSI < 20, near support, with selling exhaustion (+25) or without (+20)',
      },
      {
        name: 'Oversold Bounce Setup',
        points: '+15 to +20',
        detail: 'RSI < 30, near support, with exhaustion (+20) or without (+15)',
      },
      { name: 'Oversold After Drop', points: '+5', detail: 'Mild bounce signal when oversold follows a drawdown' },
    ],
  },
  {
    title: '52-Week & Value',
    description:
      'Context-gated: points are halved during active price declines to prevent value-trap signals.',
    signals: [
      { name: 'Near 52-Week High', points: '+5', detail: 'Price within 5% of 52-week high' },
      { name: 'Deep Pullback from High', points: '+5 to +10', detail: '+10 normally, +5 if declining. 20%+ off 52w high' },
      { name: 'Below Consensus PT', points: '+5 to +10', detail: '+10 normally, +5 if declining. 15%+ below avg analyst PT' },
    ],
  },
  {
    title: 'Volume & Short Interest',
    description: 'Volume patterns and short squeeze indicators.',
    signals: [
      { name: 'Volume-Confirmed Rally', points: '+10', detail: 'Up move on above-average volume' },
      { name: 'Volume-Confirmed Selloff', points: '-10', detail: 'Down move on above-average volume' },
      { name: 'Short Squeeze Setup', points: '+15', detail: 'High short interest + rising price + volume' },
      { name: 'High Short Interest', points: '-5', detail: 'Short interest > 15% of float' },
    ],
  },
  {
    title: 'OHLC Candlestick',
    description: 'Intraday price action signals from Open/High/Low/Close data. Silently skip when OHLC unavailable.',
    signals: [
      { name: 'Gap-Down After Up Day', points: '-12', detail: 'Opens >0.5% below prior close after >1% up day — overnight reversal' },
      { name: 'Upper Wick Rejection', points: '-8', detail: 'Up day where >60% of range is above close — selling into strength' },
      { name: 'Closed Near Low', points: '-5', detail: 'Close in bottom 20% of day\'s range — bears dominated' },
      { name: 'Closed Near High', points: '+5', detail: 'Close in top 80% of day\'s range — bulls dominated' },
    ],
  },
  {
    title: 'Relative Strength',
    description: 'Compares stock 5-day momentum vs SPY benchmark. SPY data fetched once per pipeline run.',
    signals: [
      { name: 'Outperforming Market', points: '+10', detail: 'Stock 5d momentum exceeds SPY by >3%' },
      { name: 'Underperforming Market', points: '-10', detail: 'Stock 5d momentum trails SPY by >3%' },
    ],
  },
  {
    title: 'Options Flow',
    description:
      'Unusual activity detected by comparing today\'s volume to 20-day historical average (threshold: 1.5x).',
    signals: [
      { name: 'Unusual Call Volume', points: '+15', detail: 'Call volume > 1.5x 20-day average' },
      { name: 'Unusual Put Volume', points: '-15', detail: 'Put volume > 1.5x 20-day average' },
      { name: 'Bullish OI Skew', points: '+10', detail: 'Put/Call OI ratio < 0.5 — call-heavy positioning' },
      { name: 'Bearish OI Skew', points: '-10', detail: 'Put/Call OI ratio > 1.5 — put-heavy positioning' },
      { name: 'High IV Rank', points: '-10', detail: 'IV rank > 70 — elevated premium risk' },
    ],
  },
  {
    title: 'News Sentiment',
    description:
      'FinBERT (ProsusAI/finbert) scores article sentiment on a -1.0 to 1.0 scale. Points scale with magnitude and article count. Sector tailwind/headwind from sector-level news trends.',
    signals: [
      {
        name: 'Positive News Sentiment',
        points: '+5 to +15',
        detail: 'Base +5, up to +5 for magnitude (beyond 0.3 threshold), +5 for article count (1 per 3 articles)',
      },
      {
        name: 'Negative News Sentiment',
        points: '-5 to -15',
        detail: 'Same scaling as positive, applied negatively',
      },
      { name: 'Sector Tailwind', points: '+10', detail: 'Positive news sentiment across the sector' },
      { name: 'Sector Headwind', points: '-10', detail: 'Negative news sentiment across the sector' },
    ],
  },
];

/* ── Component ──────────────────────────────────────────────────────────── */

function SignalTable({ category }: { category: SignalCategory }) {
  return (
    <div className="mb-6">
      <h3 className="text-base font-semibold text-text-primary mb-1">{category.title}</h3>
      <p className="text-xs text-text-secondary mb-3">{category.description}</p>
      <div className="overflow-hidden rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-border/40">
              <th className="text-left px-3 py-2 text-text-secondary font-medium">Signal</th>
              <th className="text-center px-3 py-2 text-text-secondary font-medium w-28">Points</th>
              <th className="text-left px-3 py-2 text-text-secondary font-medium hidden md:table-cell">Detail</th>
            </tr>
          </thead>
          <tbody>
            {category.signals.map((s, i) => {
              const isPositive = s.points.startsWith('+');
              const isNegative = s.points.startsWith('-');
              return (
                <tr key={i} className="border-t border-border/60 hover:bg-border/20">
                  <td className="px-3 py-2 text-text-primary">{s.name}</td>
                  <td
                    className="px-3 py-2 text-center font-mono font-semibold"
                    style={{
                      color: isPositive ? '#2ea043' : isNegative ? '#f85149' : '#d29922',
                    }}
                  >
                    {s.points}
                  </td>
                  <td className="px-3 py-2 text-text-secondary hidden md:table-cell">{s.detail}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ClassificationSection() {
  const thresholds = [
    { action: 'STRONG_BUY', range: '60 to 100', desc: 'High conviction bullish — multiple strong signals aligned' },
    { action: 'BUY', range: '30 to 59', desc: 'Moderate bullish conviction' },
    { action: 'HOLD', range: '-15 to 29', desc: 'Insufficient directional conviction' },
    { action: 'SELL', range: '-30 to -16', desc: 'Moderate bearish conviction' },
    { action: 'STRONG_SELL', range: '-100 to -31', desc: 'High conviction bearish — multiple strong signals aligned' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Conviction Score Range</h3>
        <p className="text-xs text-text-secondary mb-4">
          Conviction scores range from -100 to +100. They are computed by stacking all applicable signals
          from the categories listed in the Signal Stacking tab. The final score determines the recommendation action.
        </p>
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-border/40">
                <th className="text-left px-3 py-2 text-text-secondary font-medium w-40">Action</th>
                <th className="text-center px-3 py-2 text-text-secondary font-medium w-32">Score Range</th>
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Description</th>
              </tr>
            </thead>
            <tbody>
              {thresholds.map((t) => (
                <tr key={t.action} className="border-t border-border/60">
                  <td className="px-3 py-2">
                    <span
                      className="inline-block px-2 py-0.5 rounded text-xs font-bold"
                      style={{
                        backgroundColor: ACTION_COLORS[t.action] + '22',
                        color: ACTION_COLORS[t.action],
                      }}
                    >
                      {t.action.replace('_', ' ')}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-center font-mono text-text-primary">{t.range}</td>
                  <td className="px-3 py-2 text-text-secondary">{t.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Toxic Ticker Removal</h3>
        <p className="text-sm text-text-secondary">
          Tickers receiving <span className="font-semibold text-red-400">3 or more consecutive</span> SELL
          or STRONG_SELL recommendations are flagged as toxic and automatically removed from the watchlist
          during the next rotation — regardless of their composite score. The recommendation history is
          preserved in the database.
        </p>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Context Gating</h3>
        <p className="text-sm text-text-secondary">
          Certain bullish signals (52-week pullback, below consensus PT) are <span className="font-semibold text-amber-400">context-gated</span>:
          their point values are halved when the stock is actively declining. This prevents value-trap signals
          where a stock looks cheap but is falling for fundamental reasons.
        </p>
      </div>
    </div>
  );
}

function WatchlistSection() {
  const weights = [
    { factor: 'Catalyst Proximity', weight: '25%', desc: 'How close the next earnings date is (T-7 to T+10 window)' },
    { factor: 'Analyst Momentum', weight: '20%', desc: 'Recent rating upgrades/downgrades and PT changes' },
    { factor: 'Recommendation Conviction', weight: '15%', desc: 'Feedback loop: SELL/STRONG_SELL recs deprioritize the stock' },
    { factor: 'Sector Momentum', weight: '15%', desc: 'Relative strength of the stock\'s sector' },
    { factor: 'Options Liquidity', weight: '10%', desc: 'Daily options volume and open interest' },
    { factor: 'Volatility Profile', weight: '5%', desc: 'IV rank and historical volatility' },
    { factor: 'Institutional Flow', weight: '5%', desc: 'Insider buying/selling activity' },
    { factor: 'Price vs Consensus PT', weight: '5%', desc: 'Discount to average analyst price target' },
  ];

  const filters = [
    { filter: 'Market Cap', threshold: '≥ $5B' },
    { filter: 'Avg Daily Volume', threshold: '≥ 2M shares' },
    { filter: 'Options Volume', threshold: '≥ 1,000 contracts/day' },
    { filter: 'Analyst Coverage', threshold: '≥ 5 analysts' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Composite Scoring Weights</h3>
        <p className="text-xs text-text-secondary mb-3">
          Each stock in the universe is scored daily. The top stocks per sector are selected for the active watchlist.
        </p>
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-border/40">
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Factor</th>
                <th className="text-center px-3 py-2 text-text-secondary font-medium w-20">Weight</th>
                <th className="text-left px-3 py-2 text-text-secondary font-medium hidden md:table-cell">Description</th>
              </tr>
            </thead>
            <tbody>
              {weights.map((w) => (
                <tr key={w.factor} className="border-t border-border/60">
                  <td className="px-3 py-2 text-text-primary">{w.factor}</td>
                  <td className="px-3 py-2 text-center font-mono font-semibold text-purple-400">{w.weight}</td>
                  <td className="px-3 py-2 text-text-secondary hidden md:table-cell">{w.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Liquidity Filters</h3>
        <p className="text-xs text-text-secondary mb-3">
          Stocks must pass all filters before scoring. This ensures tradable, liquid names only.
        </p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {filters.map((f) => (
            <div key={f.filter} className="bg-card border border-border rounded-lg p-3 text-center">
              <div className="text-xs text-text-secondary mb-1">{f.filter}</div>
              <div className="text-sm font-semibold text-text-primary">{f.threshold}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="text-xs text-text-secondary mb-1">Max Watchlist Size</div>
          <div className="text-2xl font-bold text-text-primary">30</div>
          <div className="text-xs text-text-secondary mt-1">6 per sector, 5 sectors</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="text-xs text-text-secondary mb-1">Max Daily Changes</div>
          <div className="text-2xl font-bold text-text-primary">5</div>
          <div className="text-xs text-text-secondary mt-1">Limits turnover per rotation</div>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="text-xs text-text-secondary mb-1">Toxic Removal Threshold</div>
          <div className="text-2xl font-bold text-red-400">3 days</div>
          <div className="text-xs text-text-secondary mt-1">Consecutive SELL/STRONG_SELL</div>
        </div>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Manual & Locked Tickers</h3>
        <p className="text-sm text-text-secondary">
          <span className="text-purple-400 font-semibold">Manual</span> tickers are added by hand and are
          not scored — they remain on the watchlist until removed.{' '}
          <span className="text-amber-400 font-semibold">Locked</span> tickers participate in scoring but
          are protected from automatic rotation, ensuring they stay on the watchlist regardless of rank.
        </p>
      </div>
    </div>
  );
}

function StrikeSection() {
  const profiles = [
    {
      name: 'Conservative',
      color: '#2ea043',
      callDelta: '0.50 – 0.80',
      putDelta: '-0.80 – -0.50',
      dte: '7 – 30 days',
      desc: 'ITM/ATM strikes with shorter expiry. Highest probability of profit, lowest leverage. Best for high-conviction directional plays.',
    },
    {
      name: 'Moderate',
      color: '#d29922',
      callDelta: '0.30 – 0.55',
      putDelta: '-0.55 – -0.30',
      dte: '14 – 50 days',
      desc: 'ATM to slightly OTM. Balanced cost vs probability. Good default for most recommendations.',
    },
    {
      name: 'Aggressive',
      color: '#f85149',
      callDelta: '0.10 – 0.35',
      putDelta: '-0.35 – -0.10',
      dte: '21 – 75 days',
      desc: 'OTM strikes with longer expiry. Maximum leverage, lowest probability. For speculative plays or hedging.',
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Risk Profiles</h3>
        <p className="text-xs text-text-secondary mb-4">
          The strike recommender filters options chains by delta range and DTE for each profile.
          All profiles enforce minimum open interest (≥ 10), bid-ask spread (≤ $1.50), and budget constraints.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {profiles.map((p) => (
            <div
              key={p.name}
              className="bg-card border rounded-lg p-4"
              style={{ borderColor: p.color + '60' }}
            >
              <h4 className="font-semibold mb-3" style={{ color: p.color }}>
                {p.name}
              </h4>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-text-secondary">Call Delta</span>
                  <span className="font-mono text-text-primary">{p.callDelta}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-secondary">Put Delta</span>
                  <span className="font-mono text-text-primary">{p.putDelta}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-text-secondary">DTE Range</span>
                  <span className="font-mono text-text-primary">{p.dte}</span>
                </div>
              </div>
              <p className="text-xs text-text-secondary mt-3">{p.desc}</p>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Delta Estimation</h3>
        <p className="text-sm text-text-secondary">
          Delta is estimated from moneyness (strike vs stock price) using a 3x slope approximation.
          This avoids the need for a full Black-Scholes computation while still providing reasonable
          filtering for strike selection. Exact delta values from the broker may differ.
        </p>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Contract Filters</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <div className="text-xs text-text-secondary mb-1">Min Open Interest</div>
            <div className="text-sm font-semibold text-text-primary">≥ 10</div>
          </div>
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <div className="text-xs text-text-secondary mb-1">Max Bid-Ask Spread</div>
            <div className="text-sm font-semibold text-text-primary">≤ $1.50</div>
          </div>
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <div className="text-xs text-text-secondary mb-1">Budget Filter</div>
            <div className="text-sm font-semibold text-text-primary">premium × 100 ≤ budget</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function PipelineSection() {
  const phases = [
    {
      name: 'Discovery',
      time: '05:00 ET',
      desc: 'Scans FMP market lists (most active, gainers, losers) and news-trending tickers. Surfaces candidates for user approval.',
    },
    {
      name: 'Watchlist',
      time: '05:30 ET',
      desc: 'Scores universe stocks by composite score, rotates the active watchlist (max 5 changes). Snapshot saved for historical viewing.',
    },
    {
      name: 'Ratings',
      time: '06:00 ET',
      desc: 'Scans for analyst rating changes since prior close. Classifies as TIER_CHANGE, PT_CHANGE, INITIATION, or REITERATION.',
      parallel: true,
    },
    {
      name: 'Earnings',
      time: '07:00 ET',
      desc: 'Refreshes earnings calendar with EPS estimates, manages catalyst windows (T-7 to T+10).',
      parallel: true,
    },
    {
      name: 'News',
      time: '06:30 ET',
      desc: 'Fetches and categorizes news. Sentiment scored via FinBERT. Tracks per-ticker relevance via junction table.',
      parallel: true,
    },
    {
      name: 'Options',
      time: '09:35 ET',
      desc: 'Pulls options chains via yfinance. Calculates IV rank, detects unusual volume activity.',
      parallel: true,
    },
    {
      name: 'Recommendations',
      time: '10:30 ET',
      desc: 'Stacks all signals into conviction scores. Generates rationale, selects contracts, classifies action.',
    },
  ];

  const dataSources = [
    { source: 'FMP (Financial Modeling Prep)', usage: 'Analyst ratings (primary), earnings with EPS, insider trades, news, discovery lists', limit: '300 req/min' },
    { source: 'Finnhub', usage: 'News (primary), analyst ratings (fallback)', limit: '60 calls/min (free)' },
    { source: 'yfinance', usage: 'Prices, fundamentals, options chains, technicals', limit: '~2 req/sec (rate limited)' },
    { source: 'NewsAPI', usage: 'News (last fallback)', limit: '100 calls/day (free)' },
    { source: 'FinBERT', usage: 'Sentiment scoring (ProsusAI/finbert, runs locally on CPU)', limit: 'No external limit' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Daily Pipeline Schedule</h3>
        <p className="text-xs text-text-secondary mb-3">
          Runs Mon-Fri on US Eastern time. Phases marked with <span className="text-blue-400">parallel</span> run
          concurrently via asyncio.gather for faster execution.
        </p>
        <div className="space-y-2">
          {phases.map((p) => (
            <div
              key={p.name}
              className="flex items-start gap-3 bg-card border border-border rounded-lg px-4 py-3"
            >
              <div className="w-20 shrink-0">
                <span className="font-mono text-xs text-text-secondary">{p.time}</span>
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-text-primary">{p.name}</span>
                  {p.parallel && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/40 text-blue-400 font-medium">
                      parallel
                    </span>
                  )}
                </div>
                <p className="text-xs text-text-secondary mt-0.5">{p.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Intraday Updates</h3>
        <p className="text-sm text-text-secondary">
          Beyond the morning pipeline, several phases repeat throughout the day: Ratings at 11:00 and 14:00,
          News at 10:00 and 16:45, Options at 16:15, Earnings at 17:00, and Recommendations at 16:30.
          The full pipeline can also be triggered manually from the dashboard.
        </p>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Data Sources</h3>
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-border/40">
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Source</th>
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Usage</th>
                <th className="text-left px-3 py-2 text-text-secondary font-medium w-36 hidden md:table-cell">Rate Limit</th>
              </tr>
            </thead>
            <tbody>
              {dataSources.map((d) => (
                <tr key={d.source} className="border-t border-border/60">
                  <td className="px-3 py-2 text-text-primary font-medium whitespace-nowrap">{d.source}</td>
                  <td className="px-3 py-2 text-text-secondary">{d.usage}</td>
                  <td className="px-3 py-2 text-text-secondary font-mono text-xs hidden md:table-cell">{d.limit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Idempotency</h3>
        <p className="text-sm text-text-secondary">
          Pipeline re-runs on the same day are safe. Watchlist rotation deletes existing snapshots for the date
          before inserting new ones. Recommendations use a unique constraint per date + ticker — re-runs update
          existing rows. Manual and locked tickers are always preserved.
        </p>
      </div>
    </div>
  );
}

/* ── Trading Guide ──────────────────────────────────────────────────────── */

function GuideCard({ title, children, accent = '#58a6ff' }: { title: string; children: React.ReactNode; accent?: string }) {
  return (
    <div className="bg-card border border-border rounded-lg p-5" style={{ borderLeftColor: accent, borderLeftWidth: 3 }}>
      <h4 className="text-sm font-semibold text-text-primary mb-3">{title}</h4>
      {children}
    </div>
  );
}

function TradingGuideSection() {
  return (
    <div className="space-y-8">
      {/* ── Morning Routine ──────────────────────────────────────── */}
      <div>
        <h3 className="text-lg font-bold text-text-primary mb-1">Daily Workflow</h3>
        <p className="text-xs text-text-secondary mb-4">
          EdgeFlow's pipeline runs automatically before market open. Here's how to use its output for weekly options.
        </p>
        <div className="space-y-3">
          <GuideCard title="1. Check Pipeline Status (Pre-Market)" accent="#58a6ff">
            <p className="text-sm text-text-secondary">
              The pipeline finishes by ~10:30 ET. Open the Dashboard and verify the status bar shows green dots
              for DB and Scheduler. Check the "Updated" timestamp — if stale, hit Refresh to trigger a manual run.
            </p>
          </GuideCard>

          <GuideCard title="2. Review Watchlist Changes" accent="#58a6ff">
            <p className="text-sm text-text-secondary">
              Look at the green (NEW_ENTRANT) and red (REMOVED) badges at the top. New entrants just scored
              high enough to join — they often have fresh catalysts. Removed tickers may have deteriorating
              signals. Don't chase removed tickers; focus attention on new entrants and existing holdings.
            </p>
          </GuideCard>

          <GuideCard title="3. Scan Recommendations" accent="#58a6ff">
            <div className="text-sm text-text-secondary space-y-2">
              <p>
                Sort mentally by conviction score. For weekly options, focus on:
              </p>
              <ul className="list-disc list-inside space-y-1 ml-2">
                <li><span className="font-semibold" style={{ color: '#2ea043' }}>STRONG_BUY (60+)</span> — Primary trade candidates. Multiple signals aligned.</li>
                <li><span className="font-semibold" style={{ color: '#56d364' }}>BUY (30-59)</span> — Secondary candidates. May need confirmation from Day-by-Day trend.</li>
                <li><span className="font-semibold" style={{ color: '#f85149' }}>STRONG_SELL (≤-31)</span> — Put candidates if you trade bearish.</li>
              </ul>
              <p>
                Ignore HOLD — insufficient edge for weekly options where theta decay is aggressive.
              </p>
            </div>
          </GuideCard>

          <GuideCard title="4. Use Day-by-Day for Confirmation" accent="#58a6ff">
            <div className="text-sm text-text-secondary space-y-2">
              <p>
                Click a ticker to open the detail panel. The Day-by-Day view shows conviction trajectory.
                Look for:
              </p>
              <ul className="list-disc list-inside space-y-1 ml-2">
                <li><span className="font-semibold text-green-400">Rising conviction</span> — Score increasing over 2-3 days. Strong entry signal.</li>
                <li><span className="font-semibold text-green-400">Stable high conviction</span> — Consistently 50+. Reliable for weeklies.</li>
                <li><span className="font-semibold text-amber-400">Spike from nowhere</span> — Jumped from HOLD to STRONG_BUY in one day. Could be a catalyst event — verify with the signal list before entering.</li>
                <li><span className="font-semibold text-red-400">Declining conviction</span> — Score dropping day over day. Avoid even if still BUY.</li>
              </ul>
            </div>
          </GuideCard>
        </div>
      </div>

      {/* ── Reading the Signals ──────────────────────────────────── */}
      <div>
        <h3 className="text-lg font-bold text-text-primary mb-1">Signal Quality for Weeklies</h3>
        <p className="text-xs text-text-secondary mb-4">
          Not all signals are equal for short-dated positions. Weekly options need catalysts that move price <span className="font-semibold text-text-primary">this week</span>.
        </p>
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-border/40">
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Signal Type</th>
                <th className="text-center px-3 py-2 text-text-secondary font-medium w-24">Weekly Value</th>
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Why</th>
              </tr>
            </thead>
            <tbody>
              {[
                { signal: 'T1/T2 Upgrade', value: 'Excellent', color: '#2ea043', why: 'Institutional repricing happens within 1-3 days. Price gap + follow-through.' },
                { signal: 'Earnings Beat + Raised', value: 'Excellent', color: '#2ea043', why: 'Post-earnings drift is strongest in the first 5 trading days.' },
                { signal: 'Unusual Call Volume', value: 'Excellent', color: '#2ea043', why: 'Smart money positioning before a known catalyst. Acts fast.' },
                { signal: 'Reversal Setup', value: 'Good', color: '#56d364', why: 'Oversold bounces are violent and fast — but need RSI + support confirmation.' },
                { signal: 'Short Squeeze Setup', value: 'Good', color: '#56d364', why: 'Squeezes are explosive. High reward but timing is unpredictable.' },
                { signal: 'Positive News Sentiment', value: 'Moderate', color: '#d29922', why: 'News drives 1-2 day moves but fades. Only strong if sentiment > 0.5.' },
                { signal: 'Above 50d SMA', value: 'Moderate', color: '#d29922', why: 'Trend confirmation, but doesn\'t create urgency. Better as a filter.' },
                { signal: 'Near 52-Week High', value: 'Low', color: '#f85149', why: 'Breakout potential exists but moves are small. Poor risk/reward for weeklies.' },
                { signal: 'Below Consensus PT', value: 'Low', color: '#f85149', why: 'Value convergence takes weeks/months. Not a weekly catalyst.' },
                { signal: 'Sector Tailwind', value: 'Low', color: '#f85149', why: 'Diffuse. Sector rotation is a multi-week theme, not a weekly trigger.' },
              ].map((row) => (
                <tr key={row.signal} className="border-t border-border/60">
                  <td className="px-3 py-2 text-text-primary">{row.signal}</td>
                  <td className="px-3 py-2 text-center font-semibold text-xs" style={{ color: row.color }}>
                    {row.value}
                  </td>
                  <td className="px-3 py-2 text-text-secondary">{row.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Scenario Playbooks ───────────────────────────────────── */}
      <div>
        <h3 className="text-lg font-bold text-text-primary mb-1">Scenario Playbooks</h3>
        <p className="text-xs text-text-secondary mb-4">
          Match EdgeFlow's output to the right options strategy. Each scenario maps a signal + IV combination to a specific play.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Playbook 1 */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: '#2ea04322', color: '#2ea043' }}>
                STRONG BUY
              </span>
              <span className="px-2 py-0.5 rounded text-xs font-bold bg-green-900/30 text-green-400">
                LOW IV
              </span>
              <span className="text-[10px] text-green-400 font-medium">RISK: LOW</span>
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Buy Calls (Directional)</h4>
            <div className="text-xs text-text-secondary space-y-1.5">
              <p><span className="font-semibold text-text-primary">Strategy:</span> Buy ATM or slightly OTM calls. This is the highest-conviction, lowest-risk setup — strong signals with cheap premium.</p>
              <p><span className="font-semibold text-text-primary">Strike:</span> Use the Conservative or Moderate profile from Strike Recommender. Delta 0.40-0.60 for weeklies.</p>
              <p><span className="font-semibold text-text-primary">DTE:</span> 5-12 days. Current week or next Friday expiry.</p>
              <p><span className="font-semibold text-text-primary">Position size:</span> Up to 3-5% of portfolio per trade.</p>
              <p><span className="font-semibold text-text-primary">Exit:</span> Take profit at 50-100% gain. Stop loss at 40-50% of premium paid. Exit by Wednesday if no movement.</p>
            </div>
          </div>

          {/* Playbook 2 */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: '#2ea04322', color: '#2ea043' }}>
                STRONG BUY
              </span>
              <span className="px-2 py-0.5 rounded text-xs font-bold bg-red-900/30 text-red-400">
                HIGH IV
              </span>
              <span className="text-[10px] text-amber-400 font-medium">RISK: MEDIUM</span>
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Bull Call Spread (Defined Risk)</h4>
            <div className="text-xs text-text-secondary space-y-1.5">
              <p><span className="font-semibold text-text-primary">Strategy:</span> Buy ATM call, sell OTM call 3-5% above. High IV makes naked calls expensive — the spread neutralizes vega so IV crush doesn't hurt you.</p>
              <p><span className="font-semibold text-text-primary">Width:</span> $2-5 wide on stocks under $150, $5-10 on higher-priced names. Wider = more profit potential but costs more.</p>
              <p><span className="font-semibold text-text-primary">DTE:</span> 7-14 days. Gives time for the move without excessive theta bleed.</p>
              <p><span className="font-semibold text-text-primary">Max risk:</span> Net debit paid (difference between premiums). Target 2:1 reward-to-risk.</p>
              <p><span className="font-semibold text-text-primary">Exit:</span> Close at 50-70% of max profit. Don't hold to expiry — gamma risk accelerates.</p>
            </div>
          </div>

          {/* Playbook 3 */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: '#56d36422', color: '#56d364' }}>
                BUY
              </span>
              <span className="px-2 py-0.5 rounded text-xs font-bold bg-green-900/30 text-green-400">
                LOW IV
              </span>
              <span className="text-[10px] text-amber-400 font-medium">RISK: MEDIUM</span>
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Buy Calls (Smaller Size)</h4>
            <div className="text-xs text-text-secondary space-y-1.5">
              <p><span className="font-semibold text-text-primary">Strategy:</span> Same as directional calls but with reduced conviction. Signals are aligned but not overwhelming — one headwind could flip it.</p>
              <p><span className="font-semibold text-text-primary">Strike:</span> ATM only (delta 0.45-0.55). Don't go OTM with moderate conviction — you need the move to be smaller to profit.</p>
              <p><span className="font-semibold text-text-primary">Position size:</span> 1-2% of portfolio. Half the size of a STRONG_BUY play.</p>
              <p><span className="font-semibold text-text-primary">Confirmation:</span> Check Day-by-Day — only enter if conviction is rising or stable. Skip if conviction dropped today.</p>
              <p><span className="font-semibold text-text-primary">Exit:</span> Take profit at 30-50% gain. Tighter stop at 30% loss. Exit by Tuesday if flat.</p>
            </div>
          </div>

          {/* Playbook 4 */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: '#56d36422', color: '#56d364' }}>
                BUY
              </span>
              <span className="px-2 py-0.5 rounded text-xs font-bold bg-red-900/30 text-red-400">
                HIGH IV
              </span>
              <span className="text-[10px] text-red-400 font-medium">RISK: HIGH</span>
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Cash-Secured Put / Skip</h4>
            <div className="text-xs text-text-secondary space-y-1.5">
              <p><span className="font-semibold text-text-primary">Strategy:</span> Moderate conviction + expensive premiums = poor setup for buying options. Consider selling instead: sell a cash-secured put below support to collect inflated premium. If you don't sell puts, skip this trade.</p>
              <p><span className="font-semibold text-text-primary">Strike:</span> Sell put at 5-7% below current price (OTM). You want the stock but at a discount.</p>
              <p><span className="font-semibold text-text-primary">DTE:</span> Current week. Maximum theta decay works in your favor.</p>
              <p><span className="font-semibold text-text-primary">Requirement:</span> Must have cash/margin to cover assignment. Only on stocks you'd own at that price.</p>
              <p><span className="font-semibold text-text-primary">Exit:</span> Let expire worthless for full premium, or buy back at 80% profit.</p>
            </div>
          </div>

          {/* Playbook 5 */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: '#da363322', color: '#da3633' }}>
                STRONG SELL
              </span>
              <span className="px-2 py-0.5 rounded text-xs font-bold bg-green-900/30 text-green-400">
                LOW IV
              </span>
              <span className="text-[10px] text-amber-400 font-medium">RISK: MEDIUM</span>
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Buy Puts (Bearish Directional)</h4>
            <div className="text-xs text-text-secondary space-y-1.5">
              <p><span className="font-semibold text-text-primary">Strategy:</span> Strong bearish conviction with cheap puts. Mirror of the bullish playbook. Works best when drawdown signals are stacking.</p>
              <p><span className="font-semibold text-text-primary">Strike:</span> ATM or slightly OTM puts (delta -0.40 to -0.55). Use Moderate profile from Strike Recommender.</p>
              <p><span className="font-semibold text-text-primary">Best when:</span> Multiple drawdown signals active, negative news sentiment, unusual put volume, and the stock is below both 50d and 200d SMA.</p>
              <p><span className="font-semibold text-text-primary">Caution:</span> Check for reversal signals. If "Oversold Bounce Setup" or "Strong Reversal Setup" is also firing, the stock may be bottoming — skip.</p>
              <p><span className="font-semibold text-text-primary">Exit:</span> Take profit at 50-80% gain. Stop at 40% loss.</p>
            </div>
          </div>

          {/* Playbook 6 */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: '#da363322', color: '#da3633' }}>
                STRONG SELL
              </span>
              <span className="px-2 py-0.5 rounded text-xs font-bold bg-red-900/30 text-red-400">
                HIGH IV
              </span>
              <span className="text-[10px] text-red-400 font-medium">RISK: HIGH</span>
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Bear Put Spread / Call Credit Spread</h4>
            <div className="text-xs text-text-secondary space-y-1.5">
              <p><span className="font-semibold text-text-primary">Strategy:</span> Bearish conviction but IV is elevated — naked puts are overpriced. Use a bear put spread (buy ATM put, sell lower put) or a call credit spread (sell ATM call, buy higher call) to collect premium while IV is rich.</p>
              <p><span className="font-semibold text-text-primary">Advantage:</span> Credit spreads profit from both the move AND IV crush. If the stock stays flat or drops, you win.</p>
              <p><span className="font-semibold text-text-primary">Width:</span> $2-5 wide. Max loss = width minus credit received.</p>
              <p><span className="font-semibold text-text-primary">DTE:</span> 7-14 days. Let theta work for you on the credit side.</p>
              <p><span className="font-semibold text-text-primary">Exit:</span> Buy back at 50% of max profit. Never hold credit spreads to expiry — assignment risk.</p>
            </div>
          </div>

          {/* Playbook 7 — Reversal */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold bg-purple-900/30 text-purple-400">
                REVERSAL SETUP
              </span>
              <span className="text-[10px] text-text-secondary font-medium">Any IV</span>
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Oversold Bounce Play</h4>
            <div className="text-xs text-text-secondary space-y-1.5">
              <p><span className="font-semibold text-text-primary">Trigger:</span> "Strong Reversal Setup" or "Oversold Bounce Setup" signal active. RSI &lt; 30, price near 200d SMA or 52-week low, selling exhaustion.</p>
              <p><span className="font-semibold text-text-primary">Strategy:</span> Buy slightly OTM calls (delta 0.30-0.40) for 10-14 DTE. The bounce usually happens within 2-3 days but needs time to develop. IV is usually elevated after a crash, so use a call debit spread if IV rank &gt; 60.</p>
              <p><span className="font-semibold text-text-primary">Size:</span> 1-2% of portfolio. Reversal plays are high-reward but binary — the stock either bounces or keeps falling.</p>
              <p><span className="font-semibold text-text-primary">Confirmation:</span> Wait for a green day with volume. Don't front-run the bounce — let the stock prove it's turning.</p>
              <p><span className="font-semibold text-text-primary">Exit:</span> Take profit at 80-150% gain (bounces are fast). Hard stop at 50% loss.</p>
            </div>
          </div>

          {/* Playbook 8 — Earnings */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded text-xs font-bold bg-amber-900/30 text-amber-400">
                EARNINGS CATALYST
              </span>
              <span className="text-[10px] text-text-secondary font-medium">Pre-Earnings</span>
            </div>
            <h4 className="text-sm font-semibold text-text-primary">Earnings Run-Up / Straddle</h4>
            <div className="text-xs text-text-secondary space-y-1.5">
              <p><span className="font-semibold text-text-primary">Trigger:</span> "Active Catalyst Window" signal firing, earnings date in 1-5 days. Check the Catalyst Calendar for exact date and time (BMO/AMC).</p>
              <p><span className="font-semibold text-text-primary">Run-up play:</span> If conviction is BUY or higher AND the stock hasn't run up yet, buy calls 5-7 DTE. Sell BEFORE earnings — don't hold through the event. You're playing the anticipation, not the result.</p>
              <p><span className="font-semibold text-text-primary">Straddle play:</span> If you want to play the earnings event itself, buy a straddle (ATM call + ATM put) for the weekly expiry right after earnings. You need a big move in either direction to overcome the IV crush. Only do this if historical earnings moves for the ticker are &gt; the implied move (check options flow section for current IV rank).</p>
              <p><span className="font-semibold text-text-primary">Caution:</span> IV peaks right before earnings. Buying options pre-earnings means paying maximum premium. The stock must move more than the "expected move" priced into options for you to profit.</p>
            </div>
          </div>
        </div>
      </div>

      {/* ── Entry Strategies ────────────────────────────────────── */}
      <div>
        <h3 className="text-lg font-bold text-text-primary mb-1">Entry Strategies</h3>
        <p className="text-xs text-text-secondary mb-4">
          Each recommendation includes an entry strategy that tells you <span className="font-semibold text-text-primary">when and how</span> to enter,
          not just <span className="font-semibold text-text-primary">what</span> to trade. The strategy is determined by conviction strength, whether the stock has already moved, and earnings proximity.
        </p>

        <div className="overflow-hidden rounded-lg border border-border mb-6">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-border/40">
                <th className="text-left px-3 py-2 text-text-secondary font-medium w-36">Strategy</th>
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Condition</th>
                <th className="text-left px-3 py-2 text-text-secondary font-medium">What It Means</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2">
                  <span className="px-2 py-0.5 rounded text-xs font-bold bg-blue-900/40 text-blue-400">PRE_POSITION</span>
                </td>
                <td className="px-3 py-2 text-text-secondary">Conviction &ge; 30 AND inside an active earnings catalyst window (T-7 to T+10)</td>
                <td className="px-3 py-2 text-text-secondary">Get in before the catalyst fires. The earnings event hasn't happened yet — you're positioning for the anticipated move.</td>
              </tr>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2">
                  <span className="px-2 py-0.5 rounded text-xs font-bold bg-amber-900/40 text-amber-400">REACTIVE</span>
                </td>
                <td className="px-3 py-2 text-text-secondary">Conviction &ge; 30 AND stock already moved &gt; 3%</td>
                <td className="px-3 py-2 text-text-secondary">The catalyst already triggered a move — you'd be chasing. Entry is still valid but use smaller size and tighter stops.</td>
              </tr>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2">
                  <span className="px-2 py-0.5 rounded text-xs font-bold bg-purple-900/40 text-purple-400">WAIT</span>
                </td>
                <td className="px-3 py-2 text-text-secondary">Conviction 15-29, or conviction &ge; 30 but stock hasn't moved yet</td>
                <td className="px-3 py-2 text-text-secondary">Signals are positive but need confirmation — a pullback entry, volume breakout, or another signal stacking before committing.</td>
              </tr>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2">
                  <span className="px-2 py-0.5 rounded text-xs font-bold bg-zinc-800 text-text-secondary">HOLD</span>
                </td>
                <td className="px-3 py-2 text-text-secondary">Conviction &lt; 15</td>
                <td className="px-3 py-2 text-text-secondary">Not actionable. Mixed or weak signals — no edge for entry. Sit tight or look elsewhere.</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GuideCard title="How to Use Entry Strategies" accent="#58a6ff">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-text-primary">PRE_POSITION:</span> Set your limit orders the night before or pre-market. Queue them to fire at the open. Best entry for weeklies — you're ahead of the catalyst.</p>
              <p><span className="font-semibold text-text-primary">REACTIVE:</span> Don't market-order at the open — spreads are wide in the first 10 minutes. Wait until 9:45-10:00 AM for spreads to tighten. Size down to 50-75% of normal. Use tighter stops (30% vs 50%).</p>
              <p><span className="font-semibold text-text-primary">WAIT:</span> Add to your watchlist and monitor. Enter only when conviction rises above 30 on a subsequent day, or when a confirming signal fires (e.g., unusual call volume, T1 upgrade). Don't force it.</p>
              <p><span className="font-semibold text-text-primary">HOLD:</span> No action. Check back tomorrow — conviction may shift as new data arrives.</p>
            </div>
          </GuideCard>

          <GuideCard title="Entry Strategy + Action Matrix" accent="#58a6ff">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-green-400">STRONG_BUY + PRE_POSITION</span> — Best possible setup. High conviction before an earnings catalyst. Enter aggressively with full size.</p>
              <p><span className="font-semibold text-green-400">STRONG_BUY + REACTIVE</span> — Strong signals but the stock has moved. Still tradeable — use spreads or smaller size to manage the chase risk.</p>
              <p><span className="font-semibold text-green-400">BUY + WAIT</span> — Moderate conviction, stock is flat. The signal exists but isn't screaming. Wait for confirmation or a pullback to support before entering.</p>
              <p><span className="font-semibold text-amber-400">BUY + REACTIVE</span> — Moderate conviction and already moved. Worst risk/reward of the actionable setups. Consider skipping unless Day-by-Day shows rising trend.</p>
              <p><span className="font-semibold text-red-400">SELL/STRONG_SELL + HOLD</span> — Bearish but not actionable for entry. Only trade these if you actively play puts and conviction is below -30.</p>
            </div>
          </GuideCard>
        </div>
      </div>

      {/* ── Risk Management ──────────────────────────────────────── */}
      <div>
        <h3 className="text-lg font-bold text-text-primary mb-1">Risk Management Rules</h3>
        <p className="text-xs text-text-secondary mb-4">
          Weekly options are high-leverage instruments. These rules keep drawdowns survivable.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GuideCard title="Position Sizing" accent="#d29922">
            <div className="text-xs text-text-secondary space-y-2">
              <div className="overflow-hidden rounded border border-border">
                <table className="w-full text-xs">
                  <thead><tr className="bg-border/40">
                    <th className="px-2 py-1.5 text-left text-text-secondary font-medium">Risk Level</th>
                    <th className="px-2 py-1.5 text-center text-text-secondary font-medium">Max Per Trade</th>
                    <th className="px-2 py-1.5 text-center text-text-secondary font-medium">Max Open</th>
                  </tr></thead>
                  <tbody>
                    <tr className="border-t border-border/60">
                      <td className="px-2 py-1.5 text-green-400 font-medium">LOW</td>
                      <td className="px-2 py-1.5 text-center text-text-primary">3-5%</td>
                      <td className="px-2 py-1.5 text-center text-text-primary">15-20%</td>
                    </tr>
                    <tr className="border-t border-border/60">
                      <td className="px-2 py-1.5 text-amber-400 font-medium">MEDIUM</td>
                      <td className="px-2 py-1.5 text-center text-text-primary">1-2%</td>
                      <td className="px-2 py-1.5 text-center text-text-primary">8-10%</td>
                    </tr>
                    <tr className="border-t border-border/60">
                      <td className="px-2 py-1.5 text-red-400 font-medium">HIGH</td>
                      <td className="px-2 py-1.5 text-center text-text-primary">0.5-1%</td>
                      <td className="px-2 py-1.5 text-center text-text-primary">3-5%</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p>"Max Open" = total portfolio allocation in that risk category at once. Never have more than 25% of portfolio in weekly options total.</p>
            </div>
          </GuideCard>

          <GuideCard title="The Greeks for Weeklies" accent="#d29922">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-text-primary">Theta (time decay):</span> Accelerates dramatically in the last 5 days. Monday calls lose ~20% of remaining extrinsic value per day. Enter early in the week, exit by Wednesday/Thursday unless momentum is strong.</p>
              <p><span className="font-semibold text-text-primary">Delta (directional exposure):</span> Stay 0.35-0.60 for weeklies. Below 0.30, you need a huge move. The Strike Recommender's Moderate profile is calibrated for this.</p>
              <p><span className="font-semibold text-text-primary">Gamma (delta acceleration):</span> Extremely high for ATM weeklies. Works for you when the stock moves in your direction, violently against you when it reverses. This is why stop losses matter.</p>
              <p><span className="font-semibold text-text-primary">Vega (IV sensitivity):</span> Check IV rank in the ticker detail panel. If IV rank &gt; 60, use spreads. If &lt; 40, naked calls/puts are fine.</p>
            </div>
          </GuideCard>

          <GuideCard title="Stop Loss & Profit Taking" accent="#d29922">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-text-primary">Hard stop:</span> Exit at 40-50% loss of premium. Weekly options can go to zero — don't "average down" on weeklies. Ever.</p>
              <p><span className="font-semibold text-text-primary">Time stop:</span> If the position is flat by Wednesday and you hold Friday expiry, close. Theta will eat the rest. Roll to next week if conviction is still high.</p>
              <p><span className="font-semibold text-text-primary">Profit taking:</span> Scale out. Sell half at 50% gain, let the rest run with a trailing stop. On a 100%+ gain, sell 75% and let a small piece ride.</p>
              <p><span className="font-semibold text-text-primary">Never hold to expiry.</span> Close or roll by Thursday afternoon. Gamma risk at expiry creates unpredictable P&L swings.</p>
            </div>
          </GuideCard>

          <GuideCard title="When NOT to Trade" accent="#f85149">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-text-primary">HOLD recommendations:</span> No edge. The system sees mixed signals. Wait.</p>
              <p><span className="font-semibold text-text-primary">Declining conviction trend:</span> Even if today is BUY, a 3-day downtrend in conviction means the setup is deteriorating.</p>
              <p><span className="font-semibold text-text-primary">Friday entries:</span> Never buy weekly options on Friday. You pay for 2 days of theta (weekend) with zero trading time. Enter Monday or Tuesday.</p>
              <p><span className="font-semibold text-text-primary">Major macro events:</span> FOMC days, CPI releases, jobs reports. These override individual stock signals. Sit out or hedge with index puts.</p>
              <p><span className="font-semibold text-text-primary">IV Rank &gt; 80 (single-name):</span> Premium is extremely expensive. Even correct directional bets can lose money from IV crush.</p>
            </div>
          </GuideCard>
        </div>
      </div>

      {/* ── Using EdgeFlow Features ──────────────────────────────── */}
      <div>
        <h3 className="text-lg font-bold text-text-primary mb-1">Feature Usage for Trading</h3>
        <p className="text-xs text-text-secondary mb-4">
          How each dashboard feature maps to a trading decision.
        </p>
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-border/40">
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Feature</th>
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Trading Use</th>
              </tr>
            </thead>
            <tbody>
              {[
                { feature: 'Recommendation Cards', use: 'Trade selection. Filter by STRONG_BUY/STRONG_SELL for weeklies. Read the signal bullets — they tell you WHY.' },
                { feature: 'Day-by-Day View', use: 'Entry timing. Rising conviction = enter. Stable = hold. Declining = exit or skip.' },
                { feature: 'Strike Recommender', use: 'Contract selection. Use the budget filter to find affordable contracts. Start with Moderate profile for weeklies.' },
                { feature: 'Strike Scanner', use: 'Batch scanning. Run "Scan Watchlist" to see strike recommendations for all tickers at once. Saves time vs clicking each one.' },
                { feature: 'Options Flow (Ticker Detail)', use: 'IV assessment. Check IV Rank before entering. High IV → use spreads. Also check Put/Call ratio for sentiment confirmation.' },
                { feature: 'Trend Charts', use: 'Visual confirmation. Price/conviction chart shows the relationship between price action and signal quality. Divergences are informative.' },
                { feature: 'News Timeline', use: 'Catalyst verification. When a stock jumps to STRONG_BUY, check the news for the driver. Event-driven signals are the highest-quality for weeklies.' },
                { feature: 'Catalyst Calendar', use: 'Earnings avoidance/targeting. Know when earnings are. Either play the run-up and exit before, or use straddles if you want to play the event.' },
                { feature: 'Research Page', use: 'Off-watchlist analysis. Test any ticker before committing capital. Good for stocks from your own screening or tips.' },
              ].map((row) => (
                <tr key={row.feature} className="border-t border-border/60">
                  <td className="px-3 py-2 text-text-primary font-medium whitespace-nowrap">{row.feature}</td>
                  <td className="px-3 py-2 text-text-secondary">{row.use}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Quick Reference ──────────────────────────────────────── */}
      <div>
        <h3 className="text-lg font-bold text-text-primary mb-1">Quick Decision Matrix</h3>
        <p className="text-xs text-text-secondary mb-4">
          Fast reference for the most common scenarios.
        </p>
        <div className="overflow-hidden rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-border/40">
                <th className="text-left px-3 py-2 text-text-secondary font-medium">Action</th>
                <th className="text-center px-3 py-2 text-text-secondary font-medium">IV Low (&lt;40)</th>
                <th className="text-center px-3 py-2 text-text-secondary font-medium">IV Mid (40-65)</th>
                <th className="text-center px-3 py-2 text-text-secondary font-medium">IV High (&gt;65)</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2 font-semibold" style={{ color: '#2ea043' }}>STRONG BUY</td>
                <td className="px-3 py-2 text-center text-text-primary">Buy Calls</td>
                <td className="px-3 py-2 text-center text-text-primary">Buy Calls (smaller)</td>
                <td className="px-3 py-2 text-center text-text-primary">Bull Call Spread</td>
              </tr>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2 font-semibold" style={{ color: '#56d364' }}>BUY</td>
                <td className="px-3 py-2 text-center text-text-primary">Buy Calls (small)</td>
                <td className="px-3 py-2 text-center text-amber-400">Sell CSP or Skip</td>
                <td className="px-3 py-2 text-center text-red-400">Skip</td>
              </tr>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2 font-semibold" style={{ color: '#d29922' }}>HOLD</td>
                <td className="px-3 py-2 text-center text-red-400">No trade</td>
                <td className="px-3 py-2 text-center text-red-400">No trade</td>
                <td className="px-3 py-2 text-center text-red-400">No trade</td>
              </tr>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2 font-semibold" style={{ color: '#f85149' }}>SELL</td>
                <td className="px-3 py-2 text-center text-amber-400">Buy Puts (small) or Skip</td>
                <td className="px-3 py-2 text-center text-red-400">Skip</td>
                <td className="px-3 py-2 text-center text-red-400">Skip</td>
              </tr>
              <tr className="border-t border-border/60">
                <td className="px-3 py-2 font-semibold" style={{ color: '#da3633' }}>STRONG SELL</td>
                <td className="px-3 py-2 text-center text-text-primary">Buy Puts</td>
                <td className="px-3 py-2 text-center text-text-primary">Bear Put Spread</td>
                <td className="px-3 py-2 text-center text-text-primary">Call Credit Spread</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="text-[10px] text-text-secondary mt-2">
          CSP = Cash-Secured Put. "Skip" means the risk/reward doesn't justify a weekly options position.
          All strategies assume delta-neutral IV management — use spreads when IV is elevated to avoid paying inflated premium.
        </p>
      </div>
    </div>
  );
}

export default function KnowledgePage() {
  const [activeTab, setActiveTab] = useState<Section>('guide');

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-5xl mx-auto px-4 py-8">
        <h1 className="text-2xl font-bold mb-1">Knowledge Base</h1>
        <p className="text-sm text-text-secondary mb-6">
          Reference guide for EdgeFlow's signal stacking, scoring, and pipeline mechanics.
        </p>

        {/* Tab bar */}
        <div className="flex gap-1 border-b border-border mb-6 overflow-x-auto">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors border-b-2 ${
                activeTab === tab.key
                  ? 'border-purple-500 text-text-primary'
                  : 'border-transparent text-text-secondary hover:text-text-primary'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Content */}
        {activeTab === 'guide' && <TradingGuideSection />}
        {activeTab === 'signals' && (
          <div>
            <p className="text-sm text-text-secondary mb-6">
              Conviction scores are built by stacking signals from all categories below.
              Each signal adds or subtracts points. The total determines the recommendation action.
              Signals from different categories can stack — a stock can receive points from analyst
              upgrades, positive earnings, bullish options flow, and strong technicals simultaneously.
            </p>
            {SIGNAL_CATEGORIES.map((cat) => (
              <SignalTable key={cat.title} category={cat} />
            ))}
          </div>
        )}
        {activeTab === 'classification' && <ClassificationSection />}
        {activeTab === 'watchlist' && <WatchlistSection />}
        {activeTab === 'strikes' && <StrikeSection />}
        {activeTab === 'pipeline' && <PipelineSection />}
      </div>
    </div>
  );
}
