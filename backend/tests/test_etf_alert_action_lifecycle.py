from __future__ import annotations

from datetime import date

import pytest

from app.services.tracked_positions.lifecycle import (
    AbsolutePositionTarget,
    ActionStatus,
    AlertEvent,
    AlertRuleState,
    AlertState,
    EvaluationDataOutcome,
    EvaluationDataState,
    ExecutionProvenance,
    ExposureBaseline,
    ExposureMutationKind,
    NotificationEnvelopePayload,
    NotificationItemPayload,
    PositionExposureState,
    RuleActionCandidate,
    aggregate_action_candidates,
    calculate_absolute_position_target,
    initialize_position_exposure,
    mutate_position_exposure,
    stable_contract_hash,
    stable_contract_json,
    target_stage_for_fraction,
    transition_alert_state,
)


def test_lifecycle_contract_values_are_frozen() -> None:
    assert {state.value for state in EvaluationDataState} == {"eligible", "data_waiting", "no_data", "error"}
    assert {state.value for state in AlertState} == {"normal", "pending", "firing", "recovering", "resolved"}
    assert {status.value for status in ActionStatus} == {
        "proposed",
        "acknowledged",
        "partially_executed",
        "executed",
        "expired",
        "cancelled",
        "superseded",
    }
    assert ExecutionProvenance.LEGACY_UNVERIFIED.value == "legacy_unverified"
    assert ExecutionProvenance.OWNER_CONFIRMED.value == "owner_confirmed"
    assert AlertEvent.DATA_FROZEN.value == "data_frozen"


def test_evaluation_outcome_requires_stable_reason_code() -> None:
    eligible = EvaluationDataOutcome.eligible()
    waiting = EvaluationDataOutcome.invalid(EvaluationDataState.DATA_WAITING, "adjusted_price_stale")

    assert eligible.decision_eligible is True
    assert eligible.reason_code == "decision_eligible"
    assert waiting.decision_eligible is False
    assert waiting.reason_code == "adjusted_price_stale"

    with pytest.raises(ValueError, match="invalid data state"):
        EvaluationDataOutcome.invalid(EvaluationDataState.ELIGIBLE, "not-invalid")
    with pytest.raises(ValueError, match="reason_code"):
        EvaluationDataOutcome.invalid(EvaluationDataState.NO_DATA, " ")


@pytest.mark.parametrize(
    ("fraction", "stage"),
    [(0.0, "remaining_0bp"), (0.5, "remaining_5000bp"), (1.0, "remaining_10000bp")],
)
def test_target_stage_is_server_owned_and_stable(fraction: float, stage: str) -> None:
    assert target_stage_for_fraction(fraction) == stage


@pytest.mark.parametrize("fraction", [-0.01, 1.01, float("nan"), float("inf")])
def test_target_stage_rejects_invalid_fraction(fraction: float) -> None:
    with pytest.raises(ValueError, match="finite fraction"):
        target_stage_for_fraction(fraction)


def test_notification_item_and_envelope_identities_are_separate() -> None:
    item = NotificationItemPayload(
        alert_episode_id="alert-episode-1",
        transition="firing",
        recipient="owner@example.com",
        channel="email",
        repeat_slot="2026-07-14:close",
    )
    envelope = NotificationEnvelopePayload(
        owner_id=7,
        trade_session=date(2026, 7, 14),
        route="ordinary_digest",
        severity="warning",
        channel="email",
        sealed_snapshot_hash="snapshot-hash",
        digest_revision=1,
    )

    assert item.item_key == (
        "alert-episode-1",
        "firing",
        "owner@example.com",
        "email",
        "2026-07-14:close",
    )
    assert envelope.envelope_key == (7, "2026-07-14", "ordinary_digest", "warning", "email", "snapshot-hash", 1)
    assert "recipient" not in envelope.as_dict()
    assert "owner_id" not in item.as_dict()


def test_contract_serialization_and_hash_ignore_mapping_insertion_order() -> None:
    first = {"z": [3, 2, 1], "a": {"right": 2, "left": 1}}
    second = {"a": {"left": 1, "right": 2}, "z": [3, 2, 1]}

    assert stable_contract_json(first) == stable_contract_json(second)
    assert stable_contract_hash(first) == stable_contract_hash(second)
    assert len(stable_contract_hash(first)) == 64


