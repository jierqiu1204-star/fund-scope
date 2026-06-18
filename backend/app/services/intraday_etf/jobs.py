from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import IntradayEtfWatchRun, TrackedPosition, utcnow
from app.services.intraday_etf.service import (
    ASIA_SHANGHAI,
    build_watchlist,
    current_market_state,
    fetch_spot_quotes,
    latest_quotes_by_code,
    persist_quotes,
)
from app.services.tracked_positions.service import (
    ACTIVE_STATUS,
    create_alert_if_needed,
    refresh_entry_if_waiting,
)


async def intraday_etf_watch_job(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    run_type: str = "scheduled",
    force: bool = False,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    started = utcnow()
    market_state = current_market_state()
    watchlist = await build_watchlist(session)
    run = IntradayEtfWatchRun(
        run_type=run_type,
        status="running",
        started_at=started,
        market_session=market_state.session,
        watched_count=len(watchlist.items),
        details_json={
            "signal_run_id": watchlist.signal_run_id,
            "signal_as_of_date": watchlist.signal_as_of_date.isoformat() if watchlist.signal_as_of_date else None,
            "signal_status": watchlist.signal_status,
            "watchlist_sources": {
                item.etf_code: sorted(item.sources)
                for item in watchlist.items
            },
        },
    )
    session.add(run)
    await session.commit()
    try:
        if market_state.status != "open" and not force:
            run.status = "skipped"
            run.skipped_reason = "当前不在 A 股 ETF 交易时段，已跳过外部实时行情请求。"
            run.finished_at = utcnow()
            await session.commit()
            return _result(run)

        if not watchlist.items:
            run.status = "skipped"
            run.skipped_reason = "没有需要盘中盯盘的 ETF。"
            run.finished_at = utcnow()
            await session.commit()
            return _result(run)

        provider_error = None
        try:
            quotes = await fetch_spot_quotes(fetcher)
            updated = await persist_quotes(session, watchlist, quotes)
        except Exception as exc:  # noqa: BLE001
            provider_error = str(exc)
            quotes = {}
            updated = 0
        latest_quotes = await latest_quotes_by_code(session, [item.etf_code for item in watchlist.items])
        local_now = datetime.now(ASIA_SHANGHAI).replace(tzinfo=None)
        stale = sum(1 for quote in latest_quotes.values() if local_now - quote.quote_time > timedelta(minutes=3))
        alert_result = await _check_tracked_etf_alerts(session, settings or get_settings())
        run.status = "degraded" if provider_error else "success"
        run.updated_quote_count = updated
        run.stale_quote_count = stale
        run.alert_count = alert_result["alerts_created"]
        run.email_sent_count = alert_result["emails_sent"]
        run.suppressed_count = alert_result["suppressed"]
        run.error_message = provider_error
        run.finished_at = utcnow()
        run.details_json = {
            **dict(run.details_json or {}),
            "provider_returned": len(quotes),
            "updated_codes": sorted(set(quotes).intersection({item.etf_code for item in watchlist.items})),
            "provider_error": provider_error,
            "alert_result": alert_result,
        }
        await session.commit()
        return _result(run)
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = utcnow()
        await session.commit()
        raise


async def _check_tracked_etf_alerts(session: AsyncSession, settings: Settings) -> dict[str, int]:
    rows = (
        await session.scalars(
            select(TrackedPosition).where(
                TrackedPosition.asset_type == ASSET_TYPE_ETF,
                TrackedPosition.status == ACTIVE_STATUS,
            )
        )
    ).all()
    result = {
        "positions_checked": len(rows),
        "alerts_created": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "emails_skipped": 0,
        "web_only": 0,
        "deduplicated": 0,
        "suppressed": 0,
        "data_ineligible": 0,
        "no_signal": 0,
    }
    for position in rows:
        await refresh_entry_if_waiting(session, position)
        alert, status = await create_alert_if_needed(session, position, settings, evaluation_mode="intraday")
        if status == "deduplicated":
            result["deduplicated"] += 1
            continue
        if status == "suppressed":
            result["suppressed"] += 1
            continue
        if status == "no_signal":
            result["no_signal"] += 1
            continue
        if status == "data_ineligible":
            result["data_ineligible"] += 1
            continue
        if alert is not None:
            result["alerts_created"] += 1
        if status == "email_sent":
            result["emails_sent"] += 1
        elif status == "email_failed":
            result["emails_failed"] += 1
        elif status == "email_skipped":
            result["emails_skipped"] += 1
        elif status == "web_only":
            result["web_only"] += 1
    return result


def _result(run: IntradayEtfWatchRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "run_type": run.run_type,
        "status": run.status,
        "market_session": run.market_session,
        "watched_count": run.watched_count,
        "updated_quote_count": run.updated_quote_count,
        "stale_quote_count": run.stale_quote_count,
        "alert_count": run.alert_count,
        "email_sent_count": run.email_sent_count,
        "suppressed_count": run.suppressed_count,
        "skipped_reason": run.skipped_reason,
        "error_message": run.error_message,
        "details": dict(run.details_json or {}),
    }
