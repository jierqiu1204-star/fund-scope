from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from app.services.tracked_positions.lifecycle import stable_contract_hash, stable_contract_json

from .contracts import MAX_REPLAY_CANDIDATES
from .execution import DeferredExecutionSession
from .replay import (
    CandidatePortfolioState,
    PendingFillState,
    ReplayPositionState,
    ReplayState,
)

CHECKPOINT_FORMAT_VERSION = "etf-action-replay-checkpoint-v3"


class IncompatibleCheckpointError(ValueError):
    pass


class NewRunIdentityRequiredError(IncompatibleCheckpointError):
    pass


class IncompleteCheckpointError(IncompatibleCheckpointError):
    pass


@dataclass(frozen=True)
class ReplayRunContract:
    run_id: str
    contract_hash: str
    input_snapshot_hash: str
    code_hash: str
    schema_hash: str
    candidate_config_hash: str
    frozen_parameter_hash: str
    policy_input_hash: str
    data_cutoff: date
    warmup_boundary: date
    candidate_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        text_fields = (
            self.run_id,
            self.contract_hash,
            self.input_snapshot_hash,
            self.code_hash,
            self.schema_hash,
            self.candidate_config_hash,
            self.frozen_parameter_hash,
            self.policy_input_hash,
        )
        if any(not value.strip() for value in text_fields):
            raise ValueError("run and hash fields are required")
        if (
            not self.candidate_ids
            or len(self.candidate_ids) > MAX_REPLAY_CANDIDATES
            or any(not value.strip() for value in self.candidate_ids)
            or len(self.candidate_ids) != len(set(self.candidate_ids))
        ):
            raise ValueError("candidate_ids must be non-empty and unique")


@dataclass(frozen=True)
class ReplayCheckpoint:
    stage: str
    generation: int
    run_id: str
    contract_hash: str
    input_snapshot_hash: str
    code_hash: str
    schema_hash: str
    candidate_config_hash: str
    frozen_parameter_hash: str
    policy_input_hash: str
    data_cutoff: date
    warmup_boundary: date
    candidate_ids: tuple[str, ...]
    manifest_hash: str
    state: ReplayState | None
    last_completed_unit: str
    output_keys: tuple[str, ...]
    atomic_complete: bool
    checkpoint_hash: str
    format_version: str = CHECKPOINT_FORMAT_VERSION


def _position_payload(position: ReplayPositionState) -> dict[str, Any]:
    return {
        "shares": position.shares,
        "exposure_baseline_shares": position.exposure_baseline_shares,
        "high_watermark": position.high_watermark,
        "position_episode_id": position.position_episode_id,
        "exposure_version": position.exposure_version,
        "rule_states": dict(sorted(position.rule_states.items())),
        "alert_episode_ids": dict(sorted(position.alert_episode_ids.items())),
        "action_decisions": {
            key: [value[0], value[1]]
            for key, value in sorted(position.action_decisions.items())
        },
        "cycle_targets": dict(sorted(position.cycle_targets.items())),
        "action_cycle_id": position.action_cycle_id,
        "current_action_decision_id": position.current_action_decision_id,
        "current_target_remaining_fraction": position.current_target_remaining_fraction,
    }


def _pending_payload(item: PendingFillState) -> dict[str, Any]:
    return {
        "order_id": item.order_id,
        "asset_code": item.asset_code,
        "signal_date": item.signal_date.isoformat(),
        "side": item.side,
        "rank": item.rank,
        "target_weight": item.target_weight,
        "target_remaining_fraction": item.target_remaining_fraction,
        "signal_equity": item.signal_equity,
        "reason": item.reason,
        "action_cycle_id": item.action_cycle_id,
        "action_decision_id": item.action_decision_id,
        "deferred_sessions": [
            {"session_date": value.session_date.isoformat(), "reason": value.reason}
            for value in item.deferred_sessions
        ],
    }


