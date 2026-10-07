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

export interface StrikeSnapshotResult extends WatchlistStrikesResult {
  snapshot_date: string;
  budget: number | null;
}

export type RecAction = 'STRONG_BUY' | 'BUY' | 'HOLD' | 'SELL' | 'STRONG_SELL';

// Mirrors SuggestedOptionResponse (backend/utils/schemas.py): contract fields are
// optional there because research-fallback rows are rebuilt from stored JSON.
export interface SuggestedOption {
  id: number;
  contract_type: 'CALL' | 'PUT' | null;
  strike: number | null;
  expiry: string | null;
  premium_estimate: number | null;
  delta_estimate: number | null;
  strategy: string | null;
  strategy_rationale: string | null;
  days_to_expiry: number | null;
  breakeven_price: number | null;
}

export interface Recommendation {
  id: number;
  recommendation_date: string;
  ticker: string;
  action: RecAction;
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
  prior_action: RecAction | null;
  prior_conviction_score: number | null;
  revision_number: number;
  revised_at: string | null;
  revision_reason: string | null;
  suggested_options: SuggestedOption[];
}

export interface SignalDetail {
  signal: string;
  points: number;
  detail: string;
}

// ── Watchlist rotate-out ──────────────────────────────────────────────────
export interface RotationGate {
  blocked: boolean;
  reasons: string[];
}

export interface RotationCandidate {
  ticker: string;
  company_name: string | null;
  sector_name: string | null;
  composite_score: number;
  reasons: string[];
  cross_sector: boolean;
}

export interface RotationPreviewItem {
  ticker: string;
  gate: RotationGate;
  candidate: RotationCandidate | null;
}

export interface RotationPreviewResponse {
  items: RotationPreviewItem[];
}

export interface RotationCommitPair {
  remove: string;
  add: string;
}

export interface RotationBlockedItem {
  ticker: string;
  reasons: string[];
}

export interface RotationCommitResponse {
  status: string; // committed | already_running | no_op
  committed: RotationCommitPair[];
  blocked: RotationBlockedItem[];
  analysis_started: boolean;
}

export type RotationTickerState = 'pending' | 'analyzing' | 'done' | 'error';

export interface RotationStatus {
  running: boolean;
  started_at: string | null;
  tickers: Record<string, RotationTickerState>;
  errors: Record<string, string>;
}

export interface NewsTickerRelevance {
  ticker: string;
  relevance_score: number;
  relevance_source: string;
}

export interface BreakingNewsRec {
  id: number;
  action: RecAction;
  conviction_score: number | null;
  prior_action: RecAction | null;
  prior_conviction_score: number | null;
  revised_at: string | null;
  /** True when this exact headline produced the recommendation's latest revision. */
  triggered_revision: boolean;
}

export interface BreakingNewsTicker {
  ticker: string;
  relevance_score: number;
  held: boolean;
  recommendation: BreakingNewsRec | null;
}

export interface BreakingNewsItem {
  id: number;
  headline: string;
  source: string | null;
  source_url: string | null;
  category: string | null;
  impact_level: 'HIGH' | 'MEDIUM' | 'LOW' | null;
  sentiment_score: number | null;
  published_at: string | null;
  tickers: BreakingNewsTicker[];
}

export interface BreakingNewsRec {
  id: number;
  action: RecAction;
  conviction_score: number | null;
  prior_action: RecAction | null;
  prior_conviction_score: number | null;
  revised_at: string | null;
  /** True when this exact headline produced the recommendation's latest revision. */
  triggered_revision: boolean;
}

export interface BreakingNewsTicker {
  ticker: string;
  relevance_score: number;
  held: boolean;
  recommendation: BreakingNewsRec | null;
}

