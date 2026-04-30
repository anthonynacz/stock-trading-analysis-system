import { useState, useMemo, useRef } from 'react';

type Section = 'guide' | 'signals' | 'classification' | 'watchlist' | 'strikes' | 'optionslab' | 'strategies' | 'pipeline';

const TABS: { key: Section; label: string }[] = [
  { key: 'guide', label: 'Trading Guide' },
  { key: 'signals', label: 'Signal Stacking' },
  { key: 'classification', label: 'Classification' },
  { key: 'watchlist', label: 'Watchlist Scoring' },
  { key: 'strikes', label: 'Strike Profiles' },
  { key: 'optionslab', label: 'Options Lab' },
  { key: 'strategies', label: 'Strategies' },
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

/* ── Options Lab section ──────────────────────────────────────────────── */

interface LabElement {
  name: string;
  definition: string;
  effect: string;
  watchOut: string;
  values?: string;
}

function LabElementCard({ el }: { el: LabElement }) {
  return (
    <div className="bg-card border border-border rounded-lg p-3">
      <div className="flex items-baseline gap-2 mb-2 flex-wrap">
        <h4 className="text-sm font-semibold text-text-primary">{el.name}</h4>
        {el.values && (
          <span className="text-[10px] font-mono text-purple-300 bg-purple-900/30 px-1.5 py-px rounded">
            {el.values}
          </span>
        )}
      </div>
      <div className="space-y-1.5 text-xs leading-snug">
        <p>
          <span className="text-text-secondary font-semibold">Definition: </span>
          <span className="text-text-primary">{el.definition}</span>
        </p>
        <p>
          <span className="text-text-secondary font-semibold">Effect: </span>
          <span className="text-text-primary">{el.effect}</span>
        </p>
        <p>
          <span className="text-amber-400 font-semibold">Watch out: </span>
          <span className="text-text-primary">{el.watchOut}</span>
        </p>
      </div>
    </div>
  );
}

function LabSubsection({
  title,
  description,
  elements,
}: {
  title: string;
  description: string;
  elements: LabElement[];
}) {
  return (
    <div>
      <h3 className="text-base font-semibold text-text-primary mb-1">{title}</h3>
      <p className="text-xs text-text-secondary mb-3">{description}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {elements.map((el) => (
          <LabElementCard key={el.name} el={el} />
        ))}
      </div>
    </div>
  );
}

const LAB_HEADER_ELEMENTS: LabElement[] = [
  {
    name: 'Directional Bias',
    values: 'BULLISH / BEARISH / NEUTRAL',
    definition:
      "The engine's read on which way the stock is likely to move next, derived from the full signal stack (same score used on the Dashboard) mapped into three buckets.",
    effect:
      'Drives strategy selection together with IV bucket and earnings proximity. A BULLISH + HIGH IV read produces a BUY_CALL_SPREAD; a NEUTRAL + HIGH IV read produces SELL_IRON_CONDOR, etc.',
    watchOut:
      'Bias is not certainty. A BULLISH tag with conviction just above 0 is a weak lean, not a green light. Always cross-check with the Dashboard signal list before trading.',
  },
  {
    name: 'Price',
    definition: 'Last traded spot from yfinance at the time of analysis.',
    effect:
      'Anchors every moneyness calculation — ATM strike, expected move percentage, distance from pin magnets, max pain distance.',
    watchOut:
      'yfinance prices are stale on weekends and off-hours. If you re-run an analysis Monday morning the whole report can shift because spot finally repriced — the analysis is a snapshot, not a live feed.',
  },
  {
    name: 'IV Rank',
    values: '0 – 100',
    definition:
      'Where current 30-day ATM implied vol sits between its 52-week high and low, expressed 0–100. 0 = at 52-week low, 100 = at 52-week high.',
    effect:
      'Buckets into LOW (<30), MID (30–60), HIGH (>60). Long premium strategies (BUY_CALL, BUY_PUT, BUY_STRADDLE) only fire in LOW/MID; short premium (SELL spreads, SELL_IRON_CONDOR) only fire in HIGH.',
    watchOut:
      'IV rank is range-bound — a stock that never moves can sit at rank 80 on tiny absolute vol. Use alongside expected move percentage to gauge real dollar risk.',
  },
  {
    name: 'IV Percentile',
    values: '0 – 100',
    definition:
      'Percentage of trading days over the last year where IV was below the current reading.',
    effect:
      "Better than IV rank for distributions with outliers — if one earnings spike sets the 52w high, IV rank understates how elevated today really is. Percentile captures the shape of the distribution.",
    watchOut:
      'When rank and percentile diverge sharply (e.g. rank 40, percentile 80), there is a recent outlier distorting rank. Treat percentile as the truer "is vol rich or cheap" read.',
  },
];

const LAB_STRATEGY_ELEMENTS: LabElement[] = [
  {
    name: 'Verdict',
    values:
      'BUY_CALL / BUY_PUT / BUY_CALL_SPREAD / BUY_PUT_SPREAD / SELL_PUT_SPREAD / SELL_CALL_SPREAD / SELL_IRON_CONDOR / BUY_STRADDLE / NO_TRADE',
    definition:
      'The single strategy the engine recommends, selected from a lookup of (directional bias) × (IV bucket) × (near-earnings flag).',
    effect:
      'Dictates everything downstream: leg structure, target expiry, risk profile. A BUY_* verdict means you pay premium; SELL_* means you collect it; NO_TRADE means no edge was found.',
    watchOut:
      'Verdict is the engine\'s single best answer, not the only viable one. BUY_STRADDLE only fires NEUTRAL + HIGH IV + near earnings — a niche setup. NO_TRADE usually means conflicting signals, not "safe to ignore."',
  },
  {
    name: 'Target Expiry / DTE',
    definition:
      'The expiration bucket the engine picked for the trade. DTE = days to expiry.',
    effect:
      'Maps to the DTE spread: 0–7d (weeklies, pure gamma plays), 7–21d (earnings window), 21–45d (directional swing), 45–90d (positioning), 90–180+d (LEAPS-like). Long premium tilts toward 21–45d to balance theta; short premium toward 14–30d to harvest decay fast.',
    watchOut:
      "If target DTE is <10 and verdict is long premium, theta burn is about to eat you — cross-check the THETA_BURN hidden risk. If DTE is >60 on a short-premium trade, you are tying up capital with minimal daily decay.",
  },
  {
    name: 'IV Bucket',
    values: 'LOW / MID / HIGH',
    definition: 'Categorical bin of IV rank: LOW <30, MID 30–60, HIGH >60.',
    effect:
      'Filters which verdicts are eligible. Long premium gets blocked in HIGH IV (paying rich vol); short premium gets blocked in LOW IV (collecting nothing). MID allows directional singles in both directions.',
    watchOut:
      "The bucket edges are sharp — a rank of 59 vs 61 routes you to completely different strategies. When you are near a boundary, read the rationale to see whether the pick is robust or a coin-flip.",
  },
  {
    name: 'Earnings Tag / Earnings DTE',
    definition:
      'Flag that fires when earnings are within 14 days, with days-to-earnings count. Shown as a purple "Earnings Nd" pill.',
    effect:
      'Forces the strategy selector to prefer spreads over naked long premium (to limit IV crush) and opens up BUY_STRADDLE when bias is NEUTRAL and IV is HIGH. Also triggers the IV_CRUSH hidden risk.',
    watchOut:
      'Earnings tag is not "buy the event." Most retail earnings long-premium trades lose to IV crush even when direction is right. If you see this tag, spread structure is nearly always safer than naked calls/puts.',
  },
  {
    name: 'Legs',
    definition:
      'The individual contracts that make up the trade. Each row shows action (BUY/SELL), side (C/P), strike, expiry, quantity, and a per-leg rationale.',
    effect:
      'Spreads have 2+ legs that must be entered together; singles have 1. BUY legs are debit; SELL legs are credit. Net cost = sum of debits − sum of credits.',
    watchOut:
      "When entering a spread manually, always submit as a single spread order — not two market orders — or you'll pay extra slippage and may only get half filled. Check each leg's expiry matches; calendars and diagonals look similar but have different risk.",
  },
  {
    name: 'Notes',
    definition: 'Free-text caveats the engine attaches: entry guidance, management rules, profit targets, risks.',
    effect:
      'Typical notes: "Take profit at 50% max gain," "Close before earnings if IV rank doubles," "Requires $X buying power per spread."',
    watchOut:
      "Notes are not scored — they are the engine's qualitative commentary. Don't substitute them for reading the Hidden Risks panel; they can miss edge cases the structured risk checks catch.",
  },
];

const LAB_HIDDEN_RISKS: LabElement[] = [
  {
    name: 'IV_CRUSH',
    values: 'HIGH',
    definition:
      'Fires when earnings are within 14 days AND IV rank >60 AND the strategy is long-premium (BUY_*).',
    effect:
      'Post-event IV typically collapses 30–50% overnight. Long ATM premium can lose 15–30% even if the directional thesis plays out and the stock moves in your favor.',
    watchOut:
      'This is the single most common way retail loses money on earnings trades. If you see this, convert to a debit spread (caps cost), close the day before earnings, or sit out.',
  },
  {
    name: 'THETA_BURN',
    values: 'HIGH if >3%/d, MEDIUM if >2%/d',
    definition:
      'Fires when the ATM near-term option is losing more than 2% of its premium per calendar day to time decay, on a long-premium trade.',
    effect:
      'You need a fast directional move or a vol expansion just to break even. Holding through a flat weekend can wipe 4–6% before Monday open.',
    watchOut:
      "Theta accelerates non-linearly into expiry — the last 7 DTE decay more than the prior 21. If theta_pct is already >3 on day 1, don't plan to \"give it time.\"",
  },
  {
    name: 'VEGA_EXPOSURE',
    values: 'MEDIUM',
    definition:
      'Fires when ATM vega exceeds 10% of premium per 1 IV point, on a long-premium trade.',
    effect:
      'A 5-point IV drop would shave ~50% off the premium regardless of direction. Matters most on ATM long options right before a known vol-crush event (earnings, FDA, Fed).',
    watchOut:
      "Vega cuts both ways — you benefit from IV expansion, hurt by contraction. If IV rank is already high AND vega is high, you are paying peak price for peak vol sensitivity.",
  },
  {
    name: 'PIN_RISK',
    values: 'MEDIUM',
    definition:
      'Fires when a near-dated strike carries a large share of near-spot open interest, flagging it as a "magnet" price will gravitate toward at expiration.',
    effect:
      'Dealers short that strike hedge dynamically, creating mean-reversion to the pin. Your long premium can decay through expiration sitting near the magnet strike.',
    watchOut:
      "Pin risk only matters 1–3 days pre-expiration. If your DTE is >7 the magnet has time to dissolve. But for weeklies it's real — don't buy ATM the day before monthly OPEX.",
  },
  {
    name: 'LIQUIDITY',
    values: 'MEDIUM',
    definition:
      'Fires when average bid-ask spread exceeds 15% of premium across expirations (Liquidity rating = WIDE).',
    effect:
      'Round-trip slippage eats 5–10% of the trade — you pay the spread going in and coming out. Limit orders may sit unfilled or only get partial fills.',
    watchOut:
      "Never market-order in wide-spread names. Use limits at mid, walk the price in small increments. If you can't get a reasonable fill within 10 minutes, the trade isn't meant for you.",
  },
  {
    name: 'NEGATIVE_GEX',
    values: 'MEDIUM if short-premium, LOW otherwise',
    definition:
      'Fires when net dealer gamma exposure is negative, meaning dealers are short gamma and must hedge with-the-trend.',
    effect:
      'Dealer hedging amplifies realized vol — up moves get chased higher, down moves get pressed lower. Short premium (selling condors, strangles) faces fatter tails.',
    watchOut:
      'A negative GEX regime is fragile. It reverses quickly when dealers buy back hedges, often causing violent mean-reversion — bad for trend-following, dangerous for overnight short vol.',
  },
  {
    name: 'BACKWARDATION',
    values: 'LOW',
    definition:
      'Fires when front-month IV sits above back-month IV (term shape = BACKWARDATION).',
    effect:
      'The market is pricing a specific near-term event (earnings, product launch, Fed). Expect front-month IV to normalize sharply after the event — harmful to long front-month premium.',
    watchOut:
      'Backwardation is a tell that "something is priced in." If your long-call thesis was "the event will be good," the event being good may not be enough — it has to exceed the priced-in expectation.',
  },
  {
    name: 'PUT_SKEW',
    values: 'LOW',
    definition:
      'Fires when 25-delta put IV exceeds 25-delta call IV by more than 8 IV points (average across expirations).',
    effect:
      'Market is paying up for downside hedges. Protective puts are expensive; short puts carry larger-than-usual left-tail risk. Call overwrites harvest less premium than the skew suggests.',
    watchOut:
      'Elevated put skew is structural in indexes (SPY) but notable in single names. A sudden skew spike often precedes or accompanies a macro de-risking event — treat as a sentiment gauge, not just a pricing anomaly.',
  },
  {
    name: 'ASSIGNMENT_RISK',
    values: 'LOW',
    definition:
      'Fires whenever the strategy has a short leg (SELL action) — American options can be assigned any time.',
    effect:
      'Short ITM calls are most vulnerable before ex-dividend dates. Deep-ITM short puts can be assigned late in life when extrinsic value approaches 0. Early assignment flips your risk profile and ties up buying power.',
    watchOut:
      'Monitor intrinsic vs extrinsic on short legs: when extrinsic drops below the next dividend, assignment is likely. Roll or close before extrinsic disappears.',
  },
];

const LAB_VOL_ELEMENTS: LabElement[] = [
  {
    name: 'Term Shape',
    values: 'CONTANGO / BACKWARDATION / FLAT / UNKNOWN',
    definition:
      'Relationship between front-month and back-month ATM IV. CONTANGO = back > front (normal). BACKWARDATION = front > back (stressed). FLAT = within ~1 pt.',
    effect:
      'Contango favors calendar spreads (sell front, buy back — collect front-month theta). Backwardation favors the opposite. Flat is neutral.',
    watchOut:
      "Contango is the default state. Backwardation is your flag that something is up — earnings, macro event, or panic. Don't short vol into backwardation unless you know what's driving it.",
  },
  {
    name: 'Front − Back IV (pts)',
    definition:
      'Numeric spread between front-month and back-month ATM IV, in IV percentage points.',
    effect:
      'Quantifies term-shape intensity. +5 pts backwardation is mild; +15 pts is severe and usually means a specific event is in the front contract.',
    watchOut:
      'Large negative values (-8+) = deep contango, signals vol is expected to expand — common in low-vol regimes before a catalyst. Large positive values = event risk loaded into the front contract.',
  },
  {
    name: '25-Δ Skew',
    definition:
      'Average (25-delta put IV − 25-delta call IV) across expirations, in IV points. Positive = puts richer than calls.',
    effect:
      'Measures demand for crash hedges vs upside bets. In single names, >8 pts is elevated; >15 pts is extreme. Indexes run chronically higher than single names.',
    watchOut:
      "Trades against skew (selling expensive puts, buying cheap calls) can print consistently in a range — until they don't. Skew exists because tail events happen; don't harvest skew naked.",
  },
  {
    name: 'Term Structure (per-expiry chips)',
    definition: 'ATM IV for each expiration bucket (0–7d, 7–21d, 21–45d, 45–90d, 90–180+d).',
    effect:
      'Lets you see whether a specific expiry is mispriced relative to its neighbors. A spike in one bucket usually marks a known event landing in that window.',
    watchOut:
      'Look for "kinks" — one DTE bucket way above its neighbors. That is the event. Trade calendars around the kink; avoid naked long premium in the kink month.',
  },
  {
    name: 'Expected Moves',
    definition:
      'One-standard-deviation expected price range by expiry, derived from ATM straddle price. Shown as ±% and ±$.',
    effect:
      'Represents what the options market is pricing as a 68% likely range by that expiry. Useful for strike selection (OTM strikes outside the expected move are low-probability) and for position sizing.',
    watchOut:
      'Expected move is not a ceiling — by definition 32% of the time the stock moves further. And it assumes normal distribution; real returns have fat tails. Use as a budget, not a guarantee.',
  },
];

const LAB_POSITIONING_ELEMENTS: LabElement[] = [
  {
    name: 'Dealer Gamma (GEX)',
    values: 'POSITIVE / NEGATIVE / NEUTRAL + signed total',
    definition:
      'Net signed gamma exposure held by options market makers, aggregated across the chain. POSITIVE = dealers long gamma (hedge against-trend, dampens vol). NEGATIVE = dealers short gamma (hedge with-trend, amplifies vol).',
    effect:
      'POSITIVE GEX produces sticky, range-bound action — good for short-premium theta harvesting. NEGATIVE GEX produces trending, violent action — bad for short-premium, okay for directional longs.',
    watchOut:
      "GEX is computed from a model (assumed delta/gamma per contract), not observed. It is directionally useful, not precise. Regime flips (positive → negative) matter more than absolute values.",
  },
  {
    name: 'Max Pain (per expiry)',
    definition:
      'The strike where total option holder loss would be maximized at expiration — equivalently, where dealers would owe the least. Shown with distance from spot in %.',
    effect:
      'Classic "pin" theory says price gravitates toward max pain into expiration as dealers hedge. Useful for strike selection near OPEX.',
    watchOut:
      'Max pain is a weak signal on its own — think of it as a magnet, not a destination. Only material within ~3 days of expiration and only when spot is already within ~3%. Ignore for longer-dated trades.',
  },
  {
    name: 'Pin Magnets',
    definition:
      'Near-dated strikes holding a high share of near-spot open interest. Each row shows strike, DTE, call OI, put OI, and % share of near-spot OI.',
    effect:
      'These strikes act as mean-reversion magnets in the final days of their expiry — options dealers hedging the clustered OI pull spot toward the strike.',
    watchOut:
      "If you are long an ATM option and a pin magnet sits at the same strike with 20%+ share, your extrinsic decays while price gets pulled in. Consider rolling out one expiry or switching to a vertical spread.",
  },
  {
    name: 'P/C OI by Expiry',
    definition:
      'Put-to-call open interest ratio for each expiration. Values >1.2 lean bearish (more put positioning); <0.8 lean bullish.',
    effect:
      "Shows where positioning is concentrated across the term structure. A front-month P/C of 1.8 with back-months near 1.0 tells you there is a short-term hedge stack, not a structural bearish bet.",
    watchOut:
      "OI ratio measures accumulated positioning, not today's flow. A stock can have high put OI because of an old protective hedge that is actually bullish (someone buying puts to protect a long equity position). Read alongside unusual options volume.",
  },
];

const LAB_LIQUIDITY_ELEMENTS: LabElement[] = [
  {
    name: 'Liquidity Rating',
    values: 'TIGHT / MODERATE / WIDE / UNKNOWN',
    definition:
      'Categorical rating based on average bid-ask spread as % of premium across the chain. TIGHT <5%, MODERATE 5–15%, WIDE >15%.',
    effect:
      'Drives execution cost. TIGHT names fill at mid easily; WIDE names often need limit-order patience or see 5–10% round-trip slippage.',
    watchOut:
      'Weekend data can show spreads wider than real market-hours spreads because quotes are stale. Re-check spreads live before placing an order. If a name rates WIDE even during the day, size smaller or skip.',
  },
  {
    name: 'Average Spread %',
    definition: 'Mean bid-ask spread as a percentage of option premium, averaged across expirations.',
    effect:
      'Your round-trip cost floor. A 10% average spread means 10% of your premium is lost to the spread over one entry + one exit, independent of directional P&L.',
    watchOut:
      "Spreads are worse at the open (9:30–9:45 ET) and into the close, and wider on expiring contracts. Factor a 1.5× buffer for execution timing if you don't trade mid-day.",
  },
  {
    name: 'By-Expiry Breakdown',
    definition:
      'Per-expiration median spread % and total OI. Thin-OI expiries flagged in amber; wide-spread expiries flagged in red.',
    effect:
      'Lets you pick the most liquid expiry even when the overall name is "MODERATE." Often a weekly is tight while a monthly two months out is wide, or vice versa.',
    watchOut:
      "If your strategy's target DTE lands on a thin-OI expiry, consider rolling to the next standard expiration (third-Friday monthly) — liquidity concentrates there even if DTE moves by a week.",
  },
];

const LAB_GREEKS_ELEMENTS: LabElement[] = [
  {
    name: 'Delta (Δ)',
    values: '-1 to +1',
    definition:
      'Rate of change of option price per $1 move in the underlying. Also interpretable as approximate probability of expiring ITM.',
    effect:
      '0.50 = ATM; 0.30 = slightly OTM; 0.70 = ITM. Directional exposure scales linearly near ATM. The strike recommender filters by delta for each risk profile.',
    watchOut:
      'Delta is dynamic — a 0.30 delta call at entry can become 0.60 if the stock rallies. Position delta + gamma = your real directional exposure. Re-check delta before adding to winners.',
  },
  {
    name: 'Gamma (Γ)',
    definition:
      'Rate of change of delta per $1 move in the underlying. Highest ATM and peaks as expiration approaches.',
    effect:
      'High gamma = delta accelerates fast. Long gamma benefits from big moves in either direction (curvature pays). Short gamma (selling ATM) exposes you to runaway losses.',
    watchOut:
      'Gamma spikes in the last 7 DTE. Short-gamma positions entering that window without hedges can explode — this is why iron condors are closed at ~21 DTE by mechanical sellers.',
  },
  {
    name: 'Theta (Θ/d)',
    definition:
      'Dollar decay per calendar day, all else equal. Shown as $/day; also see theta_pct (percentage of premium per day).',
    effect:
      'Long options pay theta (negative); short options collect it (positive). ATM theta is highest. The greeks row highlights theta in red when it exceeds ~3% of premium/day.',
    watchOut:
      'Theta burns weekends and holidays too. A Friday-bought weekly loses 3 days of decay by Monday even though markets were closed. Factor this into weekend hold decisions.',
  },
  {
    name: 'Vega (V)',
    definition:
      'Dollar change in option price per 1-point change in IV (per 1% IV). Higher for longer-dated options and ATM strikes.',
    effect:
      'Long options are long vega (benefit from IV rising); short options are short vega. Vega is your IV P&L.',
    watchOut:
      'Vega dominates P&L on longer-dated positions — a 20-point IV drop on a 90-DTE long call can wipe the trade even with favorable price action. Match vega to your view on vol.',
  },
  {
    name: 'Rho (ρ)',
    definition:
      'Dollar change in option price per 1-point change in risk-free rate. Higher for longer-dated options; calls positive, puts negative.',
    effect:
      'In normal-rate regimes, essentially ignorable. Matters on LEAPS (365+ DTE) or when rate expectations shift violently.',
    watchOut:
      "Fed pivot days can move rho materially on long-dated positions. If you are holding LEAPS through FOMC, know which direction rho helps you.",
  },
  {
    name: 'Vanna (Vn)',
    definition: 'Rate of change of delta per 1-point change in IV. Captures cross-sensitivity between direction and vol.',
    effect:
      'High positive vanna means rising IV pushes delta up (your call gets more bullish as vol expands). Critical for dealer hedging flows — "vanna rallies" happen when vol drops and dealers buy to rebalance.',
    watchOut:
      'Vanna is second-order — small absolute values. Matters most for structurally short-vol books (dealers, vol funds). Retail traders rarely need to trade it directly, but it explains why vol-crush days sometimes see paradoxical rallies.',
  },
  {
    name: 'Charm (Ch)',
    definition: 'Rate of change of delta per day (delta decay). Shows how your directional exposure drifts as time passes.',
    effect:
      'OTM options lose delta over time (charm negative); ITM options gain it. Explains why a 0.25 delta OTM call can still look OTM even after a small rally if time passed too.',
    watchOut:
      "Pronounced in the last 2 weeks. A weekly OTM call bought Monday can have materially less delta Friday even if the stock didn't move — you have lost both price and participation rate.",
  },
  {
    name: 'Vomma (Vm)',
    definition: 'Rate of change of vega per 1-point change in IV. Second derivative of option price with respect to vol.',
    effect:
      'Positive vomma means vega expands as IV rises — long premium positions benefit more from each additional IV point in a vol spike. Captures the convexity of vol exposure.',
    watchOut:
      'Mostly a vol-trader metric. For single-name directional trading, watch vega first; vomma only bites on large vol moves (>10 IV pts), typically during crashes or earnings.',
  },
];

const LAB_GREEKS_COLUMNS: LabElement[] = [
  {
    name: 'Exp / DTE',
    definition: 'Expiration date and days to expiry for each row in the greeks table.',
    effect:
      'Each row corresponds to one expiration bucket from the DTE spread (up to 8 expirations: 0–7d, 7–21d, 21–45d, 45–90d, 90–180d, 180+d).',
    watchOut:
      "Greeks change dramatically across the term — don't compare a 7-DTE gamma to a 90-DTE gamma directly. Pick the row that matches your target DTE.",
  },
  {
    name: 'ATM Strike / ATM IV',
    definition:
      'The at-the-money strike for that expiry and its implied volatility. ATM IV falls back to the median of near-ATM strikes when direct-ATM contract quotes are stale.',
    effect:
      'Anchors the greeks row — all greeks are computed at the ATM strike, which represents the most liquid / most-used reference contract.',
    watchOut:
      'On weekends or illiquid names, ATM IV can look absurdly low or high if a single stale quote sneaks through the fallback. Compare to IV rank and neighboring expirations to sanity-check.',
  },
  {
    name: 'EM% (Expected Move)',
    definition: 'One-standard-deviation expected move by this expiry, as a % of spot, derived from the ATM straddle.',
    effect:
      'Your probability-weighted price range. OTM strikes outside the EM% are low-probability; ATM straddles are priced for exactly EM% moves to break even.',
    watchOut:
      "EM% grows with √time — a 30-DTE EM of 8% doesn't mean the stock moves 8% in 30 days; it means 68% of scenarios end within ±8%. Sizing a trade on EM requires matching your thesis timeframe to the expiry.",
  },
];

const LAB_RATIONALE_ELEMENT: LabElement = {
  name: 'Rationale',
  definition:
    'A narrative paragraph generated by the engine that walks through why this verdict was chosen given the bias, IV regime, earnings proximity, and structure of the chain.',
  effect:
    "Bridges the numeric dashboards into plain English. Useful for sanity-checking that the verdict matches the raw inputs you would expect.",
  watchOut:
    'Rationale is a summary, not an independent signal. If it contradicts what you see in the Hidden Risks panel, trust the structured risks — those are rule-based and exhaustive; rationale is a generated overview.',
};

function OptionsLabSection() {
  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm text-text-secondary">
          The Options Lab runs a per-ticker deep analysis combining greeks, volatility structure,
          dealer positioning, and liquidity — then picks a single strategy verdict. This reference
          walks through every element in the detail panel: what it is, how it affects the verdict,
          and what to watch out for when acting on it.
        </p>
      </div>

      <LabSubsection
        title="Header & Summary Tiles"
        description="Top of the detail panel: ticker, directional bias, and the three summary tiles (Price / IV Rank / IV %tile) that drive strategy selection."
        elements={LAB_HEADER_ELEMENTS}
      />

      <LabSubsection
        title="Strategy Recommendation"
        description="The verdict card — the engine's single best strategy, target expiry, IV bucket, earnings tag, legs, and notes."
        elements={LAB_STRATEGY_ELEMENTS}
      />

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-1">Hidden Risks</h3>
        <p className="text-xs text-text-secondary mb-3">
          Rule-based structural risks attached to every analysis. Each risk has a severity (HIGH / MEDIUM / LOW),
          a short code, and a reason. Not every risk fires on every trade — they only appear when their trigger conditions are met.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {LAB_HIDDEN_RISKS.map((r) => (
            <LabElementCard key={r.name} el={r} />
          ))}
        </div>
      </div>

      <LabSubsection
        title="Volatility Structure"
        description="How the options market is pricing forward volatility across the term structure and skew curve."
        elements={LAB_VOL_ELEMENTS}
      />

      <LabSubsection
        title="Positioning"
        description="Where the open interest sits — dealer gamma exposure, max pain, pin magnets, and put/call OI ratios by expiry."
        elements={LAB_POSITIONING_ELEMENTS}
      />

      <LabSubsection
        title="Liquidity"
        description="Execution quality metrics. Tells you whether you can actually trade the contracts the engine picked without giving up alpha to spreads."
        elements={LAB_LIQUIDITY_ELEMENTS}
      />

      <LabSubsection
        title="ATM Call Greeks by Expiry — Columns"
        description="Structure of the table: each row is one expiration; each column is a greek or reference value at the ATM strike."
        elements={LAB_GREEKS_COLUMNS}
      />

      <LabSubsection
        title="ATM Call Greeks by Expiry — Greeks"
        description="The greeks themselves. First-order (Δ, Γ, Θ, V, ρ) drive most of your P&L; second-order (Vanna, Charm, Vomma) describe how the first-order greeks evolve."
        elements={LAB_GREEKS_ELEMENTS}
      />

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-1">Rationale</h3>
        <p className="text-xs text-text-secondary mb-3">The narrative block at the bottom of the panel.</p>
        <LabElementCard el={LAB_RATIONALE_ELEMENT} />
      </div>

      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-text-primary mb-2">How to read the panel in order</h3>
        <ol className="list-decimal pl-5 text-xs text-text-secondary space-y-1.5">
          <li><strong className="text-text-primary">Start with Bias + IV Rank + IV %tile</strong> — they set the strategy universe.</li>
          <li><strong className="text-text-primary">Read the Verdict and Target DTE</strong> — that is the engine's pick.</li>
          <li><strong className="text-text-primary">Scan Hidden Risks</strong> — a single HIGH risk is often reason enough to decline the trade.</li>
          <li><strong className="text-text-primary">Check Volatility Structure</strong> — is vol priced for an event? Is it contango or backwardation? Does that match the verdict's IV bucket?</li>
          <li><strong className="text-text-primary">Check Positioning</strong> — negative GEX or nearby pin magnets can invalidate an otherwise-clean setup.</li>
          <li><strong className="text-text-primary">Verify Liquidity</strong> — if WIDE, shrink your size or skip.</li>
          <li><strong className="text-text-primary">Only then read the Greeks table</strong> — confirm the target DTE row has tolerable theta, reasonable vega, and a delta that matches your risk profile.</li>
          <li><strong className="text-text-primary">Rationale last</strong> — as a sanity check, not a decision driver.</li>
        </ol>
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

/* ── Strategies section ─────────────────────────────────────────────────── */

interface Leg {
  action: 'BUY' | 'SELL';
  side: 'CALL' | 'PUT';
  strike: number;
  premium: number;
}

interface Strategy {
  name: string;
  verdict: string;
  trigger: string;
  purpose: string;
  legs: Leg[];
  legText: string[];
  maxProfit: string;
  maxLoss: string;
  breakeven: string;
  priceMin: number;
  priceMax: number;
}

function legPnL(leg: Leg, S: number): number {
  const intrinsic = leg.side === 'CALL' ? Math.max(S - leg.strike, 0) : Math.max(leg.strike - S, 0);
  return leg.action === 'BUY' ? intrinsic - leg.premium : leg.premium - intrinsic;
}

interface PayoffMetrics {
  profitStr: string;
  lossStr: string;
  beStr: string;
  yLo: number;
  yHi: number;
}

const isLegActive = (leg: Leg): boolean => leg.strike > 0 && leg.premium > 0;

function computePayoff(
  legs: Leg[],
  priceMin: number,
  priceMax: number,
  N = 401,
): { prices: number[]; pnl: number[]; legPnls: number[][]; metrics: PayoffMetrics } {
  const prices: number[] = new Array(N);
  const pnl: number[] = new Array(N);
  const legPnls: number[][] = legs.map(() => new Array(N));
  for (let i = 0; i < N; i++) {
    const S = priceMin + ((priceMax - priceMin) * i) / (N - 1);
    prices[i] = S;
    let total = 0;
    for (let l = 0; l < legs.length; l++) {
      const v = isLegActive(legs[l]) ? legPnL(legs[l], S) : 0;
      legPnls[l][i] = v;
      total += v;
    }
    pnl[i] = total;
  }
  const maxPnl = Math.max(...pnl);
  const minPnl = Math.min(...pnl);
  const pad = (maxPnl - minPnl) * 0.15 || 1;
  const yLo = Math.min(0, minPnl) - pad;
  const yHi = Math.max(0, maxPnl) + pad;

  // Edge slopes — detect unbounded profit/loss regions.
  const rSlope = pnl[N - 1] - pnl[N - 2];
  const lSlope = pnl[0] - pnl[1];
  const unboundedUp = rSlope > 0.01 || lSlope > 0.01;
  const unboundedDown = rSlope < -0.01 || lSlope < -0.01;
  const profitStr = unboundedUp ? 'Unlimited' : `$${maxPnl.toFixed(2)}`;
  const lossStr = unboundedDown ? 'Unlimited' : `$${(-minPnl).toFixed(2)}`;

  const crossings: number[] = [];
  for (let i = 1; i < N; i++) {
    const a = pnl[i - 1];
    const b = pnl[i];
    if ((a < 0 && b >= 0) || (a > 0 && b <= 0)) {
      const t = a / (a - b);
      crossings.push(prices[i - 1] + t * (prices[i] - prices[i - 1]));
    }
  }
  const beStr =
    crossings.length === 0
      ? '—'
      : crossings.map((x) => `$${x.toFixed(2)}`).join(' / ');

  return { prices, pnl, legPnls, metrics: { profitStr, lossStr, beStr, yLo, yHi } };
}

const LEG_COLOR = (leg: Leg) => (leg.side === 'CALL' ? '#2dd4bf' : '#f59e0b');

interface DragState {
  kind: 'strike' | 'premium';
  legIdx: number;
  startS: number;
  startP: number;
  anchorX: number;
  anchorY: number;
  yLo: number;
  yHi: number;
}

function InteractivePayoff({
  legs,
  priceMin,
  priceMax,
  onLegChange,
  spot = 100,
}: {
  legs: Leg[];
  priceMin: number;
  priceMax: number;
  onLegChange: (i: number, patch: Partial<Leg>) => void;
  spot?: number;
}) {
  const W = 380;
  const H = 210;
  const M = { l: 38, r: 12, t: 16, b: 30 };
  const plotW = W - M.l - M.r;
  const plotH = H - M.t - M.b;

  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  const { pnl, legPnls, metrics } = useMemo(
    () => computePayoff(legs, priceMin, priceMax),
    [legs, priceMin, priceMax],
  );
  const { yLo, yHi } = metrics;

  const xOf = (S: number) =>
    M.l + ((S - priceMin) / (priceMax - priceMin)) * plotW;
  const yOf = (v: number) =>
    M.t + (1 - (v - yLo) / (yHi - yLo)) * plotH;
  const zeroY = yOf(0);

  const N = pnl.length;
  const combinedPath = pnl
    .map(
      (v, i) =>
        `${i === 0 ? 'M' : 'L'} ${xOf(
          priceMin + ((priceMax - priceMin) * i) / (N - 1),
        ).toFixed(2)},${yOf(v).toFixed(2)}`,
    )
    .join(' ');

  // Fill profit/loss regions (tint between combined curve and y=0)
  const profitPaths: string[] = [];
  const lossPaths: string[] = [];
  {
    let bucket: 'profit' | 'loss' | null = null;
    let segStart = 0;
    const xs: number[] = pnl.map((_, i) =>
      xOf(priceMin + ((priceMax - priceMin) * i) / (N - 1)),
    );
    const ys: number[] = pnl.map((v) => yOf(v));
    const closeSeg = (endIdx: number, endX: number) => {
      if (bucket === null) return;
      let d = `M ${xs[segStart].toFixed(2)},${zeroY.toFixed(2)} `;
      for (let i = segStart; i <= endIdx; i++)
        d += `L ${xs[i].toFixed(2)},${ys[i].toFixed(2)} `;
      d += `L ${endX.toFixed(2)},${zeroY.toFixed(2)} Z`;
      (bucket === 'profit' ? profitPaths : lossPaths).push(d);
    };
    for (let i = 0; i < N; i++) {
      const side =
        ys[i] < zeroY - 0.01 ? 'profit' : ys[i] > zeroY + 0.01 ? 'loss' : null;
      if (side === null) continue;
      if (bucket === null) {
        bucket = side;
        segStart = i;
        continue;
      }
      if (side !== bucket) {
        const t = (zeroY - ys[i - 1]) / (ys[i] - ys[i - 1]);
        const crossX = xs[i - 1] + t * (xs[i] - xs[i - 1]);
        closeSeg(i - 1, crossX);
        bucket = side;
        segStart = i;
      }
    }
    if (bucket !== null) closeSeg(N - 1, xs[N - 1]);
  }

  // Leg curves and handle positions
  const legCurves = legs.map((leg, li) => {
    const d = legPnls[li]
      .map(
        (v, i) =>
          `${i === 0 ? 'M' : 'L'} ${xOf(
            priceMin + ((priceMax - priceMin) * i) / (N - 1),
          ).toFixed(2)},${yOf(v).toFixed(2)}`,
      )
      .join(' ');
    // Premium handle sits on the OTM flat segment
    const span = priceMax - priceMin;
    const handleS =
      leg.side === 'CALL'
        ? Math.max(priceMin + span * 0.05, leg.strike - span * 0.15)
        : Math.min(priceMax - span * 0.05, leg.strike + span * 0.15);
    const flatPnl = leg.action === 'BUY' ? -leg.premium : leg.premium;
    return {
      leg,
      d,
      handle: { x: xOf(handleS), y: yOf(flatPnl) },
    };
  });

  const viewBoxPoint = (e: React.PointerEvent): { x: number; y: number } => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const m = svg.getScreenCTM();
    if (!m) return { x: 0, y: 0 };
    const r = pt.matrixTransform(m.inverse());
    return { x: r.x, y: r.y };
  };

  const beginDrag =
    (kind: 'strike' | 'premium', legIdx: number) =>
    (e: React.PointerEvent) => {
      e.preventDefault();
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
      const p = viewBoxPoint(e);
      setDrag({
        kind,
        legIdx,
        startS: legs[legIdx].strike,
        startP: legs[legIdx].premium,
        anchorX: p.x,
        anchorY: p.y,
        yLo,
        yHi,
      });
    };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const p = viewBoxPoint(e);
    const leg = legs[drag.legIdx];
    if (drag.kind === 'strike') {
      const dX = p.x - drag.anchorX;
      const dS = (dX / plotW) * (priceMax - priceMin);
      const raw = drag.startS + dS;
      const snapped = Math.round(raw * 2) / 2; // 0.5 increments
      const clamped = Math.max(priceMin + 0.5, Math.min(priceMax - 0.5, snapped));
      if (clamped !== leg.strike) onLegChange(drag.legIdx, { strike: clamped });
    } else {
      // Premium drag — freeze y-scale at drag start to prevent jitter
      const dY = p.y - drag.anchorY;
      const dPnl = -(dY / plotH) * (drag.yHi - drag.yLo);
      const sign = leg.action === 'BUY' ? -1 : 1;
      const newP = drag.startP + sign * dPnl;
      const snapped = Math.round(newP * 20) / 20; // $0.05 increments
      const clamped = Math.max(0.05, Math.min(99, snapped));
      if (Math.abs(clamped - leg.premium) > 0.001)
        onLegChange(drag.legIdx, { premium: clamped });
    }
  };

  const endDrag = (e: React.PointerEvent) => {
    if (!drag) return;
    (e.currentTarget as Element).releasePointerCapture(e.pointerId);
    setDrag(null);
  };

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-auto select-none touch-none"
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <rect
        x={M.l}
        y={M.t}
        width={plotW}
        height={plotH}
        fill="#0d1117"
        stroke="#21262d"
      />
      {profitPaths.map((d, i) => (
        <path key={`p${i}`} d={d} fill="#2ea04322" />
      ))}
      {lossPaths.map((d, i) => (
        <path key={`l${i}`} d={d} fill="#da363322" />
      ))}
      <line
        x1={M.l}
        y1={zeroY}
        x2={W - M.r}
        y2={zeroY}
        stroke="#4b5563"
        strokeDasharray="3 3"
        strokeWidth={1}
      />
      <line
        x1={xOf(spot)}
        y1={M.t}
        x2={xOf(spot)}
        y2={H - M.b}
        stroke="#58a6ff"
        strokeDasharray="2 2"
        strokeWidth={1}
        opacity={0.7}
      />

      {/* Leg curves — only for active legs */}
      {legs.length > 1 &&
        legCurves.map((lc, i) =>
          isLegActive(lc.leg) ? (
            <path
              key={`leg${i}`}
              d={lc.d}
              fill="none"
              stroke={LEG_COLOR(lc.leg)}
              strokeWidth={1.2}
              strokeDasharray={lc.leg.action === 'SELL' ? '4 3' : undefined}
              opacity={0.85}
            />
          ) : null,
        )}

      {/* Combined P&L */}
      <path d={combinedPath} fill="none" stroke="#e6edf3" strokeWidth={1.75} />

      {/* Strike lines with drag handles — one per active leg */}
      {legs.map((leg, i) => {
        if (!isLegActive(leg)) return null;
        const x = xOf(leg.strike);
        const color = LEG_COLOR(leg);
        const active = drag?.kind === 'strike' && drag.legIdx === i;
        return (
          <g key={`strike-${i}`}>
            <line
              x1={x}
              y1={M.t}
              x2={x}
              y2={H - M.b}
              stroke={color}
              strokeDasharray="2 3"
              strokeWidth={active ? 1.5 : 0.9}
              opacity={0.7}
            />
            {/* Wide invisible hit area over the line for easy grabbing */}
            <rect
              x={x - 5}
              y={M.t}
              width={10}
              height={plotH}
              fill="transparent"
              style={{ cursor: 'ew-resize' }}
              onPointerDown={beginDrag('strike', i)}
            />
            {/* Visible handle at top */}
            <circle
              cx={x}
              cy={M.t + 4}
              r={active ? 4.5 : 3.5}
              fill={color}
              stroke="#0d1117"
              strokeWidth={1}
              style={{ cursor: 'ew-resize' }}
              onPointerDown={beginDrag('strike', i)}
            />
            <text
              x={x}
              y={M.t - 4}
              textAnchor="middle"
              fontSize={9}
              fill={color}
              fontWeight={active ? 600 : 400}
            >
              K{leg.strike}
            </text>
          </g>
        );
      })}

      {/* Premium drag handles on each active leg's OTM flat segment */}
      {legCurves.map((lc, i) => {
        if (!isLegActive(lc.leg)) return null;
        const active = drag?.kind === 'premium' && drag.legIdx === i;
        return (
          <g key={`prem-${i}`}>
            <circle
              cx={lc.handle.x}
              cy={lc.handle.y}
              r={active ? 5 : 3.5}
              fill="#0d1117"
              stroke={LEG_COLOR(lc.leg)}
              strokeWidth={1.5}
              style={{ cursor: 'ns-resize' }}
              onPointerDown={beginDrag('premium', i)}
            />
          </g>
        );
      })}

      {/* Axes labels */}
      <text x={M.l - 4} y={zeroY + 3} textAnchor="end" fontSize={9} fill="#8b949e">0</text>
      <text x={M.l - 4} y={M.t + 8} textAnchor="end" fontSize={9} fill="#8b949e">+${yHi.toFixed(0)}</text>
      <text x={M.l - 4} y={H - M.b} textAnchor="end" fontSize={9} fill="#8b949e">${yLo.toFixed(0)}</text>
      <text x={M.l} y={H - M.b + 14} fontSize={9} fill="#8b949e">${priceMin}</text>
      <text x={xOf(spot)} y={H - M.b + 14} textAnchor="middle" fontSize={9} fill="#58a6ff">spot ${spot}</text>
      <text x={W - M.r} y={H - M.b + 14} textAnchor="end" fontSize={9} fill="#8b949e">${priceMax}</text>
    </svg>
  );
}