def _state_payload(state: ReplayState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "candidate_states": {
            candidate_id: {
                "cash": candidate_state.cash,
                "positions": {
                    code: _position_payload(position)
                    for code, position in sorted(candidate_state.positions.items())
                },
                "pending_fills": [
                    _pending_payload(item) for item in candidate_state.pending_fills
                ],
                "cumulative_fees": candidate_state.cumulative_fees,
                "turnover": candidate_state.turnover,
                "equity": candidate_state.equity,
            }
            for candidate_id, candidate_state in sorted(state.candidate_states.items())
        }
    }


def _checkpoint_payload(
    checkpoint: ReplayCheckpoint,
    *,
    include_hash: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format_version": checkpoint.format_version,
        "stage": checkpoint.stage,
        "generation": checkpoint.generation,
        "run_id": checkpoint.run_id,
        "contract_hash": checkpoint.contract_hash,
        "input_snapshot_hash": checkpoint.input_snapshot_hash,
        "code_hash": checkpoint.code_hash,
        "schema_hash": checkpoint.schema_hash,
        "candidate_config_hash": checkpoint.candidate_config_hash,
        "frozen_parameter_hash": checkpoint.frozen_parameter_hash,
        "policy_input_hash": checkpoint.policy_input_hash,
        "data_cutoff": checkpoint.data_cutoff.isoformat(),
        "warmup_boundary": checkpoint.warmup_boundary.isoformat(),
        "candidate_ids": list(checkpoint.candidate_ids),
        "manifest_hash": checkpoint.manifest_hash,
        "state": _state_payload(checkpoint.state),
        "last_completed_unit": checkpoint.last_completed_unit,
        "output_keys": list(checkpoint.output_keys),
        "atomic_complete": checkpoint.atomic_complete,
    }
    if include_hash:
        payload["checkpoint_hash"] = checkpoint.checkpoint_hash
    return payload


def build_replay_checkpoint(
    *,
    contract: ReplayRunContract,
    stage: str,
    generation: int,
    manifest_hash: str,
    state: ReplayState | None,
    last_completed_unit: str,
    output_keys: tuple[str, ...] = (),
) -> ReplayCheckpoint:
    if stage not in {"feature", "replay"}:
        raise ValueError("checkpoint stage must be feature or replay")
    if generation < 1:
        raise ValueError("checkpoint generation must be positive")
    if not manifest_hash.strip() or not last_completed_unit.strip():
        raise ValueError("manifest hash and last_completed_unit are required")
    if stage == "replay" and state is None:
        raise ValueError("replay checkpoint requires complete candidate state")
    if state is not None and set(state.candidate_states) != set(contract.candidate_ids):
        raise ValueError("checkpoint state candidate ids do not match contract")
    draft = ReplayCheckpoint(
        stage=stage,
        generation=generation,
        run_id=contract.run_id,
        contract_hash=contract.contract_hash,
        input_snapshot_hash=contract.input_snapshot_hash,
        code_hash=contract.code_hash,
        schema_hash=contract.schema_hash,
        candidate_config_hash=contract.candidate_config_hash,
        frozen_parameter_hash=contract.frozen_parameter_hash,
        policy_input_hash=contract.policy_input_hash,
        data_cutoff=contract.data_cutoff,
        warmup_boundary=contract.warmup_boundary,
        candidate_ids=contract.candidate_ids,
        manifest_hash=manifest_hash,
        state=state,
        last_completed_unit=last_completed_unit,
        output_keys=tuple(sorted(set(output_keys))),
        atomic_complete=True,
        checkpoint_hash="pending",
    )
    return replace(
        draft,
        checkpoint_hash=stable_contract_hash(
            _checkpoint_payload(draft, include_hash=False)
        ),
    )


def checkpoint_to_json(checkpoint: ReplayCheckpoint) -> str:
    return stable_contract_json(_checkpoint_payload(checkpoint, include_hash=True))


def _checkpoint_bytes(checkpoint: ReplayCheckpoint) -> bytes:
    return checkpoint_to_json(checkpoint).encode("utf-8")


