from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.defaults.short_research import ASSET_TYPE_ETF, SHORT_RESEARCH_ASSET_BY_KEY
from app.models.entities import (
    ShortResearchAdvisorAttempt,
    ShortResearchAdvisorReport,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    Transaction,
    utcnow,
)
from app.services.llm import LLMClient
from app.services.short_research.service import (
    current_etf_snapshot_selection,
    latest_signal_run,
    list_signal_items,
    run_signal_generation,
)

ACTION_FOCUS = "重点观察"
ACTION_HIGH = "高位别追"
ACTION_CAUTION = "谨慎"
ACTION_SKIP = "暂不考虑"
ACTION_EXIT = "退出观察"
ALLOWED_ACTION_LABELS = {ACTION_FOCUS, ACTION_HIGH, ACTION_CAUTION, ACTION_SKIP, ACTION_EXIT}

PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action_label": {"type": "string", "enum": sorted(ALLOWED_ACTION_LABELS)},
        "plain_summary": {"type": "string"},
        "opportunity": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
        "risks": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
        "opposing_view": {"type": "string"},
        "watch_conditions": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
        "holding_note": {"type": "string"},
        "data_limitations": {"type": "string"},
    },
    "required": [
        "action_label",
        "plain_summary",
        "opportunity",
        "risks",
        "opposing_view",
        "watch_conditions",
        "holding_note",
        "data_limitations",
    ],
    "additionalProperties": False,
}

PROHIBITED_TERMS = (
    "建议买入",
    "建议卖出",
    "建议止盈",
    "建议止损",
    "马上买",
    "马上卖",
    "立刻买",
    "立刻卖",
    "清仓卖",
    "满仓买",
    "目标价",
    "保证收益",
    "稳赚",
    "自动交易",
    "已连接券商",
    "支付宝实时同步",
    "buy",
    "sell",
    "take_profit",
    "stop_loss",
    "target_price",
    "expected_return",
    "guaranteed_profit",
)

NEUTRAL_ALLOWED_TERMS = (
    "止盈观察",
    "移动止盈",
    "硬止损",
    "趋势转弱",
    "不代表买入建议",
    "不是卖出指令",
    "不是直接操作命令",
)

WEAK_ACTIONS = {ACTION_CAUTION, ACTION_SKIP, ACTION_EXIT}
HIGH_WATCH_RISKS = {"追高风险", "连续大涨", "高波动"}
EXIT_RISKS = {"数据滞后", "回撤较大", "流动性不足"}


def conservative_action_for_item(item: ShortResearchSignalItem, *, is_held: bool = False) -> str:
    risk_flags = set(item.risk_flags_json or [])
    if is_held and (item.conclusion in {"不适合短线", "数据不足"} or risk_flags.intersection(EXIT_RISKS)):
        return ACTION_EXIT
    if item.conclusion == "短线观察":
        return ACTION_FOCUS
    if item.conclusion == "高位观察" or risk_flags.intersection(HIGH_WATCH_RISKS):
        return ACTION_HIGH
    if item.conclusion == "谨慎观察":
        return ACTION_CAUTION
    return ACTION_SKIP


def _is_stronger(candidate: str, allowed: str) -> bool:
    strength = {
        ACTION_SKIP: 0,
        ACTION_EXIT: 0,
        ACTION_CAUTION: 1,
        ACTION_HIGH: 1,
        ACTION_FOCUS: 2,
    }
    return strength[candidate] > strength[allowed]


def _coerce_string_list(value: Any, field: str) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()[:240]]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} contains invalid text")
        result.append(item.strip()[:240])
    return result


