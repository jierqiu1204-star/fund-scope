from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfFactorExperimentEvidence
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_validation import (
    PromotionDecision,
    holm_bonferroni,
)
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    FrozenResearchLoopManifest,
    ResearchPromotionDecision,
    ResearchPromotionState,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    RANKING_COST_CONTRACT_HASH,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_validation import RankingEndpointResult

OPERATIONAL_RESEARCH_EVIDENCE_SCHEMA = "etf_point_in_time_research_evidence_v1"


class FactorEvidenceConflictError(ValueError):
    pass


class FactorEvidenceContractError(ValueError):
    pass


@dataclass(frozen=True)
class FactorEvidencePromotion:
    state: str
    passed: bool
    failed_gates: tuple[str, ...]
    endpoint: str
    production_mutation_allowed: bool = False


@dataclass(frozen=True)
class FactorEvidencePayload:
    manifest_hash: str
    ranking_contract_hash: str
    code_version: str
    samples: tuple[dict[str, Any], ...]
    aggregates: dict[str, Any]
    exclusions: tuple[dict[str, Any], ...]
    intervals: dict[str, Any]
    split_reports: dict[str, Any]
    costs: dict[str, Any]
    limitations: tuple[str, ...]
    report: dict[str, Any]
    promotion: PromotionDecision | ResearchPromotionDecision | FactorEvidencePromotion
    experiment_family: str | None = None
    hypothesis_registry_hash: str | None = None

    def validate(self) -> None:
        if (self.experiment_family is None) != (
            self.hypothesis_registry_hash is None
        ):
            raise FactorEvidenceContractError(
                "factor evidence family and hypothesis identity must be paired"
            )
        if self.experiment_family is not None:
            if not self.experiment_family.strip() or not _is_sha256(
                self.hypothesis_registry_hash
            ):
                raise FactorEvidenceContractError(
                    "factor evidence family identity is invalid"
                )
        multiplicity = self.intervals.get("multiplicity")
        if not isinstance(multiplicity, dict):
            raise FactorEvidenceContractError(
                "factor evidence must include multiplicity metadata"
            )
        if multiplicity.get("method") != "holm_bonferroni":
            raise FactorEvidenceContractError(
                "factor evidence multiplicity method must be Holm-Bonferroni"
            )
        raw = multiplicity.get("raw_primary_p_values")
        adjusted = multiplicity.get("adjusted_primary_p_values")
        if not isinstance(raw, list) or not isinstance(adjusted, list):
            raise FactorEvidenceContractError(
                "raw and adjusted primary p-values are required"
            )
        try:
            raw_values = tuple(float(value) for value in raw)
            adjusted_values = tuple(float(value) for value in adjusted)
        except (TypeError, ValueError) as exc:
            raise FactorEvidenceContractError(
                "primary p-values must be numeric"
            ) from exc
        expected = holm_bonferroni(raw_values)
        if len(adjusted_values) != len(expected) or any(
            abs(actual - target) > 1e-12
            for actual, target in zip(adjusted_values, expected, strict=True)
        ):
            raise FactorEvidenceContractError(
                "claimed Holm-Bonferroni values do not match raw primary p-values"
            )

    def canonical_payload(self) -> dict[str, Any]:
        promotion = _promotion_payload(self.promotion)
        payload = {
            "manifest_hash": self.manifest_hash,
            "ranking_contract_hash": self.ranking_contract_hash,
            "code_version": self.code_version,
            "samples": self.samples,
            "aggregates": self.aggregates,
            "exclusions": self.exclusions,
            "intervals": self.intervals,
            "split_reports": self.split_reports,
            "costs": self.costs,
            "limitations": self.limitations,
            "report": self.report,
            "promotion": promotion,
        }
        if self.experiment_family is not None:
            payload["experiment_family"] = self.experiment_family
            payload["hypothesis_registry_hash"] = self.hypothesis_registry_hash
        return payload

    @property
    def evidence_hash(self) -> str:
        return stable_contract_hash(self.canonical_payload())


