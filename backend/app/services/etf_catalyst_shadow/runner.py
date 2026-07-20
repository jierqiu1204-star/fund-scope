from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Literal
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfCatalystReceipt, EtfCatalystRunCheckpoint
from app.services.etf_catalyst_shadow.ingestion import (
    ReceiptInput,
    ingest_receipt,
    register_approved_sources,
)
from app.services.etf_catalyst_shadow.policy import (
    APPROVED_CATALYST_SOURCES,
    MAX_FETCH_ITEMS,
    MAX_OPERATION_SECONDS,
    catalyst_source_policy_hash,
)
from app.services.etf_research_evidence import stable_contract_hash

_FETCH_LOCK = asyncio.Lock()
_PROCESS_LOCK = asyncio.Lock()


class CatalystRunAlreadyActiveError(RuntimeError):
    pass


class CatalystRunAlreadyAttemptedError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceFetchBatch:
    receipts: tuple[ReceiptInput, ...]
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True)
class CatalystBatchResult:
    state: Literal["complete", "partial", "unavailable"]
    source_id: str
    session_key: str
    cursor_before: str | None
    cursor_after: str | None
    received_count: int
    new_receipt_count: int
    duplicate_count: int
    correction_count: int
    fetch_states: dict[str, int]
    latency_ms: int
    receipt_ids: tuple[str, ...]
    batch_hash: str


SourceFetcher = Callable[
    [str, str, str | None, int],
    Awaitable[SourceFetchBatch],
]
ReceiptProcessor = Callable[[EtfCatalystReceipt], Awaitable[None]]


