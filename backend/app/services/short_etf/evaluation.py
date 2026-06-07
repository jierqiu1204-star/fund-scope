from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfPriceHistory,
    ShortEtfReliabilityEvaluation,
    ShortEtfReliabilityEvaluationItem,
    utcnow,
)
from app.services.short_etf.data import list_short_etfs

EVALUATION_STATUS_SUCCESS = "success"
EVALUATION_STATUS_FAILED = "failed"
MIN_SAMPLE_DAYS = 250
DEFAULT_FEE_RATE = 0.0005
PARAMETER_GRID = (
    {"lookback_days": 5, "top_n": 1},
    {"lookback_days": 20, "top_n": 1},
    {"lookback_days": 20, "top_n": 3},
    {"lookback_days": 60, "top_n": 3},
    {"lookback_days": 60, "top_n": 5},
)


@dataclass(frozen=True)
class EvaluationCandidate:
    code: str
    lookback_return: float
    period_return: float
    max_drawdown: float


async def get_evaluation(
    session: AsyncSession,
    evaluation_id: int,
) -> ShortEtfReliabilityEvaluation | None:
    return cast(
        ShortEtfReliabilityEvaluation | None,
        await session.scalar(
            select(ShortEtfReliabilityEvaluation).where(ShortEtfReliabilityEvaluation.id == evaluation_id)
        ),
    )


async def list_evaluations(session: AsyncSession) -> list[ShortEtfReliabilityEvaluation]:
    rows = await session.scalars(
        select(ShortEtfReliabilityEvaluation).order_by(
            ShortEtfReliabilityEvaluation.started_at.desc(),
            ShortEtfReliabilityEvaluation.id.desc(),
        )
    )
    return list(rows.all())


async def list_evaluation_items(
    session: AsyncSession,
    evaluation_id: int,
) -> list[ShortEtfReliabilityEvaluationItem]:
    rows = await session.scalars(
        select(ShortEtfReliabilityEvaluationItem)
        .where(ShortEtfReliabilityEvaluationItem.evaluation_id == evaluation_id)
        .order_by(ShortEtfReliabilityEvaluationItem.rank_order.asc())
    )
    return list(rows.all())


async def _price_series(
    session: AsyncSession,
    code: str,
    start_date: date,
    end_date: date,
) -> list[EtfPriceHistory]:
    rows = await session.scalars(
        select(EtfPriceHistory)
        .where(
            EtfPriceHistory.etf_code == code,
            EtfPriceHistory.trade_date >= start_date,
            EtfPriceHistory.trade_date <= end_date,
        )
        .order_by(EtfPriceHistory.trade_date.asc())
    )
    return list(rows.all())


def _series_return(prices: list[EtfPriceHistory]) -> float:
    if len(prices) < 2 or not prices[0].close:
        return 0.0
    return prices[-1].close / prices[0].close - 1


def _lookback_return(prices: list[EtfPriceHistory], lookback_days: int) -> float:
    if len(prices) <= lookback_days or not prices[-lookback_days - 1].close:
        return 0.0
    return prices[-1].close / prices[-lookback_days - 1].close - 1


def _max_drawdown(prices: list[EtfPriceHistory]) -> float:
    peak = prices[0].close if prices else 0.0
    worst = 0.0
    for price in prices:
        peak = max(peak, price.close)
        if peak:
            worst = min(worst, price.close / peak - 1)
    return worst


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _candidate_metrics(
    series_by_code: dict[str, list[EtfPriceHistory]],
    lookback_days: int,
) -> list[EvaluationCandidate]:
    candidates: list[EvaluationCandidate] = []
    for code, prices in series_by_code.items():
        if len(prices) <= lookback_days:
            continue
        candidates.append(
            EvaluationCandidate(
                code=code,
                lookback_return=_lookback_return(prices, lookback_days),
                period_return=_series_return(prices),
                max_drawdown=_max_drawdown(prices),
            )
        )
    candidates.sort(key=lambda item: (-item.lookback_return, item.code))
    return candidates


def _baseline_metrics(series_by_code: dict[str, list[EtfPriceHistory]], fee_rate: float) -> dict[str, float]:
    returns = [_series_return(prices) for prices in series_by_code.values() if len(prices) >= 2]
    drawdowns = [_max_drawdown(prices) for prices in series_by_code.values() if len(prices) >= 2]
    return {
        "cumulative_return": round(_average(returns) - fee_rate, 6),
        "max_drawdown": round(min(drawdowns, default=0.0), 6),
        "fee_impact": round(fee_rate, 6),
        "selected_count": len(returns),
    }


