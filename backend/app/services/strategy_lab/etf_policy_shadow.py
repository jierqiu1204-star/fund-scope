"""Research-only Top20 action-policy evidence bound to PIT ranking cohorts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.etf_research_evidence import (
    ActionPolicyContractCompatibility,
    NotificationProvenance,
    PolicyMode,
    RankingSourceKind,
    ReplayEvidenceProvenanceContract,
    ReplayExecutionProvenance,
    SignalContractCompatibility,
    stable_contract_hash,
)
from app.services.strategy_lab.etf_action_policy_validation import (
    PRIMARY_ENDPOINT,
    CandidateEndpointResult,
    DevelopmentGateArtifact,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    StageBDateManifest,
    StageBRankingEvent,
)

POLICY_SHADOW_SCHEMA_VERSION = "etf_policy_shadow_evidence_v1"


class PolicyShadowContractError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyDirectionalDiagnostics:
    stop_loss_sample_count: int
    stop_loss_continued_down_count: int
    take_profit_sample_count: int
    take_profit_near_local_high_count: int

    def __post_init__(self) -> None:
        values = (
            self.stop_loss_sample_count,
            self.stop_loss_continued_down_count,
            self.take_profit_sample_count,
            self.take_profit_near_local_high_count,
        )
        if any(value < 0 for value in values):
            raise PolicyShadowContractError(
                "directional diagnostic counts must be non-negative"
            )
        if self.stop_loss_continued_down_count > self.stop_loss_sample_count:
            raise PolicyShadowContractError(
                "continued-down count exceeds stop-loss samples"
            )
        if self.take_profit_near_local_high_count > self.take_profit_sample_count:
            raise PolicyShadowContractError(
                "near-high count exceeds take-profit samples"
            )

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "stop_loss_sample_count": self.stop_loss_sample_count,
            "stop_loss_continued_down_count": (
                self.stop_loss_continued_down_count
            ),
            "stop_loss_continued_down_accuracy": (
                self.stop_loss_continued_down_count
                / self.stop_loss_sample_count
                if self.stop_loss_sample_count
                else None
            ),
            "take_profit_sample_count": self.take_profit_sample_count,
            "take_profit_near_local_high_count": (
                self.take_profit_near_local_high_count
            ),
            "take_profit_near_local_high_accuracy": (
                self.take_profit_near_local_high_count
                / self.take_profit_sample_count
                if self.take_profit_sample_count
                else None
            ),
            "primary": False,
        }


def _validate_complete_top20_cohort(
    *,
    ranking_events: tuple[StageBRankingEvent, ...],
    date_manifests: tuple[StageBDateManifest, ...],
) -> None:
    if not ranking_events or len(ranking_events) != len(date_manifests):
        raise PolicyShadowContractError(
            "policy shadow requires matching Stage B events and manifests"
        )
    manifest_by_date = {item.replay_date: item for item in date_manifests}
    if len(manifest_by_date) != len(date_manifests):
        raise PolicyShadowContractError("duplicate Stage B date manifest")
    for event in ranking_events:
        manifest = manifest_by_date.get(event.replay_date)
        expected_top20 = tuple(
            item.asset_code
            for item in sorted(event.ranked_items, key=lambda item: item.rank)[:20]
        )
        if (
            event.ranking_source_kind != RankingSourceKind.RESEARCH_REPLAY.value
            or manifest is None
            or event.date_manifest_hash != manifest.manifest_hash
            or len(event.top20) != 20
            or event.top20 != expected_top20
            or manifest.expected_universe_count < 20
        ):
            raise PolicyShadowContractError(
                "policy shadow accepts only complete point-in-time Top20 cohorts"
            )


def build_policy_shadow_surface(
    *,
    ranking_events: tuple[StageBRankingEvent, ...],
    date_manifests: tuple[StageBDateManifest, ...],
    endpoint: CandidateEndpointResult,
    development_gate: DevelopmentGateArtifact,
    action_policy_contract_hash: str,
    directional_diagnostics: PolicyDirectionalDiagnostics,
) -> dict[str, Any]:
    """Bind existing action validation evidence to complete replay Top20 inputs."""

    _validate_complete_top20_cohort(
        ranking_events=ranking_events,
        date_manifests=date_manifests,
    )
    if (
        endpoint.top_n != 20
        or endpoint.horizon_trading_days != 10
        or not endpoint.tax_fee_adjusted
        or endpoint.endpoint != "mean_action_cycle_benefit"
        or endpoint.registry_hash != development_gate.registry_hash
        or endpoint.derivation_contract_hash != development_gate.contract_hash
    ):
        raise PolicyShadowContractError(
            "action endpoint does not match the frozen Top20/10-session contract"
        )
    if endpoint.action_cycle_count != development_gate.complete_action_cycle_count:
        raise PolicyShadowContractError(
            "action endpoint and development gate sample counts differ"
        )
    provenance = ReplayEvidenceProvenanceContract(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        signal_contract_compatibility=(
            SignalContractCompatibility.SAME_REPLAY_CONTRACT
        ),
        action_policy_contract_compatibility=(
            ActionPolicyContractCompatibility.SAME_CONTRACT
        ),
        policy_mode=PolicyMode.POLICY_SHADOW,
        notification_provenance=NotificationProvenance.SHADOW_ELIGIBLE,
        execution_provenance=ReplayExecutionProvenance.SIMULATED_EXECUTION,
    ).to_dict()
    event_hashes = tuple(event.event_hash for event in ranking_events)
    manifest_hashes = tuple(item.manifest_hash for item in date_manifests)
    evidence_hash = stable_contract_hash(
        {
            "schema_version": POLICY_SHADOW_SCHEMA_VERSION,
            "event_hashes": event_hashes,
            "date_manifest_hashes": manifest_hashes,
            "endpoint_raw_evidence_hash": endpoint.raw_evidence_hash,
            "development_gate_hash": development_gate.artifact_hash,
            "action_policy_contract_hash": action_policy_contract_hash,
            "directional_diagnostics": directional_diagnostics.as_dict(),
            "provenance": provenance,
        }
    )
    status = (
        "available"
        if development_gate.sample_gate_passed
        else "insufficient_data"
    )
    return {
        "schema_version": POLICY_SHADOW_SCHEMA_VERSION,
        "status": status,
        "unavailable_reason": (
            None if status == "available" else "insufficient_independent_dates"
        ),
        "ranking_source_kind": RankingSourceKind.RESEARCH_REPLAY.value,
        "policy_mode": PolicyMode.POLICY_SHADOW.value,
        "data_cutoff": max(
            item.decision_cutoff for item in date_manifests
        ).isoformat(),
        "manifest_hash": evidence_hash,
        "ranking_contract_hash": ranking_events[-1].score_manifest_hash,
        "run_reference": {
            "replay_run_key": ranking_events[-1].replay_run_key,
            "action_endpoint": PRIMARY_ENDPOINT,
            "action_policy_contract_hash": action_policy_contract_hash,
        },
        "coverage": {
            "complete_top20_dates": len(ranking_events),
            "action_cycle_count": endpoint.action_cycle_count,
            "independent_trading_day_count": (
                endpoint.independent_trading_day_count
            ),
            "coverage_ratio": endpoint.coverage_ratio,
        },
        "exclusion_counts": {
            "incomplete_action_cycle": (
                endpoint.coverage_denominator - endpoint.coverage_numerator
            )
        },
        "primary_metric": {
            "label": PRIMARY_ENDPOINT,
            "value": endpoint.validation_mean_benefit,
            "sample_count": endpoint.action_cycle_count,
            "confidence_interval": list(endpoint.confidence_interval),
            "primary": True,
        },
        "exploratory_metrics": [
            {
                "label": "stop_profit_directional_diagnostics",
                **directional_diagnostics.as_dict(),
            }
        ],
        "costs": {
            "tax_fee_adjusted": True,
            "opportunity_cost": endpoint.opportunity_cost,
        },
        "notification_provenance": provenance["notification_provenance"],
        "execution_provenance": provenance["execution_provenance"],
        "provider_receipt": None,
        "limitations": [
            "simulated policy shadow; no SMTP attempt",
            "directional accuracy is exploratory and cannot replace net benefit",
        ],
        "provenance_contract": provenance,
        "research_only": True,
        "production_mutation_allowed": False,
    }
