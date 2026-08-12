from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    TrackedEtfSleeveLedgerEvent,
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionActionExecution,
    TrackedPositionAlertAudit,
    User,
    utcnow,
)
from app.services.tracked_positions.action_repository import (
    PersistActionDecisionCommand,
    persist_action_decision,
)
from app.services.tracked_positions.action_transition_service import (
    ActionExecutionFacts,
    ActionTransitionCommand,
    ActionTransitionConflictError,
    ActionTransitionKind,
    ActionTransitionValidationError,
    transition_position_action,
)
from app.services.tracked_positions.lifecycle_rollout import (
    LifecycleRolloutMode,
    LifecycleRolloutPolicy,
)
from app.services.tracked_positions.sleeve_repository import (
    AppendSleeveLedgerEventCommand,
    append_owner_ledger_event,
)

ACTIVE_ROLLOUT = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)


async def _seed_action(
    app,
    *,
    owner_id: int = 1,
    target: float = 0.5,
    shares: float = 1000.0,
    action_status: str = "proposed",
    action_current: bool = True,
) -> tuple[int, int, int]:
    async with app.state.db.session() as session:
        if owner_id != 1 and await session.get(User, owner_id) is None:
            session.add(
                User(
                    id=owner_id,
                    email=f"owner-{owner_id}@example.com",
                    recipient_email=f"owner-{owner_id}@example.com",
                    is_approved=True,
                )
            )
            await session.flush()
        position = TrackedPosition(
            user_id=owner_id,
            asset_type="etf",
            asset_code=f"513{owner_id:03d}",
            asset_name="测试ETF",
            buy_date=date(2026, 7, 1),
            confirmed_shares=shares,
            estimated_shares=shares,
            buy_amount=shares,
            entry_price=1.0,
            exit_state_json={},
            exit_state_version=3,
            status="active",
        )
        session.add(position)
        await session.flush()
        episode_id = f"position-episode-{position.id}"
        cycle_id = f"action-cycle-{position.id}"
        action = TrackedPositionActionDecision(
            user_id=owner_id,
            tracked_position_id=position.id,
            position_episode_id=episode_id,
            exposure_version=1,
            policy_version="policy-v1",
            action_cycle_id=cycle_id,
            target_stage=("remaining_0bp" if target == 0 else "remaining_5000bp"),
            target_remaining_fraction=target,
            baseline_normalized_quantity=shares,
            baseline_account_weight=0.25,
            baseline_adjustment_factor=1.0,
            baseline_source="confirmed_shares",
            target_normalized_quantity=shares * target,
            target_account_weight=0.25 * target,
            input_snapshot_hash="a" * 64,
            data_state="eligible",
            status=action_status,
            execution_provenance="none",
            cumulative_executed_quantity=0.0,
            contributing_rules_json=["trailing_take_profit"],
            alert_episode_ids_json=[f"alert-episode-{position.id}"],
            is_current=action_current,
            created_at=utcnow() - timedelta(hours=1),
        )
        session.add(action)
        await session.flush()
        position.exit_state_json = {
            "position_episode_id": episode_id,
            "position_episode_status": "active",
            "exposure_version": 1,
            "policy_version": "policy-v1",
            "open_action_cycle_id": cycle_id,
            "current_action_id": action.id if action_current else None,
            "exposure_baseline": {
                "normalized_quantity": shares,
                "source": "confirmed_shares",
                "adjustment_factor": 1.0,
            },
            "current_normalized_quantity": shares,
            "current_adjustment_factor": 1.0,
            "alert_rule_states": {},
        }
        await session.commit()
        return position.id, action.id, position.exit_state_version


def _execute_payload(
    *,
    expected_version: int,
    quantity: float,
    resulting_shares: float,
    close_fact: bool = False,
) -> dict[str, object]:
    return {
        "transition": "execute",
        "expected_position_state_version": expected_version,
        "execution": {
            "executed_at": utcnow().isoformat(),
            "quantity": quantity,
            "price": 1.02,
            "price_source": "owner_broker_statement",
            "fees": 1.0,
            "resulting_shares": resulting_shares,
            "close_fact": close_fact,
        },
    }


