from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.defaults.short_research import SHORT_RESEARCH_ASSET_BY_KEY
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
    "买入",
    "卖出",
    "止盈",
    "止损",
    "目标价",
    "保证收益",
    "稳赚",
    "自动交易",
    "支付宝实时同步",
    "立刻买",
    "马上买",
    "建议买",
    "建议卖",
    "buy",
    "sell",
    "take_profit",
    "stop_loss",
    "target_price",
    "expected_return",
    "guaranteed_profit",
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
        raise ValueError("LLM output action_label is not allowed")
    if _is_stronger(str(action_label), rule_action):
        action_label = rule_action

    report = {
        "action_label": action_label,
        "plain_summary": str(parsed.get("plain_summary", "")).strip()[:360],
        "opportunity": _coerce_string_list(parsed.get("opportunity"), "opportunity"),
        "risks": _coerce_string_list(parsed.get("risks"), "risks"),
        "opposing_view": str(parsed.get("opposing_view", "")).strip()[:500],
        "watch_conditions": _coerce_string_list(parsed.get("watch_conditions"), "watch_conditions"),
        "holding_note": str(parsed.get("holding_note", "")).strip()[:500],
        "data_limitations": str(parsed.get("data_limitations", "")).strip()[:500],
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
    return {
        "asset_type": item.asset_type,
        "asset_code": item.asset_code,
        "asset_name": _asset_name(item),
        "rank": item.rank,
        "total_score": item.total_score,
        "deterministic_label": item.conclusion,
        "rule_action": conservative_action_for_item(item, is_held=is_held),
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
    fallback_reason = f"AI 分析未更新：{reason}。" if reason else "当前使用规则解释。"
    risks = risk_flags or ["暂未触发主要风险标签，但短线结果仍可能很快变化。"]
    return {
        "action_label": action,
        "plain_summary": f"{name} 当前为{item.conclusion}，{fallback_reason}",
        "opportunity": [
            f"近 5 日 {_format_percent(metrics.get('return_5d'))}，近 20 日 {_format_percent(metrics.get('return_20d'))}。",
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
) -> dict[str, Any]:
    signal_run = await latest_signal_run(session)
    if signal_run is None:
        signal_run = await run_signal_generation(session)

    limit = max_assets or settings.llm_advisor_max_assets
    items, held_codes = await select_items_for_advisor(session, signal_run, max_assets=limit)
    client = llm_client or LLMClient(settings)
    succeeded = 0
    failed = 0
    fallback = 0
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
            report = validate_advisor_payload(raw_content, rule_action=rule_action)
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
                source="llm",
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
        "as_of_date": signal_run.as_of_date.isoformat(),
        "selected": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "fallback": fallback,
        "model_enabled": settings.llm_advisor_enabled and bool(settings.openai_api_key),
        "failures": failures,
    }