def test_non_hard_stop_moves_normal_pending_firing_once() -> None:
    pending = transition_alert_state(
        AlertRuleState(),
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=True,
        confirmation_required=2,
        new_alert_episode_id="episode-1",
    )
    firing = transition_alert_state(
        pending.current,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=True,
        confirmation_required=2,
    )
    persistent = transition_alert_state(
        firing.current,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=True,
        confirmation_required=2,
    )

    assert (pending.from_state, pending.to_state, pending.event) == (
        AlertState.NORMAL,
        AlertState.PENDING,
        AlertEvent.PENDING_STARTED,
    )
    assert pending.current.alert_episode_id == "episode-1"
    assert pending.current.confirmation_count == 1
    assert firing.to_state is AlertState.FIRING
    assert firing.current.alert_episode_id == "episode-1"
    assert firing.emits_action is True
    assert persistent.to_state is AlertState.FIRING
    assert persistent.event is AlertEvent.UNCHANGED
    assert persistent.emits_action is False


def test_pending_condition_disappears_and_closes_unfired_episode() -> None:
    pending = AlertRuleState(
        state=AlertState.PENDING,
        alert_episode_id="episode-pending",
        confirmation_count=1,
    )

    transition = transition_alert_state(
        pending,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=False,
    )

    assert transition.to_state is AlertState.NORMAL
    assert transition.event is AlertEvent.PENDING_CANCELLED
    assert transition.current.alert_episode_id is None
    assert transition.current.confirmation_count == 0
    assert transition.closed_alert_episode_id == "episode-pending"
    assert transition.emits_action is False


def test_hard_stop_bypasses_pending() -> None:
    transition = transition_alert_state(
        AlertRuleState(),
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=True,
        hard_stop=True,
        new_alert_episode_id="hard-stop-episode",
    )

    assert transition.to_state is AlertState.FIRING
    assert transition.event is AlertEvent.HARD_STOP_BYPASS
    assert transition.current.alert_episode_id == "hard-stop-episode"
    assert transition.emits_action is True


def test_firing_recovery_relapse_and_resolution_reuse_one_episode() -> None:
    firing = AlertRuleState(state=AlertState.FIRING, alert_episode_id="episode-1", confirmation_count=2)
    recovering = transition_alert_state(
        firing,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=False,
        recovery_met=True,
        recovery_required=2,
    )
    relapsed = transition_alert_state(
        recovering.current,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=True,
        recovery_required=2,
    )
    recovering_again = transition_alert_state(
        relapsed.current,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=False,
        recovery_met=True,
        recovery_required=2,
    )
    resolved = transition_alert_state(
        recovering_again.current,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=False,
        recovery_met=True,
        recovery_required=2,
    )

    assert recovering.event is AlertEvent.RECOVERY_STARTED
    assert relapsed.event is AlertEvent.RELAPSED
    assert relapsed.to_state is AlertState.FIRING
    assert relapsed.current.alert_episode_id == "episode-1"
    assert relapsed.emits_action is False
    assert resolved.event is AlertEvent.RESOLVED
    assert resolved.to_state is AlertState.RESOLVED
    assert resolved.closed_alert_episode_id == "episode-1"
    assert resolved.emits_action is False


def test_resolved_trigger_creates_a_new_alert_episode() -> None:
    resolved = AlertRuleState(state=AlertState.RESOLVED, alert_episode_id="episode-old")

    transition = transition_alert_state(
        resolved,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=True,
        confirmation_required=2,
        new_alert_episode_id="episode-new",
    )

    assert transition.from_state is AlertState.RESOLVED
    assert transition.to_state is AlertState.PENDING
    assert transition.current.alert_episode_id == "episode-new"
    assert transition.current.alert_episode_id != resolved.alert_episode_id


def test_new_episode_id_is_required_only_when_an_episode_opens() -> None:
    with pytest.raises(ValueError, match="new_alert_episode_id"):
        transition_alert_state(
            AlertRuleState(),
            data_outcome=EvaluationDataOutcome.eligible(),
            condition_met=True,
        )

    unchanged = transition_alert_state(
        AlertRuleState(),
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=False,
    )
    assert unchanged.event is AlertEvent.UNCHANGED


