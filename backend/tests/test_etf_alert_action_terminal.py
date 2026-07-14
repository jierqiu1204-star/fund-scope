from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionActionExecution,
)
from app.services.tracked_positions.action_repository import (
    PersistActionDecisionCommand,
    TerminalizeActionCommand,
    expire_current_action_if_due,
    persist_action_decision,
    terminalize_current_action,
)
from app.services.tracked_positions.lifecycle import ActionTerminalCause
from app.services.tracked_positions.lifecycle_rollout import (
    LifecycleRolloutMode,
    LifecycleRolloutPolicy,
)
from app.services.tracked_positions.service import (
    latest_owner_confirmed_exit_execution_context,
)

ACTIVE_ROLLOUT = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
NOW = datetime(2026, 7, 14, 15, 0)


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


def _proposal(
    position_id: int,
    *,
    target: float = 0.5,
    expected_version: int = 0,
    cycle: str = "cycle-1",
    valid_until: datetime | None = None,
):
    return PersistActionDecisionCommand(
        owner_id=1,
        position_id=position_id,
        expected_exit_state_version=expected_version,
        position_episode_id="position-episode-1",
        exposure_version=1,
        policy_version="policy-v2",
        action_cycle_id=cycle,
        target_remaining_fraction=target,
        baseline_normalized_quantity=1000.0,
        baseline_account_weight=0.3,
        baseline_adjustment_factor=1.0,
        baseline_source="confirmed_shares",
        current_normalized_quantity=1000.0,
        input_snapshot_hash="a" * 64,
        data_state="eligible",
        contributing_rules=("trailing_take_profit",),
        alert_episode_ids=("alert-1",),
        valid_until=valid_until,
    )


async def _add_partial_fill(session, action: TrackedPositionActionDecision) -> int:
    action.status = "partially_executed"
    action.execution_provenance = "owner_confirmed"
    action.cumulative_executed_quantity = 200.0
    execution = TrackedPositionActionExecution(
        action_decision_id=action.id,
        user_id=1,
        tracked_position_id=action.tracked_position_id,
        idempotency_key=f"fill-{action.id}",
        request_hash="b" * 64,
        execution_provenance="owner_confirmed",
        execution_quantity=200.0,
        execution_price=1.1,
        price_source="owner_confirmation",
        fees=1.0,
        before_normalized_quantity=1000.0,
        resulting_normalized_quantity=800.0,
        resulting_position_state_version=2,
        executed_at=NOW,
        actor_id=1,
    )
    session.add(execution)
    await session.flush()
    return execution.id


