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
  StrikeAllResult,
  WatchlistStrikesResult,
  StrikeSnapshotResult,
  TrendData,
  ResearchResult,
  UniverseSummary,
  UniverseStock,
  DiscoveryCandidate,
  Position,
  PositionCreateRequest,
  SuggestedOption,
  DeepOptionsAnalysis,
  ScannerResult,
  ScannerUniverseItem,
  ScannerDates,
  ScannerRunStatus,
  ScannerTier,
  IndustryRecommendation,
  IndustryDetail,
  IndustryHistoryPoint,
  IndustryForwardPoint,
  IndustryTopComponent,
  ChartResponse,
  ChartDatasetsResponse,
  ChartDatasetKey,
  RotationPreviewResponse,
  RotationCommitResponse,
  RotationCommitPair,
  RotationStatus,
  OutcomesSummary,
} from '../types';

export const TOKEN_STORAGE_KEY = 'vela.access_token';

/** Shape of a structured FastAPI `detail` body (quota / feature-lock errors). */
export interface ApiErrorDetail {
  error?: string;
  feature?: string;
  tier?: string;
  message?: string;
  denied_phases?: string[];
}

/** Extract a human-readable message from any thrown value (axios or otherwise). */
export function getApiErrorMessage(err: unknown, fallback = 'Request failed'): string {
  if (axios.isAxiosError(err)) {
    if (err.code === 'ECONNABORTED') return 'Request timed out';
    if (!err.response) return 'Network error — is the backend reachable?';
    const d = err.response.data?.detail;
    if (typeof d === 'string') return d;
    if (d && typeof d === 'object') return (d as ApiErrorDetail).message ?? JSON.stringify(d);
    return `${err.response.status} ${err.response.statusText}`;
  }
  return err instanceof Error ? err.message : fallback;
}

export const isQuotaError = (err: unknown) =>
  axios.isAxiosError(err) && err.response?.status === 402;

/** Fired on `window` when a stored token is rejected with 401 (token cleared first). */
export const UNAUTHORIZED_EVENT = 'vela:unauthorized';

const api = axios.create({ baseURL: '/api' });

// Attach the access token (if any) to every outgoing request. The auth
// middleware ignores the header in LEGACY_MODE and accepts it in JWT mode,
// so this is safe to set unconditionally.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// A 401 while holding a token means it expired or was revoked: drop it and let
// AuthProvider (listening for UNAUTHORIZED_EVENT) bounce the user to /login.
api.interceptors.response.use(undefined, (err: unknown) => {
  if (
    axios.isAxiosError(err) &&
    err.response?.status === 401 &&
    localStorage.getItem(TOKEN_STORAGE_KEY)
  ) {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
  return Promise.reject(err);
});

export default api;

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
  'conviction_score', 'prior_conviction_score', 'weighted_conviction_score',
  'current_price', 'target_price', 'stop_loss_price',
];
const OPT_NUMERIC = [
  'strike', 'premium_estimate', 'delta_estimate', 'breakeven_price',
];
const OPTIONS_SNAPSHOT_NUMERIC = [
  'stock_price', 'iv_rank', 'iv_percentile', 'put_call_ratio',
];

function parseSuggestedOptions(list: SuggestedOption[]): SuggestedOption[] {
  return list.map((o) => parseNumericFields(o, OPT_NUMERIC));
}

function parseRecommendation(rec: Recommendation): Recommendation {
  return {
    ...parseNumericFields(rec, REC_NUMERIC),
    suggested_options: parseSuggestedOptions(rec.suggested_options),
  };
}

export type RecommendationSort = 'conviction' | 'revised_at';

export const getRecommendations = (
  action?: string,
  minConviction?: number,
  date?: string,
  sort?: RecommendationSort,
) =>
  api
    .get<Recommendation[]>('/recommendations', {
      params: { action, min_conviction: minConviction, date, sort },
    })
    .then((r) => r.data.map(parseRecommendation));

export const getTickerRecommendations = (ticker: string, date?: string) =>
  api
    .get<Recommendation[]>(`/recommendations/${ticker}`, { params: { date } })
    .then((r) => r.data.map(parseRecommendation));

