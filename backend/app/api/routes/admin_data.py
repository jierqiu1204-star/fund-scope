from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import Fund, FundNavHistory, PaperPortfolio, StrategyDefinition

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/data-status")
async def get_data_status(session: AsyncSession = Depends(get_db_session)) -> dict[str, int | str | None]:
    fund_count = await session.scalar(select(func.count(Fund.code)).where(Fund.is_watchlist.is_(True)))
    nav_rows, earliest_nav_date, latest_nav_date = (
        await session.execute(
            select(
                func.count(FundNavHistory.id),
                func.min(FundNavHistory.nav_date),
                func.max(FundNavHistory.nav_date),
            )
        )
    ).one()
    strategy_count = await session.scalar(select(func.count(StrategyDefinition.id)))
    paper_portfolio_count = await session.scalar(select(func.count(PaperPortfolio.id)))

    return {
        "fund_count": int(fund_count or 0),
        "nav_rows": int(nav_rows or 0),
        "earliest_nav_date": earliest_nav_date.isoformat() if earliest_nav_date else None,
        "latest_nav_date": latest_nav_date.isoformat() if latest_nav_date else None,
        "strategy_count": int(strategy_count or 0),
        "paper_portfolio_count": int(paper_portfolio_count or 0),
    }
