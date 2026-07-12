export type HoldingItem = {
  fund_code: string;
  fund_name: string;
  shares: number;
  cost_basis: number;
  market_value: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  is_stale: boolean;
  as_of_date: string | null;
  valuation_status: "ready" | "missing_snapshot" | "missing_nav";
};

export type HoldingsResponse = {
  items: HoldingItem[];
};

export type ValueHistoryPoint = {
  date: string;
  value: number;
};

export type Transaction = {
  id: number;
  fund_code: string;
  action: "buy" | "sell";
  amount: number | null;
  shares: number;
  proceeds: number | null;
  nav_at_trade: number;
  fee: number;
  traded_at: string;
};

export type TransactionList = {
  total: number;
  items: Transaction[];
};

export type CurrentValuation = {
  index_code: string;
  pe: number;
  pb: number;
  pe_percentile: number | null;
  pb_percentile: number | null;
  as_of_date: string;
};

export type HistoricalValuation = {
  date: string;
  pe: number;
  pb: number;
  pe_percentile: number | null;
  pb_percentile: number | null;
  effective_window: number;
};

export type NewsItem = {
  id: number;
  title: string;
  url: string;
  published_at: string;
  summary: string | null;
  summary_status: string;
  event_type: string | null;
};

export type NewsGroup = {
  fund_code: string;
  items: NewsItem[];
};

export type NewsFeedResponse = {
  groups: NewsGroup[];
};

export type NotificationSettings = {
  recipient_email: string;
  reminder_day: number;
  reference_index_code: string | null;
  base_monthly_amount: number;
  etf_trading_capital: number;
  allow_full_exit: boolean;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_username: string | null;
  smtp_from: string | null;
};

export type JobRun = {
  id: number;
  job_name: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  details: Record<string, unknown>;
};

export type DataStatus = {
  fund_count: number;
  nav_rows: number;
  earliest_nav_date: string | null;
  latest_nav_date: string | null;
  strategy_count: number;
  paper_portfolio_count: number;
};

export type RecommendationRun = {
  id: number;
  asset_type: "fund" | "stock";
  status: string;
  as_of_date: string;
  started_at: string;
  finished_at: string | null;
  data_cutoff: Record<string, unknown>;
  details: Record<string, unknown>;
  error_message: string | null;
};

export type RecommendationItem = {
  id: number;
  asset_code: string;
  asset_name: string;
  asset_type: "fund" | "stock";
  rank: number;
  total_score: number;
  score_breakdown: Record<string, { score?: number; weight?: number; weighted_score?: number; metrics?: Record<string, unknown> }>;
  rationale: Record<string, unknown>;
  risk_flags: string[];
  data_freshness: Record<string, unknown>;
  safe_label: string;
};

export type LatestRecommendationsResponse = {
  asset_type: "fund" | "stock";
  disclaimer: string;
  run: RecommendationRun | null;
  items: RecommendationItem[];
};

export type RecommendationReviewItem = {
  id: number;
  recommendation_item_id: number;
  asset_code: string;
  verdict: string;
  agent_notes: Record<string, unknown>;
  risk_flags: string[];
};

export type RecommendationReview = {
  id: number;
  run_id: number;
  status: string;
  model_name: string;
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown>;
  error_message: string | null;
  items: RecommendationReviewItem[];
};

export type LatestRecommendationsWithReviewResponse = LatestRecommendationsResponse & {
  review: RecommendationReview | null;
};

export type StrategyType = "momentum_rotation" | "dca_baseline" | "screening";