export interface BreakingNewsItem {
  id: number;
  headline: string;
  source: string | null;
  source_url: string | null;
  category: string | null;
  impact_level: 'HIGH' | 'MEDIUM' | 'LOW' | null;
  sentiment_score: number | null;
  published_at: string | null;
  tickers: BreakingNewsTicker[];
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
  action: RecAction;
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
  suggested_options: SuggestedOption[] | null;
  // Deep-news enrichment (Tier 1)
  news_summary: string | null;
  news_clusters: {
    by_category: Record<string, number>;
    by_source_quality: Record<string, number>;
    article_count_14d: number;
  } | null;
  sentiment_timeline: Array<{
    date: string;
    mean_sentiment: number;
    article_count: number;
  }> | null;
  top_headlines: Array<{
    headline: string;
    summary: string;
    source: string | null;
    source_quality: 'PRIMARY' | 'MAJOR_PRESS' | 'ANALYST' | 'AGGREGATOR' | 'OTHER';
    category: string;
    sentiment_score: number | null;
    impact_level: 'HIGH' | 'MEDIUM' | 'LOW' | null;
    published_at: string | null;
    url: string | null;
  }> | null;
  // Bull / bear / watch synthesis (Tier 3)
  bull_case: string | null;
  bear_case: string | null;
  watch_text: string | null;
  enrichment_status: 'PENDING' | 'COMPLETE' | 'PARTIAL' | 'FAILED' | null;
  enrichment_error: string | null;
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

export type HealthSeverity = 'info' | 'warn' | 'critical';

/** Position-aware overlay flag computed server-side on every read (empty for CLOSED positions). */
export interface PositionHealthFlag {
  code: 'EXPIRED' | 'DTE_WARNING' | 'STOP_BREACH' | 'TARGET_HIT' | 'SIGNAL_CONFLICT' | 'CONVICTION_DROP';
  severity: HealthSeverity;
  message: string;
}

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
  recommendation_id: number | null;
  health_flags: PositionHealthFlag[];
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
  recommendation_id?: number;
}

// ── Multi-bagger scanner ────────────────────────────────────────────────

export type ScannerTier = 'HOT' | 'WATCH' | 'MONITOR' | 'IGNORE';

export type ScannerSignal = SignalDetail;

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

export type IndustrySignal = SignalDetail;

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

// ── Recommendation outcomes (performance) ──────────────────────────────

export interface OutcomeBucket {
  n: number;
  avg_return_pct: number | null;
  directional_n: number;
  hit_rate: number | null;
  avg_adj_return_pct: number | null;
}

export interface OutcomeSignalHorizon {
  n: number;
  hit_rate: number | null;
  avg_adj_return_pct: number | null;
}

export interface OutcomeSignalRow {
  name: string;
  count: number;
  avg_points: number;
  t5: OutcomeSignalHorizon;
  t20: OutcomeSignalHorizon;
}

export interface OutcomesSummary {
  window_days: number;
  rows: number;
  overall: Record<string, OutcomeBucket>;
  by_action: Record<string, Record<string, OutcomeBucket>>;
  signals: OutcomeSignalRow[];
}

// ── Momentum Radar (GET /api/radar) ────────────────────────────────────
// The engine's state.json document (backend/radar/docs/github-spec.md §6 and
// §12), passed through by the API plus `stale`. Timestamps are ISO-8601 UTC
// strings ("…Z"). Percent fields are already percents (4.9 = 4.9%), `z*`
// fields are sigma units, and `intensity` is a 0-100 ranking score: never a
// probability or a confidence.

export type RadarDirection = 'up' | 'down';
export type RadarScanStatus = 'ok' | 'degraded' | 'no_data' | 'closed' | 'error';
export type RadarMemberState = 'racing' | 'cooling' | 'halted';
export type RadarExitReason =
  | 'FADE'
  | 'DRY'
  | 'STALL'
  | 'REVERSAL'
  | 'GIVEBACK'
  | 'VWAP_CROSS'
  | 'SESSION_END'
  | 'HALT_LONG'
  | 'DATA_STALE'
  | 'DISPLACED';

export interface RadarSession {
  date: string | null;
  phase: 'pre' | 'regular' | 'post' | 'closed' | string;
  open: string | null;
  close: string | null;
  half_day: boolean;
}

