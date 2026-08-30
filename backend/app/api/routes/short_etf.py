from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import EtfDataHealth, TradableEtf
from app.schemas.short_etf import (
    EtfDataHealthOut,
    EtfDataStatusOut,
    EtfDataSyncRequest,
    EtfUniverseItemOut,
    EtfUniverseResponse,
)
from app.services.short_etf.data import (
    data_status_summary,
    latest_etf_price,
    list_etf_data_health,
    list_short_etfs,
    retry_failed_or_stale_etf_data,
    sync_etf_price_history,
)
from app.services.workflows.etf_publish_readiness import (
    read_publication_readiness_status,
)

router = APIRouter(prefix="/api/short-etf", tags=["short-etf"])


def _data_health_out(
    etf: TradableEtf,
    health: EtfDataHealth | None,
    is_stale: bool,
) -> EtfDataHealthOut:
    return EtfDataHealthOut(
        code=etf.code,
        name=etf.name,
        status=health.status if health else "unknown",
        provider=health.provider if health else None,
        latest_price_date=health.latest_price_date if health else None,
        successful_rows=health.successful_rows if health else 0,
        last_error_message=health.last_error_message if health else None,
        consecutive_failures=health.consecutive_failures if health else 0,
        is_stale=is_stale,
        updated_at=health.updated_at if health else None,
    )


@router.get("/universe", response_model=EtfUniverseResponse)
async def get_short_etf_universe(session: AsyncSession = Depends(get_db_session)) -> EtfUniverseResponse:
    items: list[EtfUniverseItemOut] = []
    for etf in await list_short_etfs(session):
        latest_price = await latest_etf_price(session, etf.code, date.max)
        items.append(
            EtfUniverseItemOut(
                code=etf.code,
                name=etf.name,
                exchange=etf.exchange,
                theme_tags=etf.theme_tags_json,
                trading_rule_label=etf.trading_rule_label,
                asset_class=etf.asset_class,
                is_short_term_eligible=etf.is_short_term_eligible,
                latest_price_date=latest_price.trade_date if latest_price else None,
                latest_close=latest_price.close if latest_price else None,
            )
        )
    return EtfUniverseResponse(items=items)


@router.post("/data/sync")
async def sync_short_etf_data(
    payload: EtfDataSyncRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    return await sync_etf_price_history(session, payload.from_date, payload.to_date, payload.codes)


@router.get("/data-status", response_model=EtfDataStatusOut)
async def get_short_etf_data_status(session: AsyncSession = Depends(get_db_session)) -> EtfDataStatusOut:
    rows = await list_etf_data_health(session)
    summary = await data_status_summary(session)
    history_readiness = await read_publication_readiness_status(session)
    summary["history_readiness"] = history_readiness
    return EtfDataStatusOut(
        summary=summary,
        history_readiness=history_readiness,
        items=[_data_health_out(etf, health, stale) for etf, health, stale in rows],
    )


@router.post("/data/retry-failed")
async def retry_short_etf_failed_data(session: AsyncSession = Depends(get_db_session)) -> dict[str, object]:
    return await retry_failed_or_stale_etf_data(session)
