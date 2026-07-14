from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    TrackedPositionAuditImmutableError,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
    User,
)


async def _seed_audit_history(app) -> tuple[int, tuple[int, ...]]:
    base = datetime(2026, 7, 14, 14, 50)
    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="513520",
            asset_name="日经ETF",
            buy_date=date(2026, 7, 1),
            confirmed_shares=1000.0,
            estimated_shares=1000.0,
            buy_amount=1000.0,
            entry_price=1.0,
            exit_state_json={
                "position_episode_id": "position-episode-1",
                "exposure_version": 2,
                "last_evaluation_data_state": "data_waiting",
                "last_evaluation_data_reason_code": "quote_stale",
                "alert_rule_states": {
                    "hard_stop": {"state": "firing"},
                    "trailing_take_profit": {"state": "pending"},
                },
            },
            exit_state_version=7,
            status="active",
        )
        session.add(position)
        await session.flush()

        action = TrackedPositionActionDecision(
            user_id=1,
            tracked_position_id=position.id,
            position_episode_id="position-episode-1",
            exposure_version=2,
            policy_version="policy-v2",
            action_cycle_id="cycle-1",
            target_stage="remaining_0bp",
            target_remaining_fraction=0.0,
            baseline_normalized_quantity=1000.0,
            baseline_account_weight=0.25,
            baseline_adjustment_factor=1.0,
            baseline_source="confirmed_shares",
            target_normalized_quantity=0.0,
            target_account_weight=0.0,
            input_snapshot_hash="a" * 64,
            data_state="eligible",
            status="proposed",
            execution_provenance="none",
            cumulative_executed_quantity=0.0,
            contributing_rules_json=["hard_stop"],
            alert_episode_ids_json=["alert-episode-1"],
            is_current=True,
            created_at=base,
            updated_at=base,
        )
        envelope = TrackedPositionNotificationEnvelope(
            user_id=1,
            trade_session=date(2026, 7, 14),
            route="urgent_single",
            severity="critical",
            channel="email",
            sealed_snapshot_hash="b" * 64,
            digest_revision=1,
            status="pending",
            message_id="<stable-message@example.com>",
            template_name="tracked_position_action_v2.html.j2",
            template_version="v2",
            sealed_at=None,
            first_attempt_at=None,
            last_attempt_at=None,
            attempt_count=0,
            smtp_accepted_at=None,
            created_at=base,
            updated_at=base,
        )
        session.add_all([action, envelope])
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
            route="urgent_single",
            severity="critical",
            payload_json={},
            status="pending",
            envelope_id=envelope.id,
            created_at=base + timedelta(minutes=2),
        )
        session.add(item)
        await session.flush()
        item.status = "sealed"
        envelope.status = "smtp_accepted"
        envelope.sealed_at = base
        envelope.first_attempt_at = base + timedelta(minutes=1)
        envelope.last_attempt_at = base + timedelta(minutes=2)
        envelope.attempt_count = 2
        envelope.smtp_accepted_at = base + timedelta(minutes=2)
        await session.flush()

        audits = [
            TrackedPositionAlertAudit(
                tracked_position_id=position.id,
                outcome="evaluated",
                signal_type="take_profit_watch",
                alert_date=date(2026, 7, 14),
                alert_type="take_profit_watch",
                trigger_label="仅观察",
                data_source="eastmoney",
                quote_freshness="fresh",
                threshold_context_json={"hard_stop_pct": -0.08},
                decision_context_json={"outcome": "web_only"},
                event_id="event-1",
                event_schema_version="v2",
                alert_episode_id="alert-episode-1",
                alert_transition="pending",
                policy_version="policy-v2",
                data_state="eligible",
                from_state="normal",
                to_state="pending",
                occurred_at=base,
                created_at=base,
            ),
            TrackedPositionAlertAudit(
                tracked_position_id=position.id,
                outcome="proposed",
                signal_type="hard_stop",
                alert_date=date(2026, 7, 14),
                alert_type="hard_stop",
                trigger_label="硬止损",
                data_source="eastmoney",
                quote_freshness="fresh",
                threshold_context_json={"hard_stop_pct": -0.08},
                decision_context_json={"outcome": "action_proposed"},
                event_id="event-2",
                event_schema_version="v2",
                alert_episode_id="alert-episode-1",
                alert_transition="firing",
                action_decision_id=action.id,
                policy_version="policy-v2",
                data_state="eligible",
                from_state="pending",
                to_state="firing",
                actor_id=1,
                request_id="request-1",
                causation_id="event-1",
                occurred_at=base + timedelta(minutes=1),
                created_at=base + timedelta(minutes=1),
            ),
            TrackedPositionAlertAudit(
                tracked_position_id=position.id,
                outcome="sent",
                signal_type="hard_stop",
                alert_date=date(2026, 7, 14),
                alert_type="hard_stop",
                trigger_label="硬止损",
                data_source="eastmoney",
                quote_freshness="fresh",
                threshold_context_json={
                    "hard_stop_pct": -0.08,
                    "smtp_password": "threshold-secret",
                    "provider_payload": {"token": "provider-secret"},
                },
                decision_context_json={
                    "outcome": "smtp_accepted",
                    "rule_ids": ["hard_stop"],
                    "explanation": "x" * 10_000,
                    "credentials": {"password": "decision-secret"},
                    "recipient": "owner@example.com",
                },
                recipient="owner@example.com",
                smtp_result="smtp_accepted",
                smtp_error_message="SMTPAuthenticationError password=mail-secret token=abc",
                event_id="event-3",
                event_schema_version="v2",
                alert_episode_id="alert-episode-1",
                alert_transition="firing",
                action_decision_id=action.id,
                notification_item_id=item.id,
                notification_envelope_id=envelope.id,
                policy_version="policy-v2",
                data_state="eligible",
                from_state="firing",
                to_state="firing",
                request_id="request-2",
                causation_id="event-2",
                occurred_at=base + timedelta(minutes=2),
                execution_provenance="none",
                created_at=base + timedelta(minutes=2),
            ),
        ]
        session.add_all(audits)
        await session.commit()
        return position.id, tuple(audit.id for audit in audits)


