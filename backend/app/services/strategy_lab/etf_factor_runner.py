from __future__ import annotations

import asyncio
import time
import tracemalloc
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfFactorExperimentCheckpoint, utcnow
from app.services.etf_research_evidence import stable_contract_hash

MAX_HISTORY_BATCH_SIZE = 20
MAX_DATA_OPERATION_SECONDS = 55.0

_active_lock = Lock()
_active_experiments: set[tuple[str, str]] = set()


class ConcurrentFactorExperimentError(RuntimeError):
    pass


@dataclass(frozen=True)
class FactorBatchResult:
    factor_rows: dict[str, dict[str, Any]]
    cursor_date: date | None
    exclusion_count: int = 0


BatchFetcher = Callable[
    [tuple[str, ...], date | None, dict[str, dict[str, Any]]],
    Awaitable[FactorBatchResult],
]


def _claim_local(identity: tuple[str, str]) -> None:
    with _active_lock:
        if identity in _active_experiments:
            raise ConcurrentFactorExperimentError(
                "factor experiment is already running"
            )
        _active_experiments.add(identity)


def _release_local(identity: tuple[str, str]) -> None:
    with _active_lock:
        _active_experiments.discard(identity)


async def _load_or_create_checkpoint(
    session: AsyncSession,
    *,
    manifest_hash: str,
    code_version: str,
    timeout_seconds: float,
) -> EtfFactorExperimentCheckpoint:
    now = utcnow()
    lease_token = uuid4().hex
    lease_expires_at = now + timedelta(seconds=timeout_seconds + 5)
    identity = (
        EtfFactorExperimentCheckpoint.manifest_hash == manifest_hash,
        EtfFactorExperimentCheckpoint.code_version == code_version,
    )
    checkpoint = await session.scalar(
        select(EtfFactorExperimentCheckpoint).where(*identity)
    )
    if checkpoint is None:
        checkpoint = EtfFactorExperimentCheckpoint(
            manifest_hash=manifest_hash,
            code_version=code_version,
            status="running",
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            processed_asset_codes_json=[],
            completed_batch_hashes_json=[],
            cached_factor_rows_json={},
        )
        session.add(checkpoint)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            await session.refresh(checkpoint)
            return checkpoint
    elif checkpoint.status == "complete":
        return checkpoint

    claimed_id = await session.scalar(
        update(EtfFactorExperimentCheckpoint)
        .where(
            *identity,
            EtfFactorExperimentCheckpoint.status != "complete",
            or_(
                EtfFactorExperimentCheckpoint.status != "running",
                EtfFactorExperimentCheckpoint.lease_expires_at.is_(None),
                EtfFactorExperimentCheckpoint.lease_expires_at <= now,
            ),
        )
        .values(
            status="running",
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            error_summary=None,
        )
        .returning(EtfFactorExperimentCheckpoint.id)
        .execution_options(synchronize_session=False)
    )
    if claimed_id is None:
        await session.rollback()
        raise ConcurrentFactorExperimentError("factor experiment lease is active")
    await session.commit()
    claimed = await session.get(EtfFactorExperimentCheckpoint, claimed_id)
    if claimed is None:
        raise RuntimeError("claimed factor experiment checkpoint disappeared")
    return claimed


async def run_bounded_factor_experiment(
    session: AsyncSession,
    *,
    manifest_hash: str,
    code_version: str,
    asset_codes: Sequence[str],
    fetch_batch: BatchFetcher,
    initial_cursor_date: date | None = None,
    timeout_seconds: float = 50.0,
) -> EtfFactorExperimentCheckpoint:
    if not manifest_hash or not code_version:
        raise ValueError("manifest hash and code version are required")
    if not 0 < timeout_seconds <= MAX_DATA_OPERATION_SECONDS:
        raise ValueError("data-operation timeout must be at most 55 seconds")
    identity = (manifest_hash, code_version)
    _claim_local(identity)
    started = time.perf_counter()
    started_tracing = not tracemalloc.is_tracing()
    if started_tracing:
        tracemalloc.start()
    try:
        checkpoint = await _load_or_create_checkpoint(
            session,
            manifest_hash=manifest_hash,
            code_version=code_version,
            timeout_seconds=timeout_seconds,
        )
        if checkpoint.status == "complete":
            return checkpoint
        ordered_codes = tuple(dict.fromkeys(asset_codes))
        processed = set(checkpoint.processed_asset_codes_json)
        remaining = tuple(code for code in ordered_codes if code not in processed)
        cursor = checkpoint.cursor_date or initial_cursor_date
        cache = dict(checkpoint.cached_factor_rows_json)
        for start in range(0, len(remaining), MAX_HISTORY_BATCH_SIZE):
            batch = remaining[start : start + MAX_HISTORY_BATCH_SIZE]
            try:
                result = await asyncio.wait_for(
                    fetch_batch(batch, cursor, dict(cache)),
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                checkpoint.status = "partial"
                checkpoint.cursor_date = cursor
                checkpoint.error_summary = (
                    f"{type(exc).__name__}: {str(exc).strip() or 'no message'}"
                )[:500]
                checkpoint.runtime_seconds += time.perf_counter() - started
                _, peak = tracemalloc.get_traced_memory()
                checkpoint.peak_memory_bytes = max(checkpoint.peak_memory_bytes, peak)
                checkpoint.lease_token = None
                checkpoint.lease_expires_at = None
                await session.commit()
                await session.refresh(checkpoint)
                return checkpoint
            batch_hash = stable_contract_hash(
                {
                    "asset_codes": batch,
                    "cursor_date": result.cursor_date,
                    "factor_rows": result.factor_rows,
                    "exclusion_count": result.exclusion_count,
                }
            )
            processed.update(batch)
            cache = {**cache, **result.factor_rows}
            cursor = result.cursor_date
            checkpoint.cursor_date = cursor
            checkpoint.processed_asset_codes_json = sorted(processed)
            checkpoint.completed_batch_hashes_json = [
                *checkpoint.completed_batch_hashes_json,
                batch_hash,
            ]
            checkpoint.cached_factor_rows_json = dict(cache)
            checkpoint.exclusion_count += result.exclusion_count
            checkpoint.batch_count += 1
            checkpoint.peak_batch_size = max(checkpoint.peak_batch_size, len(batch))
            checkpoint.coverage_ratio = (
                len(cache) / len(ordered_codes) if ordered_codes else 1.0
            )
            checkpoint.runtime_seconds += time.perf_counter() - started
            started = time.perf_counter()
            _, peak = tracemalloc.get_traced_memory()
            checkpoint.peak_memory_bytes = max(checkpoint.peak_memory_bytes, peak)
            checkpoint.lease_expires_at = utcnow() + timedelta(
                seconds=timeout_seconds + 5
            )
            await session.commit()
        checkpoint.status = "complete"
        checkpoint.lease_token = None
        checkpoint.lease_expires_at = None
        checkpoint.error_summary = None
        checkpoint.coverage_ratio = (
            len(cache) / len(ordered_codes) if ordered_codes else 1.0
        )
        await session.commit()
        await session.refresh(checkpoint)
        return checkpoint
    finally:
        if started_tracing:
            tracemalloc.stop()
        _release_local(identity)
