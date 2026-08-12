from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.entities import (
    EtfCanonicalPublicationRegistry,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
)
from app.services.intraday_etf.exchange_calendar import (
    AFTERNOON_CLOSE,
    is_trading_day,
    localize_exchange_time,
)
from app.services.short_research.coverage_policy import (
    ETF_COMPLETE_SCORE_COVERAGE,
    ETF_DAILY_DECISION_MIN_COVERAGE,
    EtfCoveragePolicyMode,
    EtfReadinessPolicyResult,
    evaluate_persisted_etf_readiness,
)
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_contract import final_score_v3_contract
from app.services.short_research.ranking_surfaces import (
    DUAL_RANKING_RULE_VERSION,
)


@dataclass(frozen=True)
class CanonicalSnapshotSelection:
    state: Literal[
        "ready",
        "provisional",
        "waiting",
        "stale",
        "legacy",
        "version_mismatch",
    ]
    run: ShortResearchSignalRun | None


class SnapshotMetadata(TypedDict):
    snapshot_id: int | None
    score_version: str | None
    ranking_contract_hash: str | None
    scope_kind: str | None
    as_of_trade_date: date | None
    generated_at: datetime | None
    expected_item_count: int | None
    decision_data_item_count: int | None
    decision_data_coverage_ratio: float | None
    score_eligible_item_count: int | None
    score_coverage_ratio: float | None
    research_ranked_item_count: int | None
    research_coverage_ratio: float | None
    research_quality_eligible_item_count: int | None
    research_quality_coverage_ratio: float | None
    observation_only_item_count: int | None
    actionable_eligible_item_count: int | None
    actionable_coverage_ratio: float | None
    coverage_ratio: float | None
    coverage_policy_mode: EtfCoveragePolicyMode | None
    readiness_state: EtfCoveragePolicyMode | None
    policy_version: str | None
    snapshot_state: Literal["unavailable", "provisional", "complete"]
    surface_availability_state: str | None
    unavailable_reason: str | None
    market_decision_cutoff: datetime | str | None
    data_receipt_cutoff: datetime | str | None
    replay_visibility_cutoff: datetime | str | None
    resource_profile: dict[str, Any]
    provider_health_identity: dict[str, Any]
    quality_evidence: dict[str, Any]
    publication_evidence: dict[str, Any]
    pit_evidence: dict[str, Any]
    cost_evidence: dict[str, Any]
    concentration_evidence: dict[str, Any]
    freshness_status: str
    limitations: list[str]


def _persisted_readiness_for_run(
    run: ShortResearchSignalRun,
) -> EtfReadinessPolicyResult:
    summary = run.summary_json or {}
    config = run.config_json or {}
    policy = summary.get("readiness_policy")
    policy_version = policy.get("policy_version") if isinstance(policy, dict) else None
    if not isinstance(policy_version, str):
        policy_version = summary.get("readiness_policy_version")
    if not isinstance(policy_version, str):
        policy_version = config.get("readiness_policy_version")
    return evaluate_persisted_etf_readiness(
        policy_version=policy_version if isinstance(policy_version, str) else None,
        daily_coverage_ratio=run.decision_data_coverage_ratio,
        warmup_coverage_ratio=run.coverage_ratio,
    )


def _summary_provider_health_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidate_records = value.get("candidate_records")
    summary = {key: item for key, item in value.items() if key != "candidate_records"}
    if isinstance(candidate_records, list):
        summary["candidate_record_count"] = len(candidate_records)
    summary["detail_level"] = "summary"
    return summary