async def persist_factor_evidence(
    session: AsyncSession,
    payload: FactorEvidencePayload,
) -> EtfFactorExperimentEvidence:
    payload.validate()
    if not payload.manifest_hash or not payload.ranking_contract_hash:
        raise ValueError("manifest and ranking contract hashes are required")
    existing = await session.scalar(
        select(EtfFactorExperimentEvidence).where(
            EtfFactorExperimentEvidence.manifest_hash == payload.manifest_hash
        )
    )
    if existing is not None:
        if existing.evidence_hash != payload.evidence_hash:
            raise FactorEvidenceConflictError(
                "experiment identity already has different immutable evidence"
            )
        return existing
    evidence = EtfFactorExperimentEvidence(
        manifest_hash=payload.manifest_hash,
        ranking_contract_hash=payload.ranking_contract_hash,
        code_version=payload.code_version,
        experiment_family=payload.experiment_family,
        hypothesis_registry_hash=payload.hypothesis_registry_hash,
        evidence_hash=payload.evidence_hash,
        samples_json=list(payload.samples),
        aggregates_json=payload.aggregates,
        exclusions_json=list(payload.exclusions),
        intervals_json=payload.intervals,
        split_reports_json=payload.split_reports,
        costs_json=payload.costs,
        limitations_json=list(payload.limitations),
        promotion_state=str(_promotion_payload(payload.promotion)["state"]),
        report_json=payload.report,
    )
    session.add(evidence)
    await session.commit()
    await session.refresh(evidence)
    return evidence


def _promotion_payload(
    promotion: PromotionDecision | ResearchPromotionDecision | FactorEvidencePromotion,
) -> dict[str, Any]:
    if isinstance(promotion, FactorEvidencePromotion):
        return {
            "state": promotion.state,
            "passed": promotion.passed,
            "failed_gates": promotion.failed_gates,
            "endpoint": promotion.endpoint,
            "production_mutation_allowed": False,
        }
    if isinstance(promotion, ResearchPromotionDecision):
        return {
            "state": promotion.state.value,
            "passed": promotion.state is ResearchPromotionState.PROMOTION_ELIGIBLE,
            "failed_gates": promotion.failed_gates,
            "endpoint": promotion.primary_endpoint,
            "production_mutation_allowed": promotion.production_mutation_allowed,
        }
    return {
        "state": promotion.state,
        "passed": promotion.passed,
        "failed_gates": promotion.failed_gates,
        "endpoint": promotion.endpoint,
        "production_mutation_allowed": False,
    }


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _ranking_metric(
    result: RankingEndpointResult,
    *,
    label: str,
    primary: bool,
) -> dict[str, Any]:
    return {
        "label": label,
        "value": result.mean_paired_net_excess,
        "sample_count": len(result.independent_dates),
        "confidence_interval": list(result.bootstrap_confidence_interval),
        "primary": primary,
        "candidate_id": result.candidate_id,
        "top_n": result.top_n,
        "horizon_sessions": result.horizon_sessions,
        "coverage_ratio": result.coverage_ratio,
        "average_turnover": result.average_turnover,
        "average_rank_churn": result.average_rank_churn,
        "mean_candidate_cost_drag": result.mean_candidate_cost_drag,
        "mean_baseline_cost_drag": result.mean_baseline_cost_drag,
        "candidate_maximum_drawdown": result.candidate_maximum_drawdown,
        "baseline_maximum_drawdown": result.baseline_maximum_drawdown,
        "result_hash": result.result_hash,
    }