export const getNews = (params?: {
  ticker?: string;
  mode?: string;
  category?: string;
  impact_level?: string;
  industry?: string;
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
    .then((r) => parseNumericFields(r.data, OPTIONS_SNAPSHOT_NUMERIC));

export const getStatus = () =>
  api.get<SystemStatus>('/status').then((r) => r.data);

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

// ── Watchlist rotate-out ──────────────────────────────────────────────────
export const previewRotateOut = (tickers: string[]) =>
  api
    .post<RotationPreviewResponse>('/watchlist/rotate-out/preview', { tickers })
    .then((r) => ({
      items: r.data.items.map((it) => ({
        ...it,
        candidate: it.candidate
          ? parseNumericFields(it.candidate, ['composite_score'])
          : null,
      })),
    }));

export const commitRotateOut = (pairs: RotationCommitPair[]) =>
  api
    .post<RotationCommitResponse>('/watchlist/rotate-out/commit', { pairs })
    .then((r) => r.data);

export const getRotationStatus = () =>
  api.get<RotationStatus>('/watchlist/rotate-out/status').then((r) => r.data);

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
    .post<{ status: string; snapshot_date: string; tickers_saved: number }>('/strikes/snapshots', {
      budget,
      results,
    })
    .then((r) => r.data);

export const getStrikeSnapshot = (date: string) =>
  api
    .get<StrikeSnapshotResult>('/strikes/snapshots', { params: { date } })
    .then((r) => r.data);

export const getTickerTrends = (ticker: string, days = 20, sma = 5) =>
  api
    .get<TrendData>(`/trends/${ticker}`, { params: { days, sma } })
    .then((r) => r.data);

// ── Research ────────────────────────────────────────────────────────────────

// Same Decimal columns as recommendations; extra names are no-ops in parseNumericFields.
const RESEARCH_NUMERIC = REC_NUMERIC;

function parseResearchResult(r: ResearchResult): ResearchResult {
  const parsed = parseNumericFields(r, RESEARCH_NUMERIC);
  if (parsed.options_data) {
    parsed.options_data = parseNumericFields(parsed.options_data, OPTIONS_SNAPSHOT_NUMERIC);
  }
  if (parsed.suggested_options) {
    parsed.suggested_options = parseSuggestedOptions(parsed.suggested_options);
  }
  return parsed;
}

export const analyzeResearch = (ticker: string) =>
  api.post<ResearchResult>(`/research/${ticker}`).then((r) => parseResearchResult(r.data));

export const getResearchResults = (ticker?: string, limit = 50, offset = 0) =>
  api
    .get<ResearchResult[]>('/research', { params: { ticker, limit, offset } })
    .then((r) => r.data.map(parseResearchResult));

export const deleteResearchResult = (id: number) =>
  api.delete(`/research/${id}`);

// ── Deep Options Analysis ────────────────────────────────────────────────

const DEEP_OPT_NUMERIC = [
  'stock_price', 'iv_rank', 'iv_percentile', 'conviction_score',
];

function parseDeepOptions(d: DeepOptionsAnalysis): DeepOptionsAnalysis {
  return parseNumericFields(d, DEEP_OPT_NUMERIC);
}

export const analyzeDeepOptions = (ticker: string) =>
  api
    .post<DeepOptionsAnalysis>(`/options/deep/${ticker}`)
    .then((r) => parseDeepOptions(r.data));

export const getDeepOptionsResults = (ticker?: string, limit = 50, offset = 0) =>
  api
    .get<DeepOptionsAnalysis[]>('/options/deep', {
      params: { ticker, limit, offset },
    })
    .then((r) => r.data.map(parseDeepOptions));

export const deleteDeepOptionsResult = (id: number) =>
  api.delete(`/options/deep/${id}`);

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
    parsed.recommendation = parseRecommendation(parsed.recommendation);
  }
  return parsed;
}

export const getPositions = (status?: string, ticker?: string) =>
  api.get<Position[]>('/positions', { params: { status, ticker } })
    .then((r) => r.data.map(parsePosition));

export const createPosition = (data: PositionCreateRequest) =>
  api.post<Position>('/positions', data).then((r) => parsePosition(r.data));

export const updatePosition = (id: number, data: Record<string, unknown>) =>
  api.put<Position>(`/positions/${id}`, data).then((r) => parsePosition(r.data));

export const closePosition = (id: number, data: { close_price: number; notes?: string }) =>
  api.post<Position>(`/positions/${id}/close`, data).then((r) => parsePosition(r.data));

export const deletePosition = (id: number) =>
  api.delete(`/positions/${id}`);

export const refreshAllPositions = () =>
  api.post<Position[]>('/positions/refresh-all').then((r) => r.data.map(parsePosition));

export interface PnlDaily {
  date: string;
  unrealized_pnl: number;
  realized_pnl_today: number;
  realized_pnl_cumulative: number;
  open_count: number;
  closed_count_today: number;
}

export interface PnlMonthly {
  month: string;          // "YYYY-MM"
  realized_pnl: number;
  closed_count: number;
  latest_unrealized: number;
  latest_open_count: number;
}

export interface PnlHistory {
  daily: PnlDaily[];
  monthly: PnlMonthly[];
}

