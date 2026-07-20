from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfCatalystCoverageObservation,
    EtfCatalystEventVersion,
    EtfCatalystReceipt,
    EtfCatalystShadowSnapshot,
    EtfCatalystSourceRegistry,
)
from app.services.etf_catalyst_shadow.policy import (
    CATALYST_SHADOW_CONTRACT_VERSION,
    CATALYST_SOURCE_POLICY_ID,
    CATALYST_TAXONOMY_VERSION,
)
from app.services.etf_research_evidence import stable_contract_hash

CoverageState = Literal[
    "active",
    "observed_none",
    "unavailable",
    "not_applicable",
]


class CatalystSnapshotError(ValueError):
    pass


async def record_coverage_observation(
    session: AsyncSession,
    *,
    source_id: str,
    theme_id: str,
    session_date: date,
    cutoff_at: datetime,
    state: CoverageState,
    receipt_ids: Sequence[str],
    reason: str | None = None,
    policy_id: str = CATALYST_SOURCE_POLICY_ID,
) -> EtfCatalystCoverageObservation:
    source = await session.scalar(
        select(EtfCatalystSourceRegistry).where(
            EtfCatalystSourceRegistry.source_id == source_id,
            EtfCatalystSourceRegistry.registry_version == policy_id,
            EtfCatalystSourceRegistry.active.is_(True),
        )
    )
    if source is None:
        raise CatalystSnapshotError("coverage source is not registered")
    ordered_receipt_ids = tuple(dict.fromkeys(receipt_ids))
    receipts = (
        (
            await session.scalars(
                select(EtfCatalystReceipt).where(
                    EtfCatalystReceipt.receipt_id.in_(ordered_receipt_ids)
                )
            )
        ).all()
        if ordered_receipt_ids
        else []
    )
    if {receipt.receipt_id for receipt in receipts} != set(ordered_receipt_ids):
        raise CatalystSnapshotError("coverage cites an unknown receipt")
    if any(
        receipt.source_registry_id != source.id
        or receipt.first_received_at > cutoff_at
        for receipt in receipts
    ):
        raise CatalystSnapshotError(
            "coverage receipt has wrong source or was received after cutoff"
        )
    states = {receipt.fetch_state for receipt in receipts}
    required_fetch_state = {
        "active": "item",
        "observed_none": "successful_empty",
        "unavailable": "unavailable",
        "not_applicable": "not_applicable",
    }[state]
    if required_fetch_state not in states:
        raise CatalystSnapshotError(
            f"{state} coverage requires a {required_fetch_state} receipt"
        )
    if state in {"unavailable", "not_applicable"} and not (reason or "").strip():
        raise CatalystSnapshotError(f"{state} coverage requires a reason")
    payload = {
        "source_policy_hash": source.policy_hash,
        "source_id": source_id,
        "theme_id": theme_id,
        "session_date": session_date,
        "cutoff_at": cutoff_at,
        "state": state,
        "reason": reason,
        "policy_id": policy_id,
        "receipt_ids": ordered_receipt_ids,
    }
    observation_hash = stable_contract_hash(payload)
    existing = await session.scalar(
        select(EtfCatalystCoverageObservation).where(
            EtfCatalystCoverageObservation.observation_hash == observation_hash
        )
    )
    if existing is not None:
        return existing
    observation = EtfCatalystCoverageObservation(
        source_registry_id=source.id,
        theme_id=theme_id,
        session_date=session_date,
        cutoff_at=cutoff_at,
        state=state,
        reason=reason,
        policy_id=policy_id,
        receipt_ids_json=list(ordered_receipt_ids),
        observation_hash=observation_hash,
    )
    session.add(observation)
    await session.commit()
    await session.refresh(observation)
    return observation


