from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import (
    Fund,
    PaperPortfolio,
    StrategyDefinition,
    StrategyEquityCurve,
    StrategyEvaluation,
    StrategyOrder,
    StrategyPosition,
    StrategyRun,
)
from app.schemas.strategy_lab import (
    BacktestRequest,
    EquityPointOut,
    EvaluationRequest,
    OrderOut,
    PaperPortfolioOut,
    PaperReconciliationItemOut,
    PaperReconciliationOut,
    PaperRunRequest,
    PaperStartRequest,
    PositionOut,
    StrategyCreate,
    StrategyEvaluationItemOut,
    StrategyEvaluationOut,
    StrategyOut,
    StrategyRunOut,
)
from app.services.strategy_lab.engine import (
    create_strategy,
    get_paper_portfolio,
    get_run,
    get_strategy,
    latest_run_for_paper,
    list_strategies,
    reconcile_paper_portfolio,
    run_backtest,
    run_paper_update,
    start_paper_portfolio,
)
from app.services.strategy_lab.evaluation import (
    get_evaluation,
    list_evaluation_items,
    list_evaluations,
    run_strategy_evaluation,
)

router = APIRouter(prefix="/api/strategy-lab", tags=["strategy-lab"])


async def _fund_name_map(session: AsyncSession, asset_codes: set[str]) -> dict[str, str]:
    if not asset_codes:
        return {}
    rows = await session.execute(select(Fund.code, Fund.name).where(Fund.code.in_(asset_codes)))
    return {code: name for code, name in rows.all()}


def _strategy_out(strategy: StrategyDefinition) -> StrategyOut:
    return StrategyOut(
        id=strategy.id,
        name=strategy.name,
        strategy_type=strategy.strategy_type,  # type: ignore[arg-type]
        asset_type=strategy.asset_type,  # type: ignore[arg-type]
        status=strategy.status,
        config=strategy.config_json,
        created_at=strategy.created_at,
        updated_at=strategy.updated_at,
    )


async def _run_out(session: AsyncSession, run: StrategyRun) -> StrategyRunOut:
    orders = (
        await session.scalars(
            select(StrategyOrder)
            .where(StrategyOrder.run_id == run.id)
            .order_by(StrategyOrder.trade_date.asc(), StrategyOrder.id.asc())
        )
    ).all()
    positions = (
        await session.scalars(
            select(StrategyPosition)
            .where(StrategyPosition.run_id == run.id)
            .order_by(StrategyPosition.snapshot_date.asc(), StrategyPosition.asset_code.asc())
        )
    ).all()
    equity_curve = (
        await session.scalars(
            select(StrategyEquityCurve)
            .where(StrategyEquityCurve.run_id == run.id)
            .order_by(StrategyEquityCurve.curve_date.asc())
        )
    ).all()
    fund_names = await _fund_name_map(
        session,
        {order.asset_code for order in orders} | {position.asset_code for position in positions},
    )
    return StrategyRunOut(
        id=run.id,
        strategy_id=run.strategy_id,
        run_type=run.run_type,  # type: ignore[arg-type]
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        as_of_date=run.as_of_date,
        date_range=run.date_range_json,
        metrics=run.metrics_json,
        error_message=run.error_message,
        orders=[
            OrderOut(
                id=order.id,
                submitted_date=order.submitted_date,
                trade_date=order.trade_date,
                confirmed_date=order.confirmed_date,
                asset_code=order.asset_code,
                asset_name=fund_names.get(order.asset_code),
                side=order.side,
                amount=order.amount,
                shares=order.shares,
                price=order.price,
                fee=order.fee,
                status=order.status,
                platform=order.platform,
            )
            for order in orders
        ],
        positions=[
            PositionOut(
                id=position.id,
                snapshot_date=position.snapshot_date,
                asset_code=position.asset_code,
                asset_name=fund_names.get(position.asset_code),
                shares=position.shares,
                market_value=position.market_value,
                weight=position.weight,
            )
            for position in positions
        ],
        equity_curve=[
            EquityPointOut(
                id=point.id,
                curve_date=point.curve_date,
                equity=point.equity,
                cash=point.cash,
                drawdown=point.drawdown,
            )
            for point in equity_curve
        ],
    )


