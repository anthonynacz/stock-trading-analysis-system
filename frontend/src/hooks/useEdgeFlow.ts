import { useState, useEffect, useCallback } from 'react';
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
} from '../types';

interface HookResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useWatchlist(sector?: string, date?: string): HookResult<WatchlistItem[]> {
  const [data, setData] = useState<WatchlistItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const isToday = !date || date === new Date().toISOString().slice(0, 10);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getWatchlist(sector, date);
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch watchlist');
    } finally {
      setLoading(false);
    }
  }, [sector, date]);

  useEffect(() => {
    refetch();
    if (!isToday) return;
    const interval = setInterval(refetch, 60_000);
    return () => clearInterval(interval);
  }, [refetch, isToday]);

  return { data, loading, error, refetch };
}

export function useRecommendations(
  action?: string,
  minConviction?: number,
  date?: string,
  sort?: RecommendationSort,
): HookResult<Recommendation[]> {
  const [data, setData] = useState<Recommendation[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const isToday = !date || date === new Date().toISOString().slice(0, 10);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getRecommendations(action, minConviction, date, sort);
      setData(result);
      setError(null);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Failed to fetch recommendations',
      );
    } finally {
      setLoading(false);
    }
  }, [action, minConviction, date, sort]);

  useEffect(() => {
    refetch();
    if (!isToday) return;
    const interval = setInterval(refetch, 60_000);
    return () => clearInterval(interval);
  }, [refetch, isToday]);

  return { data, loading, error, refetch };
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
  const [data, setData] = useState<NewsItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getNews(filters);
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch news');
    } finally {
      setLoading(false);
    }
  }, [filters?.ticker, filters?.mode, filters?.category, filters?.impact_level, filters?.industry, filters?.min_relevance, filters?.limit]);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 120_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}

export function useCatalysts(): HookResult<CatalystEvent[]> {
  const [data, setData] = useState<CatalystEvent[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getCatalysts();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch catalysts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 300_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}

export function useStatus(): HookResult<SystemStatus> {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getStatus();
      setData(result);
      setError(null);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Failed to fetch system status',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 30_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}

export function usePipelineDates(): HookResult<PipelineDates> {
  const [data, setData] = useState<PipelineDates | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getPipelineDates();
      setData(result);
      setError(null);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Failed to fetch pipeline dates',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 300_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}

export function useWatchlistChanges(date?: string): HookResult<WatchlistChanges> {
  const [data, setData] = useState<WatchlistChanges | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const isToday = !date || date === new Date().toISOString().slice(0, 10);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getWatchlistChanges(date);
      setData(result);
      setError(null);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Failed to fetch watchlist changes',
      );
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    refetch();
    if (!isToday) return;
    const interval = setInterval(refetch, 60_000);
    return () => clearInterval(interval);
  }, [refetch, isToday]);

  return { data, loading, error, refetch };
}

export function useTickerTrends(
  ticker: string | null,
  days = 20,
  sma = 5,
): HookResult<TrendData> {
  const [data, setData] = useState<TrendData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!ticker) return;
    try {
      setLoading(true);
      const result = await getTickerTrends(ticker, days, sma);
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch trends');
    } finally {
      setLoading(false);
    }
  }, [ticker, days, sma]);

  useEffect(() => {
    if (ticker) {
      refetch();
    } else {
      setData(null);
    }
  }, [ticker, refetch]);

  return { data, loading, error, refetch };
}

export function useUniverse(): HookResult<UniverseSummary> {
  const [data, setData] = useState<UniverseSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getUniverse();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch universe');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 120_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}

export function useDiscoveryCandidates(): HookResult<DiscoveryCandidate[]> {
  const [data, setData] = useState<DiscoveryCandidate[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getDiscoveryCandidates();
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch candidates');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 60_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}

export function usePositions(status?: string): HookResult<Position[]> {
  const [data, setData] = useState<Position[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getPositions(status);
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch positions');
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 60_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}
