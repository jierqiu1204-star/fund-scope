from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import ShortResearchSignalRun
from app.schemas.short_research import (
    ShortResearchAdvisorReportOut,
    ShortResearchAdvisorRunRequest,
    ShortResearchAssetDetailOut,
    ShortResearchAssetListOut,
    ShortResearchAssetOut,
    ShortResearchChartPointOut,
    ShortResearchDataSyncRequest,
    ShortResearchObservationPortfolioOut,
    ShortResearchSignalRunOut,
    ShortResearchSignalRunRequest,
    ShortResearchStatusOut,
)
from app.services.short_research.advisor import latest_reports_by_asset, run_advisor_generation
from app.services.short_research.service import (
    ComputedAsset,
    etf_observation_portfolio,
    get_asset_detail,
    latest_signal_run,
    list_computed_assets,
    run_signal_generation,
    signal_run_items_as_assets,
    status_summary,
    sync_short_research_data,
)

router = APIRouter(prefix="/api/short-research", tags=["short-research"])


def _advisor_report_out(report: Any | None) -> ShortResearchAdvisorReportOut | None:
    if report is None:
        return None
    return ShortResearchAdvisorReportOut(
        id=report.id,
        status=report.status,
        action_label=report.action_label,
        plain_summary=report.plain_summary,
        opportunity=list(report.opportunity_json),
        risks=list(report.risks_json),
        opposing_view=report.opposing_view,
        watch_conditions=list(report.watch_conditions_json),
        holding_note=report.holding_note,
        data_limitations=report.data_limitations,
        model_name=report.model_name,
        prompt_version=report.prompt_version,
        source=report.source,
        generated_at=report.generated_at,
    )


def _asset_out(asset: ComputedAsset, advisor_report: Any | None = None) -> ShortResearchAssetOut:
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
        advisor_report=_advisor_report_out(advisor_report),
    )


async def _signal_run_out(
    session: AsyncSession,
    run: ShortResearchSignalRun,
    *,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
) -> ShortResearchSignalRunOut:
    assets = await signal_run_items_as_assets(session, run)
    if asset_type is not None:
        assets = [item for item in assets if item.metadata.asset_type == asset_type]
    if theme is not None:
        assets = [item for item in assets if theme in item.metadata.theme_tags]
    if codes is not None:
        code_set = set(codes)
        assets = [item for item in assets if item.metadata.code in code_set]
    advisor_reports = await latest_reports_by_asset(session, run.id)
    summary = dict(run.summary_json or {})
    if asset_type is not None or theme is not None or codes is not None:
        summary["item_count"] = len(assets)
        summary["fund_count"] = sum(1 for item in assets if item.metadata.asset_type == "fund")
        summary["etf_count"] = sum(1 for item in assets if item.metadata.asset_type == "etf")
    else:
        summary.setdefault("item_count", len(assets))
        summary.setdefault("fund_count", sum(1 for item in assets if item.metadata.asset_type == "fund"))
        summary.setdefault("etf_count", sum(1 for item in assets if item.metadata.asset_type == "etf"))
    return ShortResearchSignalRunOut(
        id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        as_of_date=run.as_of_date,
        config=run.config_json,
        summary=summary,
        error_message=run.error_message,
        items=[
            _asset_out(
                item,
                advisor_reports.get((item.metadata.asset_type, item.metadata.code)),
            )
            for item in assets
        ],
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
    universe: str = Query(default="default"),
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchAssetListOut:
    try:
        assets = await list_computed_assets(
            session,
            asset_type=asset_type,
            theme=theme,
            sort=sort,
            universe=universe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if q:
        keyword = q.strip().lower()
        assets = [
            item
            for item in assets
            if keyword in item.metadata.code.lower() or keyword in item.metadata.name.lower()
        ]
    run = await latest_signal_run(session, asset_type=asset_type, theme=theme)
    advisor_reports = await latest_reports_by_asset(session, run.id) if run is not None else {}
    return ShortResearchAssetListOut(
        items=[
            _asset_out(
                item,
                advisor_reports.get((item.metadata.asset_type, item.metadata.code)),
            )
            for item in assets
        ],
        total=len(assets),
    )


@router.get("/observation-portfolio", response_model=ShortResearchObservationPortfolioOut)
async def get_short_research_observation_portfolio(
    asset_type: str = Query(default="etf"),
    limit: int = Query(default=5, ge=1, le=10),
    universe: str = Query(default="default"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    if asset_type != "etf":
        raise HTTPException(status_code=400, detail="观察组合第一版只支持场内 ETF")
    return await etf_observation_portfolio(session, limit=limit, universe=universe)


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
    run = await latest_signal_run(session, asset_type=asset_type)
    advisor_reports = await latest_reports_by_asset(session, run.id) if run is not None else {}
    return ShortResearchAssetDetailOut(
        asset=_asset_out(asset, advisor_reports.get((asset.metadata.asset_type, asset.metadata.code))),
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
    return await _signal_run_out(
        session,
        run,
        asset_type=payload.asset_type,
        theme=payload.theme,
        codes=payload.codes,
    )


@router.post("/advisor/run")
async def run_short_research_advisor(
    request: Request,
    payload: ShortResearchAdvisorRunRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    payload = payload or ShortResearchAdvisorRunRequest()
    return await run_advisor_generation(
        session,
        request.app.state.settings,
        asset_type=payload.asset_type,
        theme=payload.theme,
        codes=payload.codes,
        as_of_date=payload.as_of_date,
    )


@router.get("/signals/latest", response_model=ShortResearchSignalRunOut | None)
async def get_latest_short_research_signals(
    asset_type: str | None = Query(default=None),
    theme: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchSignalRunOut | None:
    run = await latest_signal_run(session, asset_type=asset_type, theme=theme)
    if run is None:
        return None
    return await _signal_run_out(session, run, asset_type=asset_type, theme=theme)
