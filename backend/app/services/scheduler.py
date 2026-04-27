from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import Settings
from app.core.db import DatabaseManager
from app.services.job_runner import run_job
from app.services.jobs import (
    daily_fund_nav_job,
    daily_holdings_snapshot_job,
    daily_news_fetch_job,
    daily_valuation_job,
    monthly_dca_reminder_job,
)
from app.services.llm import LLMClient


def build_scheduler() -> AsyncIOScheduler:
    return AsyncIOScheduler()


def register_default_jobs(
    scheduler: AsyncIOScheduler,
    db: DatabaseManager,
    settings: Settings,
) -> None:
    llm_client = LLMClient(settings)

    scheduler.add_job(
        lambda: run_job(db.session, "daily_fund_nav", daily_fund_nav_job),
        "cron",
        hour=19,
        minute=0,
        id="daily_fund_nav",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: run_job(db.session, "daily_valuation", daily_valuation_job),
        "cron",
        hour=19,
        minute=15,
        id="daily_valuation",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: run_job(db.session, "daily_holdings_snapshot", daily_holdings_snapshot_job),
        "cron",
        hour=19,
        minute=20,
        id="daily_holdings_snapshot",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: run_job(
            db.session,
            "daily_news_fetch",
            lambda session: daily_news_fetch_job(session, llm_client),
        ),
        "cron",
        hour=19,
        minute=30,
        id="daily_news_fetch",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: run_job(
            db.session,
            "monthly_dca_reminder",
            lambda session: monthly_dca_reminder_job(session, settings),
        ),
        "cron",
        day=1,
        hour=9,
        minute=0,
        id="monthly_dca_reminder",
        replace_existing=True,
    )