async def _events_known_at_cutoff(
    session: AsyncSession,
    *,
    theme_id: str,
    cutoff_at: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = (
        await session.scalars(
            select(EtfCatalystEventVersion)
            .where(
                EtfCatalystEventVersion.source_published_at <= cutoff_at,
                EtfCatalystEventVersion.first_received_at <= cutoff_at,
            )
            .order_by(
                EtfCatalystEventVersion.event_id.asc(),
                EtfCatalystEventVersion.event_version.desc(),
                EtfCatalystEventVersion.id.desc(),
            )
        )
    ).all()
    latest_by_event: dict[str, EtfCatalystEventVersion] = {}
    for row in rows:
        latest_by_event.setdefault(row.event_id, row)
    direct: list[dict[str, Any]] = []
    proxy: list[dict[str, Any]] = []
    for event in latest_by_event.values():
        if (
            event.verification_state != "verified"
            or event.effective_start > cutoff_at
            or event.effective_end < cutoff_at
        ):
            continue
        mapping_kind: str | None = None
        if theme_id in event.direct_theme_ids_json:
            mapping_kind = "direct"
        elif theme_id in event.proxy_theme_ids_json:
            mapping_kind = "proxy"
        if mapping_kind is None:
            continue
        payload = {
            "event_id": event.event_id,
            "event_version": event.event_version,
            "event_hash": event.event_hash,
            "event_type": event.event_type,
            "entities": event.entities_json,
            "title": event.title,
            "summary": event.summary,
            "source_published_at": event.source_published_at.isoformat(),
            "first_received_at": event.first_received_at.isoformat(),
            "effective_start": event.effective_start.isoformat(),
            "effective_end": event.effective_end.isoformat(),
            "direction": event.direction,
            "mapping_kind": mapping_kind,
            "direct_theme_ids": event.direct_theme_ids_json,
            "proxy_theme_ids": event.proxy_theme_ids_json,
            "taxonomy_version": event.taxonomy_version,
            "extraction_method": event.extraction_method,
            "verification_state": event.verification_state,
            "is_correction": event.supersedes_event_version_id is not None,
            "receipt_ids": event.supporting_receipt_ids_json,
            "limitations": event.limitations_json,
        }
        (direct if mapping_kind == "direct" else proxy).append(payload)
    direct.sort(key=lambda item: (item["event_id"], item["event_version"]))
    proxy.sort(key=lambda item: (item["event_id"], item["event_version"]))
    return direct, proxy


async def _coverage_known_at_cutoff(
    session: AsyncSession,
    *,
    theme_id: str,
    session_date: date,
    cutoff_at: datetime,
) -> list[dict[str, Any]]:
    sources = (
        await session.scalars(
            select(EtfCatalystSourceRegistry).where(
                EtfCatalystSourceRegistry.registry_version
                == CATALYST_SOURCE_POLICY_ID,
                EtfCatalystSourceRegistry.active.is_(True),
            )
        )
    ).all()
    observations = (
        await session.scalars(
            select(EtfCatalystCoverageObservation)
            .where(
                EtfCatalystCoverageObservation.theme_id == theme_id,
                EtfCatalystCoverageObservation.session_date == session_date,
                EtfCatalystCoverageObservation.cutoff_at <= cutoff_at,
            )
            .order_by(
                EtfCatalystCoverageObservation.source_registry_id.asc(),
                EtfCatalystCoverageObservation.cutoff_at.desc(),
                EtfCatalystCoverageObservation.id.desc(),
            )
        )
    ).all()
    latest_by_source: dict[int, EtfCatalystCoverageObservation] = {}
    for observation in observations:
        latest_by_source.setdefault(observation.source_registry_id, observation)
    coverage: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda item: item.source_id):
        observation = latest_by_source.get(source.id)
        coverage.append(
            {
                "source_id": source.source_id,
                "state": observation.state if observation else "unavailable",
                "reason": (
                    observation.reason
                    if observation
                    else "required source not observed by cutoff"
                ),
                "receipt_ids": observation.receipt_ids_json if observation else [],
                "observation_hash": (
                    observation.observation_hash if observation else None
                ),
            }
        )
    return coverage


