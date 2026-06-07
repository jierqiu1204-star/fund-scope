from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import ShortResearchSignalRun
from app.schemas.short_research import (
    ShortResearchAssetDetailOut,
    ShortResearchAssetListOut,
    ShortResearchAssetOut,
    ShortResearchChartPointOut,
    ShortResearchDataSyncRequest,
    ShortResearchSignalRunOut,
    ShortResearchSignalRunRequest,
    ShortResearchStatusOut,
)
from app.services.short_research.service import (
    ComputedAsset,
    get_asset_detail,
    latest_signal_run,
    list_computed_assets,
    run_signal_generation,
    signal_run_items_as_assets,
    status_summary,
    sync_short_research_data,
)

router = APIRouter(prefix="/api/short-research", tags=["short-research"])


def _asset_out(asset: ComputedAsset) -> ShortResearchAssetOut:
    return ShortResearchAssetOut(
        asset_type=asset.metadata.asset_type,
        code=asset.metadata.code,
        name=asset.metadata.name,
        rank=asset.rank,
        total_score=round(asset.total_score, 2),
        conclusion=asset.conclusion,
        theme_tags=list(asset.metadata.theme_tags),
        investment_direction=asset.metadata.investment_direction,
        trading_rule_label=asset.metadata.trading_rule_label,
        latest_date=asset.latest_date,
        latest_value=asset.latest_value,
        usable_days=asset.usable_days,
        sample_level=asset.sample_level,
        metrics=asset.metrics,
        score_breakdown=asset.score_breakdown,
        risk_flags=asset.risk_flags,
        rationale=asset.rationale,
        source_note=asset.source_note,
    )


async def _signal_run_out(session: AsyncSession, run: ShortResearchSignalRun) -> ShortResearchSignalRunOut:
    assets = await signal_run_items_as_assets(session, run)
    return ShortResearchSignalRunOut(
        id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        as_of_date=run.as_of_date,
        config=run.config_json,
        summary=run.summary_json,
        error_message=run.error_message,
        items=[_asset_out(item) for item in assets],
    )


@router.get("/status", response_model=ShortResearchStatusOut)
async def get_short_research_status(session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    return await status_summary(session)


@router.get("/assets", response_model=ShortResearchAssetListOut)
async def list_short_research_assets(
    asset_type: str | None = Query(default=None),
    theme: str | None = Query(default=None),
    sort: str = Query(default="score"),
    q: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchAssetListOut:
    assets = await list_computed_assets(session, asset_type=asset_type, theme=theme, sort=sort)
    if q:
        keyword = q.strip().lower()
        assets = [
            item
            for item in assets
            if keyword in item.metadata.code.lower() or keyword in item.metadata.name.lower()
        ]
    return ShortResearchAssetListOut(items=[_asset_out(item) for item in assets], total=len(assets))


@router.get("/assets/{asset_type}/{code}", response_model=ShortResearchAssetDetailOut)
async def get_short_research_asset_detail(
    asset_type: str,
    code: str,
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchAssetDetailOut:
    try:
        asset, chart, sections = await get_asset_detail(session, asset_type, code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ShortResearchAssetDetailOut(
        asset=_asset_out(asset),
        chart=[
            ShortResearchChartPointOut(
                date=item["date"],
                value=item["value"],
                close=item["close"],
                nav=item["nav"],
                drawdown=item["drawdown"],
                turnover=item["turnover"],
            )
            for item in chart
        ],
        return_windows={
            "return_5d": asset.metrics.get("return_5d"),
            "return_10d": asset.metrics.get("return_10d"),
            "return_20d": asset.metrics.get("return_20d"),
            "return_60d": asset.metrics.get("return_60d"),
        },
        explanation_sections=sections,
    )


@router.post("/data/sync")
async def run_short_research_data_sync(
    payload: ShortResearchDataSyncRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    to_date = payload.to_date or date.today()
    from_date = payload.from_date or (to_date - timedelta(days=payload.days))
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    return await sync_short_research_data(
        session,
        from_date=from_date,
        to_date=to_date,
        asset_type=payload.asset_type,
        codes=payload.codes,
    )


@router.post("/signals/run", response_model=ShortResearchSignalRunOut)
async def run_short_research_signals(
    payload: ShortResearchSignalRunRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchSignalRunOut:
    run = await run_signal_generation(
        session,
        as_of_date=payload.as_of_date,
        asset_type=payload.asset_type,
        theme=payload.theme,
        codes=payload.codes,
    )
    return await _signal_run_out(session, run)


@router.get("/signals/latest", response_model=ShortResearchSignalRunOut | None)
async def get_latest_short_research_signals(
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchSignalRunOut | None:
    run = await latest_signal_run(session)
    if run is None:
        return None
    return await _signal_run_out(session, run)
