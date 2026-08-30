from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.short_etf.data import retry_failed_or_stale_etf_data, sync_etf_price_history


async def daily_short_etf_data_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    return await sync_etf_price_history(session, today - timedelta(days=90), today)


async def daily_short_etf_retry_failed_data_job(session: AsyncSession) -> dict[str, Any]:
    return await retry_failed_or_stale_etf_data(session)
