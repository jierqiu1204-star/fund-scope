from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import EtfDailyWorkflowLock, ShortResearchSignalRun, utcnow
from app.services.short_research.service import run_signal_generation
from app.services.short_research.snapshot_publication import (
    SnapshotPublicationError,
    build_etf_coverage_barrier,
    publish_full_snapshot,
)
from app.services.short_research.universe import refresh_etf_universe
from app.services.workflows.short_research_data import (
    sync_short_research_data_with_tracking_priority,
)

ETF_DAILY_WORKFLOW_RUNNING = "running"
ETF_DAILY_WORKFLOW_MIN_COVERAGE = 0.95


async def try_acquire_etf_daily_workflow_lock(session: AsyncSession, trade_date: date) -> bool:
    updated = await session.execute(
        update(EtfDailyWorkflowLock)
        .where(
            EtfDailyWorkflowLock.trade_date == trade_date,
            EtfDailyWorkflowLock.status != ETF_DAILY_WORKFLOW_RUNNING,
        )
        .values(
            status=ETF_DAILY_WORKFLOW_RUNNING,
            started_at=utcnow(),
            finished_at=None,
            details_json={},
        )
    )
    if updated.rowcount:
        await session.commit()
        return True

    session.add(EtfDailyWorkflowLock(trade_date=trade_date, status=ETF_DAILY_WORKFLOW_RUNNING))
    try:
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def finish_etf_daily_workflow_lock(
    session: AsyncSession,
    trade_date: date,
    status: str,
    details: dict[str, Any],
) -> None:
    lock = await session.get(EtfDailyWorkflowLock, trade_date)
    if lock is None:
        return
    lock.status = status
    lock.finished_at = utcnow()
    lock.details_json = details
    await session.commit()


async def run_daily_etf_research_workflow(
    session: AsyncSession,
    *,
    trade_date: date,
    downstream: Callable[[AsyncSession, ShortResearchSignalRun], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if not await try_acquire_etf_daily_workflow_lock(session, trade_date):
        return {
            "trade_date": trade_date.isoformat(),
            "workflow_status": "locked",
            "job_status": "skipped",
            "job_message": "ETF daily workflow is already running for this trade date",
        }

    try:
        universe = await refresh_etf_universe(session, as_of_date=trade_date)
        sync = await sync_short_research_data_with_tracking_priority(
            session,
            from_date=trade_date - timedelta(days=120),
            to_date=trade_date,
            asset_type=ASSET_TYPE_ETF,
        )
        coverage = await build_etf_coverage_barrier(session, as_of_trade_date=trade_date)
        details: dict[str, Any] = {
            "trade_date": trade_date.isoformat(),
            "universe": universe,
            "sync": sync,
            "coverage": coverage.to_dict(),
        }
        if not coverage.expected_codes or coverage.coverage_ratio < ETF_DAILY_WORKFLOW_MIN_COVERAGE:
            result = {
                **details,
                "workflow_status": "coverage_failed",
                "job_status": "failed",
                "job_message": "ETF daily coverage is below the publication threshold",
            }
            await finish_etf_daily_workflow_lock(session, trade_date, "failed", result)
            return result

        generated = await run_signal_generation(session, as_of_date=trade_date, asset_type=ASSET_TYPE_ETF)
        try:
            published = await publish_full_snapshot(session, run_id=generated.id)
        except SnapshotPublicationError as exc:
            result = {
                **details,
                "signal_run_id": generated.id,
                "workflow_status": "publication_failed",
                "job_status": "failed",
                "job_message": str(exc),
            }
            await finish_etf_daily_workflow_lock(session, trade_date, "failed", result)
            return result

        result = {
            **details,
            "signal_run_id": generated.id,
            "published_run_id": published.id,
            "workflow_status": "success",
        }
        if downstream is not None:
            result["downstream"] = await downstream(session, published)
        await finish_etf_daily_workflow_lock(session, trade_date, "success", result)
        return result
    except Exception:
        await finish_etf_daily_workflow_lock(session, trade_date, "failed", {"trade_date": trade_date.isoformat()})
        raise
