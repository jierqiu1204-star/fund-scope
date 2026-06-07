from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.short_research.service import run_signal_generation, sync_short_research_data


async def daily_short_research_data_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    return await sync_short_research_data(session, from_date=today - timedelta(days=120), to_date=today)


async def daily_short_research_signals_job(session: AsyncSession) -> dict[str, Any]:
    run = await run_signal_generation(session)
    return {
        "run_id": run.id,
        "status": run.status,
        "as_of_date": run.as_of_date.isoformat(),
        "items": int(run.summary_json.get("item_count", 0)),
        "funds": int(run.summary_json.get("fund_count", 0)),
        "etfs": int(run.summary_json.get("etf_count", 0)),
        "conclusion_counts": run.summary_json.get("conclusion_counts", {}),
    }