export type StrategyDefinition = {
  id: number;
  name: string;
  strategy_type: StrategyType;
  asset_type: "fund";
  status: string;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type StrategyOrder = {
  id: number;
  submitted_date: string | null;
  trade_date: string;
  confirmed_date: string | null;
  asset_code: string;
  asset_name: string | null;
  side: string;
  amount: number;
  shares: number;
  price: number;
  fee: number;
  status: string;
  platform: string;
};

export type StrategyPosition = {
  id: number;
  snapshot_date: string;
  asset_code: string;
  asset_name: string | null;
  shares: number;
  market_value: number;
  weight: number;
};

export type StrategyEquityPoint = {
  id: number;
  curve_date: string;
  equity: number;
  cash: number;
  drawdown: number;
};

export type StrategyRun = {
  id: number;
  strategy_id: number;
  run_type: "backtest" | "simulation_update" | "screening";
  status: string;
  started_at: string;
  finished_at: string | null;
  as_of_date: string;
  date_range: Record<string, unknown>;
  metrics: Record<string, unknown>;
  error_message: string | null;
  orders: StrategyOrder[];
  positions: StrategyPosition[];
  equity_curve: StrategyEquityPoint[];
};

export type PaperPortfolio = {
  id: number;
  strategy_id: number;
  name: string;
  status: string;
  started_at: string;
  cash: number;
  latest_equity: number;
  latest_run: StrategyRun | null;
};

export type PaperReconciliationItem = {
  asset_code: string;
  asset_name: string | null;
  paper_shares: number;
  actual_shares: number;
  share_diff: number;
  paper_market_value: number;
  actual_market_value: number;
  market_value_diff: number;
};

export type PaperReconciliation = {
  paper_id: number;
  strategy_id: number;
  as_of_date: string;
  platform_profile: string;
  paper_equity: number;
  actual_equity: number;
  equity_diff: number;
  items: PaperReconciliationItem[];
  note: string;
};

export type StrategyEvaluationItem = {
  id: number;
  rank_order: number;
  item_type: string;
  label: string;
  parameters: Record<string, unknown>;
  metrics: Record<string, unknown>;
  in_sample_metrics: Record<string, unknown>;
  out_of_sample_metrics: Record<string, unknown>;
  rolling_windows: Array<Record<string, unknown>>;
  score: number;
  risk_flags: string[];
};

export type StrategyEvaluation = {
  id: number;
  strategy_id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  start_date: string;
  end_date: string;
  data_coverage: Record<string, unknown>;
  summary: Record<string, unknown>;
  conclusion: string;
  risk_flags: string[];
  items: StrategyEvaluationItem[];
};

export type EtfUniverseItem = {
  code: string;
  name: string;
  exchange: string;
  theme_tags: string[];
  trading_rule_label: string;
  asset_class: string;
  is_short_term_eligible: boolean;
  latest_price_date: string | null;
  latest_close: number | null;
};

export type EtfUniverseResponse = {
  items: EtfUniverseItem[];
};

export type EtfDataHealthItem = {
  code: string;
  name: string;
  status: string;
  provider: string | null;
  latest_price_date: string | null;
  successful_rows: number;
  last_error_message: string | null;
  consecutive_failures: number;
  is_stale: boolean;
  updated_at: string | null;
};

export type EtfDataStatus = {
  summary: Record<string, unknown>;
  items: EtfDataHealthItem[];
};

export type ShortEtfSignalItem = {
  id: number;
  etf_code: string;
  etf_name: string | null;
  rank: number;
  total_score: number;
  conclusion: string;
  score_breakdown: Record<string, unknown>;
  risk_flags: string[];
  rationale: Record<string, unknown>;
  theme_tags: string[];
  trading_rule_label: string | null;
};

export type ShortEtfSignalRun = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  as_of_date: string;
  config: Record<string, unknown>;
  summary: Record<string, unknown>;
  error_message: string | null;
  items: ShortEtfSignalItem[];
};

export type ShortEtfReviewItem = {
  id: number;
  signal_item_id: number;
  etf_code: string;
  etf_name: string | null;
  rank: number;
  total_score: number;
  verdict: string;
  agent_notes: Record<string, unknown>;
  risk_flags: string[];
};

export type ShortEtfReview = {
  id: number;
  run_id: number;
  status: string;
  model_name: string;
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown>;
  error_message: string | null;
  items: ShortEtfReviewItem[];
};

export type ShortEtfEvaluationItem = {
  id: number;
  rank_order: number;
  label: string;
  item_type: string;
  parameters: Record<string, unknown>;
  metrics: Record<string, unknown>;
  baseline_metrics: Record<string, unknown>;
  score: number;
  risk_flags: string[];
};

export type ShortEtfEvaluation = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  start_date: string;
  end_date: string;
  sample_days: number;
  conclusion: string;
  data_coverage: Record<string, unknown>;
  summary: Record<string, unknown>;
  risk_flags: string[];
  error_message: string | null;
  items: ShortEtfEvaluationItem[];
};

export type ShortEtfPaperOrder = {
  id: number;
  signal_run_id: number | null;
  trade_date: string;
  etf_code: string;
  etf_name: string | null;
  side: string;
  amount: number;
  shares: number;
  price: number;
  fee: number;
  status: string;
};

export type ShortEtfPaperEquityPoint = {
  id: number;
  curve_date: string;
  equity: number;
  cash: number;
  drawdown: number;
};

export type ShortEtfPaperPosition = {
  etf_code: string;
  etf_name: string | null;
  shares: number;
  market_value: number;
  weight: number;
};

export type ShortEtfPaper = {
  id: number;
  name: string;
  status: string;
  started_at: string;
  cash: number;
  latest_equity: number;
  summary: Record<string, unknown>;
  positions: ShortEtfPaperPosition[];
  orders: ShortEtfPaperOrder[];
  equity_curve: ShortEtfPaperEquityPoint[];
};

export type ShortResearchDataHealth = {
  asset_type: "fund" | "etf";
  code: string;
  name: string;
  status: string;
  latest_date: string | null;
  usable_days: number;
  provider: string | null;
  source_note: string;
  last_error_message: string | null;
  is_stale: boolean;
};

