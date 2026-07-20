from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfCatalystEventVersion,
    EtfCatalystExtractionAttempt,
    EtfCatalystReceipt,
    EtfThemeCatalystEvent,
)
from app.services.etf_catalyst_shadow.policy import CATALYST_TAXONOMY_VERSION
from app.services.etf_research_evidence import stable_contract_hash

EventDirection = Literal["positive", "negative", "neutral", "uncertain"]
VerificationState = Literal[
    "pending",
    "verified",
    "rejected",
    "manual_display_only",
]

_FORBIDDEN_SCORING_FIELDS = {
    "strength",
    "strength_score",
    "catalyst_score",
    "sentiment_heat",
    "sentiment_heat_score",
    "opportunity_score",
    "weight",
    "rank_delta",
    "score",
}
_AI_ALLOWED_FIELDS = {
    "event_id",
    "event_type",
    "entities",
    "title",
    "summary",
    "receipt_ids",
    "source_published_at",
    "effective_start",
    "effective_end",
    "direction",
    "direct_theme_ids",
    "proxy_theme_ids",
    "taxonomy_version",
}


class CatalystEventValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EventCandidate:
    event_type: str
    entities: tuple[str, ...]
    title: str
    summary: str
    receipt_ids: tuple[str, ...]
    source_published_at: datetime
    effective_start: datetime
    effective_end: datetime
    direction: EventDirection
    direct_theme_ids: tuple[str, ...]
    proxy_theme_ids: tuple[str, ...] = ()
    taxonomy_version: str = CATALYST_TAXONOMY_VERSION
    event_id: str | None = None


def _datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise CatalystEventValidationError(f"{field} must be ISO datetime") from exc
    raise CatalystEventValidationError(f"{field} must be a datetime")


def ai_candidate_from_mapping(payload: Mapping[str, Any]) -> EventCandidate:
    keys = set(payload)
    forbidden = keys & _FORBIDDEN_SCORING_FIELDS
    if forbidden:
        raise CatalystEventValidationError(
            "catalyst shadow rejects scoring fields: " + ", ".join(sorted(forbidden))
        )
    unknown = keys - _AI_ALLOWED_FIELDS
    if unknown:
        raise CatalystEventValidationError(
            "AI output contains unsupported fields: " + ", ".join(sorted(unknown))
        )
    required = _AI_ALLOWED_FIELDS - {
        "event_id",
        "summary",
        "proxy_theme_ids",
    }
    missing = required - keys
    if missing:
        raise CatalystEventValidationError(
            "AI output is missing fields: " + ", ".join(sorted(missing))
        )
    return EventCandidate(
        event_id=str(payload["event_id"]).strip() if payload.get("event_id") else None,
        event_type=str(payload["event_type"]).strip(),
        entities=tuple(str(value).strip() for value in payload["entities"]),
        title=str(payload["title"]).strip(),
        summary=str(payload.get("summary") or "").strip(),
        receipt_ids=tuple(str(value).strip() for value in payload["receipt_ids"]),
        source_published_at=_datetime(
            payload["source_published_at"], "source_published_at"
        ),
        effective_start=_datetime(payload["effective_start"], "effective_start"),
        effective_end=_datetime(payload["effective_end"], "effective_end"),
        direction=str(payload["direction"]),  # type: ignore[arg-type]
        direct_theme_ids=tuple(
            str(value).strip() for value in payload["direct_theme_ids"]
        ),
        proxy_theme_ids=tuple(
            str(value).strip() for value in payload.get("proxy_theme_ids", ())
        ),
        taxonomy_version=str(payload["taxonomy_version"]).strip(),
    )


async def _supporting_receipts(
    session: AsyncSession,
    receipt_ids: Sequence[str],
) -> tuple[EtfCatalystReceipt, ...]:
    ordered_ids = tuple(dict.fromkeys(receipt_ids))
    if not ordered_ids:
        raise CatalystEventValidationError("event requires at least one receipt citation")
    rows = (
        await session.scalars(
            select(EtfCatalystReceipt).where(
                EtfCatalystReceipt.receipt_id.in_(ordered_ids)
            )
        )
    ).all()
    by_id = {row.receipt_id: row for row in rows}
    if set(by_id) != set(ordered_ids):
        raise CatalystEventValidationError("event cites an unknown receipt")
    receipts = tuple(by_id[receipt_id] for receipt_id in ordered_ids)
    if any(receipt.fetch_state != "item" for receipt in receipts):
        raise CatalystEventValidationError("only item receipts can support events")
    return receipts


