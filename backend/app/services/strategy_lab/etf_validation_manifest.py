"""Persist and revalidate registered ETF ranking validation source manifests."""

from __future__ import annotations

import math
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfSignalValidationRun,
    EtfSignalValidationSourceEvent,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    utcnow,
)
from app.services.etf_research_evidence import RankingSourceKind
from app.services.tracked_positions.lifecycle import stable_contract_hash

from .etf_ranking_stage_b import StageBDateManifest, StageBRankingEvent
from .etf_ranking_validation import (
    RESEARCH_RULE_VERSION,
    RESEARCH_SCORE_FIELD,
    RESEARCH_SCORE_VERSION,
    TOTAL_RETURN_ADJUSTED,
    RankingValidationContractError,
    RankingValidationSourceCohort,
    RankingValidationSourceEvent,
    freeze_ranking_validation_source_cohort,
    freeze_ranking_validation_source_event,
)


def build_research_replay_validation_source_event(
    *,
    ranking_event: StageBRankingEvent,
    date_manifest: StageBDateManifest,
    replay_contract_hash: str,
) -> RankingValidationSourceEvent:
    """Adapt one immutable Stage-B date without production publication fields."""

    if (
        ranking_event.replay_run_key != date_manifest.replay_run_key
        or ranking_event.replay_date != date_manifest.replay_date
        or ranking_event.date_manifest_hash != date_manifest.manifest_hash
        or ranking_event.source_date_manifest_hash
        != date_manifest.source_date_manifest_hash
        or ranking_event.score_contract_id != RESEARCH_SCORE_VERSION
        or ranking_event.score_field != RESEARCH_SCORE_FIELD
        or ranking_event.score_manifest_hash != date_manifest.score_manifest_hash
        or ranking_event.universe_hash != date_manifest.universe_hash
        or ranking_event.input_hash != date_manifest.input_hash
    ):
        raise RankingValidationContractError(
            "Stage-B ranking event does not match its immutable date manifest"
        )
    if len(replay_contract_hash) != 64:
        raise RankingValidationContractError(
            "research replay contract hash is required"
        )
    draft = RankingValidationSourceEvent(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        signal_date=ranking_event.replay_date,
        source_signal_run_id=None,
        source_replay_run_key=ranking_event.replay_run_key,
        source_replay_contract_hash=replay_contract_hash,
        source_event_hash=ranking_event.event_hash,
        ranking_contract_hash=ranking_event.score_manifest_hash,
        scope_hash=stable_contract_hash(
            {
                "scope_kind": "research_replay",
                "replay_run_key": ranking_event.replay_run_key,
                "source_snapshot_hash": date_manifest.source_snapshot_hash,
                "universe_manifest_hash": date_manifest.universe_manifest_hash,
            }
        ),
        universe_snapshot_hash=ranking_event.universe_hash,
        input_snapshot_hash=ranking_event.input_hash,
        score_version=RESEARCH_SCORE_VERSION,
        score_field=RESEARCH_SCORE_FIELD,
        rule_version=RESEARCH_RULE_VERSION,
        price_basis=TOTAL_RETURN_ADJUSTED,
        publication_state=None,
        scope_kind="research_replay",
        availability_cutoff=date_manifest.decision_cutoff,
        immutable_hash="pending",
    )
    return freeze_ranking_validation_source_event(draft)


def build_research_replay_validation_source_cohort(
    *,
    ranking_events: Sequence[StageBRankingEvent],
    date_manifests: Sequence[StageBDateManifest],
    replay_contract_hash: str,
) -> RankingValidationSourceCohort:
    """Build a research-only cohort from exact Stage-B event/manifest pairs."""

    manifests_by_date = {item.replay_date: item for item in date_manifests}
    if len(manifests_by_date) != len(date_manifests):
        raise RankingValidationContractError(
            "research replay contains duplicate date manifests"
        )
    events = tuple(
        build_research_replay_validation_source_event(
            ranking_event=event,
            date_manifest=manifests_by_date[event.replay_date],
            replay_contract_hash=replay_contract_hash,
        )
        for event in ranking_events
    )
    if len(events) != len(manifests_by_date):
        raise RankingValidationContractError(
            "research replay event and manifest dates must match exactly"
        )
    return freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        events=events,
    )


