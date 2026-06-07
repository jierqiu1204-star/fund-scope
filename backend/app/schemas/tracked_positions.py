from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class TrackedPositionCreate(BaseModel):
    asset_type: Literal["fund", "etf"]
    asset_code: str
    buy_date: date | None = None
    buy_amount: float = Field(default=3000, gt=0)
    note: str | None = None


class TrackedPositionUpdate(BaseModel):
    buy_date: date | None = None
    buy_amount: float | None = Field(default=None, gt=0)
    note: str | None = None
    status: Literal["active", "handled", "closed", "stopped"] | None = None


class TrackedPositionCloseRequest(BaseModel):
    status: Literal["handled", "closed", "stopped"] = "closed"
    note: str | None = None


class TrackedPositionSnapshot(BaseModel):
    current_price: float | None = None
    current_price_date: date | None = None
    estimated_value: float | None = None
    estimated_pnl: float | None = None
    estimated_pnl_pct: float | None = None
    current_label: str | None = None
    advisor_label: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    explanation: str | None = None


class TrackedPositionAlertOut(BaseModel):
    id: int
    tracked_position_id: int
    alert_date: date
    alert_type: str
    trigger_label: str
    current_price: float | None
    current_price_date: date | None
    estimated_value: float | None
    estimated_pnl: float | None
    estimated_pnl_pct: float | None
    reasons: list[str]
    risk_flags: list[str]
    advisor_summary: str | None
    email_status: str
    email_error_message: str | None
    sent_at: datetime | None
    created_at: datetime


class TrackedPositionChartPoint(BaseModel):
    date: date
    price: float
    estimated_value: float | None = None
    estimated_pnl_pct: float | None = None


class TrackedPositionOut(BaseModel):
    id: int
    asset_type: str
    asset_code: str
    asset_name: str
    buy_date: date
    buy_amount: float
    entry_price: float | None
    entry_price_date: date | None
    estimated_shares: float | None
    status: str
    note: str | None
    created_at: datetime
    updated_at: datetime
    current_snapshot: TrackedPositionSnapshot
    latest_alert: TrackedPositionAlertOut | None = None


class TrackedPositionDetailOut(TrackedPositionOut):
    chart: list[TrackedPositionChartPoint]
    alerts: list[TrackedPositionAlertOut]


class TrackedPositionListOut(BaseModel):
    items: list[TrackedPositionOut]
    total: int
    email_configured: bool
    recipient_email: str

