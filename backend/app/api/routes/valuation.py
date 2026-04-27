from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import IndexValuationHistory
from app.schemas.valuation import CurrentValuation, HistoricalValuation

router = APIRouter(prefix="/api/valuation", tags=["valuation"])


@router.get("/current", response_model=list[CurrentValuation])
async def get_current_valuation(session: AsyncSession = Depends(get_db_session)) -> list[CurrentValuation]:
    latest_subquery = (
        select(
            IndexValuationHistory.index_code,
            func.max(IndexValuationHistory.valuation_date).label("latest_date"),
        )
        .group_by(IndexValuationHistory.index_code)
        .subquery()
    )
    rows = (
        await session.scalars(
            select(IndexValuationHistory)
            .join(
                latest_subquery,
                and_(
                    latest_subquery.c.index_code == IndexValuationHistory.index_code,
                    latest_subquery.c.latest_date == IndexValuationHistory.valuation_date,
                ),
            )
            .order_by(IndexValuationHistory.index_code.asc())
        )
    ).all()
    return [
        CurrentValuation(
            index_code=row.index_code,
            pe=row.pe,
            pb=row.pb,
            pe_percentile=row.pe_percentile,
            pb_percentile=row.pb_percentile,
            as_of_date=row.valuation_date,
        )
        for row in rows
    ]


@router.get("/{index_code}/history", response_model=list[HistoricalValuation])
async def get_valuation_history(
    index_code: str,
    from_: date = Query(alias="from"),
    to: date = Query(),
    session: AsyncSession = Depends(get_db_session),
) -> list[HistoricalValuation]:
    rows = (
        await session.scalars(
            select(IndexValuationHistory)
            .where(
                IndexValuationHistory.index_code == index_code,
                IndexValuationHistory.valuation_date >= from_,
                IndexValuationHistory.valuation_date <= to,
            )
            .order_by(IndexValuationHistory.valuation_date.asc())
        )
    ).all()
    return [
        HistoricalValuation(
            date=row.valuation_date,
            pe=row.pe,
            pb=row.pb,
            pe_percentile=row.pe_percentile,
            pb_percentile=row.pb_percentile,
            effective_window=row.effective_window,
        )
        for row in rows
    ]
