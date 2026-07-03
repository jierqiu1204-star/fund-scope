from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import JobRun, NewsItem, NewsSummary
from app.services.intraday_etf.jobs import intraday_etf_cleanup_job
from app.services.job_runner import run_job
from app.services.jobs import (
    daily_asset_recommendations_job,
    daily_fund_nav_job,
    daily_holdings_snapshot_job,
    daily_news_fetch_job,
    daily_recommendation_metrics_job,
    daily_valuation_job,
    fund_nav_backfill_job,
    monthly_dca_reminder_job,
)
from app.services.llm import LLMClient
from app.services.news_summarizer import parse_summary_output
from app.services.short_etf.jobs import (
    daily_short_etf_data_job,
    daily_short_etf_paper_job,
    daily_short_etf_reliability_evaluation_job,
    daily_short_etf_retry_failed_data_job,
    daily_short_etf_signals_job,
)
from app.services.short_research.jobs import (
    daily_etf_label_outcome_review_job,
    daily_etf_observation_portfolio_job,
    daily_etf_signal_validation_job,
    daily_etf_taxonomy_job,
    daily_etf_universe_job,
    daily_short_research_advisor_job,
    daily_short_research_data_job,
    daily_short_research_signals_job,
    etf_exit_hyperopt_job,
    etf_history_backfill_job,
    etf_label_historical_replay_job,
    etf_optimized_allocation_job,
    etf_portfolio_backtest_job,
    etf_strategy_comparison_backtest_job,
    etf_strategy_healthcheck_job,
    post_close_etf_data_job,
    post_close_etf_label_outcome_review_job,
    post_close_etf_observation_portfolio_job,
    post_close_etf_signals_job,
)
from app.services.strategy_lab.jobs import daily_strategy_paper_job
from app.services.tracked_positions.jobs import daily_tracked_position_alerts_job
from app.services.workflows.intraday_etf import intraday_etf_watch_with_alerts_job

router = APIRouter(prefix="/api/admin/jobs", tags=["admin"])


async def _news_summary_backfill_job(session: AsyncSession, llm_client: LLMClient) -> dict[str, int]:
    items = (
        await session.scalars(
            select(NewsItem).where(~NewsItem.id.in_(select(NewsSummary.news_item_id)))
        )
    ).all()
    succeeded = 0
    failed = 0

    for item in items:
        try:
            parsed = parse_summary_output(await llm_client.summarize_news(item.title, item.raw_content))
            session.add(
                NewsSummary(
                    news_item_id=item.id,
                    summary=parsed.summary,
                    event_type=parsed.event_type,
                    model_name=llm_client.model_name,
                )
            )
            succeeded += 1
        except Exception:  # noqa: BLE001
            failed += 1
    await session.commit()
    return {"succeeded": succeeded, "failed": failed}