export type ShortResearchStatus = {
  latest_data_date: string | null;
  signal_date: string | null;
  asset_count: number;
  fund_count: number;
  etf_count: number;
  etf_total_count: number;
  etf_eligible_count: number;
  etf_default_display_count: number;
  etf_data_stale_count: number;
  etf_failed_count: number;
  priced_asset_count: number;
  observable_count: number;
  high_risk_count: number;
  data_issue_count: number;
  data_health: ShortResearchDataHealth[];
  label_validation: Record<string, unknown>;
  label_validation_generated_at: string | null;
  score_bucket_validation: Record<string, unknown>;
  score_bucket_validation_generated_at: string | null;
};

export type ShortResearchChartPoint = {
  date: string;
  value: number;
  close: number | null;
  nav: number | null;
  drawdown: number;
  turnover: number | null;
};

export type ValidationEvidence = {
  run_id?: number;
  as_of_date?: string;
  rule_version?: string;
  validation_mode?: string;
  outcome_source?: string;
  sample_count?: number;
  win_rate?: number | null;
  median_return?: number | null;
  confidence?: "sufficient" | "limited" | "insufficient" | string;
  sample_quality?: {
    sample_count_total?: number;
    excluded_count_total?: number;
    exclusion_reasons?: string[];
  } | null;
  freshness?: {
    as_of_date?: string | null;
    generated_at?: string | null;
    rule_version?: string | null;
    validation_mode?: string | null;
  } | null;
  degradation_warning?: string | null;
  freshness_days?: number | null;
  fresh?: boolean;
  sample_quality_warnings?: string[];
  degraded_recently?: boolean;
  degradation_reason?: string | null;
  horizons?: Record<string, {
    sample_count?: number;
    excluded_count?: number;
    pending_count?: number;
    coverage?: number | null;
    exclusion_reasons?: string[];
    avg_return?: number | null;
    median_return?: number | null;
    win_rate?: number | null;
    worst_forward_drawdown?: number | null;
    favorable_excursion_median?: number | null;
    confidence?: string;
    confidence_label?: string;
  }>;
  historical_replay?: ValidationEvidence | null;
  forward_live?: ValidationEvidence | null;
  evidence_tracks?: Record<string, ValidationEvidence>;
};

export type ObservationPortfolioContext = {
  status?: "included" | "watch_only" | "excluded" | string;
  target_weight?: number;
  evidence?: string[];
  risk_reasons?: string[];
  inclusion_reasons?: string[];
  weight_reason?: string | null;
  quality_note?: string | null;
  explanation?: string | null;
  exclusion_reason?: string | null;
  weight_explanation?: string | null;
  exclusion_explanation?: string | null;
  decision_factors?: Record<string, unknown>;
  snapshot_id?: number | null;
  generated_at?: string | null;
};

export type ShortResearchFactorResult = {
  factor_id: string;
  group: string;
  group_label?: string;
  label: string;
  score: number | null;
  availability: string;
  reliability: string;
  source: string;
  reason: string;
  decision_eligible?: boolean;
  raw_value?: unknown;
  components?: Record<string, unknown>;
};

export type ShortResearchFactorGroupScore = {
  group: string;
  label: string;
  score: number | null;
  availability: string;
  reliability?: string;
  reason?: string;
  factor_count?: number;
  available_factor_count?: number;
  factor_ids?: string[];
};

export type ShortResearchAsset = {
  asset_type: "fund" | "etf";
  code: string;
  name: string;
  rank: number | null;
  ranking_score?: number | null;
  score_eligible?: boolean | null;
  global_rank?: number | null;
  total_score: number;
  technical_score?: number | null;
  opportunity_score?: number | null;
  opportunity_label?: string | null;
  opportunity_score_version?: string | null;
  sector_trend_score?: number | null;
  sector_trend_label?: string | null;
  sector_trend_summary?: string | null;
  sector_trend_reason?: string | null;
  sector_trend_status?: string | null;
  sector_peer_count?: number | null;
  catalyst_score?: number | null;
  sentiment_heat_score?: number | null;
  catalyst_summary?: string | null;
  catalyst_events?: Array<Record<string, unknown>>;
  catalyst_limitations?: string[];
  factor_profile_version?: string | null;
  factor_profile_status?: string | null;
  factor_profile_score?: number | null;
  factor_group_scores?: Record<string, ShortResearchFactorGroupScore>;
  factor_scores?: ShortResearchFactorResult[];
  factor_availability?: Record<string, unknown>;
  risk_gates?: Array<Record<string, unknown>>;
  opportunity_breakdown?: Record<string, unknown>;
  conclusion: string;
  entry_timing_label: string;
  entry_timing_reason: string;
  theme_tags: string[];
  theme_group?: string | null;
  primary_theme?: string | null;
  secondary_themes?: string[];
  classification_source?: string | null;
  classification_confidence?: string | null;
  classification_reason?: string | null;
  theme_profile?: Record<string, unknown>;
  investment_direction: string;
  trading_rule_label: string;
  latest_date: string | null;
  latest_value: number | null;
  usable_days: number;
  sample_level: string;
  metrics: Record<string, unknown>;
  score_breakdown: ShortResearchScoreBreakdown;
  risk_flags: string[];
  rationale: Record<string, unknown>;
  source_note: string;
  advisor_report: ShortResearchAdvisorReport | null;
  validation_evidence: ValidationEvidence;
  observation_portfolio: ObservationPortfolioContext;
  research_signal_contract?: Record<string, unknown>;
  evidence_status?: string;
  evidence_summary?: Record<string, unknown>;
};