def checkpoint_size_bytes(checkpoint: ReplayCheckpoint) -> int:
    return len(_checkpoint_bytes(checkpoint))


def write_replay_checkpoint_atomic(path: str | Path, checkpoint: ReplayCheckpoint) -> None:
    if not checkpoint.atomic_complete:
        raise IncompleteCheckpointError("checkpoint completion marker is false")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_checkpoint_bytes(checkpoint))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _position_from_payload(payload: Mapping[str, Any]) -> ReplayPositionState:
    return ReplayPositionState(
        shares=float(payload["shares"]),
        exposure_baseline_shares=float(payload["exposure_baseline_shares"]),
        high_watermark=float(payload["high_watermark"]),
        position_episode_id=str(payload["position_episode_id"]),
        exposure_version=int(payload["exposure_version"]),
        rule_states={str(key): str(value) for key, value in payload["rule_states"].items()},
        alert_episode_ids={
            str(key): str(value) for key, value in payload["alert_episode_ids"].items()
        },
        action_decisions={
            str(key): (str(value[0]), float(value[1]))
            for key, value in payload["action_decisions"].items()
        },
        cycle_targets={
            str(key): float(value) for key, value in payload["cycle_targets"].items()
        },
        action_cycle_id=(
            str(payload["action_cycle_id"])
            if payload.get("action_cycle_id") is not None
            else None
        ),
        current_action_decision_id=(
            str(payload["current_action_decision_id"])
            if payload.get("current_action_decision_id") is not None
            else None
        ),
        current_target_remaining_fraction=(
            float(payload["current_target_remaining_fraction"])
            if payload.get("current_target_remaining_fraction") is not None
            else None
        ),
    )


def _pending_from_payload(payload: Mapping[str, Any]) -> PendingFillState:
    return PendingFillState(
        order_id=str(payload["order_id"]),
        asset_code=str(payload["asset_code"]),
        signal_date=date.fromisoformat(str(payload["signal_date"])),
        side=str(payload["side"]),
        rank=int(payload["rank"]),
        target_weight=(
            float(payload["target_weight"])
            if payload.get("target_weight") is not None
            else None
        ),
        target_remaining_fraction=(
            float(payload["target_remaining_fraction"])
            if payload.get("target_remaining_fraction") is not None
            else None
        ),
        signal_equity=(
            float(payload["signal_equity"])
            if payload.get("signal_equity") is not None
            else None
        ),
        reason=str(payload["reason"]),
        action_cycle_id=(
            str(payload["action_cycle_id"])
            if payload.get("action_cycle_id") is not None
            else None
        ),
        action_decision_id=(
            str(payload["action_decision_id"])
            if payload.get("action_decision_id") is not None
            else None
        ),
        deferred_sessions=tuple(
            DeferredExecutionSession(
                session_date=date.fromisoformat(str(item["session_date"])),
                reason=str(item["reason"]),
            )
            for item in payload.get("deferred_sessions", [])
        ),
    )


def _state_from_payload(payload: Mapping[str, Any] | None) -> ReplayState | None:
    if payload is None:
        return None
    raw_states = payload.get("candidate_states")
    if not isinstance(raw_states, Mapping):
        raise IncompleteCheckpointError("checkpoint candidate state is missing")
    states: dict[str, CandidatePortfolioState] = {}
    for candidate_id, raw_state in raw_states.items():
        if not isinstance(raw_state, Mapping):
            raise IncompleteCheckpointError("invalid candidate state")
        states[str(candidate_id)] = CandidatePortfolioState(
            cash=float(raw_state["cash"]),
            positions={
                str(code): _position_from_payload(position)
                for code, position in raw_state["positions"].items()
            },
            pending_fills=tuple(
                _pending_from_payload(item) for item in raw_state["pending_fills"]
            ),
            cumulative_fees=float(raw_state["cumulative_fees"]),
            turnover=float(raw_state["turnover"]),
            equity=float(raw_state["equity"]),
        )
    return ReplayState(candidate_states=states)


