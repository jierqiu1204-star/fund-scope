from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)
from app.schemas.tracked_positions import (
    TrackedPositionAlertAuditOut,
    TrackedPositionAuditCorrelationOut,
    TrackedPositionAuditDeliveryOut,
    TrackedPositionLifecycleStateOut,
)

MAX_CONTEXT_BYTES = 4096
MAX_TEXT_LENGTH = 512
MAX_LIST_ITEMS = 20

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "provider_payload",
    "raw_payload",
    "recipient",
    "secret",
    "smtp_error",
    "token",
)
_THRESHOLD_KEYS = frozenset(
    {
        "alert_source",
        "alert_type",
        "calibration_contract_hash",
        "calibration_coverage_status",
        "calibration_execution_model",
        "confirmation_required",
        "current_price",
        "current_price_date",
        "distance_to_hard_stop_pct",
        "distance_to_profit_start_pct",
        "distance_to_trailing_giveback_pct",
        "estimated_pnl_pct",
        "explanation",
        "hard_stop_pct",
        "holding_days",
        "intent",
        "max_profit_pct",
        "position_sizing",
        "profit_giveback_pct",
        "profit_start_pct",
        "reason_code",
        "recovery_required",
        "rule_version",
        "target_normalized_quantity",
        "target_remaining_fraction",
        "threshold_mode",
        "trailing_giveback_pct",
        "volatility_unit_pct",
    }
)
_DECISION_KEYS = frozenset(
    {
        "action_class",
        "action_disposition",
        "alert_level",
        "close_fact",
        "consensus_status",
        "data_reliability",
        "data_reason_code",
        "delivery_semantics",
        "email_eligibility_reason",
        "email_eligible",
        "email_status",
        "evaluation_mode",
        "event",
        "events",
        "explanation",
        "fresh_provider_count",
        "guard_reasons",
        "guard_state",
        "legacy_alert_row",
        "limitation_reason",
        "message_id",
        "next_exposure_version",
        "no_alert_reason",
        "notification_outcomes",
        "outcome",
        "previous_exposure_version",
        "price_diff_abs",
        "price_diff_pct",
        "provider_count",
        "quantity",
        "quote_time",
        "reasons",
        "repeat_slot",
        "request_hash",
        "resulting_shares",
        "risk_flags",
        "rule_ids",
        "sealed_snapshot_hash",
        "suppression_status",
        "template_name",
        "template_version",
    }
)
_ALERT_STATE_PRIORITY = {
    "unknown": 0,
    "normal": 1,
    "resolved": 2,
    "pending": 3,
    "recovering": 4,
    "firing": 5,
}


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:MAX_TEXT_LENGTH]
    if depth >= 2:
        return None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in sorted(value, key=str)[:MAX_LIST_ITEMS]:
            name = str(key)
            if _is_sensitive_key(name):
                continue
            safe = _safe_value(value[key], depth=depth + 1)
            if safe is not None:
                result[name] = safe
        return result
    if isinstance(value, (list, tuple)):
        return [
            safe
            for item in value[:MAX_LIST_ITEMS]
            if (safe := _safe_value(item, depth=depth + 1)) is not None
        ]
    return str(value)[:MAX_TEXT_LENGTH]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _bounded_context(source: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted(set(source).intersection(allowed)):
        if _is_sensitive_key(key):
            continue
        value = _safe_value(source[key])
        if value is None:
            continue
        candidate = {**result, key: value}
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) <= MAX_CONTEXT_BYTES:
            result = candidate
    return result


def redact_recipient(recipient: str | None) -> str | None:
    if not recipient:
        return None
    local, separator, domain = recipient.partition("@")
    if not separator or not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


def redact_smtp_error(error: str | None) -> str | None:
    if not error:
        return None
    lowered = error.lower()
    if "auth" in lowered or "credential" in lowered:
        return "smtp_authentication_error"
    if "timeout" in lowered:
        return "smtp_timeout"
    if "refused" in lowered or "recipient" in lowered:
        return "smtp_recipient_refused"
    if "disconnect" in lowered or "reset" in lowered:
        return "smtp_connection_unknown"
    return "smtp_error_redacted"


