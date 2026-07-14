from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
)
from app.services.tracked_positions.action_repository import (
    PersistActionDecisionCommand,
    StalePositionStateError,
    persist_action_decision,
)
from app.services.tracked_positions.exposure_repository import (
    ExposureMutationCommand,
    ExposureMutationIntent,
    ExposureMutationSource,
    apply_exposure_mutation,
)
from app.services.tracked_positions.lifecycle_rollout import (
    LifecycleRolloutMode,
    LifecycleRolloutPolicy,
)

ACTIVE_ROLLOUT = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
NOW = datetime(2026, 7, 14, 15, 0)


async def _position(app, *, estimated: float | None = 1000.0, with_baseline: bool = True) -> int:
    state = {
        "position_episode_id": "position-episode-1",
        "exposure_version": 1,
        "alert_rule_states": {},
        "current_action_id": None,
        "open_action_cycle_id": None,
    }
    if with_baseline:
        state["exposure_baseline"] = {
            "normalized_quantity": 1000.0,
            "source": "confirmed_shares",
            "adjustment_factor": 1.0,
        }
    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="513520",
            asset_name="日经ETF",
            buy_date=date(2026, 7, 1),
            confirmed_shares=1000.0 if with_baseline else None,
            buy_amount=1000.0,
            entry_price=1.0,
            estimated_shares=estimated,
            exit_state_json=state,
            exit_state_version=0,
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)
        return position.id


def _proposal(position_id: int) -> PersistActionDecisionCommand:
    return PersistActionDecisionCommand(
        owner_id=1,
        position_id=position_id,
        expected_exit_state_version=0,
        position_episode_id="position-episode-1",
        exposure_version=1,
        policy_version="policy-v2",
        action_cycle_id="cycle-1",
        target_remaining_fraction=0.5,
        baseline_normalized_quantity=1000.0,
        baseline_account_weight=0.3,
        baseline_adjustment_factor=1.0,
        baseline_source="confirmed_shares",
        current_normalized_quantity=1000.0,
        input_snapshot_hash="a" * 64,
        data_state="eligible",
        contributing_rules=("trailing_take_profit",),
        alert_episode_ids=("alert-1",),
    )


@pytest.mark.asyncio
async def test_net_add_cas_starts_new_exposure_and_supersedes_open_action(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        action = await persist_action_decision(
            session, _proposal(position_id), rollout_policy=ACTIVE_ROLLOUT
        )
        await session.commit()

    async with app.state.db.session() as session:
        receipt = await apply_exposure_mutation(
            session,
            ExposureMutationCommand(
                owner_id=1,
                position_id=position_id,
                expected_exit_state_version=1,
                intent=ExposureMutationIntent.NET_ADD,
                source=ExposureMutationSource.PATCH,
                occurred_at=NOW,
                request_id="net-add-1",
                new_confirmed_shares=1500.0,
                new_estimated_shares=1500.0,
            ),
        )
        await session.commit()

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        old_action = await session.get(TrackedPositionActionDecision, action.action.id)
        audit = await session.scalar(select(TrackedPositionAlertAudit))

    assert receipt.event == "net_add"
    assert receipt.exit_state_version == 2
    assert position is not None
    assert position.confirmed_shares == 1500.0
    assert position.estimated_shares == 1500.0
    assert position.exit_state_json["exposure_version"] == 2
    assert position.exit_state_json["exposure_baseline"]["normalized_quantity"] == 1500.0
    assert position.exit_state_json["alert_rule_states"] == {}
    assert old_action is not None
    assert old_action.status == "superseded"
    assert old_action.status_reason == "exposure_net_add"
    assert old_action.is_current is False
    assert audit is not None
    assert audit.alert_type == "position_exposure_mutation"
    assert audit.from_state == "exposure:1"
    assert audit.to_state == "exposure:2"


@pytest.mark.asyncio
async def test_net_reduce_preserves_exposure_baseline_and_version(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        await apply_exposure_mutation(
            session,
            ExposureMutationCommand(
                owner_id=1,
                position_id=position_id,
                expected_exit_state_version=0,
                intent=ExposureMutationIntent.NET_REDUCE,
                source=ExposureMutationSource.PATCH,
                occurred_at=NOW,
                request_id="net-reduce-1",
                new_confirmed_shares=800.0,
                new_estimated_shares=800.0,
            ),
        )
        await session.commit()

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)

    assert position is not None
    assert position.exit_state_json["exposure_version"] == 1
    assert position.exit_state_json["exposure_baseline"]["normalized_quantity"] == 1000.0
    assert position.exit_state_json["current_normalized_quantity"] == 800.0


@pytest.mark.asyncio
async def test_system_estimate_update_does_not_rebase_existing_exposure(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        await apply_exposure_mutation(
            session,
            ExposureMutationCommand(
                owner_id=1,
                position_id=position_id,
                expected_exit_state_version=0,
                intent=ExposureMutationIntent.SYSTEM_ESTIMATE,
                source=ExposureMutationSource.RECALCULATION,
                occurred_at=NOW,
                request_id="estimate-1",
                new_estimated_shares=1100.0,
            ),
        )
        await session.commit()

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)

    assert position is not None
    assert position.estimated_shares == 1100.0
    assert position.exit_state_json["exposure_version"] == 1
    assert position.exit_state_json["exposure_baseline"]["normalized_quantity"] == 1000.0
    assert position.exit_state_json.get("current_action_id") is None


@pytest.mark.asyncio
async def test_stale_or_non_finite_mutation_changes_no_fields_or_audit(app) -> None:
    position_id = await _position(app)
    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="finite"):
            await apply_exposure_mutation(
                session,
                ExposureMutationCommand(
                    owner_id=1,
                    position_id=position_id,
                    expected_exit_state_version=0,
                    intent=ExposureMutationIntent.NET_ADD,
                    source=ExposureMutationSource.PATCH,
                    occurred_at=NOW,
                    request_id="invalid-1",
                    new_confirmed_shares=float("nan"),
                ),
            )
        await session.rollback()
    async with app.state.db.session() as session:
        with pytest.raises(StalePositionStateError):
            await apply_exposure_mutation(
                session,
                ExposureMutationCommand(
                    owner_id=1,
                    position_id=position_id,
                    expected_exit_state_version=99,
                    intent=ExposureMutationIntent.NET_ADD,
                    source=ExposureMutationSource.PATCH,
                    occurred_at=NOW,
                    request_id="stale-1",
                    new_confirmed_shares=1200.0,
                ),
            )
        await session.rollback()
    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        audit_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionAlertAudit)
        )

    assert position is not None
    assert position.confirmed_shares == 1000.0
    assert position.exit_state_version == 0
    assert audit_count == 0


