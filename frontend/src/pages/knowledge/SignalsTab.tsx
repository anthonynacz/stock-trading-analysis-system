import { PALETTE } from '../../utils/theme';
import { DocTable } from './shared';

/* ── Signal data ────────────────────────────────────────────────────────── */

interface Signal {
  name: string;
  points: string;
  detail: string;
  hint?: string;
}

const SMA_HINT = 'Base points × DTE factor (1.0 / 0.7 / 0.5 / 0.25)';

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
      {
        name: 'Analyst Revision Cluster',
        points: '+10 to +15 / -10 to -15',
        detail: '≥2 distinct T1/T2 firms acting in the same direction within 14d (+10/-10; ±15 at ≥3)',
      },
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
      { name: 'Active Catalyst Window', points: '+5', detail: 'Within T-14 to T-0 of earnings date (the pre-earnings ACTIVE window)' },
    ],
  },
  {
    title: 'Technical / RSI',
    description:
      "RSI-14 and moving average signals from 2-month price history. SMA points are scaled by the suggested trade's DTE — 1.0 at ≥30 DTE, 0.7 at 21–29, 0.5 at 14–20, 0.25 below 14 (quarter weight on weeklies).",
    signals: [
      { name: 'RSI < 20 (Extreme Oversold)', points: '+15', detail: 'Deeply oversold — high reversal probability' },
      { name: 'RSI < 30 (Oversold)', points: '+10', detail: 'Oversold territory' },
      { name: 'RSI > 80 (Extreme Overbought)', points: '-15', detail: 'Extremely overbought — pullback risk' },
      { name: 'RSI > 70 (Overbought)', points: '-10', detail: 'Overbought territory' },
      { name: 'Above 50d SMA', points: '+10 × DTE factor', detail: 'Price trading above 50-day simple moving average', hint: SMA_HINT },
      { name: 'Below 50d SMA', points: '-10 × DTE factor', detail: 'Price trading below 50-day SMA', hint: SMA_HINT },
      { name: 'Above 200d SMA', points: '+5 × DTE factor', detail: 'Long-term uptrend confirmation', hint: SMA_HINT },
      { name: 'Below 200d SMA', points: '-5 × DTE factor', detail: 'Long-term downtrend', hint: SMA_HINT },
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
      { name: 'Deep Pullback from High', points: '+5 to +10', detail: '+10 normally, +5 if declining. 30%+ off 52w high' },
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
      { name: 'High Short Interest', points: '-5', detail: 'Short interest > 10% of float' },
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
      { name: 'Outperforming Market', points: '+10', detail: 'Stock 5d momentum exceeds SPY by >5pp' },
      { name: 'Underperforming Market', points: '-10', detail: 'Stock 5d momentum trails SPY by >5pp' },
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
      {
        name: 'Smart Money Positioning',
        points: '+12',
        detail: '3d avg call volume ≥2× 20d avg AND IV rank up >10pp in ~5d — institutional accumulation ahead of a catalyst',
      },
    ],
  },
  {
    title: 'Greeks (Black-Scholes)',
    description:
      'Computed from Black-Scholes using yfinance IV per contract on the near-term ATM options. Theta is expressed as % of premium lost per day; vega as % of premium per 1% IV move.',
    signals: [
      { name: 'Heavy Theta Decay', points: '-8', detail: 'Near-term ATM theta >3% of premium per day' },
      { name: 'Elevated Theta Decay', points: '-5', detail: 'Near-term ATM theta >2% of premium per day' },
      { name: 'IV Crush Risk', points: '-12', detail: 'Earnings within 7 days AND vega >8% of premium per 1% IV — expect IV crush post-event' },
      { name: 'IV Spike Warning', points: '-12', detail: 'IV rank rose ≥20pp over the last 5 trading days — premium already inflated' },
      {
        name: 'Favorable Theta',
        points: '+5',
        detail: 'Theta <1%/day AND near-term DTE ≤14 AND running score >30 — time decay manageable with strong conviction',
      },
    ],
  },
  {
    title: 'Options Structure',
    description:
      'Hidden-risk flags from the deep options report (term structure, skew, dealer gamma, pin magnets). Backwardation fires at any severity; the others only at MEDIUM/HIGH.',
    signals: [
      { name: 'Term Backwardation', points: '-12', detail: 'Near-term IV above longer-dated IV — textbook "IV crush coming" signature' },
      { name: 'Elevated Put Skew', points: '-8', detail: '25-delta puts priced well above calls — downside hedging demand' },
      { name: 'Negative Gamma Exposure', points: '-10', detail: 'Dealers net short gamma — moves get amplified rather than dampened' },
      { name: 'Options Pin Risk', points: '-5', detail: 'Large OI strike near spot into expiry — price likely to pin' },
    ],
  },
  {
    title: 'Geopolitical',
    description:
      'Keyword-detected events from the last 48h of general news. The sign and size depend on the stock\'s sector (e.g. military conflict: Energy +18, Semis -12); points scale 0.6×–1.0× with FinBERT magnitude. One signal per event type, total capped at ±20, and news-sentiment points are halved whenever any geopolitical signal fires.',
    signals: [
      { name: 'Military Conflict', points: '-12 to +18', detail: 'Energy / Defense positive; Semis, Cloud, Fintech, Healthcare, Media negative' },
      { name: 'Trade War', points: '-18 to +10', detail: 'Semis hit hardest; only Power/Utilities positive' },
      { name: 'Sanctions', points: '-15 to +12', detail: 'Energy, Defense, Power positive; Semis, Fintech, others negative' },
      { name: 'Diplomatic Breakthrough', points: '-12 to +12', detail: 'Risk-on for Semis, Fintech, Cloud, Media; negative for Energy, Defense, Power' },
      { name: 'Oil/Energy Disruption', points: '-12 to +20', detail: 'Energy +20, Power +15; everything else negative' },
      { name: 'Regulatory Crackdown', points: '-18 to -10', detail: 'Negative for every sector; Media, Semis, Fintech, Cloud, Power hit hardest' },
    ],
  },
  {
    title: 'Insider & Positioning',
    description:
      'Insider transactions from the last 30 days, plus neutralizers that dampen or defer entries when the move is already priced in.',
    signals: [
      { name: 'Insider Buying', points: '+5', detail: 'Insider purchases totaling >$500K in last 30 days' },
      { name: 'Insider Selling', points: '-5', detail: 'Insider sales totaling >$1M in last 30 days' },
      {
        name: 'Insider Buy Cluster',
        points: '+8 / +12',
        detail: '≥2 distinct insiders bought AND buys > sells (+12 at ≥3 buyers) — supersedes Insider Buying',
      },
      { name: 'Stock Already Moved', points: '-5', detail: 'Stock moved >5% (either direction) on the catalyst' },
      {
        name: 'Stale Move Gate',
        points: '0',
        detail: 'Move >5% in the same direction as the action — downgrades the action one tier and forces entry to WAIT',
      },
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

// Ranges spanning both signs (e.g. "-12 to +18") render neutral
function pointsColor(points: string): string {
  const mixed = points.includes('+') && points.includes('-');
  if (!mixed && points.startsWith('+')) return PALETTE.green;
  if (!mixed && points.startsWith('-')) return PALETTE.red;
  return PALETTE.amber;
}

function SignalTable({ category }: { category: SignalCategory }) {
  return (
    <div className="mb-6">
      <h3 className="text-base font-semibold text-text-primary mb-1">{category.title}</h3>
      <p className="text-xs text-text-secondary mb-3">{category.description}</p>
      <DocTable<Signal>
        rows={category.signals}
        rowKey={(s) => s.name}
        rowClassName="hover:bg-border/20"
        columns={[
          {
            key: 'name',
            header: 'Signal',
            className: 'text-text-primary',
            render: (s) => (
              <>
                {s.name}
                {s.hint && <div className="text-xs text-text-secondary">{s.hint}</div>}
              </>
            ),
          },
          {
            key: 'points',
            header: 'Points',
            align: 'center',
            headerClass: 'w-36',
            className: 'font-mono font-semibold',
            render: (s) => <span style={{ color: pointsColor(s.points) }}>{s.points}</span>,
          },
          { key: 'detail', header: 'Detail', hideMd: true, className: 'text-text-secondary' },
        ]}
      />
    </div>
  );
}

export default function SignalsTab() {
  return (
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
  );
}
