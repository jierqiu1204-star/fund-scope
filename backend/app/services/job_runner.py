from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import JobRun, utcnow


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
            job_run.status = "success"
            job_run.details_json = result
            job_run.finished_at = utcnow()
            await session.commit()
            return result
        except Exception as exc:  # noqa: BLE001
            job_run.status = "failed"
            job_run.error_message = str(exc)
            job_run.finished_at = utcnow()
            await session.commit()
            raise