@pytest.mark.parametrize(
    ("data_state", "reason_code"),
    [
        (EvaluationDataState.DATA_WAITING, "adjusted_price_stale"),
        (EvaluationDataState.NO_DATA, "adjusted_price_missing"),
        (EvaluationDataState.ERROR, "provider_failed"),
    ],
)
def test_invalid_data_outcomes_freeze_business_state_and_counters(
    data_state: EvaluationDataState,
    reason_code: str,
) -> None:
    firing = AlertRuleState(
        state=AlertState.FIRING,
        alert_episode_id="hard-stop-episode",
        confirmation_count=3,
        recovery_count=1,
    )
    outcome = EvaluationDataOutcome.invalid(data_state, reason_code)

    transition = transition_alert_state(
        firing,
        data_outcome=outcome,
        condition_met=True,
        hard_stop=True,
        new_alert_episode_id="must-not-be-used",
    )

    assert outcome.state is data_state
    assert outcome.reason_code == reason_code
    assert transition.current == firing
    assert transition.event is AlertEvent.DATA_FROZEN
    assert transition.frozen is True
    assert transition.emits_action is False
    assert transition.closed_alert_episode_id is None


def test_invalid_data_state_is_one_value_not_a_combinable_flag() -> None:
    with pytest.raises(ValueError):
        EvaluationDataState("data_waiting|no_data")


def test_valid_data_resumes_from_exact_frozen_state_without_fabricated_transition() -> None:
    pending = AlertRuleState(
        state=AlertState.PENDING,
        alert_episode_id="episode-1",
        confirmation_count=1,
    )
    frozen = transition_alert_state(
        pending,
        data_outcome=EvaluationDataOutcome.invalid(EvaluationDataState.ERROR, "provider_failed"),
        condition_met=False,
    )
    resumed = transition_alert_state(
        frozen.current,
        data_outcome=EvaluationDataOutcome.eligible(),
        condition_met=True,
        confirmation_required=2,
    )

    assert frozen.current == pending
    assert resumed.from_state is AlertState.PENDING
    assert resumed.to_state is AlertState.FIRING
    assert resumed.current.alert_episode_id == "episode-1"


def test_position_episode_and_exposure_version_follow_owner_quantity_changes() -> None:
    initialized = initialize_position_exposure(
        raw_quantity=1000.0,
        adjustment_factor=1.0,
        baseline_source="confirmed_shares",
        position_episode_id="position-episode-1",
    )
    reduced = mutate_position_exposure(
        initialized,
        raw_quantity=500.0,
        adjustment_factor=1.0,
        baseline_source="owner_confirmed_execution",
        mutation_kind=ExposureMutationKind.OWNER_TRADE,
    )
    added = mutate_position_exposure(
        reduced.current,
        raw_quantity=1200.0,
        adjustment_factor=1.0,
        baseline_source="owner_confirmed_add",
        mutation_kind=ExposureMutationKind.OWNER_TRADE,
    )

    assert initialized.position_episode_id == "position-episode-1"
    assert initialized.exposure_version == 1
    assert initialized.baseline == ExposureBaseline(
        normalized_quantity=1000.0,
        source="confirmed_shares",
        adjustment_factor=1.0,
    )
    assert reduced.event == "reduction"
    assert reduced.current.position_episode_id == initialized.position_episode_id
    assert reduced.current.exposure_version == 1
    assert reduced.current.baseline == initialized.baseline
    assert reduced.supersede_prior_actions is False
    assert added.event == "net_add"
    assert added.current.position_episode_id == initialized.position_episode_id
    assert added.current.exposure_version == 2
    assert added.current.baseline.normalized_quantity == 1200.0
    assert added.current.baseline.source == "owner_confirmed_add"
    assert added.supersede_prior_actions is True
    assert added.reset_rule_state is True


