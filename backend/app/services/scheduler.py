from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import DatabaseManager
from app.services.intraday_etf.jobs import intraday_etf_watch_job
from app.services.job_runner import run_job
from app.services.jobs import (
    daily_asset_recommendations_job,
    daily_fund_nav_job,
    daily_holdings_snapshot_job,
    daily_news_fetch_job,
    daily_recommendation_metrics_job,
    daily_valuation_job,
    monthly_dca_reminder_job,
)
from app.services.llm import LLMClient
from app.services.short_research.jobs import (
    daily_short_research_advisor_job,
    daily_short_research_data_job,
    daily_short_research_signals_job,
)
from app.services.strategy_lab.jobs import daily_strategy_paper_job
from app.services.tracked_positions.jobs import daily_tracked_position_alerts_job


async def _run_tracked_job(
    db: DatabaseManager,
    job_name: str,
    job: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    return await run_job(db.session, job_name, job)


def build_scheduler(timezone: str = "Asia/Shanghai") -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone=ZoneInfo(timezone))


def register_default_jobs(
    scheduler: AsyncIOScheduler,
    db: DatabaseManager,
    settings: Settings,
) -> None:
    llm_client = LLMClient(settings)

    async def daily_news_fetch_tracked(session: AsyncSession) -> dict[str, Any]:
        return await daily_news_fetch_job(session, llm_client)

    async def daily_short_research_advisor_tracked(session: AsyncSession) -> dict[str, Any]:
        return await daily_short_research_advisor_job(session, settings, llm_client)

    async def monthly_dca_reminder_tracked(session: AsyncSession) -> dict[str, Any]:
        return await monthly_dca_reminder_job(session, settings)

    async def daily_tracked_position_alerts_tracked(session: AsyncSession) -> dict[str, Any]:
        return await daily_tracked_position_alerts_job(session, settings)

    async def intraday_etf_watch_tracked(session: AsyncSession) -> dict[str, Any]:
        return await intraday_etf_watch_job(session, settings=settings, run_type="scheduled")

    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_fund_nav", daily_fund_nav_job],
        hour=19,
        minute=0,
        id="daily_fund_nav",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_valuation", daily_valuation_job],
        hour=19,
        minute=15,
        id="daily_valuation",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_holdings_snapshot", daily_holdings_snapshot_job],
        hour=19,
        minute=20,
        id="daily_holdings_snapshot",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_news_fetch", daily_news_fetch_tracked],
        hour=19,
        minute=30,
        id="daily_news_fetch",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "monthly_dca_reminder", monthly_dca_reminder_tracked],
        day=1,
        hour=9,
        minute=0,
        id="monthly_dca_reminder",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_recommendation_metrics", daily_recommendation_metrics_job],
        hour=19,
        minute=45,
        id="daily_recommendation_metrics",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_asset_recommendations", daily_asset_recommendations_job],
        hour=20,
        minute=0,
        id="daily_asset_recommendations",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_strategy_paper", daily_strategy_paper_job],
        hour=20,
        minute=15,
        id="daily_strategy_paper",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_short_research_data", daily_short_research_data_job],
        hour=21,
        minute=25,
        id="daily_short_research_data",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_short_research_signals", daily_short_research_signals_job],
        hour=21,
        minute=40,
        id="daily_short_research_signals",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_short_research_advisor", daily_short_research_advisor_tracked],
        hour=21,
        minute=50,
        id="daily_short_research_advisor",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_tracked_position_alerts", daily_tracked_position_alerts_tracked],
        hour=22,
        minute=0,
        id="daily_tracked_position_alerts",
        replace_existing=True,
    )
    intraday_windows = [
        ("intraday_etf_watch_0930", 9, "30-59"),
        ("intraday_etf_watch_10", 10, "*"),
        ("intraday_etf_watch_11", 11, "0-30"),
        ("intraday_etf_watch_13_14", "13-14", "*"),
        ("intraday_etf_watch_1500", 15, "0"),
    ]
    for job_id, hour, minute in intraday_windows:
        scheduler.add_job(
            _run_tracked_job,
            "cron",
            args=[db, "intraday_etf_watch", intraday_etf_watch_tracked],
            day_of_week="mon-fri",
            hour=hour,
            minute=minute,
            second=0,
            id=job_id,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
