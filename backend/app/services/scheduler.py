from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any, cast
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import DatabaseManager
from app.services.intraday_etf.jobs import intraday_etf_cleanup_job
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
    daily_etf_observation_portfolio_job,
    daily_etf_signal_validation_job,
    daily_etf_taxonomy_job,
    daily_etf_theme_catalyst_job,
    daily_etf_universe_job,
    daily_short_research_advisor_job,
    daily_short_research_fund_data_job,
    daily_short_research_signals_job,
    etf_exit_hyperopt_job,
    etf_exit_signal_credibility_job,
    post_close_etf_adjusted_sync_job,
    post_close_etf_data_job,
    post_close_etf_label_outcome_review_job,
    post_close_etf_observation_portfolio_job,
    post_close_etf_signals_job,
    post_publication_etf_research_history_job,
    production_etf_pit_capture_job,
    publication_readiness_decision_context,
)
from app.services.strategy_lab.jobs import daily_strategy_paper_job
from app.services.tracked_positions.jobs import daily_tracked_position_alerts_job
from app.services.workflows.etf_leader_tactics_shadow import (
    LEADER_CONTINUATION_JOB_NAME,
    continue_etf_leader_tactics_shadow_job,
)
from app.services.workflows.etf_point_in_time_capture import (
    preflight_production_pit_capture,
)
from app.services.workflows.etf_publish_readiness import (
    preflight_post_close_etf_publication_readiness,
)
from app.services.workflows.intraday_etf import intraday_etf_watch_with_alerts_job

V2_JOB_MODULE = "app.services.workflows.dual_universe_leader_tactics_v2_jobs"
V2_CAPTURE_JOB_NAME = "dual_universe_leader_tactics_v2_capture"
V2_MATERIALIZE_JOB_NAME = "dual_universe_leader_tactics_v2_materialize"
V2_ETF_MATERIALIZE_JOB_NAME = "dual_universe_leader_tactics_v2_materialize_etf"
V2_CAPTURE_JOB_ATTRIBUTE = "dual_universe_leader_tactics_v2_capture_job"
V2_MATERIALIZE_JOB_ATTRIBUTE = "dual_universe_leader_tactics_v2_materialize_job"
V2_ETF_MATERIALIZE_JOB_ATTRIBUTE = "dual_universe_leader_tactics_v2_etf_materialize_job"


