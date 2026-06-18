import { useEffect, useState } from 'react';
import { previewRotateOut, commitRotateOut } from '../utils/api';
import { useRotationStatus } from '../hooks/useEdgeFlow';
import type { RotationPreviewItem, RotationCommitPair } from '../types';

interface RotationPreviewModalProps {
  tickers: string[];
  onClose: () => void;
  onCommitted: () => void;
}

type Phase = 'loading' | 'preview' | 'committing' | 'progress' | 'error';

export default function RotationPreviewModal({ tickers, onClose, onCommitted }: RotationPreviewModalProps) {
  const [phase, setPhase] = useState<Phase>('loading');
  const [items, setItems] = useState<RotationPreviewItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [committedAdds, setCommittedAdds] = useState<string[]>([]);

  const rotation = useRotationStatus(phase === 'progress');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await previewRotateOut(tickers);
        if (!cancelled) {
          setItems(res.items);
          setPhase('preview');
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load preview');
          setPhase('error');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [tickers]);

  const allowed = items.filter((it) => !it.gate.blocked && it.candidate);
  const blocked = items.filter((it) => it.gate.blocked);
  const noCandidate = items.filter((it) => !it.gate.blocked && !it.candidate);

  const handleConfirm = async () => {
    const pairs: RotationCommitPair[] = allowed.map((it) => ({
      remove: it.ticker,
      add: it.candidate!.ticker,
    }));
    if (pairs.length === 0) return;
    setPhase('committing');
    try {
      const res = await commitRotateOut(pairs);
      if (res.status === 'already_running') {
        setError('A rotation is already running — try again in a moment.');
        setPhase('error');
        return;
      }
      if (res.committed.length === 0) {
        setError('No tickers were rotated (all blocked on the final check).');
        setPhase('error');
        return;
      }
      setCommittedAdds(res.committed.map((c) => c.add));
      setPhase('progress');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to commit rotation');
      setPhase('error');
    }
  };

  const tickerStates = rotation.data?.tickers ?? {};
  const allDone =
    committedAdds.length > 0 &&
    committedAdds.every((t) => {
      const s = tickerStates[t];
      return s === 'done' || s === 'error';
    });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="bg-card border border-border rounded-lg shadow-xl w-full max-w-lg max-h-[85vh] overflow-y-auto p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-text-primary">Rotate out</h3>
          <button
            onClick={onClose}
            className="text-text-secondary hover:text-text-primary text-xl leading-none"
          >
            ×
          </button>
        </div>

        {phase === 'loading' && (
          <div className="flex items-center justify-center py-10">
            <div className="w-5 h-5 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
            <span className="ml-2 text-sm text-text-secondary">Scoring candidates…</span>
          </div>
        )}

        {phase === 'error' && (
          <div className="py-4">
            <p className="text-sm text-red-400">{error}</p>
            <div className="mt-4 flex justify-end">
              <button
                onClick={onClose}
                className="px-3 py-1.5 text-xs font-semibold rounded bg-border text-text-primary"
              >
                Close
              </button>
            </div>
          </div>
        )}

        {(phase === 'preview' || phase === 'committing') && (
          <>
            {allowed.length > 0 && (
              <div className="space-y-2 mb-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Proposed swaps
                </p>
                {allowed.map((it) => (
                  <div
                    key={it.ticker}
                    className="flex items-center gap-2 bg-page border border-border rounded p-2 text-sm"
                  >
                    <span className="font-bold text-sell line-through">{it.ticker}</span>
                    <span className="text-text-secondary">→</span>
                    <span className="font-bold text-new-entrant">{it.candidate!.ticker}</span>
                    <span className="text-[10px] text-text-secondary ml-auto text-right">
                      score {it.candidate!.composite_score.toFixed(1)}
                      {it.candidate!.cross_sector && ' · cross-sector'}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {blocked.length > 0 && (
              <div className="space-y-2 mb-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-red-400">
                  Blocked — strong imminent potential
                </p>
                {blocked.map((it) => (
                  <div
                    key={it.ticker}
                    className="bg-red-900/20 border border-red-900/40 rounded p-2 text-sm"
                  >
                    <span className="font-bold text-text-primary">{it.ticker}</span>
                    <span className="text-xs text-red-300 ml-2">
                      {it.gate.reasons.join('; ')}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {noCandidate.length > 0 && (
              <div className="space-y-2 mb-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-amber-400">
                  No eligible replacement
                </p>
                {noCandidate.map((it) => (
                  <div
                    key={it.ticker}
                    className="bg-amber-900/10 border border-amber-900/30 rounded p-2 text-sm"
                  >
                    <span className="font-bold text-text-primary">{it.ticker}</span>
                    <span className="text-xs text-text-secondary ml-2">
                      no candidate available — will be skipped
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={onClose}
                className="px-3 py-1.5 text-xs font-semibold rounded bg-border text-text-secondary hover:text-text-primary"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirm}
                disabled={allowed.length === 0 || phase === 'committing'}
                className="px-3 py-1.5 text-xs font-semibold rounded bg-amber-500 text-black hover:bg-amber-400 disabled:opacity-40"
              >
                {phase === 'committing'
                  ? 'Committing…'
                  : `Confirm ${allowed.length} swap${allowed.length === 1 ? '' : 's'}`}
              </button>
            </div>
          </>
        )}

        {phase === 'progress' && (
          <div className="py-2">
            <p className="text-sm text-text-secondary mb-3">
              Generating recommendations for the new tickers…
            </p>
            <div className="space-y-2">
              {committedAdds.map((t) => {
                const s = tickerStates[t] ?? 'pending';
                const color =
                  s === 'done' ? 'text-green-400'
                    : s === 'error' ? 'text-red-400'
                      : s === 'analyzing' ? 'text-amber-400'
                        : 'text-text-secondary';
                return (
                  <div
                    key={t}
                    className="flex items-center justify-between bg-page border border-border rounded p-2 text-sm"
                  >
                    <span className="font-bold text-text-primary">{t}</span>
                    <span className={`text-xs font-semibold flex items-center ${color}`}>
                      {s === 'analyzing' && (
                        <span className="inline-block w-3 h-3 mr-1 border-2 border-amber-400 border-t-transparent rounded-full animate-spin" />
                      )}
                      {s}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="mt-4 flex justify-end">
              <button
                onClick={onCommitted}
                disabled={!allDone}
                className="px-3 py-1.5 text-xs font-semibold rounded bg-new-entrant text-black hover:opacity-90 disabled:opacity-40"
              >
                {allDone ? 'Done' : 'Working…'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
