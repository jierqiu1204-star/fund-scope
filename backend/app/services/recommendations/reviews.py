from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    RecommendationItem,
    RecommendationReview,
    RecommendationReviewItem,
    RecommendationRun,
    StrategyEvaluation,
    utcnow,
)
from app.services.recommendations.constants import ASSET_TYPE_FUND, RUN_STATUS_SUCCESS

RULE_REVIEW_MODEL = "rules-v1"
VERDICT_WATCH = "可观察"
VERDICT_CAUTION = "谨慎"
VERDICT_INSUFFICIENT = "样本不足"
VERDICT_REJECT = "不建议采用"


@dataclass(frozen=True)
class EvaluationContext:
    conclusion: str | None
    risk_flags: list[str]
    is_sample_sufficient: bool | None
    available_days: int | None
    required_days: int | None


async def get_review_for_run(session: AsyncSession, run_id: int) -> RecommendationReview | None:
    return cast(
        RecommendationReview | None,
        await session.scalar(select(RecommendationReview).where(RecommendationReview.run_id == run_id)),
    )


async def list_review_items(session: AsyncSession, review_id: int) -> list[RecommendationReviewItem]:
    rows = await session.scalars(
        select(RecommendationReviewItem)
        .where(RecommendationReviewItem.review_id == review_id)
        .order_by(RecommendationReviewItem.id.asc())
    )
    return list(rows.all())


async def generate_review_for_run(session: AsyncSession, run: RecommendationRun) -> RecommendationReview:
    if run.asset_type != ASSET_TYPE_FUND:
        raise ValueError("第一版只支持基金推荐审查。")
    if run.status != RUN_STATUS_SUCCESS:
        raise ValueError("只能审查已成功完成的推荐结果。")

    items = await _recommendation_items(session, run.id)
    evaluation = await _latest_evaluation_context(session)
    item_payloads = [_review_item_payload(item, evaluation) for item in items]
    summary = _summary_payload(run, item_payloads, evaluation)
    now = utcnow()
    existing = await get_review_for_run(session, run.id)
    if existing is None:
        review = RecommendationReview(
            run_id=run.id,
            status=RUN_STATUS_SUCCESS,
            model_name=RULE_REVIEW_MODEL,
            started_at=now,
            finished_at=utcnow(),
            summary_json=summary,
        )
        session.add(review)
        await session.flush()
    else:
        review = existing
        review.status = RUN_STATUS_SUCCESS
        review.model_name = RULE_REVIEW_MODEL
        review.started_at = now
        review.finished_at = utcnow()
        review.summary_json = summary
        review.error_message = None
        review.updated_at = utcnow()
        await session.execute(delete(RecommendationReviewItem).where(RecommendationReviewItem.review_id == review.id))
        await session.flush()

    session.add_all(
        [
            RecommendationReviewItem(
                review_id=review.id,
                recommendation_item_id=int(payload["recommendation_item_id"]),
                asset_code=str(payload["asset_code"]),
                verdict=str(payload["verdict"]),
                agent_notes_json=cast(dict[str, Any], payload["agent_notes"]),
                risk_flags_json=cast(list[str], payload["risk_flags"]),
            )
            for payload in item_payloads
        ]
    )
    await session.commit()
    await session.refresh(review)
    return review


async def _recommendation_items(session: AsyncSession, run_id: int) -> list[RecommendationItem]:
    rows = await session.scalars(
        select(RecommendationItem)
        .where(RecommendationItem.run_id == run_id)
        .order_by(RecommendationItem.rank.asc(), RecommendationItem.asset_code.asc())
    )
    return list(rows.all())


async def _latest_evaluation_context(session: AsyncSession) -> EvaluationContext:
    evaluation = await session.scalar(
        select(StrategyEvaluation)
        .where(StrategyEvaluation.status == RUN_STATUS_SUCCESS)
        .order_by(StrategyEvaluation.created_at.desc(), StrategyEvaluation.id.desc())
    )
    if evaluation is None:
        return EvaluationContext(None, [], None, None, None)
    coverage = evaluation.data_coverage_json
    return EvaluationContext(
        conclusion=evaluation.conclusion,
        risk_flags=[str(flag) for flag in evaluation.risk_flags_json],
        is_sample_sufficient=coverage.get("is_sample_sufficient")
        if isinstance(coverage.get("is_sample_sufficient"), bool)
        else None,
        available_days=coverage.get("available_days") if isinstance(coverage.get("available_days"), int) else None,
        required_days=coverage.get("required_days") if isinstance(coverage.get("required_days"), int) else None,
    )


