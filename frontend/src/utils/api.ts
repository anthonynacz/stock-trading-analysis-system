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
  PipelineRunStatus,
  StrikeRecommenderResult,
  StrikeAllResult,
  WatchlistStrikesResult,
  TrendData,
  ResearchResult,
  UniverseSummary,
  UniverseStock,
  DiscoveryCandidate,
  Position,
  PositionCreateRequest,
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

export const getTickerRecommendations = (ticker: string, date?: string) =>
  api.get<Recommendation[]>(`/recommendations/${ticker}`, { params: { date } }).then((r) =>
    r.data.map((rec) => ({
      ...parseNumericFields(rec, REC_NUMERIC),
      suggested_options: rec.suggested_options.map((o) =>
        parseNumericFields(o, OPT_NUMERIC),
      ),
    })),
  );

export const getNews = (params?: {
  ticker?: string;
  mode?: string;
  category?: string;
  impact_level?: string;
  min_relevance?: number;
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

export const startPipeline = (phases?: string[]) =>
  api.post<{ status: string; phases: string[] }>('/pipeline/run', phases ? { phases } : {}).then((r) => r.data);

export const getPipelineStatus = () =>
  api.get<PipelineRunStatus>('/pipeline/status').then((r) => r.data);

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

export const getTickerTrends = (ticker: string, days = 20, sma = 5) =>
  api
    .get<TrendData>(`/trends/${ticker}`, { params: { days, sma } })
    .then((r) => r.data);

// ── Research ────────────────────────────────────────────────────────────────

const RESEARCH_NUMERIC = [
  'conviction_score', 'current_price', 'target_price', 'stop_loss_price',
];

function parseResearchResult(r: ResearchResult): ResearchResult {
  const parsed = parseNumericFields(r, RESEARCH_NUMERIC);
  if (parsed.options_data) {
    parsed.options_data = parseNumericFields(parsed.options_data, [
      'stock_price', 'iv_rank', 'iv_percentile', 'put_call_ratio',
    ]);
  }
  if (parsed.suggested_options) {
    parsed.suggested_options = parsed.suggested_options.map((o) =>
      parseNumericFields(o, OPT_NUMERIC),
    );
  }
  return parsed;
}

export const analyzeResearch = (ticker: string) =>
  api.post<ResearchResult>(`/research/${ticker}`).then((r) => parseResearchResult(r.data));

export const getResearchResults = (ticker?: string, limit = 50, offset = 0) =>
  api
    .get<ResearchResult[]>('/research', { params: { ticker, limit, offset } })
    .then((r) => r.data.map(parseResearchResult));

export const getResearchResult = (id: number) =>
  api.get<ResearchResult>(`/research/${id}`).then((r) => parseResearchResult(r.data));

export const deleteResearchResult = (id: number) =>
  api.delete(`/research/${id}`);

// ── Universe ────────────────────────────────────────────────────────────────

export const getUniverse = () =>
  api.get<UniverseSummary>('/universe').then((r) => r.data);

export const addToUniverse = (ticker: string, sector: string) =>
  api.post<UniverseStock>('/universe', { ticker, sector }).then((r) => r.data);

export const removeFromUniverse = (ticker: string) =>
  api.delete(`/universe/${ticker}`).then((r) => r.data);

export const getDiscoveryCandidates = () =>
  api.get<DiscoveryCandidate[]>('/universe/candidates').then((r) => r.data);

export const approveCandidate = (id: number, sector: string) =>
  api.post<UniverseStock>(`/universe/candidates/${id}/approve`, { sector }).then((r) => r.data);

export const dismissCandidate = (id: number) =>
  api.post(`/universe/candidates/${id}/dismiss`).then((r) => r.data);

export const triggerDiscovery = () =>
  api.post('/universe/discover').then((r) => r.data);

// ── Positions ────────────────────────────────────────────────────────────────

const POS_NUMERIC = [
  'entry_price', 'current_price', 'strike_price', 'premium_paid',
  'stop_loss', 'target_price', 'close_price', 'realized_pnl', 'unrealized_pnl',
];

function parsePosition(p: Position): Position {
  const parsed = parseNumericFields(p, POS_NUMERIC);
  if (parsed.recommendation) {
    parsed.recommendation = {
      ...parseNumericFields(parsed.recommendation, REC_NUMERIC),
      suggested_options: parsed.recommendation.suggested_options.map((o) =>
        parseNumericFields(o, OPT_NUMERIC),
      ),
    };
  }
  return parsed;
}

export const getPositions = (status?: string, ticker?: string) =>
  api.get<Position[]>('/positions', { params: { status, ticker } })
    .then((r) => r.data.map(parsePosition));

export const getPosition = (id: number) =>
  api.get<Position>(`/positions/${id}`).then((r) => parsePosition(r.data));

export const createPosition = (data: PositionCreateRequest) =>
  api.post<Position>('/positions', data).then((r) => parsePosition(r.data));

export const updatePosition = (id: number, data: Record<string, unknown>) =>
  api.put<Position>(`/positions/${id}`, data).then((r) => parsePosition(r.data));

export const closePosition = (id: number, data: { close_price: number; notes?: string }) =>
  api.post<Position>(`/positions/${id}/close`, data).then((r) => parsePosition(r.data));

export const deletePosition = (id: number) =>
  api.delete(`/positions/${id}`);

export const refreshPositionPrice = (id: number) =>
  api.post<Position>(`/positions/${id}/refresh-price`).then((r) => parsePosition(r.data));

// ── Reports ──────────────────────────────────────────────────────────────

export const downloadReport = async (date?: string) => {
  const response = await api.get('/reports/daily', {
    params: { date },
    responseType: 'blob',
  });
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', `edgeflow-report-${date || 'today'}.pdf`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};