def _summary_observation_only(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    items = value.get("items")
    summary = {key: item for key, item in value.items() if key != "items"}
    if isinstance(items, list):
        summary.setdefault("count", len(items))
    summary["detail_level"] = "summary"
    return summary


def _summary_ranking_surfaces(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    summary: dict[str, Any] = {}
    for surface_name, surface_value in value.items():
        if not isinstance(surface_value, dict):
            continue
        excluded = surface_value.get("excluded")
        surface_summary = {key: item for key, item in surface_value.items() if key != "excluded"}
        if isinstance(excluded, list):
            surface_summary["excluded_count"] = len(excluded)
        surface_summary["detail_level"] = "summary"
        summary[str(surface_name)] = surface_summary
    return summary


def snapshot_metadata(
    run: ShortResearchSignalRun | None,
    *,
    selection_state: str | None = None,
    evidence_detail: Literal["full", "summary"] = "full",
) -> SnapshotMetadata:
    if run is None:
        freshness_status = selection_state or "waiting"
        return {
            "snapshot_id": None,
            "score_version": None,
            "ranking_contract_hash": None,
            "scope_kind": None,
            "as_of_trade_date": None,
            "generated_at": None,
            "expected_item_count": None,
            "decision_data_item_count": None,
            "decision_data_coverage_ratio": None,
            "score_eligible_item_count": None,
            "score_coverage_ratio": None,
            "research_ranked_item_count": None,
            "research_coverage_ratio": None,
            "research_quality_eligible_item_count": None,
            "research_quality_coverage_ratio": None,
            "observation_only_item_count": None,
            "actionable_eligible_item_count": None,
            "actionable_coverage_ratio": None,
            "coverage_ratio": None,
            "coverage_policy_mode": None,
            "readiness_state": None,
            "policy_version": None,
            "snapshot_state": "unavailable",
            "surface_availability_state": None,
            "unavailable_reason": f"canonical_snapshot_{freshness_status}",
            "market_decision_cutoff": None,
            "data_receipt_cutoff": None,
            "replay_visibility_cutoff": None,
            "resource_profile": {},
            "provider_health_identity": {},
            "quality_evidence": {},
            "publication_evidence": {},
            "pit_evidence": {},
            "cost_evidence": {},
            "concentration_evidence": {},
            "freshness_status": freshness_status,
            "limitations": ["no_snapshot", f"canonical_snapshot_{freshness_status}"],
        }
    limitations: list[str] = []
    for field in (
        "score_version",
        "rule_version",
        "ranking_contract_hash",
        "score_field",
        "scope_kind",
        "as_of_trade_date",
        "price_basis",
        "expected_item_count",
        "decision_data_item_count",
        "decision_data_coverage_ratio",
        "eligible_item_count",
        "coverage_ratio",
    ):
        if getattr(run, field) is None:
            limitations.append(f"missing_{field}")
    if run.scope_kind not in {None, "full"}:
        limitations.append("partial_scope")
    summary = run.summary_json or {}
    config = run.config_json or {}
    readiness = _persisted_readiness_for_run(run)
    policy_version = readiness.policy_version
    if readiness.state == "degraded":
        snapshot_state: Literal["unavailable", "provisional", "complete"] = "provisional"
        limitations.extend(["provisional_research_only", "actionable_surface_unavailable"])
        freshness_status = "provisional"
        unavailable_reason = str(
            summary.get("unavailable_reason") or "history_depth_61_coverage_below_90pct"
        )
    elif (
        readiness.complete_publication_allowed
        and run.publication_state == "published"
        and not limitations
    ):
        snapshot_state = "complete"
        unavailable_reason = None
        freshness_status = selection_state or "ready"
    elif run.publication_state != "published":
        snapshot_state = "unavailable"
        limitations.append("not_published")
        freshness_status = "unpublished"
        unavailable_reason = "complete_snapshot_not_published"
    elif limitations:
        snapshot_state = "unavailable"
        freshness_status = "legacy"
        unavailable_reason = "legacy_snapshot_missing_readiness_evidence"
    else:
        snapshot_state = "unavailable"
        freshness_status = selection_state or "ready"
        unavailable_reason = "snapshot_readiness_incompatible"
    cutoff_provenance = summary.get("cutoff_provenance")
    cutoff_payload = cutoff_provenance if isinstance(cutoff_provenance, dict) else {}
    resource_profile = summary.get("resource_profile")
    provider_health_identity = summary.get("provider_health_identity")
    observation_only = summary.get("observation_only", {})
    ranking_surfaces = summary.get("ranking_surfaces", {})
    research_surface = (
        ranking_surfaces.get("research") if isinstance(ranking_surfaces, dict) else None
    )
    actionable_surface = (
        ranking_surfaces.get("actionable") if isinstance(ranking_surfaces, dict) else None
    )
    if evidence_detail == "summary":
        provider_health_payload = _summary_provider_health_identity(provider_health_identity)
        observation_only_payload = _summary_observation_only(observation_only)
        ranking_surfaces_payload = _summary_ranking_surfaces(ranking_surfaces)
    else:
        provider_health_payload = (
            dict(provider_health_identity) if isinstance(provider_health_identity, dict) else {}
        )
        observation_only_payload = observation_only
        ranking_surfaces_payload = ranking_surfaces
    quality_evidence = {
        "canonical_research_eligibility": summary.get(
            "canonical_research_eligibility",
            {},
        ),
        "observation_only": observation_only_payload,
        "ranking_surfaces": ranking_surfaces_payload,
        "non_finite_reject_count": summary.get("non_finite_reject_count", 0),
        "cap_violation_count": summary.get("cap_violation_count", 0),
    }
    if evidence_detail == "summary":
        quality_evidence["detail_level"] = "summary"
    return {
        "snapshot_id": run.id,
        "score_version": run.score_version,
        "ranking_contract_hash": run.ranking_contract_hash,
        "scope_kind": run.scope_kind,
        "as_of_trade_date": run.as_of_trade_date,
        "generated_at": run.published_at or run.finished_at or run.started_at,
        "expected_item_count": run.expected_item_count,
        "decision_data_item_count": run.decision_data_item_count,
        "decision_data_coverage_ratio": run.decision_data_coverage_ratio,
        "score_eligible_item_count": run.eligible_item_count,
        "score_coverage_ratio": run.coverage_ratio,
        "research_ranked_item_count": (
            research_surface.get("eligible_count") if isinstance(research_surface, dict) else None
        ),
        "research_coverage_ratio": (
            research_surface.get("coverage_ratio") if isinstance(research_surface, dict) else None
        ),
        "research_quality_eligible_item_count": (
            research_surface.get("quality_eligible_count")
            if isinstance(research_surface, dict)
            else None
        ),
        "research_quality_coverage_ratio": (
            research_surface.get("quality_coverage_ratio")
            if isinstance(research_surface, dict)
            else None
        ),
        "observation_only_item_count": (
            observation_only.get("count") if isinstance(observation_only, dict) else None
        ),
        "actionable_eligible_item_count": (
            actionable_surface.get("eligible_count")
            if isinstance(actionable_surface, dict)
            else None
        ),
        "actionable_coverage_ratio": (
            actionable_surface.get("coverage_ratio")
            if isinstance(actionable_surface, dict)
            else None
        ),
        "coverage_ratio": run.coverage_ratio,
        "coverage_policy_mode": readiness.state,
        "readiness_state": readiness.state,
        "policy_version": policy_version,
        "snapshot_state": snapshot_state,
        "surface_availability_state": (
            str(summary["surface_availability_state"])
            if summary.get("surface_availability_state")
            else None
        ),
        "unavailable_reason": unavailable_reason,
        "market_decision_cutoff": cutoff_payload.get(
            "market_decision_cutoff",
            config.get("market_decision_cutoff"),
        ),
        "data_receipt_cutoff": cutoff_payload.get(
            "data_receipt_cutoff",
            config.get("source_availability_cutoff") or run.data_cutoff,
        ),
        "replay_visibility_cutoff": cutoff_payload.get(
            "replay_visibility_cutoff",
            config.get("replay_visibility_cutoff"),
        ),
        "resource_profile": (dict(resource_profile) if isinstance(resource_profile, dict) else {}),
        "provider_health_identity": provider_health_payload,
        "quality_evidence": quality_evidence,
        "publication_evidence": dict(summary.get("publication_evidence") or {}),
        "pit_evidence": dict(summary.get("pit_evidence") or {}),
        "cost_evidence": dict(summary.get("cost_evidence") or {}),
        "concentration_evidence": dict(summary.get("concentration_evidence") or {}),
        "freshness_status": freshness_status,
        "limitations": limitations,
    }


def etf_ranking_surface_selection_from_run(
    run: ShortResearchSignalRun,
    *,
    ranking_surface: Literal["research", "actionable"],
) -> CanonicalSnapshotSelection:
    readiness = _persisted_readiness_for_run(run)
    if readiness.complete_publication_allowed and run.publication_state == "published":
        if ranking_surface == "research":
            return CanonicalSnapshotSelection("ready", run)
        surfaces = (run.summary_json or {}).get("ranking_surfaces")
        actionable = surfaces.get("actionable") if isinstance(surfaces, dict) else None
        if isinstance(actionable, dict) and int(actionable.get("eligible_count") or 0) > 0:
            return CanonicalSnapshotSelection("ready", run)
    if ranking_surface == "research" and readiness.state == "degraded":
        return CanonicalSnapshotSelection("provisional", run)
    return CanonicalSnapshotSelection("waiting", None)


def _etf_item_clauses() -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    has_etf_item = (
        select(ShortResearchSignalItem.id)
        .where(
            ShortResearchSignalItem.run_id == ShortResearchSignalRun.id,
            ShortResearchSignalItem.asset_type == "etf",
        )
        .exists()
    )
    has_non_etf_item = (
        select(ShortResearchSignalItem.id)
        .where(
            ShortResearchSignalItem.run_id == ShortResearchSignalRun.id,
            ShortResearchSignalItem.asset_type != "etf",
        )
        .exists()
    )
    return has_etf_item, has_non_etf_item


def required_etf_snapshot_trade_date(now: datetime | None = None) -> date:
    local_now = localize_exchange_time(now)
    candidate = local_now.date()
    if is_trading_day(candidate) and local_now.time() >= AFTERNOON_CLOSE:
        return candidate
    candidate -= timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _current_contract_fields() -> dict[str, Any]:
    contract = final_score_v3_contract()
    selector = contract.get("selector")
    calculation = contract.get("calculation")
    if not isinstance(selector, dict) or not isinstance(calculation, dict):
        raise ValueError("final_score_v3 selector contract is invalid")
    return {
        "score_version": str(
            selector.get("target_score_version") or contract.get("contract_id") or ""
        ),
        "rule_version": str(contract.get("rule_version") or ""),
        "score_field": str(selector.get("score_field") or ""),
        "scope_kind": str(selector.get("required_scope") or ""),
        "price_basis": str(calculation.get("price_basis") or ""),
    }


def _current_contract_clauses() -> tuple[ColumnElement[bool], ...]:
    fields = _current_contract_fields()
    return (
        ShortResearchSignalRun.status == "success",
        ShortResearchSignalRun.publication_state == "published",
        ShortResearchSignalRun.scope_kind == fields["scope_kind"],
        ShortResearchSignalRun.score_version == fields["score_version"],
        ShortResearchSignalRun.rule_version == fields["rule_version"],
        ShortResearchSignalRun.score_field == fields["score_field"],
        ShortResearchSignalRun.price_basis == fields["price_basis"],
        ShortResearchSignalRun.scope_hash.is_not(None),
        ShortResearchSignalRun.universe_snapshot_hash.is_not(None),
        ShortResearchSignalRun.input_snapshot_hash.is_not(None),
        ShortResearchSignalRun.ranking_contract_hash.is_not(None),
        ShortResearchSignalRun.data_cutoff.is_not(None),
        ShortResearchSignalRun.expected_item_count.is_not(None),
        ShortResearchSignalRun.decision_data_item_count.is_not(None),
        ShortResearchSignalRun.decision_data_coverage_ratio >= ETF_DAILY_DECISION_MIN_COVERAGE,
        ShortResearchSignalRun.eligible_item_count.is_not(None),
        ShortResearchSignalRun.coverage_ratio >= ETF_COMPLETE_SCORE_COVERAGE,
        ShortResearchSignalRun.idempotency_key.is_not(None),
    )


async def select_current_canonical_etf_snapshot(
    session: AsyncSession,
    *,
    required_trade_date: date,
) -> ShortResearchSignalRun | None:
    has_etf_item, has_non_etf_item = _etf_item_clauses()
    registered = (
        await session.scalars(
            select(ShortResearchSignalRun)
            .join(
                EtfCanonicalPublicationRegistry,
                EtfCanonicalPublicationRegistry.source_signal_run_id == ShortResearchSignalRun.id,
            )
            .where(
                *_current_contract_clauses(),
                ShortResearchSignalRun.as_of_trade_date == required_trade_date,
                EtfCanonicalPublicationRegistry.is_current.is_(True),
                has_etf_item,
                ~has_non_etf_item,
            )
            .order_by(EtfCanonicalPublicationRegistry.id.desc())
            .limit(2)
        )
    ).all()
    if len(registered) > 1:
        return None
    if registered:
        run = registered[0]
        return run if snapshot_metadata(run)["snapshot_state"] == "complete" else None
    rows = await session.scalars(
        select(ShortResearchSignalRun)
        .where(
            *_current_contract_clauses(),
            ShortResearchSignalRun.as_of_trade_date == required_trade_date,
            has_etf_item,
            ~has_non_etf_item,
        )
        .order_by(ShortResearchSignalRun.published_at.desc(), ShortResearchSignalRun.id.desc())
    )
    return next(
        (run for run in rows if snapshot_metadata(run)["snapshot_state"] == "complete"),
        None,
    )


async def resolve_current_canonical_etf_snapshot(
    session: AsyncSession,
    *,
    required_trade_date: date,
) -> CanonicalSnapshotSelection:
    run = await select_current_canonical_etf_snapshot(
        session,
        required_trade_date=required_trade_date,
    )
    if run is not None:
        return CanonicalSnapshotSelection("ready", run)

    has_etf_item, has_non_etf_item = _etf_item_clauses()
    item_clauses = (has_etf_item, ~has_non_etf_item)
    stale = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            *_current_contract_clauses(),
            ShortResearchSignalRun.as_of_trade_date != required_trade_date,
            *item_clauses,
        )
    )
    if stale is not None:
        return CanonicalSnapshotSelection("stale", None)
    fields = _current_contract_fields()
    mismatch = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            ShortResearchSignalRun.status == "success",
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.scope_kind == "full",
            ShortResearchSignalRun.as_of_trade_date == required_trade_date,
            (
                (ShortResearchSignalRun.score_version != fields["score_version"])
                | (ShortResearchSignalRun.rule_version != fields["rule_version"])
                | (ShortResearchSignalRun.score_field != fields["score_field"])
                | (ShortResearchSignalRun.price_basis != fields["price_basis"])
            ),
            *item_clauses,
        )
    )
    if mismatch is not None:
        return CanonicalSnapshotSelection("version_mismatch", None)
    legacy = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            ShortResearchSignalRun.status == "success",
            (
                ShortResearchSignalRun.scope_kind.is_(None)
                | ShortResearchSignalRun.score_version.is_(None)
                | ShortResearchSignalRun.ranking_contract_hash.is_(None)
            ),
        )
    )
    return CanonicalSnapshotSelection("legacy" if legacy is not None else "waiting", None)


