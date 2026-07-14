from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, select, update

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    TrackedPositionLifecycleShadowEvidence,
    TrackedPositionLifecycleShadowImmutableError,
)
from app.services.tracked_positions import lifecycle_rollout
from app.services.tracked_positions.action_repository import (
    LifecycleWritesDisabledError,
    PersistActionDecisionCommand,
    persist_action_decision,
)
from app.services.tracked_positions.lifecycle_rollout import (
    CutoverGateObservation,
    LifecycleRolloutMode,
    LifecycleRolloutPolicy,
    evaluate_cutover_gates,
    read_position_action_projection,
    safe_rollback_policy,
)


async def _legacy_position(app) -> int:
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
                "latest_position_action": {
                    "position_action": "reduce",
                    "recommended_action_label": "旧口径减仓",
                    "cooldown_end": "2026-07-20",
                },
            },
            exit_state_version=0,
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)
        return position.id


def _command(position_id: int) -> PersistActionDecisionCommand:
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
        alert_episode_ids=("alert-episode-1",),
    )


def test_rollout_modes_never_reenable_legacy_relative_action_writes() -> None:
    active = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
    actions_only = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIONS_ONLY)
    read_only = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.READ_ONLY)
    shadow = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.SHADOW)
    rollback = safe_rollback_policy()

    assert active.v2_action_writes_enabled is True
    assert active.production_state_writes_enabled is True
    assert active.v2_read_priority_enabled is True
    assert active.notification_generation_enabled is True
    assert actions_only.v2_action_writes_enabled is True
    assert actions_only.production_state_writes_enabled is True
    assert actions_only.v2_read_priority_enabled is True
    assert actions_only.notification_generation_enabled is False
    assert read_only.v2_action_writes_enabled is False
    assert read_only.production_state_writes_enabled is False
    assert read_only.v2_read_priority_enabled is True
    assert read_only.notification_generation_enabled is False
    assert shadow.v2_action_writes_enabled is False
    assert shadow.production_state_writes_enabled is False
    assert shadow.shadow_evidence_writes_enabled is True
    assert shadow.v2_read_priority_enabled is False
    assert rollback.mode is LifecycleRolloutMode.DISPLAY_ONLY
    assert rollback.v2_action_writes_enabled is False
    assert rollback.production_state_writes_enabled is False
    assert rollback.v2_read_priority_enabled is True
    assert rollback.notification_generation_enabled is False
    assert all(
        policy.legacy_relative_action_writes_enabled is False
        for policy in (active, actions_only, read_only, shadow, rollback)
    )


def test_rollout_policy_rejects_flags_that_bypass_display_only_rollback() -> None:
    with pytest.raises(ValueError, match="rollout policy flags do not match mode"):
        LifecycleRolloutPolicy(
            mode=LifecycleRolloutMode.DISPLAY_ONLY,
            v2_action_writes_enabled=True,
            v2_read_priority_enabled=True,
            notification_generation_enabled=False,
        )


def test_cutover_gates_reject_caller_supplied_observations() -> None:
    observation = CutoverGateObservation(evaluation_count=1_000, error_count=0)

    with pytest.raises(
        ValueError, match="cutover observation must come from immutable evidence"
    ):
        evaluate_cutover_gates(observation, max_error_rate=0.01)


@pytest.mark.parametrize(
    "session_count,eligible_session_count,expected_pass",
    [(1, 1, False), (2, 2, False), (3, 3, True), (3, 0, False), (3, 2, False)],
)
@pytest.mark.asyncio
async def test_cutover_gate_requires_three_distinct_sessions(
    app,
    session_count: int,
    eligible_session_count: int,
    expected_pass: bool,
) -> None:
    position_id = await _legacy_position(app)
    rows = [
        TrackedPositionLifecycleShadowEvidence(
            user_id=1,
            tracked_position_id=position_id,
            policy_version="policy-v2",
            position_episode_id="position-episode-1",
            exposure_version=1,
            stream_sequence=index,
            predecessor_event_id=None if index == 1 else str(index) * 64,
            event_id=str(index + 1) * 64,
            event_schema_version="etf_alert_action_event_v1",
            trade_session=date(2026, 7, 13 + index),
            repeat_slot=f"2026-07-{13 + index}:close",
            sealed_snapshot_hash=str(index + 3) * 64,
            production_position_state_version=0,
            rule_states_json={},
            transitions_json=[],
            action_evidence_json={
                "disposition": "reuse_current",
                "legacy_comparison_status": "compared",
                "legacy_difference": False,
            },
            data_state=("eligible" if index <= eligible_session_count else "data_waiting"),
            occurred_at=datetime(2026, 7, 13 + index, 15),
        )
        for index in range(1, session_count + 1)
    ]
    async with app.state.db.session() as session:
        session.add_all(rows)
        await session.commit()

    async with app.state.db.session() as session:
        observation = await lifecycle_rollout.collect_cutover_gate_observation(
            session,
            policy_version="policy-v2",
            session_start=date(2026, 7, 14),
            session_end=date(2026, 7, 16),
            max_evaluations=10,
        )

    result = evaluate_cutover_gates(observation, max_error_rate=0.0)
    assert result.passed is expected_pass
    assert observation.distinct_session_count == min(
        session_count,
        eligible_session_count,
    )
    assert ("insufficient_distinct_sessions" in result.failures) is (not expected_pass)


