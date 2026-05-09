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
  sector: string | null;
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
  // Revision tracking — revision_number=0 means first run, prior_* will be null.
  // When >0, this row was overwritten by a later same-day run; UI renders a
  // "revised" badge with hover tooltip showing prior_action / prior_conviction_score.
  prior_action: 'STRONG_BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG_SELL' | null;
  prior_conviction_score: number | null;
  revision_number: number;
  revised_at: string | null;
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
  sector: string | null;
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

// ── Deep Options Analysis ─────────────────────────────────────────────────

export interface ExtendedGreeks {
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  rho: number | null;
  vanna: number | null;
  charm: number | null;
  vomma: number | null;
  premium?: number | null;
  theta_pct?: number | null;
  vega_pct?: number | null;
}

export interface ExpiryGreeksRow {
  expiry: string;
  dte: number;
  atm_strike: number | null;
  atm_iv: number | null;
  atm_call: ExtendedGreeks | null;
  atm_put: ExtendedGreeks | null;
  straddle_price: number | null;
  expected_move_pct: number | null;
}

export interface GreeksDetail {
  risk_free_rate: number;
  spot: number;
  expirations: ExpiryGreeksRow[];
}

export interface TermStructurePoint {
  expiry: string;
  dte: number;
  atm_iv: number | null;
}

export interface SkewPoint {
  expiry: string;
  dte: number;
  skew_25d_iv_pts: number | null;
}

export interface ExpectedMovePoint {
  expiry: string;
  dte: number;
  expected_move_pct: number | null;
  expected_move_abs: number | null;
  straddle_price: number | null;
}

export interface VolStructure {
  term_structure: TermStructurePoint[];
  term_shape: 'CONTANGO' | 'BACKWARDATION' | 'FLAT' | 'UNKNOWN';
  front_minus_back_iv_pts: number | null;
  skew_25d_by_expiry: SkewPoint[];
  avg_skew_25d_iv_pts: number | null;
  expected_moves: ExpectedMovePoint[];
}

export interface MaxPainEntry {
  expiry: string;
  dte: number;
  max_pain_strike: number | null;
  max_pain_distance_pct: number | null;
}

export interface PinMagnet {
  strike: number;
  call_oi: number;
  put_oi: number;
  oi_share_of_near_spot: number;
  distance_from_spot_pct: number;
  expiry: string;
  dte: number;
}

export interface PCOIEntry {
  expiry: string;
  dte: number;
  pc_oi_ratio: number | null;
}

export interface Positioning {
  gex_total: number;
  gex_regime: 'POSITIVE' | 'NEGATIVE' | 'NEUTRAL';
  max_pain_by_expiry: MaxPainEntry[];
  pin_magnets: PinMagnet[];
  pc_oi_by_expiry: PCOIEntry[];
}

export interface LiquidityExpiryRow {
  expiry: string;
  dte: number;
  total_oi: number;
  median_spread_pct: number | null;
  thin_oi: boolean;
  wide_spreads: boolean;
}

export interface Liquidity {
  rating: 'TIGHT' | 'MODERATE' | 'WIDE' | 'UNKNOWN';
  avg_spread_pct: number | null;
  by_expiry: LiquidityExpiryRow[];
}

export interface StrategyLeg {
  action: 'BUY' | 'SELL';
  side: 'CALL' | 'PUT';
  strike: number;
  expiry: string;
  qty: number;
  rationale: string;
}

export type StrategyVerdict =
  | 'BUY_CALL'
  | 'BUY_PUT'
  | 'BUY_CALL_SPREAD'
  | 'BUY_PUT_SPREAD'
  | 'SELL_PUT_SPREAD'
  | 'SELL_CALL_SPREAD'
  | 'SELL_IRON_CONDOR'
  | 'BUY_STRADDLE'
  | 'NO_TRADE';

export interface StrategyRecommendation {
  verdict: StrategyVerdict | string;
  strategy: string;
  target_expiry: string | null;
  target_dte: number | null;
  legs: StrategyLeg[];
  notes: string[];
  iv_bucket: 'LOW' | 'MID' | 'HIGH';
  near_earnings: boolean;
  earnings_dte: number | null;
}

export type RiskSeverity = 'HIGH' | 'MEDIUM' | 'LOW';

export interface HiddenRisk {
  severity: RiskSeverity;
  code: string;
  title: string;
  detail: string;
}