def test_full_close_then_reentry_creates_a_new_position_episode() -> None:
    active = initialize_position_exposure(
        raw_quantity=1000.0,
        adjustment_factor=1.0,
        baseline_source="confirmed_shares",
        position_episode_id="position-episode-old",
    )
    closed = mutate_position_exposure(
        active,
        raw_quantity=0.0,
        adjustment_factor=1.0,
        baseline_source="owner_confirmed_close",
        mutation_kind=ExposureMutationKind.OWNER_TRADE,
    )
    reopened = mutate_position_exposure(
        closed.current,
        raw_quantity=200.0,
        adjustment_factor=1.0,
        baseline_source="owner_confirmed_reentry",
        mutation_kind=ExposureMutationKind.OWNER_TRADE,
        new_position_episode_id="position-episode-new",
    )

    assert closed.event == "closed"
    assert closed.current.active is False
    assert reopened.event == "reentry"
    assert reopened.current.active is True
    assert reopened.current.position_episode_id == "position-episode-new"
    assert reopened.current.position_episode_id != active.position_episode_id
    assert reopened.current.exposure_version == 1
    assert reopened.current.baseline.normalized_quantity == 200.0


def test_corporate_action_normalizes_quantity_without_rearming_exposure() -> None:
    active = initialize_position_exposure(
        raw_quantity=1000.0,
        adjustment_factor=1.0,
        baseline_source="confirmed_shares",
        position_episode_id="position-episode-1",
    )

    split = mutate_position_exposure(
        active,
        raw_quantity=2000.0,
        adjustment_factor=2.0,
        baseline_source="corporate_action_split",
        mutation_kind=ExposureMutationKind.CORPORATE_ACTION,
    )

    assert split.event == "corporate_action"
    assert split.current.current_normalized_quantity == 1000.0
    assert split.current.current_adjustment_factor == 2.0
    assert split.current.position_episode_id == active.position_episode_id
    assert split.current.exposure_version == active.exposure_version
    assert split.current.baseline == active.baseline
    assert split.supersede_prior_actions is False
    assert split.reset_rule_state is False


def test_inactive_position_requires_server_owned_new_episode_id_for_reentry() -> None:
    inactive = PositionExposureState(
        position_episode_id="closed-episode",
        exposure_version=1,
        baseline=ExposureBaseline(100.0, "confirmed_shares", 1.0),
        current_normalized_quantity=0.0,
        current_adjustment_factor=1.0,
        active=False,
    )

    with pytest.raises(ValueError, match="new_position_episode_id"):
        mutate_position_exposure(
            inactive,
            raw_quantity=10.0,
            adjustment_factor=1.0,
            baseline_source="owner_confirmed_reentry",
            mutation_kind=ExposureMutationKind.OWNER_TRADE,
        )


def test_repeated_half_target_always_uses_immutable_exposure_baseline() -> None:
    targets = [
        calculate_absolute_position_target(
            baseline_normalized_quantity=1000.0,
            current_normalized_quantity=current_quantity,
            current_adjustment_factor=1.0,
            target_remaining_fraction=0.5,
            baseline_account_weight=0.3,
        )
        for current_quantity in (1000.0, 500.0, 500.0)
    ]

    assert all(isinstance(target, AbsolutePositionTarget) for target in targets)
    assert [target.calculated_target_normalized_quantity for target in targets] == [500.0, 500.0, 500.0]
    assert [target.target_account_weight for target in targets] == [0.15, 0.15, 0.15]
    assert [target.target_stage for target in targets] == ["remaining_5000bp"] * 3


def test_current_quantity_below_absolute_target_never_creates_a_buy() -> None:
    target = calculate_absolute_position_target(
        baseline_normalized_quantity=1000.0,
        current_normalized_quantity=400.0,
        current_adjustment_factor=1.0,
        target_remaining_fraction=0.5,
    )

    assert target.calculated_target_normalized_quantity == 500.0
    assert target.effective_target_normalized_quantity == 400.0
    assert target.recommended_sell_normalized_quantity == 0.0
    assert target.recommended_buy_normalized_quantity == 0.0
    assert target.target_already_satisfied is True


def test_absolute_target_respects_corporate_action_normalization() -> None:
    target = calculate_absolute_position_target(
        baseline_normalized_quantity=1000.0,
        current_normalized_quantity=1000.0,
        current_adjustment_factor=2.0,
        target_remaining_fraction=0.5,
    )

    assert target.calculated_target_normalized_quantity == 500.0
    assert target.calculated_target_raw_quantity == 1000.0
    assert target.recommended_sell_normalized_quantity == 500.0
    assert target.recommended_sell_raw_quantity == 1000.0
    assert target.target_already_satisfied is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"baseline_normalized_quantity": float("nan")},
        {"current_normalized_quantity": -1.0},
        {"current_adjustment_factor": 0.0},
        {"target_remaining_fraction": 1.1},
    ],
)
def test_absolute_target_rejects_non_finite_or_out_of_range_inputs(kwargs: dict[str, float]) -> None:
    inputs = {
        "baseline_normalized_quantity": 1000.0,
        "current_normalized_quantity": 1000.0,
        "current_adjustment_factor": 1.0,
        "target_remaining_fraction": 0.5,
    }
    inputs.update(kwargs)

    with pytest.raises(ValueError):
        calculate_absolute_position_target(**inputs)


