from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfCatalystReceipt, EtfCatalystSourceRegistry
from app.services.etf_catalyst_shadow.policy import (
    APPROVED_CATALYST_SOURCES,
    CATALYST_SOURCE_POLICY_ID,
    catalyst_source_policy_hash,
    catalyst_source_policy_payload,
)
from app.services.etf_research_evidence import stable_contract_hash

FetchState = Literal[
    "item",
    "successful_empty",
    "unavailable",
    "not_applicable",
]


class CatalystSourcePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ReceiptInput:
    source_id: str
    fetch_state: FetchState
    fetched_at: datetime
    first_received_at: datetime
    external_id: str | None = None
    canonical_url: str | None = None
    source_published_at: datetime | None = None
    published_time_precision: str = "second"
    raw_content: str | bytes | None = None
    raw_content_ref: str | None = None
    parser_version: str = "catalyst_html_v1"
    observation_key: str | None = None
    error_summary: str | None = None
    metadata: dict[str, Any] | None = None


def _canonical_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise CatalystSourcePolicyError("canonical URL must be absolute HTTP(S)")
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit(
        (
            parsed.scheme.lower(),
            f"{host}{port}",
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


async def register_approved_sources(
    session: AsyncSession,
) -> tuple[EtfCatalystSourceRegistry, ...]:
    policy = catalyst_source_policy_payload()
    policy_hash = catalyst_source_policy_hash()
    rows: list[EtfCatalystSourceRegistry] = []
    for definition in APPROVED_CATALYST_SOURCES:
        existing = await session.scalar(
            select(EtfCatalystSourceRegistry).where(
                EtfCatalystSourceRegistry.source_id == definition.source_id,
                EtfCatalystSourceRegistry.registry_version
                == CATALYST_SOURCE_POLICY_ID,
            )
        )
        if existing is not None:
            if existing.policy_hash != policy_hash:
                raise CatalystSourcePolicyError(
                    "registered source policy version has conflicting content"
                )
            rows.append(existing)
            continue
        row = EtfCatalystSourceRegistry(
            source_id=definition.source_id,
            registry_version=CATALYST_SOURCE_POLICY_ID,
            source_class=definition.source_class,
            allowed_domain=definition.allowed_domain,
            endpoint=definition.endpoint,
            timezone=definition.timezone,
            cadence=definition.cadence,
            fetch_policy_json=dict(policy["fetch_policy"]),
            raw_retention_policy_json=dict(policy["raw_content_retention"]),
            policy_hash=policy_hash,
            active=True,
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return tuple(rows)


async def _registered_source(
    session: AsyncSession,
    source_id: str,
) -> EtfCatalystSourceRegistry:
    source = await session.scalar(
        select(EtfCatalystSourceRegistry).where(
            EtfCatalystSourceRegistry.source_id == source_id,
            EtfCatalystSourceRegistry.registry_version
            == CATALYST_SOURCE_POLICY_ID,
            EtfCatalystSourceRegistry.active.is_(True),
        )
    )
    if source is None:
        raise CatalystSourcePolicyError("source is not registered and active")
    return source


def _validate_receipt_input(
    source: EtfCatalystSourceRegistry,
    item: ReceiptInput,
) -> tuple[str | None, str]:
    canonical_url = _canonical_url(item.canonical_url)
    if canonical_url is not None:
        host = (urlsplit(canonical_url).hostname or "").lower()
        allowed = source.allowed_domain.lower()
        if host != allowed and not host.endswith(f".{allowed}"):
            raise CatalystSourcePolicyError("receipt URL is outside the source allowlist")
    if item.first_received_at > item.fetched_at:
        raise CatalystSourcePolicyError("first received time cannot be after fetch time")
    if item.fetch_state == "item":
        if not item.external_id and canonical_url is None:
            raise CatalystSourcePolicyError(
                "item requires an external ID or canonical URL"
            )
        if item.source_published_at is None:
            raise CatalystSourcePolicyError("item requires source published time")
        if item.source_published_at > item.first_received_at:
            raise CatalystSourcePolicyError(
                "source published time cannot be after first received time"
            )
        if item.raw_content is None:
            raise CatalystSourcePolicyError("item receipt requires raw content")
    elif not item.observation_key:
        raise CatalystSourcePolicyError(
            "non-item receipt requires a deterministic observation key"
        )
    if item.fetch_state == "unavailable" and not (item.error_summary or "").strip():
        raise CatalystSourcePolicyError("unavailable receipt requires an error summary")
    if item.fetch_state != "unavailable" and item.error_summary:
        raise CatalystSourcePolicyError(
            "only unavailable receipts may carry an error summary"
        )
    return canonical_url, source.allowed_domain


def _content_hash(item: ReceiptInput) -> str:
    if isinstance(item.raw_content, bytes):
        raw_content = item.raw_content.hex()
    elif isinstance(item.raw_content, str):
        raw_content = item.raw_content
    else:
        raw_content = None
    return stable_contract_hash(
        {
            "fetch_state": item.fetch_state,
            "raw_content": raw_content,
            "error_summary": item.error_summary,
            "observation_key": item.observation_key,
        }
    )


async def ingest_receipt(
    session: AsyncSession,
    item: ReceiptInput,
) -> EtfCatalystReceipt:
    source = await _registered_source(session, item.source_id)
    canonical_url, _allowed_domain = _validate_receipt_input(source, item)
    identity_payload = {
        "source_id": item.source_id,
        "identity": item.external_id or canonical_url or item.observation_key,
    }
    item_identity_hash = stable_contract_hash(identity_payload)
    content_hash = _content_hash(item)
    existing = await session.scalar(
        select(EtfCatalystReceipt).where(
            EtfCatalystReceipt.source_registry_id == source.id,
            EtfCatalystReceipt.item_identity_hash == item_identity_hash,
            EtfCatalystReceipt.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing
    previous = await session.scalar(
        select(EtfCatalystReceipt)
        .where(
            EtfCatalystReceipt.source_registry_id == source.id,
            EtfCatalystReceipt.item_identity_hash == item_identity_hash,
        )
        .order_by(
            EtfCatalystReceipt.first_received_at.desc(),
            EtfCatalystReceipt.id.desc(),
        )
    )
    payload = {
        "source_policy_hash": source.policy_hash,
        "item_identity_hash": item_identity_hash,
        "content_hash": content_hash,
        "external_id": item.external_id,
        "canonical_url": canonical_url,
        "source_published_at": item.source_published_at,
        "first_received_at": item.first_received_at,
        "fetched_at": item.fetched_at,
        "fetch_state": item.fetch_state,
        "raw_content_ref": item.raw_content_ref,
        "parser_version": item.parser_version,
        "correction_of_receipt_id": previous.receipt_id if previous else None,
        "error_summary": item.error_summary,
        "metadata": item.metadata or {},
    }
    receipt_hash = stable_contract_hash(payload)
    receipt = EtfCatalystReceipt(
        receipt_id=f"catalyst-receipt-{receipt_hash}",
        source_registry_id=source.id,
        external_id=item.external_id,
        canonical_url=canonical_url,
        source_published_at=item.source_published_at,
        published_time_precision=item.published_time_precision,
        first_received_at=item.first_received_at,
        fetched_at=item.fetched_at,
        fetch_state=item.fetch_state,
        content_hash=content_hash,
        receipt_hash=receipt_hash,
        item_identity_hash=item_identity_hash,
        raw_content_ref=item.raw_content_ref,
        parser_version=item.parser_version,
        correction_of_receipt_id=previous.id if previous else None,
        error_summary=item.error_summary,
        metadata_json=item.metadata or {},
    )
    session.add(receipt)
    await session.commit()
    await session.refresh(receipt)
    return receipt