V2SchedulerJob = Callable[[AsyncSession, Settings], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class _V2SchedulerJobContract:
    """The narrow scheduler contract supplied by the V2 workflow jobs module.

    The provider and materialization coordinator deliberately do not live in the
    scheduler.  Keeping this import lazy lets the default-off application start
    without a V2 jobs module, while an accidentally enabled flag fails closed
    with an actionable configuration error.
    """

    capture_name: str
    materialize_name: str
    etf_materialize_name: str
    capture_job: V2SchedulerJob
    materialize_job: V2SchedulerJob
    etf_materialize_job: V2SchedulerJob


def _load_v2_scheduler_job_contract() -> _V2SchedulerJobContract:
    try:
        module = import_module(V2_JOB_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != V2_JOB_MODULE:
            raise
        raise RuntimeError(
            f"V2 scheduler is enabled but the workflow jobs module is unavailable: {V2_JOB_MODULE}"
        ) from exc

    capture_job = getattr(module, V2_CAPTURE_JOB_ATTRIBUTE, None)
    materialize_job = getattr(module, V2_MATERIALIZE_JOB_ATTRIBUTE, None)
    etf_materialize_job = getattr(module, V2_ETF_MATERIALIZE_JOB_ATTRIBUTE, None)
    if not all(callable(job) for job in (capture_job, materialize_job, etf_materialize_job)):
        raise RuntimeError(
            "V2 workflow jobs module must expose callable attributes "
            f"{V2_CAPTURE_JOB_ATTRIBUTE}, {V2_MATERIALIZE_JOB_ATTRIBUTE}, and "
            f"{V2_ETF_MATERIALIZE_JOB_ATTRIBUTE}"
        )

    capture_name = getattr(module, "V2_CAPTURE_JOB_NAME", V2_CAPTURE_JOB_NAME)
    materialize_name = getattr(module, "V2_MATERIALIZE_JOB_NAME", V2_MATERIALIZE_JOB_NAME)
    etf_materialize_name = getattr(
        module,
        "V2_ETF_MATERIALIZE_JOB_NAME",
        V2_ETF_MATERIALIZE_JOB_NAME,
    )
    if not isinstance(capture_name, str) or not capture_name.strip():
        raise RuntimeError("V2 capture job name must be a non-empty string")
    if not isinstance(materialize_name, str) or not materialize_name.strip():
        raise RuntimeError("V2 materialize job name must be a non-empty string")
    if not isinstance(etf_materialize_name, str) or not etf_materialize_name.strip():
        raise RuntimeError("V2 ETF materialize job name must be a non-empty string")

    return _V2SchedulerJobContract(
        capture_name=capture_name.strip(),
        materialize_name=materialize_name.strip(),
        etf_materialize_name=etf_materialize_name.strip(),
        capture_job=cast(V2SchedulerJob, capture_job),
        materialize_job=cast(V2SchedulerJob, materialize_job),
        etf_materialize_job=cast(V2SchedulerJob, etf_materialize_job),
    )


async def _run_tracked_job(
    db: DatabaseManager,
    job_name: str,
    job: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    return await run_job(db.session, job_name, job)


async def _run_due_tracked_job(
    db: DatabaseManager,
    job_name: str,
    job: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
    preflight: Callable[[AsyncSession], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with db.session() as session:
        decision = await preflight(session)
    if decision.get("due") is not True:
        return {
            "status": "skipped",
            "job_name": job_name,
            **decision,
        }
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
        return await intraday_etf_watch_with_alerts_job(
            session, settings=settings, run_type="scheduled"
        )

    async def post_close_etf_adjusted_sync_preflight(
        session: AsyncSession,
    ) -> dict[str, Any]:
        context = publication_readiness_decision_context()
        if context is None:
            return {
                "due": False,
                "reason": "no_completed_trading_session",
            }
        trade_date, decision_cutoff = context
        decision = await preflight_post_close_etf_publication_readiness(
            session,
            trade_date=trade_date,
            decision_cutoff=decision_cutoff,
        )
        return decision.to_dict()

    async def production_etf_pit_capture_tracked(
        session: AsyncSession,
    ) -> dict[str, Any]:
        return await production_etf_pit_capture_job(session, settings)

    async def production_etf_pit_capture_preflight(
        session: AsyncSession,
    ) -> dict[str, Any]:
        context = publication_readiness_decision_context()
        if context is None:
            return {
                "due": False,
                "reason": "pit_no_completed_trading_session",
            }
        trade_date, _decision_cutoff = context
        code_version = (
            settings.etf_pit_code_version.strip() or settings.readiness_deploy_artifact.strip()
        )
        decision = await preflight_production_pit_capture(
            session,
            trade_date=trade_date,
            enabled=settings.etf_pit_capture_enabled,
            code_version=code_version,
        )
        return decision.to_dict()

    async def etf_leader_tactics_observation_tracked(
        session: AsyncSession,
    ) -> dict[str, Any]:
        return await continue_etf_leader_tactics_shadow_job(
            session,
            settings=settings,
            timeout_seconds=50.0,
        )

    if (
        settings.etf_leader_tactics_v2_capture_enabled
        or settings.etf_leader_tactics_v2_materialize_enabled
    ):
        v2_jobs = _load_v2_scheduler_job_contract()

        async def dual_universe_v2_capture_tracked(
            session: AsyncSession,
        ) -> dict[str, Any]:
            return await v2_jobs.capture_job(session, settings)

        async def dual_universe_v2_materialize_tracked(
            session: AsyncSession,
        ) -> dict[str, Any]:
            return await v2_jobs.materialize_job(session, settings)

        async def dual_universe_v2_etf_materialize_tracked(
            session: AsyncSession,
        ) -> dict[str, Any]:
            return await v2_jobs.etf_materialize_job(session, settings)

        if settings.etf_leader_tactics_v2_capture_enabled:
            scheduler.add_job(
                _run_tracked_job,
                "cron",
                args=[db, v2_jobs.capture_name, dual_universe_v2_capture_tracked],
                day_of_week="mon-fri",
                hour="21-23",
                minute="*/2",
                second=0,
                id=v2_jobs.capture_name,
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
        if settings.etf_leader_tactics_v2_materialize_enabled:
            scheduler.add_job(
                _run_tracked_job,
                "cron",
                args=[
                    db,
                    v2_jobs.etf_materialize_name,
                    dual_universe_v2_etf_materialize_tracked,
                ],
                day_of_week="tue-sat",
                hour=9,
                minute=10,
                second=0,
                id=v2_jobs.etf_materialize_name,
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            scheduler.add_job(
                _run_tracked_job,
                "cron",
                args=[db, v2_jobs.materialize_name, dual_universe_v2_materialize_tracked],
                day_of_week="tue-sat",
                hour=9,
                minute=12,
                second=0,
                id=v2_jobs.materialize_name,
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )

    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_fund_nav", daily_fund_nav_job],
        hour=19,
        minute=0,
        id="daily_fund_nav",
        replace_existing=True,
    )
    if settings.etf_leader_tactics_continuation_enabled:
        scheduler.add_job(
            _run_tracked_job,
            "cron",
            args=[
                db,
                LEADER_CONTINUATION_JOB_NAME,
                etf_leader_tactics_observation_tracked,
            ],
            day_of_week="tue-sat",
            hour="0-6",
            minute="*/2",
            id=LEADER_CONTINUATION_JOB_NAME,
            max_instances=1,
            coalesce=True,
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
        args=[db, "daily_etf_universe", daily_etf_universe_job],
        day_of_week="mon-fri",
        hour=15,
        minute=0,
        id="daily_etf_universe",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "post_close_etf_data", post_close_etf_data_job],
        day_of_week="mon-fri",
        hour=15,
        minute=5,
        id="post_close_etf_data",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_etf_taxonomy", daily_etf_taxonomy_job],
        day_of_week="mon-fri",
        hour=15,
        minute=8,
        id="daily_etf_taxonomy",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_etf_theme_catalyst", daily_etf_theme_catalyst_job],
        day_of_week="mon-fri",
        hour=15,
        minute=9,
        id="daily_etf_theme_catalyst",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "post_close_etf_signals", post_close_etf_signals_job],
        day_of_week="mon-fri",
        hour=15,
        minute=10,
        id="post_close_etf_signals",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "post_close_etf_label_outcome_review", post_close_etf_label_outcome_review_job],
        day_of_week="mon-fri",
        hour=15,
        minute=11,
        id="post_close_etf_label_outcome_review",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "post_close_etf_observation_portfolio", post_close_etf_observation_portfolio_job],
        day_of_week="mon-fri",
        hour=15,
        minute=12,
        id="post_close_etf_observation_portfolio",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "intraday_etf_cleanup", intraday_etf_cleanup_job],
        day_of_week="mon-fri",
        hour=15,
        minute=20,
        id="intraday_etf_cleanup",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_due_tracked_job,
        "cron",
        args=[
            db,
            "post_close_etf_adjusted_sync",
            post_close_etf_adjusted_sync_job,
            post_close_etf_adjusted_sync_preflight,
        ],
        day_of_week="mon-fri",
        hour="15-22",
        minute="*",
        id="post_close_etf_adjusted_sync",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_due_tracked_job,
        "cron",
        args=[
            db,
            "production_etf_pit_capture",
            production_etf_pit_capture_tracked,
            production_etf_pit_capture_preflight,
        ],
        day_of_week="mon-fri",
        hour="15-22",
        minute="*/2",
        id="production_etf_pit_capture",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_short_research_data", daily_short_research_fund_data_job],
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
        args=[db, "daily_etf_signal_validation", daily_etf_signal_validation_job],
        hour=21,
        minute=45,
        id="daily_etf_signal_validation",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "daily_etf_observation_portfolio", daily_etf_observation_portfolio_job],
        hour=21,
        minute=47,
        id="daily_etf_observation_portfolio",
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
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[
            db,
            "post_publication_etf_research_history",
            post_publication_etf_research_history_job,
        ],
        day_of_week="mon-fri",
        hour=23,
        minute="0-28/2",
        id="post_publication_etf_research_history",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "etf_exit_hyperopt", etf_exit_hyperopt_job],
        day_of_week="mon-fri",
        hour=23,
        minute=45,
        id="etf_exit_hyperopt",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        _run_tracked_job,
        "cron",
        args=[db, "etf_exit_signal_credibility", etf_exit_signal_credibility_job],
        day_of_week="mon-fri",
        hour=23,
        minute=30,
        id="etf_exit_signal_credibility",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    intraday_windows = [
        ("intraday_etf_watch_0930", 9, "30-59"),
        ("intraday_etf_watch_10", 10, "*"),
        ("intraday_etf_watch_11", 11, "0-29"),
        ("intraday_etf_watch_13_14", "13-14", "*"),
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
