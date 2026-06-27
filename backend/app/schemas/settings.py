from __future__ import annotations

from pydantic import BaseModel, Field


class NotificationSettingsRead(BaseModel):
    recipient_email: str
    reminder_day: int
    reference_index_code: str | None
    base_monthly_amount: float
    etf_trading_capital: float
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


class NotificationTestSend(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str | None = None
    recipient_email: str