export type ShortResearchScoreComponent = {
  score?: number;
  reason?: string;
  confidence?: string;
  sample_count?: number;
  reliability?: string;
  [key: string]: unknown;
};

export type ShortResearchFinalScoreBreakdown = {
  score_version?: string;
  final_score?: number;
  confidence?: string;
  components?: Record<string, ShortResearchScoreComponent>;
  limitation_reasons?: string[];
  weights?: Record<string, number>;
  [key: string]: unknown;
};

export type ShortResearchScoreBreakdown = Record<string, unknown> & {
  score_version?: string;
  final_score_v2?: ShortResearchFinalScoreBreakdown;
};

export type ShortResearchAdvisorReport = {
  id: number;
  status: string;
  action_label: string;
  plain_summary: string;
  opportunity: string[];
  risks: string[];
  opposing_view: string;
  watch_conditions: string[];
  holding_note: string;
  data_limitations: string;
  model_name: string;
  prompt_version: string;
  source: string;
  generated_at: string;
};

export type ShortResearchAssetList = {
  items: ShortResearchAsset[];
  total: number;
  generated_at: string | null;
  as_of_date: string | null;
  snapshot?: EtfRankingSnapshotMetadata | null;
  theme_heat?: {
    theme: string;
    count: number;
    avg_score: number;
    avg_today_return: number | null;
    top_score: number;
    top_asset: { code: string; name: string } | null;
  }[];
};

export type ShortResearchAssetDetail = {
  asset: ShortResearchAsset;
  chart: ShortResearchChartPoint[];
  return_windows: Record<string, number | null>;
  explanation_sections: Record<string, string>;
};

export type ShortResearchSignalRun = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  as_of_date: string;
  config: Record<string, unknown>;
  summary: Record<string, unknown>;
  error_message: string | null;
  scope_kind?: string | null;
  scope_hash?: string | null;
  universe_snapshot_hash?: string | null;
  input_snapshot_hash?: string | null;
  score_version?: string | null;
  rule_version?: string | null;
  ranking_contract_hash?: string | null;
  score_field?: string | null;
  data_cutoff?: string | null;
  as_of_trade_date?: string | null;
  price_basis?: string | null;
  expected_item_count?: number | null;
  eligible_item_count?: number | null;
  coverage_ratio?: number | null;
  publication_state?: string | null;
  published_at?: string | null;
  idempotency_key?: string | null;
  items: ShortResearchAsset[];
};

export type ShortResearchObservationPortfolioItem = {
  asset_type: "etf";
  code: string;
  name: string;
  target_weight: number;
  score: number;
  conclusion: string;
  data_date: string | null;
  primary_theme?: string | null;
  theme_group?: string | null;
  evidence: string[];
  risk_reasons: string[];
  inclusion_reasons?: string[];
  exclusion_reasoning?: string[];
  weight_reason?: string | null;
  validation_confidence?: "sufficient" | "limited" | "insufficient" | string;
  score_factors?: Record<string, unknown>;
  data_quality?: Record<string, unknown>;
  entry_timing_label?: string | null;
  item_type?: string | null;
  exclusion_reason?: string | null;
  weight_explanation?: string | null;
  exclusion_explanation?: string | null;
  decision_factors?: Record<string, unknown>;
  weight_reason_json?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
};

export type EtfOptimizedAllocationItem = {
  method: string;
  code: string;
  name: string;
  target_weight: number;
  theme_group: string | null;
  expected_return: number | null;
  volatility: number | null;
  data_date: string | null;
  explanation: string | null;
  metrics: Record<string, unknown>;
};

export type EtfOptimizedAllocationMethod = {
  method: string;
  label: string;
  status: string;
  weight_sum: number;
  items: EtfOptimizedAllocationItem[];
  summary: Record<string, unknown> & {
    prior_source?: string | null;
    view_count?: number | null;
    confidence_summary?: Record<string, unknown> | null;
    covariance?: Record<string, unknown> | null;
    constraints?: Record<string, unknown> | null;
    excluded_count?: number | null;
    research_only?: boolean;
    no_trade_instruction?: boolean;
  };
  unavailable_reason: string | null;
};

