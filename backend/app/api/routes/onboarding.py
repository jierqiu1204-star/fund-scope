from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import Fund

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

DEFAULT_FUNDS = {
    "007339": ("E Fund CSI 300", "equity", 0.4),
    "001052": ("Huaxia SP500", "equity", 0.3),
    "270042": ("GF Nasdaq 100", "equity", 0.2),
    "000198": ("Tianhong YEB", "money_market", 0.1),
}


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

    for code, (name, category, allocation) in DEFAULT_FUNDS.items():
        fund = await session.get(Fund, code)
        if fund is None:
            session.add(
                Fund(
                    code=code,
                    name=name,
                    category=category,
                    target_allocation=allocation,
                    is_watchlist=True,
                )
            )
        else:
            fund.is_watchlist = True
            fund.target_allocation = allocation
    await session.commit()
    return {"applied": True, "mode": mode}
