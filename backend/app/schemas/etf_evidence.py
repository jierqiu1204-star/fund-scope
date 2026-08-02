from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceAvailability = Literal[
    "available",
    "insufficient_data",
    "unavailable",
    "legacy",
]


class EtfEvidenceMetricOut(BaseModel):
    label: str
    value: float | None = None
    sample_count: int | None = None
    confidence_interval: tuple[float, float] | None = None
    primary: bool


class EtfEvidenceExclusionsOut(BaseModel):
    count: int = 0
    reason_counts: dict[str, int] = Field(default_factory=dict)


class EtfEvidenceSurfaceOut(BaseModel):
    status: EvidenceAvailability
    unavailable_reason: str | None = None
    ranking_source_kind: Literal["production_published", "research_replay"] | None = None
    policy_mode: Literal["production_live", "policy_shadow"] | None = None
    data_cutoff: datetime | None = None
    manifest_hash: str | None = None
    ranking_contract_hash: str | None = None
    run_reference: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, float | int | None] = Field(default_factory=dict)
    exclusions: EtfEvidenceExclusionsOut = Field(
        default_factory=EtfEvidenceExclusionsOut
    )
    primary_metric: EtfEvidenceMetricOut | None = None
    exploratory_metrics: list[dict[str, Any]] = Field(default_factory=list)
    costs: dict[str, float | int | str | None] = Field(default_factory=dict)
    notification_provenance: str | None = None
    execution_provenance: str | None = None
    provider_receipt: dict[str, str] | None = None
    limitations: list[str] = Field(default_factory=list)


class EtfEvidenceSurfacesOut(BaseModel):
    production_ranking: EtfEvidenceSurfaceOut
    research_replay: EtfEvidenceSurfaceOut
    policy_shadow: EtfEvidenceSurfaceOut
    live_notification: EtfEvidenceSurfaceOut
    provider_delivery: EtfEvidenceSurfaceOut
    confirmed_execution: EtfEvidenceSurfaceOut


class EtfEvidenceOverviewOut(BaseModel):
    schema_version: Literal["etf_evidence_overview_v1"]
    generated_at: datetime | None = None
    evidence_status: EvidenceAvailability
    unavailable_reason: str | None = None
    surfaces: EtfEvidenceSurfacesOut
    research_only: Literal[True]
    production_mutation_allowed: Literal[False]
    endpoint_contracts: dict[str, str]


LeaderEvidenceStatus = Literal[
    "insufficient_data",
    "unconfirmed",
    "rejected",
    "eligible_for_v4_proposal",
]


class EtfLeaderObservationHorizonCountsOut(BaseModel):
    horizon_sessions: int = Field(ge=1)
    pending: int = Field(ge=0)
    matured: int = Field(ge=0)
    unavailable: int = Field(ge=0)


class EtfLeaderObservationCountsOut(BaseModel):
    eligible_pit_sessions: int = Field(default=0, ge=0)
    materialized_pit_sessions: int = Field(default=0, ge=0)
    required_pit_sessions: int = Field(default=252, ge=252)
    current_input_asset_count: int = Field(default=0, ge=0)
    current_available_observation_count: int = Field(default=0, ge=0)
    current_qualifying_observation_count: int = Field(default=0, ge=0)
    returned_current_observation_count: int = Field(default=0, ge=0, le=20)
    current_observations_truncated: bool = False
    pending_outcome_count: int = Field(default=0, ge=0)
    matured_outcome_count: int = Field(default=0, ge=0)
    outcomes_by_horizon: list[EtfLeaderObservationHorizonCountsOut] = Field(
        default_factory=list
    )
    independent_primary_date_count: int = Field(default=0, ge=0)
    required_primary_date_count: int = Field(default=40, ge=40)
    completed_walk_forward_fold_count: int = Field(default=0, ge=0)
    required_walk_forward_fold_count: int = Field(default=3, ge=3)


class EtfLeaderCurrentObservationOut(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    candidate_id: str
    asset_code: str
    asset_name: str | None = None
    signal_date: str
    source_cutoff: datetime
    availability: Literal["available", "unavailable"]
    qualifies: bool
    score: float | None = None
    rank: int | None = Field(default=None, ge=1)
    peer_group: str | None = None
    theme: str | None = None
    sector: str | None = None
    matched_gates: list[str] = Field(default_factory=list)
    gate_reasons: list[str] = Field(default_factory=list)
    unavailable_reasons: list[str] = Field(default_factory=list)
    components: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )
    feature_hash: str


