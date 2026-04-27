from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TransactionCreate(BaseModel):
    fund_code: str
    action: Literal["buy", "sell"]
    amount: float | None = None
    shares: float | None = None
    nav_at_trade: float
    fee: float = 0.0
    traded_at: date

    @model_validator(mode="after")
    def validate_payload(self) -> TransactionCreate:
        if self.action == "buy" and self.amount is None:
            raise ValueError("Buy transactions require amount")
        if self.action == "sell" and self.shares is None:
            raise ValueError("Sell transactions require shares")
        return self


class TransactionRead(BaseModel):
    id: int
    fund_code: str
    action: str
    amount: float | None
    shares: float
    proceeds: float | None
    nav_at_trade: float
    fee: float
    traded_at: date


class TransactionList(BaseModel):
    total: int
    items: list[TransactionRead]


class CsvImportError(BaseModel):
    row: int
    message: str


class CsvImportResponse(BaseModel):
    inserted: int
    errors: list[CsvImportError] = Field(default_factory=list)
