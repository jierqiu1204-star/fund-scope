from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.defaults.funds import DEFAULT_RESEARCH_FUNDS
from app.models.entities import Fund

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


@router.post("/apply-default-portfolio")
async def apply_default_portfolio(
    force: bool = Query(default=False),
    mode: str = Query(default="merge"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    existing = (await session.scalars(select(Fund).where(Fund.is_watchlist.is_(True)))).all()
    if existing and not force:
        raise HTTPException(
            status_code=409,
            detail="Watchlist already contains funds. Pass force=true and choose merge or replace.",
        )
    if existing and mode == "replace":
        await session.execute(update(Fund).values(is_watchlist=False))

    for default_fund in DEFAULT_RESEARCH_FUNDS:
        fund = await session.get(Fund, default_fund.code)
        if fund is None:
            session.add(
                Fund(
                    code=default_fund.code,
                    name=default_fund.name,
                    category=default_fund.category,
                    tracking_index_code=default_fund.tracking_index_code,
                    target_allocation=default_fund.target_allocation,
                    is_watchlist=True,
                )
            )
        else:
            fund.name = default_fund.name
            fund.category = default_fund.category
            fund.tracking_index_code = default_fund.tracking_index_code
            fund.is_watchlist = True
            fund.target_allocation = default_fund.target_allocation
    await session.commit()
    return {"applied": True, "mode": mode}