export interface DeepOptionsAnalysis {
  id: number;
  ticker: string;
  company_name: string | null;
  analyzed_at: string;
  stock_price: number | null;
  iv_rank: number | null;
  iv_percentile: number | null;
  directional_bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL' | null;
  conviction_score: number | null;
  verdict: string | null;
  greeks_detail: GreeksDetail | null;
  vol_structure: VolStructure | null;
  positioning: Positioning | null;
  liquidity: Liquidity | null;
  strategy: StrategyRecommendation | null;
  hidden_risks: HiddenRisk[] | null;
  rationale: string | null;
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

// ── Multi-bagger scanner ────────────────────────────────────────────────

export type ScannerTier = 'HOT' | 'WATCH' | 'MONITOR' | 'IGNORE';

export interface ScannerSignal {
  signal: string;
  points: number;
  detail: string;
}

export interface ScannerResult {
  id: number;
  run_date: string;
  ticker: string;
  company_name: string | null;
  theme: string | null;
  composite_score: number;
  tier: ScannerTier;
  signals_fired: number;
  price: number | null;
  market_cap: number | null;
  stock_age_months: number | null;
  return_12m: number | null;
  return_6m: number | null;
  momentum_percentile: number | null;
  rev_growth_latest: number | null;
  rev_growth_prior: number | null;
  rev_accel_pp: number | null;
  gross_margin_latest: number | null;
  gross_margin_prior: number | null;
  margin_delta_pp: number | null;
  avg_pt: number | null;
  pt_chase_ratio: number | null;
  revisions_90d: number | null;
  signals: ScannerSignal[] | null;
  rationale: string | null;
}

export interface ScannerUniverseItem {
  id: number;
  ticker: string;
  company_name: string | null;
  theme: string | null;
  source: string;
  is_active: boolean;
  added_at: string;
}

export interface ScannerDates {
  dates: string[];
}

export interface ScannerRunStatus {
  running: boolean;
  started_at: string | null;
  last_result: {
    status: string;
    run_date?: string;
    scored?: number;
    hot?: number;
    watch?: number;
    error?: string;
  } | null;
}

// ── Industry recommendations ────────────────────────────────────────────

export interface IndustrySignal {
  signal: string;
  points: number;
  detail: string;
}

export interface IndustryRepresentativeTicker {
  ticker: string;
  action: string;
  conviction: number;
}

export interface IndustryRecommendation {
  id: number;
  rec_date: string;
  industry: string;
  action: string;
  conviction_score: number;
  /** Conviction × user's saved industry_weight; equals conviction_score when weight=1. */
  weighted_conviction_score: number | null;
  industry_weight: number | null;
  signal_count: number;
  member_count: number | null;
  bullish_count: number | null;
  bearish_count: number | null;
  breadth_positive_pct: number | null;
  breadth_above_50d_pct: number | null;
  cap_weighted_conviction: number | null;
  etf_symbol: string | null;
  etf_rsi_14: number | null;
  etf_momentum_20d: number | null;
  avg_news_sentiment: number | null;
  news_article_count: number | null;
  geopolitical_points: number | null;
  active_catalyst_count: number | null;
  representative_tickers: IndustryRepresentativeTicker[] | null;
  signals: IndustrySignal[] | null;
  rationale: string | null;
}

export interface IndustryHistoryPoint {
  rec_date: string;
  action: string;
  conviction_score: number;
  signal_count: number;
}

export interface IndustryForwardPoint {
  forecast_date: string;
  day_offset: number;
  conviction_score: number;
  action: string;
}

export interface IndustryTopComponent {
  ticker: string;
  company_name: string | null;
  action: string;
  conviction: number;
  price: number | null;
  market_cap: number | null;
  pe_ratio: number | null;
  pct_from_52w_high: number | null;
  sector_industry: string | null;
}

export interface IndustryDetail {
  industry: string;
  latest: IndustryRecommendation;
  history: IndustryHistoryPoint[];
  members: Recommendation[];
  executive_summary: string | null;
  top_components: IndustryTopComponent[];
  forward_outlook: IndustryForwardPoint[];
}

// ── Chart builder ──────────────────────────────────────────────────────

export type ChartDatasetKey = 'ticker_time_series' | 'signal_breakdown' | 'industry_comparison';

export interface ChartSeriesPoint {
  x: string | number;
  y: number | null;
  count?: number | null;
}

export interface ChartSeries {
  name: string;
  data: ChartSeriesPoint[];
  metric_key?: string;
}

export interface ChartResponse {
  dataset: ChartDatasetKey;
  x_label: string;
  y_label: string;
  chart_type: 'line' | 'bar' | 'scatter' | string;
  series: ChartSeries[];
  meta: Record<string, unknown>;
}

export interface ChartMetricOption {
  key: string;
  label: string;
  source?: string;
}

export interface ChartDatasetInfo {
  key: ChartDatasetKey;
  label: string;
  description: string;
  chart_type: string;
  metrics?: ChartMetricOption[];
  aggregations?: string[];
  actions?: string[];
  views?: string[];
}

export interface ChartDatasetsResponse {
  datasets: ChartDatasetInfo[];
}
