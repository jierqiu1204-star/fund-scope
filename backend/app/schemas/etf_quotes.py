from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


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


class TrackedEtfIntradaySnapshotOut(BaseModel):
    current_price: float | None = None
    quote_time: datetime | None = None
    trade_date: date | None = None
    price_source: str = "unavailable"
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


class DynamicExitThresholdsOut(BaseModel):
    volatility_unit_pct: float | None = None
    hard_stop_pct: float | None = None
    profit_start_pct: float | None = None
    trailing_giveback_pct: float | None = None
    trend_weakening: bool = False
    liquidity_warnings: list[str] = Field(default_factory=list)
    structure_warnings: list[str] = Field(default_factory=list)
