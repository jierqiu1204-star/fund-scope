from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import EtfDailyWorkflowLock, ShortResearchSignalRun, utcnow
from app.services.market_data import ASIA_SHANGHAI
from app.services.short_research.dual_snapshot_materialization import (
    materialize_dual_ranking_snapshot,
    publish_dual_ranking_snapshot,
)
from app.services.short_research.snapshot_publication import (
    SnapshotPublicationError,
    build_etf_coverage_barrier,
)
from app.services.short_research.universe import refresh_etf_universe
from app.services.workflows.short_research_data import (
    sync_short_research_data_with_tracking_priority,
)

ETF_DAILY_WORKFLOW_RUNNING = "running"
ETF_DAILY_WORKFLOW_MIN_COVERAGE = 0.95
# Every production holder is bounded well below this window. Keep enough
# headroom for a slow commit while allowing a restarted worker to recover
# without blocking the remaining post-close catch-up window for half an hour.
ETF_DAILY_WORKFLOW_LOCK_LEASE = timedelta(minutes=5)


def etf_decision_cutoff(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(15, 0))


def etf_source_availability_cutoff(
    trade_date: date,
    *,
    now: datetime | None = None,
) -> datetime:
    local_now = now or datetime.now(ASIA_SHANGHAI)
    if local_now.tzinfo is not None:
        local_now = local_now.astimezone(ASIA_SHANGHAI).replace(tzinfo=None)
    if local_now.date() != trade_date:
        return etf_decision_cutoff(trade_date)
    return max(local_now, etf_decision_cutoff(trade_date))


async def generate_and_publish_etf_snapshot(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
    source_availability_cutoff: datetime | None = None,
) -> ShortResearchSignalRun:
    source_cutoff = source_availability_cutoff or etf_source_availability_cutoff(trade_date)
    generated = await materialize_dual_ranking_snapshot(
        session,
        trade_date=trade_date,
        decision_cutoff=decision_cutoff,
        source_availability_cutoff=source_cutoff,
    )
    await session.commit()
    return await publish_dual_ranking_snapshot(session, run_id=generated.id)


async def generate_provisional_etf_research_preview(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
    source_availability_cutoff: datetime | None = None,
) -> ShortResearchSignalRun:
    source_cutoff = source_availability_cutoff or etf_source_availability_cutoff(
        trade_date
    )
    generated = await materialize_dual_ranking_snapshot(
        session,
        trade_date=trade_date,
        decision_cutoff=decision_cutoff,
        source_availability_cutoff=source_cutoff,
    )
    if (generated.summary_json or {}).get("readiness_state") != "degraded":
        raise SnapshotPublicationError(
            "provisional preview requires degraded ETF readiness"
        )
    await session.commit()
    return generated


async def try_acquire_etf_daily_workflow_lock(
    session: AsyncSession,
    trade_date: date,
    *,
    now: datetime | None = None,
) -> bool:
    started_at = now or utcnow()
    lease_cutoff = started_at - ETF_DAILY_WORKFLOW_LOCK_LEASE
    updated = await session.execute(
        update(EtfDailyWorkflowLock)
        .where(
            EtfDailyWorkflowLock.trade_date == trade_date,
            or_(
                EtfDailyWorkflowLock.status != ETF_DAILY_WORKFLOW_RUNNING,
                EtfDailyWorkflowLock.started_at.is_(None),
                EtfDailyWorkflowLock.started_at < lease_cutoff,
            ),
        )
        .values(
            status=ETF_DAILY_WORKFLOW_RUNNING,
            started_at=started_at,
            finished_at=None,
            details_json={},
        )
    )
    if getattr(updated, "rowcount", 0):
        await session.commit()
        return True

    session.add(
        EtfDailyWorkflowLock(
            trade_date=trade_date,
            status=ETF_DAILY_WORKFLOW_RUNNING,
            started_at=started_at,
        )
    )
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


async def record_etf_ranking_batch_progress(
    session: AsyncSession,
    trade_date: date,
    progress: dict[str, Any],
) -> None:
    lock = await session.get(EtfDailyWorkflowLock, trade_date)
    if lock is None or lock.status != ETF_DAILY_WORKFLOW_RUNNING:
        raise RuntimeError("ETF daily workflow lock is not held")
    details = dict(lock.details_json or {})
    batches = list(details.get("ranking_batches") or [])
    batches.append(dict(progress))
    lock.details_json = {
        **details,
        "ranking_cursor": progress.get("cursor"),
        "ranking_remaining": progress.get("remaining"),
        "ranking_batches": batches,
    }
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
        if universe.get("authoritative") is not True:
            result: dict[str, Any] = {
                "trade_date": trade_date.isoformat(),
                "universe": universe,
                "workflow_status": "universe_failed",
                "job_status": "failed",
                "job_message": "ETF universe discovery is not authoritative; frozen membership was preserved",
            }
            await finish_etf_daily_workflow_lock(session, trade_date, "failed", result)
            return result
        sync = await sync_short_research_data_with_tracking_priority(
            session,
            from_date=trade_date - timedelta(days=120),
            to_date=trade_date,
            asset_type=ASSET_TYPE_ETF,
        )
        source_cutoff = etf_source_availability_cutoff(trade_date)
        coverage = await build_etf_coverage_barrier(
            session,
            as_of_trade_date=trade_date,
            data_cutoff=source_cutoff,
        )
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

        async def record_progress(progress: dict[str, Any]) -> None:
            await record_etf_ranking_batch_progress(session, trade_date, progress)

        generated = await materialize_dual_ranking_snapshot(
            session,
            trade_date=trade_date,
            decision_cutoff=etf_decision_cutoff(trade_date),
            source_availability_cutoff=source_cutoff,
            batch_progress_callback=record_progress,
        )
        await session.commit()
        try:
            published = await publish_dual_ranking_snapshot(session, run_id=generated.id)
        except SnapshotPublicationError as exc:
            await session.rollback()
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
        await session.rollback()
        await finish_etf_daily_workflow_lock(session, trade_date, "failed", {"trade_date": trade_date.isoformat()})
        raise
