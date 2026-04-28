from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class IndexWatchlistCreate(BaseModel):
    code: str
    name: str
    region: str = "CN"


class IndexWatchlistItem(BaseModel):
    code: str
    name: str
    region: str
    is_watchlist: bool
    backfill_required: bool = False


class IndexWatchlistRemoval(BaseModel):
    code: str
    is_watchlist: bool
    historical_rows_retained: int


class CurrentValuation(BaseModel):
    index_code: str
    pe: float
    pb: float
    pe_percentile: float | None
    pb_percentile: float | None
    as_of_date: date


class HistoricalValuation(BaseModel):
    date: date
    pe: float
    pb: float
    pe_percentile: float | None
    pb_percentile: float | None
    effective_window: int
