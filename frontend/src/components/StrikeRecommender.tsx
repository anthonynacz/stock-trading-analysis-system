import { useState } from 'react';
import type { StrikeAllResult, StrikeRecommendation } from '../types';
import { getStrikeRecommendationsAll } from '../utils/api';

interface StrikeRecommenderProps {
  ticker: string;
}

const RISK_LEVELS = ['conservative', 'moderate', 'aggressive'] as const;
type RiskLevel = (typeof RISK_LEVELS)[number];

function StrikeCard({
  rec,
  type,
}: {
  rec: StrikeRecommendation;
  type: 'CALL' | 'PUT';
}) {
  const isCall = type === 'CALL';
  const borderColor = isCall ? 'border-green-600/60' : 'border-red-600/60';
  const labelColor = isCall ? 'text-green-400' : 'text-red-400';
  const bgColor = isCall ? 'bg-green-900/10' : 'bg-red-900/10';

  return (
    <div className={`${bgColor} border ${borderColor} rounded-lg p-3 space-y-2`}>
      <div className="flex items-center justify-between">
        <span className={`text-xs font-bold uppercase ${labelColor}`}>{type}</span>
        <span className="text-xs text-text-secondary">{rec.days_to_expiry}d to expiry</span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <span className="text-text-secondary">Strike</span>
        <span className="text-text-primary font-mono text-right">${rec.strike.toFixed(2)}</span>
        <span className="text-text-secondary">Expiry</span>
        <span className="text-text-primary text-right">{rec.expiry}</span>
        <span className="text-text-secondary">Premium</span>
        <span className="text-text-primary font-mono text-right">${rec.premium_estimate.toFixed(2)}</span>
        <span className="text-text-secondary">Delta</span>
        <span className="text-text-primary font-mono text-right">{rec.delta_estimate.toFixed(3)}</span>
        {rec.theta_estimate != null && (
          <>
            <span className="text-text-secondary">Theta</span>
            <span className={`font-mono text-right ${rec.premium_estimate > 0 && Math.abs(rec.theta_estimate) / rec.premium_estimate > 0.02 ? 'text-amber-400' : 'text-text-primary'}`}>
              {rec.theta_estimate.toFixed(4)}/day
            </span>
          </>
        )}
        {rec.vega_estimate != null && (
          <>
            <span className="text-text-secondary">Vega</span>
            <span className="text-text-primary font-mono text-right">{rec.vega_estimate.toFixed(4)}</span>
          </>
        )}
        <span className="text-text-secondary">Breakeven</span>
        <span className="text-text-primary font-mono text-right">${rec.breakeven.toFixed(2)}</span>
        <span className="text-text-secondary">Open Interest</span>
        <span className="text-text-primary font-mono text-right">{rec.open_interest.toLocaleString()}</span>
      </div>
      {rec.theta_estimate != null && rec.premium_estimate > 0 &&
        Math.abs(rec.theta_estimate) / rec.premium_estimate > 0.02 && (
        <div className="flex items-center gap-1.5 text-[10px] text-amber-400 mt-1">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
          Heavy theta decay — {(Math.abs(rec.theta_estimate) / rec.premium_estimate * 100).toFixed(1)}% premium/day
        </div>
      )}
      <p className="text-[11px] text-text-secondary leading-relaxed border-t border-border/40 pt-2 mt-1">
        {rec.explanation}
      </p>
    </div>
  );
}

export default function StrikeRecommender({ ticker }: StrikeRecommenderProps) {
  const [activeTab, setActiveTab] = useState<RiskLevel>('moderate');
  const [budget, setBudget] = useState(2000);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<StrikeAllResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFind = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getStrikeRecommendationsAll(ticker, budget);
      setResult(data);
    } catch {
      setError('No options data available');
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const activePair = result ? result[activeTab] : null;

  // Check which tabs have results for indicator dots
  const hasResults = (level: RiskLevel): boolean | null => {
    if (!result) return null;
    const pair = result[level];
    return pair.recommended_call !== null || pair.recommended_put !== null;
  };

  return (
    <div className="space-y-3">
      <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
        Strike Recommender
      </h4>

      {/* Budget slider */}
      <div className="space-y-1">
        <div className="flex justify-between text-[10px] text-text-secondary">
          <span>Max Budget</span>
          <span className="font-mono text-text-primary">${budget.toLocaleString()}</span>
        </div>
        <input
          type="range"
          min={50}
          max={10000}
          step={50}
          value={budget}
          onChange={(e) => setBudget(Number(e.target.value))}
          className="w-full h-1.5 rounded-full appearance-none bg-border cursor-pointer accent-purple-500"
        />
        <div className="flex justify-between text-[9px] text-text-secondary">
          <span>$50</span>
          <span>$10,000</span>
        </div>
      </div>

      {/* Find button */}
      <button
        onClick={handleFind}
        disabled={loading}
        className="w-full py-1.5 text-xs font-semibold rounded bg-purple-700/80 text-white hover:bg-purple-600/80 disabled:opacity-40 transition-colors"
      >
        {loading ? 'Searching...' : 'Find Strikes'}
      </button>

      {/* Error */}
      {error && <p className="text-xs text-red-400">{error}</p>}

      {/* Results */}
      {result && (
        <div className="space-y-2">
          {result.current_price != null && (
            <p className="text-xs text-text-secondary">
              Current price: <span className="font-mono text-text-primary">${result.current_price.toFixed(2)}</span>
            </p>
          )}

          {/* Risk tabs */}
          <div className="flex gap-1">
            {RISK_LEVELS.map((level) => {
              const has = hasResults(level);
              return (
                <button
                  key={level}
                  onClick={() => setActiveTab(level)}
                  className={`flex-1 px-2 py-1 text-[10px] font-semibold rounded capitalize transition-colors ${
                    activeTab === level
                      ? 'bg-purple-900/60 text-purple-300 border border-purple-600/60'
                      : 'bg-card border border-border text-text-secondary hover:text-text-primary'
                  }`}
                >
                  {level}
                  {has !== null && (
                    <span className={`inline-block w-1.5 h-1.5 rounded-full ml-1.5 align-middle ${has ? 'bg-green-400' : 'bg-gray-600'}`} />
                  )}
                </button>
              );
            })}
          </div>

          {/* Active tab results */}
          {activePair && (
            <div className="space-y-2">
              {activePair.recommended_call ? (
                <StrikeCard rec={activePair.recommended_call} type="CALL" />
              ) : (
                <p className="text-xs text-text-secondary italic">No call strikes found — try increasing budget</p>
              )}
              {activePair.recommended_put ? (
                <StrikeCard rec={activePair.recommended_put} type="PUT" />
              ) : (
                <p className="text-xs text-text-secondary italic">No put strikes found — try increasing budget</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
