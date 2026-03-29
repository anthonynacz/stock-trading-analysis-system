import { useState, useEffect, useCallback } from 'react';
import {
  getWatchlist,
  getRecommendations,
  getNews,
  getCatalysts,
  getStatus,
} from '../utils/api';
import type {
  WatchlistItem,
  Recommendation,
  NewsItem,
  CatalystEvent,
  SystemStatus,
} from '../types';

interface HookResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useWatchlist(sector?: string): HookResult<WatchlistItem[]> {
  const [data, setData] = useState<WatchlistItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getWatchlist(sector);
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch watchlist');
    } finally {
      setLoading(false);
    }
  }, [sector]);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 60_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}

export function useRecommendations(
  action?: string,
  minConviction?: number,
): HookResult<Recommendation[]> {
  const [data, setData] = useState<Recommendation[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    try {
      setLoading(true);
      const result = await getRecommendations(action, minConviction);
      setData(result);
      setError(null);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Failed to fetch recommendations',
      );
    } finally {
      setLoading(false);
    }
  }, [action, minConviction]);

  useEffect(() => {
    refetch();
    const interval = setInterval(refetch, 60_000);
    return () => clearInterval(interval);
  }, [refetch]);

  return { data, loading, error, refetch };
}

export function useNews(filters?: {
  ticker?: string;
  category?: string;
  impact_level?: string;
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
  }, [filters?.ticker, filters?.category, filters?.impact_level, filters?.limit]);

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
