from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import JobRun
from app.services import scheduler as scheduler_module


@pytest.mark.asyncio
async def test_scheduler_tracked_job_awaits_and_records_result(app) -> None:
    async def fake_job(session: AsyncSession) -> dict[str, object]:
        await session.execute(select(JobRun.id).limit(1))
        return {"ok": True}

    result = await scheduler_module._run_tracked_job(app.state.db, "scheduler_probe", fake_job)

    async with app.state.db.session() as session:
        job_run = await session.scalar(select(JobRun).where(JobRun.job_name == "scheduler_probe"))

    assert result == {"ok": True}
    assert job_run is not None
    assert job_run.status == "success"
    assert job_run.details_json == {"ok": True}


def test_scheduler_uses_configured_timezone() -> None:
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")

    assert scheduler.timezone.key == "Asia/Shanghai"


def test_scheduler_uses_unified_short_research_jobs(app) -> None:
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")

    scheduler_module.register_default_jobs(scheduler, app.state.db, app.state.settings)

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "daily_short_research_data" in job_ids
    assert "daily_short_research_signals" in job_ids
    assert "daily_short_research_advisor" in job_ids
    assert "intraday_etf_watch_0930" in job_ids
    assert "intraday_etf_watch_10" in job_ids
    assert "intraday_etf_watch_11" in job_ids
    assert "intraday_etf_watch_13_14" in job_ids
    assert "intraday_etf_watch_1500" in job_ids
    assert "daily_short_etf_data" not in job_ids
    assert "daily_short_etf_signals" not in job_ids
    assert "daily_short_etf_paper" not in job_ids
