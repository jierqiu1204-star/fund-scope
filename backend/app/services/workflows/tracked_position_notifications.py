from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)
from app.services.intraday_etf.exchange_calendar import is_trading_day, next_trading_day
from app.services.notifier import Notifier

MAX_DIGEST_REVISION_ATTEMPTS = 3


@dataclass(frozen=True)
class NotificationPolicyContext:
    trade_session: date
    transition: str
    notification_repeat: bool
    hard_stop: bool
    data_state: str
    existing_same_slot: bool
    user_silenced: bool
    higher_severity_active: bool
    soft_watch: bool = False


@dataclass(frozen=True)
class NotificationPolicyDecision:
    emit: bool
    reason_code: str | None
    transition: str
    notification_kind: str
    route: str
    severity: str
    action_oriented: bool


@dataclass(frozen=True)
class EnvelopeAssemblyCommand:
    owner_id: int
    trade_session: date
    item_ids: tuple[int, ...]
    sealed_snapshot_hash: str
    occurred_at: datetime


@dataclass(frozen=True)
class PersistNotificationItemCommand:
    owner_id: int
    position_id: int
    action_id: int | None
    alert_episode_id: str
    transition: str
    recipient: str
    channel: str
    repeat_slot: str
    route: str
    severity: str
    payload: dict[str, object]
    status: str
    suppression_reason: str | None = None
    next_eligible_repeat_slot: str | None = None


def trading_repeat_slot(trade_session: date) -> str:
    if not is_trading_day(trade_session):
        raise ValueError("repeat slot must use an exchange trading day")
    return f"{trade_session.isoformat()}:close"


def next_trading_repeat_slot(trade_session: date) -> str:
    return trading_repeat_slot(next_trading_day(trade_session))


def decide_notification(context: NotificationPolicyContext) -> NotificationPolicyDecision:
    if context.existing_same_slot:
        return _suppressed(context, "same_repeat_slot")
    if context.user_silenced:
        return _suppressed(context, "user_silenced")
    if context.higher_severity_active:
        return _suppressed(context, "higher_severity_active")
    if context.notification_repeat and context.data_state != "eligible":
        return NotificationPolicyDecision(
            emit=True,
            reason_code=None,
            transition="current_data_unverifiable",
            notification_kind="data_status",
            route="data_status",
            severity="warning",
            action_oriented=False,
        )
    if context.soft_watch:
        return NotificationPolicyDecision(
            emit=True,
            reason_code=None,
            transition=context.transition,
            notification_kind="watch",
            route="ordinary_digest",
            severity="warning",
            action_oriented=False,
        )
    return NotificationPolicyDecision(
        emit=True,
        reason_code=None,
        transition=context.transition,
        notification_kind="action",
        route="urgent_hard_stop" if context.hard_stop else "ordinary_digest",
        severity="critical" if context.hard_stop else "warning",
        action_oriented=True,
    )


def _suppressed(
    context: NotificationPolicyContext,
    reason_code: str,
) -> NotificationPolicyDecision:
    notification_kind = "watch" if context.soft_watch else "action"
    return NotificationPolicyDecision(
        emit=False,
        reason_code=reason_code,
        transition=context.transition,
        notification_kind=notification_kind,
        route="urgent_hard_stop" if context.hard_stop else "ordinary_digest",
        severity="critical" if context.hard_stop else "warning",
        action_oriented=not context.soft_watch,
    )


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def persist_notification_item(
    session: AsyncSession,
    command: PersistNotificationItemCommand,
) -> TrackedPositionNotificationItem:
    identity = (
        TrackedPositionNotificationItem.alert_episode_id == command.alert_episode_id,
        TrackedPositionNotificationItem.transition == command.transition,
        TrackedPositionNotificationItem.recipient == command.recipient,
        TrackedPositionNotificationItem.channel == command.channel,
        TrackedPositionNotificationItem.repeat_slot == command.repeat_slot,
    )
    existing = await session.scalar(select(TrackedPositionNotificationItem).where(*identity))
    if existing is not None:
        return existing
    item = TrackedPositionNotificationItem(
        user_id=command.owner_id,
        tracked_position_id=command.position_id,
        action_decision_id=command.action_id,
        alert_episode_id=command.alert_episode_id,
        transition=command.transition,
        recipient=command.recipient,
        channel=command.channel,
        repeat_slot=command.repeat_slot,
        route=command.route,
        severity=command.severity,
        payload_json=command.payload,
        status=command.status,
        suppression_reason=command.suppression_reason,
        next_eligible_repeat_slot=command.next_eligible_repeat_slot,
    )
    try:
        async with session.begin_nested():
            session.add(item)
            await session.flush()
        return item
    except IntegrityError:
        existing = await session.scalar(select(TrackedPositionNotificationItem).where(*identity))
        if existing is None:
            raise
        return existing