export type EtfOptimizedAllocation = {
  id: number | null;
  status: string;
  as_of_date: string | null;
  generated_at: string | null;
  method_set: string;
  evidence_contract_hash?: string | null;
  data_window: Record<string, unknown>;
  constraints: Record<string, unknown>;
  summary: Record<string, unknown>;
  unavailable_reason: string | null;
  methods: EtfOptimizedAllocationMethod[];
  research_only: boolean;
  no_trade_instruction: boolean;
};

export type ShortResearchObservationPortfolio = {
  snapshot_id?: number | null;
  generated_at?: string | null;
  as_of_date: string;
  asset_type: "etf";
  items: ShortResearchObservationPortfolioItem[];
  satellite_items?: ShortResearchObservationPortfolioItem[];
  defensive_items?: ShortResearchObservationPortfolioItem[];
  watch_only_items: ShortResearchObservationPortfolioItem[];
  excluded_items: ShortResearchObservationPortfolioItem[];
  cash_weight: number;
  target_invested_weight?: number;
  weight_sum?: number;
  portfolio_mode?: "risk_on" | "neutral" | "defensive" | "cash_wait" | string;
  market_regime?: "risk_on" | "defensive" | "cash_wait" | string;
  primary_weight?: number;
  satellite_weight?: number;
  risk_exposure_weight?: number;
  defensive_weight?: number;
  cash_reason?: string | null;
  single_weight_cap: number | null;
  total_exposure_cap: number | null;
  constraint_summary: Record<string, unknown>;
  constraints_used?: Record<string, unknown>;
  risk_summary?: Record<string, unknown>;
  data_reliability_summary?: Record<string, unknown>;
  unavailable_reason?: string | null;
  research_only: boolean;
  no_trade_instruction: boolean;
  note: string;
  methodology: string;
  allocation_contract?: Record<string, unknown>;
  evidence_status?: string;
  evidence_summary?: Record<string, unknown>;
  optimized_allocation?: EtfOptimizedAllocation | null;
};

export type EtfStrategyHealthcheckItem = {
  item_type: string;
  item_key: string;
  conclusion: string;
  sample_count: number;
  avg_return: number | null;
  win_rate: number | null;
  max_drawdown: number | null;
  metrics: Record<string, unknown>;
};

export type EtfStrategyHealthcheck = {
  id: number | null;
  status: string;
  conclusion: string;
  as_of_date: string | null;
  generated_at: string | null;
  source_signal_run_id: number | null;
  validation_run_id: number | null;
  backtest_run_id: number | null;
  execution_model: string;
  evidence_contract_hash: string | null;
  evidence_status: string;
  data_window: Record<string, unknown>;
  summary: Record<string, unknown> & {
    daily_close_evidence?: string;
    intraday_alert_evidence?: string;
    daily_close_evidence_status?: string;
    intraday_alert_evidence_status?: string;
    backtest_evidence_status?: string;
    weak_themes?: string[];
    weak_market_regimes?: string[];
  };
  metrics: Record<string, unknown>;
  caveats: string[];
  items: EtfStrategyHealthcheckItem[];
  research_only: boolean;
  no_trade_instruction: boolean;
};

export type EtfPortfolioBacktestRunSummary = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  start_date: string;
  end_date: string;
  initial_cash: number;
  fee_rate: number;
  metrics: Record<string, unknown>;
  benchmark: Record<string, unknown>;
  data_coverage: Record<string, unknown>;
  caveats: string[];
  execution_model?: string | null;
  replay_contract?: Record<string, unknown>;
  evidence_status?: string;
  evidence_summary?: Record<string, unknown>;
  error_message: string | null;
};

export type EtfPortfolioBacktestCurvePoint = {
  date: string;
  equity: number;
  cash: number;
  drawdown: number;
  benchmark_equity: number | null;
  portfolio_mode: string;
};

export type EtfPortfolioBacktestTrade = {
  id: number;
  trade_date: string;
  etf_code: string;
  etf_name: string;
  side: string;
  reason: string;
  amount: number;
  shares: number;
  price: number;
  fee: number;
  realized_pnl: number | null;
  metadata: Record<string, unknown>;
};

export type EtfPortfolioBacktestLabelSummary = {
  label: string;
  entry_timing_label: string;
  horizon_days: number;
  sample_count: number;
  avg_return: number | null;
  median_return: number | null;
  win_rate: number | null;
  worst_forward_drawdown: number | null;
  confidence: string;
  metrics: Record<string, unknown>;
};

