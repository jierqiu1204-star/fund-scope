from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ShortResearchDataHealthOut(BaseModel):
    asset_type: str
    code: str
    name: str
    status: str
    latest_date: date | None = None
    usable_days: int
    provider: str | None = None
    source_note: str
    last_error_message: str | None = None
    is_stale: bool


class ShortResearchStatusOut(BaseModel):
    latest_data_date: date | None = None
    signal_date: date | None = None
    asset_count: int
    fund_count: int
    etf_count: int
    etf_total_count: int = 0
    etf_eligible_count: int = 0
    etf_default_display_count: int = 0
    etf_data_stale_count: int = 0
    etf_failed_count: int = 0
    priced_asset_count: int
    observable_count: int
    high_risk_count: int
    data_issue_count: int
    data_health: list[ShortResearchDataHealthOut] = Field(default_factory=list)
    label_validation: dict[str, Any] = Field(default_factory=dict)
    label_validation_generated_at: datetime | None = None
    score_bucket_validation: dict[str, Any] = Field(default_factory=dict)
    score_bucket_validation_generated_at: datetime | None = None
    theme_coverage: dict[str, Any] = Field(default_factory=dict)


class EtfSignalValidationItemOut(BaseModel):
    label: str
    entry_timing_label: str
    horizon_days: int
    sample_count: int
    excluded_count: int = 0
    avg_return: float | None = None
    median_return: float | None = None
    win_rate: float | None = None
    worst_forward_drawdown: float | None = None
    confidence: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class EtfSignalValidationRunOut(BaseModel):
    id: int
    status: str
    as_of_date: date
    source_signal_run_id: int | None = None
    validation_mode: str = "forward_live"
    rule_version: str
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    items: list[EtfSignalValidationItemOut] = Field(default_factory=list)


class ShortResearchDataSyncRequest(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    days: int = Field(default=120, ge=1, le=1500)
    asset_type: str | None = None
    codes: list[str] | None = None


class ShortResearchSignalRunRequest(BaseModel):
    as_of_date: date | None = None
    asset_type: str | None = None
    theme: str | None = None
    codes: list[str] | None = None


class ShortResearchAdvisorRunRequest(BaseModel):
    as_of_date: date | None = None
    asset_type: str | None = None
    theme: str | None = None
    codes: list[str] | None = None


class ShortResearchChartPointOut(BaseModel):
    date: date
    value: float
    close: float | None = None
    nav: float | None = None
    drawdown: float
    turnover: float | None = None


class ShortResearchAdvisorReportOut(BaseModel):
    id: int
    status: str
    action_label: str
    plain_summary: str
    opportunity: list[str]
    risks: list[str]
    opposing_view: str
    watch_conditions: list[str]
    holding_note: str
    data_limitations: str
    model_name: str
    prompt_version: str
    source: str
    generated_at: datetime


class ShortResearchAssetOut(BaseModel):
    asset_type: str
    code: str
    name: str
    rank: int | None = None
    total_score: float
    technical_score: float | None = None
    opportunity_score: float | None = None
    opportunity_label: str | None = None
    opportunity_score_version: str | None = None
    sector_trend_score: float | None = None
    sector_trend_label: str | None = None
    sector_trend_summary: str | None = None
    sector_trend_reason: str | None = None
    sector_trend_status: str | None = None
    sector_peer_count: int | None = None
    catalyst_score: float | None = None
    sentiment_heat_score: float | None = None
    catalyst_summary: str | None = None
    catalyst_events: list[dict[str, Any]] = Field(default_factory=list)
    catalyst_limitations: list[str] = Field(default_factory=list)
    factor_profile_version: str | None = None
    factor_profile_status: str | None = None
    factor_profile_score: float | None = None
    factor_group_scores: dict[str, Any] = Field(default_factory=dict)
    factor_scores: list[dict[str, Any]] = Field(default_factory=list)
    factor_availability: dict[str, Any] = Field(default_factory=dict)
    risk_gates: list[dict[str, Any]] = Field(default_factory=list)
    opportunity_breakdown: dict[str, Any] = Field(default_factory=dict)
    conclusion: str
    entry_timing_label: str
    entry_timing_reason: str
    theme_tags: list[str]
    theme_group: str | None = None
    primary_theme: str | None = None
    secondary_themes: list[str] = Field(default_factory=list)
    classification_source: str | None = None
    classification_confidence: str | None = None
    classification_reason: str | None = None
    theme_profile: dict[str, Any] = Field(default_factory=dict)
    investment_direction: str
    trading_rule_label: str
    latest_date: date | None = None
    latest_value: float | None = None
    usable_days: int
    sample_level: str
    metrics: dict[str, Any]
    score_breakdown: dict[str, Any]
    risk_flags: list[str]
    rationale: dict[str, Any]
    source_note: str
    advisor_report: ShortResearchAdvisorReportOut | None = None
    validation_evidence: dict[str, Any] = Field(default_factory=dict)
    observation_portfolio: dict[str, Any] = Field(default_factory=dict)
    research_signal_contract: dict[str, Any] = Field(default_factory=dict)
    evidence_status: str = "等待验证"
    evidence_summary: dict[str, Any] = Field(default_factory=dict)


class ShortResearchAssetListOut(BaseModel):
    items: list[ShortResearchAssetOut]
    total: int
    generated_at: datetime | None = None
    as_of_date: date | None = None
    theme_heat: list[dict[str, Any]] = Field(default_factory=list)


class ShortResearchAssetDetailOut(BaseModel):
    asset: ShortResearchAssetOut
    chart: list[ShortResearchChartPointOut]
    return_windows: dict[str, float | None]
    explanation_sections: dict[str, str]


class ShortResearchSignalRunOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    as_of_date: date
    config: dict[str, Any]
    summary: dict[str, Any]
    error_message: str | None
    items: list[ShortResearchAssetOut]


class ShortResearchObservationPortfolioItemOut(BaseModel):
    asset_type: str
    code: str
    name: str
    target_weight: float
    score: float
    conclusion: str
    data_date: date | None = None
    primary_theme: str | None = None
    theme_group: str | None = None
    evidence: list[str]
    risk_reasons: list[str]
    entry_timing_label: str | None = None
    item_type: str | None = None
    exclusion_reason: str | None = None
    weight_explanation: str | None = None
    exclusion_explanation: str | None = None
    decision_factors: dict[str, Any] = Field(default_factory=dict)
    weight_reason_json: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)


