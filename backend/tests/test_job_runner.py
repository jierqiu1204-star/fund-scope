from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.models.entities import JobRun
from app.services.job_runner import run_job, start_background_job


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_status", "expected_error"),
    [
        ({"job_status": "skipped", "job_message": "daily history deferred"}, "skipped", None),
        ({"job_status": "partial", "job_message": "provider returned partial data"}, "partial", None),
        ({"job_status": "failed", "job_message": "coverage below threshold"}, "failed", "coverage below threshold"),
        ({"rows_inserted": 1}, "success", None),
    ],
)
async def test_run_job_persists_business_result_status(app, result, expected_status, expected_error) -> None:
    async def job(_session):
        return result

    returned = await run_job(app.state.db.session, "business_status_probe", job)

    async with app.state.db.session() as session:
        job_run = await session.scalar(
            select(JobRun).where(JobRun.job_name == "business_status_probe").order_by(JobRun.id.desc())
        )

    assert returned == result
    assert job_run is not None
    assert job_run.status == expected_status
    assert job_run.error_message == expected_error
    assert job_run.details_json == result


@pytest.mark.asyncio
async def test_background_job_persists_business_result_status(app) -> None:
    async def job(_session):
        return {"job_status": "partial", "job_message": "provider returned partial data"}

    payload = await start_background_job(app.state.db.session, "background_status_probe", job)

    assert payload["status"] == "running"
    job_run = None
    for _ in range(20):
        async with app.state.db.session() as session:
            job_run = await session.scalar(select(JobRun).where(JobRun.job_name == "background_status_probe"))
        if job_run is not None and job_run.status != "running":
            break
        await asyncio.sleep(0.01)

    assert job_run is not None
    assert job_run.status == "partial"
