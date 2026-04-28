from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.config import dictConfig

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.admin import router as admin_router
from app.api.routes.health import router as health_router
from app.api.routes.news import router as news_router
from app.api.routes.onboarding import router as onboarding_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.recommendations import router as recommendations_router
from app.api.routes.settings import router as settings_router
from app.api.routes.transactions import router as transactions_router
from app.api.routes.valuation import router as valuation_router
from app.core.config import Settings, get_settings
from app.core.db import DatabaseManager
from app.services.scheduler import build_scheduler, register_default_jobs


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
        if start_scheduler:
            application.state.scheduler.start()
        yield
        if start_scheduler:
            application.state.scheduler.shutdown(wait=False)

    app = FastAPI(title="FundScope API", lifespan=lifespan)
    app.state.db = DatabaseManager(app_settings.database_url)
    app.state.settings = app_settings
    app.state.scheduler = build_scheduler()
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
    app.include_router(news_router)
    app.include_router(settings_router)
    app.include_router(onboarding_router)
    app.include_router(admin_router)
    return app


app = create_app()
