import { useState, useEffect, useCallback, useRef } from 'react';
import type { Recommendation } from '../types';
import { getTickerRecommendations } from '../utils/api';
import { fmtPrice, fmtSigned } from '../utils/format';
import { ActionBadge, RiskBadge, SignedScore } from './ui/badges';
import { ConvictionBar } from './ui/ConvictionBar';
import { SignalBullet } from './SignalBullet';

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
  return (
    <span className={`text-[9px] font-mono ${diff > 0 ? 'text-green-400' : 'text-red-400'}`}>
      {fmtSigned(diff, 1)}
    </span>
  );
}

/* ── Summary column (compact) ───────────────────────────────────────────── */

function SummaryColumn({ rec, prevRec }: { rec: Recommendation; prevRec: Recommendation | null }) {
  const conviction = rec.conviction_score ?? 0;
  const actionChanged = prevRec && prevRec.action !== rec.action;

  return (
    <div className="flex-1 min-w-0 space-y-1.5">
      <p className="text-[10px] text-text-secondary text-center font-medium">
        {formatDate(rec.recommendation_date)}
      </p>
      <div className="flex justify-center">
        <ActionBadge action={rec.action} size="xxs" className={actionChanged ? 'ring-1 ring-white/30' : ''} />
      </div>
      <div className="text-center">
        <SignedScore value={conviction} className="text-xs font-semibold" />
        {prevRec?.conviction_score != null && (
          <div className="mt-px">
            <ChangeIndicator prev={prevRec.conviction_score} curr={conviction} />
          </div>
        )}
      </div>
      <ConvictionBar score={conviction} thickness="h-1" className="mx-1" />
      <div className="text-center">
        {rec.current_price != null ? (
          <>
            <p className="text-[10px] font-mono text-text-primary">{fmtPrice(rec.current_price)}</p>
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
          <RiskBadge level={rec.risk_level} size="xxs" />
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
        <ActionBadge action={rec.action} size="xxs" className={actionChanged ? 'ring-1 ring-white/30' : ''} />
        <SignedScore value={conviction} className="text-[10px] font-semibold" />
        {prevRec?.conviction_score != null && (
          <ChangeIndicator prev={prevRec.conviction_score} curr={conviction} />
        )}
      </div>

      {/* Price + Risk row */}
      <div className="flex items-center justify-center gap-1.5">
        {rec.current_price != null && (
          <span className="text-[10px] font-mono text-text-primary">{fmtPrice(rec.current_price)}</span>
        )}
        {rec.current_price != null && prevRec?.current_price != null && (
          <ChangeIndicator prev={prevRec.current_price} curr={rec.current_price} />
        )}
        {rec.risk_level && <RiskBadge level={rec.risk_level} size="xxs" />}
      </div>

      {/* Signals list */}
      {rec.signals && rec.signals.length > 0 && (
        <ul className="space-y-0.5 pt-0.5">
          {rec.signals.map((sig, i) => (
            <SignalBullet
              key={i}
              signal={sig}
              compact
              detail
              highlight={prevRec != null && !prevSignals.has(sig.signal)}
            />
          ))}
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
  // Request id: a slow response for a previous ticker must not overwrite a newer one.
  const seq = useRef(0);

  const fetchHistory = useCallback(async () => {
    const id = ++seq.current;
    try {
      setLoading(true);
      const data = await getTickerRecommendations(ticker);
      if (id !== seq.current) return;
      // Take 5 most recent, reverse so oldest is first (left-to-right)
      setRecs(data.slice(0, 5).reverse());
    } catch {
      if (id !== seq.current) return;
      setRecs([]);
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [ticker]);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  useEffect(() => () => { seq.current++; }, []);

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
                  ? 'bg-accent-900/60 text-accent-300 border border-accent-600/60'
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