def _source_event_content_hash(
    source_run: ShortResearchSignalRun,
    items: Sequence[ShortResearchSignalItem],
) -> str:
    return stable_contract_hash(
        {
            "source_signal_run_id": source_run.id,
            "source_date": source_run.as_of_trade_date,
            "ranking_contract_hash": source_run.ranking_contract_hash,
            "scope_kind": source_run.scope_kind,
            "scope_hash": source_run.scope_hash,
            "universe_snapshot_hash": source_run.universe_snapshot_hash,
            "input_snapshot_hash": source_run.input_snapshot_hash,
            "score_version": source_run.score_version,
            "score_field": source_run.score_field,
            "rule_version": source_run.rule_version,
            "price_basis": source_run.price_basis,
            "publication_state": source_run.publication_state,
            "data_cutoff": source_run.data_cutoff,
            "expected_item_count": source_run.expected_item_count,
            "decision_data_item_count": source_run.decision_data_item_count,
            "decision_data_coverage_ratio": source_run.decision_data_coverage_ratio,
            "eligible_item_count": source_run.eligible_item_count,
            "coverage_ratio": source_run.coverage_ratio,
            "idempotency_key": source_run.idempotency_key,
            "items": [
                {
                    "asset_type": item.asset_type,
                    "asset_code": item.asset_code,
                    "global_rank": item.global_rank,
                    "ranking_score": item.ranking_score,
                    "score_eligible": item.score_eligible,
                }
                for item in sorted(
                    items,
                    key=lambda value: (
                        value.global_rank if value.global_rank is not None else 2**31,
                        value.asset_type,
                        value.asset_code,
                    ),
                )
            ],
        }
    )


async def build_production_validation_source_event(
    session: AsyncSession,
    source_run: ShortResearchSignalRun,
) -> RankingValidationSourceEvent:
    items = list(
        (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == source_run.id)
                .order_by(
                    ShortResearchSignalItem.global_rank.asc(),
                    ShortResearchSignalItem.asset_type.asc(),
                    ShortResearchSignalItem.asset_code.asc(),
                )
            )
        ).all()
    )
    signal_date = source_run.as_of_trade_date
    if signal_date is None or source_run.data_cutoff is None:
        raise RankingValidationContractError(
            "production source date and availability cutoff are required"
        )
    finite_eligible_count = sum(
        item.score_eligible is True
        and isinstance(item.ranking_score, int | float)
        and not isinstance(item.ranking_score, bool)
        and math.isfinite(float(item.ranking_score))
        for item in items
    )
    ranks = sorted(
        item.global_rank
        for item in items
        if isinstance(item.global_rank, int) and not isinstance(item.global_rank, bool)
    )
    draft = RankingValidationSourceEvent(
        ranking_source_kind=RankingSourceKind.PRODUCTION_PUBLISHED,
        signal_date=signal_date,
        source_signal_run_id=source_run.id,
        source_replay_run_key=None,
        source_replay_contract_hash=None,
        source_event_hash=_source_event_content_hash(source_run, items),
        ranking_contract_hash=source_run.ranking_contract_hash or "",
        scope_hash=source_run.scope_hash or "",
        universe_snapshot_hash=source_run.universe_snapshot_hash or "",
        input_snapshot_hash=source_run.input_snapshot_hash or "",
        score_version=source_run.score_version or "",
        score_field=source_run.score_field or "",
        rule_version=source_run.rule_version or "",
        price_basis=source_run.price_basis or "",
        publication_state=source_run.publication_state,
        scope_kind=source_run.scope_kind or "",
        availability_cutoff=source_run.data_cutoff,
        immutable_hash="pending",
        source_status=source_run.status,
        idempotency_key=source_run.idempotency_key,
        expected_asset_count=source_run.expected_item_count,
        decision_data_covered_count=source_run.decision_data_item_count,
        eligible_asset_count=source_run.eligible_item_count,
        item_count=len(items),
        decision_data_coverage_ratio=source_run.decision_data_coverage_ratio,
        score_coverage_ratio=source_run.coverage_ratio,
        etf_item_count=sum(item.asset_type == "etf" for item in items),
        finite_eligible_score_count=finite_eligible_count,
        contiguous_global_rank=ranks == list(range(1, len(items) + 1)),
    )
    return freeze_ranking_validation_source_event(draft)


