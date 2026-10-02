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
    instance_owner_email: str = Field(default="", alias="INSTANCE_OWNER_EMAIL")
    auth_jwt_secret: str = Field(default="", alias="AUTH_JWT_SECRET")
    auth_token_expire_days: int = Field(default=30, alias="AUTH_TOKEN_EXPIRE_DAYS")
    auth_bootstrap_admin_email: str = Field(default="", alias="AUTH_BOOTSTRAP_ADMIN_EMAIL")
    auth_bootstrap_admin_display_name: str = Field(
        default="qje", alias="AUTH_BOOTSTRAP_ADMIN_DISPLAY_NAME"
    )
    auth_bootstrap_admin_password: str = Field(default="", alias="AUTH_BOOTSTRAP_ADMIN_PASSWORD")
    nginx_basic_auth_user: str = Field(default="", alias="NGINX_BASIC_AUTH_USER")
    nginx_basic_auth_pass: str = Field(default="", alias="NGINX_BASIC_AUTH_PASS")
    readiness_expected_database_instance_uuid: str = Field(
        default="",
        alias="READINESS_EXPECTED_DATABASE_INSTANCE_UUID",
    )
    readiness_expected_environment: str = Field(
        default="",
        alias="READINESS_EXPECTED_ENVIRONMENT",
    )
    readiness_deploy_artifact: str = Field(
        default="",
        alias="READINESS_DEPLOY_ARTIFACT",
    )
    readiness_expected_schema_head: str = Field(
        default="",
        alias="READINESS_EXPECTED_SCHEMA_HEAD",
    )
    readiness_attestation_key_id: str = Field(
        default="",
        alias="READINESS_ATTESTATION_KEY_ID",
    )
    readiness_attestation_secret: str = Field(
        default="",
        alias="READINESS_ATTESTATION_SECRET",
    )
    etf_pit_capture_enabled: bool = Field(
        default=False,
        alias="ETF_PIT_CAPTURE_ENABLED",
    )
    etf_pit_code_version: str = Field(
        default="",
        alias="ETF_PIT_CODE_VERSION",
    )
    etf_pit_artifact_dir: str = Field(
        default="./data/etf-pit-artifacts",
        alias="ETF_PIT_ARTIFACT_DIR",
    )
    tracked_position_lifecycle_shadow_enabled: bool = Field(
        default=True,
        alias="TRACKED_POSITION_LIFECYCLE_SHADOW_ENABLED",
    )
    etf_leader_tactics_continuation_enabled: bool = Field(
        default=False,
        alias="ETF_LEADER_TACTICS_CONTINUATION_ENABLED",
    )
    etf_leader_tactics_evidence_api_enabled: bool = Field(
        default=False,
        alias="ETF_LEADER_TACTICS_EVIDENCE_API_ENABLED",
    )
    etf_leader_tactics_code_version: str = Field(
        default="",
        alias="ETF_LEADER_TACTICS_CODE_VERSION",
    )
    etf_leader_tactics_artifact_dir: str = Field(
        default="./data/etf-leader-tactics-artifacts",
        alias="ETF_LEADER_TACTICS_ARTIFACT_DIR",
    )
    etf_leader_tactics_v2_api_enabled: bool = Field(
        default=False,
        alias="ETF_LEADER_TACTICS_V2_API_ENABLED",
    )
    etf_leader_tactics_v2_capture_enabled: bool = Field(
        default=False,
        alias="ETF_LEADER_TACTICS_V2_CAPTURE_ENABLED",
    )
    etf_leader_tactics_v2_theme_graph_enabled: bool = Field(
        default=False,
        alias="ETF_LEADER_TACTICS_V2_THEME_GRAPH_ENABLED",
    )
    etf_leader_tactics_v2_materialize_enabled: bool = Field(
        default=False,
        alias="ETF_LEADER_TACTICS_V2_MATERIALIZE_ENABLED",
    )
    etf_leader_tactics_v2_etf_materialize_enabled: bool = Field(
        default=False,
        alias="ETF_LEADER_TACTICS_V2_ETF_MATERIALIZE_ENABLED",
    )
    etf_leader_tactics_v2_morning_confirmation_enabled: bool = Field(
        default=False,
        alias="ETF_LEADER_TACTICS_V2_MORNING_CONFIRMATION_ENABLED",
    )
    etf_leader_tactics_v2_code_version: str = Field(
        default="dual-universe-leader-tactics-v2-exit-facts-v1",
        alias="ETF_LEADER_TACTICS_V2_CODE_VERSION",
    )
    etf_leader_tactics_v2_artifact_dir: str = Field(
        default="./data/dual-universe-leader-tactics-v2-artifacts",
        alias="ETF_LEADER_TACTICS_V2_ARTIFACT_DIR",
    )
    late_day_turnaround_api_enabled: bool = Field(
        default=False,
        alias="LATE_DAY_TURNAROUND_API_ENABLED",
    )
    late_day_turnaround_etf_materialize_enabled: bool = Field(
        default=False,
        alias="LATE_DAY_TURNAROUND_ETF_MATERIALIZE_ENABLED",
    )
    late_day_turnaround_ashare_capture_enabled: bool = Field(
        default=False,
        alias="LATE_DAY_TURNAROUND_ASHARE_CAPTURE_ENABLED",
    )
    late_day_turnaround_ashare_materialize_enabled: bool = Field(
        default=False,
        alias="LATE_DAY_TURNAROUND_ASHARE_MATERIALIZE_ENABLED",
    )
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
