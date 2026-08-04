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
    state: Literal["preparing", "confirmed", "invalidated"]
    availability: Literal["available", "unavailable"]
    qualifies: bool
    score: float | None = None
    signal_date: date
    transition_date: date | None = None
    source_cutoff: datetime
    gate_facts: dict[str, Any] = Field(default_factory=dict)
    exclusion_reasons: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    feature_hash: str


class LeaderTacticsV2SummaryOut(BaseModel):
    observation_count: int
    qualifying_count: int
    coverage: str
    available_count: int = 0
    returned_count: int = 0
    exclusion_counts: dict[str, int] = Field(default_factory=dict)
    manifest_hash: str | None = None
    unavailable_reason: str | None = None


class LeaderTacticsV2CandidatesOut(BaseModel):
    schema_version: str
    experiment_family: str
    universe: Literal["etf", "ashare"]
    formula: str
    state: str
    as_of: str | None = None
    manifest_hash: str | None = None
    manifest_decision_cutoff: str | None = None
    candidates: list[LeaderTacticsV2CandidateOut]
    next_cursor: str | None = None
    has_more: bool
    summary: LeaderTacticsV2SummaryOut
    ranking_source_kind: Literal["research_replay"]
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