def _review_item_payload(item: RecommendationItem, evaluation: EvaluationContext) -> dict[str, Any]:
    verdict = _verdict(item, evaluation)
    risk_flags = _combined_risk_flags(item, evaluation)
    agent_notes = {
        "数据员": _data_agent_note(item, evaluation),
        "策略员": _strategy_agent_note(item),
        "风控员": _risk_agent_note(item, evaluation),
        "反方": _bear_agent_note(item, evaluation),
        "总结员": f"结论：{verdict}。这是研究候选，不是购买建议，也不会自动下单。",
    }
    return {
        "recommendation_item_id": item.id,
        "asset_code": item.asset_code,
        "verdict": verdict,
        "agent_notes": agent_notes,
        "risk_flags": risk_flags,
    }


def _verdict(item: RecommendationItem, evaluation: EvaluationContext) -> str:
    missing_metrics = item.data_freshness_json.get("missing_metrics", [])
    if evaluation.is_sample_sufficient is False or "样本不足" in evaluation.risk_flags:
        return VERDICT_INSUFFICIENT
    if isinstance(missing_metrics, list) and missing_metrics:
        return VERDICT_INSUFFICIENT
    if item.total_score < 50:
        return VERDICT_REJECT
    if _has_major_risk(item, evaluation):
        return VERDICT_CAUTION
    if item.total_score >= 75:
        return VERDICT_WATCH
    return VERDICT_CAUTION


def _has_major_risk(item: RecommendationItem, evaluation: EvaluationContext) -> bool:
    major_flags = {"critical_news", "large_drawdown", "high_volatility", "风险偏高", "疑似过拟合"}
    return bool(major_flags.intersection(set(item.risk_flags_json).union(evaluation.risk_flags)))


def _combined_risk_flags(item: RecommendationItem, evaluation: EvaluationContext) -> list[str]:
    flags = [str(flag) for flag in item.risk_flags_json]
    for flag in evaluation.risk_flags:
        if flag not in flags:
            flags.append(flag)
    return flags


def _data_agent_note(item: RecommendationItem, evaluation: EvaluationContext) -> str:
    missing_metrics = item.data_freshness_json.get("missing_metrics", [])
    if isinstance(missing_metrics, list) and missing_metrics:
        return f"这只基金缺少 {len(missing_metrics)} 项指标，先不要把分数当成稳定结论。"
    if evaluation.is_sample_sufficient is False:
        return f"策略验证历史约 {evaluation.available_days or 0} 天，低于 {evaluation.required_days or 730} 天门槛。"
    return "基础评分数据可用于研究排序，但仍需要结合回测和模拟盘继续观察。"


def _strategy_agent_note(item: RecommendationItem) -> str:
    drivers = item.rationale_json.get("drivers", [])
    driver_text = "、".join(str(driver) for driver in drivers[:4]) if isinstance(drivers, list) else "规则评分"
    return f"当前排序来自确定性评分，主要参考 {driver_text}，原始得分为 {item.total_score:.2f}。"


def _risk_agent_note(item: RecommendationItem, evaluation: EvaluationContext) -> str:
    flags = _combined_risk_flags(item, evaluation)
    if flags:
        return f"触发风险：{'、'.join(flags)}。如果要实盘手动买入，应先观察模拟盘。"
    return "没有触发主要风险标签，但基金仍可能出现净值波动和阶段性亏损。"


def _bear_agent_note(item: RecommendationItem, evaluation: EvaluationContext) -> str:
    if evaluation.is_sample_sufficient is False:
        return "反方观点：策略历史样本不足，当前排名可能只是短期数据下的结果。"
    if item.total_score < 70:
        return "反方观点：得分不够突出，可能只是候补观察对象，不适合急着买。"
    if item.risk_flags_json:
        return "反方观点：虽然评分靠前，但风险标签会削弱可操作性。"
    return "反方观点：即使评分靠前，也可能在市场风格切换时失效。"


def _summary_payload(
    run: RecommendationRun,
    item_payloads: list[dict[str, Any]],
    evaluation: EvaluationContext,
) -> dict[str, Any]:
    verdict_counts = Counter(str(item["verdict"]) for item in item_payloads)
    return {
        "review_scope": "fund_research_candidates",
        "ranking_policy": "保持原推荐评分排序，审查只做解释和风控，不改排名。",
        "asset_type": run.asset_type,
        "as_of_date": run.as_of_date.isoformat(),
        "item_count": len(item_payloads),
        "verdict_counts": dict(verdict_counts),
        "strategy_evaluation": {
            "conclusion": evaluation.conclusion,
            "risk_flags": evaluation.risk_flags,
            "is_sample_sufficient": evaluation.is_sample_sufficient,
            "available_days": evaluation.available_days,
            "required_days": evaluation.required_days,
        },
        "note": "审查报告只用于研究解释和风险提示，不生成买卖指令。",
    }