export const getPnlHistory = (days = 90) =>
  api.get<PnlHistory>('/positions/pnl-history', { params: { days } }).then((r) => r.data);

export const refreshPositionPrice = (id: number) =>
  api.post<Position>(`/positions/${id}/refresh-price`).then((r) => parsePosition(r.data));

// ── Multi-bagger scanner ────────────────────────────────────────────────

const SCANNER_NUMERIC = [
  'composite_score',
  'price',
  'market_cap',
  'stock_age_months',
  'return_12m',
  'return_6m',
  'momentum_percentile',
  'rev_growth_latest',
  'rev_growth_prior',
  'rev_accel_pp',
  'gross_margin_latest',
  'gross_margin_prior',
  'margin_delta_pp',
  'avg_pt',
  'pt_chase_ratio',
];

export const addScannerUniverse = (ticker: string, theme?: string) =>
  api
    .post<ScannerUniverseItem>('/scanner/universe', { ticker, theme })
    .then((r) => r.data);

export const removeScannerUniverse = (ticker: string) =>
  api.delete(`/scanner/universe/${ticker}`);

export const getScannerDates = () =>
  api.get<ScannerDates>('/scanner/dates').then((r) => r.data);

export const getScannerResults = (params?: {
  date?: string;
  tier?: ScannerTier;
  theme?: string;
}) =>
  api
    .get<ScannerResult[]>('/scanner/results', { params })
    .then((r) => r.data.map((x) => parseNumericFields(x, SCANNER_NUMERIC)));

export const runScanner = () =>
  api.post<{ status: string }>('/scanner/run').then((r) => r.data);

export const getScannerStatus = () =>
  api.get<ScannerRunStatus>('/scanner/status').then((r) => r.data);


// ── Industries (sector-level) ──────────────────────────────────────────

const INDUSTRY_NUMERIC = [
  'conviction_score',
  'weighted_conviction_score',
  'industry_weight',
  'breadth_positive_pct',
  'breadth_above_50d_pct',
  'cap_weighted_conviction',
  'etf_rsi_14',
  'etf_momentum_20d',
  'avg_news_sentiment',
  'geopolitical_points',
];

export const getIndustries = (date?: string) =>
  api
    .get<IndustryRecommendation[]>('/industries', { params: { date } })
    .then((r) => r.data.map((x) => parseNumericFields(x, INDUSTRY_NUMERIC)));

export const getIndustryDetail = (industry: string) =>
  api
    .get<IndustryDetail>(`/industries/${encodeURIComponent(industry)}`)
    .then((r) => {
      const d = r.data;
      return {
        ...d,
        latest: parseNumericFields(d.latest, INDUSTRY_NUMERIC),
        history: d.history.map((h) =>
          parseNumericFields<IndustryHistoryPoint>(h, ['conviction_score']),
        ),
        members: d.members.map(parseRecommendation),
        top_components: (d.top_components ?? []).map((c) =>
          parseNumericFields<IndustryTopComponent>(c, [
            'conviction',
            'price',
            'market_cap',
            'pe_ratio',
            'pct_from_52w_high',
          ]),
        ),
        forward_outlook: (d.forward_outlook ?? []).map((p) =>
          parseNumericFields<IndustryForwardPoint>(p, ['conviction_score']),
        ),
      };
    });


// ── Charts (dynamic visualization builder) ──────────────────────────────

export const getChartDatasets = () =>
  api.get<ChartDatasetsResponse>('/charts/datasets').then((r) => r.data);

export const runChartQuery = (dataset: ChartDatasetKey, spec: Record<string, unknown>) =>
  api
    .post<ChartResponse>('/charts/query', { dataset, spec })
    .then((r) => r.data);


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

// ── Schedule ──────────────────────────────────────────────────────────────

export interface ScheduleRun {
  id: number;
  phase: string;
  user_id: number | null;
  status: 'RUNNING' | 'SUCCESS' | 'FAILED';
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
  meta: Record<string, unknown> | null;
}

export interface ScheduleUpcoming {
  job_id: string;
  phase: string;
  fires_at: string;
}

export const getScheduleRuns = (hours = 72) =>
  api.get<ScheduleRun[]>('/schedule/runs', { params: { hours } }).then((r) => r.data);

export const getScheduleUpcoming = (hours = 24) =>
  api.get<ScheduleUpcoming[]>('/schedule/upcoming', { params: { hours } }).then((r) => r.data);

// ── Recommendation outcomes (performance) ────────────────────────────────

export const getOutcomesSummary = (days = 90) =>
  api.get<OutcomesSummary>('/outcomes/summary', { params: { days } }).then((r) => r.data);
