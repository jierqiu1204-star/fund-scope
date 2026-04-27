from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NewsFeedItem(BaseModel):
    id: int
    title: str
    url: str
    published_at: datetime
    summary: str | None
    summary_status: str
    event_type: str | None


class NewsFeedGroup(BaseModel):
    fund_code: str
    items: list[NewsFeedItem]


class NewsFeedResponse(BaseModel):
    groups: list[NewsFeedGroup]