class EtfLeaderPendingOutcomeOut(BaseModel):
    candidate_id: str
    asset_code: str
    signal_date: str
    pending_horizons: list[int] = Field(default_factory=list)
    feature_hash: str


class EtfLeaderHistoricalProxyCandidateOut(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    candidate_id: Literal[
        "leader_breakout_proxy_v1",
        "former_leader_repair_proxy_v1",
    ]
    asset_code: str
    name: str | None = None
    score: float = Field(ge=0, le=1)
    baseline_score: float | None = None
    peer_group: str
    components: dict[str, float | int | None] = Field(default_factory=dict)
    feature_hash: str


class EtfLeaderHistoricalProxyOut(BaseModel):
    schema_version: Literal[
        "etf_leader_tactics_historical_proxy_evidence_v1"
    ]
    status: Literal["complete", "unavailable", "incompatible"]
    unavailable_reason: str | None = None
    generated_at: datetime | None = None
    manifest_hash: str | None = None
    contract_hash: str | None = None
    source_ranking_contract_hash: str | None = None
    source_input_snapshot_hash: str | None = None
    ranking_source_kind: Literal["research_replay"]
    evidence_mode: Literal["source_snapshot_historical_proxy"]
    policy_mode: Literal["none"]
    notification_provenance: Literal["none"]
    execution_provenance: Literal["none"]
    signal_date: str | None = None
    signal_run_id: int | None = Field(default=None, ge=1)
    history_sessions: int | None = Field(default=None, ge=1)
    membership_mode: Literal[
        "sealed_source_snapshot_current_vintage_proxy"
    ]
    price_basis: Literal["total_return_adjusted"]
    coverage: dict[str, float | int] = Field(default_factory=dict)
    exclusion_counts: dict[str, int] = Field(default_factory=dict)
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    candidates: list[EtfLeaderHistoricalProxyCandidateOut] = Field(
        default_factory=list,
        max_length=40,
    )
    promotion_gate_credit: dict[str, Literal[0]] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    research_only: Literal[True]
    production_mutation_allowed: Literal[False]


class EtfLeaderTacticsEvidenceOut(BaseModel):
    schema_version: Literal["etf_leader_tactics_evidence_view_v1"]
    experiment_family: Literal["leader_tactics_shadow_v1"]
    status: LeaderEvidenceStatus
    unavailable_reason: str | None = None
    generated_at: datetime | None = None
    data_cutoff: datetime | None = None
    manifest_hash: str | None = None
    factor_manifest_hash: str | None = None
    source_snapshot_hash: str | None = None
    universe_manifest_hash: str | None = None
    input_snapshot_hash: str | None = None
    feature_panel_hashes: list[str] = Field(default_factory=list)
    hypothesis_registry: dict[str, Any]
    candidate_registry: dict[str, Any]
    ranking_source_kind: Literal["research_replay"]
    policy_mode: Literal["none", "policy_shadow"]
    notification_provenance: Literal["none", "simulated"]
    execution_provenance: Literal["none", "simulated_execution"]
    coverage: dict[str, Any] = Field(default_factory=dict)
    exclusion_counts: dict[str, int] = Field(default_factory=dict)
    primary_metrics: list[dict[str, Any]] = Field(default_factory=list)
    exploratory_metrics: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    holdout: dict[str, Any] = Field(default_factory=dict)
    ma5_policy_shadow: list[dict[str, Any]] = Field(default_factory=list)
    candidate_decisions: list[dict[str, Any]] = Field(default_factory=list)
    observation_state: Literal[
        "not_started",
        "partial",
        "observing",
        "blocked",
    ] = "not_started"
    observation_unavailable_reason: str | None = None
    observation_data_cutoff: datetime | None = None
    observation_manifest_hash: str | None = None
    observation_counts: EtfLeaderObservationCountsOut = Field(
        default_factory=EtfLeaderObservationCountsOut
    )
    current_observations: list[EtfLeaderCurrentObservationOut] = Field(
        default_factory=list,
        max_length=20,
    )
    pending_outcomes: list[EtfLeaderPendingOutcomeOut] = Field(
        default_factory=list
    )
    partial_checkpoint: dict[str, Any] = Field(default_factory=dict)
    historical_proxy: EtfLeaderHistoricalProxyOut
    costs: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    research_only: Literal[True]
    production_mutation_allowed: Literal[False]
