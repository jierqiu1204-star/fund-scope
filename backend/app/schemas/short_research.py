from __future__ import annotations

from datetime import date, datetime
from typing import Any

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
    conclusion: str
    entry_timing_label: str
    entry_timing_reason: str
    theme_tags: list[str]
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


class ShortResearchAssetListOut(BaseModel):
    items: list[ShortResearchAssetOut]
    total: int
    generated_at: datetime | None = None
    as_of_date: date | None = None


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


class ShortResearchObservationPortfolioOut(BaseModel):
    as_of_date: date
    asset_type: str
    items: list[ShortResearchObservationPortfolioItemOut]
    defensive_items: list[ShortResearchObservationPortfolioItemOut] = Field(default_factory=list)
    watch_only_items: list[ShortResearchObservationPortfolioItemOut] = Field(default_factory=list)
    excluded_items: list[ShortResearchObservationPortfolioItemOut] = Field(default_factory=list)
    cash_weight: float
    target_invested_weight: float = 1.0
    weight_sum: float = 0.0
    portfolio_mode: str = "risk_on"
    market_regime: str = "risk_on"
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
