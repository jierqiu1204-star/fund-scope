from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ShortEtfPaperPortfolio
from app.services.short_etf.data import retry_failed_or_stale_etf_data, sync_etf_price_history
from app.services.short_etf.evaluation import run_reliability_evaluation
from app.services.short_etf.paper import PAPER_STATUS_ACTIVE, run_paper_update
from app.services.short_etf.signals import generate_signal_review, run_signal_generation


async def daily_short_etf_data_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    return await sync_etf_price_history(session, today - timedelta(days=90), today)


async def daily_short_etf_retry_failed_data_job(session: AsyncSession) -> dict[str, Any]:
    return await retry_failed_or_stale_etf_data(session)


async def daily_short_etf_signals_job(session: AsyncSession) -> dict[str, Any]:
    run = await run_signal_generation(session)
    review = await generate_signal_review(session, run)
    return {
        "run_id": run.id,
        "review_id": review.id,
        "status": run.status,
        "as_of_date": run.as_of_date.isoformat(),
        "items": int(run.summary_json.get("item_count", 0)),
        "review_items": int(review.summary_json.get("item_count", 0)),
    }


async def daily_short_etf_paper_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    papers = (
        await session.scalars(
            select(ShortEtfPaperPortfolio).where(ShortEtfPaperPortfolio.status == PAPER_STATUS_ACTIVE)
        )
    ).all()
    updated = 0
    failures: list[dict[str, str]] = []
    for paper in papers:
        try:
            await run_paper_update(session, paper, as_of_date=today)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"paper_id": str(paper.id), "error": str(exc)})
    return {"papers": len(papers), "updated": updated, "failed": len(failures), "failures": failures}


async def daily_short_etf_reliability_evaluation_job(session: AsyncSession) -> dict[str, Any]:
    evaluation = await run_reliability_evaluation(session)
    return {
        "evaluation_id": evaluation.id,
        "status": evaluation.status,
        "conclusion": evaluation.conclusion,
        "sample_days": evaluation.sample_days,
        "risk_flags": evaluation.risk_flags_json,
    }