def _audit_summary(row: TrackedPositionAlertAudit) -> str:
    result = row.smtp_result or row.outcome
    if result in {"sent", "smtp_accepted"}:
        return "SMTP 已接受邮件；这不等于最终送达。"
    if result == "failed":
        return "发送失败；错误详情已脱敏。"
    if result == "unknown":
        return "发送状态未知；外部邮件可能重复，但不会重复创建或推进持仓动作。"
    if row.outcome == "web_only":
        return "仅在网页展示，未生成或发送交易动作邮件。"
    if row.outcome == "data_ineligible":
        return "当前数据不可用于决策，不发送卖出或减仓邮件。"
    if row.outcome == "suppressed":
        return (row.duplicate_reason or "重复提醒已抑制。")[:MAX_TEXT_LENGTH]
    return "已记录提醒评估结果。"


def lifecycle_state_for_position(
    position: TrackedPosition,
    *,
    latest_data_state: str | None = None,
    latest_data_reason_code: str | None = None,
) -> TrackedPositionLifecycleStateOut:
    state = dict(position.exit_state_json or {})
    alert_state = "unknown"
    alert_rule_states = state.get("alert_rule_states")
    if isinstance(alert_rule_states, Mapping):
        for value in alert_rule_states.values():
            candidate = value.get("state") if isinstance(value, Mapping) else value
            if not isinstance(candidate, str):
                continue
            if _ALERT_STATE_PRIORITY.get(candidate, 0) > _ALERT_STATE_PRIORITY.get(
                alert_state, 0
            ):
                alert_state = candidate

    outcome = state.get("evaluation_data_outcome")
    if latest_data_state is not None:
        data_state = latest_data_state
        data_reason_code = latest_data_reason_code
    else:
        data_state = state.get("last_evaluation_data_state")
        data_reason_code = state.get("last_evaluation_data_reason_code")
    if isinstance(outcome, Mapping) and latest_data_state is None:
        data_state = outcome.get("state", data_state)
        data_reason_code = outcome.get("reason_code", data_reason_code)
    return TrackedPositionLifecycleStateOut(
        alert_state=alert_state,
        data_state=str(data_state or "unknown"),
        data_reason_code=(str(data_reason_code) if data_reason_code else None),
    )


async def project_alert_audits(
    session: AsyncSession,
    *,
    owner_id: int,
    rows: Iterable[TrackedPositionAlertAudit],
) -> list[TrackedPositionAlertAuditOut]:
    audits = tuple(rows)
    action_ids = {row.action_decision_id for row in audits if row.action_decision_id}
    item_ids = {row.notification_item_id for row in audits if row.notification_item_id}
    envelope_ids = {
        row.notification_envelope_id for row in audits if row.notification_envelope_id
    }
    actions = await _load_by_id(
        session,
        TrackedPositionActionDecision,
        action_ids,
        owner_id=owner_id,
    )
    items = await _load_by_id(
        session,
        TrackedPositionNotificationItem,
        item_ids,
        owner_id=owner_id,
    )
    envelope_ids.update(item.envelope_id for item in items.values() if item.envelope_id)
    envelopes = await _load_by_id(
        session,
        TrackedPositionNotificationEnvelope,
        envelope_ids,
        owner_id=owner_id,
    )
    return [
        _project_alert_audit(row, actions=actions, items=items, envelopes=envelopes)
        for row in audits
    ]


async def _load_by_id(
    session: AsyncSession,
    model: type,
    row_ids: set[int],
    *,
    owner_id: int,
) -> dict[int, Any]:
    if not row_ids:
        return {}
    rows = await session.scalars(
        select(model).where(model.id.in_(row_ids), model.user_id == owner_id)
    )
    return {row.id: row for row in rows.all()}


