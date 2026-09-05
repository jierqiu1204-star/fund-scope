"""Read-only ETF evidence overview with provenance-separated surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfFactorExperimentEvidence,
    EtfSignalValidationRun,
    ShortResearchSignalRun,
)
from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.coverage_policy import (
    ETF_COMPLETE_SCORE_COVERAGE,
    ETF_DAILY_DECISION_MIN_COVERAGE,
    persisted_etf_complete_coverage_allowed,
)
from app.services.short_research.daily_reconstructable import (
    PRICE_BASIS,
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_surfaces import DUAL_RANKING_RULE_VERSION
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    MIN_PRIMARY_INDEPENDENT_DATES,
    MIN_PROMOTION_SESSIONS,
    PRIMARY_POLICY_ENDPOINT,
    PRIMARY_RANKING_ENDPOINT,
    EvidenceAvailabilityReason,
)

SCHEMA_VERSION = "etf_evidence_overview_v1"
CURRENT_RESEARCH_EVIDENCE_SCHEMA = "etf_point_in_time_research_evidence_v1"


def _surface(
    *,
    status: str,
    unavailable_reason: str | None,
    ranking_source_kind: str | None = None,
    policy_mode: str | None = None,
    data_cutoff: datetime | str | None = None,
    manifest_hash: str | None = None,
    ranking_contract_hash: str | None = None,
    run_reference: dict[str, Any] | None = None,
    coverage: dict[str, float | int | None] | None = None,
    exclusions: dict[str, int] | None = None,
    primary_metric: dict[str, Any] | None = None,
    exploratory_metrics: list[dict[str, Any]] | None = None,
    costs: dict[str, float | int | str | None] | None = None,
    notification_provenance: str | None = None,
    execution_provenance: str | None = None,
    provider_receipt: dict[str, str] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    reason_counts = exclusions or {}
    return {
        "status": status,
        "unavailable_reason": unavailable_reason,
        "ranking_source_kind": ranking_source_kind,
        "policy_mode": policy_mode,
        "data_cutoff": data_cutoff,
        "manifest_hash": manifest_hash,
        "ranking_contract_hash": ranking_contract_hash,
        "run_reference": run_reference or {},
        "coverage": coverage or {},
        "exclusions": {
            "count": sum(reason_counts.values()),
            "reason_counts": reason_counts,
        },
        "primary_metric": primary_metric,
        "exploratory_metrics": exploratory_metrics or [],
        "costs": costs or {},
        "notification_provenance": notification_provenance,
        "execution_provenance": execution_provenance,
        "provider_receipt": provider_receipt,
        "limitations": limitations or [],
    }


def _production_manifest_hash(run: ShortResearchSignalRun) -> str | None:
    required = (
        run.ranking_contract_hash,
        run.scope_hash,
        run.universe_snapshot_hash,
        run.input_snapshot_hash,
        run.idempotency_key,
    )
    if any(not isinstance(value, str) or not value for value in required):
        return None
    return stable_contract_hash(
        {
            "schema_version": "production_dual_ranking_evidence_v1",
            "signal_run_id": run.id,
            "ranking_contract_hash": run.ranking_contract_hash,
            "scope_hash": run.scope_hash,
            "universe_snapshot_hash": run.universe_snapshot_hash,
            "input_snapshot_hash": run.input_snapshot_hash,
            "score_version": run.score_version,
            "score_field": run.score_field,
            "rule_version": run.rule_version,
            "price_basis": run.price_basis,
            "data_cutoff": run.data_cutoff,
            "idempotency_key": run.idempotency_key,
        }
    )


async def _latest_production_run(
    session: AsyncSession,
) -> ShortResearchSignalRun | None:
    research = daily_reconstructable_manifest()
    return await session.scalar(
        select(ShortResearchSignalRun)
        .where(
            ShortResearchSignalRun.status == "success",
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.scope_kind == "full",
            ShortResearchSignalRun.score_version == research.contract_id,
            ShortResearchSignalRun.score_field == research.score_field,
            ShortResearchSignalRun.rule_version == DUAL_RANKING_RULE_VERSION,
            ShortResearchSignalRun.price_basis == PRICE_BASIS,
        )
        .order_by(
            ShortResearchSignalRun.as_of_trade_date.desc(),
            ShortResearchSignalRun.published_at.desc(),
            ShortResearchSignalRun.id.desc(),
        )
        .limit(1)
    )


async def _latest_factor_evidence(
    session: AsyncSession,
) -> EtfFactorExperimentEvidence | None:
    return await session.scalar(
        select(EtfFactorExperimentEvidence)
        .where(
            or_(
                EtfFactorExperimentEvidence.experiment_family.is_(None),
                EtfFactorExperimentEvidence.experiment_family
                == "ranking_promotion_v1",
            )
        )
        .order_by(
            EtfFactorExperimentEvidence.created_at.desc(),
            EtfFactorExperimentEvidence.id.desc(),
        )
        .limit(1)
    )


async def _latest_replay_run(
    session: AsyncSession,
) -> EtfSignalValidationRun | None:
    return await session.scalar(
        select(EtfSignalValidationRun)
        .where(EtfSignalValidationRun.ranking_source_kind == "research_replay")
        .order_by(
            EtfSignalValidationRun.as_of_date.desc(),
            EtfSignalValidationRun.id.desc(),
        )
        .limit(1)
    )


def _research_surface(
    evidence: EtfFactorExperimentEvidence | None,
    replay: EtfSignalValidationRun | None,
) -> dict[str, Any]:
    if evidence is None:
        if replay is None:
            return _surface(
                status="unavailable",
                unavailable_reason=(
                    EvidenceAvailabilityReason.RESEARCH_REPLAY_NOT_MATERIALIZED.value
                ),
                ranking_source_kind="research_replay",
            )
        observed_dates = int(replay.source_event_count or 0)
        return _surface(
            status="insufficient_data",
            unavailable_reason=(
                EvidenceAvailabilityReason.INSUFFICIENT_INDEPENDENT_DATES.value
            ),
            ranking_source_kind="research_replay",
            policy_mode="policy_shadow",
            data_cutoff=replay.data_cutoff,
            manifest_hash=replay.source_manifest_hash,
            ranking_contract_hash=replay.source_ranking_contract_hash,
            run_reference={
                "validation_run_id": replay.id,
                "replay_run_key": replay.source_replay_run_key,
            },
            coverage={
                "eligible_point_in_time_sessions": observed_dates,
                "required_eligible_point_in_time_sessions": MIN_PROMOTION_SESSIONS,
                "independent_primary_dates": observed_dates,
                "required_independent_primary_dates": MIN_PRIMARY_INDEPENDENT_DATES,
            },
            limitations=["research_replay_has_no_operational_factor_evidence"],
        )

    report = dict(evidence.report_json or {})
    if report.get("schema_version") != CURRENT_RESEARCH_EVIDENCE_SCHEMA:
        return _surface(
            status="legacy",
            unavailable_reason=EvidenceAvailabilityReason.LEGACY_OR_INCOMPATIBLE.value,
            ranking_source_kind="research_replay",
            manifest_hash=evidence.manifest_hash,
            ranking_contract_hash=evidence.ranking_contract_hash,
            run_reference={"factor_evidence_id": evidence.id},
            limitations=["missing_current_research_loop_evidence_schema"],
        )
    promotion_state = str(evidence.promotion_state)
    status = (
        "insufficient_data"
        if promotion_state == "insufficient_data"
        else "available"
    )
    primary = report.get("primary_metric")
    primary_diagnostics = report.get("primary_diagnostics")
    candidate_results = (
        dict(primary_diagnostics.get("candidate_results") or {})
        if isinstance(primary_diagnostics, dict)
        else {}
    )
    candidate_metrics = []
    for candidate_id, candidate_payload in sorted(candidate_results.items()):
        if not isinstance(candidate_payload, dict):
            continue
        endpoint = candidate_payload.get("endpoint")
        if not isinstance(endpoint, dict):
            continue
        candidate_metrics.append(
            {
                "label": str(candidate_id),
                "value": endpoint.get("mean_paired_net_excess"),
                "sample_count": len(endpoint.get("independent_dates") or []),
                "confidence_interval": endpoint.get(
                    "bootstrap_confidence_interval"
                ),
                "coverage_ratio": endpoint.get("coverage_ratio"),
                "average_turnover": endpoint.get("average_turnover"),
                "candidate_maximum_drawdown": endpoint.get(
                    "candidate_maximum_drawdown"
                ),
                "diagnostic": True,
            }
        )
    return _surface(
        status=status,
        unavailable_reason=(
            EvidenceAvailabilityReason.INSUFFICIENT_INDEPENDENT_DATES.value
            if status == "insufficient_data"
            else None
        ),
        ranking_source_kind=str(
            report.get("ranking_source_kind") or "research_replay"
        ),
        policy_mode="policy_shadow",
        data_cutoff=report.get("data_cutoff"),
        manifest_hash=evidence.manifest_hash,
        ranking_contract_hash=evidence.ranking_contract_hash,
        run_reference={
            "factor_evidence_id": evidence.id,
            "replay_run_key": report.get("replay_run_key"),
        },
        coverage=dict(report.get("coverage") or {}),
        exclusions={
            str(key): int(value)
            for key, value in dict(report.get("exclusion_counts") or {}).items()
        },
        primary_metric=dict(primary) if isinstance(primary, dict) else None,
        exploratory_metrics=(
            candidate_metrics + list(report.get("exploratory_metrics") or [])
        ),
        costs=dict(evidence.costs_json or {}),
        limitations=list(evidence.limitations_json or []),
    )


def _report_surface(
    evidence: EtfFactorExperimentEvidence | None,
    *,
    key: str,
    unavailable_reason: EvidenceAvailabilityReason,
    policy_mode: str | None,
    notification_provenance: str | None = None,
    execution_provenance: str | None = None,
) -> dict[str, Any]:
    report = dict(evidence.report_json or {}) if evidence is not None else {}
    value = report.get(key)
    if not isinstance(value, dict):
        return _surface(
            status="unavailable",
            unavailable_reason=unavailable_reason.value,
            ranking_source_kind="research_replay" if policy_mode else None,
            policy_mode=policy_mode,
            notification_provenance=notification_provenance,
            execution_provenance=execution_provenance,
        )
    return _surface(
        status=str(value.get("status") or "available"),
        unavailable_reason=value.get("unavailable_reason"),
        ranking_source_kind=value.get("ranking_source_kind"),
        policy_mode=value.get("policy_mode"),
        data_cutoff=value.get("data_cutoff"),
        manifest_hash=value.get("manifest_hash"),
        ranking_contract_hash=value.get("ranking_contract_hash"),
        run_reference=dict(value.get("run_reference") or {}),
        coverage=dict(value.get("coverage") or {}),
        exclusions={
            str(reason): int(count)
            for reason, count in dict(value.get("exclusion_counts") or {}).items()
        },
        primary_metric=(
            dict(value["primary_metric"])
            if isinstance(value.get("primary_metric"), dict)
            else None
        ),
        exploratory_metrics=list(value.get("exploratory_metrics") or []),
        costs=dict(value.get("costs") or {}),
        notification_provenance=value.get(
            "notification_provenance",
            notification_provenance,
        ),
        execution_provenance=value.get(
            "execution_provenance",
            execution_provenance,
        ),
        provider_receipt=(
            dict(value["provider_receipt"])
            if isinstance(value.get("provider_receipt"), dict)
            else None
        ),
        limitations=list(value.get("limitations") or []),
    )


async def build_etf_evidence_overview(
    session: AsyncSession,
) -> dict[str, Any]:
    production = await _latest_production_run(session)
    factor_evidence = await _latest_factor_evidence(session)
    replay = await _latest_replay_run(session)

    if production is None:
        production_surface = _surface(
            status="unavailable",
            unavailable_reason=(
                EvidenceAvailabilityReason.NO_PRODUCTION_PUBLISHED_SNAPSHOT.value
            ),
            ranking_source_kind="production_published",
            policy_mode="production_live",
        )
    else:
        decision_coverage = production.decision_data_coverage_ratio
        score_coverage = production.coverage_ratio
        summary = production.summary_json or {}
        policy_payload = summary.get("readiness_policy")
        policy_version = (
            policy_payload.get("policy_version")
            if isinstance(policy_payload, dict)
            else summary.get("readiness_policy_version")
        )
        coverage_ready = persisted_etf_complete_coverage_allowed(
            policy_version=policy_version if isinstance(policy_version, str) else None,
            daily_coverage_ratio=decision_coverage,
            warmup_coverage_ratio=score_coverage,
        )
        production_surface = _surface(
            status="available" if coverage_ready else "insufficient_data",
            unavailable_reason=(
                None
                if coverage_ready
                else EvidenceAvailabilityReason.INSUFFICIENT_SCORE_COVERAGE.value
            ),
            ranking_source_kind="production_published",
            policy_mode="production_live",
            data_cutoff=production.data_cutoff,
            manifest_hash=_production_manifest_hash(production),
            ranking_contract_hash=production.ranking_contract_hash,
            run_reference={
                "signal_run_id": production.id,
                "readiness_policy_version": policy_version,
            },
            coverage={
                "expected_asset_count": production.expected_item_count,
                "decision_data_covered_count": production.decision_data_item_count,
                "decision_data_coverage_ratio": decision_coverage,
                "score_eligible_count": production.eligible_item_count,
                "score_coverage_ratio": score_coverage,
                "minimum_required_ratio": ETF_COMPLETE_SCORE_COVERAGE,
                "minimum_decision_data_ratio": ETF_DAILY_DECISION_MIN_COVERAGE,
                "minimum_score_ratio": ETF_COMPLETE_SCORE_COVERAGE,
            },
        )

    research_surface = _research_surface(factor_evidence, replay)
    policy_surface = _report_surface(
        factor_evidence,
        key="policy_shadow",
        unavailable_reason=EvidenceAvailabilityReason.NO_COMPLETE_POLICY_SHADOW,
        policy_mode="policy_shadow",
        notification_provenance="shadow_eligible",
        execution_provenance="simulated_execution",
    )
    live_surface = _report_surface(
        factor_evidence,
        key="live_notification",
        unavailable_reason=EvidenceAvailabilityReason.NO_LIVE_NOTIFICATION_SAMPLE,
        policy_mode="production_live",
    )
    delivery_surface = _report_surface(
        factor_evidence,
        key="provider_delivery",
        unavailable_reason=(
            EvidenceAvailabilityReason.PROVIDER_DELIVERY_UNCONFIRMED
        ),
        policy_mode="production_live",
    )
    confirmed_surface = _report_surface(
        factor_evidence,
        key="confirmed_execution",
        unavailable_reason=(
            EvidenceAvailabilityReason.NO_USER_CONFIRMED_EXECUTION
        ),
        policy_mode="production_live",
        execution_provenance="user_confirmed",
    )
    generated_candidates = [
        value
        for value in (
            production.published_at if production is not None else None,
            production.finished_at if production is not None else None,
            factor_evidence.created_at if factor_evidence is not None else None,
            replay.finished_at if replay is not None else None,
        )
        if value is not None
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": max(generated_candidates) if generated_candidates else None,
        "evidence_status": production_surface["status"],
        "unavailable_reason": production_surface["unavailable_reason"],
        "surfaces": {
            "production_ranking": production_surface,
            "research_replay": research_surface,
            "policy_shadow": policy_surface,
            "live_notification": live_surface,
            "provider_delivery": delivery_surface,
            "confirmed_execution": confirmed_surface,
        },
        "research_only": True,
        "production_mutation_allowed": False,
        "endpoint_contracts": {
            "ranking_primary": PRIMARY_RANKING_ENDPOINT,
            "policy_primary": PRIMARY_POLICY_ENDPOINT,
        },
    }