async def resolve_current_etf_ranking_surface_snapshot(
    session: AsyncSession,
    *,
    required_trade_date: date,
    ranking_surface: Literal["research", "actionable"],
) -> CanonicalSnapshotSelection:
    research = daily_reconstructable_manifest()
    has_etf_item, has_non_etf_item = _etf_item_clauses()
    base = (
        ShortResearchSignalRun.status == "success",
        ShortResearchSignalRun.scope_kind == "full",
        ShortResearchSignalRun.score_version == research.contract_id,
        ShortResearchSignalRun.rule_version == DUAL_RANKING_RULE_VERSION,
        ShortResearchSignalRun.score_field == research.score_field,
        ShortResearchSignalRun.price_basis == research.price_basis,
        ShortResearchSignalRun.ranking_contract_hash.is_not(None),
        ShortResearchSignalRun.decision_data_coverage_ratio.is_not(None),
        ShortResearchSignalRun.coverage_ratio.is_not(None),
        has_etf_item,
        ~has_non_etf_item,
    )
    registered_runs = (
        await session.scalars(
            select(ShortResearchSignalRun)
            .join(
                EtfCanonicalPublicationRegistry,
                EtfCanonicalPublicationRegistry.source_signal_run_id == ShortResearchSignalRun.id,
            )
            .where(
                *base,
                ShortResearchSignalRun.as_of_trade_date == required_trade_date,
                EtfCanonicalPublicationRegistry.is_current.is_(True),
            )
            .order_by(EtfCanonicalPublicationRegistry.id.desc())
            .limit(2)
        )
    ).all()
    if len(registered_runs) > 1:
        return CanonicalSnapshotSelection("version_mismatch", None)
    if registered_runs:
        runs = registered_runs
    else:
        runs = (
            await session.scalars(
                select(ShortResearchSignalRun)
                .where(
                    *base,
                    ShortResearchSignalRun.as_of_trade_date == required_trade_date,
                )
                .order_by(
                    ShortResearchSignalRun.published_at.desc(),
                    ShortResearchSignalRun.id.desc(),
                )
            )
        ).all()
    for run in runs:
        selection = etf_ranking_surface_selection_from_run(
            run,
            ranking_surface=ranking_surface,
        )
        if selection.run is not None:
            return selection
    if runs:
        return CanonicalSnapshotSelection("waiting", None)
    stale = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            *base,
            ShortResearchSignalRun.as_of_trade_date != required_trade_date,
        )
    )
    if stale is not None:
        return CanonicalSnapshotSelection("stale", None)
    return CanonicalSnapshotSelection("waiting", None)