@pytest.mark.asyncio
async def test_action_transition_api_acknowledges_and_cancels_without_execution(app, client) -> None:
    position_id, action_id, version = await _seed_action(app)
    acknowledge = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "ack-1"},
        json={"transition": "acknowledge", "expected_position_state_version": version},
    )
    assert acknowledge.status_code == 200
    assert acknowledge.json()["action_status"] == "acknowledged"
    assert acknowledge.json()["position_state_version"] == version + 1
    assert acknowledge.json()["execution_provenance"] == "none"

    cancel = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "cancel-1"},
        json={"transition": "cancel", "expected_position_state_version": version + 1},
    )
    assert cancel.status_code == 200
    assert cancel.json()["action_status"] == "cancelled"
    assert cancel.json()["position_state_version"] == version + 2

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        action = await session.get(TrackedPositionActionDecision, action_id)
        executions = await session.scalar(
            select(func.count()).select_from(TrackedPositionActionExecution)
        )
    assert position is not None and action is not None
    assert position.confirmed_shares == 1000.0
    assert position.exit_state_json["declined_action_cycle"]["action_id"] == action_id
    assert action.is_current is False
    assert executions == 0


@pytest.mark.asyncio
async def test_action_transition_api_records_partial_then_complete_execution(app, client) -> None:
    position_id, action_id, version = await _seed_action(app)
    partial = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "fill-1"},
        json=_execute_payload(expected_version=version, quantity=200, resulting_shares=800),
    )
    assert partial.status_code == 200
    assert partial.json()["action_status"] == "partially_executed"
    assert partial.json()["cumulative_executed_quantity"] == 200.0
    assert partial.json()["resulting_shares"] == 800.0
    assert partial.json()["execution_provenance"] == "owner_confirmed"

    complete = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "fill-2"},
        json=_execute_payload(
            expected_version=version + 1,
            quantity=300,
            resulting_shares=500,
        ),
    )
    assert complete.status_code == 200
    assert complete.json()["action_status"] == "executed"
    assert complete.json()["cumulative_executed_quantity"] == 500.0
    assert complete.json()["position_state_version"] == version + 2

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        action = await session.get(TrackedPositionActionDecision, action_id)
        executions = list(
            (
                await session.scalars(
                    select(TrackedPositionActionExecution)
                    .where(TrackedPositionActionExecution.action_decision_id == action_id)
                    .order_by(TrackedPositionActionExecution.id)
                )
            ).all()
        )
    assert position is not None and action is not None
    assert position.confirmed_shares == 500.0
    assert position.estimated_shares == 500.0
    assert action.status == "executed"
    assert action.is_current is False
    assert len(executions) == 2


@pytest.mark.asyncio
async def test_owner_confirmed_exit_appends_one_atomic_sleeve_sell_event(app, client) -> None:
    position_id, action_id, version = await _seed_action(app)
    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        position = await session.get(TrackedPosition, position_id)
        assert user is not None and position is not None
        user.etf_trading_capital = 10_000
        user.etf_trading_capital_confirmed_at = utcnow()
        await append_owner_ledger_event(
            session,
            AppendSleeveLedgerEventCommand(
                owner_id=user.id,
                idempotency_key="action-ledger-opening",
                event_type="opening_reconciliation",
                effective_date=date.today(),
                occurred_at=utcnow(),
                provenance="owner_confirmed",
                expected_predecessor_event_hash=None,
                cash_balance_after=9_000,
                holdings_after=(
                    {
                        "tracked_position_id": position.id,
                        "asset_code": position.asset_code,
                        "quantity": 1_000.0,
                        "remaining_cost_basis": 1_000.0,
                        "adjustment_factor": 1.0,
                    },
                ),
            ),
        )
        await session.commit()

    response = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "atomic-sleeve-fill"},
        json=_execute_payload(expected_version=version, quantity=200, resulting_shares=800),
    )
    assert response.status_code == 200, response.text

    async with app.state.db.session() as session:
        events = tuple(
            (
                await session.scalars(
                    select(TrackedEtfSleeveLedgerEvent)
                    .where(TrackedEtfSleeveLedgerEvent.user_id == 1)
                    .order_by(TrackedEtfSleeveLedgerEvent.sequence_no.asc())
                )
            ).all()
        )
    assert [event.event_type for event in events] == ["opening_reconciliation", "sell"]
    assert events[-1].quantity_delta == -200
    assert events[-1].quantity_after == 800
    assert events[-1].cash_delta == pytest.approx(203.0)


