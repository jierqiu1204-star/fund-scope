from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import PaperPortfolio, StrategyRun, utcnow
from app.services.strategy_lab.engine import run_paper_update


async def daily_strategy_paper_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    portfolios = (
        await session.scalars(
            select(PaperPortfolio)
            .where(PaperPortfolio.status == "active")
            .order_by(PaperPortfolio.id.asc())
        )
    ).all()
    updated = 0
    failures: list[dict[str, str]] = []
    for paper in portfolios:
        try:
            await run_paper_update(session, paper, as_of_date=today)
            updated += 1
        except Exception as exc:  # noqa: BLE001
            session.add(
                StrategyRun(
                    strategy_id=paper.strategy_id,
                    run_type="simulation_update",
                    status="failed",
                    started_at=utcnow(),
                    finished_at=utcnow(),
                    as_of_date=today,
                    date_range_json={"start_date": paper.started_at.isoformat(), "end_date": today.isoformat()},
                    metrics_json={},
                    error_message=str(exc),
                )
            )
            await session.commit()
            failures.append({"paper_id": str(paper.id), "error": str(exc)})
    return {"paper_portfolios": len(portfolios), "updated": updated, "failed": len(failures), "failures": failures}
