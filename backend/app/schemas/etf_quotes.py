from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.short_research import EtfRankingSnapshotMetadataOut


class EtfIntradayQuoteOut(BaseModel):
    etf_code: str
    etf_name: str | None = None
    quote_time: datetime
    trade_date: date
    latest_price: float
    change_percent: float | None = None
    volume: float | None = None
    turnover: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    iopv: float | None = None
    premium_discount_pct: float | None = None
    source: str
    freshness_status: str
    is_stale: bool = False
    quote_time_is_fallback: bool = False
    consensus_status: str = "single_provider"
    quote_reliability: str = "single_fresh"
    decision_eligible: bool = True
    provider_count: int = 1
    fresh_provider_count: int = 1
    price_diff_abs: float | None = None
    price_diff_pct: float | None = None
    limitation_reason: str | None = None


class IntradayEtfWatchRunOut(BaseModel):
    id: int
    run_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    market_session: str | None = None
    watched_count: int
    updated_quote_count: int
    stale_quote_count: int
    alert_count: int
    email_sent_count: int
    suppressed_count: int
    skipped_reason: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class IntradayEtfWatchItemOut(BaseModel):
    etf_code: str
    etf_name: str | None = None
    rank: int | None = None
    sources: list[str] = Field(default_factory=list)
    quote: EtfIntradayQuoteOut | None = None


class IntradayEtfWatchStatusOut(BaseModel):
    market_status: str
    market_session: str | None = None
    message: str
    quote_refresh_seconds: int = 60
    page_poll_seconds: int = 30
    watched_count: int
    top20_signal_run_id: int | None = None
    signal_as_of_date: date | None = None
    signal_status: str
    latest_run: IntradayEtfWatchRunOut | None = None
    items: list[IntradayEtfWatchItemOut] = Field(default_factory=list)


class EtfLiveRankingItemOut(BaseModel):
    etf_code: str
    etf_name: str | None = None
    base_rank: int | None = Field(default=None, description="兼容字段，等于 base_global_rank")
    live_rank: int | None = Field(default=None, description="兼容字段，等于 live_scope_rank")
    base_global_rank: int | None = None
    live_scope_rank: int | None = None
    filtered_position: int | None = None
    rank_scope: str | None = None
    rank_change: int | None = None
    sources: list[str] = Field(default_factory=list)
    conclusion: str | None = None
    base_score: float | None = None
    live_total_score: float | None = None
    intraday_adjustment_score: float | None = None
    score_source: str = "unavailable"
    score_version: str | None = None
    score_breakdown: dict[str, Any] = Field(default_factory=dict)
    score_contribution_reasons: list[str] = Field(default_factory=list)
    live_entry_timing_label: str
    live_entry_timing_reason: str
    daily_entry_timing_label: str
    daily_entry_timing_reason: str
    quote: EtfIntradayQuoteOut | None = None


class EtfLiveRankingListOut(BaseModel):
    market_status: str
    market_session: str | None = None
    message: str
    quote_refresh_seconds: int = 60
    page_poll_seconds: int = 30
    watched_count: int
    total: int
    signal_as_of_date: date | None = None
    signal_status: str
    live_scope_hash: str | None = None
    latest_run: IntradayEtfWatchRunOut | None = None
    items: list[EtfLiveRankingItemOut] = Field(default_factory=list)
    snapshot: EtfRankingSnapshotMetadataOut | None = None


class TrackedEtfIntradaySnapshotOut(BaseModel):
    current_price: float | None = None
    quote_time: datetime | None = None
    trade_date: date | None = None
    price_source: str = "unavailable"
    reliability_level: str = "missing"
    email_eligible: bool = False
    email_eligibility_reason: str | None = None
    is_stale: bool = False
    freshness_status: str | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    spread_pct: float | None = None
    iopv: float | None = None
    premium_discount_pct: float | None = None
    turnover: float | None = None
    source: str | None = None
    message: str | None = None
    consensus_status: str | None = None
    quote_reliability: str | None = None
    decision_eligible: bool = False
    provider_count: int = 0
    fresh_provider_count: int = 0
    price_diff_abs: float | None = None
    price_diff_pct: float | None = None
    limitation_reason: str | None = None


class DynamicExitThresholdsOut(BaseModel):
    threshold_source: str = "rule_dynamic"
    rule_version: str = "dynamic_exit_v2"
    calibration_run_id: int | None = None
    calibration_candidate_id: int | None = None
    calibration_bucket_key: str | None = None
    calibration_version: str | None = None
    calibration_execution_model: str | None = None
    calibration_contract_hash: str | None = None
    calibration_coverage_status: str | None = None
    volatility_unit_pct: float | None = None
    hard_stop_pct: float | None = None
    profit_start_pct: float | None = None
    trailing_giveback_pct: float | None = None
    trend_weakening: bool = False
    distance_to_hard_stop_pct: float | None = None
    distance_to_profit_start_pct: float | None = None
    distance_to_trailing_giveback_pct: float | None = None
    trend_weakening_distance_pct: float | None = None
    explanation: list[str] = Field(default_factory=list)
    liquidity_warnings: list[str] = Field(default_factory=list)
    structure_warnings: list[str] = Field(default_factory=list)