@pytest.mark.asyncio
async def test_audit_api_is_owner_scoped_cursor_paginated_and_safe(app, client) -> None:
    position_id, audit_ids = await _seed_audit_history(app)

    first = await client.get(f"/api/tracked-positions/{position_id}/audit?limit=2")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["total"] == 3
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"]
    assert [item["id"] for item in first_body["items"]] == [audit_ids[2], audit_ids[1]]

    latest = first_body["items"][0]
    serialized = json.dumps(latest, ensure_ascii=False)
    assert latest["recipient"] == "o***@example.com"
    assert latest["audit_summary"] == "SMTP 已接受邮件；这不等于最终送达。"
    assert latest["correlation"] == {
        "event_id": "event-3",
        "event_schema_version": "v2",
        "position_episode_id": "position-episode-1",
        "exposure_version": 2,
        "action_cycle_id": "cycle-1",
        "alert_episode_id": "alert-episode-1",
        "action_decision_id": latest["correlation"]["action_decision_id"],
        "notification_item_id": latest["correlation"]["notification_item_id"],
        "notification_envelope_id": latest["correlation"]["notification_envelope_id"],
        "policy_version": "policy-v2",
        "input_snapshot_hash": "a" * 64,
        "from_state": "firing",
        "to_state": "firing",
        "actor_id": None,
        "request_id": "request-2",
        "causation_id": "event-2",
        "occurred_at": "2026-07-14T14:52:00",
        "recorded_at": "2026-07-14T14:52:00",
        "execution_provenance": "none",
    }
    assert latest["delivery"]["item_status"] == "sealed"
    assert latest["delivery"]["envelope_status"] == "smtp_accepted"
    assert latest["delivery"]["message_id"] == "<stable-message@example.com>"
    assert latest["delivery"]["attempt_count"] == 2
    assert len(json.dumps(latest["threshold_context"], ensure_ascii=False).encode()) <= 4096
    assert len(json.dumps(latest["decision_context"], ensure_ascii=False).encode()) <= 4096
    for secret in (
        "threshold-secret",
        "provider-secret",
        "decision-secret",
        "mail-secret",
        "token=abc",
        "owner@example.com",
    ):
        assert secret not in serialized

    second = await client.get(
        f"/api/tracked-positions/{position_id}/audit",
        params={"limit": 2, "cursor": first_body["next_cursor"]},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["total"] == 3
    assert [item["id"] for item in second_body["items"]] == [audit_ids[0]]
    assert second_body["next_cursor"] is None
    assert {item["id"] for item in first_body["items"]}.isdisjoint(
        {item["id"] for item in second_body["items"]}
    )


@pytest.mark.asyncio
async def test_audit_api_rejects_invalid_page_requests_and_other_owner(app, client) -> None:
    position_id, _audit_ids = await _seed_audit_history(app)

    assert (
        await client.get(f"/api/tracked-positions/{position_id}/audit?limit=101")
    ).status_code == 422
    assert (
        await client.get(f"/api/tracked-positions/{position_id}/audit?cursor=not-a-cursor")
    ).status_code == 422

    async with app.state.db.session() as session:
        session.add(
            User(
                id=2,
                email="other-owner@example.com",
                recipient_email="other-owner@example.com",
                is_approved=True,
            )
        )
        other = TrackedPosition(
            user_id=2,
            asset_type="etf",
            asset_code="510300",
            asset_name="沪深300ETF",
            buy_date=date(2026, 7, 1),
            buy_amount=1000.0,
            status="active",
        )
        session.add(other)
        await session.commit()
        other_id = other.id

    assert (await client.get(f"/api/tracked-positions/{other_id}/audit")).status_code == 404


@pytest.mark.asyncio
async def test_position_api_separates_alert_data_and_action_state(app, client) -> None:
    position_id, _audit_ids = await _seed_audit_history(app)

    response = await client.get(f"/api/tracked-positions/{position_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle_state"] == {
        "alert_state": "firing",
        "data_state": "eligible",
        "data_reason_code": None,
    }
    assert body["current_action"]["status"] == "proposed"
    assert body["current_action"]["execution_provenance"] == "none"


def test_existing_privacy_delete_contract_cascades_audit_history() -> None:
    foreign_keys = TrackedPositionAlertAudit.__table__.c.tracked_position_id.foreign_keys

    assert len(foreign_keys) == 1
    assert next(iter(foreign_keys)).ondelete == "CASCADE"


@pytest.mark.asyncio
async def test_audit_events_cannot_be_rewritten_during_retention(app) -> None:
    _position_id, audit_ids = await _seed_audit_history(app)

    async with app.state.db.session() as session:
        audit = await session.get(TrackedPositionAlertAudit, audit_ids[-1])
        assert audit is not None
        audit.outcome = "rewritten"
        with pytest.raises(TrackedPositionAuditImmutableError, match="immutable"):
            await session.commit()
        await session.rollback()