@pytest.mark.asyncio
async def test_patch_and_close_routes_use_versioned_exposure_mutations(client, app) -> None:
    position_id = await _position(app)
    patch_response = await client.patch(
        f"/api/tracked-positions/{position_id}",
        json={
            "confirmed_shares": 1200.0,
            "exposure_mutation_intent": "net_add",
            "mutation_reason": "owner added shares",
            "expected_exit_state_version": 0,
        },
    )
    assert patch_response.status_code == 200
    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        assert position is not None
        patch_version = position.exit_state_version
        assert position.confirmed_shares == 1200.0
        assert position.exit_state_json["exposure_version"] == 2

    stale_response = await client.patch(
        f"/api/tracked-positions/{position_id}",
        json={
            "buy_amount": 1200.0,
            "exposure_mutation_intent": "correction",
            "mutation_reason": "cost correction",
            "expected_exit_state_version": 0,
        },
    )
    assert stale_response.status_code == 409

    close_response = await client.post(
        f"/api/tracked-positions/{position_id}/close",
        json={"status": "stopped", "expected_exit_state_version": patch_version},
    )
    assert close_response.status_code == 200
    async with app.state.db.session() as session:
        closed = await session.get(TrackedPosition, position_id)
    assert closed is not None
    assert closed.status == "stopped"
    assert closed.exit_state_version == patch_version + 1


@pytest.mark.asyncio
async def test_get_position_does_not_materialize_or_commit_missing_estimate(client, app) -> None:
    position_id = await _position(app, estimated=None, with_baseline=False)

    response = await client.get(f"/api/tracked-positions/{position_id}")

    assert response.status_code == 200
    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        audit_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionAlertAudit)
        )
    assert position is not None
    assert position.estimated_shares is None
    assert position.exit_state_version == 0
    assert audit_count == 0


def test_production_exposure_fields_have_no_direct_write_bypass() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    allowed = {
        app_root / "models" / "entities.py",
        app_root / "services" / "tracked_positions" / "exposure_repository.py",
    }
    protected = {"confirmed_shares", "estimated_shares", "buy_amount", "status"}
    violations: list[str] = []
    for path in app_root.rglob("*.py"):
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                tracked_module = (
                    "tracked_positions" in path.parts
                    or path == app_root / "api" / "routes" / "tracked_positions.py"
                )
                tracked_name = isinstance(target, ast.Attribute) and isinstance(
                    target.value, ast.Name
                ) and target.value.id in {"position", "row"}
                if tracked_module and tracked_name and target.attr in protected:
                    violations.append(f"{path.relative_to(app_root)}:{node.lineno}:{target.attr}")
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "TrackedPosition":
                fields = protected & {keyword.arg for keyword in node.keywords if keyword.arg}
                if fields:
                    violations.append(
                        f"{path.relative_to(app_root)}:{node.lineno}:constructor:{sorted(fields)}"
                    )
    assert violations == []
