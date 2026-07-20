from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfCatalystExtractionAttempt,
    EtfCatalystReceipt,
    EtfCatalystShadowSnapshot,
    EtfCatalystSourceRegistry,
)
from app.services.etf_catalyst_shadow.policy import CATALYST_RANKING_WEIGHT
from app.services.etf_catalyst_shadow.snapshots import latest_completed_shadow_by_theme
from app.services.etf_research_evidence import stable_contract_hash


def _receipt_payload(
    receipt: EtfCatalystReceipt,
    source: EtfCatalystSourceRegistry,
) -> dict[str, Any]:
    return {
        "receipt_id": receipt.receipt_id,
        "receipt_hash": receipt.receipt_hash,
        "content_hash": receipt.content_hash,
        "source_id": source.source_id,
        "source_registry_version": source.registry_version,
        "source_policy_hash": source.policy_hash,
        "canonical_url": receipt.canonical_url,
        "external_id": receipt.external_id,
        "source_published_at": (
            receipt.source_published_at.isoformat()
            if receipt.source_published_at
            else None
        ),
        "first_received_at": receipt.first_received_at.isoformat(),
        "fetched_at": receipt.fetched_at.isoformat(),
        "fetch_state": receipt.fetch_state,
        "parser_version": receipt.parser_version,
        "correction_of_receipt_id": receipt.correction_of_receipt_id,
        "error_summary": receipt.error_summary,
    }


def serialize_catalyst_snapshot_evidence(
    snapshot: EtfCatalystShadowSnapshot,
    *,
    receipts_by_id: dict[str, EtfCatalystReceipt],
    sources_by_id: dict[int, EtfCatalystSourceRegistry],
    attempts_by_receipt_id: dict[int, list[EtfCatalystExtractionAttempt]],
) -> dict[str, Any]:
    receipts = [
        _receipt_payload(receipts_by_id[receipt_id], sources_by_id[receipts_by_id[receipt_id].source_registry_id])
        for receipt_id in snapshot.receipt_ids_json
        if receipt_id in receipts_by_id
        and receipts_by_id[receipt_id].source_registry_id in sources_by_id
    ]
    source_registry = sorted(
        {
            (
                source.source_id,
                source.registry_version,
                source.policy_hash,
                source.source_class,
            )
            for source in sources_by_id.values()
        }
    )
    extraction = [
        {
            "receipt_id": receipt.receipt_id,
            "attempt_hash": attempt.attempt_hash,
            "extractor_version": attempt.extractor_version,
            "method": attempt.extraction_method,
            "status": attempt.status,
            "error_summary": attempt.error_summary,
        }
        for receipt in receipts_by_id.values()
        for attempt in attempts_by_receipt_id.get(receipt.id, [])
    ]
    payload = {
        "source_registry": [
            {
                "source_id": source_id,
                "registry_version": version,
                "policy_hash": policy_hash,
                "source_class": source_class,
            }
            for source_id, version, policy_hash, source_class in source_registry
        ],
        "receipts": receipts,
        "events": snapshot.event_versions_json,
        "mappings": [
            {
                "event_id": event["event_id"],
                "event_version": event["event_version"],
                "mapping_kind": event["mapping_kind"],
                "direct_theme_ids": event["direct_theme_ids"],
                "proxy_theme_ids": event["proxy_theme_ids"],
                "taxonomy_version": event["taxonomy_version"],
            }
            for event in snapshot.event_versions_json
        ],
        "snapshot": {
            "theme_id": snapshot.theme_id,
            "session_date": snapshot.session_date.isoformat(),
            "cutoff_at": snapshot.cutoff_at.isoformat(),
            "contract_version": snapshot.contract_version,
            "taxonomy_version": snapshot.taxonomy_version,
            "coverage_state": snapshot.coverage_state,
            "snapshot_hash": snapshot.snapshot_hash,
        },
        "coverage": snapshot.source_coverage_json,
        "extraction": extraction,
        "verification": [
            {
                "event_id": event["event_id"],
                "event_version": event["event_version"],
                "state": event["verification_state"],
                "method": event["extraction_method"],
            }
            for event in snapshot.event_versions_json
        ],
        "limitations": snapshot.limitations_json,
        "production_isolation": {
            "research_only": True,
            "ranking_weight": CATALYST_RANKING_WEIGHT,
            "may_change_score": False,
            "may_change_rank": False,
            "may_change_allocation": False,
            "may_change_alert": False,
            "may_change_notification": False,
        },
    }
    return {**payload, "evidence_hash": stable_contract_hash(payload)}


async def catalyst_shadow_contexts(
    session: AsyncSession,
    *,
    theme_ids: Sequence[str],
    as_of_date: date,
) -> dict[str, dict[str, Any]]:
    ordered_theme_ids = tuple(dict.fromkeys(theme_ids))
    snapshots = await latest_completed_shadow_by_theme(
        session,
        theme_ids=ordered_theme_ids,
        as_of_date=as_of_date,
    )
    receipt_ids = {
        receipt_id
        for snapshot in snapshots.values()
        for receipt_id in snapshot.receipt_ids_json
    }
    receipts = (
        (
            await session.scalars(
                select(EtfCatalystReceipt).where(
                    EtfCatalystReceipt.receipt_id.in_(receipt_ids)
                )
            )
        ).all()
        if receipt_ids
        else []
    )
    receipts_by_id = {receipt.receipt_id: receipt for receipt in receipts}
    source_registry_ids = {receipt.source_registry_id for receipt in receipts}
    sources = (
        (
            await session.scalars(
                select(EtfCatalystSourceRegistry).where(
                    EtfCatalystSourceRegistry.id.in_(source_registry_ids)
                )
            )
        ).all()
        if source_registry_ids
        else []
    )
    sources_by_id = {source.id: source for source in sources}
    receipt_db_ids = {receipt.id for receipt in receipts}
    attempts = (
        (
            await session.scalars(
                select(EtfCatalystExtractionAttempt).where(
                    EtfCatalystExtractionAttempt.receipt_id.in_(receipt_db_ids)
                )
            )
        ).all()
        if receipt_db_ids
        else []
    )
    attempts_by_receipt_id: dict[int, list[EtfCatalystExtractionAttempt]] = {}
    for attempt in attempts:
        attempts_by_receipt_id.setdefault(attempt.receipt_id, []).append(attempt)
    return {
        theme_id: serialize_catalyst_snapshot_evidence(
            snapshot,
            receipts_by_id=receipts_by_id,
            sources_by_id=sources_by_id,
            attempts_by_receipt_id=attempts_by_receipt_id,
        )
        for theme_id, snapshot in snapshots.items()
    }