@pytest.mark.asyncio
async def test_cutover_collector_derives_scope_and_rejects_limit_plus_one(app) -> None:
    position_id = await _legacy_position(app)
    rows = [
        TrackedPositionLifecycleShadowEvidence(
            user_id=1,
            tracked_position_id=position_id,
            policy_version="policy-v2",
            position_episode_id="position-episode-1",
            exposure_version=1,
            stream_sequence=index,
            predecessor_event_id=None if index == 1 else "a" * 64,
            event_id=("a" if index == 1 else "b") * 64,
            event_schema_version="etf_alert_action_event_v1",
            trade_session=session_date,
            repeat_slot=f"{session_date.isoformat()}:close",
            sealed_snapshot_hash=snapshot_hash,
            production_position_state_version=0,
            rule_states_json={},
            transitions_json=[],
            action_evidence_json={
                "disposition": "create",
                "action_cycle_id": f"cycle-{index}",
                "target_stage": "remaining_0bp",
                "target_remaining_fraction": 0.0,
                "candidate_target_fractions": [0.0, 0.5],
                "legacy_comparison_status": "compared",
                "legacy_difference": False,
            },
            data_state="eligible" if index == 1 else "no_data",
            occurred_at=datetime.combine(session_date, datetime.min.time()).replace(hour=15),
        )
        for index, (session_date, snapshot_hash) in enumerate(
            [
                (date(2026, 7, 14), "1" * 64),
                (date(2026, 7, 15), "2" * 64),
            ],
            start=1,
        )
    ]
    async with app.state.db.session() as session:
        session.add_all(rows)
        await session.commit()

    async with app.state.db.session() as session:
        observation = await lifecycle_rollout.collect_cutover_gate_observation(
            session,
            policy_version="policy-v2",
            session_start=date(2026, 7, 14),
            session_end=date(2026, 7, 15),
            max_evaluations=10,
        )

    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="exceeds max_evaluations"):
            await lifecycle_rollout.collect_cutover_gate_observation(
                session,
                policy_version="policy-v2",
                session_start=date(2026, 7, 14),
                session_end=date(2026, 7, 15),
                max_evaluations=1,
            )

    result = evaluate_cutover_gates(observation, max_error_rate=0.01)
    assert observation.policy_version == "policy-v2"
    assert observation.sealed_snapshot_hashes == ("1" * 64, "2" * 64)
    assert observation.evaluation_count == 2
    assert observation.distinct_session_count == 1
    assert observation.collected_scope_valid is True
    assert len(observation.collection_hash) == 64
    assert observation.actions_from_ineligible_data == 1
    assert result.passed is False
    assert "actions_from_ineligible_data" in result.failures


