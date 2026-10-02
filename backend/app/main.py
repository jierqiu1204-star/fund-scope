from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.config import dictConfig

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.admin import router as admin_router
from app.api.routes.admin_data import router as admin_data_router
from app.api.routes.dual_universe_leader_tactics_v2 import router as leader_tactics_v2_router
from app.api.routes.etf_quotes import router as etf_quotes_router
from app.api.routes.health import router as health_router
from app.api.routes.late_day_turnaround import router as late_day_turnaround_router
from app.api.routes.news import router as news_router
from app.api.routes.onboarding import router as onboarding_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.recommendations import router as recommendations_router
from app.api.routes.settings import router as settings_router
from app.api.routes.short_etf import router as short_etf_router
from app.api.routes.short_research import router as short_research_router
from app.api.routes.strategy_lab import router as strategy_lab_router
from app.api.routes.tracked_positions import router as tracked_positions_router
from app.api.routes.transactions import router as transactions_router
from app.api.routes.valuation import router as valuation_router
from app.core.config import Settings, get_settings
from app.core.db import DatabaseManager
from app.core.instance import resolve_instance_owner
from app.services.job_runner import reconcile_interrupted_job_runs
from app.services.scheduler import build_scheduler, register_default_jobs

logger = logging.getLogger(__name__)

_LEADER_TACTICS_JOB_NAMES = (
    "dual_universe_leader_tactics_v2_capture",
    "dual_universe_leader_tactics_v2_materialize",
    "dual_universe_leader_tactics_v2_materialize_etf",
    "etf_leader_tactics_shadow_continue",
)


def _configure_logging() -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                }
            },
            "root": {"handlers": ["console"], "level": "INFO"},
        }
    )


def create_app(settings: Settings | None = None, *, start_scheduler: bool = True) -> FastAPI:
    _configure_logging()
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            async with application.state.db.session() as session:
                await resolve_instance_owner(session, application.state.settings)
        except SQLAlchemyError:
            logger.warning(
                "实例设置表尚未就绪，请先运行 alembic upgrade head。"
            )
        try:
            reconciled = await reconcile_interrupted_job_runs(
                application.state.db.session,
                job_names=_LEADER_TACTICS_JOB_NAMES,
            )
            if reconciled:
                logger.warning("已关闭 %s 条因进程重启中断的任务审计记录。", reconciled)
        except SQLAlchemyError:
            logger.warning("任务审计表暂不可用，已跳过重启中断记录清理。")
        if start_scheduler:
            application.state.scheduler.start()
        yield
        if start_scheduler:
            application.state.scheduler.shutdown(wait=False)

    app = FastAPI(title="FundScope API", lifespan=lifespan)
    app.state.db = DatabaseManager(app_settings.database_url)
    app.state.settings = app_settings
    app.state.scheduler = build_scheduler(app_settings.scheduler_timezone)
    register_default_jobs(app.state.scheduler, app.state.db, app_settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(transactions_router)
    app.include_router(portfolio_router)
    app.include_router(valuation_router)
    app.include_router(recommendations_router)
    app.include_router(strategy_lab_router)
    app.include_router(short_etf_router)
    app.include_router(short_research_router)
    app.include_router(late_day_turnaround_router)
    app.include_router(leader_tactics_v2_router)
    app.include_router(etf_quotes_router)
    app.include_router(tracked_positions_router)
    app.include_router(news_router)
    app.include_router(settings_router)
    app.include_router(onboarding_router)
    app.include_router(admin_data_router)
    app.include_router(admin_router)
    return app


app = create_app()