export type EtfPortfolioBacktestDetail = EtfPortfolioBacktestRunSummary & {
  equity_curve: EtfPortfolioBacktestCurvePoint[];
  trades: EtfPortfolioBacktestTrade[];
  latest_positions: Array<{
    snapshot_date: string;
    etf_code: string;
    etf_name: string;
    shares: number;
    price: number;
    market_value: number;
    weight: number;
    cost_basis: number | null;
    unrealized_pnl: number | null;
    metadata: Record<string, unknown>;
  }>;
  label_summaries: EtfPortfolioBacktestLabelSummary[];
};

export type EtfPortfolioBacktestList = {
  items: EtfPortfolioBacktestRunSummary[];
};

export type EtfStrategyComparisonStrategy = {
  strategy_key: string;
  strategy_label: string;
  metrics: Record<string, unknown>;
  equity_curve: Array<Record<string, unknown>>;
  caveats: string[];
};

export type EtfStrategyComparison = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  start_date: string;
  end_date: string;
  initial_cash: number;
  fee_rate: number;
  data_coverage: Record<string, unknown>;
  caveats: string[];
  strategies: EtfStrategyComparisonStrategy[];
  best_strategy: string | null;
  exit_v2_baseline_comparison?: Record<string, unknown> | null;
  exit_v2_evidence_contract?: Record<string, unknown> | null;
  error_message: string | null;
};

export type EtfExitHyperoptItem = {
  id: number;
  bucket_type: string;
  bucket_key: string;
  status: string;
  conclusion: string;
  parameters: Record<string, unknown>;
    train_metrics: Record<string, unknown>;
    out_of_sample_metrics: Record<string, unknown>;
    rolling_metrics: Record<string, unknown>;
    baseline_metrics: Record<string, unknown>;
    baseline_comparison: Record<string, unknown>;
    rejection_reason: string | null;
    coverage_status: string | null;
    manual_delay_minutes: number | null;
    policy_class: string | null;
    policy_class_label?: string | null;
    evidence_status?: string | null;
    recommended_usage?: string | null;
    approval_status: string | null;
    approved_for_live: boolean;
    is_live_rule_evidence?: boolean;
    protection_guard_version: string | null;
    guard_enabled_metrics: Record<string, unknown>;
    confidence: Record<string, unknown>;
  source_reliability: string | null;
  score: number;
  sample_count: number;
  trade_count: number;
  approved_at: string | null;
  created_at: string;
};

export type EtfExitHyperopt = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  as_of_date: string;
  objective: string;
  rule_version: string;
  calibration_rule_version: string | null;
  execution_model: string | null;
  contract_hash: string | null;
  data_cutoff: string | null;
  train_range: Record<string, unknown>;
  out_of_sample_range: Record<string, unknown>;
  search_space: Record<string, unknown>;
  bucket_summary: Record<string, unknown>;
  summary: Record<string, unknown>;
  error_message: string | null;
  research_only: boolean;
  no_trade_instruction: boolean;
  items: EtfExitHyperoptItem[];
};

export type EtfExitCredibilityEvent = {
  id: number;
  etf_code: string;
  etf_name: string | null;
  signal_type: string;
  signal_time: string | null;
  signal_date: string;
  signal_price: number;
  outcome: string;
  forward_window_days: number;
  forward_return: number | null;
  max_favorable_return: number | null;
  max_adverse_return: number | null;
  context: Record<string, unknown>;
};

export type EtfExitCredibilityItem = {
  id: number;
  signal_type: string;
  group_type: string;
  group_key: string;
  policy_class?: string | null;
  policy_class_label?: string | null;
  evidence_status?: string | null;
  recommended_usage?: string | null;
  strong_conclusion_allowed?: boolean;
  is_live_rule_evidence?: boolean;
  evidence_level: string;
  sample_count: number;
  success_avoidance_rate: number | null;
  false_stop_rate: number | null;
  sold_too_early_rate: number | null;
  avg_avoided_drawdown: number | null;
  avg_missed_upside: number | null;
  avg_forward_return: number | null;
  metrics: Record<string, unknown>;
  kpi_summary?: Record<string, unknown>;
  events: EtfExitCredibilityEvent[];
  created_at: string;
};

export type EtfExitCredibility = {
  id: number;
  status: string;
  started_at: string;
  finished_at: string | null;
  as_of_date: string;
  execution_model: string;
  signal_version: string;
  exit_rule_version: string;
  contract_hash: string | null;
  evidence_status: string;
  data_cutoff: string | null;
  data_window: Record<string, unknown>;
  summary: Record<string, unknown>;
  insufficiency_reasons: string[];
  error_message: string | null;
  research_only: boolean;
  no_trade_instruction: boolean;
  items: EtfExitCredibilityItem[];
};

