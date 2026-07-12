from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import JobRun, utcnow

_BUSINESS_JOB_STATUSES = {"skipped", "partial", "failed"}


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


async def run_job(
    session_factory: Callable[[], AsyncSession],
    job_name: str,
    job: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with session_factory() as session:
        job_run = JobRun(job_name=job_name, status="running")
        session.add(job_run)
        await session.commit()
        try:
            result = await job(session)
            await _finish_job_run(job_run, result)
            await session.commit()
            return result
        except Exception as exc:  # noqa: BLE001
            job_run.status = "failed"
            job_run.error_message = str(exc)
            job_run.finished_at = utcnow()
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
