from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class HoldingItem(BaseModel):
    fund_code: str
    fund_name: str
    shares: float
    cost_basis: float
    market_value: float | None
    pnl: float | None
    pnl_pct: float | None
    is_stale: bool
    as_of_date: date | None
    valuation_status: str = "ready"


class HoldingsResponse(BaseModel):
    items: list[HoldingItem]


class ValueHistoryPoint(BaseModel):
    date: date
    value: float
