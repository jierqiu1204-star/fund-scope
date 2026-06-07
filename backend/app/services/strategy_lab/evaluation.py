from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt
from statistics import median
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    Fund,
    FundNavHistory,
    StrategyDefinition,
    StrategyEvaluation,
    StrategyEvaluationItem,
    utcnow,
)
from app.services.recommendations.constants import ASSET_TYPE_FUND
from app.services.strategy_lab.engine import (
    STRATEGY_TYPE_DCA,
    STRATEGY_TYPE_MOMENTUM,
    SimulatedEquityPoint,
    SimulationResult,
    _latest_price,
    _metrics,
    _nav_map,
    _shared_dates,
    default_config,
    simulate_strategy_config,
)

LOOKBACK_GRID = [40, 60, 90, 120]
TOP_N_GRID = [3, 5, 8]
MAX_PE_PERCENTILE_GRID = [70, 80, 90]
MIN_SUFFICIENT_DAYS = 730

ITEM_TYPE_PARAMETER_GRID = "parameter_grid"
ITEM_TYPE_BASELINE_DEFAULT = "baseline_default"
ITEM_TYPE_BASELINE_EQUAL_WEIGHT = "baseline_equal_weight"
ITEM_TYPE_BASELINE_DCA = "baseline_dca"


@dataclass(frozen=True)
class EvaluationItemDraft:
    rank_order: int
    item_type: str
    label: str
    parameters: dict[str, Any]
    metrics: dict[str, Any]
    in_sample_metrics: dict[str, Any]
    out_of_sample_metrics: dict[str, Any]
    rolling_windows: list[dict[str, Any]]
    score: float
    risk_flags: list[str]


async def list_evaluations(session: AsyncSession) -> list[StrategyEvaluation]:
    rows = await session.scalars(
        select(StrategyEvaluation).order_by(StrategyEvaluation.created_at.desc(), StrategyEvaluation.id.desc())
    )
    return list(rows.all())


async def get_evaluation(session: AsyncSession, evaluation_id: int) -> StrategyEvaluation | None:
    return cast(
        StrategyEvaluation | None,
        await session.scalar(select(StrategyEvaluation).where(StrategyEvaluation.id == evaluation_id)),
    )


async def list_evaluation_items(session: AsyncSession, evaluation_id: int) -> list[StrategyEvaluationItem]:
    rows = await session.scalars(
        select(StrategyEvaluationItem)
        .where(StrategyEvaluationItem.evaluation_id == evaluation_id)
        .order_by(StrategyEvaluationItem.rank_order.asc(), StrategyEvaluationItem.id.asc())
    )
    return list(rows.all())


