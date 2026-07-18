from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import (
    EtfDataHealth,
    ShortEtfPaperPortfolio,
    ShortEtfReliabilityEvaluation,
    ShortEtfSignalItem,
    ShortEtfSignalReview,
    ShortEtfSignalRun,
    TradableEtf,
)
from app.schemas.short_etf import (
    EtfDataHealthOut,
    EtfDataStatusOut,
    EtfDataSyncRequest,
    EtfUniverseItemOut,
    EtfUniverseResponse,
    ShortEtfEvaluationItemOut,
    ShortEtfEvaluationOut,
    ShortEtfEvaluationRequest,
    ShortEtfPaperEquityPointOut,
    ShortEtfPaperOrderOut,
    ShortEtfPaperOut,
    ShortEtfPaperPositionOut,
    ShortEtfPaperRunRequest,
    ShortEtfPaperStartRequest,
    ShortEtfReviewItemOut,
    ShortEtfReviewOut,
    ShortEtfSignalItemOut,
    ShortEtfSignalRunOut,
    ShortEtfSignalRunRequest,
)
from app.services.short_etf.data import (
    data_status_summary,
    latest_etf_price,
    list_etf_data_health,
    list_short_etfs,
    retry_failed_or_stale_etf_data,
    sync_etf_price_history,
)
from app.services.short_etf.evaluation import (
    get_evaluation,
    list_evaluation_items,
    list_evaluations,
    run_reliability_evaluation,
)
from app.services.short_etf.paper import (
    get_paper_portfolio,
    latest_etf_names,
    list_equity_curve,
    list_paper_orders,
    paper_summary,
    run_paper_update,
    start_paper_portfolio,
)
from app.services.short_etf.signals import (
    generate_signal_review,
    get_review_for_run,
    get_signal_run,
    latest_signal_run,
    list_review_items,
    list_signal_items,
    run_signal_generation,
)
from app.services.workflows.etf_history_readiness import read_etf_history_readiness

router = APIRouter(prefix="/api/short-etf", tags=["short-etf"])


async def _etf_name_map(session: AsyncSession) -> dict[str, TradableEtf]:
    rows = await session.scalars(select(TradableEtf))
    return {row.code: row for row in rows.all()}


async def _signal_item_out(
    session: AsyncSession,
    item: ShortEtfSignalItem,
    etf_map: dict[str, TradableEtf] | None = None,
) -> ShortEtfSignalItemOut:
    etf_map = etf_map or await _etf_name_map(session)
    etf = etf_map.get(item.etf_code)
    return ShortEtfSignalItemOut(
        id=item.id,
        etf_code=item.etf_code,
        etf_name=etf.name if etf else None,
        rank=item.rank,
        total_score=round(item.total_score, 2),
        conclusion=item.conclusion,
        score_breakdown=item.score_breakdown_json,
        risk_flags=item.risk_flags_json,
        rationale=item.rationale_json,
        theme_tags=etf.theme_tags_json if etf else [],
        trading_rule_label=etf.trading_rule_label if etf else None,
    )


async def _signal_run_out(session: AsyncSession, run: ShortEtfSignalRun) -> ShortEtfSignalRunOut:
    etf_map = await _etf_name_map(session)
    return ShortEtfSignalRunOut(
        id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        as_of_date=run.as_of_date,
        config=run.config_json,
        summary=run.summary_json,
        error_message=run.error_message,
        items=[await _signal_item_out(session, item, etf_map) for item in await list_signal_items(session, run.id)],
    )


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


async def _review_out(session: AsyncSession, review: ShortEtfSignalReview) -> ShortEtfReviewOut:
    names = await latest_etf_names(session)
    items = [
        ShortEtfReviewItemOut(
            id=item.id,
            signal_item_id=item.signal_item_id,
            etf_code=item.etf_code,
            etf_name=names.get(item.etf_code),
            rank=item.rank,
            total_score=round(item.total_score, 2),
            verdict=item.verdict,
            agent_notes=item.agent_notes_json,
            risk_flags=item.risk_flags_json,
        )
        for item in await list_review_items(session, review.id)
    ]
    return ShortEtfReviewOut(
        id=review.id,
        run_id=review.run_id,
        status=review.status,
        model_name=review.model_name,
        started_at=review.started_at,
        finished_at=review.finished_at,
        summary=review.summary_json,
        error_message=review.error_message,
        items=items,
    )


async def _evaluation_out(
    session: AsyncSession,
    evaluation: ShortEtfReliabilityEvaluation,
    *,
    include_items: bool = True,
) -> ShortEtfEvaluationOut:
    items: list[ShortEtfEvaluationItemOut] = []
    if include_items:
        items = [
            ShortEtfEvaluationItemOut(
                id=item.id,
                rank_order=item.rank_order,
                label=item.label,
                item_type=item.item_type,
                parameters=item.parameters_json,
                metrics=item.metrics_json,
                baseline_metrics=item.baseline_metrics_json,
                score=round(item.score, 4),
                risk_flags=item.risk_flags_json,
            )
            for item in await list_evaluation_items(session, evaluation.id)
        ]
    return ShortEtfEvaluationOut(
        id=evaluation.id,
        status=evaluation.status,
        started_at=evaluation.started_at,
        finished_at=evaluation.finished_at,
        start_date=evaluation.start_date,
        end_date=evaluation.end_date,
        sample_days=evaluation.sample_days,
        conclusion=evaluation.conclusion,
        data_coverage=evaluation.data_coverage_json,
        summary=evaluation.summary_json,
        risk_flags=evaluation.risk_flags_json,
        error_message=evaluation.error_message,
        items=items,
    )


