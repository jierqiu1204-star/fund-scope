from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import Index, IndexValuationHistory
from app.schemas.valuation import (
    CurrentValuation,
    HistoricalValuation,
    IndexWatchlistCreate,
    IndexWatchlistItem,
    IndexWatchlistRemoval,
)

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


@router.post(
    "/watchlist",
    response_model=IndexWatchlistItem,
    status_code=status.HTTP_201_CREATED,
)
async def add_index_to_watchlist(
    payload: IndexWatchlistCreate,
    session: AsyncSession = Depends(get_db_session),
) -> IndexWatchlistItem:
    index_code = payload.code.strip().upper()
    index = await session.get(Index, index_code)
    if index is None:
        index = Index(
            code=index_code,
            name=payload.name.strip(),
            region=payload.region.strip().upper(),
            is_watchlist=True,
        )
        session.add(index)
        backfill_required = True
    else:
        index.name = payload.name.strip() or index.name
        index.region = payload.region.strip().upper() or index.region
        index.is_watchlist = True
        history_count = await session.scalar(
            select(func.count())
            .select_from(IndexValuationHistory)
            .where(IndexValuationHistory.index_code == index_code)
        )
        backfill_required = not bool(history_count)
    await session.commit()
    return IndexWatchlistItem(
        code=index.code,
        name=index.name,
        region=index.region,
        is_watchlist=index.is_watchlist,
        backfill_required=backfill_required,
    )


@router.delete("/watchlist/{index_code}", response_model=IndexWatchlistRemoval)
async def remove_index_from_watchlist(
    index_code: str,
    session: AsyncSession = Depends(get_db_session),
) -> IndexWatchlistRemoval:
    normalized_code = index_code.strip().upper()
    index = await session.get(Index, normalized_code)
    if index is None:
        raise HTTPException(status_code=404, detail="Index is not in the watchlist.")
    index.is_watchlist = False
    historical_rows = await session.scalar(
        select(func.count())
        .select_from(IndexValuationHistory)
        .where(IndexValuationHistory.index_code == normalized_code)
    )
    await session.commit()
    return IndexWatchlistRemoval(
        code=index.code,
        is_watchlist=index.is_watchlist,
        historical_rows_retained=int(historical_rows or 0),
    )


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