export interface RadarSource {
  name: string;
  status: 'ok' | 'degraded' | 'down' | null;
  consecutive_failures: number | null;
  last_ok_at: string | null;
}

export interface RadarMarket {
  mode: 'normal' | 'market';
  dir: RadarDirection | null;
  spy_chg_day_pct: number | null;
  spy_z30: number | null;
  /** Net share of stocks moving the same way over 30 min, as a fraction (0.66 = 66%). */
  breadth30: number | null;
}

export interface RadarCounts {
  universe: number | null;
  stage_b: number | null;
  members: number | null;
  heating: number | null;
  entered_today: number | null;
  exited_today: number | null;
}

export interface RadarSpark {
  t0: string;
  step_s: number;
  /** Index in `p` of the entry close. */
  entry_i: number;
  /** 5-minute closes from up to 6 slots before entry through now (≤ 80 points). */
  p: number[];
}

export interface RadarMember {
  ticker: string;
  name: string;
  sector: string;
  direction: RadarDirection;
  state: RadarMemberState;
  /** Added during a catch-up scan, more than 10 minutes after the move was confirmed. */
  late: boolean;
  entered_at: string;
  entry_price: number | null;
  last_price: number | null;
  last_bar_at: string | null;
  minutes_on_radar: number | null;
  move_since_entry_pct: number | null;
  peak_since_entry_pct: number | null;
  chg_5m_pct: number | null;
  chg_15m_pct: number | null;
  chg_30m_pct: number | null;
  chg_day_pct: number | null;
  rvol: number | null;
  rvol_day: number | null;
  vwap_dist_pct: number | null;
  z15: number | null;
  z30: number | null;
  zday: number | null;
  intensity: number | null;
  reasons: string[];
  soft_fails: number;
  /** 1 for the first time on the radar today, 2+ when it came back. */
  episode: number;
  spark: RadarSpark | null;
  /** Share of the move (from its base) given back; the GIVEBACK exit fires at 70. Display only. */
  giveback_pct?: number | null;
  /** Minutes since the last new high (up) / low (down); feeds the STALL check. Display only. */
  mins_since_extreme?: number | null;
}

/** Passed the entry checks on the last bar; waits one more bar to confirm. Not a member. */
export interface RadarHeating {
  ticker: string;
  name: string;
  direction: RadarDirection;
  since: string | null;
  price: number | null;
  chg_day_pct: number | null;
  intensity: number | null;
  reasons: string[];
}

export interface RadarExit {
  ticker: string;
  name: string;
  direction: RadarDirection;
  entered_at: string | null;
  exited_at: string | null;
  minutes_on_radar: number | null;
  move_since_entry_pct: number | null;
  exit_reason: RadarExitReason | string;
  /** Engine sentence that already starts with the reason (SPEC 12.5); shown alone when present. */
  exit_detail: string;
}

/** Names the per-sector cap kept off the radar (never members). */
export interface RadarSectorBanner {
  sector: string;
  direction: RadarDirection;
  /** How many names were held back beyond the cap. */
  count: number | null;
  tickers: string[];
}

export interface RadarHealth {
  ticks_today: number | null;
  ticks_skipped: number | null;
  last_tick_ms: number | null;
  loop_run_id: string | null;
  loop_started_at: string | null;
  /** Earlier scans delivered late (always 0 in Vela; kept for schema parity). */
  published_late: number | null;
}

export interface RadarState {
  schema: 1;
  generated_at: string | null;
  tick_id: string;
  last_bar: string | null;
  status: RadarScanStatus;
  /** Scanner's own sentence for degraded / no_data / error scans; shown as is. */
  message: string;
  session: RadarSession;
  /** Expected start of the next scan (5-minute boundary + 50 s). */
  next_tick_at: string | null;
  params_version: string;
  source: RadarSource;
  market: RadarMarket;
  counts: RadarCounts;
  members: RadarMember[];
  heating: RadarHeating[];
  /** Current session, newest first, ≤ 30. A closed heartbeat keeps the last session's exits. */
  recent_exits: RadarExit[];
  sector_banners: RadarSectorBanner[];
  health: RadarHealth;
  disclaimer: string;
}

