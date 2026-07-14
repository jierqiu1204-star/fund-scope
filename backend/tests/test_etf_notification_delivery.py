from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from email.message import EmailMessage

import pytest
from sqlalchemy import select

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)
from app.services.notifier import (
    NotificationDeliveryResult,
    Notifier,
    SMTPError,
    claim_notification_envelope,
    record_notification_delivery,
)

NOW = datetime(2026, 7, 14, 15, 10)
MESSAGE_ID = f"<fundscope-{'a' * 64}@fundscope.local>"


async def _seed_envelope(app) -> tuple[int, int]:
    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="513520",
            asset_name="测试ETF",
            buy_date=date(2026, 7, 1),
            confirmed_shares=1000.0,
            estimated_shares=1000.0,
            buy_amount=1000.0,
            exit_state_json={},
            status="active",
        )
        session.add(position)
        await session.flush()
        action = TrackedPositionActionDecision(
            user_id=1,
            tracked_position_id=position.id,
            position_episode_id="position-episode-1",
            exposure_version=1,
            policy_version="policy-v1",
            action_cycle_id="action-cycle-1",
            target_stage="remaining_5000bp",
            target_remaining_fraction=0.5,
            baseline_normalized_quantity=1000.0,
            baseline_adjustment_factor=1.0,
            baseline_source="confirmed_shares",
            target_normalized_quantity=500.0,
            input_snapshot_hash="b" * 64,
            data_state="eligible",
            status="proposed",
            execution_provenance="none",
            contributing_rules_json=["confirmed_trend_weakening"],
            alert_episode_ids_json=["alert-episode-1"],
            is_current=True,
        )
        session.add(action)
        await session.flush()
        envelope = TrackedPositionNotificationEnvelope(
            user_id=1,
            trade_session=date(2026, 7, 14),
            route="ordinary_digest",
            severity="warning",
            channel="email",
            sealed_snapshot_hash="c" * 64,
            digest_revision=1,
            status="pending",
            message_id=MESSAGE_ID,
            template_name="tracked_position_action_v2.html.j2",
            template_version="tracked-position-notification-v2",
            rendered_subject="动作建议待确认",
            rendered_body="<p>未自动执行</p>",
            rendered_content_hash="d" * 64,
        )
        session.add(envelope)
        await session.flush()
        item = TrackedPositionNotificationItem(
            user_id=1,
            tracked_position_id=position.id,
            action_decision_id=action.id,
            alert_episode_id="alert-episode-1",
            transition="firing",
            recipient="owner@example.com",
            channel="email",
            repeat_slot="2026-07-14:close",
            route="ordinary_digest",
            severity="warning",
            payload_json={
                "policy_version": "policy-v1",
                "data_source": "eastmoney_adjusted",
                "quote_freshness": "close_final",
                "data_state": "eligible",
            },
            envelope_id=envelope.id,
            status="sealed",
        )
        session.add(item)
        await session.flush()
        envelope.sealed_at = NOW
        await session.commit()
        return envelope.id, action.id


@pytest.mark.asyncio
async def test_atomic_claim_blocks_competitor_and_expired_lease_reuses_identity(app) -> None:
    envelope_id, _ = await _seed_envelope(app)
    async with app.state.db.session() as session:
        first = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=30,
        )
        await session.commit()
    async with app.state.db.session() as session:
        competing = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-2",
            now=NOW + timedelta(seconds=1),
            lease_seconds=30,
        )
        await session.commit()
    async with app.state.db.session() as session:
        reclaimed = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-2",
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
        )
        await session.commit()

    assert first is not None
    assert competing is None
    assert reclaimed is not None
    assert reclaimed.claim_token != first.claim_token
    assert reclaimed.message_id == first.message_id == MESSAGE_ID
    assert reclaimed.attempt_count == 2