def _residual_common_support_summary(
    factor_diagnostics: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Expose incremental-factor fields through the existing evidence payload.

    The evidence model intentionally stores diagnostics in its established JSON
    contract.  This avoids treating residual/common-support research as a new
    production score or API contract.
    """

    fields = (
        "residual_ic",
        "marginal_residual_spearman",
        "common_support_marginal_contribution",
        "common_support_ic_delta",
        "common_support_observation_count",
        "common_support_date_count",
        "common_support_bucket_count",
    )
    output: dict[str, dict[str, Any]] = {}
    for name in sorted(factor_diagnostics):
        value = factor_diagnostics[name]
        if not isinstance(value, Mapping):
            continue
        supported = {
            field: value[field]
            for field in fields
            if field in value
        }
        if supported:
            output[str(name)] = supported
    return output


def _candidate_state(
    promotion: ResearchPromotionDecision,
) -> str:
    if promotion.state is ResearchPromotionState.INSUFFICIENT_DATA:
        return "insufficient_data"
    return "research_only"


def build_operational_factor_evidence(
    *,
    manifest: FrozenResearchLoopManifest,
    data_cutoff: datetime,
    primary_result: RankingEndpointResult,
    exploratory_results: tuple[RankingEndpointResult, ...],
    factor_diagnostics: dict[str, Any],
    coverage: dict[str, float | int | None],
    exclusion_counts: dict[str, int],
    split_reports: dict[str, Any],
    raw_primary_p_values: tuple[float, ...],
    promotion: ResearchPromotionDecision,
    policy_shadow: dict[str, Any] | None = None,
    limitations: tuple[str, ...] = (),
    primary_diagnostics: Mapping[str, Any] | None = None,
) -> FactorEvidencePayload:
    """Build current evidence from existing result objects without rescoring."""

    manifest.validate()
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    frozen_candidate_ids = tuple(item.candidate_id for item in registry.candidates)
    if (
        primary_result.endpoint_role != "primary"
        or primary_result.top_n != 10
        or primary_result.horizon_sessions != 5
        or primary_result.candidate_registry_hash
        != manifest.candidate_registry_hash
        or manifest.candidate_registry_hash != registry.registry_hash
        or primary_result.candidate_id not in frozen_candidate_ids
        or primary_result.cost_contract_hash != RANKING_COST_CONTRACT_HASH
    ):
        raise FactorEvidenceContractError(
            "operational primary result does not match the frozen ranking endpoint"
        )
    if any(
        result.endpoint_role != "exploratory"
        or result.candidate_registry_hash != registry.registry_hash
        or result.candidate_id not in frozen_candidate_ids
        for result in exploratory_results
    ):
        raise FactorEvidenceContractError(
            "auxiliary ranking results must remain exploratory"
        )
    if not 1 <= len(raw_primary_p_values) <= 3:
        raise FactorEvidenceContractError(
            "operational evidence permits one to three frozen comparisons"
        )
    if any(count < 0 for count in exclusion_counts.values()):
        raise FactorEvidenceContractError(
            "operational exclusion counts must be non-negative"
        )
    adjusted_p_values = holm_bonferroni(raw_primary_p_values)
    residual_common_support = _residual_common_support_summary(factor_diagnostics)
    immutable_primary_diagnostics = dict(primary_diagnostics or {})
    primary_metric = _ranking_metric(
        primary_result,
        label=manifest.primary_ranking_endpoint,
        primary=True,
    )
    exploratory_metrics = [
        _ranking_metric(
            result,
            label=result.endpoint_name,
            primary=False,
        )
        for result in exploratory_results
    ]
    promotion_payload = _promotion_payload(promotion)
    report = {
        "schema_version": OPERATIONAL_RESEARCH_EVIDENCE_SCHEMA,
        "replay_run_key": manifest.replay_run_key,
        "data_cutoff": data_cutoff.isoformat(),
        "manifest_hash": manifest.manifest_hash,
        "ranking_source_kind": "research_replay",
        "policy_mode": "policy_shadow",
        "coverage": dict(coverage),
        "exclusion_counts": dict(exclusion_counts),
        "primary_metric": primary_metric,
        "exploratory_metrics": exploratory_metrics,
        "factor_diagnostics": factor_diagnostics,
        "residual_common_support": residual_common_support,
        "primary_diagnostics": immutable_primary_diagnostics,
        "main_candidate_ids": frozen_candidate_ids,
        "candidate_state": _candidate_state(promotion),
        "policy_shadow": policy_shadow,
        "promotion": promotion_payload,
        "holdout": dict(split_reports.get("holdout") or {}),
        "research_only": True,
        "production_mutation_allowed": False,
    }
    return FactorEvidencePayload(
        manifest_hash=manifest.manifest_hash,
        ranking_contract_hash=manifest.research_contract_hash,
        code_version=manifest.code_version,
        samples=tuple(
            {
                "sample_hash": sample_hash,
                "endpoint": manifest.primary_ranking_endpoint,
            }
            for sample_hash in primary_result.accepted_sample_hashes
        ),
        aggregates={
            "primary_net_excess": primary_result.mean_paired_net_excess,
            "candidate_net_return": primary_result.mean_candidate_net_return,
            "baseline_net_return": primary_result.mean_baseline_net_return,
            "average_turnover": primary_result.average_turnover,
            "average_rank_churn": primary_result.average_rank_churn,
            "candidate_cost_drag": primary_result.mean_candidate_cost_drag,
            "baseline_cost_drag": primary_result.mean_baseline_cost_drag,
            "candidate_maximum_drawdown": (
                primary_result.candidate_maximum_drawdown
            ),
            "baseline_maximum_drawdown": primary_result.baseline_maximum_drawdown,
            "coverage_ratio": primary_result.coverage_ratio,
            "residual_common_support": residual_common_support,
            "primary_diagnostics": immutable_primary_diagnostics,
        },
        exclusions=tuple(
            {"reason": reason, "count": count}
            for reason, count in sorted(exclusion_counts.items())
        ),
        intervals={
            "primary": {
                "lower": primary_result.bootstrap_confidence_interval[0],
                "upper": primary_result.bootstrap_confidence_interval[1],
                "method": "moving_block_bootstrap",
                "block_length": primary_result.bootstrap_block_length,
                "resamples": primary_result.bootstrap_resamples,
            },
            "multiplicity": {
                "method": "holm_bonferroni",
                "raw_primary_p_values": list(raw_primary_p_values),
                "adjusted_primary_p_values": list(adjusted_p_values),
                "comparison_count": len(raw_primary_p_values),
            },
        },
        split_reports=split_reports,
        costs={
            "fee_bps_per_side": primary_result.fee_bps_per_side,
            "slippage_bps_per_side": primary_result.slippage_bps_per_side,
            "round_trip_cost_bps": primary_result.round_trip_cost_bps,
            "cost_contract_hash": primary_result.cost_contract_hash,
            "candidate_cost_drag": primary_result.mean_candidate_cost_drag,
            "baseline_cost_drag": primary_result.mean_baseline_cost_drag,
            "provenance": immutable_primary_diagnostics.get(
                "cost_provenance",
                "unavailable",
            ),
        },
        limitations=(
            *limitations,
            *(
                ("insufficient PIT evidence; production weights remain frozen",)
                if promotion.state is ResearchPromotionState.INSUFFICIENT_DATA
                else ()
            ),
            "research evidence only; manual promotion required",
        ),
        report=report,
        promotion=promotion,
    )


async def get_factor_evidence(
    session: AsyncSession,
    manifest_hash: str,
) -> EtfFactorExperimentEvidence | None:
    return await session.scalar(
        select(EtfFactorExperimentEvidence).where(
            EtfFactorExperimentEvidence.manifest_hash == manifest_hash
        )
    )


def factor_evidence_view(evidence: EtfFactorExperimentEvidence) -> dict[str, Any]:
    split_reports = evidence.split_reports_json
    return {
        "manifest_hash": evidence.manifest_hash,
        "ranking_contract_hash": evidence.ranking_contract_hash,
        "code_version": evidence.code_version,
        "experiment_family": evidence.experiment_family,
        "hypothesis_registry_hash": evidence.hypothesis_registry_hash,
        "evidence_hash": evidence.evidence_hash,
        "development": split_reports.get("development", {}),
        "validation": split_reports.get("validation", {}),
        "holdout": split_reports.get("holdout", {}),
        "samples": evidence.samples_json,
        "aggregates": evidence.aggregates_json,
        "exclusions": evidence.exclusions_json,
        "intervals": evidence.intervals_json,
        "costs": evidence.costs_json,
        "limitations": evidence.limitations_json,
        "promotion_state": evidence.promotion_state,
        "report": evidence.report_json,
        "research_only": True,
        "production_mutation_allowed": False,
    }
