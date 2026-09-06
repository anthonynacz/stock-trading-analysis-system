import { SectionHeading, StatTile } from './shared';

const CONTRACT_FILTERS: [label: string, value: string][] = [
  ['Min Open Interest', '≥ 10'],
  ['Max Bid-Ask Spread', 'ask ≤ 1.5 × bid'],
  ['Budget Filter', 'premium × 100 ≤ budget'],
];

const PROFILES = [
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

export default function StrikesTab() {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Risk Profiles</h3>
        <p className="text-xs text-text-secondary mb-4">
          The strike recommender filters options chains by delta range and DTE for each profile.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {PROFILES.map((p) => (
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
        <SectionHeading title="Greeks" />
        <p className="text-sm text-text-secondary">
          Delta, gamma, theta and vega are computed with Black-Scholes from each contract's implied
          volatility. Within a profile's delta band, contracts closest to the band midpoint score highest;
          contracts losing &gt;3% of premium per day to theta are scored lower (&gt;1.5%/day slightly lower)
          and flagged HIGH THETA / HIGH VEGA in the explanation. Broker values may differ.
        </p>
      </div>

      <div>
        <SectionHeading title="Contract Filters" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {CONTRACT_FILTERS.map(([label, value]) => (
            <StatTile key={label} label={label} value={value} />
          ))}
        </div>
      </div>
    </div>
  );
}