def test_same_target_rules_aggregate_to_one_action_with_deterministic_reasons() -> None:
    result = aggregate_action_candidates(
        [
            RuleActionCandidate("trailing_take_profit", 0.5, "盈利回吐达到阈值"),
            RuleActionCandidate("confirmed_trend_weakening", 0.5, "趋势转弱已确认"),
        ],
        baseline_normalized_quantity=1000.0,
        current_normalized_quantity=1000.0,
        current_adjustment_factor=1.0,
    )

    assert result.disposition == "create"
    assert result.target is not None
    assert result.target.target_remaining_fraction == 0.5
    assert result.target.target_stage == "remaining_5000bp"
    assert result.contributing_rule_ids == ("confirmed_trend_weakening", "trailing_take_profit")
    assert result.reasons == ("趋势转弱已确认", "盈利回吐达到阈值")
    assert result.supersedes_current is False


def test_simultaneous_half_and_zero_targets_leave_only_zero_current() -> None:
    result = aggregate_action_candidates(
        [
            RuleActionCandidate("trailing_take_profit", 0.5, "先保护一半盈利"),
            RuleActionCandidate("hard_stop", 0.0, "硬止损触发"),
        ],
        baseline_normalized_quantity=1000.0,
        current_normalized_quantity=1000.0,
        current_adjustment_factor=1.0,
    )

    assert result.target is not None
    assert result.target.target_remaining_fraction == 0.0
    assert result.target.target_stage == "remaining_0bp"
    assert result.target.recommended_sell_normalized_quantity == 1000.0
    assert result.contributing_rule_ids == ("hard_stop", "trailing_take_profit")


def test_stricter_target_requests_atomic_supersession_intent() -> None:
    result = aggregate_action_candidates(
        [RuleActionCandidate("hard_stop", 0.0, "硬止损触发")],
        baseline_normalized_quantity=1000.0,
        current_normalized_quantity=500.0,
        current_adjustment_factor=1.0,
        current_action_target_fraction=0.5,
    )

    assert result.disposition == "supersede"
    assert result.supersedes_current is True
    assert result.prior_target_stage == "remaining_5000bp"
    assert result.target is not None
    assert result.target.target_stage == "remaining_0bp"


def test_same_or_weaker_target_reuses_existing_stricter_action() -> None:
    result = aggregate_action_candidates(
        [RuleActionCandidate("confirmed_trend_weakening", 0.5, "趋势转弱已确认")],
        baseline_normalized_quantity=1000.0,
        current_normalized_quantity=500.0,
        current_adjustment_factor=1.0,
        current_action_target_fraction=0.0,
    )

    assert result.disposition == "reuse_current"
    assert result.supersedes_current is False
    assert result.target is not None
    assert result.target.target_remaining_fraction == 0.0


def test_ineligible_candidates_and_already_satisfied_target_create_no_sell() -> None:
    ineligible = aggregate_action_candidates(
        [RuleActionCandidate("exit_watch", 0.0, "排名证据缺失", eligible=False)],
        baseline_normalized_quantity=1000.0,
        current_normalized_quantity=1000.0,
        current_adjustment_factor=1.0,
    )
    satisfied = aggregate_action_candidates(
        [RuleActionCandidate("trailing_take_profit", 0.5, "盈利回吐达到阈值")],
        baseline_normalized_quantity=1000.0,
        current_normalized_quantity=400.0,
        current_adjustment_factor=1.0,
    )

    assert ineligible.disposition == "none"
    assert ineligible.target is None
    assert satisfied.disposition == "target_already_satisfied"
    assert satisfied.target is not None
    assert satisfied.target.recommended_sell_normalized_quantity == 0.0
