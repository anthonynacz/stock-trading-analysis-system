import { useState, useEffect } from 'react';
import type { WatchlistStrikesResult, StrikeRecommendation } from '../types';
import {
  getWatchlistStrikes,
  saveStrikeSnapshot,
  getStrikeSnapshot,
} from '../utils/api';

const RISK_LEVELS = ['conservative', 'moderate', 'aggressive'] as const;
type RiskLevel = (typeof RISK_LEVELS)[number];

interface WatchlistStrikesProps {
  selectedDate: string;
}

function MiniStrikeCard({
  rec,
  type,
}: {
  rec: StrikeRecommendation;
  type: 'CALL' | 'PUT';
}) {
  const isCall = type === 'CALL';
  const borderColor = isCall ? 'border-green-600/40' : 'border-red-600/40';
  const labelColor = isCall ? 'text-green-400' : 'text-red-400';
  const bgColor = isCall ? 'bg-green-900/10' : 'bg-red-900/10';

  return (
    <div className={`${bgColor} border ${borderColor} rounded p-2`}>
      <div className="flex items-center justify-between mb-1">
        <span className={`text-[10px] font-bold uppercase ${labelColor}`}>{type}</span>
        <span className="text-[10px] text-text-secondary">{rec.days_to_expiry}d</span>
      </div>
      <div className="grid grid-cols-3 gap-x-2 text-[11px]">
        <div>
          <span className="text-text-secondary">Strike </span>
          <span className="font-mono text-text-primary">${rec.strike.toFixed(0)}</span>
        </div>
        <div>
          <span className="text-text-secondary">Prem </span>
          <span className="font-mono text-text-primary">${rec.premium_estimate.toFixed(2)}</span>
        </div>
        <div>
          <span className="text-text-secondary">Delta </span>
          <span className="font-mono text-text-primary">{rec.delta_estimate.toFixed(2)}</span>
        </div>
      </div>
      <div className="text-[10px] text-text-secondary mt-1">
        Breakeven ${rec.breakeven.toFixed(2)} &middot; OI {rec.open_interest.toLocaleString()} &middot; Exp {rec.expiry}
      </div>
    </div>
  );
}