async def select_canonical_etf_snapshot(
    session: AsyncSession,
    *,
    score_version: str,
    ranking_contract_hash: str,
    price_basis: str,
    required_trade_date: date,
) -> ShortResearchSignalRun | None:
    has_etf_item, has_non_etf_item = _etf_item_clauses()
    registered = (
        await session.scalars(
            select(ShortResearchSignalRun)
            .join(
                EtfCanonicalPublicationRegistry,
                EtfCanonicalPublicationRegistry.source_signal_run_id == ShortResearchSignalRun.id,
            )
            .where(
                ShortResearchSignalRun.status == "success",
                ShortResearchSignalRun.publication_state == "published",
                ShortResearchSignalRun.scope_kind == "full",
                ShortResearchSignalRun.score_version == score_version,
                ShortResearchSignalRun.ranking_contract_hash == ranking_contract_hash,
                ShortResearchSignalRun.price_basis == price_basis,
                ShortResearchSignalRun.as_of_trade_date == required_trade_date,
                ShortResearchSignalRun.decision_data_coverage_ratio
                >= ETF_DAILY_DECISION_MIN_COVERAGE,
                ShortResearchSignalRun.coverage_ratio >= ETF_COMPLETE_SCORE_COVERAGE,
                EtfCanonicalPublicationRegistry.is_current.is_(True),
                has_etf_item,
                ~has_non_etf_item,
            )
            .order_by(EtfCanonicalPublicationRegistry.id.desc())
            .limit(2)
        )
    ).all()
    if len(registered) > 1:
        return None
    if registered:
        run = registered[0]
        return run if snapshot_metadata(run)["snapshot_state"] == "complete" else None
    rows = await session.scalars(
        select(ShortResearchSignalRun)
        .where(
            ShortResearchSignalRun.status == "success",
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.scope_kind == "full",
            ShortResearchSignalRun.score_version == score_version,
            ShortResearchSignalRun.ranking_contract_hash == ranking_contract_hash,
            ShortResearchSignalRun.price_basis == price_basis,
            ShortResearchSignalRun.as_of_trade_date == required_trade_date,
            ShortResearchSignalRun.decision_data_coverage_ratio >= ETF_DAILY_DECISION_MIN_COVERAGE,
            ShortResearchSignalRun.coverage_ratio >= ETF_COMPLETE_SCORE_COVERAGE,
            has_etf_item,
            ~has_non_etf_item,
        )
        .order_by(ShortResearchSignalRun.published_at.desc(), ShortResearchSignalRun.id.desc())
    )
    return next(
        (run for run in rows if snapshot_metadata(run)["snapshot_state"] == "complete"),
        None,
    )


