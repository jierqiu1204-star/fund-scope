from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("请输入有效邮箱")
    return normalized


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str | None = None
    recipient_email: str
    is_approved: bool
    is_super_admin: bool
    created_at: datetime


class RegisterRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class AuthTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


class UserApprovalUpdate(BaseModel):
    is_approved: bool


class AdminUserOut(UserOut):
    last_login_at: datetime | None = None


class AdminUserSummaryOut(AdminUserOut):
    detail_url: str
    notification_configured: bool = False
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username_masked: str | None = None
    smtp_from: str | None = None
    tracking_summary: dict[str, int] = Field(default_factory=dict)


class AdminUserDetailOut(AdminUserSummaryOut):
    readonly: bool = True
