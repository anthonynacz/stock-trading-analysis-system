import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getWatchlist,
  getRecommendations,
  getNews,
  getCatalysts,
  getStatus,
  getPipelineDates,
  getWatchlistChanges,
  getTickerTrends,
  getUniverse,
  getDiscoveryCandidates,
  getPositions,
  getRotationStatus,
  getApiErrorMessage,
  type RecommendationSort,
} from '../utils/api';
import type {
  WatchlistItem,
  Recommendation,
  NewsItem,
  CatalystEvent,
  SystemStatus,
  WatchlistChanges,
  PipelineDates,
  TrendData,
  UniverseSummary,
  DiscoveryCandidate,
  Position,
  RotationStatus,
} from '../types';
import { usePolling } from './usePolling';

interface RefetchOptions {
  /** Skip the loading spinner (poll ticks); foreground fetches show it. */
  background?: boolean;
}

interface HookResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: (opts?: RefetchOptions) => void;
}

interface QueryOptions {
  /** Poll interval in ms; `null` disables polling. */
  pollMs: number | null;
  /**
   * After the first successful load, keep the previous object when the
   * payload is unchanged and never touch `loading` again — used for /status
   * so the 30s tick does not re-render the whole Dashboard.
   */
  dedupe?: boolean;
}

/**
 * Shared fetch/poll state machine behind the list hooks. `fetcher` must be a
 * stable `useCallback` keyed on the hook's params — its identity drives the
 * foreground refetch on param change. Out-of-order responses are dropped via
 * a per-hook sequence counter, which is also bumped on unmount.
 */
function usePolledQuery<T>(
  fetcher: () => Promise<T>,
  fallback: string,
  { pollMs, dedupe = false }: QueryOptions,
): HookResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const loaded = useRef(false);
  const first = useRef(true);

  const refetch = useCallback(
    async (opts?: RefetchOptions) => {
      const id = ++seq.current;
      const quiet = dedupe && loaded.current;
      if (!opts?.background && !quiet) setLoading(true);
      try {
        const result = await fetcher();
        if (id !== seq.current) return;
        if (dedupe) {
          setData((prev) =>
            prev && JSON.stringify(prev) === JSON.stringify(result) ? prev : result,
          );
        } else {
          setData(result);
        }
        loaded.current = true;
        setError(null);
      } catch (e) {
        if (id !== seq.current) return;
        setError(getApiErrorMessage(e, fallback));
      } finally {
        if (id === seq.current && !quiet) setLoading(false);
      }
    },
    [fetcher, fallback, dedupe],
  );

  useEffect(() => () => { seq.current++; }, []);

  useEffect(() => {
    if (first.current) {
      first.current = false;
    } else {
      // Param change: drop the previous params' payload so `loading && !data`
      // gates show a spinner again. Background ticks never change `refetch`.
      loaded.current = false;
      setData(null);
      setError(null);
    }
    refetch();
  }, [refetch]);

  usePolling(refetch, pollMs);

  return { data, loading, error, refetch };
}

const isTodayDate = (date?: string) =>
  !date || date === new Date().toISOString().slice(0, 10);

export function useWatchlist(sector?: string, date?: string): HookResult<WatchlistItem[]> {
  const fetcher = useCallback(() => getWatchlist(sector, date), [sector, date]);
  return usePolledQuery(fetcher, 'Failed to fetch watchlist', {
    pollMs: isTodayDate(date) ? 60_000 : null,
  });
}

export function useRecommendations(
  action?: string,
  minConviction?: number,
  date?: string,
  sort?: RecommendationSort,
): HookResult<Recommendation[]> {
  const fetcher = useCallback(
    () => getRecommendations(action, minConviction, date, sort),
    [action, minConviction, date, sort],
  );
  return usePolledQuery(fetcher, 'Failed to fetch recommendations', {
    pollMs: isTodayDate(date) ? 60_000 : null,
  });
}

