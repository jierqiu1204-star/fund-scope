from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class NotificationSettingsRead(BaseModel):
    recipient_email: str
    reminder_day: int
    reference_index_code: str | None
    base_monthly_amount: float
    etf_trading_capital: float
    etf_trading_capital_confirmed_at: datetime | None = None
    allow_full_exit: bool
    smtp_host: str | None
    smtp_port: int | None
    smtp_username: str | None
    smtp_from: str | None


class NotificationSettingsUpdate(BaseModel):
    recipient_email: str
    reminder_day: int
    reference_index_code: str
    base_monthly_amount: float
    etf_trading_capital: float = Field(gt=0)
    allow_full_exit: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_from: str
    smtp_password: str | None = None

    @field_validator("etf_trading_capital", mode="before")
    @classmethod
    def validate_finite_explicit_etf_capital(cls, value: object) -> object:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise ValueError("etf_trading_capital must be a finite positive number")
        return value


class NotificationTestSend(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str | None = None
    recipient_email: str