@pytest.mark.asyncio
async def test_claim_and_result_refresh_preloaded_envelope_projection(app) -> None:
    envelope_id, _ = await _seed_envelope(app)
    async with app.state.db.session() as session:
        envelope = await session.get(TrackedPositionNotificationEnvelope, envelope_id)
        assert envelope is not None and envelope.attempt_count == 0

        claim = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=30,
        )
        assert claim is not None
        assert claim.attempt_count == 1
        assert envelope.status == "claimed"
        assert envelope.attempt_count == 1

        recorded = await record_notification_delivery(
            session,
            claim=claim,
            result=NotificationDeliveryResult(status="smtp_accepted"),
            occurred_at=NOW + timedelta(seconds=2),
        )
        assert recorded is True
        assert envelope.status == "smtp_accepted"
        assert envelope.smtp_accepted_at == NOW + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_fixed_message_id_smtp_acceptance_is_audited_without_executing_action(app) -> None:
    envelope_id, action_id = await _seed_envelope(app)
    sent: list[EmailMessage] = []

    async def sender(message: EmailMessage) -> None:
        sent.append(message)

    async with app.state.db.session() as session:
        claim = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=30,
        )
        await session.commit()
    assert claim is not None
    result = await Notifier().deliver_claimed_envelope(claim, sender=sender)
    async with app.state.db.session() as session:
        recorded = await record_notification_delivery(
            session,
            claim=claim,
            result=result,
            occurred_at=NOW + timedelta(seconds=2),
        )
        await session.commit()
    async with app.state.db.session() as session:
        envelope = await session.get(TrackedPositionNotificationEnvelope, envelope_id)
        action = await session.get(TrackedPositionActionDecision, action_id)
        audit = await session.scalar(select(TrackedPositionAlertAudit))

    assert recorded is True
    assert len(sent) == 1 and sent[0]["Message-ID"] == MESSAGE_ID
    assert envelope is not None and envelope.status == "smtp_accepted"
    assert envelope.smtp_accepted_at == NOW + timedelta(seconds=2)
    assert action is not None and action.status == "proposed"
    assert audit is not None
    assert audit.smtp_result == "smtp_accepted"
    assert audit.notification_envelope_id == envelope_id
    assert audit.recipient != "owner@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "notifier",
    [
        Notifier(),
        Notifier(
            smtp_host="smtp.example.com",
            smtp_from="FundScope <mailer@example.com>",
        ),
    ],
)
async def test_missing_or_example_smtp_configuration_never_reports_acceptance(
    app,
    notifier: Notifier,
) -> None:
    envelope_id, _ = await _seed_envelope(app)
    async with app.state.db.session() as session:
        claim = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=30,
        )
    assert claim is not None

    result = await notifier.deliver_claimed_envelope(claim)

    assert result.status == "failed"
    assert result.error_redacted == "SMTPError:send_failed"


@pytest.mark.asyncio
async def test_message_construction_failure_is_recorded_instead_of_escaping(app) -> None:
    envelope_id, _ = await _seed_envelope(app)
    async with app.state.db.session() as session:
        claim = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=30,
        )
    assert claim is not None

    result = await Notifier().deliver_claimed_envelope(
        replace(claim, subject="invalid\nsubject"),
        sender=lambda _message: pytest.fail("invalid message must not be sent"),
    )

    assert result.status == "failed"
    assert result.error_redacted == "ValueError:send_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (SMTPError("password=hunter2 recipient=owner@example.com"), "failed"),
        (TimeoutError(), "unknown"),
        (ConnectionResetError("connection reset after DATA"), "unknown"),
        (ConnectionRefusedError("connection refused before SMTP"), "failed"),
    ],
)
async def test_failed_and_unknown_results_are_redacted_and_retry_same_envelope(
    app,
    failure: Exception,
    expected_status: str,
) -> None:
    envelope_id, _ = await _seed_envelope(app)

    async def sender(_message: EmailMessage) -> None:
        raise failure

    async with app.state.db.session() as session:
        first_claim = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=30,
        )
        await session.commit()
    assert first_claim is not None
    result = await Notifier().deliver_claimed_envelope(first_claim, sender=sender)
    async with app.state.db.session() as session:
        await record_notification_delivery(
            session,
            claim=first_claim,
            result=result,
            occurred_at=NOW + timedelta(seconds=2),
        )
        await session.commit()
    async with app.state.db.session() as session:
        retry_claim = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-2",
            now=NOW + timedelta(seconds=3),
            lease_seconds=30,
        )
        await session.commit()
        envelope = await session.get(TrackedPositionNotificationEnvelope, envelope_id)

    assert result.status == expected_status
    assert result.error_redacted is not None
    assert "hunter2" not in result.error_redacted
    assert "owner@example.com" not in result.error_redacted
    assert retry_claim is not None
    assert retry_claim.message_id == first_claim.message_id
    assert envelope is not None and envelope.attempt_count == 2


@pytest.mark.asyncio
async def test_smtp_acceptance_before_local_commit_can_reclaim_at_least_once(app) -> None:
    envelope_id, _ = await _seed_envelope(app)
    sent: list[str] = []

    async def sender(message: EmailMessage) -> None:
        sent.append(str(message["Message-ID"]))

    async with app.state.db.session() as session:
        first_claim = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-1",
            now=NOW,
            lease_seconds=30,
        )
        await session.commit()
    assert first_claim is not None
    accepted_but_not_recorded = await Notifier().deliver_claimed_envelope(
        first_claim, sender=sender
    )
    assert accepted_but_not_recorded.status == "smtp_accepted"

    async with app.state.db.session() as session:
        before_expiry = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-2",
            now=NOW + timedelta(seconds=29),
            lease_seconds=30,
        )
        await session.commit()
    async with app.state.db.session() as session:
        after_expiry = await claim_notification_envelope(
            session,
            envelope_id=envelope_id,
            worker_id="worker-2",
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
        )
        await session.commit()

    assert before_expiry is None
    assert after_expiry is not None
    assert after_expiry.message_id == first_claim.message_id
    assert sent == [MESSAGE_ID]
