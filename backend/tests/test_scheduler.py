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
    intraday_11 = scheduler.get_job("intraday_etf_watch_11")
    post_close_etf_data = scheduler.get_job("post_close_etf_data")
    post_close_etf_signals = scheduler.get_job("post_close_etf_signals")
    post_close_etf_observation = scheduler.get_job("post_close_etf_observation_portfolio")

    def trigger_field(job: object, name: str) -> str:
        return str(next(field for field in job.trigger.fields if field.name == name))

    assert "daily_short_research_data" in job_ids
    assert "daily_short_research_signals" in job_ids
    assert "daily_short_research_advisor" in job_ids
    assert "post_close_etf_data" in job_ids
    assert "post_close_etf_signals" in job_ids
    assert "post_close_etf_observation_portfolio" in job_ids
    assert "intraday_etf_watch_0930" in job_ids
    assert "intraday_etf_watch_10" in job_ids
    assert "intraday_etf_watch_11" in job_ids
    assert "intraday_etf_watch_13_14" in job_ids
    assert "intraday_etf_watch_1500" not in job_ids
    assert intraday_11 is not None
    assert post_close_etf_data is not None
    assert post_close_etf_signals is not None
    assert post_close_etf_observation is not None
    assert trigger_field(intraday_11, "minute") == "0-29"
    assert trigger_field(post_close_etf_data, "hour") == "15"
    assert trigger_field(post_close_etf_data, "minute") == "5"
    assert trigger_field(post_close_etf_signals, "hour") == "15"
    assert trigger_field(post_close_etf_signals, "minute") == "10"
    assert trigger_field(post_close_etf_observation, "hour") == "15"
    assert trigger_field(post_close_etf_observation, "minute") == "12"
    assert "daily_short_etf_data" not in job_ids
    assert "daily_short_etf_signals" not in job_ids
    assert "daily_short_etf_paper" not in job_ids