class EtfOptimizedAllocationItemOut(BaseModel):
    method: str
    code: str
    name: str
    target_weight: float
    expected_return: float | None = None
    volatility: float | None = None
    theme_group: str | None = None
    data_date: date | None = None
    explanation: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class EtfOptimizedAllocationMethodOut(BaseModel):
    method: str
    label: str
    status: str
    items: list[EtfOptimizedAllocationItemOut] = Field(default_factory=list)
    weight_sum: float = 0.0
    summary: dict[str, Any] = Field(default_factory=dict)
    unavailable_reason: str | None = None


class EtfOptimizedAllocationOut(BaseModel):
    id: int | None = None
    status: str
    as_of_date: date | None = None
    generated_at: datetime | None = None
    method_set: str = "stable_v1"
    evidence_contract_hash: str | None = None
    data_window: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    unavailable_reason: str | None = None
    methods: list[EtfOptimizedAllocationMethodOut] = Field(default_factory=list)
    research_only: bool = True
    no_trade_instruction: bool = True


class ShortResearchObservationPortfolioOut(BaseModel):
    as_of_date: date
    asset_type: str
    items: list[ShortResearchObservationPortfolioItemOut]
    satellite_items: list[ShortResearchObservationPortfolioItemOut] = Field(default_factory=list)
    defensive_items: list[ShortResearchObservationPortfolioItemOut] = Field(default_factory=list)
    watch_only_items: list[ShortResearchObservationPortfolioItemOut] = Field(default_factory=list)
    excluded_items: list[ShortResearchObservationPortfolioItemOut] = Field(default_factory=list)
    cash_weight: float
    target_invested_weight: float = 1.0
    weight_sum: float = 0.0
    portfolio_mode: str = "risk_on"
    market_regime: str = "risk_on"
    primary_weight: float = 0.0
    satellite_weight: float = 0.0
    risk_exposure_weight: float = 0.0
    defensive_weight: float = 0.0
    cash_reason: str | None = None
    single_weight_cap: float | None = None
    total_exposure_cap: float | None = None
    constraint_summary: dict[str, Any] = Field(default_factory=dict)
    constraints_used: dict[str, Any] = Field(default_factory=dict)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    data_reliability_summary: dict[str, Any] = Field(default_factory=dict)
    unavailable_reason: str | None = None
    research_only: bool
    no_trade_instruction: bool
    note: str
    methodology: str = ""
    snapshot_id: int | None = None
    generated_at: datetime | None = None
    data_as_of_time: datetime | None = None
    quote_time: datetime | None = None
    daily_signal_date: date | None = None
    portfolio_generated_at: datetime | None = None
    weight_fill_reason: str | None = None
    allocation_contract: dict[str, Any] = Field(default_factory=dict)
    evidence_status: str = "等待验证"
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    optimized_allocation: EtfOptimizedAllocationOut | None = None


