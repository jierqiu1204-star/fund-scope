from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import (
    EtfSignalValidationItem,
    EtfSignalValidationRun,
    ShortResearchSignalRun,
)
from app.schemas.short_research import (
    EtfSignalValidationItemOut,
    EtfSignalValidationRunOut,
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
    cached_signal_assets,
    etf_observation_portfolio,
    get_asset_detail,
    latest_signal_run,
    latest_validation_evidence_by_label,
    run_etf_signal_validation,
    run_signal_generation,
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


def _asset_out(
    asset: ComputedAsset,
    advisor_report: Any | None = None,
    *,
    validation_evidence: dict[str, Any] | None = None,
    observation_portfolio: dict[str, Any] | None = None,
) -> ShortResearchAssetOut:
    return ShortResearchAssetOut(
        asset_type=asset.metadata.asset_type,
        code=asset.metadata.code,
        name=asset.metadata.name,
        rank=asset.rank,
        total_score=round(asset.total_score, 2),
        conclusion=asset.conclusion,
        entry_timing_label=asset.entry_timing_label,
        entry_timing_reason=asset.entry_timing_reason,
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
        validation_evidence=validation_evidence or {},
        observation_portfolio=observation_portfolio or {},
    )


def _validation_for_asset(asset: ComputedAsset, evidence_by_label: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    return evidence_by_label.get((asset.conclusion, asset.entry_timing_label), {})


def _portfolio_contexts(portfolio: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for section, status in (
        ("items", "included"),
        ("defensive_items", "defensive"),
        ("watch_only_items", "watch_only"),
        ("excluded_items", "excluded"),
    ):
        for item in portfolio.get(section, []):
            code = str(item.get("code") or "")
            if not code:
                continue
            decision_factors = item.get("decision_factors")
            if not isinstance(decision_factors, dict):
                decision_factors = {}
            result[code] = {
                "status": status,
                "target_weight": item.get("target_weight", 0.0),
                "evidence": list(item.get("evidence") or []),
                "risk_reasons": list(item.get("risk_reasons") or []),
                "exclusion_reason": item.get("exclusion_reason"),
                "weight_explanation": item.get("weight_explanation"),
                "exclusion_explanation": item.get("exclusion_explanation"),
                "decision_factors": decision_factors,
                "snapshot_id": portfolio.get("snapshot_id"),
                "generated_at": portfolio.get("generated_at"),
            }
    return result


async def _validation_run_out(session: AsyncSession, run: EtfSignalValidationRun) -> EtfSignalValidationRunOut:
    rows = (
        await session.scalars(
            select(EtfSignalValidationItem)
            .where(EtfSignalValidationItem.run_id == run.id)
            .order_by(
                EtfSignalValidationItem.label.asc(),
                EtfSignalValidationItem.entry_timing_label.asc(),
                EtfSignalValidationItem.horizon_days.asc(),
            )
        )
    ).all()
    return EtfSignalValidationRunOut(
        id=run.id,
        status=run.status,
        as_of_date=run.as_of_date,
        source_signal_run_id=run.source_signal_run_id,
        rule_version=run.rule_version,
        summary=dict(run.summary_json or {}),
        created_at=run.created_at,
        items=[
            EtfSignalValidationItemOut(
                label=row.label,
                entry_timing_label=row.entry_timing_label,
                horizon_days=row.horizon_days,
                sample_count=row.sample_count,
                excluded_count=row.excluded_count,
                avg_return=row.avg_return,
                median_return=row.median_return,
                win_rate=row.win_rate,
                worst_forward_drawdown=row.worst_forward_drawdown,
                confidence=row.confidence,
                metrics=dict(row.metrics_json or {}),
            )
            for row in rows
        ],
    )


async def _signal_run_out(
    session: AsyncSession,
    run: ShortResearchSignalRun,
    *,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
) -> ShortResearchSignalRunOut:
    assets, _total = await cached_signal_assets(
        session,
        run,
        asset_type=asset_type,
        theme=theme,
        codes=codes,
        sort="score",
        universe="all",
    )
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
async def get_short_research_status(
    include_health: bool = Query(default=False),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await status_summary(session, include_health=include_health)


@router.get("/assets", response_model=ShortResearchAssetListOut)
async def list_short_research_assets(
    asset_type: str | None = Query(default=None),
    theme: str | None = Query(default=None),
    sort: str = Query(default="score"),
    q: str | None = Query(default=None),
    universe: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ShortResearchAssetListOut:
    try:
        run = await latest_signal_run(session, asset_type=asset_type, theme=theme)
        if run is None and theme is not None:
            run = await latest_signal_run(session, asset_type=asset_type)
        if run is None:
            return ShortResearchAssetListOut(items=[], total=0)
        assets, total = await cached_signal_assets(
            session,
            run,
            asset_type=asset_type,
            theme=theme,
            q=q,
            sort=sort,
            universe=universe,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    advisor_reports = await latest_reports_by_asset(session, run.id) if run is not None else {}
    validation_by_label = await latest_validation_evidence_by_label(session) if asset_type in {None, "etf"} else {}
    portfolio_context_by_code = _portfolio_contexts(await etf_observation_portfolio(session)) if asset_type in {None, "etf"} else {}
    return ShortResearchAssetListOut(
        items=[
            _asset_out(
                item,
                advisor_reports.get((item.metadata.asset_type, item.metadata.code)),
                validation_evidence=_validation_for_asset(item, validation_by_label),
                observation_portfolio=portfolio_context_by_code.get(item.metadata.code, {}),
            )
            for item in assets
        ],
        total=total,
        generated_at=run.finished_at or run.started_at,
        as_of_date=run.as_of_date,
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


@router.post("/validation/run", response_model=EtfSignalValidationRunOut)
async def run_short_research_validation(
    session: AsyncSession = Depends(get_db_session),
) -> EtfSignalValidationRunOut:
    run = await run_etf_signal_validation(session)
    return await _validation_run_out(session, run)


@router.get("/validation/latest", response_model=EtfSignalValidationRunOut | None)
async def get_latest_short_research_validation(
    session: AsyncSession = Depends(get_db_session),
) -> EtfSignalValidationRunOut | None:
    run = await session.scalar(
        select(EtfSignalValidationRun).order_by(
            EtfSignalValidationRun.as_of_date.desc(),
            EtfSignalValidationRun.id.desc(),
        )
    )
    if run is None:
        return None
    return await _validation_run_out(session, run)


@router.get("/validation", response_model=list[EtfSignalValidationRunOut])
async def list_short_research_validations(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
) -> list[EtfSignalValidationRunOut]:
    runs = (
        await session.scalars(
            select(EtfSignalValidationRun)
            .order_by(EtfSignalValidationRun.as_of_date.desc(), EtfSignalValidationRun.id.desc())
            .limit(limit)
        )
    ).all()
    return [await _validation_run_out(session, run) for run in runs]


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
    validation_by_label = await latest_validation_evidence_by_label(session) if asset.metadata.asset_type == "etf" else {}
    portfolio_context_by_code = _portfolio_contexts(await etf_observation_portfolio(session)) if asset.metadata.asset_type == "etf" else {}
    return ShortResearchAssetDetailOut(
        asset=_asset_out(
            asset,
            advisor_reports.get((asset.metadata.asset_type, asset.metadata.code)),
            validation_evidence=_validation_for_asset(asset, validation_by_label),
            observation_portfolio=portfolio_context_by_code.get(asset.metadata.code, {}),
        ),
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
