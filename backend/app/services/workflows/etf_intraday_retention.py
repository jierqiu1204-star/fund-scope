from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfIntradayQuoteEvidenceRef,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
)
from app.services.intraday_etf.evidence import (
    ACTIONABLE_RANKING_INPUT_PURPOSE,
    EVIDENCE_STATE_PROTECTED,
    EVIDENCE_STATE_UNAVAILABLE,
    PUBLISHED_SNAPSHOT_OWNER,
    UNAVAILABLE_NO_QUOTE_AT_CUTOFF,
    seal_intraday_quote_evidence,
)
from app.services.intraday_etf.retention_policy import (
    INTRADAY_FULL_DETAIL_RETENTION_TRADING_DAYS,
)
from app.services.intraday_etf.service import (
    INTRADAY_CLEANUP_BATCH_SIZE,
    summarize_and_cleanup_intraday_quotes,
)

INTRADAY_RETENTION_TIMEOUT_SECONDS = 50.0
INTRADAY_RETENTION_WORK_SECONDS = 45.0
INTRADAY_EVIDENCE_BACKFILL_RUN_LIMIT = 8
INTRADAY_CLEANUP_SLICE_LIMIT = 64
INTRADAY_CLEANUP_BATCH_SIZE_MIN = 500
INTRADAY_CLEANUP_BATCH_SIZE_MAX = 5_000


def _market_decision_cutoff(run: ShortResearchSignalRun) -> datetime | None:
    value = (run.config_json or {}).get("market_decision_cutoff")
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _expected_item_count_subquery() -> Any:
    return (
        select(func.count(ShortResearchSignalItem.id))
        .where(
            ShortResearchSignalItem.run_id == ShortResearchSignalRun.id,
            ShortResearchSignalItem.asset_type == "etf",
        )
        .correlate(ShortResearchSignalRun)
        .scalar_subquery()
    )


def _valid_evidence_count_subquery() -> Any:
    return (
        select(func.count(EtfIntradayQuoteEvidenceRef.id))
        .where(
            EtfIntradayQuoteEvidenceRef.owner_kind == PUBLISHED_SNAPSHOT_OWNER,
            EtfIntradayQuoteEvidenceRef.owner_id == ShortResearchSignalRun.id,
            EtfIntradayQuoteEvidenceRef.evidence_purpose
            == ACTIONABLE_RANKING_INPUT_PURPOSE,
            or_(
                EtfIntradayQuoteEvidenceRef.evidence_state
                == EVIDENCE_STATE_PROTECTED,
                and_(
                    EtfIntradayQuoteEvidenceRef.evidence_state
                    == EVIDENCE_STATE_UNAVAILABLE,
                    EtfIntradayQuoteEvidenceRef.unavailable_reason
                    == UNAVAILABLE_NO_QUOTE_AT_CUTOFF,
                ),
            ),
        )
        .correlate(ShortResearchSignalRun)
        .scalar_subquery()
    )


async def _next_unsealed_publication(
    session: AsyncSession,
    *,
    cutoff_date: date,
) -> ShortResearchSignalRun | None:
    expected_count = _expected_item_count_subquery()
    valid_evidence_count = _valid_evidence_count_subquery()
    return await session.scalar(
        select(ShortResearchSignalRun)
        .where(
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.as_of_trade_date < cutoff_date,
            expected_count > 0,
            valid_evidence_count != expected_count,
        )
        .order_by(
            ShortResearchSignalRun.as_of_trade_date.asc(),
            ShortResearchSignalRun.id.asc(),
        )
        .limit(1)
    )


async def _publication_without_items(
    session: AsyncSession,
    *,
    cutoff_date: date,
) -> ShortResearchSignalRun | None:
    expected_count = _expected_item_count_subquery()
    return await session.scalar(
        select(ShortResearchSignalRun)
        .where(
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.as_of_trade_date < cutoff_date,
            expected_count == 0,
        )
        .order_by(
            ShortResearchSignalRun.as_of_trade_date.asc(),
            ShortResearchSignalRun.id.asc(),
        )
        .limit(1)
    )


async def _seal_publication(
    session: AsyncSession,
    run: ShortResearchSignalRun,
) -> dict[str, Any]:
    codes = (
        await session.scalars(
            select(ShortResearchSignalItem.asset_code)
            .where(
                ShortResearchSignalItem.run_id == run.id,
                ShortResearchSignalItem.asset_type == "etf",
            )
            .order_by(ShortResearchSignalItem.asset_code.asc())
        )
    ).all()
    return await seal_intraday_quote_evidence(
        session,
        owner_kind=PUBLISHED_SNAPSHOT_OWNER,
        owner_id=run.id,
        asset_codes=codes,
        trade_date=run.as_of_trade_date,
        decision_cutoff=_market_decision_cutoff(run),
        receipt_cutoff=run.data_cutoff,
    )


