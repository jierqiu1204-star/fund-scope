from __future__ import annotations

from datetime import date
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfMetric,
    ShortEtfSignalItem,
    ShortEtfSignalReview,
    ShortEtfSignalReviewItem,
    ShortEtfSignalRun,
    TradableEtf,
    utcnow,
)
from app.services.short_etf.data import compute_etf_metric, latest_price_date, list_short_etfs

RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_RUNNING = "running"
REVIEW_MODEL_RULES = "rules-v1"


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _format_abs_percent(value: float) -> str:
    return f"{abs(value) * 100:.2f}%"


def _format_turnover(value: float) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f} 亿元"
    if value >= 10_000:
        return f"{value / 10_000:.2f} 万元"
    return f"{value:.2f} 元"


def _metric_value(value: float | None) -> float:
    return float(value) if value is not None else 0.0


def signal_conclusion(risk_flags: list[str], total_score: float) -> str:
    if "流动性不足" in risk_flags or "数据滞后" in risk_flags:
        return "不适合短线"
    if "数据不足" in risk_flags:
        return "谨慎"
    if "追高风险" in risk_flags or "连续大涨" in risk_flags or "高波动" in risk_flags:
        return "高位观察"
    if total_score >= 70:
        return "可观察"
    return "谨慎"


def total_signal_score(metric: EtfMetric) -> float:
    return round(metric.trend_score * 0.5 + metric.liquidity_score * 0.25 + metric.risk_score * 0.25, 2)


def score_breakdown(metric: EtfMetric) -> dict[str, Any]:
    return {
        "trend": {"score": metric.trend_score, "weight": 0.5},
        "liquidity": {"score": metric.liquidity_score, "weight": 0.25},
        "risk": {"score": metric.risk_score, "weight": 0.25},
        "metrics": {
            "return_5d": metric.return_5d,
            "return_20d": metric.return_20d,
            "return_60d": metric.return_60d,
            "average_turnover_20d": metric.average_turnover_20d,
            "volatility_20d": metric.volatility_20d,
            "max_drawdown_60d": metric.max_drawdown_60d,
        },
    }


def _metric_summary(metric: EtfMetric) -> dict[str, float]:
    return {
        "return_5d": _metric_value(metric.return_5d),
        "return_20d": _metric_value(metric.return_20d),
        "return_60d": _metric_value(metric.return_60d),
        "average_turnover_20d": _metric_value(metric.average_turnover_20d),
        "volatility_20d": _metric_value(metric.volatility_20d),
        "max_drawdown_60d": _metric_value(metric.max_drawdown_60d),
    }


def _metric_summary_text(metrics: dict[str, float]) -> list[str]:
    return [
        f"近5日涨跌幅 {_format_percent(metrics.get('return_5d', 0.0))}",
        f"近20日涨跌幅 {_format_percent(metrics.get('return_20d', 0.0))}",
        f"近60日涨跌幅 {_format_percent(metrics.get('return_60d', 0.0))}",
        f"近20日年化波动 {_format_abs_percent(metrics.get('volatility_20d', 0.0))}",
        f"近60日最大回撤 {_format_abs_percent(metrics.get('max_drawdown_60d', 0.0))}",
        f"近20日平均成交额 {_format_turnover(metrics.get('average_turnover_20d', 0.0))}",
    ]


def _risk_explanation(risk_flags: list[str]) -> str:
    if not risk_flags:
        return "暂未触发主要风险标签。"
    explanations = {
        "追高风险": "近一段时间涨幅已经偏高，继续追进去容易遇到回落。",
        "连续大涨": "最近连续上涨，短线情绪可能偏热。",
        "高波动": "日线波动较大，短线可能快速涨跌。",
        "流动性不足": "成交不够活跃，买卖价差和成交不确定性会更高。",
        "数据不足": "历史样本太短，分数稳定性不足。",
        "数据滞后": "最新行情数据不够新，信号可能已经失效。",
    }
    details = [f"{flag}：{explanations.get(flag, '需要额外谨慎。')}" for flag in risk_flags]
    return "触发风险标签：" + "；".join(details)


def _score_driver_text(metric: EtfMetric) -> str:
    drivers = [
        f"趋势分 {metric.trend_score:.1f}",
        f"成交活跃度分 {metric.liquidity_score:.1f}",
        f"风险分 {metric.risk_score:.1f}",
    ]
    return "、".join(drivers)