def _validate_candidate(
    candidate: EventCandidate,
    receipts: Sequence[EtfCatalystReceipt],
) -> None:
    if (
        not candidate.event_type
        or not candidate.title
        or not candidate.entities
        or any(not entity for entity in candidate.entities)
    ):
        raise CatalystEventValidationError(
            "event type, title, and entities are required"
        )
    if candidate.direction not in {"positive", "negative", "neutral", "uncertain"}:
        raise CatalystEventValidationError("event direction is invalid")
    if candidate.taxonomy_version != CATALYST_TAXONOMY_VERSION:
        raise CatalystEventValidationError("taxonomy version is not registered")
    if not candidate.direct_theme_ids and not candidate.proxy_theme_ids:
        raise CatalystEventValidationError("event requires a direct or proxy mapping")
    if set(candidate.direct_theme_ids) & set(candidate.proxy_theme_ids):
        raise CatalystEventValidationError(
            "direct and proxy theme mappings must be separate"
        )
    if candidate.effective_start > candidate.effective_end:
        raise CatalystEventValidationError("effective period is invalid")
    published_times = {
        receipt.source_published_at
        for receipt in receipts
        if receipt.source_published_at is not None
    }
    if candidate.source_published_at not in published_times:
        raise CatalystEventValidationError(
            "event published time is not supported by cited receipts"
        )
    supported_entities = {
        str(value)
        for receipt in receipts
        for value in receipt.metadata_json.get("supported_entities", [])
    }
    if supported_entities and not set(candidate.entities).issubset(supported_entities):
        raise CatalystEventValidationError(
            "event entities are not supported by cited receipts"
        )
    supported_direct = {
        str(value)
        for receipt in receipts
        for value in receipt.metadata_json.get("supported_theme_ids", [])
    }
    if supported_direct and not set(candidate.direct_theme_ids).issubset(
        supported_direct
    ):
        raise CatalystEventValidationError(
            "direct theme mapping is not supported by cited receipts"
        )
    supported_proxy = {
        str(value)
        for receipt in receipts
        for value in receipt.metadata_json.get("supported_proxy_theme_ids", [])
    }
    if candidate.proxy_theme_ids and (
        not supported_proxy
        or not set(candidate.proxy_theme_ids).issubset(supported_proxy)
    ):
        raise CatalystEventValidationError(
            "proxy theme mapping is not supported by cited receipts"
        )
    supported_directions = {
        str(receipt.metadata_json["supported_direction"])
        for receipt in receipts
        if receipt.metadata_json.get("supported_direction")
    }
    if supported_directions and candidate.direction not in supported_directions:
        raise CatalystEventValidationError(
            "event direction is not supported by cited receipts"
        )


