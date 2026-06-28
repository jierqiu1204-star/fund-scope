from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.schemas.etf_quotes import EtfLiveRankingListOut, IntradayEtfWatchStatusOut
from app.services.intraday_etf.service import watch_status
from app.services.workflows.etf_live_rankings import live_rankings

router = APIRouter(prefix="/api/etf-quotes", tags=["etf-quotes"])


@router.get("/tracked", response_model=IntradayEtfWatchStatusOut)
async def get_tracked_etf_quotes(
    session: AsyncSession = Depends(get_db_session),
) -> IntradayEtfWatchStatusOut:
    return await watch_status(session)


@router.get("/live-rankings", response_model=EtfLiveRankingListOut)
async def get_live_rankings(
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    theme: str | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> EtfLiveRankingListOut:
    safe_limit = max(1, limit)
    safe_offset = max(0, offset)
    return await live_rankings(session, limit=safe_limit, offset=safe_offset, q=q, theme=theme)
