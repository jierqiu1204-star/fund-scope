from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.entities import (
    NotificationEnvelopeImmutableError,
    TrackedPosition,
    TrackedPositionNotificationEnvelope,
    TrackedPositionNotificationItem,
)
from app.services.notifier import Notifier
from app.services.workflows.tracked_position_notifications import (
    EnvelopeAssemblyCommand,
    assemble_notification_envelope,
)

NOW = datetime(2026, 7, 14, 15, 10)
SNAPSHOT_HASH = "a" * 64


def _action_payload(action_id: int) -> dict[str, object]:
    return {
        "contract_version": "tracked_position_action_notification_v1",
        "notification_kind": "action",
        "asset_name": f"测试ETF-{action_id}",
        "asset_code": "513520",
        "alert_title": "减仓建议待确认",
        "action_id": action_id,
        "action_status": "proposed",
        "policy_version": "policy-v1",
        "eligible_data_time": "2026-07-14T15:00:00",
        "cutoff_time": "2026-07-14T15:00:00",
        "data_source": "eastmoney_adjusted",
        "quote_freshness": "close_final",
        "evidence_hash": SNAPSHOT_HASH,
        "target_semantics": "absolute_exposure_baseline",
        "target_stage": "remaining_5000bp",
        "target_remaining_fraction": 0.5,
        "baseline_normalized_quantity": 1000.0,
        "baseline_adjustment_factor": 1.0,
        "baseline_source": "confirmed_shares",
        "target_normalized_quantity": 500.0,
        "automatic_execution": False,
    }


async def _seed_items(
    app,
    *,
    count: int,
    route: str = "ordinary_digest",
    severity: str = "warning",
    start: int = 1,
) -> tuple[int, ...]:
    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code=f"513{start:03d}",
            asset_name="测试ETF",
            buy_date=date(2026, 7, 1),
            confirmed_shares=1000.0,
            buy_amount=1000.0,
            exit_state_json={},
            status="active",
        )
        session.add(position)
        await session.flush()
        items = [
            TrackedPositionNotificationItem(
                user_id=1,
                tracked_position_id=position.id,
                alert_episode_id=f"episode-{start + index}",
                transition="firing",
                recipient="owner@example.com",
                channel="email",
                repeat_slot="2026-07-14:close",
                route=route,
                severity=severity,
                payload_json=_action_payload(start + index),
                status="pending",
            )
            for index in range(count)
        ]
        session.add_all(items)
        await session.commit()
        return tuple(item.id for item in items)


@pytest.mark.asyncio
async def test_ordinary_items_share_one_sealed_digest_and_exact_retry(app) -> None:
    item_ids = await _seed_items(app, count=2)
    command = EnvelopeAssemblyCommand(
        owner_id=1,
        trade_session=date(2026, 7, 14),
        item_ids=item_ids,
        sealed_snapshot_hash=SNAPSHOT_HASH,
        occurred_at=NOW,
    )
    async with app.state.db.session() as session:
        first = await assemble_notification_envelope(session, command, notifier=Notifier())
        await session.commit()
        first_id = first.id
    async with app.state.db.session() as session:
        retry = await assemble_notification_envelope(session, command, notifier=Notifier())
        await session.commit()
        envelopes = list((await session.scalars(select(TrackedPositionNotificationEnvelope))).all())
        items = list(
            (
                await session.scalars(
                    select(TrackedPositionNotificationItem).order_by(
                        TrackedPositionNotificationItem.id
                    )
                )
            ).all()
        )

    assert retry.id == first_id
    assert len(envelopes) == 1
    assert envelopes[0].digest_revision == 1
    assert envelopes[0].sealed_at == NOW
    assert envelopes[0].message_id.startswith("<fundscope-")
    assert len(envelopes[0].rendered_content_hash) == 64
    assert all(item.envelope_id == first_id and item.status == "sealed" for item in items)