def _assert_compatible(
    checkpoint: ReplayCheckpoint,
    expected: ReplayRunContract,
) -> None:
    if (
        checkpoint.run_id == expected.run_id
        and checkpoint.data_cutoff == expected.data_cutoff
        and checkpoint.input_snapshot_hash != expected.input_snapshot_hash
    ):
        raise NewRunIdentityRequiredError(
            "input snapshot changed at the same cutoff; a new run identity is required"
        )
    actual = (
        checkpoint.run_id,
        checkpoint.contract_hash,
        checkpoint.input_snapshot_hash,
        checkpoint.code_hash,
        checkpoint.schema_hash,
        checkpoint.candidate_config_hash,
        checkpoint.frozen_parameter_hash,
        checkpoint.policy_input_hash,
        checkpoint.data_cutoff,
        checkpoint.warmup_boundary,
        checkpoint.candidate_ids,
    )
    wanted = (
        expected.run_id,
        expected.contract_hash,
        expected.input_snapshot_hash,
        expected.code_hash,
        expected.schema_hash,
        expected.candidate_config_hash,
        expected.frozen_parameter_hash,
        expected.policy_input_hash,
        expected.data_cutoff,
        expected.warmup_boundary,
        expected.candidate_ids,
    )
    if actual != wanted:
        raise IncompatibleCheckpointError("checkpoint contract is incompatible with resume")


def checkpoint_from_json(
    raw_json: str,
    *,
    expected_contract: ReplayRunContract | None = None,
) -> ReplayCheckpoint:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise IncompleteCheckpointError("checkpoint is unreadable or incomplete") from exc
    if not isinstance(payload, dict) or payload.get("atomic_complete") is not True:
        raise IncompleteCheckpointError("checkpoint completion marker is missing")
    try:
        checkpoint = ReplayCheckpoint(
            stage=str(payload["stage"]),
            generation=int(payload["generation"]),
            run_id=str(payload["run_id"]),
            contract_hash=str(payload["contract_hash"]),
            input_snapshot_hash=str(payload["input_snapshot_hash"]),
            code_hash=str(payload["code_hash"]),
            schema_hash=str(payload["schema_hash"]),
            candidate_config_hash=str(payload["candidate_config_hash"]),
            frozen_parameter_hash=str(payload["frozen_parameter_hash"]),
            policy_input_hash=str(payload["policy_input_hash"]),
            data_cutoff=date.fromisoformat(str(payload["data_cutoff"])),
            warmup_boundary=date.fromisoformat(str(payload["warmup_boundary"])),
            candidate_ids=tuple(str(item) for item in payload["candidate_ids"]),
            manifest_hash=str(payload["manifest_hash"]),
            state=_state_from_payload(payload.get("state")),
            last_completed_unit=str(payload["last_completed_unit"]),
            output_keys=tuple(str(item) for item in payload["output_keys"]),
            atomic_complete=True,
            checkpoint_hash=str(payload["checkpoint_hash"]),
            format_version=str(payload["format_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IncompleteCheckpointError("checkpoint fields are incomplete") from exc
    if checkpoint.format_version != CHECKPOINT_FORMAT_VERSION:
        raise IncompatibleCheckpointError("checkpoint format version is incompatible")
    if checkpoint.stage not in {"feature", "replay"} or checkpoint.generation < 1:
        raise IncompatibleCheckpointError("checkpoint stage or generation is invalid")
    expected_hash = stable_contract_hash(
        _checkpoint_payload(checkpoint, include_hash=False)
    )
    if checkpoint.checkpoint_hash != expected_hash:
        raise IncompatibleCheckpointError("checkpoint hash mismatch")
    if expected_contract is not None:
        _assert_compatible(checkpoint, expected_contract)
    return checkpoint


def load_replay_checkpoint(
    path: str | Path,
    *,
    expected_contract: ReplayRunContract,
) -> ReplayCheckpoint:
    try:
        raw_json = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise IncompleteCheckpointError("checkpoint is unreadable or incomplete") from exc
    return checkpoint_from_json(raw_json, expected_contract=expected_contract)