function LegLegend({ legs }: { legs: Leg[] }) {
  if (legs.length < 2) return null;
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 px-1 pt-1.5 text-[10px] text-text-secondary">
      <span className="flex items-center gap-1">
        <span className="inline-block w-4 h-0.5 bg-[#e6edf3]" /> combined
      </span>
      {legs.map((leg, i) => {
        const active = isLegActive(leg);
        return (
          <span
            key={i}
            className={`flex items-center gap-1 ${active ? '' : 'opacity-40 line-through'}`}
          >
            <svg width="18" height="4" className="inline-block">
              <line
                x1="0" y1="2" x2="18" y2="2"
                stroke={active ? LEG_COLOR(leg) : '#6b7280'}
                strokeWidth="1.5"
                strokeDasharray={leg.action === 'SELL' ? '4 3' : undefined}
              />
            </svg>
            <span>
              {leg.action === 'BUY' ? '+' : '−'}
              {leg.strike} {leg.side[0]}{leg.side.slice(1).toLowerCase()} @ ${leg.premium.toFixed(2)}
              {!active && ' (off)'}
            </span>
          </span>
        );
      })}
    </div>
  );
}

function LegEditor({
  legs,
  onChange,
}: {
  legs: Leg[];
  onChange: (i: number, patch: Partial<Leg>) => void;
}) {
  return (
    <div className="space-y-1">
      {legs.map((leg, i) => {
        const active = isLegActive(leg);
        const color = active ? LEG_COLOR(leg) : '#6b7280';
        return (
          <div
            key={i}
            className={`flex items-center gap-1.5 text-[11px] font-mono bg-page rounded px-1.5 py-1 ${
              active ? '' : 'opacity-50'
            }`}
            title={active ? undefined : 'Disabled — set strike and premium > 0 to re-enable'}
          >
            <span
              className="inline-block w-1.5 h-4 rounded-sm shrink-0"
              style={{ background: color, opacity: leg.action === 'SELL' ? 0.55 : 1 }}
            />
            <select
              value={leg.action}
              onChange={(e) =>
                onChange(i, { action: e.target.value as Leg['action'] })
              }
              className="bg-card border border-border rounded px-1 py-0.5 text-text-primary"
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
            <select
              value={leg.side}
              onChange={(e) =>
                onChange(i, { side: e.target.value as Leg['side'] })
              }
              className="bg-card border border-border rounded px-1 py-0.5 text-text-primary"
            >
              <option value="CALL">CALL</option>
              <option value="PUT">PUT</option>
            </select>
            <label className="text-text-secondary ml-1">K</label>
            <input
              type="number"
              step={0.5}
              min={0}
              value={leg.strike}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) onChange(i, { strike: v });
              }}
              className="w-16 bg-card border border-border rounded px-1 py-0.5 text-text-primary"
            />
            <label className="text-text-secondary ml-1">$</label>
            <input
              type="number"
              step={0.05}
              min={0}
              value={leg.premium}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) onChange(i, { premium: v });
              }}
              className="w-16 bg-card border border-border rounded px-1 py-0.5 text-text-primary"
            />
            {!active && (
              <span className="text-[10px] text-text-secondary uppercase tracking-wider ml-1">
                off
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

const STRATEGIES: Strategy[] = [
  {
    name: 'Long Call',
    verdict: 'BUY_CALL',
    trigger: 'BULLISH • LOW or MID IV • not near earnings',
    purpose: 'Cheap premium + directional conviction. Unlimited upside, fixed downside.',
    legs: [{ action: 'BUY', side: 'CALL', strike: 100, premium: 3 }],
    legText: ['BUY 1 × 100 call @ $3.00'],
    maxProfit: 'Unlimited',
    maxLoss: '$3.00 (debit paid)',
    breakeven: '$103',
    priceMin: 85, priceMax: 115,
  },
  {
    name: 'Long Put',
    verdict: 'BUY_PUT',
    trigger: 'BEARISH • LOW or MID IV • not near earnings',
    purpose: 'Cheap premium + directional bearish conviction. Profits on a drop.',
    legs: [{ action: 'BUY', side: 'PUT', strike: 100, premium: 3 }],
    legText: ['BUY 1 × 100 put @ $3.00'],
    maxProfit: '$97 (strike − debit, at $0)',
    maxLoss: '$3.00 (debit paid)',
    breakeven: '$97',
    priceMin: 85, priceMax: 115,
  },
  {
    name: 'Bull Call Spread',
    verdict: 'BUY_CALL_SPREAD',
    trigger: 'BULLISH • MID IV • near earnings',
    purpose: 'Debit spread caps IV-crush exposure into an event while keeping bullish P&L.',
    legs: [
      { action: 'BUY', side: 'CALL', strike: 100, premium: 3 },
      { action: 'SELL', side: 'CALL', strike: 105, premium: 1 },
    ],
    legText: ['BUY 1 × 100 call @ $3.00', 'SELL 1 × 105 call @ $1.00'],
    maxProfit: '$3.00 (width − debit)',
    maxLoss: '$2.00 (net debit)',
    breakeven: '$102',
    priceMin: 90, priceMax: 115,
  },
  {
    name: 'Bear Put Spread',
    verdict: 'BUY_PUT_SPREAD',
    trigger: 'BEARISH • MID IV • near earnings',
    purpose: 'Debit put spread — defined-risk bearish play that blunts IV crush.',
    legs: [
      { action: 'BUY', side: 'PUT', strike: 100, premium: 3 },
      { action: 'SELL', side: 'PUT', strike: 95, premium: 1 },
    ],
    legText: ['BUY 1 × 100 put @ $3.00', 'SELL 1 × 95 put @ $1.00'],
    maxProfit: '$3.00 (width − debit)',
    maxLoss: '$2.00 (net debit)',
    breakeven: '$98',
    priceMin: 85, priceMax: 110,
  },
  {
    name: 'Bull Put Spread',
    verdict: 'SELL_PUT_SPREAD',
    trigger: 'BULLISH • HIGH IV • any earnings',
    purpose: 'Credit spread — sell expensive premium while staying bullish. IV crush helps.',
    legs: [
      { action: 'SELL', side: 'PUT', strike: 100, premium: 3 },
      { action: 'BUY', side: 'PUT', strike: 95, premium: 1 },
    ],
    legText: ['SELL 1 × 100 put @ $3.00', 'BUY 1 × 95 put @ $1.00'],
    maxProfit: '$2.00 (net credit)',
    maxLoss: '$3.00 (width − credit)',
    breakeven: '$98',
    priceMin: 85, priceMax: 115,
  },
  {
    name: 'Bear Call Spread',
    verdict: 'SELL_CALL_SPREAD',
    trigger: 'BEARISH • HIGH IV • any earnings',
    purpose: 'Credit spread — collect rich premium while bearish. Profits from sideways/down.',
    legs: [
      { action: 'SELL', side: 'CALL', strike: 100, premium: 3 },
      { action: 'BUY', side: 'CALL', strike: 105, premium: 1 },
    ],
    legText: ['SELL 1 × 100 call @ $3.00', 'BUY 1 × 105 call @ $1.00'],
    maxProfit: '$2.00 (net credit)',
    maxLoss: '$3.00 (width − credit)',
    breakeven: '$102',
    priceMin: 85, priceMax: 115,
  },
  {
    name: 'Iron Condor',
    verdict: 'SELL_IRON_CONDOR',
    trigger: 'NEUTRAL • HIGH IV',
    purpose: 'No directional edge + rich premium → harvest theta in a range, defined-risk wings.',
    legs: [
      { action: 'SELL', side: 'PUT', strike: 95, premium: 1.5 },
      { action: 'BUY', side: 'PUT', strike: 90, premium: 0.5 },
      { action: 'SELL', side: 'CALL', strike: 105, premium: 1.5 },
      { action: 'BUY', side: 'CALL', strike: 110, premium: 0.5 },
    ],
    legText: [
      'SELL 1 × 95 put @ $1.50',
      'BUY 1 × 90 put @ $0.50',
      'SELL 1 × 105 call @ $1.50',
      'BUY 1 × 110 call @ $0.50',
    ],
    maxProfit: '$2.00 (net credit)',
    maxLoss: '$3.00 (wing width − credit)',
    breakeven: '$93 and $107',
    priceMin: 80, priceMax: 120,
  },
  {
    name: 'Long Straddle',
    verdict: 'BUY_STRADDLE',
    trigger: 'NEUTRAL • LOW IV • near earnings',
    purpose: 'Bet on volatility expansion into a catalyst. Profits on a big move either direction.',
    legs: [
      { action: 'BUY', side: 'CALL', strike: 100, premium: 3 },
      { action: 'BUY', side: 'PUT', strike: 100, premium: 3 },
    ],
    legText: ['BUY 1 × 100 call @ $3.00', 'BUY 1 × 100 put @ $3.00'],
    maxProfit: 'Unlimited (above); $94 (below, to $0)',
    maxLoss: '$6.00 (combined debit, at strike)',
    breakeven: '$94 and $106',
    priceMin: 80, priceMax: 120,
  },
];

function StrategyCard({ strategy }: { strategy: Strategy }) {
  const [legs, setLegs] = useState<Leg[]>(() =>
    strategy.legs.map((l) => ({ ...l })),
  );
  const [dirty, setDirty] = useState(false);

  const updateLeg = (i: number, patch: Partial<Leg>) => {
    setLegs((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
    setDirty(true);
  };

  const reset = () => {
    setLegs(strategy.legs.map((l) => ({ ...l })));
    setDirty(false);
  };

  const { metrics } = useMemo(
    () => computePayoff(legs, strategy.priceMin, strategy.priceMax),
    [legs, strategy.priceMin, strategy.priceMax],
  );

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className="flex items-baseline justify-between gap-2 mb-1 flex-wrap">
        <h3 className="text-base font-semibold text-text-primary">{strategy.name}</h3>
        <span className="text-[10px] font-mono text-purple-300 bg-purple-900/30 px-1.5 py-px rounded">
          {strategy.verdict}
        </span>
      </div>
      <p className="text-[11px] text-text-secondary mb-2">
        <span className="font-semibold">Fires when:</span> {strategy.trigger}
      </p>
      <p className="text-xs text-text-primary mb-3 leading-snug">{strategy.purpose}</p>
      <div className="bg-page rounded border border-border mb-3 p-1">
        <InteractivePayoff
          legs={legs}
          priceMin={strategy.priceMin}
          priceMax={strategy.priceMax}
          onLegChange={updateLeg}
        />
        <LegLegend legs={legs} />
      </div>
      <div className="space-y-2 text-xs">
        <div className="flex items-center justify-between">
          <div className="text-text-secondary font-semibold">Legs</div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-text-secondary italic">
              drag handles or edit below
            </span>
            {dirty && (
              <button
                type="button"
                onClick={reset}
                className="text-[10px] px-1.5 py-0.5 rounded bg-border/60 text-text-primary hover:bg-border transition-colors"
              >
                Reset
              </button>
            )}
          </div>
        </div>
        <LegEditor legs={legs} onChange={updateLeg} />
        <div className="grid grid-cols-3 gap-2 pt-2 border-t border-border">
          <div>
            <div className="text-[10px] text-text-secondary uppercase">Max profit</div>
            <div className="text-green-400 font-semibold">{metrics.profitStr}</div>
          </div>
          <div>
            <div className="text-[10px] text-text-secondary uppercase">Max loss</div>
            <div className="text-red-400 font-semibold">{metrics.lossStr}</div>
          </div>
          <div>
            <div className="text-[10px] text-text-secondary uppercase">Breakeven</div>
            <div className="text-text-primary font-semibold">{metrics.beStr}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StrategiesSection() {
  return (
    <div>
      <p className="text-sm text-text-secondary mb-4">
        The deep options analyzer picks one of eight strategies using a lookup of{' '}
        <span className="text-text-primary font-semibold">directional bias</span> ×{' '}
        <span className="text-text-primary font-semibold">IV bucket</span> ×{' '}
        <span className="text-text-primary font-semibold">near-earnings flag</span>.
        Each card below shows the P&amp;L at expiration for a canonical $100 underlying. Strike lines
        are colored per leg (<span className="text-[#2dd4bf]">calls teal</span>, <span className="text-[#f59e0b]">puts amber</span>); current spot is the blue dashed line.
        <br />
        <span className="text-text-primary font-semibold">Interactive:</span> drag a strike handle left/right or a round premium dot up/down, or edit the leg values below each chart — Max P/L and breakeven recompute live.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {STRATEGIES.map((s) => <StrategyCard key={s.verdict} strategy={s} />)}
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
        {activeTab === 'optionslab' && <OptionsLabSection />}
        {activeTab === 'strategies' && <StrategiesSection />}
        {activeTab === 'pipeline' && <PipelineSection />}
      </div>
    </div>
  );
}