@pytest.mark.asyncio
async def test_lifecycle_shadow_evidence_is_immutable_during_retention(app) -> None:
    position_id = await _legacy_position(app)
    evidence = TrackedPositionLifecycleShadowEvidence(
        user_id=1,
        tracked_position_id=position_id,
        policy_version="policy-v2",
        position_episode_id="position-episode-1",
        exposure_version=1,
        stream_sequence=1,
        predecessor_event_id=None,
        event_id="c" * 64,
        event_schema_version="etf_alert_action_event_v1",
        trade_session=date(2026, 7, 15),
        repeat_slot="2026-07-15:close",
        sealed_snapshot_hash="d" * 64,
        production_position_state_version=0,
        rule_states_json={},
        transitions_json=[],
        action_evidence_json={"disposition": "none"},
        data_state="eligible",
        occurred_at=datetime(2026, 7, 15, 15),
    )
    async with app.state.db.session() as session:
        session.add(evidence)
        await session.commit()
        await session.refresh(evidence)
        evidence_id = evidence.id

    async with app.state.db.session() as session:
        stored = await session.get(TrackedPositionLifecycleShadowEvidence, evidence_id)
        assert stored is not None
        stored.data_state = "error"
        with pytest.raises(
            TrackedPositionLifecycleShadowImmutableError,
            match="shadow evidence is immutable",
        ):
            await session.commit()
        await session.rollback()

    async with app.state.db.session() as session:
        stored = await session.get(TrackedPositionLifecycleShadowEvidence, evidence_id)
        assert stored is not None
        assert stored.data_state == "eligible"
        before_raw_change = await lifecycle_rollout.collect_cutover_gate_observation(
            session,
            policy_version="policy-v2",
            session_start=date(2026, 7, 15),
            session_end=date(2026, 7, 15),
            max_evaluations=10,
        )
        await session.execute(
            update(TrackedPositionLifecycleShadowEvidence)
            .where(TrackedPositionLifecycleShadowEvidence.id == evidence_id)
            .values(data_state="error")
        )
        await session.commit()

    async with app.state.db.session() as session:
        after_raw_change = await lifecycle_rollout.collect_cutover_gate_observation(
            session,
            policy_version="policy-v2",
            session_start=date(2026, 7, 15),
            session_end=date(2026, 7, 15),
            max_evaluations=10,
        )

    assert before_raw_change.collection_hash != after_raw_change.collection_hash


@pytest.mark.parametrize(
    "failure",
    [
        "repeated_same_stage_actions",
        "actions_from_ineligible_data",
        "smtp_to_executed_transitions",
        "strictest_target_mismatches",
        "unexplained_legacy_differences",
        "error_rate_exceeded",
    ],
)
@pytest.mark.asyncio
async def test_cutover_gate_rejects_each_failed_invariant_from_immutable_evidence(
    app, failure: str
) -> None:
    position_id = await _legacy_position(app)
    hashes = (
        ("6" * 64, "7" * 64)
        if failure == "repeated_same_stage_actions"
        else ("a" * 64,)
        if failure == "smtp_to_executed_transitions"
        else ("6" * 64,)
    )
    rows: list[TrackedPositionLifecycleShadowEvidence] = []
    for index, snapshot_hash in enumerate(hashes, start=1):
        data_state = (
            "no_data"
            if failure == "actions_from_ineligible_data"
            else "error"
            if failure == "error_rate_exceeded"
            else "eligible"
        )
        actionable = failure in {
            "repeated_same_stage_actions",
            "actions_from_ineligible_data",
            "strictest_target_mismatches",
        }
        selected_target = 0.5 if failure == "strictest_target_mismatches" else 0.0
        evidence = {
            "disposition": "create" if actionable else "reuse_current",
            "action_cycle_id": "cycle-1",
            "target_stage": "remaining_5000bp" if selected_target == 0.5 else "remaining_0bp",
            "target_remaining_fraction": selected_target,
            "candidate_target_fractions": [0.0, 0.5],
            "legacy_comparison_status": "compared",
            "legacy_difference": failure == "unexplained_legacy_differences",
            "legacy_difference_explained": False,
        }
        rows.append(
            TrackedPositionLifecycleShadowEvidence(
                user_id=1,
                tracked_position_id=position_id,
                policy_version="policy-v2",
                position_episode_id="position-episode-1",
                exposure_version=1,
                stream_sequence=index,
                predecessor_event_id=None if index == 1 else rows[-1].event_id,
                event_id=("a" if index == 1 else "b") * 64,
                event_schema_version="etf_alert_action_event_v1",
                trade_session=date(2026, 7, 14),
                repeat_slot=f"2026-07-14:close:{index}",
                sealed_snapshot_hash=snapshot_hash,
                production_position_state_version=0,
                rule_states_json={},
                transitions_json=[],
                action_evidence_json=evidence,
                data_state=data_state,
                occurred_at=datetime(2026, 7, 14, 15, index),
            )
        )
    async with app.state.db.session() as session:
        session.add_all(rows)
        await session.commit()

    if failure == "smtp_to_executed_transitions":
        active = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
        async with app.state.db.session() as session:
            persisted = await persist_action_decision(
                session,
                _command(position_id),
                rollout_policy=active,
            )
            session.add(
                TrackedPositionAlertAudit(
                    tracked_position_id=position_id,
                    outcome="action_transition",
                    alert_date=date(2026, 7, 14),
                    alert_type="position_action_transition",
                    data_source="smtp",
                    quote_freshness="not_applicable",
                    action_decision_id=persisted.action.id,
                    policy_version="policy-v2",
                    data_state="eligible",
                    from_state="proposed",
                    to_state="executed",
                    occurred_at=datetime(2026, 7, 14, 15, 30),
                    execution_provenance="smtp_accepted",
                )
            )
            await session.commit()

    async with app.state.db.session() as session:
        observation = await lifecycle_rollout.collect_cutover_gate_observation(
            session,
            policy_version="policy-v2",
            session_start=date(2026, 7, 14),
            session_end=date(2026, 7, 14),
            max_evaluations=10,
        )

    result = evaluate_cutover_gates(observation, max_error_rate=0.0)
    assert result.passed is False
    assert failure in result.failures


