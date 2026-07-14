from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)
from app.services.tracked_positions.lifecycle_read_repository import (
    TimelineCursor,
    get_current_action,
    list_action_history,
    list_alert_audit_page,
    list_pending_envelopes,
    list_repeat_slot_items,
)


async def _seed_repository_rows(app, *, row_count: int = 8) -> dict[str, object]:
    base = datetime(2026, 7, 14, 9, 30)
    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="513520",
            asset_name="日经ETF",
            buy_date=date(2026, 7, 1),
            confirmed_shares=1000.0,
            buy_amount=1000.0,
            entry_price=1.0,
            estimated_shares=1000.0,
            exit_state_json={},
            exit_state_version=0,
            status="active",
        )
        session.add(position)
        await session.flush()

        actions: list[TrackedPositionActionDecision] = []
        audits: list[TrackedPositionAlertAudit] = []
        items: list[TrackedPositionNotificationItem] = []
        envelopes: list[TrackedPositionNotificationEnvelope] = []
        for index in range(row_count):
            created_at = base + timedelta(minutes=index)
            action = TrackedPositionActionDecision(
                user_id=1,
                tracked_position_id=position.id,
                position_episode_id="episode-1",
                exposure_version=1,
                policy_version="policy-v2",
                action_cycle_id=f"cycle-{index}",
                target_stage="remaining_5000bp",
                target_remaining_fraction=0.5,
                baseline_normalized_quantity=1000.0,
                baseline_account_weight=0.3,
                baseline_adjustment_factor=1.0,
                baseline_source="confirmed_shares",
                target_normalized_quantity=500.0,
                target_account_weight=0.15,
                input_snapshot_hash=f"{index:064x}",
                data_state="eligible",
                status="proposed" if index == row_count - 1 else "superseded",
                execution_provenance="none",
                cumulative_executed_quantity=0.0,
                contributing_rules_json=["trailing_take_profit"],
                alert_episode_ids_json=[f"episode-{index}"],
                is_current=index == row_count - 1,
                created_at=created_at,
                updated_at=created_at,
            )
            actions.append(action)
            audits.append(
                TrackedPositionAlertAudit(
                    tracked_position_id=position.id,
                    outcome="evaluated",
                    alert_date=date(2026, 7, 14),
                    alert_type="trailing_take_profit",
                    data_source="eastmoney",
                    quote_freshness="fresh",
                    created_at=created_at,
                )
            )
            items.append(
                TrackedPositionNotificationItem(
                    user_id=1,
                    tracked_position_id=position.id,
                    alert_episode_id=f"episode-{index}",
                    transition="firing",
                    recipient="owner@example.com",
                    channel="email",
                    repeat_slot="2026-07-14:close",
                    route="ordinary_digest",
                    severity="warning",
                    payload_json={},
                    created_at=created_at,
                )
            )
            envelopes.append(
                TrackedPositionNotificationEnvelope(
                    user_id=1,
                    trade_session=date(2026, 7, 14),
                    route="ordinary_digest",
                    severity="warning",
                    channel="email",
                    sealed_snapshot_hash=f"{index + row_count:064x}",
                    digest_revision=1,
                    status="pending" if index < row_count - 1 else "smtp_accepted",
                    lease_expires_at=created_at,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        session.add_all([*actions, *audits, *items, *envelopes])
        await session.commit()
        return {
            "position_id": position.id,
            "action_ids": tuple(action.id for action in actions),
            "audit_ids": tuple(audit.id for audit in audits),
            "item_ids": tuple(item.id for item in items),
            "pending_envelope_ids": tuple(envelope.id for envelope in envelopes[:-1]),
        }


@pytest.mark.asyncio
async def test_repository_reads_are_owner_scoped_and_keyset_bounded(app) -> None:
    seeded = await _seed_repository_rows(app)
    position_id = int(seeded["position_id"])

    async with app.state.db.session() as session:
        current = await get_current_action(session, owner_id=1, position_id=position_id)
        missing_owner = await get_current_action(session, owner_id=2, position_id=position_id)
        first_actions = await list_action_history(
            session, owner_id=1, position_id=position_id, limit=3
        )
        second_actions = await list_action_history(
            session,
            owner_id=1,
            position_id=position_id,
            before=first_actions.next_cursor,
            limit=3,
        )
        first_audits = await list_alert_audit_page(
            session, owner_id=1, position_id=position_id, limit=3
        )
        second_audits = await list_alert_audit_page(
            session,
            owner_id=1,
            position_id=position_id,
            before=first_audits.next_cursor,
            limit=3,
        )

    assert current is not None
    assert current.id == seeded["action_ids"][-1]
    assert missing_owner is None
    assert len(first_actions.items) == 3
    assert first_actions.next_cursor is not None
    assert len(second_actions.items) == 3
    assert {row.id for row in first_actions.items}.isdisjoint(
        {row.id for row in second_actions.items}
    )
    assert len(first_audits.items) == 3
    assert first_audits.next_cursor is not None
    assert {row.id for row in first_audits.items}.isdisjoint(
        {row.id for row in second_audits.items}
    )

    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="between 1 and 100"):
            await list_action_history(session, owner_id=1, position_id=position_id, limit=101)
        with pytest.raises(ValueError, match="between 1 and 100"):
            await list_alert_audit_page(session, owner_id=1, position_id=position_id, limit=0)


@pytest.mark.asyncio
async def test_repeat_pending_reads_and_query_plans_use_expected_indexes(app) -> None:
    seeded = await _seed_repository_rows(app, row_count=128)
    position_id = int(seeded["position_id"])

    async with app.state.db.session() as session:
        repeat_page = await list_repeat_slot_items(
            session,
            owner_id=1,
            position_id=position_id,
            repeat_slot="2026-07-14:close",
            limit=5,
        )
        pending = await list_pending_envelopes(session, limit=5)
        await session.execute(text("ANALYZE"))
        plans = {}
        statements = {
            "uq_tracked_action_current_slot": (
                "SELECT id FROM tracked_position_action_decisions "
                "WHERE tracked_position_id = :position_id AND is_current = 1 LIMIT 1"
            ),
            "ix_tracked_action_history": (
                "SELECT id FROM tracked_position_action_decisions "
                "WHERE tracked_position_id = :position_id "
                "ORDER BY created_at DESC, id DESC LIMIT 20"
            ),
            "ix_tracked_alert_audit_cursor": (
                "SELECT id FROM tracked_position_alert_audits "
                "WHERE tracked_position_id = :position_id "
                "ORDER BY created_at DESC, id DESC LIMIT 20"
            ),
            "ix_tracked_notification_item_repeat": (
                "SELECT id FROM tracked_position_notification_items "
                "WHERE tracked_position_id = :position_id AND repeat_slot = :repeat_slot "
                "ORDER BY created_at DESC, id DESC LIMIT 20"
            ),
            "ix_tracked_notification_envelope_pending": (
                "SELECT id FROM tracked_position_notification_envelopes "
                "WHERE status = 'pending' ORDER BY lease_expires_at, id LIMIT 20"
            ),
        }
        for index_name, statement in statements.items():
            rows = (
                await session.execute(
                    text(f"EXPLAIN QUERY PLAN {statement}"),
                    {
                        "position_id": position_id,
                        "repeat_slot": "2026-07-14:close",
                    },
                )
            ).all()
            plans[index_name] = " ".join(str(row[3]) for row in rows)

    assert len(repeat_page.items) == 5
    assert repeat_page.next_cursor is not None
    assert len(pending) == 5
    assert all(envelope.status == "pending" for envelope in pending)
    for index_name, plan in plans.items():
        assert index_name in plan
        assert "USE TEMP B-TREE" not in plan

    assert TimelineCursor(recorded_at=datetime(2026, 7, 14), row_id=1).row_id == 1
