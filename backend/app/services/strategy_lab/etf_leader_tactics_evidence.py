"""Immutable evidence serialization and latest-family lookup for leader research."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfFactorExperimentEvidence
from app.services.strategy_lab.etf_factor_evidence import (
    FactorEvidencePayload,
    FactorEvidencePromotion,
)
from app.services.strategy_lab.etf_leader_tactics_evaluation import (
    LeaderCandidateDecision,
    LeaderEndpointResult,
    LeaderExperimentRegistration,
    LeaderPrimaryInference,
)
from app.services.strategy_lab.etf_leader_tactics_ma5 import (
    LeaderMa5PolicyResult,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_EXPERIMENT_FAMILY,
    LEADER_HYPOTHESIS_REGISTRY,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    RANKING_COST_CONTRACT_HASH,
    RANKING_FEE_BPS_PER_SIDE,
    RANKING_SLIPPAGE_BPS_PER_SIDE,
    REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
)

LEADER_EVIDENCE_SCHEMA_VERSION = "etf_leader_tactics_evidence_v1"


def _overall_status(decisions: Sequence[LeaderCandidateDecision]) -> str:
    statuses = {item.status for item in decisions}
    if not statuses or statuses == {"insufficient_data"}:
        return "insufficient_data"
    if "eligible_for_v4_proposal" in statuses:
        return "eligible_for_v4_proposal"
    if "unconfirmed" in statuses or "insufficient_data" in statuses:
        return "unconfirmed"
    return "rejected"


def build_leader_factor_evidence(
    *,
    registration: LeaderExperimentRegistration,
    data_cutoff: datetime,
    source_snapshot_hash: str,
    universe_manifest_hash: str,
    input_snapshot_hash: str,
    feature_panel_hashes: Sequence[str],
    coverage: Mapping[str, float | int | None],
    exclusion_counts: Mapping[str, int],
    endpoint_results: Sequence[LeaderEndpointResult],
    inferences: Sequence[LeaderPrimaryInference],
    decisions: Sequence[LeaderCandidateDecision],
    diagnostics: Mapping[str, Any],
    split_reports: Mapping[str, Any],
    ma5_results: Sequence[LeaderMa5PolicyResult] = (),
) -> FactorEvidencePayload:
    if registration.leader_manifest.experiment_family != LEADER_EXPERIMENT_FAMILY:
        raise ValueError("leader evidence registration family is incompatible")
    available_inference = [item for item in inferences if item.available]
    if not available_inference:
        raise ValueError("leader evidence requires at least one completed primary inference")
    if any(count < 0 for count in exclusion_counts.values()):
        raise ValueError("leader evidence exclusions must be non-negative")
    primary = [item for item in endpoint_results if item.endpoint_role == "primary"]
    exploratory = [
        item for item in endpoint_results if item.endpoint_role == "exploratory"
    ]
    inference_by_candidate = {item.candidate_id: item for item in available_inference}
    raw_p_values = [item.raw_p_value for item in available_inference]
    adjusted_p_values = [item.holm_adjusted_p_value for item in available_inference]
    if any(value is None for value in (*raw_p_values, *adjusted_p_values)):
        raise ValueError("leader evidence inference is incomplete")
    status = _overall_status(decisions)
    failed_gates = tuple(
        sorted({gate for item in decisions for gate in item.failed_gates})
    )
    source_registry = {
        "version": LEADER_HYPOTHESIS_REGISTRY.version,
        "interpretation_version": LEADER_HYPOTHESIS_REGISTRY.interpretation_version,
        "registry_hash": LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        "articles": [
            {
                "article_id": item.article_id,
                "title": item.title,
                "source_url": item.source_url,
                "published_at": item.published_at.isoformat(),
                "captured_content_hash": item.captured_content_hash,
            }
            for item in LEADER_HYPOTHESIS_REGISTRY.articles
        ],
        "statements": [
            {
                "statement_id": item.statement_id,
                "disclosure_state": item.disclosure_state,
                "proxy_id": item.proxy_id,
                "interpretation": item.interpretation,
                "limitation": item.limitation,
            }
            for item in LEADER_HYPOTHESIS_REGISTRY.statements
        ],
        "non_equivalence_notice": LEADER_HYPOTHESIS_REGISTRY.non_equivalence_notice,
    }
    candidate_registry = {
        "registry_hash": FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "formula": item.formula,
                "required_history_sessions": item.required_history_sessions,
                "missing_value_rule": item.missing_value_rule,
                "manifest_hash": item.manifest_hash,
            }
            for item in FROZEN_LEADER_CANDIDATE_REGISTRY.candidates
        ],
    }
    primary_metrics = [
        {
            "candidate_id": item.candidate_id,
            "endpoint": item.endpoint_name,
            "top_n": item.top_n,
            "horizon_sessions": item.horizon_sessions,
            "mean_paired_net_excess": item.mean_paired_net_excess,
            "independent_date_count": len(item.independent_dates),
            "coverage_ratio": item.coverage_ratio,
            "turnover": item.average_turnover,
            "rank_churn": item.average_rank_churn,
            "candidate_maximum_drawdown": item.candidate_maximum_drawdown,
            "baseline_maximum_drawdown": item.baseline_maximum_drawdown,
            "result_hash": item.result_hash,
            "inference": (
                {
                    "raw_p_value": inference_by_candidate[item.candidate_id].raw_p_value,
                    "holm_adjusted_p_value": inference_by_candidate[
                        item.candidate_id
                    ].holm_adjusted_p_value,
                    "simultaneous_confidence": inference_by_candidate[
                        item.candidate_id
                    ].simultaneous_confidence,
                    "simultaneous_interval": inference_by_candidate[
                        item.candidate_id
                    ].simultaneous_interval,
                    "method": inference_by_candidate[item.candidate_id].method,
                }
                if item.candidate_id in inference_by_candidate
                else None
            ),
        }
        for item in primary
    ]
    exploratory_metrics = [
        {
            "candidate_id": item.candidate_id,
            "endpoint": item.endpoint_name,
            "top_n": item.top_n,
            "horizon_sessions": item.horizon_sessions,
            "mean_paired_net_excess": item.mean_paired_net_excess,
            "sample_count": len(item.independent_dates),
            "primary": False,
            "result_hash": item.result_hash,
        }
        for item in exploratory
    ]
    ma5_payload = [
        {
            "candidate_id": item.candidate_id,
            "asset_code": item.asset_code,
            "status": item.status,
            "ma5_net_return": item.ma5_net_return,
            "fixed_5_session_net_return": item.fixed_5_session_net_return,
            "fixed_10_session_net_return": item.fixed_10_session_net_return,
            "result_hash": item.result_hash,
        }
        for item in ma5_results
    ]
    report = {
        "schema_version": LEADER_EVIDENCE_SCHEMA_VERSION,
        "experiment_family": LEADER_EXPERIMENT_FAMILY,
        "status": status,
        "unavailable_reason": None,
        "ranking_source_kind": "research_replay",
        "policy_mode": "policy_shadow" if ma5_results else "none",
        "notification_provenance": "none",
        "execution_provenance": (
            "simulated_execution" if ma5_results else "none"
        ),
        "data_cutoff": data_cutoff.isoformat(),
        "manifest_hash": registration.leader_manifest.manifest_hash,
        "factor_manifest_hash": registration.factor_experiment.manifest_hash,
        "source_snapshot_hash": source_snapshot_hash,
        "universe_manifest_hash": universe_manifest_hash,
        "input_snapshot_hash": input_snapshot_hash,
        "feature_panel_hashes": list(feature_panel_hashes),
        "hypothesis_registry": source_registry,
        "candidate_registry": candidate_registry,
        "regime_contract_hash": REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
        "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        "coverage": dict(coverage),
        "exclusion_counts": dict(exclusion_counts),
        "primary_metrics": primary_metrics,
        "exploratory_metrics": exploratory_metrics,
        "diagnostics": dict(diagnostics),
        "ma5_policy_shadow": ma5_payload,
        "holdout": dict(split_reports.get("holdout") or {}),
        "candidate_decisions": [
            {
                "candidate_id": item.candidate_id,
                "status": item.status,
                "failed_gates": list(item.failed_gates),
                "production_mutation_allowed": False,
            }
            for item in decisions
        ],
        "research_only": True,
        "production_mutation_allowed": False,
    }
    return FactorEvidencePayload(
        manifest_hash=registration.leader_manifest.manifest_hash,
        ranking_contract_hash=registration.leader_manifest.baseline_contract_hash,
        code_version=registration.leader_manifest.code_version,
        samples=tuple(
            {
                "candidate_id": item.candidate_id,
                "result_hash": item.result_hash,
                "independent_date_count": len(item.independent_dates),
            }
            for item in primary
        ),
        aggregates={
            "status": status,
            "primary_metrics": primary_metrics,
            "diagnostics": dict(diagnostics),
        },
        exclusions=tuple(
            {"reason": reason, "count": count}
            for reason, count in sorted(exclusion_counts.items())
        ),
        intervals={
            "primary_by_candidate": {
                item.candidate_id: {
                    "simultaneous_interval": item.simultaneous_interval,
                    "confidence": item.simultaneous_confidence,
                    "method": item.method,
                }
                for item in available_inference
            },
            "multiplicity": {
                "method": "holm_bonferroni",
                "candidate_ids": [item.candidate_id for item in available_inference],
                "raw_primary_p_values": [float(value) for value in raw_p_values],
                "adjusted_primary_p_values": [
                    float(value) for value in adjusted_p_values
                ],
            },
        },
        split_reports=dict(split_reports),
        costs={
            "fee_bps_per_side": RANKING_FEE_BPS_PER_SIDE,
            "slippage_bps_per_side": RANKING_SLIPPAGE_BPS_PER_SIDE,
            "round_trip_cost_bps": 2
            * (RANKING_FEE_BPS_PER_SIDE + RANKING_SLIPPAGE_BPS_PER_SIDE),
            "cost_contract_hash": RANKING_COST_CONTRACT_HASH,
        },
        limitations=(
            LEADER_HYPOTHESIS_REGISTRY.non_equivalence_notice,
            "research proxy only; no production ranking, alert, or email mutation",
            "intraday T-trading remains unavailable without an executable PIT model",
        ),
        report=report,
        promotion=FactorEvidencePromotion(
            state=status,
            passed=status == "eligible_for_v4_proposal",
            failed_gates=failed_gates,
            endpoint="paired_top10_5_session_net_excess_common_support",
        ),
        experiment_family=LEADER_EXPERIMENT_FAMILY,
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
    )


async def latest_leader_factor_evidence(
    session: AsyncSession,
) -> EtfFactorExperimentEvidence | None:
    return await session.scalar(
        select(EtfFactorExperimentEvidence)
        .where(
            EtfFactorExperimentEvidence.experiment_family
            == LEADER_EXPERIMENT_FAMILY
        )
        .order_by(
            EtfFactorExperimentEvidence.created_at.desc(),
            EtfFactorExperimentEvidence.id.desc(),
        )
        .limit(1)
    )
