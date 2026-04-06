import axios from 'axios';
import type {
  WatchlistItem,
  Recommendation,
  NewsItem,
  CatalystEvent,
  OptionsSnapshot,
  SystemStatus,
  WatchlistChanges,
  PipelineDates,
  StrikeRecommenderResult,
  StrikeAllResult,
  WatchlistStrikesResult,
} from '../types';

const api = axios.create({ baseURL: '/api' });

/** Convert string-encoded Decimal fields to numbers (backend serializes Numeric as strings). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function parseNumericFields<T>(obj: T, fields: string[]): T {
  const out = { ...obj } as any;
  for (const f of fields) {
    const v = out[f];
    if (v != null && typeof v === 'string') {
      out[f] = Number(v);
    }
  }
  return out as T;
}

export const getWatchlist = (sector?: string, date?: string) =>
  api
    .get<WatchlistItem[]>('/watchlist', { params: { sector, date } })
    .then((r) => r.data);

const REC_NUMERIC = [
  'conviction_score', 'current_price', 'target_price', 'stop_loss_price',
];
const OPT_NUMERIC = [
  'strike', 'premium_estimate', 'delta_estimate', 'breakeven_price',
];

export const getRecommendations = (
  action?: string,
  minConviction?: number,
  date?: string,
) =>
  api
    .get<Recommendation[]>('/recommendations', {
      params: { action, min_conviction: minConviction, date },
    })
    .then((r) =>
      r.data.map((rec) => ({
        ...parseNumericFields(rec, REC_NUMERIC),
        suggested_options: rec.suggested_options.map((o) =>
          parseNumericFields(o, OPT_NUMERIC),
        ),
      })),
    );

export const getTickerRecommendations = (ticker: string) =>
  api.get<Recommendation[]>(`/recommendations/${ticker}`).then((r) =>
    r.data.map((rec) => ({
      ...parseNumericFields(rec, REC_NUMERIC),
      suggested_options: rec.suggested_options.map((o) =>
        parseNumericFields(o, OPT_NUMERIC),
      ),
    })),
  );

export const getNews = (params?: {
  ticker?: string;
  category?: string;
  impact_level?: string;
  limit?: number;
}) =>
  api
    .get<NewsItem[]>('/news', { params })
    .then((r) => r.data.map((n) => parseNumericFields(n, ['sentiment_score'])));

export const getCatalysts = () =>
  api.get<CatalystEvent[]>('/catalysts').then((r) =>
    r.data.map((c) => parseNumericFields(c, ['consensus_eps'])),
  );

export const getOptions = (ticker: string) =>
  api
    .get<OptionsSnapshot>(`/options/${ticker}`)
    .then((r) =>
      parseNumericFields(r.data, [
        'stock_price', 'iv_rank', 'iv_percentile', 'put_call_ratio',
      ]),
    );

export const getStatus = () =>
  api.get<SystemStatus>('/status').then((r) => r.data);

export const triggerRefresh = () =>
  api.post<{ status: string }>('/refresh').then((r) => r.data);

export const getPipelineDates = () =>
  api.get<PipelineDates>('/pipeline-dates').then((r) => r.data);

export const getWatchlistChanges = (date?: string) =>
  api
    .get<WatchlistChanges>('/watchlist/changes', { params: { date } })
    .then((r) => r.data);

export const addToWatchlist = (ticker: string) =>
  api.post<WatchlistItem>('/watchlist', { ticker }).then((r) => r.data);

export const removeFromWatchlist = (ticker: string) =>
  api.delete(`/watchlist/${ticker}`).then((r) => r.data);

export const toggleLockTicker = (ticker: string) =>
  api.put(`/watchlist/${ticker}/lock`).then((r) => r.data);

export const getStrikeRecommendations = (
  ticker: string,
  risk: string = 'moderate',
  budget?: number,
) =>
  api
    .get<StrikeRecommenderResult>(`/options/${ticker}/strikes`, {
      params: { risk, budget },
    })
    .then((r) => r.data);

export const getStrikeRecommendationsAll = (ticker: string, budget?: number) =>
  api
    .get<StrikeAllResult>(`/options/${ticker}/strikes/all`, {
      params: { budget },
    })
    .then((r) => r.data);

export const getWatchlistStrikes = (budget?: number) =>
  api
    .get<WatchlistStrikesResult>('/options/watchlist/strikes', {
      params: { budget },
    })
    .then((r) => r.data);

export const saveStrikeSnapshot = (budget: number | undefined, results: Record<string, unknown>) =>
  api
    .post('/strikes/snapshots', { budget, results })
    .then((r) => r.data);

export const getStrikeSnapshot = (date: string) =>
  api
    .get<WatchlistStrikesResult & { snapshot_date: string; budget: number | null }>('/strikes/snapshots', {
      params: { date },
    })
    .then((r) => r.data);

