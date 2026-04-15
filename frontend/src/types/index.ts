export interface WatchlistItem {
  id: number;
  ticker: string;
  company_name: string | null;
  sector: string | null;
  added_date: string;
  is_active: boolean;
  is_manual: boolean;
  is_locked: boolean;
  entry_reason: string | null;
  status: 'NEW_ENTRANT' | 'EXISTING' | 'REMOVED';
  rotation_protected: boolean;
  protection_reasons: string[];
}

export interface StrikeRecommendation {
  strike: number;
  expiry: string;
  premium_estimate: number;
  delta_estimate: number;
  gamma_estimate: number | null;
  theta_estimate: number | null;
  vega_estimate: number | null;
  breakeven: number;
  days_to_expiry: number;
  open_interest: number;
  explanation: string;
}

export interface StrikeRecommenderResult {
  ticker: string;
  current_price: number | null;
  risk_level: string;
  max_budget: number | null;
  recommended_call: StrikeRecommendation | null;
  recommended_put: StrikeRecommendation | null;
}

export interface StrikeRiskPair {
  recommended_call: StrikeRecommendation | null;
  recommended_put: StrikeRecommendation | null;
}

export interface StrikeAllResult {
  ticker: string;
  current_price: number | null;
  max_budget: number | null;
  conservative: StrikeRiskPair;
  moderate: StrikeRiskPair;
  aggressive: StrikeRiskPair;
}

export interface WatchlistStrikesResult {
  results: Record<string, StrikeAllResult>;
  scanned: number;
  with_results: number;
}

export interface SuggestedOption {
  id: number;
  contract_type: 'CALL' | 'PUT';
  strike: number;
  expiry: string;
  premium_estimate: number | null;
  delta_estimate: number | null;
  gamma_estimate: number | null;
  theta_estimate: number | null;
  vega_estimate: number | null;
  strategy: string | null;
  strategy_rationale: string | null;
  days_to_expiry: number | null;
  breakeven_price: number | null;
}

export interface Recommendation {
  id: number;
  recommendation_date: string;
  ticker: string;
  action: 'STRONG_BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG_SELL';
  conviction_score: number | null;
  signal_count: number | null;
  signals: SignalDetail[] | null;
  rationale: string;
  catalyst_type: string | null;
  entry_strategy: string | null;
  exit_rules: string | null;
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | null;
  current_price: number | null;
  target_price: number | null;
  stop_loss_price: number | null;
  suggested_options: SuggestedOption[];
}

export interface SignalDetail {
  signal: string;
  points: number;
  detail: string;
}

export interface NewsTickerRelevance {
  ticker: string;
  relevance_score: number;
  relevance_source: string;
}

export interface NewsItem {
  id: number;
  ticker: string | null;
  headline: string;
  summary: string | null;
  source: string | null;
  category: string | null;
  sentiment_score: number | null;
  impact_level: 'HIGH' | 'MEDIUM' | 'LOW' | null;
  published_at: string | null;
  source_url: string | null;
  related_tickers: NewsTickerRelevance[];
}

export interface CatalystEvent {
  ticker: string;
  earnings_date: string;
  earnings_time: string | null;
  fiscal_quarter: string | null;
  consensus_eps: number | null;
  days_until: number;
  window_status: 'APPROACHING' | 'ACTIVE' | 'POST';
}

export interface OptionsSnapshot {
  id: number;
  ticker: string;
  snapshot_time: string;
  stock_price: number | null;
  iv_rank: number | null;
  iv_percentile: number | null;
  put_call_ratio: number | null;
  total_call_volume: number | null;
  total_put_volume: number | null;
  unusual_activity: boolean;
  unusual_activity_detail: string | null;
}

export interface SystemStatus {
  db_connected: boolean;
  scheduler_running: boolean;
  active_watchlist_count: number;
  last_refresh: Record<string, string | null>;
  version: string;
}

export interface PipelineRunStatus {
  status: 'idle' | 'running' | 'completed' | 'failed';
  phases: string[];
  completed: string[];
  current: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface WatchlistChange {
  ticker: string;
  sector: string | null;
  reason: string | null;
}

export interface WatchlistChanges {
  date: string;
  entrants: WatchlistChange[];
  exiters: WatchlistChange[];
}

export interface PipelineDates {
  dates: string[];
}

export interface TrendDataPoint {
  date: string;
  price: number | null;
  target_price: number | null;
  conviction: number | null;
  signal_count: number | null;
  sentiment: number | null;
  article_count: number | null;
  price_sma: number | null;
  conviction_sma: number | null;
  signal_count_sma: number | null;
  sentiment_sma: number | null;
}

export interface TrendData {
  ticker: string;
  days: number;
  sma_window: number;
  data: TrendDataPoint[];
}

export interface ResearchResult {
  id: number;
  ticker: string;
  company_name: string | null;
  analyzed_at: string;
  action: 'STRONG_BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG_SELL';
  conviction_score: number | null;
  signal_count: number | null;
  signals: SignalDetail[] | null;
  rationale: string;
  catalyst_type: string | null;
  entry_strategy: string | null;
  exit_rules: string | null;
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | null;
  current_price: number | null;
  target_price: number | null;
  stop_loss_price: number | null;
  options_data: {
    stock_price: number | null;
    iv_rank: number | null;
    iv_percentile: number | null;
    put_call_ratio: number | null;
    total_call_volume: number | null;
    total_put_volume: number | null;
    unusual_activity: boolean;
    unusual_activity_detail: string | null;
  } | null;
  suggested_options: SuggestedOption[];
}

// ── Universe management ──────────────────────────────────────────────────

export interface UniverseStock {
  id: number;
  ticker: string;
  company_name: string | null;
  sector: string | null;
  source: 'SEED' | 'MANUAL' | 'DISCOVERED';
  is_active: boolean;
  added_at: string;
}

export interface UniverseSectorGroup {
  name: string;
  stock_count: number;
  stocks: UniverseStock[];
}

export interface UniverseSummary {
  sectors: UniverseSectorGroup[];
  total_stocks: number;
  pending_candidates: number;
}

export interface DiscoveryCandidate {
  id: number;
  ticker: string;
  company_name: string | null;
  suggested_sector: string | null;
  discovered_at: string;
  source: string;
  score: number | null;
  market_cap: number | null;
  avg_volume: number | null;
  price: number | null;
  change_pct: number | null;
  rationale: string | null;
  status: 'PENDING' | 'APPROVED' | 'DISMISSED';
}

// ── Positions ─────────────────────────────────────────────────────────

export type PositionType = 'CALL' | 'PUT' | 'STOCK';
export type PositionStatus = 'OPEN' | 'CLOSED';

export interface Position {
  id: number;
  ticker: string;
  company_name: string | null;
  position_type: PositionType;
  quantity: number;
  entry_price: number;
  current_price: number | null;
  strike_price: number | null;
  premium_paid: number | null;
  expiry: string | null;
  stop_loss: number | null;
  target_price: number | null;
  status: PositionStatus;
  opened_at: string;
  closed_at: string | null;
  close_price: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  unrealized_pnl_pct: number | null;
  days_to_expiry: number | null;
  is_on_watchlist: boolean;
  recommendation: Recommendation | null;
  notes: string | null;
}

export interface PositionCreateRequest {
  ticker: string;
  position_type: PositionType;
  quantity: number;
  entry_price: number;
  strike_price?: number;
  premium_paid?: number;
  expiry?: string;
  stop_loss?: number;
  target_price?: number;
  notes?: string;
}
