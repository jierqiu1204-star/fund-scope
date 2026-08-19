from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ClassificationStatus = Literal["available", "unavailable", "not_applicable"]


class LeaderTacticsV2IndustryPathOut(BaseModel):
    """Persisted primary-industry evidence; missing levels remain explicit."""

    model_config = ConfigDict(extra="allow")

    taxonomy: str | None = None
    taxonomy_version: str | None = None
    mapping_kind: str | None = None
    source: str | None = None
    confidence: float | None = None
    level_1: dict[str, Any] | None = None
    level_2: dict[str, Any] | None = None
    level_3: dict[str, Any] | None = None
    missing_levels: list[str] = Field(default_factory=list)
    effective_from: date | None = None
    effective_to: date | None = None
    received_at: datetime | None = None
    fact_hash: str | None = None
    snapshot_hash: str | None = None


class LeaderTacticsV2PeerContextOut(BaseModel):
    """One persisted theme or industry peer context."""

    model_config = ConfigDict(extra="allow")

    context_key: str | None = None
    display_label: str | None = None
    relation_kind: str | None = None
    hierarchy_level: str | None = None
    taxonomy: str | None = None
    source: str | None = None
    snapshot_date: date | None = None
    snapshot_hash: str | None = None
    fact_hash: str | None = None
    state_hash: str | None = None
    peer_count: int | None = None
    confidence: float | None = None
    freshness_days: int | None = None


class LeaderTacticsV2ThemeStateOut(BaseModel):
    """Independent persisted daily state for the selected peer context."""

    model_config = ConfigDict(extra="allow")

    status: Literal["available", "unavailable"] = "unavailable"
    state_hash: str | None = None
    session_date: date | None = None
    eligible_member_count: int | None = None
    up_breadth: float | None = None
    median_return_1d: float | None = None
    median_return_5d: float | None = None
    relative_market_return: float | None = None
    amount_participation: float | None = None
    leader_count: int | None = None
    limit_board_metrics: dict[str, Any] | None = None
    source_cutoff: datetime | None = None
    unavailable_reasons: list[str] = Field(default_factory=list)


class LeaderTacticsV2ClassificationReadinessOut(BaseModel):
    """Persisted-only coverage and source-health projection."""

    model_config = ConfigDict(extra="allow")

    status: ClassificationStatus = "unavailable"
    authoritative_universe_count: int | None = None
    industry_path_count: int | None = None
    industry_level_1_count: int | None = None
    industry_level_2_count: int | None = None
    industry_level_3_count: int | None = None
    theme_member_count: int | None = None
    theme_relation_count: int | None = None
    theme_state_count: int | None = None
    fallback_count: int | None = None
    coverage: dict[str, Any] = Field(default_factory=dict)
    latest_snapshots: dict[str, Any] = Field(default_factory=dict)
    provider_health: dict[str, Any] = Field(default_factory=dict)
    stale_sources: list[str] = Field(default_factory=list)
    unavailable_reasons: list[str] = Field(default_factory=list)


class LeaderTacticsV2CandidateOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    manifest_hash: str
    universe: Literal["etf", "ashare"]
    asset_code: str
    asset_name: str
    theme: str | None = None
    sector: str | None = None
    tracked_index: str | None = None
    formula_id: str
    state: Literal["preparing", "turning_watch", "confirmed", "invalidated"]
    availability: Literal["available", "unavailable"]
    qualifies: bool
    entry_status: Literal["watch", "actionable", "overextended", "invalidated"]
    score: float | None = None
    signal_date: date
    transition_date: date | None = None
    source_cutoff: datetime
    gate_facts: dict[str, Any] = Field(default_factory=dict)
    exclusion_reasons: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    feature_hash: str
    sentiment_risk_state: Literal[
        "healthy", "warning", "risk_off", "unavailable", "not_applicable"
    ] = "not_applicable"
    sentiment_risk_action_mode: Literal[
        "shadow_entry_allowed", "observe_only", "not_applicable"
    ] = "not_applicable"
    sentiment_risk_new_entry_allowed: bool | None = None
    sentiment_risk_provenance: dict[str, Any] = Field(default_factory=dict)
    classification_status: ClassificationStatus = "not_applicable"
    industry_path: LeaderTacticsV2IndustryPathOut | None = None
    selected_context: LeaderTacticsV2PeerContextOut | None = None
    alternative_contexts: list[LeaderTacticsV2PeerContextOut] = Field(default_factory=list)
    rejected_contexts: list[dict[str, Any]] = Field(default_factory=list)
    theme_state: LeaderTacticsV2ThemeStateOut | None = None
    classification_provider_health: dict[str, Any] = Field(default_factory=dict)
    classification_unavailable_reasons: list[str] = Field(default_factory=list)


class LeaderTacticsV2SummaryOut(BaseModel):
    observation_count: int
    qualifying_count: int
    coverage: str
    available_count: int = 0
    returned_count: int = 0
    exclusion_counts: dict[str, int] = Field(default_factory=dict)
    manifest_hash: str | None = None
    unavailable_reason: str | None = None
    sentiment_risk: dict[str, Any] = Field(default_factory=dict)
    classification_readiness: LeaderTacticsV2ClassificationReadinessOut = Field(
        default_factory=LeaderTacticsV2ClassificationReadinessOut
    )


class LeaderTacticsV2CandidatesOut(BaseModel):
    schema_version: str
    experiment_family: str
    universe: Literal["etf", "ashare"]
    formula: str
    state: str
    as_of: str | None = None
    manifest_hash: str | None = None
    manifest_decision_cutoff: str | None = None
    decision_mode: Literal["session_pit", "post_close_watchlist"] | None = None
    feature_trade_date: date | None = None
    membership_evaluation_date: date | None = None
    next_eligible_date: date | None = None
    historical_validation_eligible: bool | None = None
    candidates: list[LeaderTacticsV2CandidateOut]
    next_cursor: str | None = None
    has_more: bool
    summary: LeaderTacticsV2SummaryOut
    ranking_source_kind: Literal["research_replay", "post_close_watchlist"]
    notification_provenance: Literal["none"]
    execution_provenance: Literal["none"]
    research_only: Literal[True]
    production_mutation_allowed: Literal[False]


class LeaderTacticsV2ContractOut(BaseModel):
    schema_version: str
    experiment_family: str
    source_registry: dict[str, Any]
    formulas: list[dict[str, Any]]
    candidate_ids: list[str]
    lifecycle_states: list[str]
    research_only: Literal[True]
    production_mutation_allowed: Literal[False]
    unavailable_reason: str | None = None


__all__ = [
    "LeaderTacticsV2CandidateOut",
    "LeaderTacticsV2CandidatesOut",
    "LeaderTacticsV2ClassificationReadinessOut",
    "LeaderTacticsV2ContractOut",
    "LeaderTacticsV2IndustryPathOut",
    "LeaderTacticsV2PeerContextOut",
    "LeaderTacticsV2SummaryOut",
    "LeaderTacticsV2ThemeStateOut",
]