def _parameter_metrics(
    candidates: list[EvaluationCandidate],
    *,
    top_n: int,
    fee_rate: float,
) -> dict[str, Any]:
    selected = candidates[:top_n]
    trade_fee = fee_rate * max(len(selected), 1)
    return {
        "selected_codes": [item.code for item in selected],
        "cumulative_return": round(_average([item.period_return for item in selected]) - trade_fee, 6),
        "max_drawdown": round(min([item.max_drawdown for item in selected], default=0.0), 6),
        "trade_count": len(selected),
        "fee_impact": round(trade_fee, 6),
    }


def _stability(values: list[float], sample_days: int) -> tuple[str, list[str]]:
    if sample_days < MIN_SAMPLE_DAYS:
        return "样本不足", ["样本不足"]
    if len(values) < 3:
        return "样本不足", ["样本不足"]
    middle = median(values)
    best = max(values)
    if best > middle + 0.12:
        return "不稳定", ["参数不稳定"]
    return "稳定", []


async def run_reliability_evaluation(
    session: AsyncSession,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> ShortEtfReliabilityEvaluation:
    etfs = await list_short_etfs(session)
    if end_date is None:
        latest = await session.scalar(select(EtfPriceHistory.trade_date).order_by(EtfPriceHistory.trade_date.desc()))
        end_date = cast(date | None, latest) or date.today()
    if start_date is None:
        earliest = await session.scalar(select(EtfPriceHistory.trade_date).order_by(EtfPriceHistory.trade_date.asc()))
        start_date = cast(date | None, earliest) or end_date

    evaluation = ShortEtfReliabilityEvaluation(
        status=EVALUATION_STATUS_SUCCESS,
        start_date=start_date,
        end_date=end_date,
        sample_days=max((end_date - start_date).days + 1, 0),
        conclusion="样本不足，继续观察",
        data_coverage_json={},
        summary_json={},
        risk_flags_json=[],
    )
    session.add(evaluation)
    await session.flush()
    try:
        series_by_code: dict[str, list[EtfPriceHistory]] = {}
        for etf in etfs:
            prices = await _price_series(session, etf.code, start_date, end_date)
            if prices:
                series_by_code[etf.code] = prices
        sample_days = max((end_date - start_date).days + 1, 0)
        baseline = _baseline_metrics(series_by_code, fee_rate)
        returns: list[float] = []
        for rank, params in enumerate(PARAMETER_GRID, start=1):
            candidates = _candidate_metrics(series_by_code, int(params["lookback_days"]))
            metrics = _parameter_metrics(candidates, top_n=int(params["top_n"]), fee_rate=fee_rate)
            returns.append(float(metrics["cumulative_return"]))
            session.add(
                ShortEtfReliabilityEvaluationItem(
                    evaluation_id=evaluation.id,
                    rank_order=rank,
                    label=f"{params['lookback_days']}日动量 Top {params['top_n']}",
                    item_type="parameter",
                    parameters_json=params,
                    metrics_json=metrics,
                    baseline_metrics_json=baseline,
                    score=round(float(metrics["cumulative_return"]) * 100, 4),
                    risk_flags_json=[],
                )
            )
        stability, stability_flags = _stability(returns, sample_days)
        risk_flags = list(stability_flags)
        conclusion = "样本不足，继续观察" if "样本不足" in risk_flags else "可观察"
        if "参数不稳定" in risk_flags:
            conclusion = "谨慎"
        evaluation.sample_days = sample_days
        evaluation.conclusion = conclusion
        evaluation.risk_flags_json = risk_flags
        evaluation.finished_at = utcnow()
        evaluation.data_coverage_json = {
            "etf_count": len(etfs),
            "priced_etf_count": len(series_by_code),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "sample_days": sample_days,
        }
        evaluation.summary_json = {
            "fee_rate": fee_rate,
            "parameter_stability": stability,
            "best_return": round(max(returns, default=0.0), 6),
            "median_return": round(median(returns), 6) if returns else 0.0,
            "baseline": baseline,
            "research_only": True,
        }
        await session.commit()
        await session.refresh(evaluation)
        return evaluation
    except Exception as exc:  # noqa: BLE001
        evaluation.status = EVALUATION_STATUS_FAILED
        evaluation.finished_at = utcnow()
        evaluation.error_message = str(exc)
        await session.commit()
        raise