def _project_alert_audit(
    row: TrackedPositionAlertAudit,
    *,
    actions: Mapping[int, TrackedPositionActionDecision],
    items: Mapping[int, TrackedPositionNotificationItem],
    envelopes: Mapping[int, TrackedPositionNotificationEnvelope],
) -> TrackedPositionAlertAuditOut:
    action = actions.get(row.action_decision_id) if row.action_decision_id else None
    item = items.get(row.notification_item_id) if row.notification_item_id else None
    envelope_id = row.notification_envelope_id or (item.envelope_id if item else None)
    envelope = envelopes.get(envelope_id) if envelope_id else None
    return TrackedPositionAlertAuditOut(
        id=row.id,
        tracked_position_id=row.tracked_position_id,
        tracked_position_alert_id=row.tracked_position_alert_id,
        outcome=row.outcome,
        signal_type=row.signal_type,
        alert_date=row.alert_date,
        alert_type=row.alert_type,
        trigger_label=row.trigger_label,
        data_source=row.data_source,
        quote_freshness=row.quote_freshness,
        threshold_context=_bounded_context(
            dict(row.threshold_context_json or {}), _THRESHOLD_KEYS
        ),
        decision_context=_bounded_context(
            dict(row.decision_context_json or {}), _DECISION_KEYS
        ),
        recipient=redact_recipient(row.recipient),
        duplicate_reason=(row.duplicate_reason[:MAX_TEXT_LENGTH] if row.duplicate_reason else None),
        cooldown_reason=(row.cooldown_reason[:MAX_TEXT_LENGTH] if row.cooldown_reason else None),
        smtp_result=row.smtp_result,
        smtp_error_message=redact_smtp_error(row.smtp_error_message),
        quote_time=row.quote_time,
        created_at=row.created_at,
        audit_summary=_audit_summary(row),
        correlation=TrackedPositionAuditCorrelationOut(
            event_id=row.event_id,
            event_schema_version=row.event_schema_version,
            position_episode_id=action.position_episode_id if action else None,
            exposure_version=action.exposure_version if action else None,
            action_cycle_id=action.action_cycle_id if action else None,
            alert_episode_id=row.alert_episode_id,
            action_decision_id=row.action_decision_id,
            notification_item_id=row.notification_item_id,
            notification_envelope_id=envelope_id,
            policy_version=row.policy_version,
            input_snapshot_hash=action.input_snapshot_hash if action else None,
            from_state=row.from_state,
            to_state=row.to_state,
            actor_id=row.actor_id,
            request_id=row.request_id,
            causation_id=row.causation_id,
            occurred_at=row.occurred_at,
            recorded_at=row.created_at,
            execution_provenance=row.execution_provenance,
        ),
        delivery=(
            TrackedPositionAuditDeliveryOut(
                item_status=item.status if item else None,
                suppression_reason=item.suppression_reason if item else None,
                repeat_slot=item.repeat_slot if item else None,
                envelope_status=envelope.status if envelope else None,
                message_id=envelope.message_id if envelope else None,
                attempt_count=envelope.attempt_count if envelope else None,
                first_attempt_at=envelope.first_attempt_at if envelope else None,
                last_attempt_at=envelope.last_attempt_at if envelope else None,
                smtp_accepted_at=envelope.smtp_accepted_at if envelope else None,
            )
            if item or envelope
            else None
        ),
    )


def sanitize_legacy_audit(
    value: TrackedPositionAlertAuditOut,
) -> TrackedPositionAlertAuditOut:
    payload = value.model_dump()
    payload.update(
        threshold_context=_bounded_context(value.threshold_context, _THRESHOLD_KEYS),
        decision_context=_bounded_context(value.decision_context, _DECISION_KEYS),
        recipient=redact_recipient(value.recipient),
        smtp_error_message=redact_smtp_error(value.smtp_error_message),
        audit_summary=(
            "SMTP 已接受邮件；这不等于最终送达。"
            if value.smtp_result == "sent"
            else value.audit_summary[:MAX_TEXT_LENGTH]
        ),
        correlation=TrackedPositionAuditCorrelationOut(
            recorded_at=value.created_at,
            execution_provenance="legacy_unverified",
        ),
        delivery=TrackedPositionAuditDeliveryOut(
            envelope_status=value.smtp_result,
            smtp_accepted_at=value.created_at if value.smtp_result == "sent" else None,
        ),
    )
    return TrackedPositionAlertAuditOut(**payload)