function TickerStrikeRow({
  ticker,
  data,
  riskLevel,
}: {
  ticker: string;
  data: { current_price: number | null; [key: string]: unknown };
  riskLevel: RiskLevel;
}) {
  const pair = data[riskLevel] as {
    recommended_call: StrikeRecommendation | null;
    recommended_put: StrikeRecommendation | null;
  } | undefined;

  const hasCall = pair?.recommended_call != null;
  const hasPut = pair?.recommended_put != null;

  if (!hasCall && !hasPut) return null;

  return (
    <div className="bg-card border border-border rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-text-primary">{ticker}</span>
          {data.current_price != null && (
            <span className="text-xs text-text-secondary font-mono">
              ${(data.current_price as number).toFixed(2)}
            </span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {hasCall && <MiniStrikeCard rec={pair!.recommended_call!} type="CALL" />}
        {hasPut && <MiniStrikeCard rec={pair!.recommended_put!} type="PUT" />}
      </div>
    </div>
  );
}

export default function WatchlistStrikes({ selectedDate }: WatchlistStrikesProps) {
  const [activeTab, setActiveTab] = useState<RiskLevel>('moderate');
  const [budget, setBudget] = useState(2000);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<WatchlistStrikesResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Snapshot state
  const [saving, setSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [snapshotLoaded, setSnapshotLoaded] = useState(false);

  const today = new Date().toISOString().slice(0, 10);
  const isToday = selectedDate === today;

  // When date changes, load saved snapshot for historical dates
  useEffect(() => {
    setResult(null);
    setError(null);
    setSaveMessage(null);
    setSnapshotLoaded(false);

    if (!isToday) {
      setLoading(true);
      getStrikeSnapshot(selectedDate)
        .then((data) => {
          setResult(data);
          setSnapshotLoaded(true);
        })
        .catch(() => {
          // No snapshot for this date — that's fine
        })
        .finally(() => setLoading(false));
    }
  }, [selectedDate, isToday]);

  const handleScan = async () => {
    setLoading(true);
    setError(null);
    setSaveMessage(null);
    setSnapshotLoaded(false);
    try {
      const data = await getWatchlistStrikes(budget);
      setResult(data);
    } catch {
      setError('Failed to scan watchlist strikes');
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!result) return;
    setSaving(true);
    setSaveMessage(null);
    try {
      const resp = await saveStrikeSnapshot(budget, result.results);
      setSaveMessage(`Saved ${resp.tickers_saved} tickers for ${resp.snapshot_date}`);
    } catch {
      setSaveMessage('Failed to save snapshot');
    } finally {
      setSaving(false);
    }
  };

  // Count how many tickers have results for each risk level
  const countForLevel = (level: RiskLevel): number => {
    if (!result) return 0;
    return Object.values(result.results).filter((data) => {
      const pair = data[level] as {
        recommended_call: StrikeRecommendation | null;
        recommended_put: StrikeRecommendation | null;
      } | undefined;
      return pair?.recommended_call != null || pair?.recommended_put != null;
    }).length;
  };

  const tickers = result ? Object.keys(result.results).sort() : [];

  return (
    <div className="space-y-4">
      {/* Controls — only show for today */}
      {isToday && (
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex flex-col sm:flex-row items-start sm:items-end gap-4">
            {/* Budget slider */}
            <div className="flex-1 w-full sm:w-auto space-y-1">
              <div className="flex justify-between text-xs text-text-secondary">
                <span>Max Budget per Contract</span>
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

            {/* Scan + Save buttons */}
            <div className="flex gap-2">
              <button
                onClick={handleScan}
                disabled={loading}
                className="px-4 py-2 text-sm font-semibold rounded bg-purple-700/80 text-white hover:bg-purple-600/80 disabled:opacity-40 transition-colors whitespace-nowrap"
              >
                {loading ? 'Scanning...' : 'Scan Watchlist'}
              </button>
              {result && !snapshotLoaded && (
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-4 py-2 text-sm font-semibold rounded bg-green-800/60 text-green-300 hover:bg-green-700/60 disabled:opacity-40 transition-colors whitespace-nowrap"
                >
                  {saving ? 'Saving...' : 'Save Snapshot'}
                </button>
              )}
            </div>
          </div>

          {saveMessage && (
            <p className={`text-xs mt-2 ${saveMessage.startsWith('Failed') ? 'text-red-400' : 'text-green-400'}`}>
              {saveMessage}
            </p>
          )}
        </div>
      )}

      {/* Historical date info */}
      {!isToday && !loading && snapshotLoaded && result && (
        <div className="bg-card border border-border rounded-lg px-4 py-2">
          <p className="text-xs text-text-secondary">
            Saved snapshot from {selectedDate}
            {'budget' in result && (result as unknown as { budget: number | null }).budget != null && (
              <> &middot; Budget: ${(result as unknown as { budget: number }).budget.toLocaleString()}</>
            )}
          </p>
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {loading && (
        <div className="flex items-center justify-center py-6">
          <div className="w-5 h-5 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
          <span className="ml-2 text-sm text-text-secondary">Loading...</span>
        </div>
      )}

      {/* No snapshot for historical date */}
      {!isToday && !loading && !snapshotLoaded && (
        <p className="text-sm text-text-secondary text-center py-6">
          No saved snapshot for {selectedDate}
        </p>
      )}

      {/* Results */}
      {result && !loading && (
        <div className="space-y-3">
          {/* Summary + risk tabs */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <p className="text-xs text-text-secondary">
              {result.scanned} tickers &middot; {result.with_results} with options data
            </p>
            <div className="flex gap-1">
              {RISK_LEVELS.map((level) => {
                const count = countForLevel(level);
                return (
                  <button
                    key={level}
                    onClick={() => setActiveTab(level)}
                    className={`px-3 py-1.5 text-xs font-semibold rounded capitalize transition-colors ${
                      activeTab === level
                        ? 'bg-purple-900/60 text-purple-300 border border-purple-600/60'
                        : 'bg-card border border-border text-text-secondary hover:text-text-primary'
                    }`}
                  >
                    {level}
                    <span className={`ml-1.5 text-[10px] ${activeTab === level ? 'text-purple-400' : 'text-text-secondary'}`}>
                      ({count})
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Ticker cards */}
          {tickers.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {tickers.map((ticker) => (
                <TickerStrikeRow
                  key={ticker}
                  ticker={ticker}
                  data={result.results[ticker] as { current_price: number | null }}
                  riskLevel={activeTab}
                />
              ))}
            </div>
          ) : (
            <p className="text-sm text-text-secondary text-center py-4">
              No strike recommendations found — try increasing the budget
            </p>
          )}
        </div>
      )}
    </div>
  );
}
