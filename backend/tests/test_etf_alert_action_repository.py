from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.entities import TrackedPosition, TrackedPositionActionDecision
from app.services.tracked_positions.action_repository import (
    PersistActionDecisionCommand,
    StalePositionStateError,
    persist_action_decision,
)
from app.services.tracked_positions.lifecycle_rollout import (
    LifecycleRolloutMode,
    LifecycleRolloutPolicy,
)

ACTIVE_ROLLOUT = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)


async def _position(app) -> int:
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
            exit_state_json={
                "position_episode_id": "position-episode-1",
                "exposure_version": 1,
                "exposure_baseline": {
                    "normalized_quantity": 1000.0,
                    "source": "confirmed_shares",
                    "adjustment_factor": 1.0,
                },
            },
            exit_state_version=0,
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)
        return position.id


def _command(
    position_id: int,
    *,
    target: float = 0.5,
    expected_version: int = 0,
    cycle: str = "action-cycle-1",
    rules: tuple[str, ...] = ("trailing_take_profit",),
) -> PersistActionDecisionCommand:
    return PersistActionDecisionCommand(
        owner_id=1,
        position_id=position_id,
        expected_exit_state_version=expected_version,
        position_episode_id="position-episode-1",
        exposure_version=1,
        policy_version="policy-v1",
        action_cycle_id=cycle,
        target_remaining_fraction=target,
        baseline_normalized_quantity=1000.0,
        baseline_account_weight=0.3,
        baseline_adjustment_factor=1.0,
        baseline_source="confirmed_shares",
        current_normalized_quantity=1000.0,
        input_snapshot_hash="a" * 64,
        data_state="eligible",
        contributing_rules=rules,
        alert_episode_ids=("alert-episode-1",),
    )


@pytest.mark.asyncio
async def test_same_stage_retry_resolves_to_one_persisted_action(app) -> None:
    position_id = await _position(app)

    async with app.state.db.session() as session:
        first = await persist_action_decision(
            session, _command(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()
        first_action_id = first.action.id

    async with app.state.db.session() as session:
        retry = await persist_action_decision(
            session, _command(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()
        actions = (
            await session.scalars(
                select(TrackedPositionActionDecision).where(
                    TrackedPositionActionDecision.tracked_position_id == position_id
                )
            )
        ).all()
        position = await session.get(TrackedPosition, position_id)

    assert first.outcome == "created"
    assert retry.outcome == "existing"
    assert retry.action.id == first_action_id
    assert len(actions) == 1
    assert position is not None
    assert position.exit_state_version == 1


@pytest.mark.asyncio
async def test_stricter_stage_supersedes_weaker_current_action_atomically(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        weaker = await persist_action_decision(
            session, _command(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()
        weaker_id = weaker.action.id

    async with app.state.db.session() as session:
        stricter = await persist_action_decision(
            session,
            _command(
                position_id,
                target=0.0,
                expected_version=1,
                rules=("hard_stop", "trailing_take_profit"),
            ),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()
        actions = (
            await session.scalars(
                select(TrackedPositionActionDecision)
                .where(TrackedPositionActionDecision.tracked_position_id == position_id)
                .order_by(TrackedPositionActionDecision.id)
            )
        ).all()
        position = await session.get(TrackedPosition, position_id)

    assert stricter.outcome == "superseded"
    assert len(actions) == 2
    assert actions[0].id == weaker_id
    assert actions[0].status == "superseded"
    assert actions[0].is_current is False
    assert actions[0].superseded_by_action_id == actions[1].id
    assert actions[1].target_remaining_fraction == 0.0
    assert actions[1].is_current is True
    assert sum(action.is_current for action in actions) == 1
    assert position is not None
    assert position.exit_state_version == 2
    assert position.exit_state_json["current_action_id"] == actions[1].id


@pytest.mark.asyncio
async def test_stale_cas_cannot_create_a_second_action_or_change_position_state(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        await persist_action_decision(
            session, _command(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        with pytest.raises(StalePositionStateError):
            await persist_action_decision(
                session,
                _command(position_id, target=0.0, expected_version=0, cycle="action-cycle-2"),
                rollout_policy=ACTIVE_ROLLOUT,
            )
        await session.rollback()

    async with app.state.db.session() as session:
        actions = (
            await session.scalars(
                select(TrackedPositionActionDecision).where(
                    TrackedPositionActionDecision.tracked_position_id == position_id
                )
            )
        ).all()
        position = await session.get(TrackedPosition, position_id)

    assert len(actions) == 1
    assert actions[0].target_remaining_fraction == 0.5
    assert position is not None
    assert position.exit_state_version == 1
