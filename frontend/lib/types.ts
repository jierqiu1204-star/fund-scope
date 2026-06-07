export type HoldingItem = {
  fund_code: string;
  fund_name: string;
  shares: number;
  cost_basis: number;
  market_value: number;
  pnl: number;
  pnl_pct: number;
  is_stale: boolean;
  as_of_date: string | null;
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
  priced_asset_count: number;
  observable_count: number;
  high_risk_count: number;
  data_issue_count: number;
  data_health: ShortResearchDataHealth[];
};

export type ShortResearchChartPoint = {
  date: string;
  value: number;
  close: number | null;
  nav: number | null;
  drawdown: number;
  turnover: number | null;
};

export type ShortResearchAsset = {
  asset_type: "fund" | "etf";
  code: string;
  name: string;
  rank: number | null;
  total_score: number;
  conclusion: string;
  theme_tags: string[];
  investment_direction: string;
  trading_rule_label: string;
  latest_date: string | null;
  latest_value: number | null;
  usable_days: number;
  sample_level: string;
  metrics: Record<string, unknown>;
  score_breakdown: Record<string, unknown>;
  risk_flags: string[];
  rationale: Record<string, unknown>;
  source_note: string;
  advisor_report: ShortResearchAdvisorReport | null;
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
  items: ShortResearchAsset[];
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
  email_status: string;
  email_error_message: string | null;
  sent_at: string | null;
  created_at: string;
};

export type TrackedPositionChartPoint = {
  date: string;
  price: number;
  estimated_value: number | null;
  estimated_pnl_pct: number | null;
};

export type TrackedPosition = {
  id: number;
  asset_type: "fund" | "etf";
  asset_code: string;
  asset_name: string;
  buy_date: string;
  buy_amount: number;
  entry_price: number | null;
  entry_price_date: string | null;
  estimated_shares: number | null;
  status: string;
  note: string | null;
  created_at: string;
  updated_at: string;
  current_snapshot: TrackedPositionSnapshot;
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
