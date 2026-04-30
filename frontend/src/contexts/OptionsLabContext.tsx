import { createContext, useContext, useState, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import type { DeepOptionsAnalysis } from '../types';
import { analyzeDeepOptions } from '../utils/api';

interface BatchProgress {
  current: number;
  total: number;
  ticker: string;
}

interface OptionsLabContextType {
  analyzingTicker: string | null;
  analyzeError: string | null;
  lastResult: DeepOptionsAnalysis | null;
  batchProgress: BatchProgress | null;
  startAnalysis: (ticker: string) => void;
  refreshAll: (tickers: string[]) => Promise<void>;
  cancelBatch: () => void;
  consumeResult: () => void;
  clearError: () => void;
}

const OptionsLabContext = createContext<OptionsLabContextType | null>(null);

export function OptionsLabProvider({ children }: { children: ReactNode }) {
  const [analyzingTicker, setAnalyzingTicker] = useState<string | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<DeepOptionsAnalysis | null>(null);
  const [batchProgress, setBatchProgress] = useState<BatchProgress | null>(null);
  const runningRef = useRef(false);
  const cancelBatchRef = useRef(false);

  const extractError = (err: unknown): string => {
    if (err && typeof err === 'object' && 'response' in err) {
      const detail = (err as { response: { data: { detail: string } } }).response
        ?.data?.detail;
      if (detail) return detail;
    }
    return 'Deep options analysis failed';
  };

  const startAnalysis = useCallback((ticker: string) => {
    if (runningRef.current) return;
    runningRef.current = true;
    const upper = ticker.trim().toUpperCase();
    setAnalyzingTicker(upper);
    setAnalyzeError(null);
    setLastResult(null);

    analyzeDeepOptions(upper)
      .then((result) => {
        setLastResult(result);
      })
      .catch((err: unknown) => {
        setAnalyzeError(extractError(err));
      })
      .finally(() => {
        setAnalyzingTicker(null);
        runningRef.current = false;
      });
  }, []);

  const refreshAll = useCallback(async (tickers: string[]) => {
    if (runningRef.current) return;
    const unique = Array.from(
      new Set(tickers.map((t) => t.trim().toUpperCase()).filter(Boolean)),
    );
    if (unique.length === 0) return;

    runningRef.current = true;
    cancelBatchRef.current = false;
    setAnalyzeError(null);
    setLastResult(null);

    const failures: string[] = [];
    for (let i = 0; i < unique.length; i += 1) {
      if (cancelBatchRef.current) break;
      const t = unique[i];
      setAnalyzingTicker(t);
      setBatchProgress({ current: i + 1, total: unique.length, ticker: t });
      try {
        const result = await analyzeDeepOptions(t);
        setLastResult(result);
      } catch (err) {
        failures.push(`${t}: ${extractError(err)}`);
      }
    }

    setAnalyzingTicker(null);
    setBatchProgress(null);
    runningRef.current = false;
    cancelBatchRef.current = false;
    if (failures.length > 0) {
      setAnalyzeError(
        failures.length === 1
          ? failures[0]
          : `${failures.length} tickers failed — ${failures.slice(0, 2).join('; ')}${failures.length > 2 ? '…' : ''}`,
      );
    }
  }, []);

  const cancelBatch = useCallback(() => {
    cancelBatchRef.current = true;
  }, []);

  const consumeResult = useCallback(() => setLastResult(null), []);
  const clearError = useCallback(() => setAnalyzeError(null), []);

  return (
    <OptionsLabContext.Provider
      value={{
        analyzingTicker,
        analyzeError,
        lastResult,
        batchProgress,
        startAnalysis,
        refreshAll,
        cancelBatch,
        consumeResult,
        clearError,
      }}
    >
      {children}
    </OptionsLabContext.Provider>
  );
}

export function useOptionsLabContext() {
  const ctx = useContext(OptionsLabContext);
  if (!ctx) throw new Error('useOptionsLabContext must be used within OptionsLabProvider');
  return ctx;
}
