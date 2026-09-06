import { createContext, useContext, useMemo, useState, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import type { ResearchResult } from '../types';
import { analyzeResearch, getApiErrorMessage } from '../utils/api';

interface ResearchContextType {
  /** Ticker currently being analyzed, or null if idle */
  analyzingTicker: string | null;
  /** Error from the last analysis attempt */
  analyzeError: string | null;
  /** Result produced by the last completed analysis (consumed once by the page) */
  lastResult: ResearchResult | null;
  /** Kick off a new analysis. No-op if one is already running. */
  startAnalysis: (ticker: string) => void;
  /** Clear the last result after the page has consumed it */
  consumeResult: () => void;
  /** Clear error */
  clearError: () => void;
}

const ResearchContext = createContext<ResearchContextType | null>(null);

export function ResearchProvider({ children }: { children: ReactNode }) {
  const [analyzingTicker, setAnalyzingTicker] = useState<string | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<ResearchResult | null>(null);
  const runningRef = useRef(false);

  const startAnalysis = useCallback((ticker: string) => {
    if (runningRef.current) return;
    runningRef.current = true;
    const upper = ticker.trim().toUpperCase();
    setAnalyzingTicker(upper);
    setAnalyzeError(null);
    setLastResult(null);

    analyzeResearch(upper)
      .then((result) => {
        setLastResult(result);
      })
      .catch((err: unknown) => {
        setAnalyzeError(getApiErrorMessage(err, 'Analysis failed'));
      })
      .finally(() => {
        setAnalyzingTicker(null);
        runningRef.current = false;
      });
  }, []);

  const consumeResult = useCallback(() => setLastResult(null), []);
  const clearError = useCallback(() => setAnalyzeError(null), []);

  const value = useMemo<ResearchContextType>(
    () => ({
      analyzingTicker,
      analyzeError,
      lastResult,
      startAnalysis,
      consumeResult,
      clearError,
    }),
    [analyzingTicker, analyzeError, lastResult, startAnalysis, consumeResult, clearError],
  );

  return <ResearchContext.Provider value={value}>{children}</ResearchContext.Provider>;
}

export function useResearchContext() {
  const ctx = useContext(ResearchContext);
  if (!ctx) throw new Error('useResearchContext must be used within ResearchProvider');
  return ctx;
}