async def _paper_out(session: AsyncSession, paper: ShortEtfPaperPortfolio) -> ShortEtfPaperOut:
    names = await latest_etf_names(session)
    orders = [
        ShortEtfPaperOrderOut(
            id=order.id,
            signal_run_id=order.signal_run_id,
            trade_date=order.trade_date,
            etf_code=order.etf_code,
            etf_name=names.get(order.etf_code),
            side=order.side,
            amount=round(order.amount, 2),
            shares=round(order.shares, 6),
            price=order.price,
            fee=round(order.fee, 2),
            status=order.status,
        )
        for order in await list_paper_orders(session, paper.id)
    ]
    positions = [
        ShortEtfPaperPositionOut(etf_name=names.get(str(item["etf_code"])), **item)
        for item in paper.config_json.get("latest_positions", [])
    ]
    equity_curve = [
        ShortEtfPaperEquityPointOut(
            id=point.id,
            curve_date=point.curve_date,
            equity=round(point.equity, 2),
            cash=round(point.cash, 2),
            drawdown=point.drawdown,
        )
        for point in await list_equity_curve(session, paper.id)
    ]
    return ShortEtfPaperOut(
        id=paper.id,
        name=paper.name,
        status=paper.status,
        started_at=paper.started_at,
        cash=round(paper.cash, 2),
        latest_equity=round(paper.latest_equity, 2),
        summary=paper_summary(paper),
        positions=positions,
        orders=orders,
        equity_curve=equity_curve,
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
    summary["history_readiness"] = await read_etf_history_readiness(session)
    return EtfDataStatusOut(
        summary=summary,
        items=[_data_health_out(etf, health, stale) for etf, health, stale in rows],
    )


@router.post("/data/retry-failed")
async def retry_short_etf_failed_data(session: AsyncSession = Depends(get_db_session)) -> dict[str, object]:
    return await retry_failed_or_stale_etf_data(session)


@router.post("/signals/run", response_model=ShortEtfSignalRunOut)
async def run_short_etf_signals(
    payload: ShortEtfSignalRunRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfSignalRunOut:
    run = await run_signal_generation(session, as_of_date=payload.as_of_date, codes=payload.codes)
    return await _signal_run_out(session, run)


@router.get("/signals/latest", response_model=ShortEtfSignalRunOut | None)
async def get_latest_short_etf_signals(
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfSignalRunOut | None:
    run = await latest_signal_run(session)
    return await _signal_run_out(session, run) if run is not None else None


@router.post("/signals/{run_id}/review", response_model=ShortEtfReviewOut)
async def create_short_etf_review(
    run_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfReviewOut:
    run = await get_signal_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="短线 ETF 信号不存在")
    review = await generate_signal_review(session, run)
    return await _review_out(session, review)


@router.get("/signals/{run_id}/review", response_model=ShortEtfReviewOut)
async def get_short_etf_review(
    run_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfReviewOut:
    run = await get_signal_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="短线 ETF 信号不存在")
    review = await get_review_for_run(session, run.id)
    if review is None:
        raise HTTPException(status_code=404, detail="短线 ETF 审查报告不存在")
    return await _review_out(session, review)


@router.post("/evaluations", response_model=ShortEtfEvaluationOut)
async def create_short_etf_evaluation(
    payload: ShortEtfEvaluationRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfEvaluationOut:
    evaluation = await run_reliability_evaluation(
        session,
        start_date=payload.start_date,
        end_date=payload.end_date,
        fee_rate=payload.fee_rate,
    )
    return await _evaluation_out(session, evaluation)


@router.get("/evaluations", response_model=list[ShortEtfEvaluationOut])
async def list_short_etf_evaluations(
    session: AsyncSession = Depends(get_db_session),
) -> list[ShortEtfEvaluationOut]:
    return [await _evaluation_out(session, evaluation, include_items=False) for evaluation in await list_evaluations(session)]


@router.get("/evaluations/{evaluation_id}", response_model=ShortEtfEvaluationOut)
async def get_short_etf_evaluation(
    evaluation_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfEvaluationOut:
    evaluation = await get_evaluation(session, evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="短线 ETF 可靠性评估不存在")
    return await _evaluation_out(session, evaluation)


@router.post("/paper/start", response_model=ShortEtfPaperOut)
async def start_short_etf_paper(
    payload: ShortEtfPaperStartRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfPaperOut:
    paper = await start_paper_portfolio(
        session,
        name=payload.name,
        started_at=payload.started_at,
        initial_cash=payload.initial_cash,
    )
    return await _paper_out(session, paper)


@router.get("/paper", response_model=list[ShortEtfPaperOut])
async def list_short_etf_papers(session: AsyncSession = Depends(get_db_session)) -> list[ShortEtfPaperOut]:
    papers = (
        await session.scalars(
            select(ShortEtfPaperPortfolio).order_by(
                ShortEtfPaperPortfolio.updated_at.desc(),
                ShortEtfPaperPortfolio.id.desc(),
            )
        )
    ).all()
    return [await _paper_out(session, paper) for paper in papers]


@router.post("/paper/{paper_id}/run", response_model=ShortEtfPaperOut)
async def run_short_etf_paper(
    paper_id: int,
    payload: ShortEtfPaperRunRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfPaperOut:
    paper = await get_paper_portfolio(session, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="短线 ETF 模拟盘不存在")
    paper = await run_paper_update(session, paper, as_of_date=payload.as_of_date)
    return await _paper_out(session, paper)


@router.get("/paper/{paper_id}", response_model=ShortEtfPaperOut)
async def get_short_etf_paper(
    paper_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> ShortEtfPaperOut:
    paper = await get_paper_portfolio(session, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="短线 ETF 模拟盘不存在")
    return await _paper_out(session, paper)
