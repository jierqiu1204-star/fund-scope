from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import NewsItem, NewsSummary
from app.schemas.news import NewsFeedGroup, NewsFeedItem, NewsFeedResponse

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("", response_model=NewsFeedResponse)
async def get_news_feed(
    fund_code: str | None = None,
    event_type: str | None = None,
    days: int = 14,
    session: AsyncSession = Depends(get_db_session),
) -> NewsFeedResponse:
    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        await session.execute(
            select(NewsItem, NewsSummary)
            .join(NewsSummary, NewsSummary.news_item_id == NewsItem.id, isouter=True)
            .where(NewsItem.published_at >= since)
            .order_by(NewsItem.fund_code.asc(), NewsItem.published_at.desc())
        )
    ).all()
    groups: dict[str, list[NewsFeedItem]] = defaultdict(list)
    for news_item, summary in rows:
        if fund_code and news_item.fund_code != fund_code:
            continue
        if event_type and (summary is None or summary.event_type != event_type):
            continue
        groups[news_item.fund_code].append(
            NewsFeedItem(
                id=news_item.id,
                title=news_item.title,
                url=news_item.url,
                published_at=news_item.published_at,
                summary=summary.summary if summary is not None else None,
                summary_status="ok" if summary is not None else "failed",
                event_type=summary.event_type if summary is not None else None,
            )
        )
    return NewsFeedResponse(
        groups=[NewsFeedGroup(fund_code=code, items=items) for code, items in groups.items()]
    )