async def _run_retention_slice(
    session: AsyncSession,
    *,
    retention_trading_days: int,
    batch_size: int,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    work_deadline = loop.time() + INTRADAY_RETENTION_WORK_SECONDS
    preview = await summarize_and_cleanup_intraday_quotes(
        session,
        retention_trading_days=retention_trading_days,
        batch_size=batch_size,
        evidence_seal_complete=False,
    )
    cutoff_value = preview.get("cutoff_date")
    if not isinstance(cutoff_value, str):
        return preview
    cutoff_date = date.fromisoformat(cutoff_value)
    invalid_publication = await _publication_without_items(
        session,
        cutoff_date=cutoff_date,
    )
    if invalid_publication is not None:
        return {
            **preview,
            "job_status": "partial",
            "job_message": "历史发布缺少榜单项目，证据封存失败。",
            "unavailable_reason": "published_snapshot_items_missing",
            "blocked_run_id": invalid_publication.id,
        }

    backfilled: list[dict[str, Any]] = []
    for _ in range(INTRADAY_EVIDENCE_BACKFILL_RUN_LIMIT):
        if loop.time() >= work_deadline - 2.0:
            break
        run = await _next_unsealed_publication(session, cutoff_date=cutoff_date)
        if run is None:
            break
        if _market_decision_cutoff(run) is None:
            return {
                **preview,
                "job_status": "partial",
                "job_message": "历史发布缺少真实决策截止时间。",
                "unavailable_reason": "published_snapshot_decision_cutoff_missing",
                "blocked_run_id": run.id,
                "evidence_backfill": backfilled,
            }
        result = await _seal_publication(session, run)
        await session.commit()
        backfilled.append(
            {
                "run_id": run.id,
                "trade_date": run.as_of_trade_date.isoformat(),
                **result,
            }
        )

    remaining = await _next_unsealed_publication(session, cutoff_date=cutoff_date)
    if remaining is not None:
        return {
            **preview,
            "job_status": "partial",
            "job_message": "历史发布证据仍在分批封存。",
            "unavailable_reason": "intraday_quote_evidence_backfill_pending",
            "next_run_id": remaining.id,
            "evidence_backfill": backfilled,
        }

    current_batch_size = max(
        INTRADAY_CLEANUP_BATCH_SIZE_MIN,
        min(INTRADAY_CLEANUP_BATCH_SIZE_MAX, batch_size),
    )
    cleanup_slices: list[dict[str, Any]] = []
    total_deleted_rows = 0
    total_summarized_groups = 0
    last_result: dict[str, Any] = preview
    for _ in range(INTRADAY_CLEANUP_SLICE_LIMIT):
        if loop.time() >= work_deadline - 2.0:
            break
        slice_started = loop.time()
        result = await summarize_and_cleanup_intraday_quotes(
            session,
            retention_trading_days=retention_trading_days,
            batch_size=current_batch_size,
            evidence_seal_complete=True,
        )
        duration_seconds = loop.time() - slice_started
        deleted_rows = int(result.get("deleted_rows") or 0)
        summarized_groups = int(result.get("summarized_groups") or 0)
        total_deleted_rows += deleted_rows
        total_summarized_groups += summarized_groups
        cleanup_slices.append(
            {
                "batch_size": current_batch_size,
                "duration_ms": round(duration_seconds * 1_000, 3),
                "deleted_rows": deleted_rows,
                "summarized_groups": summarized_groups,
                "unavailable_reason": result.get("unavailable_reason"),
            }
        )
        last_result = result
        if deleted_rows <= 0 or result.get("job_status") == "partial":
            break
        if duration_seconds < 2.0 and deleted_rows >= current_batch_size * 0.8:
            current_batch_size = min(
                INTRADAY_CLEANUP_BATCH_SIZE_MAX,
                max(current_batch_size + 1, int(current_batch_size * 1.5)),
            )
        elif duration_seconds > 8.0:
            current_batch_size = max(
                INTRADAY_CLEANUP_BATCH_SIZE_MIN,
                current_batch_size // 2,
            )
    return {
        **last_result,
        "summarized_groups": total_summarized_groups,
        "deleted_rows": total_deleted_rows,
        "evidence_backfill": backfilled,
        "cleanup_slice_count": len(cleanup_slices),
        "cleanup_slices": cleanup_slices,
        "next_batch_size": current_batch_size,
        "work_budget_seconds": INTRADAY_RETENTION_WORK_SECONDS,
    }


async def intraday_etf_retention_job(
    session: AsyncSession,
    *,
    retention_trading_days: int = INTRADAY_FULL_DETAIL_RETENTION_TRADING_DAYS,
    batch_size: int = INTRADAY_CLEANUP_BATCH_SIZE,
) -> dict[str, Any]:
    try:
        async with asyncio.timeout(INTRADAY_RETENTION_TIMEOUT_SECONDS):
            return await _run_retention_slice(
                session,
                retention_trading_days=retention_trading_days,
                batch_size=batch_size,
            )
    except TimeoutError:
        await session.rollback()
        return {
            "job_status": "partial",
            "job_message": "盘中证据保留切片达到50秒硬超时。",
            "retention_trading_days": max(1, retention_trading_days),
            "batch_size": max(
                INTRADAY_CLEANUP_BATCH_SIZE_MIN,
                min(INTRADAY_CLEANUP_BATCH_SIZE_MAX, batch_size),
            ),
            "summarized_groups": 0,
            "deleted_rows": 0,
            "unavailable_reason": "intraday_retention_slice_timeout",
        }
