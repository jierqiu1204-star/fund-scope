from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class EtfUniverseItemOut(BaseModel):
    code: str
    name: str
    exchange: str
    theme_tags: list[str]
    trading_rule_label: str
    asset_class: str
    is_short_term_eligible: bool
    latest_price_date: date | None = None
    latest_close: float | None = None


class EtfUniverseResponse(BaseModel):
    items: list[EtfUniverseItemOut]


class EtfDataSyncRequest(BaseModel):
    from_date: date
    to_date: date
    codes: list[str] | None = None


class EtfDataHealthOut(BaseModel):
    code: str
    name: str
    status: str
    provider: str | None = None
    latest_price_date: date | None = None
    successful_rows: int
    last_error_message: str | None = None
    consecutive_failures: int
    is_stale: bool
    updated_at: datetime | None = None


class EtfDataStatusOut(BaseModel):
    summary: dict[str, Any]
    history_readiness: dict[str, Any] = Field(default_factory=dict)
    items: list[EtfDataHealthOut]


class ShortEtfSignalRunRequest(BaseModel):
    as_of_date: date | None = None
    codes: list[str] | None = None


class ShortEtfSignalItemOut(BaseModel):
    id: int
    etf_code: str
    etf_name: str | None = None
    rank: int
    total_score: float
    conclusion: str
    score_breakdown: dict[str, Any]
    risk_flags: list[str]
    rationale: dict[str, Any]
    theme_tags: list[str] = Field(default_factory=list)
    trading_rule_label: str | None = None


class ShortEtfSignalRunOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    as_of_date: date
    config: dict[str, Any]
    summary: dict[str, Any]
    error_message: str | None
    items: list[ShortEtfSignalItemOut]


class ShortEtfReviewItemOut(BaseModel):
    id: int
    signal_item_id: int
    etf_code: str
    etf_name: str | None = None
    rank: int
    total_score: float
    verdict: str
    agent_notes: dict[str, Any]
    risk_flags: list[str]


class ShortEtfReviewOut(BaseModel):
    id: int
    run_id: int
    status: str
    model_name: str
    started_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any]
    error_message: str | None
    items: list[ShortEtfReviewItemOut]


class ShortEtfEvaluationRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    fee_rate: float = Field(default=0.0005, ge=0, le=0.02)


class ShortEtfEvaluationItemOut(BaseModel):
    id: int
    rank_order: int
    label: str
    item_type: str
    parameters: dict[str, Any]
    metrics: dict[str, Any]
    baseline_metrics: dict[str, Any]
    score: float
    risk_flags: list[str]


class ShortEtfEvaluationOut(BaseModel):
    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    start_date: date
    end_date: date
    sample_days: int
    conclusion: str
    data_coverage: dict[str, Any]
    summary: dict[str, Any]
    risk_flags: list[str]
    error_message: str | None
    items: list[ShortEtfEvaluationItemOut] = Field(default_factory=list)


class ShortEtfPaperStartRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    started_at: date
    initial_cash: float = Field(default=100000, gt=0)


class ShortEtfPaperRunRequest(BaseModel):
    as_of_date: date


class ShortEtfPaperOrderOut(BaseModel):
    id: int
    signal_run_id: int | None
    trade_date: date
    etf_code: str
    etf_name: str | None = None
    side: str
    amount: float
    shares: float
    price: float
    fee: float
    status: str


class ShortEtfPaperEquityPointOut(BaseModel):
    id: int
    curve_date: date
    equity: float
    cash: float
    drawdown: float


class ShortEtfPaperPositionOut(BaseModel):
    etf_code: str
    etf_name: str | None = None
    shares: float
    market_value: float
    weight: float


class ShortEtfPaperOut(BaseModel):
    id: int
    name: str
    status: str
    started_at: date
    cash: float
    latest_equity: float
    summary: dict[str, Any]
    positions: list[ShortEtfPaperPositionOut] = Field(default_factory=list)
    orders: list[ShortEtfPaperOrderOut] = Field(default_factory=list)
    equity_curve: list[ShortEtfPaperEquityPointOut] = Field(default_factory=list)