export function useNews(filters?: {
  ticker?: string;
  mode?: string;
  category?: string;
  impact_level?: string;
  industry?: string;
  min_relevance?: number;
  limit?: number;
}): HookResult<NewsItem[]> {
  const fetcher = useCallback(
    () => getNews(filters),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filters?.ticker, filters?.mode, filters?.category, filters?.impact_level, filters?.industry, filters?.min_relevance, filters?.limit],
  );
  return usePolledQuery(fetcher, 'Failed to fetch news', { pollMs: 120_000 });
}

export function useCatalysts(): HookResult<CatalystEvent[]> {
  const fetcher = useCallback(() => getCatalysts(), []);
  return usePolledQuery(fetcher, 'Failed to fetch catalysts', { pollMs: 300_000 });
}

export function useStatus(): HookResult<SystemStatus> {
  const fetcher = useCallback(() => getStatus(), []);
  return usePolledQuery(fetcher, 'Failed to fetch system status', {
    pollMs: 30_000,
    dedupe: true,
  });
}

/**
 * Polls the rotate-out background analysis state every ~2s while `active`,
 * and stops once every rotated-in ticker reaches a terminal state
 * (`done`/`error`) and the worker is no longer running.
 */
export function useRotationStatus(active: boolean): HookResult<RotationStatus> {
  const [data, setData] = useState<RotationStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      const s = await getRotationStatus();
      setData(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch rotation status');
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    let interval: ReturnType<typeof setInterval>;
    const poll = async () => {
      try {
        const s = await getRotationStatus();
        setData(s);
        setError(null);
        const states = Object.values(s.tickers);
        const allTerminal =
          states.length > 0 && states.every((st) => st === 'done' || st === 'error');
        if (!s.running && allTerminal) clearInterval(interval);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to fetch rotation status');
      } finally {
        setLoading(false);
      }
    };
    setLoading(true);
    poll();
    interval = setInterval(poll, 2_000);
    return () => clearInterval(interval);
  }, [active]);

  return { data, loading, error, refetch };
}

export function usePipelineDates(): HookResult<PipelineDates> {
  const fetcher = useCallback(() => getPipelineDates(), []);
  return usePolledQuery(fetcher, 'Failed to fetch pipeline dates', { pollMs: 300_000 });
}

export function useWatchlistChanges(date?: string): HookResult<WatchlistChanges> {
  const fetcher = useCallback(() => getWatchlistChanges(date), [date]);
  return usePolledQuery(fetcher, 'Failed to fetch watchlist changes', {
    pollMs: isTodayDate(date) ? 60_000 : null,
  });
}

export function useTickerTrends(
  ticker: string | null,
  days = 20,
  sma = 5,
): HookResult<TrendData> {
  const [data, setData] = useState<TrendData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const refetch = useCallback(async (opts?: RefetchOptions) => {
    if (!ticker) return;
    const id = ++seq.current;
    if (!opts?.background) setLoading(true);
    try {
      const result = await getTickerTrends(ticker, days, sma);
      if (id !== seq.current) return;
      setData(result);
      setError(null);
    } catch (e) {
      if (id !== seq.current) return;
      setError(getApiErrorMessage(e, 'Failed to fetch trends'));
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [ticker, days, sma]);

  useEffect(() => () => { seq.current++; }, []);

  useEffect(() => {
    if (ticker) {
      refetch();
    } else {
      seq.current++;
      setData(null);
      setError(null);
      setLoading(false);
    }
  }, [ticker, refetch]);

  return { data, loading, error, refetch };
}

export function useUniverse(): HookResult<UniverseSummary> {
  const fetcher = useCallback(() => getUniverse(), []);
  return usePolledQuery(fetcher, 'Failed to fetch universe', { pollMs: 120_000 });
}

export function useDiscoveryCandidates(): HookResult<DiscoveryCandidate[]> {
  const fetcher = useCallback(() => getDiscoveryCandidates(), []);
  return usePolledQuery(fetcher, 'Failed to fetch candidates', { pollMs: 60_000 });
}

export function usePositions(status?: string): HookResult<Position[]> {
  const fetcher = useCallback(() => getPositions(status), [status]);
  return usePolledQuery(fetcher, 'Failed to fetch positions', { pollMs: 60_000 });
}