export type TrackedPositionSnapshot = {
  current_price: number | null;
  current_price_date: string | null;
  estimated_value: number | null;
  estimated_pnl: number | null;
  estimated_pnl_pct: number | null;
  current_label: string | null;
  advisor_label: string | null;
  risk_flags: string[];
  explanation: string | null;
  data_reliability: string;
  price_source: string;
  decision_eligible: boolean;
  display_only_reason: string | null;
};

export type TrackedPositionExitSignal = {
  alert_type: string | null;
  label: string;
  level: "none" | "watch" | "warning" | "urgent";
  action_class: "none" | "actionable_exit" | "soft_watch" | "guard_only" | "data_waiting" | "research_only";
  position_action: string | null;
  action_version: string | null;
  reentry_rule_version: string | null;
  guard_state: string | null;
  guard_reasons: string[];
  threshold_context: Record<string, unknown>;
  approved_for_live: boolean;
  no_alert_reason: string | null;
  reason: string | null;
  reasons: string[];
  email_eligible: boolean;
  email_eligibility_reason: string | null;
  data_reliability: string | null;
};

export type TrackedPositionAlert = {
  id: number;
  tracked_position_id: number;
  alert_date: string;
  alert_type: string;
  trigger_label: string;
  current_price: number | null;
  current_price_date: string | null;
  estimated_value: number | null;
  estimated_pnl: number | null;
  estimated_pnl_pct: number | null;
  reasons: string[];
  risk_flags: string[];
  advisor_summary: string | null;
  alert_level: string | null;
  quote_time: string | null;
  alert_source: string | null;
  suppression_status: string | null;
  email_status: string;
  email_error_message: string | null;
  sent_at: string | null;
  created_at: string;
  audit_outcome?: string | null;
  suppression_reason?: string | null;
  data_ineligible_reason?: string | null;
  delivery_status?: string | null;
  audit_context?: Record<string, unknown>;
  quote_freshness?: number | null;
  threshold_context: Record<string, unknown>;
};

export type TrackedPositionAlertAudit = {
  id: number;
  tracked_position_id: number;
  tracked_position_alert_id: number | null;
  outcome: string;
  signal_type: string | null;
  alert_date: string;
  alert_type: string;
  trigger_label: string | null;
  data_source: string;
  quote_freshness: string;
  threshold_context: Record<string, unknown>;
  decision_context: Record<string, unknown>;
  recipient: string | null;
  duplicate_reason: string | null;
  cooldown_reason: string | null;
  smtp_result: string | null;
  smtp_error_message: string | null;
  quote_time: string | null;
  created_at: string;
  audit_summary: string;
};

export type TrackedPositionAlertAuditList = {
  items: TrackedPositionAlertAudit[];
  total: number;
};

export type TrackedPositionChartPoint = {
  date: string;
  price: number;
  estimated_value: number | null;
  estimated_pnl_pct: number | null;
  is_entry: boolean;
  is_high: boolean;
  is_current: boolean;
  trailing_stop_pnl_pct: number | null;
};

export type TrackedPosition = {
  id: number;
  asset_type: "fund" | "etf";
  asset_code: string;
  asset_name: string;
  buy_date: string;
  order_time_bucket: "before_15" | "after_15" | "unknown";
  confirmed_nav_date: string | null;
  confirmed_nav: number | null;
  confirmed_shares: number | null;
  buy_amount: number;
  cost_basis: number | null;
  cost_basis_source: string | null;
  entry_price: number | null;
  entry_price_date: string | null;
  estimated_shares: number | null;
  status: string;
  note: string | null;
  created_at: string;
  updated_at: string;
  current_snapshot: TrackedPositionSnapshot;
  exit_signal: TrackedPositionExitSignal;
  position_action: string;
  recommended_action_label: string;
  action_class: string | null;
  exit_action_version: string | null;
  reentry_state: string;
  reentry_reason: string | null;
  reentry_rule_version: string | null;
  current_market_value: number | null;
  current_account_weight: number | null;
  target_account_weight: number | null;
  recommended_trade_amount: number | null;
  recommended_trade_shares: number | null;
  position_sizing_reason: string | null;
  max_profit_pct: number | null;
  profit_giveback_pct: number | null;
  holding_days: number | null;
  technical_metrics: Record<string, unknown>;
  intraday_snapshot: TrackedEtfIntradaySnapshot | null;
  dynamic_thresholds: DynamicExitThresholds | null;
  recent_intraday_alerts: TrackedPositionAlert[];
  latest_alert: TrackedPositionAlert | null;
};

export type TrackedPositionDetail = TrackedPosition & {
  chart: TrackedPositionChartPoint[];
  alerts: TrackedPositionAlert[];
};

export type TrackedPositionList = {
  items: TrackedPosition[];
  total: number;
  email_configured: boolean;
  recipient_email: string;
};