async def _paper_out(session: AsyncSession, paper: PaperPortfolio, latest_run: StrategyRun | None = None) -> PaperPortfolioOut:
    if latest_run is None:
        latest_run = await latest_run_for_paper(session, paper)
    latest_equity = round(paper.latest_equity, 2)
    cash = round(paper.cash, 2)
    if latest_run is not None:
        ending_equity = latest_run.metrics_json.get("ending_equity")
        if isinstance(ending_equity, int | float):
            latest_equity = round(float(ending_equity), 2)
        latest_point = await session.scalar(
            select(StrategyEquityCurve)
            .where(StrategyEquityCurve.run_id == latest_run.id)
            .order_by(StrategyEquityCurve.curve_date.desc(), StrategyEquityCurve.id.desc())
        )
        if latest_point is not None:
            cash = round(latest_point.cash, 2)
    return PaperPortfolioOut(
        id=paper.id,
        strategy_id=paper.strategy_id,
        name=paper.name,
        status=paper.status,
        started_at=paper.started_at,
        cash=cash,
        latest_equity=latest_equity,
        latest_run=await _run_out(session, latest_run) if latest_run is not None else None,
    )


async def _evaluation_out(
    session: AsyncSession,
    evaluation: StrategyEvaluation,
    *,
    include_items: bool,
) -> StrategyEvaluationOut:
    items = await list_evaluation_items(session, evaluation.id) if include_items else []
    return StrategyEvaluationOut(
        id=evaluation.id,
        strategy_id=evaluation.strategy_id,
        status=evaluation.status,
        started_at=evaluation.started_at,
        finished_at=evaluation.finished_at,
        start_date=evaluation.start_date,
        end_date=evaluation.end_date,
        data_coverage=evaluation.data_coverage_json,
        summary=evaluation.summary_json,
        conclusion=evaluation.conclusion,
        risk_flags=evaluation.risk_flags_json,
        items=[
            StrategyEvaluationItemOut(
                id=item.id,
                rank_order=item.rank_order,
                item_type=item.item_type,
                label=item.label,
                parameters=item.parameters_json,
                metrics=item.metrics_json,
                in_sample_metrics=item.in_sample_metrics_json,
                out_of_sample_metrics=item.out_of_sample_metrics_json,
                rolling_windows=item.rolling_windows_json,
                score=item.score,
                risk_flags=item.risk_flags_json,
            )
            for item in items
        ],
    )


@router.get("/strategies", response_model=list[StrategyOut])
async def list_strategy_definitions(session: AsyncSession = Depends(get_db_session)) -> list[StrategyOut]:
    return [_strategy_out(strategy) for strategy in await list_strategies(session)]