def _reject_prohibited_language(value: Any) -> None:
    if isinstance(value, str):
        lowered = value.lower()
        for allowed in NEUTRAL_ALLOWED_TERMS:
            lowered = lowered.replace(allowed.lower(), "")
        for term in PROHIBITED_TERMS:
            if term.lower() in lowered:
                raise ValueError(f"LLM output contains prohibited language: {term}")
        return
    if isinstance(value, list):
        for item in value:
            _reject_prohibited_language(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_prohibited_language(item)


def validate_advisor_payload(
    payload: str | dict[str, Any],
    *,
    rule_action: str,
    fallback_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed: dict[str, Any]
    if isinstance(payload, str):
        parsed = json.loads(payload)
    else:
        parsed = payload
    if not isinstance(parsed, dict):
        raise ValueError("LLM output must be a JSON object")

    action_label = parsed.get("action_label")
    if action_label not in ALLOWED_ACTION_LABELS:
        action_label = rule_action
    if _is_stronger(str(action_label), rule_action):
        action_label = rule_action
    fallback_used = False

    def text_field(key: str, limit: int) -> str:
        nonlocal fallback_used
        value = str(parsed.get(key, "")).strip()
        if not value and fallback_payload is not None:
            value = str(fallback_payload.get(key, "")).strip()
            fallback_used = True
        return value[:limit]

    def list_field(key: str) -> list[str]:
        nonlocal fallback_used
        try:
            return _coerce_string_list(parsed.get(key), key)
        except ValueError:
            if fallback_payload is None:
                raise
            fallback_used = True
            return _coerce_string_list(fallback_payload.get(key), key)

    report = {
        "action_label": action_label,
        "plain_summary": text_field("plain_summary", 360),
        "opportunity": list_field("opportunity"),
        "risks": list_field("risks"),
        "opposing_view": text_field("opposing_view", 500),
        "watch_conditions": list_field("watch_conditions"),
        "holding_note": text_field("holding_note", 500),
        "data_limitations": text_field("data_limitations", 500),
        "_fallback_used": fallback_used,
    }
    for key in ("plain_summary", "opposing_view", "holding_note", "data_limitations"):
        if not report[key]:
            raise ValueError(f"{key} is required")
    _reject_prohibited_language(report)
    return report


def _format_percent(value: Any) -> str:
    if not isinstance(value, int | float):
        return "暂无"
    return f"{value * 100:.2f}%"


def _asset_name(item: ShortResearchSignalItem) -> str:
    metadata = SHORT_RESEARCH_ASSET_BY_KEY.get((item.asset_type, item.asset_code))
    return metadata.name if metadata else item.asset_code


def _deterministic_snapshot(item: ShortResearchSignalItem, *, is_held: bool) -> dict[str, Any]:
    metadata = SHORT_RESEARCH_ASSET_BY_KEY.get((item.asset_type, item.asset_code))
    metrics = item.metrics_json or {}
    rationale = item.rationale_json or {}
    return {
        "asset_type": item.asset_type,
        "asset_code": item.asset_code,
        "asset_name": _asset_name(item),
        "rank": item.rank,
        "total_score": item.total_score,
        "deterministic_label": item.conclusion,
        "entry_timing_label": metrics.get("entry_timing_label") or rationale.get("entry_timing_label"),
        "entry_timing_reason": metrics.get("entry_timing_reason") or rationale.get("entry_timing_reason"),
        "rule_action": conservative_action_for_item(item, is_held=is_held),
        "ai_boundaries": "AI 只能解释系统已计算的标签证据、组合权重、提醒审计和其他确定性结果，不能改分数、标签、组合权重、动态阈值或邮件触发。",
        "risk_flags": list(item.risk_flags_json or []),
        "metrics": item.metrics_json,
        "rationale": item.rationale_json,
        "theme_tags": list(metadata.theme_tags) if metadata else [],
        "investment_direction": metadata.investment_direction if metadata else "",
        "trading_rule_label": metadata.trading_rule_label if metadata else "",
        "is_held": is_held,
    }


def fallback_report(item: ShortResearchSignalItem, *, is_held: bool, reason: str | None = None) -> dict[str, Any]:
    metrics = item.metrics_json or {}
    risk_flags = list(item.risk_flags_json or [])
    action = conservative_action_for_item(item, is_held=is_held)
    name = _asset_name(item)
    fallback_reason = f"{reason}。" if reason else "当前使用规则解释。"
    risks = risk_flags or ["暂未触发主要风险标签，但短线结果仍可能很快变化。"]
    return {
        "action_label": action,
        "plain_summary": f"{name} 当前为{item.conclusion}，{fallback_reason}",
        "opportunity": [
            f"近 5 日 {_format_percent(metrics.get('return_5d'))}，近 20 日 {_format_percent(metrics.get('return_20d'))}。",
            f"今日买点：{metrics.get('entry_timing_label') or (item.rationale_json or {}).get('entry_timing_label') or '数据不足'}；{metrics.get('entry_timing_reason') or (item.rationale_json or {}).get('entry_timing_reason') or '缺少买点解释'}",
            f"综合分 {item.total_score:.1f}，排名 #{item.rank}。",
        ],
        "risks": risks,
        "opposing_view": str(
            (item.rationale_json or {}).get(
                "opposing_view",
                "短线排序依赖近期公开数据，市场风格切换时可能快速失效。",
            )
        ),
        "watch_conditions": [
            "继续看近 20 日趋势是否保持。",
            "留意 60 日回撤是否扩大。",
            "如果公开数据滞后，先不要依据这条结果行动。",
        ],
        "holding_note": "如果已经持有，只把它当作观察提醒；真实操作仍需要你在投资平台手动确认。",
        "data_limitations": "结果基于公开基金净值和 ETF 日线数据，不连接支付宝或券商，也不是盘中实时交易指令。",
    }


async def _held_fund_codes(session: AsyncSession) -> set[str]:
    rows = (await session.scalars(select(Transaction).order_by(Transaction.traded_at.asc(), Transaction.id.asc()))).all()
    shares_by_code: dict[str, float] = {}
    for row in rows:
        current = shares_by_code.get(row.fund_code, 0.0)
        if row.action == "buy":
            shares_by_code[row.fund_code] = current + row.shares
        else:
            shares_by_code[row.fund_code] = current - row.shares
    return {code for code, shares in shares_by_code.items() if shares > 0}


async def _previous_actions(session: AsyncSession, run_id: int) -> dict[tuple[str, str], str]:
    rows = (
        await session.scalars(
            select(ShortResearchAdvisorReport)
            .where(ShortResearchAdvisorReport.signal_run_id != run_id)
            .order_by(ShortResearchAdvisorReport.generated_at.desc(), ShortResearchAdvisorReport.id.desc())
        )
    ).all()
    actions: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (row.asset_type, row.asset_code)
        if key not in actions:
            actions[key] = row.action_label
    return actions


async def select_items_for_advisor(
    session: AsyncSession,
    signal_run: ShortResearchSignalRun,
    *,
    max_assets: int,
) -> tuple[list[ShortResearchSignalItem], set[str]]:
    items = await list_signal_items(session, signal_run.id)
    held_codes = await _held_fund_codes(session)
    previous = await _previous_actions(session, signal_run.id)
    selected: dict[int, ShortResearchSignalItem] = {}
    for item in items[:max_assets]:
        selected[item.id] = item
    for item in items:
        if item.asset_type == "fund" and item.asset_code in held_codes:
            selected[item.id] = item
            continue
        previous_action = previous.get((item.asset_type, item.asset_code))
        if previous_action and previous_action != conservative_action_for_item(
            item,
            is_held=item.asset_type == "fund" and item.asset_code in held_codes,
        ):
            selected[item.id] = item
    return sorted(selected.values(), key=lambda item: item.rank), held_codes


async def _upsert_report(
    session: AsyncSession,
    *,
    signal_run: ShortResearchSignalRun,
    item: ShortResearchSignalItem,
    report: dict[str, Any],
    model_name: str,
    prompt_version: str,
    source: str,
    snapshot: dict[str, Any],
    raw_response: dict[str, Any],
) -> ShortResearchAdvisorReport:
    existing = await session.scalar(
        select(ShortResearchAdvisorReport).where(
            ShortResearchAdvisorReport.signal_run_id == signal_run.id,
            ShortResearchAdvisorReport.asset_type == item.asset_type,
            ShortResearchAdvisorReport.asset_code == item.asset_code,
        )
    )
    now = utcnow()
    if existing is None:
        existing = ShortResearchAdvisorReport(
            signal_run_id=signal_run.id,
            signal_item_id=item.id,
            asset_type=item.asset_type,
            asset_code=item.asset_code,
            status="success",
            action_label=str(report["action_label"]),
            plain_summary=str(report["plain_summary"]),
            opportunity_json=list(report["opportunity"]),
            risks_json=list(report["risks"]),
            opposing_view=str(report["opposing_view"]),
            watch_conditions_json=list(report["watch_conditions"]),
            holding_note=str(report["holding_note"]),
            data_limitations=str(report["data_limitations"]),
            model_name=model_name,
            prompt_version=prompt_version,
            source=source,
            deterministic_snapshot_json=snapshot,
            raw_response_json=raw_response,
            generated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    else:
        existing.signal_item_id = item.id
        existing.status = "success"
        existing.action_label = str(report["action_label"])
        existing.plain_summary = str(report["plain_summary"])
        existing.opportunity_json = list(report["opportunity"])
        existing.risks_json = list(report["risks"])
        existing.opposing_view = str(report["opposing_view"])
        existing.watch_conditions_json = list(report["watch_conditions"])
        existing.holding_note = str(report["holding_note"])
        existing.data_limitations = str(report["data_limitations"])
        existing.model_name = model_name
        existing.prompt_version = prompt_version
        existing.source = source
        existing.deterministic_snapshot_json = snapshot
        existing.raw_response_json = raw_response
        existing.generated_at = now
        existing.updated_at = now
    return existing


async def latest_reports_by_asset(
    session: AsyncSession,
    signal_run_id: int,
) -> dict[tuple[str, str], ShortResearchAdvisorReport]:
    rows = (
        await session.scalars(
            select(ShortResearchAdvisorReport).where(
                ShortResearchAdvisorReport.signal_run_id == signal_run_id,
                ShortResearchAdvisorReport.status == "success",
            )
        )
    ).all()
    return {(row.asset_type, row.asset_code): row for row in rows}


async def run_advisor_generation(
    session: AsyncSession,
    settings: Settings,
    *,
    llm_client: LLMClient | None = None,
    max_assets: int | None = None,
    asset_type: str | None = None,
    theme: str | None = None,
    codes: list[str] | None = None,
    as_of_date: date | None = None,
    source_signal_run_id: int | None = None,
) -> dict[str, Any]:
    explicit_signal_run = (
        await session.get(ShortResearchSignalRun, source_signal_run_id)
        if source_signal_run_id is not None
        else None
    )
    explicit_source_has_etf = (
        explicit_signal_run is not None
        and await session.scalar(
            select(ShortResearchSignalItem.id)
            .where(
                ShortResearchSignalItem.run_id == explicit_signal_run.id,
                ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
            )
            .limit(1)
        )
        is not None
    )
    canonical_etf_run = None
    default_source_request = asset_type is None and source_signal_run_id is None
    if asset_type == ASSET_TYPE_ETF or explicit_source_has_etf or default_source_request:
        canonical_selection = await current_etf_snapshot_selection(session)
        canonical_etf_run = canonical_selection.run
        if canonical_etf_run is None and (asset_type == ASSET_TYPE_ETF or explicit_source_has_etf):
            raise ValueError(
                f"当前 canonical ETF 排名快照不可用（{canonical_selection.state}）。"
            )
        if (
            source_signal_run_id is not None
            and canonical_etf_run is not None
            and source_signal_run_id != canonical_etf_run.id
        ):
            raise ValueError("指定的 ETF 顾问来源不是当前 canonical 排名快照。")
    signal_run = (
        canonical_etf_run
        if canonical_etf_run is not None
        else explicit_signal_run
        if source_signal_run_id is not None
        else await latest_signal_run(session, asset_type=asset_type, theme=theme, codes=codes)
    )
    if default_source_request and canonical_etf_run is None and signal_run is not None:
        latest_source_has_etf = (
            await session.scalar(
                select(ShortResearchSignalItem.id)
                .where(
                    ShortResearchSignalItem.run_id == signal_run.id,
                    ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
                )
                .limit(1)
            )
            is not None
        )
        if latest_source_has_etf:
            raise ValueError("当前 canonical ETF 排名快照不可用，不能回退旧 ETF 顾问来源。")
    if source_signal_run_id is not None and (
        signal_run is None or signal_run.status != "success" or signal_run.finished_at is None
    ):
        raise ValueError("指定的顾问来源快照不存在或尚未完成。")
    if signal_run is None:
        if default_source_request:
            raise ValueError("当前没有可用的 canonical ETF 或基金顾问来源快照。")
        signal_run = await run_signal_generation(
            session,
            as_of_date=as_of_date,
            asset_type=asset_type,
            theme=theme,
            codes=codes,
        )

    limit = max_assets or settings.llm_advisor_max_assets
    items, held_codes = await select_items_for_advisor(session, signal_run, max_assets=limit)
    client = llm_client or LLMClient(settings)
    succeeded = 0
    failed = 0
    fallback = 0
    partial_fallback = 0
    failures: list[dict[str, str]] = []

    for item in items:
        is_held = item.asset_type == "fund" and item.asset_code in held_codes
        rule_action = conservative_action_for_item(item, is_held=is_held)
        snapshot = _deterministic_snapshot(item, is_held=is_held)
        model_enabled = settings.llm_advisor_enabled and bool(settings.openai_api_key)
        if not model_enabled:
            report = validate_advisor_payload(
                fallback_report(
                    item,
                    is_held=is_held,
                    reason="未配置或未启用大模型",
                ),
                rule_action=rule_action,
            )
            await _upsert_report(
                session,
                signal_run=signal_run,
                item=item,
                report=report,
                model_name=settings.model_name,
                prompt_version=settings.llm_advisor_prompt_version,
                source="fallback",
                snapshot=snapshot,
                raw_response={"fallback_reason": "llm_disabled"},
            )
            fallback += 1
            await session.commit()
            continue

        attempt = ShortResearchAdvisorAttempt(
            signal_run_id=signal_run.id,
            signal_item_id=item.id,
            asset_type=item.asset_type,
            asset_code=item.asset_code,
            status="running",
            model_name=client.model_name,
            prompt_version=settings.llm_advisor_prompt_version,
            request_json=snapshot,
            response_json={},
        )
        session.add(attempt)
        await session.commit()
        try:
            raw_content = await client.generate_short_research_report(
                snapshot,
                response_schema=PROMPT_SCHEMA,
                timeout_seconds=settings.llm_advisor_timeout_seconds,
            )
            model_fallback = fallback_report(
                item,
                is_held=is_held,
                reason="模型输出不完整，已用规则解释补齐",
            )
            report = validate_advisor_payload(
                raw_content,
                rule_action=rule_action,
                fallback_payload=model_fallback,
            )
            source = "partial_fallback" if report.get("_fallback_used") else "llm"
            if source == "partial_fallback":
                partial_fallback += 1
            attempt.status = "success"
            attempt.finished_at = utcnow()
            attempt.response_json = report
            await _upsert_report(
                session,
                signal_run=signal_run,
                item=item,
                report=report,
                model_name=client.model_name,
                prompt_version=settings.llm_advisor_prompt_version,
                source=source,
                snapshot=snapshot,
                raw_response=report,
            )
            succeeded += 1
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            failure = {"asset_code": item.asset_code, "error": str(exc)}
            failures.append(failure)
            attempt.status = "failed"
            attempt.finished_at = utcnow()
            attempt.error_message = str(exc)
            report = validate_advisor_payload(
                fallback_report(item, is_held=is_held, reason="模型返回不可用"),
                rule_action=rule_action,
            )
            await _upsert_report(
                session,
                signal_run=signal_run,
                item=item,
                report=report,
                model_name=client.model_name,
                prompt_version=settings.llm_advisor_prompt_version,
                source="fallback",
                snapshot=snapshot,
                raw_response={"fallback_reason": str(exc)},
            )
            fallback += 1
            await session.commit()

    return {
        "signal_run_id": signal_run.id,
        "source_signal_run_id": signal_run.id,
        "as_of_date": signal_run.as_of_date.isoformat(),
        "selected": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "fallback": fallback,
        "partial_fallback": partial_fallback,
        "model_enabled": settings.llm_advisor_enabled and bool(settings.openai_api_key),
        "failures": failures,
    }