@pytest.mark.asyncio
async def test_action_transition_exact_terminal_retry_returns_original_result(app, client) -> None:
    position_id, action_id, version = await _seed_action(app, target=0.0)
    payload = _execute_payload(
        expected_version=version,
        quantity=1000,
        resulting_shares=0,
        close_fact=True,
    )
    first = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "full-close-1"},
        json=payload,
    )
    retry = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "full-close-1"},
        json=payload,
    )
    assert first.status_code == 200
    assert retry.status_code == 200
    assert {**first.json(), "idempotent_replay": True} == retry.json()
    assert retry.json()["action_status"] == "executed"
    assert retry.json()["idempotent_replay"] is True

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        execution_count = await session.scalar(
            select(func.count())
            .select_from(TrackedPositionActionExecution)
            .where(TrackedPositionActionExecution.action_decision_id == action_id)
        )
    assert position is not None
    assert position.confirmed_shares == 0.0
    assert position.status == "closed"
    assert execution_count == 1


@pytest.mark.asyncio
async def test_action_transition_rejects_stale_forged_and_cross_owner_requests(app, client) -> None:
    position_id, action_id, version = await _seed_action(app)
    stale = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "stale-1"},
        json={"transition": "acknowledge", "expected_position_state_version": version - 1},
    )
    forged = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "forged-1"},
        json={
            "transition": "acknowledge",
            "expected_position_state_version": version,
            "target_remaining_fraction": 0,
            "policy_version": "forged",
        },
    )
    other_position, other_action, other_version = await _seed_action(app, owner_id=2)
    cross_owner = await client.post(
        f"/api/tracked-positions/{other_position}/actions/{other_action}/transitions",
        headers={"Idempotency-Key": "cross-owner-1"},
        json={"transition": "acknowledge", "expected_position_state_version": other_version},
    )
    assert stale.status_code == 409
    assert forged.status_code == 422
    assert cross_owner.status_code == 404


@pytest.mark.asyncio
async def test_action_transition_rejects_invalid_shape_and_nonfinite_facts(app, client) -> None:
    position_id, action_id, version = await _seed_action(app)
    invalid_transition = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "invalid-transition"},
        json={"transition": "expire", "expected_position_state_version": version},
    )
    negative_price = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "negative-price"},
        json={
            **_execute_payload(
                expected_version=version,
                quantity=100,
                resulting_shares=900,
            ),
            "execution": {
                **_execute_payload(
                    expected_version=version,
                    quantity=100,
                    resulting_shares=900,
                )["execution"],
                "price": -1,
            },
        },
    )
    assert invalid_transition.status_code == 422
    assert negative_price.status_code == 422

    async with app.state.db.session() as session:
        with pytest.raises(ActionTransitionValidationError, match="finite"):
            await transition_position_action(
                session,
                ActionTransitionCommand(
                    owner_id=1,
                    position_id=position_id,
                    action_id=action_id,
                    transition=ActionTransitionKind.EXECUTE,
                    idempotency_key="nan-quantity",
                    expected_position_state_version=version,
                    occurred_at=utcnow(),
                    actor_id=1,
                    execution=ActionExecutionFacts(
                        executed_at=utcnow(),
                        quantity=math.nan,
                        price=1.0,
                        price_source="owner_broker_statement",
                        fees=0.0,
                        resulting_shares=900,
                        close_fact=False,
                    ),
                ),
            )


@pytest.mark.asyncio
async def test_action_transition_rejects_expired_or_non_current_policy_action(app, client) -> None:
    position_id, action_id, version = await _seed_action(app)
    async with app.state.db.session() as session:
        action = await session.get(TrackedPositionActionDecision, action_id)
        assert action is not None
        action.valid_until = utcnow() - timedelta(minutes=1)
        await session.commit()
    expired = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "expired-action"},
        json={"transition": "acknowledge", "expected_position_state_version": version},
    )
    assert expired.status_code == 409

    other_position, other_action, other_version = await _seed_action(app)
    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, other_position)
        assert position is not None
        state = dict(position.exit_state_json)
        state["policy_version"] = "policy-v2"
        position.exit_state_json = state
        await session.commit()
    wrong_policy = await client.post(
        f"/api/tracked-positions/{other_position}/actions/{other_action}/transitions",
        headers={"Idempotency-Key": "wrong-policy"},
        json={"transition": "acknowledge", "expected_position_state_version": other_version},
    )
    assert wrong_policy.status_code == 409