def rationale(etf: TradableEtf, metric: EtfMetric) -> dict[str, Any]:
    metrics = _metric_summary(metric)
    total_score = total_signal_score(metric)
    conclusion = signal_conclusion(metric.risk_flags_json, total_score)
    risk_text = _risk_explanation(metric.risk_flags_json)
    metrics_text = _metric_summary_text(metrics)
    if conclusion == "高位观察":
        reason = (
            f"高位观察：综合分 {total_score:.1f}，主要依据是{_score_driver_text(metric)}；"
            f"但{risk_text} 关键数据：{metrics_text[2]}、{metrics_text[3]}、{metrics_text[4]}。"
            "意思是它短线表现强，但位置和波动都要警惕，只能观察，不是买入建议。"
        )
    elif conclusion == "可观察":
        reason = (
            f"可观察：综合分 {total_score:.1f}，{_score_driver_text(metric)}；{risk_text} "
            f"关键数据：{metrics_text[1]}、{metrics_text[2]}、{metrics_text[5]}。"
            "意思是它进入观察清单，但还需要继续看模拟盘和风控审查。"
        )
    elif conclusion == "不适合短线":
        reason = (
            f"不适合短线：综合分 {total_score:.1f}，{risk_text} "
            "这种情况下短线信号容易失真，先排除出短线操作观察。"
        )
    else:
        reason = (
            f"谨慎：综合分 {total_score:.1f}，{_score_driver_text(metric)}；{risk_text} "
            "分数或数据条件还不够明确，只适合继续跟踪。"
        )
    return {
        "themes": etf.theme_tags_json,
        "trading_rule": etf.trading_rule_label,
        "reason": reason,
        "opportunity": f"机会来自趋势和成交活跃度：{_score_driver_text(metric)}。",
        "danger": risk_text,
        "metric_summary": metrics,
        "metric_summary_text": metrics_text,
        "conclusion_meaning": {
            "可观察": "可以放进观察清单继续跟踪，不等于现在就买。",
            "高位观察": "分数高但风险也高，尤其要防追高和大幅波动。",
            "谨慎": "数据或分数不够扎实，先别急着行动。",
            "不适合短线": "流动性、数据或风险条件不适合短线研究。",
        }.get(conclusion, "只用于研究观察。"),
        "no_trade_instruction": True,
        "data_lag_note": "第一版按日线公开数据模拟，不代表盘中实时成交。",
        "risk_flags": metric.risk_flags_json,
    }


async def get_signal_run(session: AsyncSession, run_id: int) -> ShortEtfSignalRun | None:
    return cast(
        ShortEtfSignalRun | None,
        await session.scalar(select(ShortEtfSignalRun).where(ShortEtfSignalRun.id == run_id)),
    )


async def list_signal_items(session: AsyncSession, run_id: int) -> list[ShortEtfSignalItem]:
    rows = await session.scalars(
        select(ShortEtfSignalItem)
        .where(ShortEtfSignalItem.run_id == run_id)
        .order_by(ShortEtfSignalItem.rank.asc(), ShortEtfSignalItem.etf_code.asc())
    )
    return list(rows.all())


async def latest_signal_run(session: AsyncSession) -> ShortEtfSignalRun | None:
    return cast(
        ShortEtfSignalRun | None,
        await session.scalar(
            select(ShortEtfSignalRun)
            .where(ShortEtfSignalRun.status == RUN_STATUS_SUCCESS)
            .order_by(
                ShortEtfSignalRun.as_of_date.desc(),
                ShortEtfSignalRun.finished_at.desc(),
                ShortEtfSignalRun.id.desc(),
            )
        ),
    )


async def run_signal_generation(
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
    codes: list[str] | None = None,
) -> ShortEtfSignalRun:
    if as_of_date is None:
        as_of_date = await latest_price_date(session) or date.today()
    signal_run = ShortEtfSignalRun(
        status=RUN_STATUS_RUNNING,
        as_of_date=as_of_date,
        config_json={"codes": codes or [], "language": "research_only"},
        summary_json={},
    )
    session.add(signal_run)
    await session.commit()
    await session.refresh(signal_run)

    try:
        etfs = await list_short_etfs(session, codes)
        candidates: list[tuple[TradableEtf, EtfMetric, float]] = []
        for etf in etfs:
            metric = await compute_etf_metric(session, etf.code, as_of_date)
            if metric is None:
                continue
            candidates.append((etf, metric, total_signal_score(metric)))
        candidates.sort(key=lambda item: (-item[2], item[0].code))
        for rank, (etf, metric, score) in enumerate(candidates, start=1):
            session.add(
                ShortEtfSignalItem(
                    run_id=signal_run.id,
                    etf_code=etf.code,
                    rank=rank,
                    total_score=score,
                    conclusion=signal_conclusion(metric.risk_flags_json, score),
                    score_breakdown_json=score_breakdown(metric),
                    risk_flags_json=metric.risk_flags_json,
                    rationale_json=rationale(etf, metric),
                )
            )
        signal_run.status = RUN_STATUS_SUCCESS
        signal_run.finished_at = utcnow()
        signal_run.summary_json = {
            "item_count": len(candidates),
            "as_of_date": as_of_date.isoformat(),
            "ranking_policy": "趋势、流动性、风险扣分排序，只做研究观察，不生成买卖指令。",
        }
        await session.commit()
        await session.refresh(signal_run)
        return signal_run
    except Exception as exc:  # noqa: BLE001
        signal_run.status = RUN_STATUS_FAILED
        signal_run.finished_at = utcnow()
        signal_run.error_message = str(exc)
        await session.commit()
        raise