@pytest.mark.asyncio
async def test_valid_resolution_expires_unfulfilled_remainder_and_preserves_fill(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session, _proposal(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        fill_id = await _add_partial_fill(session, persisted.action)
        await session.commit()

    async with app.state.db.session() as session:
        result = await terminalize_current_action(
            session,
            TerminalizeActionCommand(
                owner_id=1,
                position_id=position_id,
                action_id=persisted.action.id,
                expected_exit_state_version=1,
                cause=ActionTerminalCause.ALL_RULES_RESOLVED,
                occurred_at=NOW,
                data_eligible=True,
                all_contributing_rules_resolved=True,
            ),
        )
        await session.commit()

    async with app.state.db.session() as session:
        action = await session.get(TrackedPositionActionDecision, persisted.action.id)
        execution = await session.get(TrackedPositionActionExecution, fill_id)
        position = await session.get(TrackedPosition, position_id)

    assert result.outcome == "terminalized"
    assert action is not None
    assert action.status == "expired"
    assert action.status_reason == "all_contributing_rules_resolved"
    assert action.is_current is False
    assert action.cumulative_executed_quantity == 200.0
    assert execution is not None
    assert position is not None
    assert position.exit_state_version == 2
    assert position.exit_state_json["current_action_id"] is None
    assert position.exit_state_json["open_action_cycle_id"] is None
    assert position.exit_state_json["last_closed_action_cycle"]["reason"] == (
        "all_contributing_rules_resolved"
    )


@pytest.mark.asyncio
async def test_stricter_target_supersedes_partial_action_without_deleting_fill(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        weaker = await persist_action_decision(
            session, _proposal(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        fill_id = await _add_partial_fill(session, weaker.action)
        await session.commit()

    async with app.state.db.session() as session:
        stricter = await persist_action_decision(
            session,
            _proposal(position_id, target=0.0, expected_version=1),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()

    async with app.state.db.session() as session:
        old = await session.get(TrackedPositionActionDecision, weaker.action.id)
        execution = await session.get(TrackedPositionActionExecution, fill_id)

    assert stricter.outcome == "superseded"
    assert old is not None
    assert old.status == "superseded"
    assert old.superseded_by_action_id == stricter.action.id
    assert old.cumulative_executed_quantity == 200.0
    assert execution is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cause", "reason"),
    [
        (ActionTerminalCause.EXPOSURE_NET_ADD, "exposure_net_add"),
        (ActionTerminalCause.POLICY_RETIRED, "policy_retired"),
    ],
)
async def test_exposure_or_policy_retirement_supersedes_action_and_preserves_fills(
    app, cause: ActionTerminalCause, reason: str
) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session, _proposal(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await _add_partial_fill(session, persisted.action)
        await session.commit()

    async with app.state.db.session() as session:
        await terminalize_current_action(
            session,
            TerminalizeActionCommand(
                owner_id=1,
                position_id=position_id,
                action_id=persisted.action.id,
                expected_exit_state_version=1,
                cause=cause,
                occurred_at=NOW,
            ),
        )
        await session.commit()

    async with app.state.db.session() as session:
        action = await session.get(TrackedPositionActionDecision, persisted.action.id)
        fill_count = await session.scalar(
            select(func.count())
            .select_from(TrackedPositionActionExecution)
            .where(TrackedPositionActionExecution.action_decision_id == persisted.action.id)
        )
        position = await session.get(TrackedPosition, position_id)

    assert action is not None
    assert action.status == "superseded"
    assert action.status_reason == reason
    assert fill_count == 1
    assert position is not None
    assert position.exit_state_json["open_action_cycle_id"] is None


@pytest.mark.asyncio
async def test_valid_until_expires_remainder_but_keeps_firing_cycle_open(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session,
            _proposal(position_id, valid_until=NOW),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()

    async with app.state.db.session() as session:
        expired = await expire_current_action_if_due(
            session,
            owner_id=1,
            position_id=position_id,
            action_id=persisted.action.id,
            expected_exit_state_version=1,
            now=NOW,
        )
        await session.commit()
    async with app.state.db.session() as session:
        retry = await expire_current_action_if_due(
            session,
            owner_id=1,
            position_id=position_id,
            action_id=persisted.action.id,
            expected_exit_state_version=2,
            now=NOW,
        )
        await session.commit()
        action = await session.get(TrackedPositionActionDecision, persisted.action.id)
        position = await session.get(TrackedPosition, position_id)

    assert expired.outcome == "terminalized"
    assert retry.outcome == "existing_terminal"
    assert action is not None
    assert action.status == "expired"
    assert action.status_reason == "valid_until_elapsed"
    assert action.expired_at == NOW
    assert position is not None
    assert position.exit_state_version == 2
    assert position.exit_state_json["current_action_id"] is None
    assert position.exit_state_json["open_action_cycle_id"] == "cycle-1"


@pytest.mark.asyncio
async def test_valid_resolution_closes_cycle_and_later_episode_can_reopen_same_target(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        first = await persist_action_decision(
            session, _proposal(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()
    async with app.state.db.session() as session:
        await terminalize_current_action(
            session,
            TerminalizeActionCommand(
                owner_id=1,
                position_id=position_id,
                action_id=first.action.id,
                expected_exit_state_version=1,
                cause=ActionTerminalCause.ALL_RULES_RESOLVED,
                occurred_at=NOW,
                data_eligible=True,
                all_contributing_rules_resolved=True,
            ),
        )
        await session.commit()
    async with app.state.db.session() as session:
        reopened = await persist_action_decision(
            session,
            _proposal(position_id, expected_version=2, cycle="cycle-2"),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()

    assert reopened.outcome == "created"
    assert reopened.action.action_cycle_id == "cycle-2"
    assert reopened.action.target_remaining_fraction == 0.5


@pytest.mark.asyncio
async def test_reentry_context_uses_only_owner_confirmed_execution_facts(app) -> None:
    owner_position_id = await _position(app)
    simulated_position_id = await _position(app)
    async with app.state.db.session() as session:
        owner_action = await persist_action_decision(
            session, _proposal(owner_position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await _add_partial_fill(session, owner_action.action)
        simulated_action = await persist_action_decision(
            session,
            _proposal(simulated_position_id, cycle="simulated-cycle"),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        session.add(
            TrackedPositionActionExecution(
                action_decision_id=simulated_action.action.id,
                user_id=1,
                tracked_position_id=simulated_position_id,
                idempotency_key="simulated-fill",
                request_hash="c" * 64,
                execution_provenance="simulated",
                execution_quantity=500.0,
                execution_price=1.1,
                price_source="backtest",
                fees=0.0,
                before_normalized_quantity=1000.0,
                resulting_normalized_quantity=500.0,
                resulting_position_state_version=2,
                executed_at=NOW,
                actor_id=1,
            )
        )
        await session.commit()

    async with app.state.db.session() as session:
        owner_context = await latest_owner_confirmed_exit_execution_context(
            session, owner_id=1, position_id=owner_position_id
        )
        simulated_context = await latest_owner_confirmed_exit_execution_context(
            session, owner_id=1, position_id=simulated_position_id
        )

    assert owner_context == ("reduce", NOW.date())
    assert simulated_context == (None, None)


@pytest.mark.asyncio
async def test_reentry_cooldown_uses_first_owner_fill_in_latest_action_cycle(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session, _proposal(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await _add_partial_fill(session, persisted.action)
        session.add(
            TrackedPositionActionExecution(
                action_decision_id=persisted.action.id,
                user_id=1,
                tracked_position_id=position_id,
                idempotency_key="later-partial-fill",
                request_hash="d" * 64,
                execution_provenance="owner_confirmed",
                execution_quantity=100.0,
                execution_price=1.08,
                price_source="owner_confirmation",
                fees=1.0,
                before_normalized_quantity=800.0,
                resulting_normalized_quantity=700.0,
                resulting_position_state_version=3,
                executed_at=NOW + timedelta(days=2),
                actor_id=1,
            )
        )
        await session.commit()

    async with app.state.db.session() as session:
        context = await latest_owner_confirmed_exit_execution_context(
            session, owner_id=1, position_id=position_id
        )

    assert context == ("reduce", NOW.date())
