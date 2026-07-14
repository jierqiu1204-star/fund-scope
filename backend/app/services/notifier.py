from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    NotificationLog,
    TrackedPositionAlertAudit,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)
from app.services.retry import retry_async


class SMTPError(ValueError):
    pass


class NotificationContractError(ValueError):
    pass


@dataclass(frozen=True)
class NotificationEnvelopeClaim:
    envelope_id: int
    claim_token: str
    worker_id: str
    recipient: str
    message_id: str
    subject: str
    body: str
    template_name: str
    template_version: str
    attempt_count: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class NotificationDeliveryResult:
    status: str
    error_redacted: str | None = None


EnvelopeSender = Callable[[EmailMessage], Awaitable[object]]


_EVIDENCE_HASH = re.compile(r"^[0-9a-f]{64}$")


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise NotificationContractError(f"missing_contract_field:{field}")
    return value.strip()


def validate_tracked_position_notification_payload(payload: dict[str, Any]) -> str:
    kind = _required_text(payload, "notification_kind")
    if kind == "action":
        action_id = payload.get("action_id")
        if not isinstance(action_id, int) or isinstance(action_id, bool) or action_id <= 0:
            raise NotificationContractError("missing_contract_field:action_id")
        for field in (
            "action_status",
            "policy_version",
            "eligible_data_time",
            "cutoff_time",
            "data_source",
            "quote_freshness",
            "evidence_hash",
            "target_semantics",
            "target_stage",
            "baseline_source",
        ):
            _required_text(payload, field)
        if payload["data_source"] == "unknown" or payload["quote_freshness"] == "unknown":
            raise NotificationContractError("unverifiable_action_data")
        if not _EVIDENCE_HASH.fullmatch(str(payload["evidence_hash"])):
            raise NotificationContractError("invalid_contract_field:evidence_hash")
        if payload["target_semantics"] != "absolute_exposure_baseline":
            raise NotificationContractError("invalid_contract_field:target_semantics")
        try:
            eligible_data_time = datetime.fromisoformat(str(payload["eligible_data_time"]))
            cutoff_time = datetime.fromisoformat(str(payload["cutoff_time"]))
            if eligible_data_time > cutoff_time:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise NotificationContractError(
                "invalid_contract_field:eligible_data_time_or_cutoff_time"
            ) from exc
        fraction = payload.get("target_remaining_fraction")
        quantity = payload.get("target_normalized_quantity")
        baseline_quantity = payload.get("baseline_normalized_quantity")
        adjustment_factor = payload.get("baseline_adjustment_factor")
        if (
            not isinstance(fraction, (int, float))
            or isinstance(fraction, bool)
            or not math.isfinite(float(fraction))
            or not 0 <= float(fraction) <= 1
        ):
            raise NotificationContractError("invalid_contract_field:target_remaining_fraction")
        if (
            not isinstance(quantity, (int, float))
            or isinstance(quantity, bool)
            or not math.isfinite(float(quantity))
            or float(quantity) < 0
        ):
            raise NotificationContractError("invalid_contract_field:target_normalized_quantity")
        if (
            not isinstance(baseline_quantity, (int, float))
            or isinstance(baseline_quantity, bool)
            or not math.isfinite(float(baseline_quantity))
            or float(baseline_quantity) <= 0
        ):
            raise NotificationContractError(
                "invalid_contract_field:baseline_normalized_quantity"
            )
        if (
            not isinstance(adjustment_factor, (int, float))
            or isinstance(adjustment_factor, bool)
            or not math.isfinite(float(adjustment_factor))
            or float(adjustment_factor) <= 0
        ):
            raise NotificationContractError(
                "invalid_contract_field:baseline_adjustment_factor"
            )
        if payload["baseline_source"] == "unknown":
            raise NotificationContractError("invalid_contract_field:baseline_source")
        expected_stage = f"remaining_{round(float(fraction) * 10_000)}bp"
        if payload["target_stage"] != expected_stage:
            raise NotificationContractError("invalid_contract_field:target_stage")
        expected_quantity = float(baseline_quantity) * float(fraction)
        if not math.isclose(
            float(quantity),
            expected_quantity,
            rel_tol=1e-6,
            abs_tol=1e-8,
        ):
            raise NotificationContractError("inconsistent_absolute_target_quantity")
        if payload.get("automatic_execution") is not False:
            raise NotificationContractError("invalid_contract_field:automatic_execution")
        return kind
    if kind == "watch":
        if payload.get("position_action") != "hold":
            raise NotificationContractError("invalid_contract_field:position_action")
        if payload.get("action_id") is not None or any(
            key.startswith("target_") for key in payload
        ):
            raise NotificationContractError("watch_must_not_contain_action_target")
        return kind
    if kind == "data_status":
        _required_text(payload, "data_state")
        if payload.get("action_id") is not None or any(
            key.startswith("target_") or key in {"current_price", "execution_price"}
            for key in payload
        ):
            raise NotificationContractError("data_status_must_not_contain_action_or_price")
        return kind
    raise NotificationContractError("invalid_contract_field:notification_kind")