async def assemble_notification_envelope(
    session: AsyncSession,
    command: EnvelopeAssemblyCommand,
    *,
    notifier: Notifier,
) -> TrackedPositionNotificationEnvelope:
    item_ids = tuple(dict.fromkeys(command.item_ids))
    if not item_ids or len(item_ids) != len(command.item_ids):
        raise ValueError("notification item ids must be non-empty and unique")
    if len(command.sealed_snapshot_hash) != 64:
        raise ValueError("sealed_snapshot_hash must be a sha256 digest")

    items = list(
        (
            await session.scalars(
                select(TrackedPositionNotificationItem)
                .where(
                    TrackedPositionNotificationItem.id.in_(item_ids),
                    TrackedPositionNotificationItem.user_id == command.owner_id,
                )
                .order_by(TrackedPositionNotificationItem.id)
                .with_for_update()
            )
        ).all()
    )
    if len(items) != len(item_ids):
        raise LookupError("notification item not found for owner")
    routes = {item.route for item in items}
    severities = {item.severity for item in items}
    channels = {item.channel for item in items}
    recipients = {item.recipient for item in items}
    if any(len(values) != 1 for values in (routes, severities, channels, recipients)):
        raise ValueError("notification items have incompatible envelope identities")
    route = routes.pop()
    severity = severities.pop()
    channel = channels.pop()
    if route == "urgent_hard_stop" and len(items) != 1:
        raise ValueError("urgent hard-stop envelope requires a single item")
    if any(item.status == "suppressed" for item in items):
        raise ValueError("suppressed notification item cannot be delivered")

    assigned = {item.envelope_id for item in items if item.envelope_id is not None}
    if assigned:
        if len(assigned) != 1 or any(item.envelope_id is None for item in items):
            raise ValueError("sealed and unsealed items cannot be assembled together")
        envelope = await session.get(TrackedPositionNotificationEnvelope, assigned.pop())
        if (
            envelope is None
            or envelope.user_id != command.owner_id
            or envelope.trade_session != command.trade_session
            or envelope.sealed_snapshot_hash != command.sealed_snapshot_hash
            or envelope.sealed_at is None
            or any(item.status != "sealed" for item in items)
        ):
            raise ValueError("notification envelope identity mismatch")
        return envelope

    base_filter = (
        TrackedPositionNotificationEnvelope.user_id == command.owner_id,
        TrackedPositionNotificationEnvelope.trade_session == command.trade_session,
        TrackedPositionNotificationEnvelope.route == route,
        TrackedPositionNotificationEnvelope.severity == severity,
        TrackedPositionNotificationEnvelope.channel == channel,
        TrackedPositionNotificationEnvelope.sealed_snapshot_hash
        == command.sealed_snapshot_hash,
    )
    payloads = [dict(item.payload_json or {}) for item in items]
    template_name, template_version, subject, body = notifier.render_tracked_position_envelope(
        payloads,
        route=route,
    )
    envelope: TrackedPositionNotificationEnvelope | None = None
    last_conflict: IntegrityError | None = None
    for _attempt in range(MAX_DIGEST_REVISION_ATTEMPTS):
        latest_revision = await session.scalar(
            select(func.max(TrackedPositionNotificationEnvelope.digest_revision)).where(
                *base_filter
            )
        )
        digest_revision = int(latest_revision or 0) + 1
        identity = {
            "owner_id": command.owner_id,
            "trade_session": command.trade_session.isoformat(),
            "route": route,
            "severity": severity,
            "channel": channel,
            "snapshot": command.sealed_snapshot_hash,
            "revision": digest_revision,
            "item_ids": [item.id for item in items],
        }
        message_id = f"<fundscope-{_stable_hash(identity)}@fundscope.local>"
        rendered_content_hash = _stable_hash(
            {
                "template_name": template_name,
                "template_version": template_version,
                "subject": subject,
                "body": body,
                "message_id": message_id,
            }
        )
        candidate = TrackedPositionNotificationEnvelope(
            user_id=command.owner_id,
            trade_session=command.trade_session,
            route=route,
            severity=severity,
            channel=channel,
            sealed_snapshot_hash=command.sealed_snapshot_hash,
            digest_revision=digest_revision,
            status="pending",
            message_id=message_id,
            template_name=template_name,
            template_version=template_version,
            rendered_subject=subject,
            rendered_body=body,
            rendered_content_hash=rendered_content_hash,
        )
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
        except IntegrityError as exc:
            last_conflict = exc
            continue
        envelope = candidate
        break
    if envelope is None:
        raise RuntimeError("digest revision allocation conflict limit reached") from last_conflict

    for item in items:
        item.envelope_id = envelope.id
        item.status = "sealed"
    await session.flush()
    envelope.sealed_at = command.occurred_at
    await session.flush()
    return envelope
