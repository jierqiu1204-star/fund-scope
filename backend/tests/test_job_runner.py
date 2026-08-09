from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.entities import JobRun
from app.services.job_runner import (
    INTERRUPTED_BY_RESTART,
    reconcile_interrupted_job_runs,
    run_job,
    start_background_job,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_status", "expected_error"),
    [
        ({"job_status": "skipped", "job_message": "daily history deferred"}, "skipped", None),
        (
            {"job_status": "partial", "job_message": "provider returned partial data"},
            "partial",
            None,
        ),
        (
            {"job_status": "failed", "job_message": "coverage below threshold"},
            "failed",
            "coverage below threshold",
        ),
        ({"rows_inserted": 1}, "success", None),
    ],
)
async def test_run_job_persists_business_result_status(
    app, result, expected_status, expected_error
) -> None:
    async def job(_session):
        return result

    returned = await run_job(app.state.db.session, "business_status_probe", job)

    async with app.state.db.session() as session:
        job_run = await session.scalar(
            select(JobRun)
            .where(JobRun.job_name == "business_status_probe")
            .order_by(JobRun.id.desc())
        )

    assert returned == result
    assert job_run is not None
    assert job_run.status == expected_status
    assert job_run.error_message == expected_error
    assert job_run.details_json == result


@pytest.mark.asyncio
async def test_run_job_rolls_back_partial_business_writes_before_recording_failure(app) -> None:
    async def job(session):
        session.add(JobRun(job_name="uncommitted_business_write", status="running"))
        await session.flush()
        raise RuntimeError("business write failed")

    with pytest.raises(RuntimeError, match="business write failed"):
        await run_job(app.state.db.session, "rollback_probe", job)

    async with app.state.db.session() as session:
        partial = await session.scalar(
            select(JobRun).where(JobRun.job_name == "uncommitted_business_write")
        )
        recorded = await session.scalar(select(JobRun).where(JobRun.job_name == "rollback_probe"))

    assert partial is None
    assert recorded is not None
    assert recorded.status == "failed"
    assert recorded.error_message == "business write failed"


@pytest.mark.asyncio
async def test_run_job_recovers_failed_flush_before_recording_failure(app) -> None:
    async def job(session):
        current = await session.scalar(
            select(JobRun).where(JobRun.job_name == "failed_flush_probe")
        )
        assert current is not None
        session.add(JobRun(id=current.id, job_name="duplicate_primary_key", status="running"))
        await session.flush()
        return {}

    with pytest.raises(IntegrityError):
        await run_job(app.state.db.session, "failed_flush_probe", job)

    async with app.state.db.session() as session:
        recorded = await session.scalar(
            select(JobRun).where(JobRun.job_name == "failed_flush_probe")
        )

    assert recorded is not None
    assert recorded.status == "failed"
    assert recorded.error_message


@pytest.mark.asyncio
async def test_startup_reconciles_only_unfinished_running_job_rows(app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                JobRun(job_name="stale", status="running"),
                JobRun(job_name="finished", status="success"),
                JobRun(job_name="unrelated-ranking", status="running"),
            ]
        )
        await session.commit()

    reconciled = await reconcile_interrupted_job_runs(
        app.state.db.session,
        job_names=("stale",),
    )

    async with app.state.db.session() as session:
        stale = await session.scalar(select(JobRun).where(JobRun.job_name == "stale"))
        finished = await session.scalar(select(JobRun).where(JobRun.job_name == "finished"))
        unrelated = await session.scalar(
            select(JobRun).where(JobRun.job_name == "unrelated-ranking")
        )

    assert reconciled == 1
    assert stale is not None
    assert stale.status == "failed"
    assert stale.finished_at is not None
    assert stale.error_message == INTERRUPTED_BY_RESTART
    assert finished is not None
    assert finished.status == "success"
    assert unrelated is not None
    assert unrelated.status == "running"


@pytest.mark.asyncio
async def test_background_job_persists_business_result_status(app) -> None:
    async def job(_session):
        return {"job_status": "partial", "job_message": "provider returned partial data"}

    payload = await start_background_job(app.state.db.session, "background_status_probe", job)

    assert payload["status"] == "running"
    job_run = None
    for _ in range(20):
        async with app.state.db.session() as session:
            job_run = await session.scalar(
                select(JobRun).where(JobRun.job_name == "background_status_probe")
            )
        if job_run is not None and job_run.status != "running":
            break
        await asyncio.sleep(0.01)

    assert job_run is not None
    assert job_run.status == "partial"
