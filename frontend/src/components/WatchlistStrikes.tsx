import { useState, useEffect } from 'react';
import type { StrikeAllResult, StrikeSnapshotResult, WatchlistStrikesResult } from '../types';
import {
  getWatchlistStrikes,
  saveStrikeSnapshot,
  getStrikeSnapshot,
} from '../utils/api';
import { fmtPrice } from '../utils/format';
import { RiskLevel } from '../utils/options';
import { BudgetSlider } from './BudgetSlider';
import { RiskLevelTabs } from './RiskLevelTabs';
import { StrikeCard } from './StrikeCard';
import { LoadingRow } from './ui/feedback';

interface WatchlistStrikesProps {
  selectedDate: string;
}

function TickerStrikeRow({
  ticker,
  data,
  riskLevel,
}: {
  ticker: string;
  data: StrikeAllResult;
  riskLevel: RiskLevel;
}) {
  const pair = data[riskLevel];
  if (!pair.recommended_call && !pair.recommended_put) return null;

  return (
    <div className="bg-card border border-border rounded-lg p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-text-primary">{ticker}</span>
          {data.current_price != null && (
            <span className="text-xs text-text-secondary font-mono">{fmtPrice(data.current_price)}</span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {pair.recommended_call && <StrikeCard rec={pair.recommended_call} type="CALL" variant="mini" />}
        {pair.recommended_put && <StrikeCard rec={pair.recommended_put} type="PUT" variant="mini" />}
      </div>
    </div>
  );
}

export default function WatchlistStrikes({ selectedDate }: WatchlistStrikesProps) {
  const [activeTab, setActiveTab] = useState<RiskLevel>('moderate');
  const [budget, setBudget] = useState(2000);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<WatchlistStrikesResult | StrikeSnapshotResult | null>(null);
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
    return Object.values(result.results).filter(
      (data) => data[level].recommended_call != null || data[level].recommended_put != null,
    ).length;
  };

  const tickers = result ? Object.keys(result.results).sort() : [];
  const snapshotBudget = result && 'snapshot_date' in result ? result.budget : null;

  return (
    <div className="space-y-4">
      {/* Controls — only show for today */}
      {isToday && (
        <div className="bg-card border border-border rounded-lg p-4">
          <div className="flex flex-col sm:flex-row items-start sm:items-end gap-4">
            <BudgetSlider
              value={budget}
              onChange={setBudget}
              label="Max Budget per Contract"
              size="md"
              className="flex-1 w-full sm:w-auto"
            />

            <div className="flex gap-2">
              <button onClick={handleScan} disabled={loading} className="btn-primary whitespace-nowrap">
                {loading ? 'Scanning...' : 'Scan Watchlist'}
              </button>
              {result && !snapshotLoaded && (
                <button onClick={handleSave} disabled={saving} className="btn-secondary whitespace-nowrap">
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
            {snapshotBudget != null && <> &middot; Budget: {fmtPrice(snapshotBudget, 0)}</>}
          </p>
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {loading && <LoadingRow label="Loading..." py="py-6" />}

      {/* No snapshot for historical date */}
      {!isToday && !loading && !snapshotLoaded && (
        <p className="text-sm text-text-secondary text-center py-6">
          No saved snapshot for {selectedDate}
        </p>
      )}

      {/* Results */}
      {result && !loading && (
        <div className="space-y-3">
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <p className="text-xs text-text-secondary">
              {result.scanned} tickers &middot; {result.with_results} with options data
            </p>
            <RiskLevelTabs
              active={activeTab}
              onChange={setActiveTab}
              indicator={(level, isActive) => (
                <span className={`ml-1.5 text-[10px] ${isActive ? 'text-accent-400' : 'text-text-secondary'}`}>
                  ({countForLevel(level)})
                </span>
              )}
            />
          </div>

          {tickers.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {tickers.map((ticker) => (
                <TickerStrikeRow
                  key={ticker}
                  ticker={ticker}
                  data={result.results[ticker]}
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
