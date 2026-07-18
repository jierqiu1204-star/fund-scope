"""Factual, fail-closed repair for legacy ETF validation source manifests."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfSignalValidationRun, ShortResearchSignalRun

from .etf_ranking_validation import RankingValidationContractError
from .etf_validation_manifest import (
    attach_validation_source_manifest,
    build_production_validation_source_cohort,
    validate_attached_validation_source_manifest,
)

_IDENTITY_FIELDS = (
    "ranking_contract_hash",
    "scope_kind",
    "scope_hash",
    "universe_snapshot_hash",
    "input_snapshot_hash",
    "score_field",
    "score_version",
    "rule_version",
    "price_basis",
)


@dataclass(frozen=True)
class ValidationManifestRepairItem:
    validation_run_id: int
    disposition: str
    reason: str | None
    source_signal_run_ids: tuple[int, ...]
    source_dates: tuple[str, ...]
    source_manifest_hash: str | None


@dataclass(frozen=True)
class ValidationManifestRepairReport:
    apply: bool
    schema_ready: bool
    blocker: str | None
    inspected_count: int
    ready_count: int
    repaired_count: int
    legacy_untrusted_count: int
    items: tuple[ValidationManifestRepairItem, ...]


def _legacy_item(
    validation_run: EtfSignalValidationRun,
    reason: str,
) -> ValidationManifestRepairItem:
    return ValidationManifestRepairItem(
        validation_run_id=validation_run.id,
        disposition="legacy_untrusted",
        reason=reason,
        source_signal_run_ids=(),
        source_dates=(),
        source_manifest_hash=None,
    )


def _stored_identities(
    validation_run: EtfSignalValidationRun,
    source_ids: tuple[int, ...],
) -> dict[int, dict[str, object]] | None:
    raw_identities = (validation_run.summary_json or {}).get(
        "source_snapshot_identities"
    )
    if not isinstance(raw_identities, list) or len(raw_identities) != len(source_ids):
        return None
    result: dict[int, dict[str, object]] = {}
    for identity in raw_identities:
        if not isinstance(identity, dict):
            return None
        source_id = identity.get("source_signal_run_id")
        if not isinstance(source_id, int) or source_id <= 0 or source_id in result:
            return None
        result[source_id] = identity
    return result if set(result) == set(source_ids) else None


def _identity_matches(
    identity: dict[str, object],
    source_run: ShortResearchSignalRun,
) -> bool:
    if identity.get("source_signal_run_id") != source_run.id:
        return False
    if identity.get("source_date") != (
        source_run.as_of_trade_date.isoformat()
        if source_run.as_of_trade_date is not None
        else None
    ):
        return False
    return all(identity.get(field) == getattr(source_run, field) for field in _IDENTITY_FIELDS)


async def repair_legacy_validation_manifests(
    session: AsyncSession,
    *,
    apply: bool,
    limit: int = 100,
) -> ValidationManifestRepairReport:
    if limit <= 0 or limit > 1_000:
        raise ValueError("limit must be between 1 and 1000")
    connection = await session.connection()
    columns = await connection.run_sync(
        lambda sync_connection: {
            column["name"]
            for column in sa_inspect(sync_connection).get_columns(
                "etf_signal_validation_runs"
            )
        }
    )
    required_columns = {
        "ranking_source_kind",
        "source_manifest_hash",
        "source_event_count",
    }
    missing_columns = sorted(required_columns - columns)
    if missing_columns:
        return ValidationManifestRepairReport(
            apply=apply,
            schema_ready=False,
            blocker="missing_schema_columns:" + ",".join(missing_columns),
            inspected_count=0,
            ready_count=0,
            repaired_count=0,
            legacy_untrusted_count=0,
            items=(),
        )
    candidates = list(
        (
            await session.scalars(
                select(EtfSignalValidationRun)
                .where(
                    EtfSignalValidationRun.status == "success",
                    EtfSignalValidationRun.validation_mode == "score_bucket_replay",
                    EtfSignalValidationRun.ranking_source_kind.is_(None),
                    EtfSignalValidationRun.source_manifest_hash.is_(None),
                )
                .order_by(EtfSignalValidationRun.id)
                .limit(limit)
            )
        ).all()
    )
    items: list[ValidationManifestRepairItem] = []
    ready_count = 0
    repaired_count = 0
    for validation_run in candidates:
        summary = dict(validation_run.summary_json or {})
        if summary.get("ranking_source_kind") != "production_published":
            items.append(_legacy_item(validation_run, "missing_explicit_source_kind"))
            continue
        raw_source_ids = summary.get("source_signal_run_ids")
        if not isinstance(raw_source_ids, list) or not raw_source_ids:
            items.append(_legacy_item(validation_run, "missing_complete_source_links"))
            continue
        source_ids = tuple(
            value
            for value in raw_source_ids
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        )
        if len(source_ids) != len(raw_source_ids) or len(set(source_ids)) != len(source_ids):
            items.append(_legacy_item(validation_run, "ambiguous_source_links"))
            continue
        identities = _stored_identities(validation_run, source_ids)
        if identities is None:
            items.append(_legacy_item(validation_run, "missing_complete_source_identities"))
            continue
        source_runs = list(
            (
                await session.scalars(
                    select(ShortResearchSignalRun).where(
                        ShortResearchSignalRun.id.in_(source_ids)
                    )
                )
            ).all()
        )
        by_id = {source_run.id: source_run for source_run in source_runs}
        if set(by_id) != set(source_ids):
            items.append(_legacy_item(validation_run, "missing_factual_source_run"))
            continue
        ordered_runs = tuple(by_id[source_id] for source_id in source_ids)
        if any(
            not _identity_matches(identities[source_run.id], source_run)
            for source_run in ordered_runs
        ):
            items.append(_legacy_item(validation_run, "stored_identity_mismatch"))
            continue
        try:
            cohort = await build_production_validation_source_cohort(
                session,
                source_runs=ordered_runs,
            )
        except RankingValidationContractError:
            items.append(_legacy_item(validation_run, "incompatible_factual_sources"))
            continue
        ready_count += 1
        disposition = "ready"
        if apply:
            validation_run.status = "running"
            await attach_validation_source_manifest(session, validation_run, cohort)
            await validate_attached_validation_source_manifest(session, validation_run)
            validation_run.status = "success"
            repaired_count += 1
            disposition = "repaired"
        items.append(
            ValidationManifestRepairItem(
                validation_run_id=validation_run.id,
                disposition=disposition,
                reason=None,
                source_signal_run_ids=tuple(
                    event.source_signal_run_id
                    for event in cohort.events
                    if event.source_signal_run_id is not None
                ),
                source_dates=tuple(
                    event.signal_date.isoformat() for event in cohort.events
                ),
                source_manifest_hash=cohort.cohort_hash,
            )
        )
    legacy_untrusted_count = sum(
        item.disposition == "legacy_untrusted" for item in items
    )
    return ValidationManifestRepairReport(
        apply=apply,
        schema_ready=True,
        blocker=None,
        inspected_count=len(candidates),
        ready_count=ready_count,
        repaired_count=repaired_count,
        legacy_untrusted_count=legacy_untrusted_count,
        items=tuple(items),
    )