async def run_strategy_evaluation(
    session: AsyncSession,
    strategy: StrategyDefinition,
    *,
    start_date: date,
    end_date: date,
) -> StrategyEvaluation:
    if strategy.asset_type != ASSET_TYPE_FUND:
        raise ValueError("第一版策略评估只支持基金和 ETF。")
    if strategy.strategy_type != STRATEGY_TYPE_MOMENTUM:
        raise ValueError("第一版可靠性评估只支持动量轮动策略。")
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期。")

    config = default_config(strategy.strategy_type, strategy.config_json)
    asset_codes = await _asset_codes(session, config)
    coverage = await _data_coverage(session, asset_codes, start_date, end_date)
    if int(coverage["nav_rows"]) <= 0:
        raise ValueError("没有足够的基金净值，先回填历史净值后再评估。")

    split_date = start_date + timedelta(days=max((end_date - start_date).days // 2, 0))
    items = await _build_evaluation_items(session, strategy, config, asset_codes, start_date, end_date, split_date)
    summary, conclusion, risk_flags = _summarize_evaluation(coverage, items)
    now = utcnow()
    evaluation = StrategyEvaluation(
        strategy_id=strategy.id,
        status="success",
        started_at=now,
        finished_at=utcnow(),
        start_date=start_date,
        end_date=end_date,
        data_coverage_json=coverage,
        summary_json=summary,
        conclusion=conclusion,
        risk_flags_json=risk_flags,
        created_at=now,
    )
    session.add(evaluation)
    await session.flush()
    session.add_all(
        [
            StrategyEvaluationItem(
                evaluation_id=evaluation.id,
                rank_order=item.rank_order,
                item_type=item.item_type,
                label=item.label,
                parameters_json=item.parameters,
                metrics_json=item.metrics,
                in_sample_metrics_json=item.in_sample_metrics,
                out_of_sample_metrics_json=item.out_of_sample_metrics,
                rolling_windows_json=item.rolling_windows,
                score=item.score,
                risk_flags_json=item.risk_flags,
            )
            for item in items
        ]
    )
    await session.commit()
    await session.refresh(evaluation)
    return evaluation


async def _asset_codes(session: AsyncSession, config: dict[str, Any]) -> list[str]:
    configured = config.get("asset_codes") or config.get("target_asset_codes")
    if configured:
        return [str(code) for code in configured]
    rows = await session.scalars(select(Fund.code).where(Fund.is_watchlist.is_(True)).order_by(Fund.code.asc()))
    return list(rows.all())


async def _data_coverage(
    session: AsyncSession,
    asset_codes: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    available_start, available_end, nav_rows, distinct_dates = (
        await session.execute(
            select(
                func.min(FundNavHistory.nav_date),
                func.max(FundNavHistory.nav_date),
                func.count(FundNavHistory.id),
                func.count(func.distinct(FundNavHistory.nav_date)),
            ).where(FundNavHistory.fund_code.in_(asset_codes))
        )
    ).one()
    available_days = 0
    if available_start is not None and available_end is not None:
        available_days = (available_end - available_start).days + 1
    return {
        "fund_count": len(asset_codes),
        "nav_rows": int(nav_rows or 0),
        "distinct_nav_dates": int(distinct_dates or 0),
        "available_start_date": available_start.isoformat() if available_start else None,
        "available_end_date": available_end.isoformat() if available_end else None,
        "requested_start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "available_days": available_days,
        "required_days": MIN_SUFFICIENT_DAYS,
        "is_sample_sufficient": available_days >= MIN_SUFFICIENT_DAYS,
    }


async def _build_evaluation_items(
    session: AsyncSession,
    strategy: StrategyDefinition,
    base_config: dict[str, Any],
    asset_codes: list[str],
    start_date: date,
    end_date: date,
    split_date: date,
) -> list[EvaluationItemDraft]:
    items: list[EvaluationItemDraft] = []
    rank_order = 1
    default_result = await simulate_strategy_config(
        session,
        strategy_type=strategy.strategy_type,
        asset_type=strategy.asset_type,
        config=base_config,
        start_date=start_date,
        end_date=end_date,
    )
    items.append(
        _item_from_result(
            rank_order=rank_order,
            item_type=ITEM_TYPE_BASELINE_DEFAULT,
            label="默认策略",
            parameters=_public_parameters(base_config),
            result=default_result,
            split_date=split_date,
        )
    )
    rank_order += 1

    equal_weight_result = await _simulate_equal_weight(session, base_config, asset_codes, start_date, end_date)
    items.append(
        _item_from_result(
            rank_order=rank_order,
            item_type=ITEM_TYPE_BASELINE_EQUAL_WEIGHT,
            label="等权买入持有",
            parameters={"asset_count": len(asset_codes), "initial_cash": base_config["initial_cash"]},
            result=equal_weight_result,
            split_date=split_date,
        )
    )
    rank_order += 1

    dca_result = await _simulate_dca_baseline(session, base_config, asset_codes, start_date, end_date)
    items.append(
        _item_from_result(
            rank_order=rank_order,
            item_type=ITEM_TYPE_BASELINE_DCA,
            label="定投对照",
            parameters={
                "asset_count": len(asset_codes),
                "monthly_amount": _monthly_dca_amount(base_config, start_date, end_date),
            },
            result=dca_result,
            split_date=split_date,
        )
    )
    rank_order += 1

    for lookback_days in LOOKBACK_GRID:
        for top_n in TOP_N_GRID:
            for max_pe_percentile in MAX_PE_PERCENTILE_GRID:
                config = dict(base_config)
                config.update(
                    {
                        "lookback_days": lookback_days,
                        "top_n": top_n,
                        "max_pe_percentile": max_pe_percentile,
                    }
                )
                result = await simulate_strategy_config(
                    session,
                    strategy_type=strategy.strategy_type,
                    asset_type=strategy.asset_type,
                    config=config,
                    start_date=start_date,
                    end_date=end_date,
                )
                items.append(
                    _item_from_result(
                        rank_order=rank_order,
                        item_type=ITEM_TYPE_PARAMETER_GRID,
                        label=f"动量{lookback_days}天 Top {top_n} 估值{max_pe_percentile}%",
                        parameters=_public_parameters(config),
                        result=result,
                        split_date=split_date,
                    )
                )
                rank_order += 1
    return items


def _public_parameters(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "lookback_days": int(config["lookback_days"]),
        "top_n": int(config["top_n"]),
        "max_pe_percentile": float(config["max_pe_percentile"]),
        "initial_cash": float(config["initial_cash"]),
        "fee_rate": float(config["fee_rate"]),
    }


def _item_from_result(
    *,
    rank_order: int,
    item_type: str,
    label: str,
    parameters: dict[str, Any],
    result: SimulationResult,
    split_date: date,
) -> EvaluationItemDraft:
    metrics = _metrics_with_trades(result)
    in_sample = _period_metrics(result.equity_curve, None, split_date)
    out_of_sample = _period_metrics(result.equity_curve, split_date + timedelta(days=1), None)
    rolling_windows = _rolling_windows(result.equity_curve)
    risk_flags = _item_risk_flags(metrics, in_sample, out_of_sample)
    return EvaluationItemDraft(
        rank_order=rank_order,
        item_type=item_type,
        label=label,
        parameters=parameters,
        metrics=metrics,
        in_sample_metrics=in_sample,
        out_of_sample_metrics=out_of_sample,
        rolling_windows=rolling_windows,
        score=_score(metrics, in_sample, out_of_sample),
        risk_flags=risk_flags,
    )


def _metrics_with_trades(result: SimulationResult) -> dict[str, Any]:
    metrics = dict(result.metrics)
    if result.orders:
        metrics["trade_count"] = len(result.orders)
        metrics["total_fees"] = round(sum(order.fee for order in result.orders), 2)
    else:
        metrics.setdefault("trade_count", 0)
        metrics.setdefault("total_fees", 0.0)
    metrics["equity_points"] = len(result.equity_curve)
    return metrics


def _period_metrics(
    equity_curve: list[SimulatedEquityPoint],
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    points = [
        point
        for point in equity_curve
        if (start_date is None or point.curve_date >= start_date) and (end_date is None or point.curve_date <= end_date)
    ]
    if len(points) < 2:
        return {
            "start_date": points[0].curve_date.isoformat() if points else None,
            "end_date": points[-1].curve_date.isoformat() if points else None,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "annualized_volatility": 0.0,
            "observation_count": len(points),
        }
    returns = []
    peak = points[0].equity
    max_drawdown = 0.0
    for index in range(1, len(points)):
        previous = points[index - 1].equity
        current = points[index].equity
        if previous > 0:
            returns.append(current / previous - 1.0)
        peak = max(peak, current)
        if peak > 0:
            max_drawdown = min(max_drawdown, current / peak - 1.0)
    average = sum(returns) / len(returns) if returns else 0.0
    variance = sum((item - average) ** 2 for item in returns) / len(returns) if returns else 0.0
    return {
        "start_date": points[0].curve_date.isoformat(),
        "end_date": points[-1].curve_date.isoformat(),
        "total_return": round(points[-1].equity / points[0].equity - 1.0, 6) if points[0].equity > 0 else 0.0,
        "max_drawdown": round(max_drawdown, 6),
        "annualized_volatility": round(sqrt(variance) * sqrt(252), 6) if returns else 0.0,
        "observation_count": len(points),
    }


def _rolling_windows(equity_curve: list[SimulatedEquityPoint]) -> list[dict[str, Any]]:
    if len(equity_curve) < 2:
        return []
    windows: list[dict[str, Any]] = []
    first = equity_curve[0].curve_date
    last = equity_curve[-1].curve_date
    current = first
    while current <= last:
        window_end = min(current + timedelta(days=90), last)
        metrics = _period_metrics(equity_curve, current, window_end)
        if int(metrics["observation_count"]) >= 2:
            windows.append(metrics)
        current += timedelta(days=30)
    if not windows:
        windows.append(_period_metrics(equity_curve, first, last))
    return windows


def _item_risk_flags(
    metrics: dict[str, Any],
    in_sample_metrics: dict[str, Any],
    out_of_sample_metrics: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    if float(metrics.get("max_drawdown", 0.0)) <= -0.2:
        flags.append("最大回撤偏高")
    if float(out_of_sample_metrics.get("total_return", 0.0)) + 0.05 < float(
        in_sample_metrics.get("total_return", 0.0)
    ):
        flags.append("样本外走弱")
    if float(metrics.get("total_fees", 0.0)) > float(metrics.get("starting_equity", 1.0)) * 0.02:
        flags.append("手续费影响偏高")
    return flags


def _score(
    metrics: dict[str, Any],
    in_sample_metrics: dict[str, Any],
    out_of_sample_metrics: dict[str, Any],
) -> float:
    total_return = float(metrics.get("total_return", 0.0))
    drawdown = abs(float(metrics.get("max_drawdown", 0.0)))
    volatility = float(metrics.get("annualized_volatility", 0.0))
    out_of_sample = float(out_of_sample_metrics.get("total_return", 0.0))
    in_sample = float(in_sample_metrics.get("total_return", 0.0))
    overfit_penalty = max(in_sample - out_of_sample, 0.0)
    return round(total_return * 100 - drawdown * 60 - volatility * 12 - overfit_penalty * 40, 4)


async def _simulate_equal_weight(
    session: AsyncSession,
    config: dict[str, Any],
    asset_codes: list[str],
    start_date: date,
    end_date: date,
) -> SimulationResult:
    navs = await _nav_map(session, asset_codes, start_date, end_date)
    dates = _shared_dates(navs, start_date, end_date)
    initial_cash = float(config["initial_cash"])
    fee_rate = float(config["fee_rate"])
    if not dates:
        return SimulationResult(
            _metrics(initial_cash, [], {"trade_count": 0, "total_fees": 0.0}),
            [],
            [],
            [],
        )
    first = dates[0]
    investable = [code for code in asset_codes if _latest_price(navs.get(code, {}), first) is not None]
    if not investable:
        return SimulationResult(
            _metrics(initial_cash, [], {"trade_count": 0, "total_fees": 0.0}),
            [],
            [],
            [],
        )
    cash = initial_cash
    amount_per_asset = initial_cash / len(investable)
    holdings: dict[str, float] = {}
    total_fees = 0.0
    for asset_code in investable:
        latest = _latest_price(navs[asset_code], first)
        if latest is None:
            continue
        price = latest[1]
        fee = amount_per_asset * fee_rate
        total_fees += fee
        holdings[asset_code] = (amount_per_asset - fee) / price
        cash -= amount_per_asset
    equity_curve = _equity_curve_from_holdings(navs, dates, holdings, cash)
    return SimulationResult(
        _metrics(
            initial_cash,
            equity_curve,
            {
                "trade_count": len(holdings),
                "total_fees": round(total_fees, 2),
                "baseline": "equal_weight_buy_hold",
            },
        ),
        [],
        [],
        equity_curve,
    )


def _equity_curve_from_holdings(
    navs: dict[str, dict[date, float]],
    dates: list[date],
    holdings: dict[str, float],
    cash: float,
) -> list[SimulatedEquityPoint]:
    peak = max(cash, 0.0)
    curve: list[SimulatedEquityPoint] = []
    for current in dates:
        equity = cash
        for asset_code, shares in holdings.items():
            latest = _latest_price(navs.get(asset_code, {}), current)
            if latest is not None:
                equity += shares * latest[1]
        peak = max(peak, equity)
        drawdown = 0.0 if peak <= 0 else equity / peak - 1.0
        curve.append(
            SimulatedEquityPoint(
                curve_date=current,
                equity=round(equity, 2),
                cash=round(cash, 2),
                drawdown=round(drawdown, 6),
            )
        )
    return curve


async def _simulate_dca_baseline(
    session: AsyncSession,
    config: dict[str, Any],
    asset_codes: list[str],
    start_date: date,
    end_date: date,
) -> SimulationResult:
    dca_config = {
        "target_asset_codes": asset_codes,
        "monthly_amount": _monthly_dca_amount(config, start_date, end_date),
        "day_of_month": 1,
        "fee_rate": float(config["fee_rate"]),
        "platform_profile": config.get("platform_profile", "generic"),
    }
    return await simulate_strategy_config(
        session,
        strategy_type=STRATEGY_TYPE_DCA,
        asset_type=ASSET_TYPE_FUND,
        config=dca_config,
        start_date=start_date,
        end_date=end_date,
    )


def _monthly_dca_amount(config: dict[str, Any], start_date: date, end_date: date) -> float:
    months = (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1
    return round(float(config["initial_cash"]) / max(months, 1), 2)


def _summarize_evaluation(
    coverage: dict[str, Any],
    items: list[EvaluationItemDraft],
) -> tuple[dict[str, Any], str, list[str]]:
    parameter_items = [item for item in items if item.item_type == ITEM_TYPE_PARAMETER_GRID]
    default_item = next(item for item in items if item.item_type == ITEM_TYPE_BASELINE_DEFAULT)
    best_item = max(parameter_items, key=lambda item: item.score) if parameter_items else default_item
    returns = [float(item.metrics.get("total_return", 0.0)) for item in parameter_items]
    positive_ratio = sum(1 for item in returns if item > 0) / len(returns) if returns else 0.0
    return_median = median(returns) if returns else 0.0
    default_out = float(default_item.out_of_sample_metrics.get("total_return", 0.0))
    default_in = float(default_item.in_sample_metrics.get("total_return", 0.0))
    risk_flags: list[str] = []
    if not bool(coverage["is_sample_sufficient"]):
        risk_flags.append("样本不足")
    if default_out + 0.05 < default_in:
        risk_flags.append("疑似过拟合")
    if positive_ratio < 0.4 or abs(float(best_item.metrics.get("total_return", 0.0)) - return_median) > 0.2:
        risk_flags.append("参数不稳定")
    if float(default_item.metrics.get("max_drawdown", 0.0)) <= -0.2:
        risk_flags.append("风险偏高")
    if not risk_flags:
        conclusion = "可观察"
    elif "样本不足" in risk_flags:
        conclusion = "样本不足，继续观察"
    elif "风险偏高" in risk_flags or "疑似过拟合" in risk_flags:
        conclusion = "谨慎"
    else:
        conclusion = "不建议采用"
    summary = {
        "parameter_grid_count": len(parameter_items),
        "baseline_names": ["默认策略", "等权买入持有", "定投对照"],
        "best_parameter_label": best_item.label,
        "best_parameter_score": best_item.score,
        "positive_parameter_ratio": round(positive_ratio, 4),
        "median_parameter_return": round(return_median, 6),
        "default_metrics": default_item.metrics,
        "default_in_sample_return": default_in,
        "default_out_of_sample_return": default_out,
    }
    return summary, conclusion, risk_flags