/** At-the-money call of the chosen expiry (radar/options.py). Yahoo quotes, delayed. */
export interface RadarOptionAtm {
  strike: number;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  /** Bid-ask spread in % of mid. */
  spread_pct: number | null;
  volume: number;
  oi: number;
  iv_pct: number | null;
  /** Stock move needed by expiry to break even when buying at the ask, in %. */
  breakeven_pct: number | null;
  contract: string | null;
}

export type RadarOptionLiquidity = 'good' | 'fair' | 'thin' | 'none';

/** Option-chain and liquidity metrics of one radar name, refreshed after each scan. */
export interface RadarOptionMetrics {
  as_of: string;
  price: number | null;
  /** Typical daily dollar volume of the stock (20-day median). */
  adv_usd: number | null;
  /** Realised volatility, annualised %, from daily closes. */
  hv_pct: number | null;
  has_options: boolean;
  weeklies: boolean;
  earnings_date: string | null;
  earnings_estimate: boolean | null;
  days_to_earnings: number | null;
  /** First expiry at least 7 days out. */
  expiry: string | null;
  dte: number | null;
  earnings_before_expiry: boolean | null;
  atm: RadarOptionAtm | null;
  /** Calls with strikes within ±10% of the price: open interest and volume today. */
  ntm_call_oi: number | null;
  ntm_call_volume: number | null;
  call_volume: number | null;
  put_volume: number | null;
  put_call_volume: number | null;
  iv_pct: number | null;
  iv_hv: number | null;
  /** One-standard-deviation move by expiry implied by the ATM IV, in %. */
  expected_move_pct: number | null;
  liquidity: RadarOptionLiquidity;
}

/** The latest "Scan now" request (POST /api/radar/scan), as the worker left it. */
export interface RadarScanRequest {
  id: string;
  status: 'pending' | 'running' | 'done' | 'refused' | 'error';
  requested_at: string;
  requested_by?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  tick_id?: string | null;
  message?: string | null;
  result?: {
    status?: string | null;
    members?: number | null;
    entered?: string[] | null;
    exited?: string[] | null;
    duration_ms?: number | null;
  } | null;
}

/** `GET /api/radar` once the radar has written a snapshot. */
export interface RadarSnapshot extends RadarState {
  /** Server-side check: session open and no scan for more than RUNTIME.stale_warning_min. */
  stale: boolean;
  /** Option metrics of the members and warming-up names, by ticker (missing until fetched). */
  options?: Record<string, RadarOptionMetrics>;
  scan_request?: RadarScanRequest | null;
}

/** One stay on the radar that has ended (`GET /api/radar/history`). */
export interface RadarTrip {
  ticker: string;
  direction: RadarDirection;
  session: string;
  episode: number | null;
  entered_at: string | null;
  entry_price: number | null;
  entry_detail: string | null;
  entry_intensity: number | null;
  late: boolean;
  exited_at: string;
  exit_price: number | null;
  held_min: number | null;
  move_since_entry_pct: number | null;
  exit_reason: RadarExitReason | string;
  exit_detail: string | null;
}

export interface RadarHistory {
  days: number;
  ticker: string | null;
  trips: RadarTrip[];
}

/** One member / heating row of `GET /api/radar/ticker/{ticker}`. */
export interface RadarTickRow {
  tick: string;
  role: 'member' | 'heating';
  state: string;
  price: number | null;
  move_since_entry_pct: number | null;
}

/** `GET /api/radar` before the radar has written anything (still a 200). */
export interface RadarNoData {
  status: 'no_data';
  schema?: undefined;
  tick_id?: undefined;
  stale?: boolean;
  message?: string;
  scan_request?: RadarScanRequest | null;
}

export type RadarResponse = RadarSnapshot | RadarNoData;