async def build_production_validation_source_cohort(
    session: AsyncSession,
    *,
    source_runs: Sequence[ShortResearchSignalRun],
) -> RankingValidationSourceCohort:
    events = tuple(
        [
            await build_production_validation_source_event(session, source_run)
            for source_run in source_runs
        ]
    )
    return freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.PRODUCTION_PUBLISHED,
        events=events,
    )


def _common_or_none(values: Sequence[object]) -> object | None:
    return values[0] if values and all(value == values[0] for value in values) else None


async def attach_validation_source_manifest(
    session: AsyncSession,
    validation_run: EtfSignalValidationRun,
    cohort: RankingValidationSourceCohort,
) -> None:
    if validation_run.id is None:
        raise RankingValidationContractError("validation run must be persisted first")
    if validation_run.status == "success":
        raise RankingValidationContractError(
            "validation source manifest must be attached before success"
        )
    existing = await session.scalar(
        select(EtfSignalValidationSourceEvent.id).where(
            EtfSignalValidationSourceEvent.validation_run_id == validation_run.id
        )
    )
    if existing is not None:
        raise RankingValidationContractError(
            "validation source manifest is immutable once attached"
        )
    ranking_source_kind = cohort.ranking_source_kind.value
    validation_run.ranking_source_kind = ranking_source_kind
    validation_run.source_signal_run_id = None
    validation_run.source_replay_run_key = cohort.source_replay_run_key
    validation_run.source_manifest_hash = cohort.cohort_hash
    validation_run.source_event_count = len(cohort.events)
    validation_run.source_ranking_contract_hash = cohort.events[0].ranking_contract_hash
    validation_run.source_scope_kind = str(
        _common_or_none([event.scope_kind for event in cohort.events]) or ""
    )
    validation_run.source_scope_hash = _common_or_none(
        [event.scope_hash for event in cohort.events]
    )
    validation_run.source_universe_snapshot_hash = _common_or_none(
        [event.universe_snapshot_hash for event in cohort.events]
    )
    validation_run.source_input_snapshot_hash = _common_or_none(
        [event.input_snapshot_hash for event in cohort.events]
    )
    validation_run.source_score_field = cohort.score_field
    validation_run.source_score_version = cohort.score_version
    validation_run.source_rule_version = cohort.rule_version
    validation_run.price_basis = cohort.price_basis
    validation_run.data_cutoff = _common_or_none(
        [event.availability_cutoff for event in cohort.events]
    )
    for order, event in enumerate(cohort.events):
        session.add(
            EtfSignalValidationSourceEvent(
                validation_run_id=validation_run.id,
                event_order=order,
                source_date=event.signal_date,
                ranking_source_kind=ranking_source_kind,
                source_signal_run_id=event.source_signal_run_id,
                source_replay_run_key=event.source_replay_run_key,
                source_replay_contract_hash=event.source_replay_contract_hash,
                source_event_hash=event.source_event_hash,
                ranking_contract_hash=event.ranking_contract_hash,
                scope_hash=event.scope_hash,
                universe_snapshot_hash=event.universe_snapshot_hash,
                input_snapshot_hash=event.input_snapshot_hash,
                availability_cutoff=event.availability_cutoff,
                score_version=event.score_version,
                score_field=event.score_field,
                rule_version=event.rule_version,
                price_basis=event.price_basis,
                publication_state=event.publication_state,
                scope_kind=event.scope_kind,
                source_status=event.source_status,
                idempotency_key=event.idempotency_key,
                expected_asset_count=event.expected_asset_count,
                decision_data_covered_count=event.decision_data_covered_count,
                eligible_asset_count=event.eligible_asset_count,
                item_count=event.item_count,
                decision_data_coverage_ratio=event.decision_data_coverage_ratio,
                score_coverage_ratio=event.score_coverage_ratio,
                etf_item_count=event.etf_item_count,
                finite_eligible_score_count=event.finite_eligible_score_count,
                contiguous_global_rank=event.contiguous_global_rank,
                immutable_hash=event.immutable_hash,
                created_at=utcnow(),
            )
        )
    await session.flush()