@pytest.mark.asyncio
async def test_cutover_collector_keeps_smtp_acceptance_distinct_from_execution(app) -> None:
    position_id = await _legacy_position(app)
    active = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session,
            _command(position_id),
            rollout_policy=active,
        )
        session.add_all(
            [
                TrackedPositionLifecycleShadowEvidence(
                    user_id=1,
                    tracked_position_id=position_id,
                    policy_version="policy-v2",
                    position_episode_id="position-episode-1",
                    exposure_version=1,
                    stream_sequence=1,
                    predecessor_event_id=None,
                    event_id="b" * 64,
                    event_schema_version="etf_alert_action_event_v1",
                    trade_session=date(2026, 7, 14),
                    repeat_slot="2026-07-14:close",
                    sealed_snapshot_hash="a" * 64,
                    production_position_state_version=1,
                    rule_states_json={},
                    transitions_json=[],
                    action_evidence_json={
                        "disposition": "create",
                        "action_cycle_id": "cycle-1",
                        "target_stage": "remaining_5000bp",
                        "target_remaining_fraction": 0.5,
                        "candidate_target_fractions": [0.5],
                        "legacy_comparison_status": "compared",
                        "legacy_difference": False,
                    },
                    data_state="eligible",
                    occurred_at=datetime(2026, 7, 14, 15, 0),
                ),
                TrackedPositionAlertAudit(
                    tracked_position_id=position_id,
                    outcome="notification_delivery",
                    alert_date=date(2026, 7, 14),
                    alert_type="position_action_notification",
                    data_source="smtp",
                    quote_freshness="accepted",
                    action_decision_id=persisted.action.id,
                    policy_version="policy-v2",
                    data_state="eligible",
                    from_state="proposed",
                    to_state="proposed",
                    occurred_at=datetime(2026, 7, 14, 15, 30),
                    execution_provenance="smtp_accepted",
                ),
            ]
        )
        await session.commit()

    async with app.state.db.session() as session:
        observation = await lifecycle_rollout.collect_cutover_gate_observation(
            session,
            policy_version="policy-v2",
            session_start=date(2026, 7, 14),
            session_end=date(2026, 7, 14),
            max_evaluations=10,
        )
        action = await session.get(TrackedPositionActionDecision, persisted.action.id)

    result = evaluate_cutover_gates(observation, max_error_rate=0.0)
    assert observation.actions_from_ineligible_data == 0
    assert observation.smtp_to_executed_transitions == 0
    assert "smtp_to_executed_transitions" not in result.failures
    assert action is not None
    assert action.status == "proposed"


