from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AssetType = Literal["fund"]
StrategyType = Literal["momentum_rotation", "dca_baseline", "screening"]
RunType = Literal["backtest", "simulation_update", "screening"]


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    strategy_type: StrategyType
    asset_type: AssetType = "fund"
    config: dict[str, Any] = Field(default_factory=dict)


class StrategyOut(BaseModel):
    id: int
    name: str
    strategy_type: StrategyType
    asset_type: AssetType
    status: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class BacktestRequest(BaseModel):
    start_date: date
    end_date: date


class EvaluationRequest(BaseModel):
    strategy_id: int
    start_date: date
    end_date: date


class OrderOut(BaseModel):
    id: int
    submitted_date: date | None = None
    trade_date: date
    confirmed_date: date | None = None
    asset_code: str
    asset_name: str | None = None
    side: str
    amount: float
    shares: float
    price: float
    fee: float
    status: str
    platform: str


class PositionOut(BaseModel):
    id: int
    snapshot_date: date
    asset_code: str
    asset_name: str | None = None
    shares: float
    market_value: float
    weight: float


class EquityPointOut(BaseModel):
    id: int
    curve_date: date
    equity: float
    cash: float
    drawdown: float


class StrategyRunOut(BaseModel):
    id: int
    strategy_id: int
    run_type: RunType
    status: str
    started_at: datetime
    finished_at: datetime | None
    as_of_date: date
    date_range: dict[str, Any]
    metrics: dict[str, Any]
    error_message: str | None
    orders: list[OrderOut]
    positions: list[PositionOut]
    equity_curve: list[EquityPointOut]


class PaperStartRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    started_at: date


class PaperRunRequest(BaseModel):
    as_of_date: date


class PaperPortfolioOut(BaseModel):
    id: int
    strategy_id: int
    name: str
    status: str
    started_at: date
    cash: float
    latest_equity: float
    latest_run: StrategyRunOut | None = None


class PaperReconciliationItemOut(BaseModel):
    asset_code: str
    asset_name: str | None = None
    paper_shares: float
    actual_shares: float
    share_diff: float
    paper_market_value: float
    actual_market_value: float
    market_value_diff: float


class PaperReconciliationOut(BaseModel):
    paper_id: int
    strategy_id: int
    as_of_date: date
    platform_profile: str
    paper_equity: float
    actual_equity: float
    equity_diff: float
    items: list[PaperReconciliationItemOut]
    note: str


class StrategyEvaluationItemOut(BaseModel):
    id: int
    rank_order: int
    item_type: str
    label: str
    parameters: dict[str, Any]
    metrics: dict[str, Any]
    in_sample_metrics: dict[str, Any]
    out_of_sample_metrics: dict[str, Any]
    rolling_windows: list[dict[str, Any]]
    score: float
    risk_flags: list[str]


class StrategyEvaluationOut(BaseModel):
    id: int
    strategy_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    start_date: date
    end_date: date
    data_coverage: dict[str, Any]
    summary: dict[str, Any]
    conclusion: str
    risk_flags: list[str]
    items: list[StrategyEvaluationItemOut] = Field(default_factory=list)