@pytest.mark.asyncio
async def test_action_transition_rejects_idempotency_payload_conflict(app, client) -> None:
    position_id, action_id, version = await _seed_action(app)
    first = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "same-key"},
        json=_execute_payload(expected_version=version, quantity=100, resulting_shares=900),
    )
    conflict = await client.post(
        f"/api/tracked-positions/{position_id}/actions/{action_id}/transitions",
        headers={"Idempotency-Key": "same-key"},
        json=_execute_payload(expected_version=version, quantity=200, resulting_shares=800),
    )
    assert first.status_code == 200
    assert conflict.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quantity", "resulting", "executed_delta", "error_fragment"),
    [
        (1001.0, 0.0, timedelta(), "exceeds"),
        (200.0, 700.0, timedelta(), "inconsistent"),
        (100.0, 900.0, timedelta(hours=-2), "before"),
        (100.0, 900.0, timedelta(minutes=10), "future"),
    ],
)
async def test_action_transition_validates_execution_facts(
    app,
    quantity: float,
    resulting: float,
    executed_delta: timedelta,
    error_fragment: str,
) -> None:
    position_id, action_id, version = await _seed_action(app)
    async with app.state.db.session() as session:
        command = ActionTransitionCommand(
            owner_id=1,
            position_id=position_id,
            action_id=action_id,
            transition=ActionTransitionKind.EXECUTE,
            idempotency_key=f"invalid-{error_fragment}",
            expected_position_state_version=version,
            occurred_at=utcnow(),
            actor_id=1,
            execution=ActionExecutionFacts(
                executed_at=utcnow() + executed_delta,
                quantity=quantity,
                price=1.0,
                price_source="owner_broker_statement",
                fees=0.0,
                resulting_shares=resulting,
                close_fact=False,
            ),
        )
        with pytest.raises(ActionTransitionValidationError, match=error_fragment):
            await transition_position_action(session, command)
        await session.rollback()


@pytest.mark.asyncio
async def test_zero_target_requires_consistent_close_fact(app) -> None:
    position_id, action_id, version = await _seed_action(app, target=0.0)
    async with app.state.db.session() as session:
        command = ActionTransitionCommand(
            owner_id=1,
            position_id=position_id,
            action_id=action_id,
            transition=ActionTransitionKind.EXECUTE,
            idempotency_key="missing-close-fact",
            expected_position_state_version=version,
            occurred_at=utcnow(),
            actor_id=1,
            execution=ActionExecutionFacts(
                executed_at=utcnow(),
                quantity=1000,
                price=1.0,
                price_source="owner_broker_statement",
                fees=0.0,
                resulting_shares=0,
                close_fact=False,
            ),
        )
        with pytest.raises(ActionTransitionValidationError, match="close_fact"):
            await transition_position_action(session, command)
        await session.rollback()


@pytest.mark.asyncio
async def test_action_execution_rolls_back_position_action_fill_and_audit_together(app) -> None:
    position_id, action_id, version = await _seed_action(app)
    async with app.state.db.session() as session:
        result = await transition_position_action(
            session,
            ActionTransitionCommand(
                owner_id=1,
                position_id=position_id,
                action_id=action_id,
                transition=ActionTransitionKind.EXECUTE,
                idempotency_key="rollback-fill",
                expected_position_state_version=version,
                occurred_at=utcnow(),
                actor_id=1,
                execution=ActionExecutionFacts(
                    executed_at=utcnow(),
                    quantity=200,
                    price=1.0,
                    price_source="owner_broker_statement",
                    fees=0.0,
                    resulting_shares=800,
                    close_fact=False,
                ),
            ),
        )
        assert result.action_status == "partially_executed"
        await session.rollback()

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        action = await session.get(TrackedPositionActionDecision, action_id)
        execution_count = await session.scalar(
            select(func.count())
            .select_from(TrackedPositionActionExecution)
            .where(TrackedPositionActionExecution.action_decision_id == action_id)
        )
        transition_audit_count = await session.scalar(
            select(func.count())
            .select_from(TrackedPositionAlertAudit)
            .where(TrackedPositionAlertAudit.alert_type == "position_action_transition")
        )
    assert position is not None and action is not None
    assert position.confirmed_shares == 1000.0
    assert position.exit_state_version == version
    assert action.status == "proposed"
    assert execution_count == 0
    assert transition_audit_count == 0


