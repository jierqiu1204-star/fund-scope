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


@pytest.mark.asyncio
async def test_due_aware_scheduler_skips_before_creating_tracked_run(app) -> None:
    job_called = False

    async def fake_job(_session: AsyncSession) -> dict[str, object]:
        nonlocal job_called
        job_called = True
        return {"ok": True}

    async def not_due(_session: AsyncSession) -> dict[str, object]:
        return {"due": False, "reason": "catch_up_cadence_not_due"}

    result = await scheduler_module._run_due_tracked_job(
        app.state.db,
        "scheduler_not_due_probe",
        fake_job,
        not_due,
    )

    async with app.state.db.session() as session:
        job_run = await session.scalar(
            select(JobRun).where(JobRun.job_name == "scheduler_not_due_probe")
        )

    assert result["status"] == "skipped"
    assert result["reason"] == "catch_up_cadence_not_due"
    assert job_called is False
    assert job_run is None


@pytest.mark.asyncio
async def test_due_aware_scheduler_creates_exactly_one_run_when_due(app) -> None:
    async def fake_job(_session: AsyncSession) -> dict[str, object]:
        return {"ok": True}

    async def due(_session: AsyncSession) -> dict[str, object]:
        return {"due": True, "reason": "publication_readiness_due"}

    result = await scheduler_module._run_due_tracked_job(
        app.state.db,
        "scheduler_due_probe",
        fake_job,
        due,
    )

    async with app.state.db.session() as session:
        runs = (
            await session.scalars(
                select(JobRun).where(JobRun.job_name == "scheduler_due_probe")
            )
        ).all()

    assert result == {"ok": True}
    assert len(runs) == 1


def test_scheduler_uses_configured_timezone() -> None:
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")

    assert scheduler.timezone.key == "Asia/Shanghai"


def test_morning_leader_confirmation_has_one_bounded_checkpoint(app) -> None:
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")
    settings = app.state.settings.model_copy(
        update={"etf_leader_tactics_v2_morning_confirmation_enabled": True}
    )

    scheduler_module.register_default_jobs(scheduler, app.state.db, settings)

    job = scheduler.get_job("dual_universe_leader_tactics_v2_morning_confirmation")
    assert job is not None
    fields = {field.name: str(field) for field in job.trigger.fields}
    assert fields["day_of_week"] == "mon-fri"
    assert fields["hour"] == "10"
    assert fields["minute"] == "42"
    assert fields["second"] == "0"
    assert job.max_instances == 1
    assert job.coalesce is True


def test_etf_leader_materialization_retries_bounded_persisted_reads(app) -> None:
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")
    settings = app.state.settings.model_copy(
        update={"etf_leader_tactics_v2_etf_materialize_enabled": True}
    )

    scheduler_module.register_default_jobs(scheduler, app.state.db, settings)

    job = scheduler.get_job("dual_universe_leader_tactics_v2_materialize_etf")
    assert job is not None
    fields = {field.name: str(field) for field in job.trigger.fields}
    assert fields["day_of_week"] == "tue-sat"
    assert fields["hour"] == "9-10"
    assert fields["minute"] == "10,30,50"
    assert job.max_instances == 1
    assert job.coalesce is True


