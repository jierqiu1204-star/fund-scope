from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    EtfLabelReplaySample,
    EtfPortfolioBacktestRun,
    EtfSignalValidationItem,
    EtfSignalValidationRun,
    EtfStrategyHealthcheckItem,
    EtfStrategyHealthcheckSnapshot,
    ShortResearchSignalRun,
)
from app.services.etf_research_evidence import (
    EVIDENCE_STATUS_INSUFFICIENT,
    EVIDENCE_STATUS_SAME_CONTRACT,
    EVIDENCE_STATUS_VERSION_MISMATCH,
    EVIDENCE_STATUS_WAITING,
    EXECUTION_MODEL_INTRADAY_ALERT,
    stable_contract_hash,
)

HEALTHCHECK_CONCLUSION_OK = "可继续观察"
HEALTHCHECK_CONCLUSION_WATCH = "待验证"
HEALTHCHECK_CONCLUSION_FAILED = "近期失效"
HEALTHCHECK_CONCLUSION_INSUFFICIENT = "数据不足"

HEALTHCHECK_RULE_VERSION = "etf_strategy_healthcheck_v1"
HEALTHCHECK_MIN_FULL_SAMPLES = 30
HEALTHCHECK_MIN_RECENT_SAMPLES = 10


@dataclass(frozen=True)
class HealthcheckStats:
    sample_count: int
    avg_return: float | None
    win_rate: float | None
    max_drawdown: float | None


def classify_healthcheck_conclusion(
    *,
    full_sample_count: int,
    recent_sample_count: int,
    full_avg_return: float | None,
    recent_avg_return: float | None,
    full_win_rate: float | None,
    recent_win_rate: float | None,
    recent_max_drawdown: float | None,
) -> str:
    if full_sample_count < HEALTHCHECK_MIN_FULL_SAMPLES:
        return HEALTHCHECK_CONCLUSION_INSUFFICIENT
    if recent_sample_count < HEALTHCHECK_MIN_RECENT_SAMPLES:
        return HEALTHCHECK_CONCLUSION_WATCH
    if (
        (recent_avg_return is not None and recent_avg_return <= -0.01)
        or (recent_win_rate is not None and recent_win_rate < 0.4)
        or (recent_max_drawdown is not None and recent_max_drawdown <= -0.08)
    ):
        return HEALTHCHECK_CONCLUSION_FAILED
    if (full_avg_return is not None and full_avg_return > 0) and (
        full_win_rate is not None and full_win_rate >= 0.5
    ):
        return HEALTHCHECK_CONCLUSION_OK
    return HEALTHCHECK_CONCLUSION_WATCH


def classify_item_conclusion(stats: HealthcheckStats) -> str:
    if stats.sample_count < HEALTHCHECK_MIN_RECENT_SAMPLES:
        return HEALTHCHECK_CONCLUSION_INSUFFICIENT
    if (
        (stats.avg_return is not None and stats.avg_return <= -0.01)
        or (stats.win_rate is not None and stats.win_rate < 0.4)
        or (stats.max_drawdown is not None and stats.max_drawdown <= -0.08)
    ):
        return HEALTHCHECK_CONCLUSION_FAILED
    if (stats.avg_return is not None and stats.avg_return > 0) and (
        stats.win_rate is not None and stats.win_rate >= 0.5
    ):
        return HEALTHCHECK_CONCLUSION_OK
    return HEALTHCHECK_CONCLUSION_WATCH


def aggregate_validation_rows(rows: Iterable[EtfSignalValidationItem], *, key_attr: str) -> dict[str, HealthcheckStats]:
    groups: dict[str, list[EtfSignalValidationItem]] = {}
    for row in rows:
        key = str(getattr(row, key_attr) or "未分类")
        groups.setdefault(key, []).append(row)
    result: dict[str, HealthcheckStats] = {}
    for key, group in groups.items():
        sample_count = sum(max(0, int(item.sample_count or 0)) for item in group)
        weighted_returns = [
            float(item.avg_return) * max(0, int(item.sample_count or 0))
            for item in group
            if item.avg_return is not None and int(item.sample_count or 0) > 0
        ]
        weighted_wins = [
            float(item.win_rate) * max(0, int(item.sample_count or 0))
            for item in group
            if item.win_rate is not None and int(item.sample_count or 0) > 0
        ]
        drawdowns = [float(item.worst_forward_drawdown) for item in group if item.worst_forward_drawdown is not None]
        result[key] = HealthcheckStats(
            sample_count=sample_count,
            avg_return=sum(weighted_returns) / sample_count if weighted_returns and sample_count > 0 else None,
            win_rate=sum(weighted_wins) / sample_count if weighted_wins and sample_count > 0 else None,
            max_drawdown=min(drawdowns) if drawdowns else None,
        )
    return result