def _aggregate_coverage_state(
    *,
    direct_events: Sequence[dict[str, Any]],
    source_coverage: Sequence[dict[str, Any]],
) -> CoverageState:
    if direct_events:
        return "active"
    states = [str(item["state"]) for item in source_coverage]
    if not states or "unavailable" in states:
        return "unavailable"
    applicable = [state for state in states if state != "not_applicable"]
    if not applicable:
        return "not_applicable"
    if all(state == "observed_none" for state in applicable):
        return "observed_none"
    return "unavailable"


async def build_shadow_snapshot(
    session: AsyncSession,
    *,
    theme_id: str,
    session_date: date,
    cutoff_at: datetime,
) -> EtfCatalystShadowSnapshot:
    direct_events, proxy_events = await _events_known_at_cutoff(
        session,
        theme_id=theme_id,
        cutoff_at=cutoff_at,
    )
    source_coverage = await _coverage_known_at_cutoff(
        session,
        theme_id=theme_id,
        session_date=session_date,
        cutoff_at=cutoff_at,
    )
    state = _aggregate_coverage_state(
        direct_events=direct_events,
        source_coverage=source_coverage,
    )
    limitations = [
        "catalyst shadow is research-only and has zero ranking weight",
        "proxy mappings are not direct ETF catalyst evidence",
    ]
    if state == "unavailable":
        limitations.append(
            "required source coverage is unavailable; silence cannot be inferred"
        )
    event_versions = [*direct_events, *proxy_events]
    receipt_ids = sorted(
        {
            receipt_id
            for event in event_versions
            for receipt_id in event["receipt_ids"]
        }
        | {
            receipt_id
            for coverage in source_coverage
            for receipt_id in coverage["receipt_ids"]
        }
    )
    payload = {
        "theme_id": theme_id,
        "session_date": session_date,
        "cutoff_at": cutoff_at,
        "taxonomy_version": CATALYST_TAXONOMY_VERSION,
        "contract_version": CATALYST_SHADOW_CONTRACT_VERSION,
        "coverage_state": state,
        "event_versions": event_versions,
        "source_coverage": source_coverage,
        "receipt_ids": receipt_ids,
        "limitations": limitations,
    }
    snapshot_hash = stable_contract_hash(payload)
    existing = await session.scalar(
        select(EtfCatalystShadowSnapshot).where(
            EtfCatalystShadowSnapshot.theme_id == theme_id,
            EtfCatalystShadowSnapshot.session_date == session_date,
            EtfCatalystShadowSnapshot.cutoff_at == cutoff_at,
            EtfCatalystShadowSnapshot.contract_version
            == CATALYST_SHADOW_CONTRACT_VERSION,
        )
    )
    if existing is not None:
        if existing.snapshot_hash != snapshot_hash:
            raise CatalystSnapshotError(
                "historical snapshot inputs changed for an immutable cutoff"
            )
        return existing
    snapshot = EtfCatalystShadowSnapshot(
        theme_id=theme_id,
        session_date=session_date,
        cutoff_at=cutoff_at,
        taxonomy_version=CATALYST_TAXONOMY_VERSION,
        contract_version=CATALYST_SHADOW_CONTRACT_VERSION,
        coverage_state=state,
        event_versions_json=event_versions,
        source_coverage_json=source_coverage,
        receipt_ids_json=receipt_ids,
        limitations_json=limitations,
        snapshot_hash=snapshot_hash,
    )
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def latest_completed_shadow_by_theme(
    session: AsyncSession,
    *,
    theme_ids: Sequence[str],
    as_of_date: date,
) -> dict[str, EtfCatalystShadowSnapshot]:
    rows = (
        await session.scalars(
            select(EtfCatalystShadowSnapshot)
            .where(
                EtfCatalystShadowSnapshot.theme_id.in_(tuple(theme_ids)),
                EtfCatalystShadowSnapshot.session_date <= as_of_date,
            )
            .order_by(
                EtfCatalystShadowSnapshot.session_date.desc(),
                EtfCatalystShadowSnapshot.cutoff_at.desc(),
                EtfCatalystShadowSnapshot.id.desc(),
            )
        )
    ).all()
    result: dict[str, EtfCatalystShadowSnapshot] = {}
    for row in rows:
        result.setdefault(row.theme_id, row)
    return result