@pytest.mark.asyncio
async def test_late_item_rolls_to_new_immutable_digest_revision(app) -> None:
    first_ids = await _seed_items(app, count=1)
    async with app.state.db.session() as session:
        first = await assemble_notification_envelope(
            session,
            EnvelopeAssemblyCommand(
                owner_id=1,
                trade_session=date(2026, 7, 14),
                item_ids=first_ids,
                sealed_snapshot_hash=SNAPSHOT_HASH,
                occurred_at=NOW,
            ),
            notifier=Notifier(),
        )
        await session.commit()
        first_body = first.rendered_body

    late_ids = await _seed_items(app, count=1, start=20)
    async with app.state.db.session() as session:
        second = await assemble_notification_envelope(
            session,
            EnvelopeAssemblyCommand(
                owner_id=1,
                trade_session=date(2026, 7, 14),
                item_ids=late_ids,
                sealed_snapshot_hash=SNAPSHOT_HASH,
                occurred_at=NOW,
            ),
            notifier=Notifier(),
        )
        await session.commit()
        first_reloaded = await session.get(TrackedPositionNotificationEnvelope, first.id)

    assert second.id != first.id
    assert second.digest_revision == 2
    assert first_reloaded is not None and first_reloaded.rendered_body == first_body

    async with app.state.db.session() as session:
        sealed_item = await session.get(TrackedPositionNotificationItem, first_ids[0])
        assert sealed_item is not None
        sealed_item.status = "pending"
        with pytest.raises(NotificationEnvelopeImmutableError):
            await session.flush()
        await session.rollback()

    async with app.state.db.session() as session:
        sealed_item = await session.get(TrackedPositionNotificationItem, first_ids[0])
        assert sealed_item is not None
        sealed_item.payload_json = {"forged": True}
        with pytest.raises(NotificationEnvelopeImmutableError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_late_item_cannot_be_attached_to_a_sealed_envelope(app) -> None:
    first_ids = await _seed_items(app, count=1)
    async with app.state.db.session() as session:
        envelope = await assemble_notification_envelope(
            session,
            EnvelopeAssemblyCommand(
                owner_id=1,
                trade_session=date(2026, 7, 14),
                item_ids=first_ids,
                sealed_snapshot_hash=SNAPSHOT_HASH,
                occurred_at=NOW,
            ),
            notifier=Notifier(),
        )
        await session.commit()
        envelope_id = envelope.id

    late_ids = await _seed_items(app, count=1, start=30)
    async with app.state.db.session() as session:
        late_item = await session.get(TrackedPositionNotificationItem, late_ids[0])
        assert late_item is not None
        late_item.envelope_id = envelope_id
        late_item.status = "sealed"
        with pytest.raises(NotificationEnvelopeImmutableError, match="late notification item"):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_digest_revision_unique_conflict_retries_with_next_revision(
    app,
    monkeypatch,
) -> None:
    item_ids = await _seed_items(app, count=1, start=40)
    async with app.state.db.session() as session:
        session.add(
            TrackedPositionNotificationEnvelope(
                user_id=1,
                trade_session=date(2026, 7, 14),
                route="ordinary_digest",
                severity="warning",
                channel="email",
                sealed_snapshot_hash=SNAPSHOT_HASH,
                digest_revision=1,
                status="pending",
            )
        )
        await session.commit()

    async with app.state.db.session() as session:
        original_scalar = session.scalar
        scalar_calls = 0

        async def stale_max_once(*args, **kwargs):
            nonlocal scalar_calls
            scalar_calls += 1
            if scalar_calls == 1:
                return 0
            return await original_scalar(*args, **kwargs)

        monkeypatch.setattr(session, "scalar", stale_max_once)
        envelope = await assemble_notification_envelope(
            session,
            EnvelopeAssemblyCommand(
                owner_id=1,
                trade_session=date(2026, 7, 14),
                item_ids=item_ids,
                sealed_snapshot_hash=SNAPSHOT_HASH,
                occurred_at=NOW,
            ),
            notifier=Notifier(),
        )
        await session.commit()

    assert scalar_calls == 2
    assert envelope.digest_revision == 2


@pytest.mark.asyncio
async def test_urgent_hard_stop_envelope_is_single_item(app) -> None:
    item_ids = await _seed_items(
        app,
        count=2,
        route="urgent_hard_stop",
        severity="critical",
    )
    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="single item"):
            await assemble_notification_envelope(
                session,
                EnvelopeAssemblyCommand(
                    owner_id=1,
                    trade_session=date(2026, 7, 14),
                    item_ids=item_ids,
                    sealed_snapshot_hash=SNAPSHOT_HASH,
                    occurred_at=NOW,
                ),
                notifier=Notifier(),
            )