async def normalize_event_candidate(
    session: AsyncSession,
    candidate: EventCandidate,
    *,
    extraction_method: Literal["deterministic", "ai", "human_review"],
    verify: bool = False,
) -> EtfCatalystEventVersion:
    receipts = await _supporting_receipts(session, candidate.receipt_ids)
    _validate_candidate(candidate, receipts)
    if extraction_method == "ai" and verify:
        raise CatalystEventValidationError(
            "AI extraction cannot directly create a verified event"
        )
    state: VerificationState = "verified" if verify else "pending"
    first_received_at = max(receipt.first_received_at for receipt in receipts)
    event_identity = candidate.event_id or stable_contract_hash(
        {
            "event_type": candidate.event_type,
            "entities": sorted(candidate.entities),
            "title": candidate.title,
            "direct_theme_ids": sorted(candidate.direct_theme_ids),
            "proxy_theme_ids": sorted(candidate.proxy_theme_ids),
        }
    )
    previous = await session.scalar(
        select(EtfCatalystEventVersion)
        .where(EtfCatalystEventVersion.event_id == event_identity)
        .order_by(
            EtfCatalystEventVersion.event_version.desc(),
            EtfCatalystEventVersion.id.desc(),
        )
    )
    payload = {
        **asdict(candidate),
        "event_id": event_identity,
        "first_received_at": first_received_at,
        "extraction_method": extraction_method,
        "verification_state": state,
        "supersedes_event_hash": previous.event_hash if previous else None,
    }
    event_hash = stable_contract_hash(payload)
    existing = await session.scalar(
        select(EtfCatalystEventVersion).where(
            EtfCatalystEventVersion.event_hash == event_hash
        )
    )
    if existing is not None:
        return existing
    event = EtfCatalystEventVersion(
        event_id=event_identity,
        event_version=(previous.event_version + 1) if previous else 1,
        event_type=candidate.event_type,
        entities_json=list(candidate.entities),
        title=candidate.title,
        summary=candidate.summary,
        supporting_receipt_ids_json=list(candidate.receipt_ids),
        source_published_at=candidate.source_published_at,
        first_received_at=first_received_at,
        effective_start=candidate.effective_start,
        effective_end=candidate.effective_end,
        direction=candidate.direction,
        direct_theme_ids_json=list(candidate.direct_theme_ids),
        proxy_theme_ids_json=list(candidate.proxy_theme_ids),
        taxonomy_version=candidate.taxonomy_version,
        extraction_method=extraction_method,
        verification_state=state,
        supersedes_event_version_id=previous.id if previous else None,
        event_hash=event_hash,
        limitations_json=(
            ["proxy mapping is display-only unless pre-registered separately"]
            if candidate.proxy_theme_ids
            else []
        ),
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def verify_pending_event(
    session: AsyncSession,
    event_version_id: int,
) -> EtfCatalystEventVersion:
    pending = await session.get(EtfCatalystEventVersion, event_version_id)
    if pending is None or pending.verification_state != "pending":
        raise CatalystEventValidationError("only a pending event can be verified")
    candidate = EventCandidate(
        event_id=pending.event_id,
        event_type=pending.event_type,
        entities=tuple(pending.entities_json),
        title=pending.title,
        summary=pending.summary,
        receipt_ids=tuple(pending.supporting_receipt_ids_json),
        source_published_at=pending.source_published_at,
        effective_start=pending.effective_start,
        effective_end=pending.effective_end,
        direction=pending.direction,  # type: ignore[arg-type]
        direct_theme_ids=tuple(pending.direct_theme_ids_json),
        proxy_theme_ids=tuple(pending.proxy_theme_ids_json),
        taxonomy_version=pending.taxonomy_version,
    )
    return await normalize_event_candidate(
        session,
        candidate,
        extraction_method="human_review",
        verify=True,
    )


async def _record_attempt(
    session: AsyncSession,
    *,
    receipt: EtfCatalystReceipt,
    extractor_version: str,
    status: Literal["success", "invalid", "failed"],
    candidate: Mapping[str, Any] | None,
    error_summary: str | None,
) -> EtfCatalystExtractionAttempt:
    payload = {
        "receipt_id": receipt.receipt_id,
        "extractor_version": extractor_version,
        "status": status,
        "candidate": dict(candidate or {}),
        "error_summary": error_summary,
    }
    attempt_hash = stable_contract_hash(payload)
    existing = await session.scalar(
        select(EtfCatalystExtractionAttempt).where(
            EtfCatalystExtractionAttempt.receipt_id == receipt.id,
            EtfCatalystExtractionAttempt.extractor_version == extractor_version,
            EtfCatalystExtractionAttempt.attempt_hash == attempt_hash,
        )
    )
    if existing is not None:
        return existing
    attempt = EtfCatalystExtractionAttempt(
        receipt_id=receipt.id,
        extractor_version=extractor_version,
        extraction_method="ai",
        status=status,
        candidate_json=dict(candidate or {}),
        cited_receipt_ids_json=list((candidate or {}).get("receipt_ids", [])),
        error_summary=error_summary,
        attempt_hash=attempt_hash,
    )
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    return attempt


async def normalize_ai_extraction(
    session: AsyncSession,
    *,
    receipt_id: str,
    extractor_version: str,
    payload: Mapping[str, Any],
) -> EtfCatalystEventVersion:
    receipt = await session.scalar(
        select(EtfCatalystReceipt).where(
            EtfCatalystReceipt.receipt_id == receipt_id
        )
    )
    if receipt is None:
        raise CatalystEventValidationError("AI extraction receipt does not exist")
    try:
        candidate = ai_candidate_from_mapping(payload)
        if set(candidate.receipt_ids) != {receipt.receipt_id}:
            raise CatalystEventValidationError(
                "AI output must cite exactly its registered input receipt"
            )
        event = await normalize_event_candidate(
            session,
            candidate,
            extraction_method="ai",
            verify=False,
        )
    except CatalystEventValidationError as exc:
        await _record_attempt(
            session,
            receipt=receipt,
            extractor_version=extractor_version,
            status="invalid",
            candidate=payload,
            error_summary=str(exc),
        )
        raise
    await _record_attempt(
        session,
        receipt=receipt,
        extractor_version=extractor_version,
        status="success",
        candidate=payload,
        error_summary=None,
    )
    return event


async def record_ai_extraction_failure(
    session: AsyncSession,
    *,
    receipt_id: str,
    extractor_version: str,
    error_summary: str,
) -> EtfCatalystExtractionAttempt:
    receipt = await session.scalar(
        select(EtfCatalystReceipt).where(
            EtfCatalystReceipt.receipt_id == receipt_id
        )
    )
    if receipt is None:
        raise CatalystEventValidationError("AI extraction receipt does not exist")
    if not error_summary.strip():
        raise CatalystEventValidationError("AI failure requires an error summary")
    return await _record_attempt(
        session,
        receipt=receipt,
        extractor_version=extractor_version,
        status="failed",
        candidate=None,
        error_summary=error_summary[:500],
    )


async def migrate_manual_seeds_display_only(
    session: AsyncSession,
) -> tuple[EtfCatalystEventVersion, ...]:
    seeds = (
        await session.scalars(
            select(EtfThemeCatalystEvent).where(
                EtfThemeCatalystEvent.source_type == "manual_seed"
            )
        )
    ).all()
    migrated: list[EtfCatalystEventVersion] = []
    for seed in seeds:
        event_id = f"legacy-manual-{seed.id}"
        existing = await session.scalar(
            select(EtfCatalystEventVersion).where(
                EtfCatalystEventVersion.event_id == event_id,
                EtfCatalystEventVersion.verification_state
                == "manual_display_only",
            )
        )
        if existing is not None:
            migrated.append(existing)
            continue
        event_date = seed.event_date or seed.created_at.date()
        effective_start = seed.effective_start or event_date
        effective_end = seed.effective_end or effective_start
        proxy = bool((seed.metadata_json or {}).get("proxy_theme"))
        payload = {
            "event_id": event_id,
            "title": seed.title,
            "event_date": event_date,
            "theme_key": seed.theme_key,
            "verification_state": "manual_display_only",
        }
        event = EtfCatalystEventVersion(
            event_id=event_id,
            event_version=1,
            event_type=seed.catalyst_type,
            entities_json=[],
            title=seed.title,
            summary=seed.summary,
            supporting_receipt_ids_json=[],
            source_published_at=datetime.combine(event_date, time.min),
            first_received_at=seed.created_at,
            effective_start=datetime.combine(effective_start, time.min),
            effective_end=datetime.combine(effective_end, time.max),
            direction=(
                seed.direction
                if seed.direction in {"positive", "negative", "neutral", "uncertain"}
                else "uncertain"
            ),
            direct_theme_ids_json=[] if proxy else [seed.theme_key],
            proxy_theme_ids_json=[seed.theme_key] if proxy else [],
            taxonomy_version=CATALYST_TAXONOMY_VERSION,
            extraction_method="manual_legacy",
            verification_state="manual_display_only",
            supersedes_event_version_id=None,
            event_hash=stable_contract_hash(payload),
            limitations_json=[
                "legacy manual seed has no registered immutable source receipt",
                "display-only and excluded from verified snapshots and event studies",
            ],
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        migrated.append(event)
    return tuple(migrated)


def candidate_with(
    candidate: EventCandidate,
    **changes: Any,
) -> EventCandidate:
    return replace(candidate, **changes)
