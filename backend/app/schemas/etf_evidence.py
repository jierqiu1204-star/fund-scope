from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