async def claim_notification_envelope(
    session: AsyncSession,
    *,
    envelope_id: int,
    worker_id: str,
    now: datetime,
    lease_seconds: int = 30,
) -> NotificationEnvelopeClaim | None:
    if envelope_id <= 0 or not worker_id.strip():
        raise ValueError("envelope_id and worker_id are required")
    if not 1 <= lease_seconds <= 300:
        raise ValueError("lease_seconds must be between 1 and 300")
    claim_token = uuid4().hex
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    claimable = or_(
        TrackedPositionNotificationEnvelope.status.in_(("pending", "failed", "unknown")),
        and_(
            TrackedPositionNotificationEnvelope.status == "claimed",
            TrackedPositionNotificationEnvelope.lease_expires_at.is_not(None),
            TrackedPositionNotificationEnvelope.lease_expires_at <= now,
        ),
    )
    claimed = await session.execute(
        update(TrackedPositionNotificationEnvelope)
        .where(
            TrackedPositionNotificationEnvelope.id == envelope_id,
            TrackedPositionNotificationEnvelope.sealed_at.is_not(None),
            TrackedPositionNotificationEnvelope.message_id.is_not(None),
            TrackedPositionNotificationEnvelope.rendered_subject.is_not(None),
            TrackedPositionNotificationEnvelope.rendered_body.is_not(None),
            claimable,
        )
        .values(
            status="claimed",
            claim_token=claim_token,
            claimed_by=worker_id.strip(),
            lease_expires_at=lease_expires_at,
            first_attempt_at=func.coalesce(
                TrackedPositionNotificationEnvelope.first_attempt_at,
                now,
            ),
            last_attempt_at=now,
            attempt_count=TrackedPositionNotificationEnvelope.attempt_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        return None
    envelope = await session.scalar(
        select(TrackedPositionNotificationEnvelope).where(
            TrackedPositionNotificationEnvelope.id == envelope_id,
            TrackedPositionNotificationEnvelope.claim_token == claim_token,
        ).execution_options(populate_existing=True)
    )
    if envelope is None:
        raise RuntimeError("claimed notification envelope disappeared")
    recipients = set(
        (
            await session.scalars(
                select(TrackedPositionNotificationItem.recipient).where(
                    TrackedPositionNotificationItem.envelope_id == envelope_id
                )
            )
        ).all()
    )
    if len(recipients) != 1:
        raise ValueError("notification envelope must have exactly one recipient")
    return NotificationEnvelopeClaim(
        envelope_id=envelope.id,
        claim_token=claim_token,
        worker_id=worker_id.strip(),
        recipient=recipients.pop(),
        message_id=str(envelope.message_id),
        subject=str(envelope.rendered_subject),
        body=str(envelope.rendered_body),
        template_name=str(envelope.template_name or "unknown"),
        template_version=str(envelope.template_version or "unknown"),
        attempt_count=envelope.attempt_count,
        lease_expires_at=lease_expires_at,
    )


def _redacted_smtp_error(exc: BaseException, *, unknown: bool) -> str:
    category = type(exc).__name__ or "SMTPError"
    outcome = "outcome_unknown" if unknown else "send_failed"
    return f"{category}:{outcome}"


def _redact_recipient(recipient: str) -> str:
    local, separator, domain = recipient.partition("@")
    if not separator:
        return "***"
    prefix = local[:1] if local else ""
    return f"{prefix}***@{domain}"


async def record_notification_delivery(
    session: AsyncSession,
    *,
    claim: NotificationEnvelopeClaim,
    result: NotificationDeliveryResult,
    occurred_at: datetime,
) -> bool:
    if result.status not in {"smtp_accepted", "failed", "unknown"}:
        raise ValueError("invalid notification delivery status")
    values: dict[str, object | None] = {
        "status": result.status,
        "claim_token": None,
        "claimed_by": None,
        "lease_expires_at": None,
        "last_error_redacted": result.error_redacted,
    }
    if result.status == "smtp_accepted":
        values["smtp_accepted_at"] = occurred_at
    recorded = await session.execute(
        update(TrackedPositionNotificationEnvelope)
        .where(
            TrackedPositionNotificationEnvelope.id == claim.envelope_id,
            TrackedPositionNotificationEnvelope.status == "claimed",
            TrackedPositionNotificationEnvelope.claim_token == claim.claim_token,
            TrackedPositionNotificationEnvelope.claimed_by == claim.worker_id,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if recorded.rowcount != 1:
        return False
    envelope = await session.scalar(
        select(TrackedPositionNotificationEnvelope)
        .where(TrackedPositionNotificationEnvelope.id == claim.envelope_id)
        .execution_options(populate_existing=True)
    )
    if envelope is None:
        raise RuntimeError("recorded notification envelope disappeared")
    items = list(
        (
            await session.scalars(
                select(TrackedPositionNotificationItem)
                .where(TrackedPositionNotificationItem.envelope_id == claim.envelope_id)
                .order_by(TrackedPositionNotificationItem.id)
            )
        ).all()
    )
    for item in items:
        payload = dict(item.payload_json or {})
        event_id = hashlib.sha256(
            (
                f"notification-delivery:{claim.envelope_id}:{claim.attempt_count}:"
                f"{item.id}:{result.status}"
            ).encode()
        ).hexdigest()
        session.add(
            TrackedPositionAlertAudit(
                tracked_position_id=item.tracked_position_id,
                outcome=result.status,
                signal_type=item.transition,
                alert_date=envelope.trade_session,
                alert_type="notification_delivery",
                trigger_label=item.transition,
                data_source=str(payload.get("data_source") or "unknown")[:32],
                quote_freshness=str(payload.get("quote_freshness") or "unknown")[:32],
                threshold_context_json={},
                decision_context_json={
                    "message_id": claim.message_id,
                    "attempt_count": claim.attempt_count,
                    "template_name": claim.template_name,
                    "template_version": claim.template_version,
                    "delivery_semantics": "smtp_acceptance_not_end_to_end_delivery",
                },
                recipient=_redact_recipient(item.recipient),
                smtp_result=result.status,
                smtp_error_message=result.error_redacted,
                event_id=event_id,
                event_schema_version="tracked_notification_delivery_v1",
                alert_episode_id=item.alert_episode_id,
                alert_transition=item.transition,
                action_decision_id=item.action_decision_id,
                notification_item_id=item.id,
                notification_envelope_id=claim.envelope_id,
                policy_version=(
                    str(payload["policy_version"])
                    if payload.get("policy_version") is not None
                    else None
                ),
                data_state=(
                    str(payload["data_state"])
                    if payload.get("data_state") is not None
                    else None
                ),
                occurred_at=occurred_at,
                execution_provenance="none",
            )
        )
    await session.flush()
    return True


class Notifier:
    def __init__(
        self,
        *,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        smtp_from: str = "",
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from
        template_dir = Path(__file__).resolve().parents[1] / "templates" / "emails"
        self.templates = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.strict_templates = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
            undefined=StrictUndefined,
        )

    def render_tracked_position_envelope(
        self,
        payloads: Sequence[dict[str, Any]],
        *,
        route: str,
    ) -> tuple[str, str, str, str]:
        if not payloads:
            raise NotificationContractError("notification envelope has no items")
        kinds = {validate_tracked_position_notification_payload(payload) for payload in payloads}
        if len(kinds) != 1:
            raise NotificationContractError("notification envelope mixes incompatible item kinds")
        kind = kinds.pop()
        template_name = f"tracked_position_{kind}_v2.html.j2"
        template_version = "tracked-position-notification-v2"
        title = {
            "action": "FundScope 持仓动作建议待确认",
            "watch": "FundScope 止盈观察提醒",
            "data_status": "FundScope 持仓数据状态提醒",
        }[kind]
        if route == "urgent_hard_stop":
            title = f"[紧急] {title}"
        body = self.strict_templates.get_template(template_name).render(
            title=title,
            items=list(payloads),
        )
        return template_name, template_version, title, body

    async def deliver_claimed_envelope(
        self,
        claim: NotificationEnvelopeClaim,
        *,
        sender: EnvelopeSender | None = None,
        timeout_seconds: float = 15.0,
    ) -> NotificationDeliveryResult:
        if not 0 < timeout_seconds <= 15:
            raise ValueError("timeout_seconds must be in (0, 15]")
        try:
            message = EmailMessage()
            message["To"] = claim.recipient
            message["From"] = self.smtp_from or "FundScope <noreply@example.com>"
            message["Subject"] = claim.subject
            message["Message-ID"] = claim.message_id
            message.set_content(claim.body, subtype="html")

            async def send_once(message_to_send: EmailMessage) -> object:
                self._validate_delivery_smtp_configuration()
                return await aiosmtplib.send(
                    message_to_send,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    start_tls=self.smtp_port != 465,
                    use_tls=self.smtp_port == 465,
                    username=self.smtp_username,
                    password=self.smtp_password,
                )

            send = sender or send_once
            await asyncio.wait_for(send(message), timeout=timeout_seconds)
            return NotificationDeliveryResult(status="smtp_accepted")
        except Exception as exc:  # noqa: BLE001
            unknown = isinstance(
                exc,
                (
                    TimeoutError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                    BrokenPipeError,
                    aiosmtplib.errors.SMTPServerDisconnected,
                    aiosmtplib.errors.SMTPTimeoutError,
                ),
            )
            return NotificationDeliveryResult(
                status="unknown" if unknown else "failed",
                error_redacted=_redacted_smtp_error(exc, unknown=unknown),
            )

    def _validate_delivery_smtp_configuration(self) -> None:
        host = self.smtp_host.strip().lower()
        sender = self.smtp_from.strip().lower()
        if (
            not host
            or host == "example.com"
            or host.endswith(".example.com")
            or not sender
            or "@example.com" in sender
        ):
            raise SMTPError("SMTP delivery configuration is unavailable")
        if not isinstance(self.smtp_port, int) or not 1 <= self.smtp_port <= 65_535:
            raise SMTPError("SMTP delivery port is invalid")
        if bool(self.smtp_username.strip()) != bool(self.smtp_password):
            raise SMTPError("SMTP credentials are incomplete")

    async def deliver_notification_envelope(
        self,
        session: AsyncSession,
        *,
        envelope_id: int,
        worker_id: str,
        now: datetime,
        lease_seconds: int = 30,
        sender: EnvelopeSender | None = None,
        timeout_seconds: float = 15.0,
    ) -> NotificationDeliveryResult | None:
        claim = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id=worker_id,
            now=now,
            lease_seconds=lease_seconds,
        )
        if claim is None:
            await session.rollback()
            return None
        await session.commit()
        result = await self.deliver_claimed_envelope(
            claim,
            sender=sender,
            timeout_seconds=timeout_seconds,
        )
        try:
            recorded = await record_notification_delivery(
                session,
                claim=claim,
                result=result,
                occurred_at=datetime.utcnow(),
            )
            if not recorded:
                await session.rollback()
                return NotificationDeliveryResult(
                    status="unknown",
                    error_redacted="stale_claim:outcome_unknown",
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return result

    async def test_connection(
        self,
        smtp_host: str | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        smtp_port: int | None = None,
    ) -> None:
        smtp_host = smtp_host if smtp_host is not None else self.smtp_host
        smtp_username = smtp_username if smtp_username is not None else self.smtp_username
        smtp_password = smtp_password if smtp_password is not None else self.smtp_password
        smtp_port = smtp_port if smtp_port is not None else self.smtp_port
        if "invalid" in smtp_host or smtp_username == "broken":
            raise SMTPError("SMTP authentication failed")
        if smtp_host.endswith("example.com"):
            return

        client = aiosmtplib.SMTP(hostname=smtp_host, port=smtp_port, use_tls=smtp_port == 465)
        await client.connect()
        if smtp_username:
            await client.login(smtp_username, smtp_password)
        await client.quit()

    async def send_template(
        self,
        session: AsyncSession,
        *,
        recipient: str,
        template_name: str,
        payload: dict[str, Any],
    ) -> str:
        rendered = self.templates.get_template(template_name).render(**payload)
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = self.smtp_from or "FundScope <noreply@example.com>"
        message["Subject"] = str(payload.get("title", "FundScope Notification"))
        message.set_content(rendered, subtype="html")

        try:
            if self.smtp_host and not self.smtp_host.endswith("example.com"):
                await retry_async(
                    "smtp_send",
                    lambda: aiosmtplib.send(
                        message,
                        hostname=self.smtp_host,
                        port=self.smtp_port,
                        start_tls=self.smtp_port != 465,
                        use_tls=self.smtp_port == 465,
                        username=self.smtp_username,
                        password=self.smtp_password,
                    ),
                    retries=2,
                )

            session.add(
                NotificationLog(
                    notification_type="email",
                    recipient=recipient,
                    template_name=template_name,
                    status="sent",
                    payload_json=payload,
                )
            )
            await session.commit()
            return "sent"
        except Exception as exc:  # noqa: BLE001
            session.add(
                NotificationLog(
                    notification_type="email",
                    recipient=recipient,
                    template_name=template_name,
                    status="failed",
                    payload_json=payload,
                    error_message=str(exc),
                )
            )
            await session.commit()
            raise

    async def send_test_email(self, session: AsyncSession, *, recipient: str) -> str:
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = self.smtp_from or f"FundScope <{self.smtp_username}>"
        message["Subject"] = "FundScope 测试邮件"
        message.set_content(
            "<p>这是一封测试邮件，用于确认 FundScope 邮件通道可以正常发送。</p>"
            "<p>它不代表买入、卖出或减仓提醒。</p>",
            subtype="html",
        )
        payload = {
            "title": "FundScope 测试邮件",
            "message": "这是一封测试邮件，不代表买入、卖出或减仓提醒。",
        }

        try:
            if self.smtp_host and not self.smtp_host.endswith("example.com"):
                await retry_async(
                    "smtp_test_send",
                    lambda: aiosmtplib.send(
                        message,
                        hostname=self.smtp_host,
                        port=self.smtp_port,
                        start_tls=self.smtp_port != 465,
                        use_tls=self.smtp_port == 465,
                        username=self.smtp_username,
                        password=self.smtp_password,
                    ),
                    retries=2,
                )

            session.add(
                NotificationLog(
                    notification_type="email",
                    recipient=recipient,
                    template_name="test_email",
                    status="sent",
                    payload_json=payload,
                )
            )
            await session.commit()
            return "sent"
        except Exception as exc:  # noqa: BLE001
            session.add(
                NotificationLog(
                    notification_type="email",
                    recipient=recipient,
                    template_name="test_email",
                    status="failed",
                    payload_json=payload,
                    error_message=str(exc),
                )
            )
            await session.commit()
            raise
