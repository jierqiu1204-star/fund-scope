from __future__ import annotations

from datetime import date

import pytest

from app.services.strategy_lab.etf_action_replay.checkpoint import (
    IncompatibleCheckpointError,
    IncompleteCheckpointError,
    NewRunIdentityRequiredError,
    ReplayRunContract,
    build_replay_checkpoint,
    checkpoint_size_bytes,
    load_replay_checkpoint,
    write_replay_checkpoint_atomic,
)
from app.services.strategy_lab.etf_action_replay.execution import DeferredExecutionSession
from app.services.strategy_lab.etf_action_replay.replay import (
    CandidatePortfolioState,
    PendingFillState,
    ReplayCandidateConfig,
    ReplayPositionState,
    ReplayState,
    canonical_candidate_config_hash,
    canonical_policy_input_hash,
)

FROZEN_PARAMETER_HASH = "frozen-parameters-a"
BASE_CANDIDATES = (
    ReplayCandidateConfig("candidate-2", top_n=2),
    ReplayCandidateConfig("candidate-3", top_n=3),
)


def _contract(**overrides: object) -> ReplayRunContract:
    values: dict[str, object] = {
        "run_id": "run-1",
        "contract_hash": "contract-a",
        "input_snapshot_hash": "input-a",
        "code_hash": "code-a",
        "schema_hash": "schema-a",
        "candidate_config_hash": canonical_candidate_config_hash(
            BASE_CANDIDATES,
            frozen_parameter_hash=FROZEN_PARAMETER_HASH,
        ),
        "frozen_parameter_hash": FROZEN_PARAMETER_HASH,
        "policy_input_hash": canonical_policy_input_hash(()),
        "data_cutoff": date(2026, 2, 4),
        "warmup_boundary": date(2026, 1, 1),
        "candidate_ids": ("candidate-2", "candidate-3"),
    }
    values.update(overrides)
    return ReplayRunContract(**values)  # type: ignore[arg-type]


def _state() -> ReplayState:
    position = ReplayPositionState(
        shares=100.0,
        exposure_baseline_shares=200.0,
        high_watermark=12.0,
        position_episode_id="position-episode-1",
        exposure_version=1,
        rule_states={"trend": "firing"},
        alert_episode_ids={"trend": "alert-1"},
        action_decisions={"action-1": ("cycle-1", 0.5)},
        cycle_targets={"cycle-1": 0.5},
        action_cycle_id="cycle-1",
        current_action_decision_id="action-1",
        current_target_remaining_fraction=0.5,
    )
    return ReplayState(
        candidate_states={
            "candidate-2": CandidatePortfolioState(
                cash=10_000.0,
                positions={"510001": position},
                pending_fills=(
                    PendingFillState(
                        order_id="order-1",
                        asset_code="510001",
                        signal_date=date(2026, 2, 4),
                        side="sell",
                        rank=0,
                        target_remaining_fraction=0.5,
                        reason="trend",
                        action_cycle_id="cycle-1",
                        action_decision_id="action-1",
                        deferred_sessions=(
                            DeferredExecutionSession(date(2026, 2, 5), "suspended"),
                        ),
                    ),
                ),
                cumulative_fees=12.5,
                turnover=20_000.0,
                equity=21_000.0,
            ),
            "candidate-3": CandidatePortfolioState(
                cash=20_000.0,
                equity=20_000.0,
            ),
        }
    )


def _checkpoint(**overrides: object):
    values: dict[str, object] = {
        "contract": _contract(),
        "stage": "replay",
        "generation": 1,
        "manifest_hash": "manifest-1",
        "state": _state(),
        "last_completed_unit": "replay:2026-02-04",
        "output_keys": ("rank:2026-02-04", "candidate-2:510001"),
    }
    values.update(overrides)
    return build_replay_checkpoint(**values)  # type: ignore[arg-type]


def test_checkpoint_roundtrip_contains_full_state_generation_and_manifest(tmp_path) -> None:
    checkpoint = _checkpoint()
    path = tmp_path / "replay-checkpoint.json"

    write_replay_checkpoint_atomic(path, checkpoint)
    loaded = load_replay_checkpoint(path, expected_contract=_contract())

    assert loaded == checkpoint
    assert loaded.atomic_complete is True
    assert loaded.generation == 1
    assert loaded.manifest_hash == "manifest-1"
    assert loaded.state == _state()
    assert loaded.state.candidate_states["candidate-2"].positions[
        "510001"
    ].action_decisions == {"action-1": ("cycle-1", 0.5)}
    assert checkpoint_size_bytes(checkpoint) == path.stat().st_size


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contract_hash", "contract-b"),
        ("code_hash", "code-b"),
        ("schema_hash", "schema-b"),
        ("candidate_config_hash", "candidate-config-b"),
        ("frozen_parameter_hash", "frozen-parameters-b"),
        ("policy_input_hash", "policy-input-b"),
        ("warmup_boundary", date(2025, 12, 1)),
        ("candidate_ids", ("candidate-2",)),
    ],
)
def test_checkpoint_rejects_incompatible_resume(field: str, value: object, tmp_path) -> None:
    path = tmp_path / "replay-checkpoint.json"
    write_replay_checkpoint_atomic(path, _checkpoint())

    with pytest.raises(IncompatibleCheckpointError):
        load_replay_checkpoint(path, expected_contract=_contract(**{field: value}))


def test_revised_data_at_same_cutoff_requires_a_new_run_identity(tmp_path) -> None:
    path = tmp_path / "replay-checkpoint.json"
    write_replay_checkpoint_atomic(path, _checkpoint())

    with pytest.raises(NewRunIdentityRequiredError, match="new run identity"):
        load_replay_checkpoint(
            path,
            expected_contract=_contract(input_snapshot_hash="input-revised"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("top_n", 4),
        ("initial_cash", 200_000.0),
        ("fee_rate", 0.002),
        ("lot_size", 100),
    ],
)
def test_candidate_config_hash_binds_every_execution_parameter(
    field: str,
    value: object,
) -> None:
    changed = ReplayCandidateConfig(
        candidate_id="candidate-2",
        top_n=int(value) if field == "top_n" else 2,
        initial_cash=float(value) if field == "initial_cash" else 100_000.0,
        fee_rate=float(value) if field == "fee_rate" else 0.001,
        lot_size=int(value) if field == "lot_size" else 1,
    )

    assert canonical_candidate_config_hash(
        (changed, BASE_CANDIDATES[1]),
        frozen_parameter_hash=FROZEN_PARAMETER_HASH,
    ) != _contract().candidate_config_hash


def test_incomplete_checkpoint_is_never_resumable(tmp_path) -> None:
    path = tmp_path / "replay-checkpoint.json"
    path.write_text('{"atomic_complete":false}', encoding="utf-8")

    with pytest.raises(IncompleteCheckpointError):
        load_replay_checkpoint(path, expected_contract=_contract())