def test_scheduler_uses_unified_short_research_jobs(app) -> None:
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")

    scheduler_module.register_default_jobs(scheduler, app.state.db, app.state.settings)

    job_ids = {job.id for job in scheduler.get_jobs()}
    intraday_11 = scheduler.get_job("intraday_etf_watch_11")
    daily_etf_universe = scheduler.get_job("daily_etf_universe")
    post_close_etf_data = scheduler.get_job("post_close_etf_data")
    daily_etf_theme_catalyst = scheduler.get_job("daily_etf_theme_catalyst")
    post_close_etf_signals = scheduler.get_job("post_close_etf_signals")
    post_close_etf_adjusted_sync = scheduler.get_job("post_close_etf_adjusted_sync")
    production_etf_pit_capture = scheduler.get_job("production_etf_pit_capture")
    research_history = scheduler.get_job(
        "post_publication_etf_research_history"
    )
    taxonomy_facts = scheduler.get_job("etf_taxonomy_fact_ingestion")
    daily_short_research_data = scheduler.get_job("daily_short_research_data")
    post_close_etf_label_review = scheduler.get_job("post_close_etf_label_outcome_review")
    post_close_etf_observation = scheduler.get_job("post_close_etf_observation_portfolio")
    etf_exit_signal_credibility = scheduler.get_job("etf_exit_signal_credibility")
    etf_exit_hyperopt = scheduler.get_job("etf_exit_hyperopt")
    intraday_etf_cleanup = scheduler.get_job("intraday_etf_cleanup")
    database_statistics = scheduler.get_job("database_statistics_maintenance")

    def trigger_field(job: object, name: str) -> str:
        return str(next(field for field in job.trigger.fields if field.name == name))

    assert "daily_short_research_data" in job_ids
    assert "daily_short_research_signals" in job_ids
    assert "daily_short_research_advisor" in job_ids
    assert "daily_etf_universe" in job_ids
    assert "daily_etf_theme_catalyst" in job_ids
    assert "post_close_etf_data" in job_ids
    assert "post_close_etf_signals" in job_ids
    assert "post_close_etf_adjusted_sync" in job_ids
    assert "production_etf_pit_capture" in job_ids
    assert "post_publication_etf_research_history" in job_ids
    assert "etf_taxonomy_fact_ingestion" in job_ids
    assert "post_close_etf_label_outcome_review" in job_ids
    assert "post_close_etf_observation_portfolio" in job_ids
    assert "intraday_etf_watch_0930" in job_ids
    assert "intraday_etf_watch_10" in job_ids
    assert "intraday_etf_watch_11" in job_ids
    assert "intraday_etf_watch_13_14" in job_ids
    assert "etf_exit_signal_credibility" in job_ids
    assert "etf_exit_hyperopt" in job_ids
    assert "intraday_etf_cleanup" in job_ids
    assert "database_statistics_maintenance" in job_ids
    assert "intraday_etf_watch_1500" not in job_ids
    assert intraday_11 is not None
    assert daily_etf_universe is not None
    assert post_close_etf_data is not None
    assert daily_etf_theme_catalyst is not None
    assert post_close_etf_signals is not None
    assert post_close_etf_adjusted_sync is not None
    assert production_etf_pit_capture is not None
    assert research_history is not None
    assert taxonomy_facts is not None
    assert daily_short_research_data is not None
    assert post_close_etf_label_review is not None
    assert post_close_etf_observation is not None
    assert etf_exit_signal_credibility is not None
    assert etf_exit_hyperopt is not None
    assert intraday_etf_cleanup is not None
    assert database_statistics is not None
    assert trigger_field(intraday_11, "minute") == "0-29"
    assert trigger_field(daily_etf_universe, "hour") == "15"
    assert trigger_field(daily_etf_universe, "minute") == "0"
    assert trigger_field(post_close_etf_data, "hour") == "15"
    assert trigger_field(post_close_etf_data, "minute") == "5"
    assert trigger_field(daily_etf_theme_catalyst, "hour") == "15"
    assert trigger_field(daily_etf_theme_catalyst, "minute") == "9"
    assert trigger_field(post_close_etf_signals, "hour") == "15"
    assert trigger_field(post_close_etf_signals, "minute") == "10"
    assert trigger_field(post_close_etf_adjusted_sync, "hour") == "15-22"
    assert trigger_field(post_close_etf_adjusted_sync, "minute") == "*"
    assert trigger_field(post_close_etf_adjusted_sync, "second") == "0,30"
    assert post_close_etf_adjusted_sync.max_instances == 1
    assert post_close_etf_adjusted_sync.coalesce is True
    assert trigger_field(production_etf_pit_capture, "hour") == "15-22"
    assert trigger_field(production_etf_pit_capture, "minute") == "*/2"
    assert production_etf_pit_capture.max_instances == 1
    assert production_etf_pit_capture.coalesce is True
    assert trigger_field(research_history, "day_of_week") == "mon-fri"
    assert trigger_field(research_history, "hour") == "23"
    assert trigger_field(research_history, "minute") == "0-28/2"
    assert research_history.max_instances == 1
    assert research_history.coalesce is True
    assert trigger_field(taxonomy_facts, "hour") == "15-22"
    assert trigger_field(taxonomy_facts, "minute") == "*/2"
    assert taxonomy_facts.max_instances == 1
    assert taxonomy_facts.coalesce is True
    assert daily_short_research_data.args[2] is scheduler_module.daily_short_research_fund_data_job
    assert trigger_field(post_close_etf_label_review, "hour") == "15"
    assert trigger_field(post_close_etf_label_review, "minute") == "11"
    assert trigger_field(post_close_etf_observation, "hour") == "15"
    assert trigger_field(post_close_etf_observation, "minute") == "12"
    assert trigger_field(etf_exit_signal_credibility, "hour") == "23"
    assert trigger_field(etf_exit_signal_credibility, "minute") == "30"
    assert trigger_field(etf_exit_hyperopt, "hour") == "23"
    assert trigger_field(etf_exit_hyperopt, "minute") == "45"
    assert trigger_field(intraday_etf_cleanup, "hour") == "1-6"
    assert trigger_field(intraday_etf_cleanup, "minute") == "20"
    assert trigger_field(database_statistics, "hour") == "4"
    assert trigger_field(database_statistics, "minute") == "45"
    assert database_statistics.max_instances == 1
    assert database_statistics.coalesce is True
    assert "daily_short_etf_data" not in job_ids
    assert "daily_short_etf_signals" not in job_ids
    assert "daily_short_etf_paper" not in job_ids
