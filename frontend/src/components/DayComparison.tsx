import { useState, useEffect, useCallback } from 'react';
import type { Recommendation, SignalDetail } from '../types';
import { getTickerRecommendations } from '../utils/api';
import { ACTION_COLORS, getActionLabel } from '../utils/theme';

interface DayComparisonProps {
  ticker: string;
}

type Tab = 'summary' | 'detailed';

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function ChangeIndicator({ prev, curr }: { prev: number; curr: number }) {
  const diff = curr - prev;
  if (Math.abs(diff) < 0.01) return null;
  const isUp = diff > 0;
  return (
    <span className={`text-[9px] font-mono ${isUp ? 'text-green-400' : 'text-red-400'}`}>
      {isUp ? '+' : ''}{diff.toFixed(1)}
    </span>
  );
}

/* ── Summary column (compact) ───────────────────────────────────────────── */

function SummaryColumn({ rec, prevRec }: { rec: Recommendation; prevRec: Recommendation | null }) {
  const borderColor = ACTION_COLORS[rec.action] ?? '#21262d';
  const conviction = rec.conviction_score ?? 0;
  const actionChanged = prevRec && prevRec.action !== rec.action;

  return (
    <div className="flex-1 min-w-0 space-y-1.5">
      <p className="text-[10px] text-text-secondary text-center font-medium">
        {formatDate(rec.recommendation_date)}
      </p>
      <div className="flex justify-center">
        <span
          className={`px-1.5 py-px rounded text-[9px] font-bold uppercase ${actionChanged ? 'ring-1 ring-white/30' : ''}`}
          style={{ backgroundColor: borderColor + '22', color: borderColor }}
        >
          {getActionLabel(rec.action)}
        </span>
      </div>
      <div className="text-center">
        <span className={`text-xs font-mono font-semibold ${conviction >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {conviction > 0 ? '+' : ''}{conviction}
        </span>
        {prevRec?.conviction_score != null && (
          <div className="mt-px">
            <ChangeIndicator prev={prevRec.conviction_score} curr={conviction} />
          </div>
        )}
      </div>
      <div className="h-1 rounded-full bg-border overflow-hidden mx-1">
        <div
          className={`h-full rounded-full ${conviction >= 0 ? 'bg-green-500' : 'bg-red-500'}`}
          style={{ width: `${Math.min(Math.abs(conviction), 100)}%` }}
        />
      </div>
      <div className="text-center">
        {rec.current_price != null ? (
          <>
            <p className="text-[10px] font-mono text-text-primary">${rec.current_price.toFixed(2)}</p>
            {prevRec?.current_price != null && (
              <ChangeIndicator prev={prevRec.current_price} curr={rec.current_price} />
            )}
          </>
        ) : (
          <p className="text-[10px] text-text-secondary">—</p>
        )}
      </div>
      <p className="text-[9px] text-text-secondary text-center">
        {rec.signal_count ?? 0} sig{(rec.signal_count ?? 0) !== 1 ? 's' : ''}
      </p>
      {rec.risk_level && (
        <div className="flex justify-center">
          <span className={`text-[9px] px-1 py-px rounded ${
            rec.risk_level === 'LOW' ? 'bg-green-900/40 text-green-400' :
            rec.risk_level === 'HIGH' ? 'bg-red-900/40 text-red-400' :
            'bg-amber-900/40 text-amber-400'
          }`}>
            {rec.risk_level}
          </span>
        </div>
      )}
    </div>
  );
}

/* ── Detailed column (with signals) ─────────────────────────────────────── */

/** Build a set of signal names from the previous day for diffing */
function signalNameSet(rec: Recommendation | null): Set<string> {
  if (!rec?.signals) return new Set();
  return new Set(rec.signals.map((s) => s.signal));
}

function DetailedColumn({ rec, prevRec }: { rec: Recommendation; prevRec: Recommendation | null }) {
  const borderColor = ACTION_COLORS[rec.action] ?? '#21262d';
  const conviction = rec.conviction_score ?? 0;
  const actionChanged = prevRec && prevRec.action !== rec.action;
  const prevSignals = signalNameSet(prevRec);

  return (
    <div className="min-w-[140px] space-y-1.5 border-r border-border/30 last:border-r-0 px-2 first:pl-0 last:pr-0">
      {/* Date */}
      <p className="text-[10px] text-text-secondary text-center font-medium">
        {formatDate(rec.recommendation_date)}
      </p>

      {/* Action + Conviction row */}
      <div className="flex items-center justify-center gap-1.5">
        <span
          className={`px-1.5 py-px rounded text-[9px] font-bold uppercase ${actionChanged ? 'ring-1 ring-white/30' : ''}`}
          style={{ backgroundColor: borderColor + '22', color: borderColor }}
        >
          {getActionLabel(rec.action)}
        </span>
        <span className={`text-[10px] font-mono font-semibold ${conviction >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {conviction > 0 ? '+' : ''}{conviction}
        </span>
        {prevRec?.conviction_score != null && (
          <ChangeIndicator prev={prevRec.conviction_score} curr={conviction} />
        )}
      </div>

      {/* Price + Risk row */}
      <div className="flex items-center justify-center gap-1.5">
        {rec.current_price != null && (
          <span className="text-[10px] font-mono text-text-primary">${rec.current_price.toFixed(2)}</span>
        )}
        {rec.current_price != null && prevRec?.current_price != null && (
          <ChangeIndicator prev={prevRec.current_price} curr={rec.current_price} />
        )}
        {rec.risk_level && (
          <span className={`text-[8px] px-1 py-px rounded ${
            rec.risk_level === 'LOW' ? 'bg-green-900/40 text-green-400' :
            rec.risk_level === 'HIGH' ? 'bg-red-900/40 text-red-400' :
            'bg-amber-900/40 text-amber-400'
          }`}>
            {rec.risk_level}
          </span>
        )}
      </div>

      {/* Signals list */}
      {rec.signals && rec.signals.length > 0 && (
        <ul className="space-y-0.5 pt-0.5">
          {rec.signals.map((sig: SignalDetail, i: number) => {
            const isNew = prevRec && !prevSignals.has(sig.signal);
            const isPositive = sig.points > 0;
            return (
              <li key={i} className="text-[9px] leading-tight">
                <div className="flex items-start gap-1">
                  <span className={`font-mono font-bold shrink-0 w-5 text-right ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
                    {isPositive ? '+' : ''}{sig.points}
                  </span>
                  <span className={`text-text-primary ${isNew ? 'underline decoration-purple-500/60' : ''}`}>
                    {sig.signal}
                  </span>
                </div>
                {sig.detail && (
                  <p className="text-text-secondary ml-6 leading-tight">{sig.detail}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/* ── Main component ─────────────────────────────────────────────────────── */

export default function DayComparison({ ticker }: DayComparisonProps) {
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>('summary');

  const fetchHistory = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getTickerRecommendations(ticker);
      // Take 5 most recent, reverse so oldest is first (left-to-right)
      setRecs(data.slice(0, 5).reverse());
    } catch {
      setRecs([]);
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-3">
        <div className="w-3 h-3 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (recs.length < 2) return null;

  return (
    <div className="space-y-1.5">
      {/* Header + tab selector */}
      <div className="flex items-center justify-between">
        <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
          Day-by-Day ({recs.length}d)
        </h4>
        <div className="flex items-center gap-1">
          {(['summary', 'detailed'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-1.5 py-0.5 text-[10px] rounded transition-colors ${
                tab === t
                  ? 'bg-purple-900/60 text-purple-300 border border-purple-600/60'
                  : 'bg-card border border-border text-text-secondary hover:text-text-primary'
              }`}
            >
              {t === 'summary' ? 'Summary' : 'Detailed'}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      {tab === 'summary' ? (
        <div className="flex gap-px bg-border/30 rounded-lg overflow-hidden p-2">
          {recs.map((rec, i) => (
            <SummaryColumn
              key={rec.id}
              rec={rec}
              prevRec={i > 0 ? recs[i - 1] : null}
            />
          ))}
        </div>
      ) : (
        <div className="bg-border/30 rounded-lg overflow-x-auto p-2">
          <div className="flex min-w-0">
            {recs.map((rec, i) => (
              <DetailedColumn
                key={rec.id}
                rec={rec}
                prevRec={i > 0 ? recs[i - 1] : null}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