async def _checkpoint(
    session: AsyncSession,
    *,
    run_kind: str,
) -> EtfCatalystRunCheckpoint:
    policy_hash = catalyst_source_policy_hash()
    row = await session.scalar(
        select(EtfCatalystRunCheckpoint).where(
            EtfCatalystRunCheckpoint.run_kind == run_kind,
            EtfCatalystRunCheckpoint.policy_hash == policy_hash,
        )
    )
    if row is None:
        row = EtfCatalystRunCheckpoint(
            run_kind=run_kind,
            policy_hash=policy_hash,
            status="idle",
            source_cursor_json={},
            processed_receipt_ids_json=[],
            batch_hashes_json=[],
            details_json={},
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            row = await session.scalar(
                select(EtfCatalystRunCheckpoint).where(
                    EtfCatalystRunCheckpoint.run_kind == run_kind,
                    EtfCatalystRunCheckpoint.policy_hash == policy_hash,
                )
            )
            if row is None:
                raise RuntimeError(
                    "catalyst checkpoint identity conflict"
                ) from None
        else:
            await session.refresh(row)
    return row


async def _acquire(
    session: AsyncSession,
    checkpoint: EtfCatalystRunCheckpoint,
    *,
    now: datetime,
) -> str:
    token = uuid4().hex
    claimed_id = await session.scalar(
        update(EtfCatalystRunCheckpoint)
        .where(
            EtfCatalystRunCheckpoint.id == checkpoint.id,
            or_(
                EtfCatalystRunCheckpoint.status != "running",
                EtfCatalystRunCheckpoint.lease_expires_at.is_(None),
                EtfCatalystRunCheckpoint.lease_expires_at <= now,
            ),
        )
        .values(
            status="running",
            lease_token=token,
            lease_expires_at=now + timedelta(
                seconds=MAX_OPERATION_SECONDS + 5,
            ),
            error_summary=None,
        )
        .returning(EtfCatalystRunCheckpoint.id)
        .execution_options(synchronize_session=False)
    )
    if claimed_id is None:
        await session.rollback()
        raise CatalystRunAlreadyActiveError(
            "catalyst shadow run is already active"
        )
    await session.commit()
    await session.refresh(checkpoint)
    return token


def _source(source_id: str):
    return next(
        (item for item in APPROVED_CATALYST_SOURCES if item.source_id == source_id),
        None,
    )


async def run_source_fetch_batch(
    session: AsyncSession,
    *,
    source_id: str,
    session_key: str,
    fetcher: SourceFetcher,
    maximum_items: int = MAX_FETCH_ITEMS,
    timeout_seconds: float = MAX_OPERATION_SECONDS,
) -> CatalystBatchResult:
    if _FETCH_LOCK.locked():
        raise CatalystRunAlreadyActiveError("the single catalyst fetch worker is busy")
    if not 1 <= maximum_items <= MAX_FETCH_ITEMS:
        raise ValueError(f"maximum_items must be between 1 and {MAX_FETCH_ITEMS}")
    if not 0 < timeout_seconds <= MAX_OPERATION_SECONDS:
        raise ValueError(
            f"timeout_seconds must be at most {MAX_OPERATION_SECONDS:g}"
        )
    definition = _source(source_id)
    if definition is None:
        raise ValueError("source is not on the approved allowlist")
    await register_approved_sources(session)

    async with _FETCH_LOCK:
        checkpoint = await _checkpoint(session, run_kind="fetch")
        details = dict(checkpoint.details_json or {})
        attempted = {
            str(key): list(value)
            for key, value in dict(details.get("attempted_sources_by_session") or {}).items()
        }
        if source_id in attempted.get(session_key, []):
            raise CatalystRunAlreadyAttemptedError(
                "source was already attempted in this session"
            )
        now = datetime.now()
        await _acquire(session, checkpoint, now=now)
        cursor_before = dict(checkpoint.source_cursor_json or {}).get(source_id)
        started = monotonic()
        try:
            batch = await asyncio.wait_for(
                fetcher(
                    definition.source_id,
                    definition.endpoint,
                    cursor_before,
                    maximum_items,
                ),
                timeout=timeout_seconds,
            )
            if len(batch.receipts) > maximum_items:
                raise ValueError("fetcher exceeded the bounded item batch")
            if any(item.source_id != source_id for item in batch.receipts):
                raise ValueError("fetcher returned a receipt for another source")
            ingested: list[EtfCatalystReceipt] = []
            existing_ids = set(checkpoint.processed_receipt_ids_json or [])
            for item in batch.receipts:
                ingested.append(await ingest_receipt(session, item))
            receipt_ids = tuple(item.receipt_id for item in ingested)
            new_ids = tuple(
                dict.fromkeys(
                    item for item in receipt_ids if item not in existing_ids
                )
            )
            latency_ms = int((monotonic() - started) * 1000)
            states = dict(Counter(item.fetch_state for item in batch.receipts))
            batch_hash = stable_contract_hash(
                {
                    "source_id": source_id,
                    "session_key": session_key,
                    "cursor_before": cursor_before,
                    "cursor_after": batch.next_cursor,
                    "receipt_ids": receipt_ids,
                    "complete": batch.complete,
                }
            )
            cursors = dict(checkpoint.source_cursor_json or {})
            cursors[source_id] = batch.next_cursor
            attempted.setdefault(session_key, []).append(source_id)
            history = list(details.get("batch_history") or [])
            history.append(
                {
                    "source_id": source_id,
                    "session_key": session_key,
                    "state": "complete" if batch.complete else "partial",
                    "received_count": len(batch.receipts),
                    "new_receipt_count": len(new_ids),
                    "duplicate_count": len(receipt_ids) - len(new_ids),
                    "correction_count": sum(
                        item.correction_of_receipt_id is not None for item in ingested
                    ),
                    "fetch_states": states,
                    "latency_ms": latency_ms,
                    "cursor_before": cursor_before,
                    "cursor_after": batch.next_cursor,
                    "batch_hash": batch_hash,
                }
            )
            checkpoint.status = "complete" if batch.complete else "partial"
            checkpoint.source_cursor_json = cursors
            checkpoint.processed_receipt_ids_json = list(
                dict.fromkeys([*sorted(existing_ids), *receipt_ids])
            )
            checkpoint.batch_hashes_json = [
                *list(checkpoint.batch_hashes_json or []),
                batch_hash,
            ]
            checkpoint.details_json = {
                **details,
                "worker_count": 1,
                "maximum_items": maximum_items,
                "operation_timeout_seconds": timeout_seconds,
                "attempted_sources_by_session": attempted,
                "batch_history": history,
            }
            checkpoint.lease_token = None
            checkpoint.lease_expires_at = None
            await session.commit()
            return CatalystBatchResult(
                state="complete" if batch.complete else "partial",
                source_id=source_id,
                session_key=session_key,
                cursor_before=cursor_before,
                cursor_after=batch.next_cursor,
                received_count=len(batch.receipts),
                new_receipt_count=len(new_ids),
                duplicate_count=len(receipt_ids) - len(new_ids),
                correction_count=sum(
                    item.correction_of_receipt_id is not None for item in ingested
                ),
                fetch_states=states,
                latency_ms=latency_ms,
                receipt_ids=receipt_ids,
                batch_hash=batch_hash,
            )
        except Exception as exc:
            latency_ms = int((monotonic() - started) * 1000)
            summary = f"{type(exc).__name__}: {str(exc)[:180]}"
            observed_at = datetime.now()
            outage = await ingest_receipt(
                session,
                ReceiptInput(
                    source_id=source_id,
                    fetch_state="unavailable",
                    fetched_at=observed_at,
                    first_received_at=observed_at,
                    observation_key=(
                        f"{source_id}:{session_key}:{cursor_before or 'start'}:unavailable"
                    ),
                    error_summary=summary,
                ),
            )
            attempted.setdefault(session_key, []).append(source_id)
            batch_hash = stable_contract_hash(
                {
                    "source_id": source_id,
                    "session_key": session_key,
                    "cursor_before": cursor_before,
                    "state": "unavailable",
                    "receipt_id": outage.receipt_id,
                    "error_summary": summary,
                }
            )
            history = list(details.get("batch_history") or [])
            history.append(
                {
                    "source_id": source_id,
                    "session_key": session_key,
                    "state": "unavailable",
                    "received_count": 1,
                    "new_receipt_count": 1,
                    "duplicate_count": 0,
                    "correction_count": 0,
                    "fetch_states": {"unavailable": 1},
                    "latency_ms": latency_ms,
                    "cursor_before": cursor_before,
                    "cursor_after": cursor_before,
                    "batch_hash": batch_hash,
                    "error_summary": summary,
                }
            )
            checkpoint.status = "failed"
            checkpoint.error_summary = summary
            checkpoint.processed_receipt_ids_json = list(
                dict.fromkeys(
                    [
                        *list(checkpoint.processed_receipt_ids_json or []),
                        outage.receipt_id,
                    ]
                )
            )
            checkpoint.batch_hashes_json = [
                *list(checkpoint.batch_hashes_json or []),
                batch_hash,
            ]
            checkpoint.details_json = {
                **details,
                "worker_count": 1,
                "maximum_items": maximum_items,
                "operation_timeout_seconds": timeout_seconds,
                "attempted_sources_by_session": attempted,
                "batch_history": history,
            }
            checkpoint.lease_token = None
            checkpoint.lease_expires_at = None
            await session.commit()
            return CatalystBatchResult(
                state="unavailable",
                source_id=source_id,
                session_key=session_key,
                cursor_before=cursor_before,
                cursor_after=cursor_before,
                received_count=1,
                new_receipt_count=1,
                duplicate_count=0,
                correction_count=0,
                fetch_states={"unavailable": 1},
                latency_ms=latency_ms,
                receipt_ids=(outage.receipt_id,),
                batch_hash=batch_hash,
            )


async def run_receipt_processing_batch(
    session: AsyncSession,
    *,
    phase: Literal["extraction", "mapping"],
    processor: ReceiptProcessor,
    maximum_items: int = MAX_FETCH_ITEMS,
    timeout_seconds: float = MAX_OPERATION_SECONDS,
) -> dict[str, object]:
    if _PROCESS_LOCK.locked():
        raise CatalystRunAlreadyActiveError("catalyst processing worker is busy")
    if not 1 <= maximum_items <= MAX_FETCH_ITEMS:
        raise ValueError(f"maximum_items must be between 1 and {MAX_FETCH_ITEMS}")
    if not 0 < timeout_seconds <= MAX_OPERATION_SECONDS:
        raise ValueError(
            f"timeout_seconds must be at most {MAX_OPERATION_SECONDS:g}"
        )
    async with _PROCESS_LOCK:
        checkpoint = await _checkpoint(session, run_kind=phase)
        await _acquire(session, checkpoint, now=datetime.now())
        processed = set(checkpoint.processed_receipt_ids_json or [])
        rows = (
            await session.scalars(
                select(EtfCatalystReceipt)
                .where(EtfCatalystReceipt.receipt_id.not_in(processed))
                .order_by(EtfCatalystReceipt.id)
                .limit(maximum_items)
            )
        ).all()

        async def process() -> list[str]:
            completed: list[str] = []
            for receipt in rows:
                await processor(receipt)
                completed.append(receipt.receipt_id)
            return completed

        started = monotonic()
        try:
            completed = await asyncio.wait_for(process(), timeout=timeout_seconds)
            batch_hash = stable_contract_hash(
                {"phase": phase, "receipt_ids": completed}
            )
            checkpoint.processed_receipt_ids_json = list(
                dict.fromkeys([*sorted(processed), *completed])
            )
            checkpoint.batch_hashes_json = [
                *list(checkpoint.batch_hashes_json or []),
                batch_hash,
            ]
            checkpoint.status = "complete" if len(rows) < maximum_items else "partial"
            checkpoint.details_json = {
                **dict(checkpoint.details_json or {}),
                "worker_count": 1,
                "maximum_items": maximum_items,
                "operation_timeout_seconds": timeout_seconds,
                "last_latency_ms": int((monotonic() - started) * 1000),
                "last_batch_count": len(completed),
            }
            checkpoint.lease_token = None
            checkpoint.lease_expires_at = None
            await session.commit()
            return {
                "phase": phase,
                "state": checkpoint.status,
                "processed_receipt_ids": completed,
                "batch_hash": batch_hash,
            }
        except Exception as exc:
            checkpoint.status = "failed"
            checkpoint.error_summary = f"{type(exc).__name__}: {str(exc)[:180]}"
            checkpoint.lease_token = None
            checkpoint.lease_expires_at = None
            await session.commit()
            raise