async def resolve_canonical_etf_snapshot(
    session: AsyncSession,
    *,
    score_version: str,
    ranking_contract_hash: str,
    price_basis: str,
    required_trade_date: date,
) -> CanonicalSnapshotSelection:
    run = await select_canonical_etf_snapshot(
        session,
        score_version=score_version,
        ranking_contract_hash=ranking_contract_hash,
        price_basis=price_basis,
        required_trade_date=required_trade_date,
    )
    if run is not None:
        return CanonicalSnapshotSelection("ready", run)

    has_etf_item, has_non_etf_item = _etf_item_clauses()
    base = (
        ShortResearchSignalRun.status == "success",
        ShortResearchSignalRun.publication_state == "published",
        ShortResearchSignalRun.scope_kind == "full",
        has_etf_item,
        ~has_non_etf_item,
    )
    stale = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            *base,
            ShortResearchSignalRun.score_version == score_version,
            ShortResearchSignalRun.ranking_contract_hash == ranking_contract_hash,
            ShortResearchSignalRun.price_basis == price_basis,
            ShortResearchSignalRun.as_of_trade_date != required_trade_date,
        )
    )
    if stale is not None:
        return CanonicalSnapshotSelection("stale", None)
    mismatch = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            *base,
            (
                (ShortResearchSignalRun.score_version != score_version)
                | (ShortResearchSignalRun.ranking_contract_hash != ranking_contract_hash)
                | (ShortResearchSignalRun.price_basis != price_basis)
            ),
        )
    )
    if mismatch is not None:
        return CanonicalSnapshotSelection("version_mismatch", None)
    legacy = await session.scalar(
        select(ShortResearchSignalRun.id).where(
            ShortResearchSignalRun.status == "success",
            (
                ShortResearchSignalRun.scope_kind.is_(None)
                | ShortResearchSignalRun.score_version.is_(None)
                | ShortResearchSignalRun.ranking_contract_hash.is_(None)
            ),
        )
    )
    return CanonicalSnapshotSelection("legacy" if legacy is not None else "waiting", None)
