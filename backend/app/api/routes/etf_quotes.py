from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.schemas.etf_quotes import IntradayEtfWatchStatusOut
from app.services.intraday_etf.service import watch_status

router = APIRouter(prefix="/api/etf-quotes", tags=["etf-quotes"])


@router.get("/tracked", response_model=IntradayEtfWatchStatusOut)
async def get_tracked_etf_quotes(
    session: AsyncSession = Depends(get_db_session),
) -> IntradayEtfWatchStatusOut:
    return await watch_status(session)