@router.post("/news_summary_backfill/run")
async def run_news_summary_backfill(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
    llm_client = LLMClient(request.app.state.settings)
    result = await run_job(
        request.app.state.db.session,
        "news_summary_backfill",
        lambda tracked_session: _news_summary_backfill_job(tracked_session, llm_client),
    )
    return {"succeeded": int(result["succeeded"]), "failed": int(result["failed"])}


@router.post("/monthly_dca_reminder/run")
async def run_monthly_dca_reminder(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    return await run_job(
        request.app.state.db.session,
        "monthly_dca_reminder",
        lambda tracked_session: monthly_dca_reminder_job(
            tracked_session,
            request.app.state.settings,
        ),
    )


@router.post("/fund_nav_backfill/run")
async def run_fund_nav_backfill(
    days: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    return await run_job(
        request.app.state.db.session,
        "fund_nav_backfill",
        lambda tracked_session: fund_nav_backfill_job(tracked_session, days),
    )


@router.get("")
async def list_job_runs(session: AsyncSession = Depends(get_db_session)) -> list[dict[str, object]]:
    rows = (
        await session.scalars(select(JobRun).order_by(JobRun.started_at.desc(), JobRun.id.desc()).limit(20))
    ).all()
    return [
        {
            "id": row.id,
            "job_name": row.job_name,
            "status": row.status,
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "error_message": row.error_message,
            "details": row.details_json,
        }
        for row in rows
    ]


@router.post("/{job_name}/run")
async def run_job_by_name(
    job_name: str,
    request: Request,
    days: int = Query(default=180, ge=30, le=1095),
    max_assets: int = Query(default=300, ge=1, le=2000),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    llm_client = LLMClient(request.app.state.settings)
    if job_name == "daily_fund_nav":
        return await run_job(request.app.state.db.session, job_name, daily_fund_nav_job)
    if job_name == "daily_valuation":
        return await run_job(request.app.state.db.session, job_name, daily_valuation_job)
    if job_name == "daily_holdings_snapshot":
        return await run_job(request.app.state.db.session, job_name, daily_holdings_snapshot_job)
    if job_name == "daily_news_fetch":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: daily_news_fetch_job(tracked_session, llm_client),
        )
    if job_name == "daily_recommendation_metrics":
        return await run_job(request.app.state.db.session, job_name, daily_recommendation_metrics_job)
    if job_name == "daily_asset_recommendations":
        return await run_job(request.app.state.db.session, job_name, daily_asset_recommendations_job)
    if job_name == "daily_strategy_paper":
        return await run_job(request.app.state.db.session, job_name, daily_strategy_paper_job)
    if job_name == "daily_short_etf_data":
        return await run_job(request.app.state.db.session, job_name, daily_short_etf_data_job)
    if job_name == "daily_short_etf_retry_failed_data":
        return await run_job(request.app.state.db.session, job_name, daily_short_etf_retry_failed_data_job)
    if job_name == "daily_short_etf_signals":
        return await run_job(request.app.state.db.session, job_name, daily_short_etf_signals_job)
    if job_name == "daily_short_etf_paper":
        return await run_job(request.app.state.db.session, job_name, daily_short_etf_paper_job)
    if job_name == "daily_short_etf_reliability_evaluation":
        return await run_job(request.app.state.db.session, job_name, daily_short_etf_reliability_evaluation_job)
    if job_name == "daily_short_research_data":
        return await run_job(request.app.state.db.session, job_name, daily_short_research_data_job)
    if job_name == "etf_history_backfill":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: etf_history_backfill_job(tracked_session, days=days),
        )
    if job_name == "post_close_etf_data":
        return await run_job(request.app.state.db.session, job_name, post_close_etf_data_job)
    if job_name == "post_close_etf_signals":
        return await run_job(request.app.state.db.session, job_name, post_close_etf_signals_job)
    if job_name == "post_close_etf_label_outcome_review":
        return await run_job(request.app.state.db.session, job_name, post_close_etf_label_outcome_review_job)
    if job_name == "post_close_etf_observation_portfolio":
        return await run_job(request.app.state.db.session, job_name, post_close_etf_observation_portfolio_job)
    if job_name == "daily_etf_universe":
        return await run_job(request.app.state.db.session, job_name, daily_etf_universe_job)
    if job_name == "daily_etf_taxonomy":
        return await run_job(request.app.state.db.session, job_name, daily_etf_taxonomy_job)
    if job_name == "daily_short_research_signals":
        return await run_job(request.app.state.db.session, job_name, daily_short_research_signals_job)
    if job_name == "daily_etf_signal_validation":
        return await run_job(request.app.state.db.session, job_name, daily_etf_signal_validation_job)
    if job_name == "etf_label_historical_replay":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: etf_label_historical_replay_job(
                tracked_session,
                days=days,
                max_assets=max_assets,
            ),
        )
    if job_name == "etf_portfolio_backtest":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: etf_portfolio_backtest_job(
                tracked_session,
                days=days,
                max_assets=min(max_assets, 500),
            ),
        )
    if job_name == "etf_strategy_comparison_backtest":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: etf_strategy_comparison_backtest_job(
                tracked_session,
                days=days,
                max_assets=min(max_assets, 500),
            ),
        )
    if job_name == "etf_strategy_healthcheck":
        return await run_job(request.app.state.db.session, job_name, etf_strategy_healthcheck_job)
    if job_name == "etf_optimized_allocation":
        return await run_job(request.app.state.db.session, job_name, etf_optimized_allocation_job)
    if job_name == "etf_exit_hyperopt":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: etf_exit_hyperopt_job(
                tracked_session,
                days=min(days, 1095),
                max_assets=max_assets,
            ),
        )
    if job_name == "daily_etf_label_outcome_review":
        return await run_job(request.app.state.db.session, job_name, daily_etf_label_outcome_review_job)
    if job_name == "daily_etf_observation_portfolio":
        return await run_job(request.app.state.db.session, job_name, daily_etf_observation_portfolio_job)
    if job_name == "daily_short_research_advisor":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: daily_short_research_advisor_job(
                tracked_session,
                request.app.state.settings,
                llm_client,
            ),
        )
    if job_name == "daily_tracked_position_alerts":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: daily_tracked_position_alerts_job(
                tracked_session,
                request.app.state.settings,
            ),
        )
    if job_name == "intraday_etf_watch":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: intraday_etf_watch_with_alerts_job(
                tracked_session,
                settings=request.app.state.settings,
                run_type="manual",
                force=True,
            ),
        )
    if job_name == "intraday_etf_cleanup":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: intraday_etf_cleanup_job(tracked_session),
        )
    if job_name == "news_summary_backfill":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: _news_summary_backfill_job(tracked_session, llm_client),
        )
    if job_name == "monthly_dca_reminder":
        return await run_job(
            request.app.state.db.session,
            job_name,
            lambda tracked_session: monthly_dca_reminder_job(
                tracked_session,
                request.app.state.settings,
            ),
        )
    raise HTTPException(status_code=404, detail="Unknown job")
