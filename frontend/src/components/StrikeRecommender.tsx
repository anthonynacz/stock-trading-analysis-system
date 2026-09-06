import { useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import type { StrikeAllResult } from '../types';
import { getStrikeRecommendationsAll } from '../utils/api';
import { fmtPrice } from '../utils/format';
import { RiskLevel } from '../utils/options';
import { BudgetSlider } from './BudgetSlider';
import { RiskLevelTabs } from './RiskLevelTabs';
import { StrikeCard } from './StrikeCard';

interface StrikeRecommenderProps {
  ticker: string;
}

export default function StrikeRecommender({ ticker }: StrikeRecommenderProps) {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState<RiskLevel>(
    (user?.risk_profile as RiskLevel) ?? 'moderate',
  );
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

  const hasResults = (level: RiskLevel): boolean => {
    const pair = result ? result[level] : null;
    return pair != null && (pair.recommended_call !== null || pair.recommended_put !== null);
  };

  return (
    <div className="space-y-3">
      <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
        Strike Recommender
      </h4>

      <BudgetSlider value={budget} onChange={setBudget} />

      <button onClick={handleFind} disabled={loading} className="btn-primary w-full justify-center">
        {loading ? 'Searching...' : 'Find Strikes'}
      </button>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {result && (
        <div className="space-y-2">
          {result.current_price != null && (
            <p className="text-xs text-text-secondary">
              Current price: <span className="font-mono text-text-primary">{fmtPrice(result.current_price)}</span>
            </p>
          )}

          <RiskLevelTabs
            active={activeTab}
            onChange={setActiveTab}
            size="xs"
            fullWidth
            indicator={(level) => {
              const has = hasResults(level);
              return (
                <span
                  role="img"
                  title={has ? 'Strikes found' : 'No strikes in budget'}
                  aria-label={`${level}: ${has ? 'results' : 'no results'}`}
                  className={`inline-block w-1.5 h-1.5 rounded-full ml-1.5 align-middle ${has ? 'bg-green-400' : 'bg-gray-600'}`}
                />
              );
            }}
          />

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
