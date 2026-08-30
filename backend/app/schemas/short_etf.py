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
