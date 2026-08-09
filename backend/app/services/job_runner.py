from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import JobRun, utcnow

_BUSINESS_JOB_STATUSES = {"skipped", "partial", "failed"}
INTERRUPTED_BY_RESTART = "interrupted_by_process_restart"


def _job_run_payload(job_run: JobRun) -> dict[str, Any]:
    return {
        "id": job_run.id,
        "job_name": job_run.job_name,
        "status": job_run.status,
        "started_at": job_run.started_at.isoformat(),
        "finished_at": job_run.finished_at.isoformat() if job_run.finished_at else None,
        "error_message": job_run.error_message,
        "details": job_run.details_json,
    }


def _business_result_status(result: dict[str, Any]) -> tuple[str, str | None]:
    status = str(result.get("job_status") or "success")
    if status not in _BUSINESS_JOB_STATUSES:
        return "success", None
    message = result.get("job_message")
    return status, str(message) if status == "failed" and message else None


async def _finish_job_run(job_run: JobRun, result: dict[str, Any]) -> None:
    status, error_message = _business_result_status(result)
    job_run.status = status
    job_run.error_message = error_message
    job_run.details_json = result
    job_run.finished_at = utcnow()


async def reconcile_interrupted_job_runs(
    session_factory: Callable[[], AsyncSession],
    *,
    job_names: Collection[str],
) -> int:
    """Close audit rows that cannot still be running after process startup."""

    normalized_names = tuple(
        sorted({name.strip() for name in job_names if isinstance(name, str) and name.strip()})
    )
    if not normalized_names:
        return 0
    finished_at = utcnow()
    async with session_factory() as session:
        result = await session.execute(
            update(JobRun)
            .where(
                JobRun.job_name.in_(normalized_names),
                JobRun.status == "running",
                JobRun.finished_at.is_(None),
            )
            .values(
                status="failed",
                finished_at=finished_at,
                error_message=INTERRUPTED_BY_RESTART,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)


async def run_job(
    session_factory: Callable[[], AsyncSession],
    job_name: str,
    job: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with session_factory() as session:
        job_run = JobRun(job_name=job_name, status="running")
        session.add(job_run)
        await session.commit()
        job_run_id = int(job_run.id)
        try:
            result = await job(session)
            await _finish_job_run(job_run, result)
            await session.commit()
            return result
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            persisted_job_run = await session.get(JobRun, job_run_id)
            if persisted_job_run is not None:
                persisted_job_run.status = "failed"
                persisted_job_run.error_message = str(exc)
                persisted_job_run.finished_at = utcnow()
                await session.commit()
            raise


async def start_background_job(
    session_factory: Callable[[], AsyncSession],
    job_name: str,
    job: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with session_factory() as session:
        job_run = JobRun(job_name=job_name, status="running", details_json=details or {})
        session.add(job_run)
        await session.commit()
        await session.refresh(job_run)
        job_run_id = int(job_run.id)

    asyncio.create_task(_run_background_job(session_factory, job_run_id, job))
    return _job_run_payload(job_run)


async def _run_background_job(
    session_factory: Callable[[], AsyncSession],
    job_run_id: int,
    job: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
) -> None:
    async with session_factory() as session:
        try:
            result = await job(session)
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            job_run = await session.get(JobRun, job_run_id)
            if job_run is not None:
                job_run.status = "failed"
                job_run.error_message = str(exc)
                job_run.finished_at = utcnow()
                await session.commit()
            return

        job_run = await session.get(JobRun, job_run_id)
        if job_run is not None:
            await _finish_job_run(job_run, result)
            await session.commit()
