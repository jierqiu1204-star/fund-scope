from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import optional_approved_user
from app.core.db import get_db_session
from app.models.entities import User
from app.schemas.etf_quotes import EtfLiveRankingListOut, IntradayEtfWatchStatusOut
from app.services.intraday_etf.service import watch_status
from app.services.workflows.etf_live_rankings import live_rankings
from app.services.workflows.tracking_filters import validate_tracking_states

router = APIRouter(prefix="/api/etf-quotes", tags=["etf-quotes"])


def _csv_values(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


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
    observation_labels: str | None = Query(default=None),
    entry_labels: str | None = Query(default=None),
    tracking_states: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    user: User | None = Depends(optional_approved_user),
) -> EtfLiveRankingListOut:
    safe_limit = max(1, limit)
    safe_offset = max(0, offset)
    tracking_filters = _csv_values(tracking_states)
    try:
        validate_tracking_states(tracking_filters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if tracking_filters and user is None:
        raise HTTPException(status_code=401, detail="持仓筛选需要登录")
    return await live_rankings(
        session,
        limit=safe_limit,
        offset=safe_offset,
        q=q,
        theme=theme,
        observation_labels=_csv_values(observation_labels),
        entry_labels=_csv_values(entry_labels),
        tracking_states=tracking_filters,
        user_id=user.id if user is not None else None,
    )