export type EtfIntradayQuote = {
  etf_code: string;
  etf_name: string | null;
  quote_time: string;
  trade_date: string;
  latest_price: number;
  change_percent: number | null;
  volume: number | null;
  turnover: number | null;
  bid_price: number | null;
  ask_price: number | null;
  iopv: number | null;
  premium_discount_pct: number | null;
  source: string;
  freshness_status: string;
  is_stale: boolean;
  quote_time_is_fallback: boolean;
  consensus_status: string;
  quote_reliability: string;
  decision_eligible: boolean;
  provider_count: number;
  fresh_provider_count: number;
  price_diff_abs: number | null;
  price_diff_pct: number | null;
  limitation_reason: string | null;
};

export type IntradayEtfWatchItem = {
  etf_code: string;
  etf_name: string | null;
  rank: number | null;
  sources: string[];
  quote: EtfIntradayQuote | null;
};

export type IntradayEtfWatchRun = {
  id: number;
  run_type: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  market_session: string | null;
  watched_count: number;
  updated_quote_count: number;
  stale_quote_count: number;
  alert_count: number;
  email_sent_count: number;
  suppressed_count: number;
  skipped_reason: string | null;
  error_message: string | null;
  details: Record<string, unknown>;
};

export type IntradayEtfWatchStatus = {
  market_status: string;
  market_session: string | null;
  message: string;
  quote_refresh_seconds: number;
  page_poll_seconds: number;
  watched_count: number;
  top20_signal_run_id: number | null;
  signal_as_of_date: string | null;
  signal_status: string;
  latest_run: IntradayEtfWatchRun | null;
  items: IntradayEtfWatchItem[];
};

export type EtfRankingSnapshotMetadata = {
  snapshot_id?: number | null;
  score_version?: string | null;
  ranking_contract_hash?: string | null;
  scope_kind?: string | null;
  as_of_trade_date?: string | null;
  generated_at?: string | null;
  coverage_ratio?: number | null;
  freshness_status?: string | null;
  limitations?: string[];
};

export type IntradayEtfLiveRankingItem = {
  etf_code: string;
  etf_name: string | null;
  base_rank: number | null;
  live_rank: number | null;
  base_global_rank?: number | null;
  live_scope_rank?: number | null;
  filtered_position?: number | null;
  rank_scope?: string | null;
  rank_change: number | null;
  sources: string[];
  conclusion: string | null;
  base_score: number | null;
  live_total_score: number | null;
  intraday_adjustment_score: number | null;
  score_source: "intraday" | "daily" | "unavailable";
  score_version: string | null;
  score_breakdown: ShortResearchScoreBreakdown;
  score_contribution_reasons: string[];
  intraday_component_status?: Record<string, Record<string, unknown>>;
  live_entry_timing_label: string;
  live_entry_timing_reason: string;
  daily_entry_timing_label: string;
  daily_entry_timing_reason: string;
  quote: EtfIntradayQuote | null;
};

export type IntradayEtfLiveRankingList = {
  market_status: string;
  market_session: string | null;
  message: string;
  quote_refresh_seconds: number;
  page_poll_seconds: number;
  next_poll_seconds?: number;
  watched_count: number;
  total: number;
  signal_as_of_date: string | null;
  signal_status: string;
  live_scope_hash?: string | null;
  latest_run: IntradayEtfWatchRun | null;
  items: IntradayEtfLiveRankingItem[];
  snapshot?: EtfRankingSnapshotMetadata | null;
};

export type TrackedEtfIntradaySnapshot = {
  current_price: number | null;
  quote_time: string | null;
  trade_date: string | null;
  price_source: string;
  reliability_level: string;
  email_eligible: boolean;
  email_eligibility_reason: string | null;
  is_stale: boolean;
  freshness_status: string | null;
  bid_price: number | null;
  ask_price: number | null;
  spread_pct: number | null;
  iopv: number | null;
  premium_discount_pct: number | null;
  turnover: number | null;
  source: string | null;
  message: string | null;
  consensus_status: string | null;
  quote_reliability: string | null;
  decision_eligible: boolean;
  provider_count: number;
  fresh_provider_count: number;
  price_diff_abs: number | null;
  price_diff_pct: number | null;
  limitation_reason: string | null;
};

export type DynamicExitThresholds = {
  threshold_source: string;
  rule_version: string;
  calibration_run_id: number | null;
  calibration_candidate_id: number | null;
  calibration_bucket_key: string | null;
  calibration_version: string | null;
  volatility_unit_pct: number | null;
  hard_stop_pct: number | null;
  profit_start_pct: number | null;
  trailing_giveback_pct: number | null;
  trend_weakening: boolean;
  distance_to_hard_stop_pct: number | null;
  distance_to_profit_start_pct: number | null;
  distance_to_trailing_giveback_pct: number | null;
  trend_weakening_distance_pct: number | null;
  explanation: string[];
  liquidity_warnings: string[];
  structure_warnings: string[];
};
