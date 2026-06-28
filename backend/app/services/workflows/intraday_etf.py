from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import IntradayEtfWatchRun
from app.services.intraday_etf.jobs import intraday_etf_watch_job
from app.services.tracked_positions.jobs import intraday_tracked_position_alerts_job


async def intraday_etf_watch_with_alerts_job(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    run_type: str = "scheduled",
    force: bool = False,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    result = await intraday_etf_watch_job(
        session,
        settings=settings,
        run_type=run_type,
        force=force,
        fetcher=fetcher,
    )
    if result.get("status") not in {"success", "degraded"}:
        return result

    alert_result = await intraday_tracked_position_alerts_job(session, settings)
    run = await session.get(IntradayEtfWatchRun, result["run_id"])
    if run is not None:
        run.alert_count = alert_result["alerts_created"]
        run.email_sent_count = alert_result["emails_sent"]
        run.suppressed_count = alert_result["suppressed"]
        run.details_json = {
            **dict(run.details_json or {}),
            "alert_result": alert_result,
        }
        await session.commit()

    result["alert_count"] = alert_result["alerts_created"]
    result["email_sent_count"] = alert_result["emails_sent"]
    result["suppressed_count"] = alert_result["suppressed"]
    result["details"] = {
        **dict(result.get("details") or {}),
        "alert_result": alert_result,
    }
    return result