@router.post("/strategies", response_model=StrategyOut)
async def create_strategy_definition(
    payload: StrategyCreate,
    session: AsyncSession = Depends(get_db_session),
) -> StrategyOut:
    try:
        strategy = await create_strategy(
            session,
            name=payload.name,
            strategy_type=payload.strategy_type,
            asset_type=payload.asset_type,
            config=payload.config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _strategy_out(strategy)


@router.get("/strategies/{strategy_id}", response_model=StrategyOut)
async def get_strategy_definition(
    strategy_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> StrategyOut:
    strategy = await get_strategy(session, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return _strategy_out(strategy)


@router.post("/strategies/{strategy_id}/backtests", response_model=StrategyRunOut)
async def run_strategy_backtest(
    strategy_id: int,
    payload: BacktestRequest,
    session: AsyncSession = Depends(get_db_session),
) -> StrategyRunOut:
    strategy = await get_strategy(session, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        run = await run_backtest(session, strategy, start_date=payload.start_date, end_date=payload.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _run_out(session, run)


@router.get("/runs/{run_id}", response_model=StrategyRunOut)
async def get_strategy_run(run_id: int, session: AsyncSession = Depends(get_db_session)) -> StrategyRunOut:
    run = await get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Strategy run not found")
    return await _run_out(session, run)


@router.post("/evaluations", response_model=StrategyEvaluationOut)
async def create_strategy_evaluation(
    payload: EvaluationRequest,
    session: AsyncSession = Depends(get_db_session),
) -> StrategyEvaluationOut:
    strategy = await get_strategy(session, payload.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        evaluation = await run_strategy_evaluation(
            session,
            strategy,
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _evaluation_out(session, evaluation, include_items=True)


@router.get("/evaluations", response_model=list[StrategyEvaluationOut])
async def list_strategy_evaluations(session: AsyncSession = Depends(get_db_session)) -> list[StrategyEvaluationOut]:
    return [
        await _evaluation_out(session, evaluation, include_items=False)
        for evaluation in await list_evaluations(session)
    ]


@router.get("/evaluations/{evaluation_id}", response_model=StrategyEvaluationOut)
async def get_strategy_evaluation(
    evaluation_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> StrategyEvaluationOut:
    evaluation = await get_evaluation(session, evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Strategy evaluation not found")
    return await _evaluation_out(session, evaluation, include_items=True)


@router.post("/strategies/{strategy_id}/paper/start", response_model=PaperPortfolioOut)
async def start_strategy_paper(
    strategy_id: int,
    payload: PaperStartRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PaperPortfolioOut:
    strategy = await get_strategy(session, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    try:
        paper = await start_paper_portfolio(session, strategy, name=payload.name, started_at=payload.started_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _paper_out(session, paper)


@router.post("/paper/{paper_id}/run", response_model=PaperPortfolioOut)
async def run_strategy_paper(
    paper_id: int,
    payload: PaperRunRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PaperPortfolioOut:
    paper = await get_paper_portfolio(session, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper portfolio not found")
    try:
        latest_run = await run_paper_update(session, paper, as_of_date=payload.as_of_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _paper_out(session, paper, latest_run)


@router.get("/paper", response_model=list[PaperPortfolioOut])
async def list_strategy_paper_portfolios(session: AsyncSession = Depends(get_db_session)) -> list[PaperPortfolioOut]:
    papers = (
        await session.scalars(
            select(PaperPortfolio).order_by(PaperPortfolio.updated_at.desc(), PaperPortfolio.id.desc())
        )
    ).all()
    return [await _paper_out(session, paper) for paper in papers]


@router.get("/paper/{paper_id}/reconciliation", response_model=PaperReconciliationOut)
async def get_strategy_paper_reconciliation(
    paper_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> PaperReconciliationOut:
    paper = await get_paper_portfolio(session, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper portfolio not found")
    try:
        result = await reconcile_paper_portfolio(session, paper)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    fund_names = await _fund_name_map(session, {str(item["asset_code"]) for item in result["items"]})
    return PaperReconciliationOut(
        paper_id=result["paper_id"],
        strategy_id=result["strategy_id"],
        as_of_date=result["as_of_date"],
        platform_profile=result["platform_profile"],
        paper_equity=result["paper_equity"],
        actual_equity=result["actual_equity"],
        equity_diff=result["equity_diff"],
        items=[
            PaperReconciliationItemOut(asset_name=fund_names.get(str(item["asset_code"])), **item)
            for item in result["items"]
        ],
        note=result["note"],
    )


@router.get("/paper/{paper_id}", response_model=PaperPortfolioOut)
async def get_strategy_paper(paper_id: int, session: AsyncSession = Depends(get_db_session)) -> PaperPortfolioOut:
    paper = await get_paper_portfolio(session, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper portfolio not found")
    return await _paper_out(session, paper)
