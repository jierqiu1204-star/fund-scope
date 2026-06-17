from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    database_url: str = Field(default="sqlite+aiosqlite:///./fundscope.db", alias="DATABASE_URL")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    model_name: str = Field(default="gpt-4o-mini", alias="MODEL_NAME")
    llm_advisor_enabled: bool = Field(default=False, alias="LLM_ADVISOR_ENABLED")
    llm_advisor_max_assets: int = Field(default=20, alias="LLM_ADVISOR_MAX_ASSETS")
    llm_advisor_timeout_seconds: float = Field(default=30.0, alias="LLM_ADVISOR_TIMEOUT_SECONDS")
    llm_advisor_prompt_version: str = Field(
        default="short_research_advisor_v1",
        alias="LLM_ADVISOR_PROMPT_VERSION",
    )
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    auth_jwt_secret: str = Field(default="dev-only-fundscope-secret", alias="AUTH_JWT_SECRET")
    auth_token_expire_days: int = Field(default=30, alias="AUTH_TOKEN_EXPIRE_DAYS")
    auth_bootstrap_admin_email: str = Field(
        default="19535838578@163.com",
        alias="AUTH_BOOTSTRAP_ADMIN_EMAIL",
    )
    auth_bootstrap_admin_display_name: str = Field(default="qje", alias="AUTH_BOOTSTRAP_ADMIN_DISPLAY_NAME")
    auth_bootstrap_admin_password: str = Field(default="", alias="AUTH_BOOTSTRAP_ADMIN_PASSWORD")
    nginx_basic_auth_user: str = Field(default="", alias="NGINX_BASIC_AUTH_USER")
    nginx_basic_auth_pass: str = Field(default="", alias="NGINX_BASIC_AUTH_PASS")
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        alias="CORS_ORIGINS",
    )
    scheduler_timezone: str = "Asia/Shanghai"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            raw_value = value.strip()
            if not raw_value:
                return []
            if raw_value.startswith("["):
                value = json.loads(raw_value)
            else:
                value = raw_value.split(",")
        if isinstance(value, list):
            parsed_values = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError("CORS origins must be strings")
                normalized_item = item.strip()
                if normalized_item:
                    parsed_values.append(normalized_item)
            return parsed_values
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