async def get_review_for_run(session: AsyncSession, run_id: int) -> ShortEtfSignalReview | None:
    return cast(
        ShortEtfSignalReview | None,
        await session.scalar(select(ShortEtfSignalReview).where(ShortEtfSignalReview.run_id == run_id)),
    )


async def list_review_items(session: AsyncSession, review_id: int) -> list[ShortEtfSignalReviewItem]:
    rows = await session.scalars(
        select(ShortEtfSignalReviewItem)
        .where(ShortEtfSignalReviewItem.review_id == review_id)
        .order_by(ShortEtfSignalReviewItem.rank.asc())
    )
    return list(rows.all())


def _verdict(item: ShortEtfSignalItem) -> str:
    if item.conclusion == "不适合短线":
        return "不适合短线"
    if "追高风险" in item.risk_flags_json or "连续大涨" in item.risk_flags_json:
        return "高位观察"
    return item.conclusion


def _item_metric_values(item: ShortEtfSignalItem) -> dict[str, float]:
    breakdown = item.score_breakdown_json
    if not isinstance(breakdown, dict):
        return {}
    metrics = breakdown.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    values: dict[str, float] = {}
    for key, value in metrics.items():
        if isinstance(value, int | float):
            values[str(key)] = float(value)
    return values


def _review_agent_notes(item: ShortEtfSignalItem, verdict: str) -> dict[str, Any]:
    metrics = _item_metric_values(item)
    metrics_text = _metric_summary_text(metrics)
    risk_text = _risk_explanation(item.risk_flags_json)
    return {
        "数据员": "使用公开 ETF 日线、成交额和波动数据，非盘中实时数据。"
        f"本次关键数据：{'；'.join(metrics_text)}。",
        "趋势员": f"原始信号排名第 {item.rank}，得分 {item.total_score:.2f}。"
        f"{metrics_text[0]}，{metrics_text[1]}，{metrics_text[2]}，只说明过去一段时间趋势强弱，不代表未来收益。",
        "风控员": f"{risk_text} 波动和回撤要重点看：{metrics_text[3]}，{metrics_text[4]}。",
        "反方": "短线信号可能失效，尤其在高涨幅、低流动性或高波动阶段。"
        "如果只是看到排行榜涨幅高就追，风险会明显放大。",
        "总结员": f"结论：{verdict}。这是研究观察，不是购买建议，也不会自动下单。",
    }


async def generate_signal_review(session: AsyncSession, run: ShortEtfSignalRun) -> ShortEtfSignalReview:
    if run.status != RUN_STATUS_SUCCESS:
        raise ValueError("只能审查成功的短线 ETF 信号。")
    items = await list_signal_items(session, run.id)
    review = await get_review_for_run(session, run.id)
    if review is None:
        review = ShortEtfSignalReview(
            run_id=run.id,
            status=RUN_STATUS_SUCCESS,
            model_name=REVIEW_MODEL_RULES,
            summary_json={},
        )
        session.add(review)
        await session.flush()
    else:
        await session.execute(delete(ShortEtfSignalReviewItem).where(ShortEtfSignalReviewItem.review_id == review.id))
    verdict_counts: dict[str, int] = {}
    for item in items:
        verdict = _verdict(item)
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        session.add(
            ShortEtfSignalReviewItem(
                review_id=review.id,
                signal_item_id=item.id,
                etf_code=item.etf_code,
                rank=item.rank,
                total_score=item.total_score,
                verdict=verdict,
                risk_flags_json=item.risk_flags_json,
                agent_notes_json=_review_agent_notes(item, verdict),
            )
        )
    review.status = RUN_STATUS_SUCCESS
    review.finished_at = utcnow()
    review.summary_json = {
        "run_id": run.id,
        "as_of_date": run.as_of_date.isoformat(),
        "item_count": len(items),
        "verdict_counts": verdict_counts,
        "ranking_policy": "保持原短线信号排序，审查只做解释和风控，不改排名。",
    }
    await session.commit()
    await session.refresh(review)
    return review