@pytest.mark.asyncio
async def test_terminal_or_non_current_action_rejects_new_transition(app) -> None:
    position_id, action_id, version = await _seed_action(
        app,
        action_status="superseded",
        action_current=False,
    )
    async with app.state.db.session() as session:
        with pytest.raises(ActionTransitionConflictError, match="terminal|current"):
            await transition_position_action(
                session,
                ActionTransitionCommand(
                    owner_id=1,
                    position_id=position_id,
                    action_id=action_id,
                    transition=ActionTransitionKind.ACKNOWLEDGE,
                    idempotency_key="terminal-new-request",
                    expected_position_state_version=version,
                    occurred_at=utcnow(),
                    actor_id=1,
                ),
            )


@pytest.mark.asyncio
async def test_cancelled_cycle_blocks_same_target_but_allows_stricter_stage(app) -> None:
    position_id, action_id, version = await _seed_action(app)
    async with app.state.db.session() as session:
        await transition_position_action(
            session,
            ActionTransitionCommand(
                owner_id=1,
                position_id=position_id,
                action_id=action_id,
                transition=ActionTransitionKind.CANCEL,
                idempotency_key="cancel-cycle",
                expected_position_state_version=version,
                occurred_at=utcnow(),
                actor_id=1,
            ),
        )
        await session.commit()

    def proposal(target: float, expected_version: int) -> PersistActionDecisionCommand:
        return PersistActionDecisionCommand(
            owner_id=1,
            position_id=position_id,
            expected_exit_state_version=expected_version,
            position_episode_id=f"position-episode-{position_id}",
            exposure_version=1,
            policy_version="policy-v1",
            action_cycle_id=f"action-cycle-{position_id}",
            target_remaining_fraction=target,
            baseline_normalized_quantity=1000.0,
            baseline_account_weight=0.25,
            baseline_adjustment_factor=1.0,
            baseline_source="confirmed_shares",
            current_normalized_quantity=1000.0,
            input_snapshot_hash="b" * 64,
            data_state="eligible",
            contributing_rules=("hard_stop",),
            alert_episode_ids=(f"alert-episode-{position_id}",),
        )

    async with app.state.db.session() as session:
        same = await persist_action_decision(
            session,
            proposal(0.5, version + 1),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        stricter = await persist_action_decision(
            session,
            proposal(0.0, version + 1),
            rollout_policy=ACTIVE_ROLLOUT,
        )
        await session.commit()
        action_count = await session.scalar(
            select(func.count())
            .select_from(TrackedPositionActionDecision)
            .where(TrackedPositionActionDecision.tracked_position_id == position_id)
        )

    assert same.action.id == action_id
    assert same.action.status == "cancelled"
    assert same.outcome == "existing"
    assert stricter.action.id != action_id
    assert stricter.action.target_remaining_fraction == 0.0
    assert action_count == 2


@pytest.mark.asyncio
async def test_tracked_position_responses_expose_bounded_safe_action_projection(app, client) -> None:
    position_id, action_id, version = await _seed_action(app)
    detail = await client.get(f"/api/tracked-positions/{position_id}")
    listing = await client.get("/api/tracked-positions")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["exit_state_version"] == version
    assert payload["current_action"]["id"] == action_id
    assert payload["action_history"][0]["id"] == action_id
    assert len(payload["action_history"]) <= 20
    internal_fields = {
        "position_episode_id",
        "exposure_version",
        "action_cycle_id",
        "target_stage",
        "input_snapshot_hash",
        "alert_episode_ids_json",
    }
    assert internal_fields.isdisjoint(payload["current_action"])

    assert listing.status_code == 200
    listed = next(item for item in listing.json()["items"] if item["id"] == position_id)
    assert listed["current_action"]["id"] == action_id
    assert "action_history" not in listed