class EtfStrategyHealthcheckItemOut(BaseModel):
    item_type: str
    item_key: str
    conclusion: str
    sample_count: int
    avg_return: float | None = None
    win_rate: float | None = None
    max_drawdown: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class EtfStrategyHealthcheckOut(BaseModel):
    id: int | None = None
    status: str
    conclusion: str
    as_of_date: date | None = None
    generated_at: datetime | None = None
    source_signal_run_id: int | None = None
    validation_run_id: int | None = None
    backtest_run_id: int | None = None
    execution_model: str = "daily_close"
    evidence_contract_hash: str | None = None
    evidence_status: str = "等待验证"
    data_window: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    items: list[EtfStrategyHealthcheckItemOut] = Field(default_factory=list)
    research_only: bool = True
    no_trade_instruction: bool = True


class EtfPortfolioBacktestRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    days: int = Field(default=180, ge=60, le=1000)
    initial_cash: float | None = Field(default=None, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, le=0.02)
    max_assets: int = Field(default=180, ge=20, le=500)
    execution_model: Literal["daily_close", "intraday_alert"] = "daily_close"


class EtfPortfolioBacktestRunSummaryOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    start_date: date
    end_date: date
    initial_cash: float
    fee_rate: float
    metrics: dict[str, Any] = Field(default_factory=dict)
    benchmark: dict[str, Any] = Field(default_factory=dict)
    data_coverage: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    execution_model: str | None = None
    replay_contract: dict[str, Any] = Field(default_factory=dict)
    evidence_status: str = "等待验证"
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class EtfPortfolioBacktestCurvePointOut(BaseModel):
    date: date
    equity: float
    cash: float
    drawdown: float
    benchmark_equity: float | None = None
    portfolio_mode: str


class EtfPortfolioBacktestTradeOut(BaseModel):
    id: int
    trade_date: date
    etf_code: str
    etf_name: str
    side: str
    reason: str
    amount: float
    shares: float
    price: float
    fee: float
    realized_pnl: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EtfPortfolioBacktestPositionOut(BaseModel):
    snapshot_date: date
    etf_code: str
    etf_name: str
    shares: float
    price: float
    market_value: float
    weight: float
    cost_basis: float | None = None
    unrealized_pnl: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EtfPortfolioBacktestLabelSummaryOut(BaseModel):
    label: str
    entry_timing_label: str
    horizon_days: int
    sample_count: int
    avg_return: float | None = None
    median_return: float | None = None
    win_rate: float | None = None
    worst_forward_drawdown: float | None = None
    confidence: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class EtfPortfolioBacktestDetailOut(EtfPortfolioBacktestRunSummaryOut):
    equity_curve: list[EtfPortfolioBacktestCurvePointOut] = Field(default_factory=list)
    trades: list[EtfPortfolioBacktestTradeOut] = Field(default_factory=list)
    latest_positions: list[EtfPortfolioBacktestPositionOut] = Field(default_factory=list)
    label_summaries: list[EtfPortfolioBacktestLabelSummaryOut] = Field(default_factory=list)


class EtfPortfolioBacktestListOut(BaseModel):
    items: list[EtfPortfolioBacktestRunSummaryOut] = Field(default_factory=list)


class EtfStrategyComparisonRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    days: int = Field(default=180, ge=60, le=1000)
    initial_cash: float | None = Field(default=None, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, le=0.02)
    max_assets: int = Field(default=180, ge=20, le=500)


class EtfStrategyComparisonOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    start_date: date
    end_date: date
    initial_cash: float
    fee_rate: float
    data_coverage: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    strategies: list[dict[str, Any]] = Field(default_factory=list)
    best_strategy: str | None = None
    exit_v2_baseline_comparison: dict[str, Any] | None = None
    exit_v2_evidence_contract: dict[str, Any] | None = None
    error_message: str | None = None


class EtfExitHyperoptRequest(BaseModel):
    days: int = Field(default=730, ge=120, le=1500)
    max_assets: int | None = Field(default=None, ge=20, le=2000)
    objective: str = "stability_first"
    execution_model: Literal["intraday_alert", "daily_close"] = "intraday_alert"
    manual_delay_minutes: int = Field(default=3, ge=0, le=60)
    universe_scope: Literal["all_eligible", "latest_opportunity_top"] = "all_eligible"
    batch_size: int = Field(default=100, ge=20, le=500)


class EtfExitHyperoptItemOut(BaseModel):
    id: int
    bucket_type: str
    bucket_key: str
    status: str
    conclusion: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    train_metrics: dict[str, Any] = Field(default_factory=dict)
    out_of_sample_metrics: dict[str, Any] = Field(default_factory=dict)
    rolling_metrics: dict[str, Any] = Field(default_factory=dict)
    baseline_metrics: dict[str, Any] = Field(default_factory=dict)
    baseline_comparison: dict[str, Any] = Field(default_factory=dict)
    rejection_reason: str | None = None
    coverage_status: str | None = None
    manual_delay_minutes: int | None = None
    policy_class: str | None = None
    approval_status: str | None = None
    approved_for_live: bool = False
    protection_guard_version: str | None = None
    guard_enabled_metrics: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)
    source_reliability: str | None = None
    score: float
    sample_count: int
    trade_count: int
    approved_at: datetime | None = None
    created_at: datetime


class EtfExitHyperoptRunOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    as_of_date: date
    objective: str
    rule_version: str
    calibration_rule_version: str | None = None
    execution_model: str | None = None
    contract_hash: str | None = None
    data_cutoff: datetime | None = None
    train_range: dict[str, Any] = Field(default_factory=dict)
    out_of_sample_range: dict[str, Any] = Field(default_factory=dict)
    search_space: dict[str, Any] = Field(default_factory=dict)
    bucket_summary: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    research_only: bool = True
    no_trade_instruction: bool = True
    items: list[EtfExitHyperoptItemOut] = Field(default_factory=list)


class EtfExitCredibilityRequest(BaseModel):
    days: int = Field(default=730, ge=30, le=1500)
    max_assets: int = Field(default=50, ge=1, le=2000)
    execution_model: Literal["intraday_alert", "daily_close"] = "intraday_alert"
    universe_scope: Literal["latest_opportunity_top"] = "latest_opportunity_top"


class EtfExitCredibilityEventOut(BaseModel):
    id: int
    etf_code: str
    etf_name: str | None = None
    signal_type: str
    signal_time: datetime | None = None
    signal_date: date
    signal_price: float
    outcome: str
    forward_window_days: int
    forward_return: float | None = None
    max_favorable_return: float | None = None
    max_adverse_return: float | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class EtfExitCredibilityItemOut(BaseModel):
    id: int
    signal_type: str
    group_type: str
    group_key: str
    evidence_level: str
    sample_count: int
    success_avoidance_rate: float | None = None
    false_stop_rate: float | None = None
    sold_too_early_rate: float | None = None
    avg_avoided_drawdown: float | None = None
    avg_missed_upside: float | None = None
    avg_forward_return: float | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    events: list[EtfExitCredibilityEventOut] = Field(default_factory=list)
    created_at: datetime


class EtfExitCredibilityRunOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    as_of_date: date
    execution_model: str
    signal_version: str
    exit_rule_version: str
    contract_hash: str | None = None
    evidence_status: str
    data_cutoff: datetime | None = None
    data_window: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    insufficiency_reasons: list[str] = Field(default_factory=list)
    error_message: str | None = None
    research_only: bool = True
    no_trade_instruction: bool = True
    items: list[EtfExitCredibilityItemOut] = Field(default_factory=list)