def _event_from_row(row: EtfSignalValidationSourceEvent) -> RankingValidationSourceEvent:
    return RankingValidationSourceEvent(
        ranking_source_kind=RankingSourceKind(row.ranking_source_kind),
        signal_date=row.source_date,
        source_signal_run_id=row.source_signal_run_id,
        source_replay_run_key=row.source_replay_run_key,
        source_replay_contract_hash=row.source_replay_contract_hash,
        source_event_hash=row.source_event_hash,
        ranking_contract_hash=row.ranking_contract_hash,
        scope_hash=row.scope_hash,
        universe_snapshot_hash=row.universe_snapshot_hash,
        input_snapshot_hash=row.input_snapshot_hash,
        score_version=row.score_version,
        score_field=row.score_field,
        rule_version=row.rule_version,
        price_basis=row.price_basis,
        publication_state=row.publication_state,
        scope_kind=row.scope_kind,
        availability_cutoff=row.availability_cutoff,
        immutable_hash=row.immutable_hash,
        source_status=row.source_status,
        idempotency_key=row.idempotency_key,
        expected_asset_count=row.expected_asset_count,
        decision_data_covered_count=row.decision_data_covered_count,
        eligible_asset_count=row.eligible_asset_count,
        item_count=row.item_count,
        decision_data_coverage_ratio=row.decision_data_coverage_ratio,
        score_coverage_ratio=row.score_coverage_ratio,
        etf_item_count=row.etf_item_count,
        finite_eligible_score_count=row.finite_eligible_score_count,
        contiguous_global_rank=row.contiguous_global_rank,
    )


async def validate_attached_validation_source_manifest(
    session: AsyncSession,
    validation_run: EtfSignalValidationRun,
) -> RankingValidationSourceCohort:
    if validation_run.ranking_source_kind is None:
        raise RankingValidationContractError("validation source kind is required")
    try:
        source_kind = RankingSourceKind(validation_run.ranking_source_kind)
    except ValueError as exc:
        raise RankingValidationContractError(
            "validation source kind is not registered"
        ) from exc
    rows = list(
        (
            await session.scalars(
                select(EtfSignalValidationSourceEvent)
                .where(
                    EtfSignalValidationSourceEvent.validation_run_id
                    == validation_run.id
                )
                .order_by(EtfSignalValidationSourceEvent.event_order)
            )
        ).all()
    )
    if validation_run.source_event_count != len(rows):
        raise RankingValidationContractError(
            "validation source manifest count does not match persisted events"
        )
    if [row.event_order for row in rows] != list(range(len(rows))):
        raise RankingValidationContractError(
            "validation source manifest event ordering is not contiguous"
        )
    events = tuple(_event_from_row(row) for row in rows)
    cohort = freeze_ranking_validation_source_cohort(
        ranking_source_kind=source_kind,
        events=events,
    )
    if validation_run.source_manifest_hash != cohort.cohort_hash:
        raise RankingValidationContractError(
            "validation source manifest immutable hash does not match header"
        )
    if validation_run.source_replay_run_key != cohort.source_replay_run_key:
        raise RankingValidationContractError(
            "validation source replay identity does not match manifest"
        )
    return cohort
