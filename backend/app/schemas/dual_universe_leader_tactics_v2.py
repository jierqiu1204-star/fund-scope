from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    "LeaderTacticsV2ContractOut",
    "LeaderTacticsV2SummaryOut",
]