@pytest.mark.asyncio
async def test_cutover_collector_cannot_hide_smtp_execution_with_snapshot_mismatch(
    app,
) -> None:
    position_id = await _legacy_position(app)
    active = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session,
            _command(position_id),
            rollout_policy=active,
        )
        session.add_all(
            [
                TrackedPositionLifecycleShadowEvidence(
                    user_id=1,
                    tracked_position_id=position_id,
                    policy_version="policy-v2",
                    position_episode_id="position-episode-1",
                    exposure_version=1,
                    stream_sequence=1,
                    predecessor_event_id=None,
                    event_id="d" * 64,
                    event_schema_version="etf_alert_action_event_v1",
                    trade_session=date(2026, 7, 14),
                    repeat_slot="2026-07-14:close",
                    sealed_snapshot_hash="b" * 64,
                    production_position_state_version=1,
                    rule_states_json={},
                    transitions_json=[],
                    action_evidence_json={
                        "disposition": "reuse_current",
                        "legacy_comparison_status": "compared",
                        "legacy_difference": False,
                    },
                    data_state="eligible",
                    occurred_at=datetime(2026, 7, 14, 15, 0),
                ),
                TrackedPositionAlertAudit(
                    tracked_position_id=position_id,
                    outcome="action_transition",
                    alert_date=date(2026, 7, 14),
                    alert_type="position_action_transition",
                    data_source="smtp",
                    quote_freshness="not_applicable",
                    action_decision_id=persisted.action.id,
                    policy_version="policy-v2",
                    data_state="eligible",
                    from_state="proposed",
                    to_state="executed",
                    occurred_at=datetime(2026, 7, 14, 15, 30),
                    execution_provenance="smtp_accepted",
                ),
            ]
        )
        await session.commit()

    async with app.state.db.session() as session:
        observation = await lifecycle_rollout.collect_cutover_gate_observation(
            session,
            policy_version="policy-v2",
            session_start=date(2026, 7, 14),
            session_end=date(2026, 7, 14),
            max_evaluations=10,
        )

    assert observation.sealed_snapshot_hashes == ("b" * 64,)
    assert observation.smtp_to_executed_transitions == 1


@pytest.mark.asyncio
async def test_compatibility_read_prefers_v2_only_in_active_mode(app) -> None:
    position_id = await _legacy_position(app)
    active = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
    shadow = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.SHADOW)

    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session,
            _command(position_id),
            rollout_policy=active,
        )
        await session.commit()

    async with app.state.db.session() as session:
        active_projection = await read_position_action_projection(
            session,
            owner_id=1,
            position_id=position_id,
            rollout_policy=active,
        )
        shadow_projection = await read_position_action_projection(
            session,
            owner_id=1,
            position_id=position_id,
            rollout_policy=shadow,
        )

    assert active_projection.source == "v2"
    assert active_projection.action_id == persisted.action.id
    assert active_projection.status == "proposed"
    assert active_projection.executable is True
    assert shadow_projection.source == "legacy_read_only"
    assert shadow_projection.action_id is None
    assert shadow_projection.label == "旧口径减仓"
    assert shadow_projection.execution_provenance == "legacy_unverified"
    assert shadow_projection.executable is False


@pytest.mark.asyncio
async def test_safe_rollback_preserves_v2_facts_and_rejects_new_v2_writes(app) -> None:
    position_id = await _legacy_position(app)
    active = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.ACTIVE)
    rollback = safe_rollback_policy()

    async with app.state.db.session() as session:
        persisted = await persist_action_decision(
            session,
            _command(position_id),
            rollout_policy=active,
        )
        session.add(
            TrackedPositionAlertAudit(
                tracked_position_id=position_id,
                outcome="action_created",
                alert_date=date(2026, 7, 14),
                alert_type="trailing_take_profit",
                data_source="eastmoney",
                quote_freshness="fresh",
                action_decision_id=persisted.action.id,
                policy_version="policy-v2",
                data_state="eligible",
            )
        )
        await session.commit()

    async with app.state.db.session() as session:
        projection = await read_position_action_projection(
            session,
            owner_id=1,
            position_id=position_id,
            rollout_policy=rollback,
        )
        with pytest.raises(LifecycleWritesDisabledError):
            await persist_action_decision(
                session,
                _command(position_id),
                rollout_policy=rollback,
            )
        await session.rollback()

    async with app.state.db.session() as session:
        action_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionActionDecision)
        )
        audit_count = await session.scalar(
            select(func.count()).select_from(TrackedPositionAlertAudit)
        )
        position = await session.get(TrackedPosition, position_id)

    assert projection.source == "v2"
    assert projection.action_id == persisted.action.id
    assert projection.executable is False
    assert action_count == 1
    assert audit_count == 1
    assert position is not None
    assert position.exit_state_version == 1
