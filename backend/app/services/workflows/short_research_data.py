from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.services.short_research.service import sync_short_research_data
from app.services.tracked_positions.service import active_tracked_etf_codes


async def sync_short_research_data_with_tracking_priority(
    session: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    asset_type: str | None = None,
    codes: list[str] | None = None,
    sync_all_etfs: bool = False,
) -> dict[str, Any]:
    priority_etf_codes = None
    if asset_type in (None, ASSET_TYPE_ETF):
        priority_etf_codes = await active_tracked_etf_codes(session, codes)
    return await sync_short_research_data(
        session,
        from_date=from_date,
        to_date=to_date,
        asset_type=asset_type,
        codes=codes,
        sync_all_etfs=sync_all_etfs,
        priority_etf_codes=priority_etf_codes,
    )