def _stats_from_samples(samples: list[EtfLabelReplaySample]) -> HealthcheckStats:
    completed = [sample for sample in samples if sample.status == "completed" and sample.forward_return is not None]
    returns = [float(sample.forward_return or 0.0) for sample in completed]
    drawdowns = [
        float(sample.adverse_drawdown)
        for sample in completed
        if sample.adverse_drawdown is not None
    ]
    return HealthcheckStats(
        sample_count=len(completed),
        avg_return=mean(returns) if returns else None,
        win_rate=sum(1 for value in returns if value > 0) / len(returns) if returns else None,
        max_drawdown=min(drawdowns) if drawdowns else None,
    )


async def latest_healthcheck_snapshot(session: AsyncSession) -> EtfStrategyHealthcheckSnapshot | None:
    return await session.scalar(
        select(EtfStrategyHealthcheckSnapshot).order_by(
            EtfStrategyHealthcheckSnapshot.created_at.desc(),
            EtfStrategyHealthcheckSnapshot.id.desc(),
        )
    )


async def healthcheck_payload(
    session: AsyncSession,
    snapshot: EtfStrategyHealthcheckSnapshot | None,
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    rows = (
        await session.scalars(
            select(EtfStrategyHealthcheckItem)
            .where(EtfStrategyHealthcheckItem.snapshot_id == snapshot.id)
            .order_by(EtfStrategyHealthcheckItem.item_type.asc(), EtfStrategyHealthcheckItem.sample_count.desc())
        )
    ).all()
    return {
        "id": snapshot.id,
        "status": snapshot.status,
        "conclusion": snapshot.conclusion,
        "as_of_date": snapshot.as_of_date,
        "generated_at": snapshot.created_at,
        "source_signal_run_id": snapshot.source_signal_run_id,
        "validation_run_id": snapshot.validation_run_id,
        "backtest_run_id": snapshot.backtest_run_id,
        "execution_model": snapshot.execution_model,
        "evidence_contract_hash": snapshot.evidence_contract_hash,
        "evidence_status": snapshot.evidence_status,
        "data_window": dict(snapshot.data_window_json or {}),
        "summary": dict(snapshot.summary_json or {}),
        "metrics": dict(snapshot.metrics_json or {}),
        "caveats": list(snapshot.caveats_json or []),
        "items": [
            {
                "item_type": row.item_type,
                "item_key": row.item_key,
                "conclusion": row.conclusion,
                "sample_count": row.sample_count,
                "avg_return": row.avg_return,
                "win_rate": row.win_rate,
                "max_drawdown": row.max_drawdown,
                "metrics": dict(row.metrics_json or {}),
            }
            for row in rows
        ],
        "research_only": True,
        "no_trade_instruction": True,
    }


async def run_etf_strategy_healthcheck(session: AsyncSession) -> EtfStrategyHealthcheckSnapshot:
    signal_run = await session.scalar(
        select(ShortResearchSignalRun)
        .where(ShortResearchSignalRun.status == "success")
        .order_by(ShortResearchSignalRun.as_of_date.desc(), ShortResearchSignalRun.id.desc())
    )
    validation_run = await session.scalar(
        select(EtfSignalValidationRun)
        .where(EtfSignalValidationRun.asset_type == ASSET_TYPE_ETF, EtfSignalValidationRun.status == "success")
        .order_by(EtfSignalValidationRun.as_of_date.desc(), EtfSignalValidationRun.id.desc())
    )
    backtest_run = await session.scalar(
        select(EtfPortfolioBacktestRun)
        .where(EtfPortfolioBacktestRun.asset_type == ASSET_TYPE_ETF, EtfPortfolioBacktestRun.status == "success")
        .order_by(EtfPortfolioBacktestRun.finished_at.desc(), EtfPortfolioBacktestRun.id.desc())
    )
    as_of = (
        signal_run.as_of_date
        if signal_run is not None
        else validation_run.as_of_date
        if validation_run is not None
        else date.today()
    )
    contract_hash = stable_contract_hash(
        {
            "rule_version": HEALTHCHECK_RULE_VERSION,
            "source_signal_run_id": signal_run.id if signal_run else None,
            "validation_run_id": validation_run.id if validation_run else None,
            "backtest_run_id": backtest_run.id if backtest_run else None,
            "as_of_date": as_of.isoformat(),
        }
    )
    evidence_status = EVIDENCE_STATUS_WAITING
    if validation_run is None:
        evidence_status = EVIDENCE_STATUS_INSUFFICIENT
    elif signal_run is not None and validation_run.source_signal_run_id not in {None, signal_run.id}:
        evidence_status = EVIDENCE_STATUS_VERSION_MISMATCH
    else:
        evidence_status = EVIDENCE_STATUS_SAME_CONTRACT

    validation_rows: list[EtfSignalValidationItem] = []
    if validation_run is not None:
        validation_rows = list(
            (
                await session.scalars(
                    select(EtfSignalValidationItem).where(EtfSignalValidationItem.run_id == validation_run.id)
                )
            ).all()
        )
    label_stats = aggregate_validation_rows(validation_rows, key_attr="label")
    entry_stats = aggregate_validation_rows(validation_rows, key_attr="entry_timing_label")

    replay_samples: list[EtfLabelReplaySample] = []
    if validation_run is not None:
        replay_samples = list(
            (
                await session.scalars(
                    select(EtfLabelReplaySample).where(EtfLabelReplaySample.validation_run_id == validation_run.id)
                )
            ).all()
        )
    full_stats = _stats_from_samples(replay_samples)
    recent_samples = sorted(replay_samples, key=lambda item: item.replay_date, reverse=True)[
        : min(120, len(replay_samples))
    ]
    recent_stats = _stats_from_samples(recent_samples)
    conclusion = classify_healthcheck_conclusion(
        full_sample_count=full_stats.sample_count,
        recent_sample_count=recent_stats.sample_count,
        full_avg_return=full_stats.avg_return,
        recent_avg_return=recent_stats.avg_return,
        full_win_rate=full_stats.win_rate,
        recent_win_rate=recent_stats.win_rate,
        recent_max_drawdown=recent_stats.max_drawdown,
    )
    if validation_run is None:
        conclusion = HEALTHCHECK_CONCLUSION_INSUFFICIENT

    intraday_available = bool(
        backtest_run
        and (
            (backtest_run.config_json or {}).get("replay_contract", {}).get("execution_model")
            in {EXECUTION_MODEL_INTRADAY_ALERT, "intraday_alert"}
        )
    )
    data_window = {
        "as_of_date": as_of.isoformat(),
        "validation_run_id": validation_run.id if validation_run else None,
        "full_sample_count": full_stats.sample_count,
        "recent_sample_count": recent_stats.sample_count,
        "daily_close_evidence": "available" if validation_run else "unavailable",
        "intraday_alert_evidence": "available" if intraday_available else "unavailable",
    }
    metrics = {
        "full": full_stats.__dict__,
        "recent": recent_stats.__dict__,
        "label_count": len(label_stats),
        "entry_timing_count": len(entry_stats),
    }
    caveats = [
        "策略体检只用于研究，不是买入或卖出指令。",
        "日线证据与盘中提醒证据分开展示；缺盘中历史时不使用日线回退。",
    ]
    if not intraday_available:
        caveats.append("暂无同源盘中提醒执行回测，不能证明邮件盘中操作效果。")

    snapshot = EtfStrategyHealthcheckSnapshot(
        status="success" if validation_run else "waiting",
        source_signal_run_id=signal_run.id if signal_run else None,
        validation_run_id=validation_run.id if validation_run else None,
        backtest_run_id=backtest_run.id if backtest_run else None,
        as_of_date=as_of,
        execution_model="daily_close",
        evidence_contract_hash=contract_hash,
        evidence_status=evidence_status,
        conclusion=conclusion,
        data_window_json=data_window,
        summary_json={
            "rule_version": HEALTHCHECK_RULE_VERSION,
            "daily_close_evidence": data_window["daily_close_evidence"],
            "intraday_alert_evidence": data_window["intraday_alert_evidence"],
            "weak_labels": [
                key for key, stats in label_stats.items() if classify_item_conclusion(stats) == HEALTHCHECK_CONCLUSION_FAILED
            ],
            "weak_entry_timing_labels": [
                key for key, stats in entry_stats.items() if classify_item_conclusion(stats) == HEALTHCHECK_CONCLUSION_FAILED
            ],
        },
        metrics_json=metrics,
        caveats_json=caveats,
    )
    session.add(snapshot)
    await session.flush()

    for item_type, groups in (("label", label_stats), ("entry_timing", entry_stats)):
        for key, stats in groups.items():
            session.add(
                EtfStrategyHealthcheckItem(
                    snapshot_id=snapshot.id,
                    item_type=item_type,
                    item_key=key,
                    conclusion=classify_item_conclusion(stats),
                    sample_count=stats.sample_count,
                    avg_return=stats.avg_return,
                    win_rate=stats.win_rate,
                    max_drawdown=stats.max_drawdown,
                    metrics_json=stats.__dict__,
                )
            )

    await session.commit()
    await session.refresh(snapshot)
    return snapshot
