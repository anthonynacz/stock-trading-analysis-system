import axios from 'axios';
import type {
  WatchlistItem,
  Recommendation,
  NewsItem,
  CatalystEvent,
  OptionsSnapshot,
  SystemStatus,
} from '../types';

const api = axios.create({ baseURL: '/api' });

export const getWatchlist = (sector?: string) =>
  api
    .get<WatchlistItem[]>('/watchlist', { params: sector ? { sector } : {} })
    .then((r) => r.data);

export const getRecommendations = (
  action?: string,
  minConviction?: number,
) =>
  api
    .get<Recommendation[]>('/recommendations', {
      params: { action, min_conviction: minConviction },
    })
    .then((r) => r.data);

export const getTickerRecommendations = (ticker: string) =>
  api.get<Recommendation[]>(`/recommendations/${ticker}`).then((r) => r.data);

export const getNews = (params?: {
  ticker?: string;
  category?: string;
  impact_level?: string;
  limit?: number;
}) => api.get<NewsItem[]>('/news', { params }).then((r) => r.data);

export const getCatalysts = () =>
  api.get<CatalystEvent[]>('/catalysts').then((r) => r.data);

export const getOptions = (ticker: string) =>
  api.get<OptionsSnapshot>(`/options/${ticker}`).then((r) => r.data);

export const getStatus = () =>
  api.get<SystemStatus>('/status').then((r) => r.data);

export const triggerRefresh = () =>
  api.post<{ status: string }>('/refresh').then((r) => r.data);
