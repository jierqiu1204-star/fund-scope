from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.services.tracked_positions.lifecycle import stable_contract_json

from .artifact_store import (
    ReplayArtifactStore,
    feature_artifact_identity,
    replay_artifact_identity,
)
from .checkpoint import ReplayRunContract, build_replay_checkpoint
from .contracts import MAX_REPLAY_CANDIDATES
from .features import (
    BoundedWorkLimitError,
    FeatureBatchRequest,
    compute_feature_batch,
    merge_feature_rows,
)
from .replay import (
    PointInTimeUniverseDay,
    ReplayBatchRequest,
    ReplayCandidateConfig,
    ReplayPolicyOutput,
    build_completion_manifest,
    canonical_candidate_config_hash,
    run_replay_batch,
)

MAX_REQUEST_BYTES = 64 * 1024
MAX_JSONL_LINE_BYTES = 16 * 1024


class ContinuationStage(StrEnum):
    FEATURE = "feature"
    REPLAY = "replay"


@dataclass(frozen=True)
class ContinuationProgress:
    stage: ContinuationStage
    batch_invocations: int
    processed_items: int
    complete: bool
    last_completed_unit: str
    next_cursor: str | None
    checkpoint_hash: str
    generation: int


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _remaining_seconds(deadline: float, label: str) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise BoundedWorkLimitError(f"{label} exceeded max_seconds")
    return min(remaining, 55.0)


def _contract_from_config(value: object) -> ReplayRunContract:
    config = _mapping(value, "contract")
    return ReplayRunContract(
        run_id=str(config["run_id"]),
        contract_hash=str(config["contract_hash"]),
        input_snapshot_hash=str(config["input_snapshot_hash"]),
        code_hash=str(config["code_hash"]),
        schema_hash=str(config["schema_hash"]),
        candidate_config_hash=str(config["candidate_config_hash"]),
        frozen_parameter_hash=str(config["frozen_parameter_hash"]),
        policy_input_hash=str(config["policy_input_hash"]),
        data_cutoff=date.fromisoformat(str(config["data_cutoff"])),
        warmup_boundary=date.fromisoformat(str(config["warmup_boundary"])),
        candidate_ids=tuple(str(item) for item in config["candidate_ids"]),
    )


def _feature_request_from_config(value: object) -> FeatureBatchRequest:
    config = _mapping(value, "feature request")
    after = config.get("after_key")
    after_key = None
    if after is not None:
        if not isinstance(after, Sequence) or isinstance(after, (str, bytes)) or len(after) != 2:
            raise ValueError("feature after_key must contain code and date")
        after_key = (str(after[0]), date.fromisoformat(str(after[1])))
    return FeatureBatchRequest(
        run_id=str(config["run_id"]),
        feature_contract_hash=str(config["feature_contract_hash"]),
        input_snapshot_hash=str(config["input_snapshot_hash"]),
        asset_codes=tuple(str(item) for item in config["asset_codes"]),
        start_date=date.fromisoformat(str(config["start_date"])),
        end_date=date.fromisoformat(str(config["end_date"])),
        data_cutoff=datetime.fromisoformat(str(config["data_cutoff"])),
        trading_sessions=tuple(
            date.fromisoformat(str(item)) for item in config["trading_sessions"]
        ),
        decision_cutoffs=tuple(
            (
                date.fromisoformat(str(item[0])),
                datetime.fromisoformat(str(item[1])),
            )
            for item in config["decision_cutoffs"]
        ),
        warmup_sessions=int(config["warmup_sessions"]),
        max_source_rows=int(config["max_source_rows"]),
        max_items=int(config["max_items"]),
        max_seconds=float(config.get("max_seconds", 55.0)),
        worker_count=int(config.get("worker_count", 1)),
        after_key=after_key,
    )


def _universe_from_config(value: object) -> PointInTimeUniverseDay:
    config = _mapping(value, "universe day")
    return PointInTimeUniverseDay(
        session_date=date.fromisoformat(str(config["session_date"])),
        eligible_asset_codes=tuple(
            str(item) for item in config["eligible_asset_codes"]
        ),
        expected_universe_count=int(config["expected_universe_count"]),
        canonical_membership_hash=str(config["canonical_membership_hash"]),
        snapshot_hash=str(config["snapshot_hash"]),
    )


def _candidate_from_config(value: object) -> ReplayCandidateConfig:
    config = _mapping(value, "candidate")
    return ReplayCandidateConfig(
        candidate_id=str(config["candidate_id"]),
        top_n=int(config["top_n"]),
        initial_cash=float(config.get("initial_cash", 100_000.0)),
        fee_rate=float(config.get("fee_rate", 0.001)),
        lot_size=int(config.get("lot_size", 1)),
    )


def _policy_from_mapping(config: Mapping[str, Any]) -> ReplayPolicyOutput:
    decision_eligible = config["decision_eligible"]
    if not isinstance(decision_eligible, bool):
        raise ValueError("policy decision_eligible must be a JSON boolean")
    return ReplayPolicyOutput(
        candidate_id=str(config["candidate_id"]),
        candidate_config_hash=str(config["candidate_config_hash"]),
        frozen_parameter_hash=str(config["frozen_parameter_hash"]),
        input_snapshot_hash=str(config["input_snapshot_hash"]),
        session_date=date.fromisoformat(str(config["session_date"])),
        asset_code=str(config["asset_code"]),
        rule_id=str(config["rule_id"]),
        alert_episode_id=str(config["alert_episode_id"]),
        action_cycle_id=str(config["action_cycle_id"]),
        action_decision_id=str(config["action_decision_id"]),
        target_remaining_fraction=float(config["target_remaining_fraction"]),
        decision_eligible=decision_eligible,
    )


def _policy_jsonl(
    path: str | Path,
    *,
    max_seconds: float,
) -> Iterable[ReplayPolicyOutput]:
    started = time.monotonic()
    with Path(path).open("rb") as handle:
        line_number = 0
        while True:
            if time.monotonic() - started >= max_seconds:
                raise BoundedWorkLimitError(
                    "policy JSONL import exceeded max_seconds"
                )
            line = handle.readline(MAX_JSONL_LINE_BYTES + 1)
            if not line:
                break
            line_number += 1
            if len(line) > MAX_JSONL_LINE_BYTES:
                raise BoundedWorkLimitError(
                    f"policy JSONL line {line_number} exceeds hard size limit"
                )
            if not line.strip():
                continue
            payload = json.loads(line.decode("utf-8"))
            yield _policy_from_mapping(_mapping(payload, "policy JSONL row"))


def _feature_cursor_from_checkpoint(last_completed_unit: str) -> tuple[str, date]:
    parts = last_completed_unit.split(":")
    if len(parts) != 3 or parts[0] != "feature":
        raise ValueError("feature checkpoint cursor is invalid")
    return parts[1], date.fromisoformat(parts[2])


def _replay_cursor_from_checkpoint(last_completed_unit: str) -> date:
    prefix, separator, value = last_completed_unit.partition(":")
    if prefix != "replay" or not separator:
        raise ValueError("replay checkpoint cursor is invalid")
    return date.fromisoformat(value)


def _run_feature(
    config: Mapping[str, Any],
    *,
    store: ReplayArtifactStore,
    contract: ReplayRunContract,
) -> ContinuationProgress:
    request = _feature_request_from_config(config["request"])
    deadline = time.monotonic() + request.max_seconds
    if (
        request.run_id != contract.run_id
        or request.input_snapshot_hash != contract.input_snapshot_hash
    ):
        raise ValueError("feature request identity does not match run contract")
    current = store.load_checkpoint(
        run_id=contract.run_id,
        stage="feature",
        expected_contract=contract,
        max_seconds=_remaining_seconds(deadline, "feature checkpoint load"),
    )
    if current is None and request.after_key is not None:
        raise ValueError("feature cursor cannot start without a checkpoint")
    if current is not None and request.after_key != _feature_cursor_from_checkpoint(
        current.last_completed_unit
    ):
        raise ValueError("feature request cursor does not match current checkpoint")

    start_index = request.trading_sessions.index(request.start_date)
    if start_index < request.warmup_sessions:
        raise BoundedWorkLimitError(
            "feature request lacks declared trading sessions for warmup"
        )
    source_start = request.trading_sessions[start_index - request.warmup_sessions]
    source_rows = store.read_source_page(
        run_id=request.run_id,
        asset_codes=request.asset_codes,
        start_date=source_start,
        end_date=request.end_date,
        max_rows=request.max_source_rows,
        max_seconds=_remaining_seconds(deadline, "feature source page"),
    )
    compute_budget = _remaining_seconds(deadline, "feature continuation")
    result = compute_feature_batch(
        source_rows=source_rows,
        request=replace(request, max_seconds=compute_budget),
        candidate_ids=contract.candidate_ids,
    )
    raw_universes = config.get("seal_universe_days", [])
    max_manifest_days = int(config["max_manifest_days"])
    if (
        max_manifest_days < 1
        or max_manifest_days > request.max_items
        or not isinstance(raw_universes, Sequence)
        or isinstance(raw_universes, (str, bytes))
    ):
        raise BoundedWorkLimitError("max_manifest_days request is invalid")
    if len(raw_universes) > max_manifest_days:
        raise BoundedWorkLimitError(
            "seal_universe_days exceeds max_manifest_days"
        )
    universes = tuple(
        _universe_from_config(item)
        for item in raw_universes
    )
    manifests = []
    for universe in universes:
        _remaining_seconds(deadline, "feature manifest sealing")
        existing = store.read_feature_day(
            run_id=request.run_id,
            session_date=universe.session_date,
            max_rows=universe.expected_universe_count,
            max_seconds=_remaining_seconds(deadline, "feature manifest day read"),
        )
        current_rows = tuple(
            row for row in result.rows if row.session_date == universe.session_date
        )
        combined = tuple(
            row
            for row in merge_feature_rows(existing, current_rows)
            if row.session_date == universe.session_date
        )
        manifests.append(
            build_completion_manifest(
                run_id=request.run_id,
                universe=universe,
                feature_rows=combined,
            )
        )
        _remaining_seconds(deadline, "feature manifest sealing")
    if not result.rows:
        raise BoundedWorkLimitError("feature continuation made no bounded progress")
    last_cursor = result.rows[-1].cursor_key
    generation = 1 if current is None else current.generation + 1
    manifest_hash, output_keys = feature_artifact_identity(result.rows, manifests)
    checkpoint = build_replay_checkpoint(
        contract=contract,
        stage="feature",
        generation=generation,
        manifest_hash=manifest_hash,
        state=None,
        last_completed_unit=f"feature:{last_cursor[0]}:{last_cursor[1].isoformat()}",
        output_keys=output_keys,
    )
    store.commit_feature_batch(
        feature_rows=result.rows,
        manifests=manifests,
        checkpoint=checkpoint,
        expected_generation=generation - 1,
        max_feature_rows=request.max_items,
        max_manifests=max_manifest_days,
        max_seconds=_remaining_seconds(deadline, "feature artifact commit"),
    )
    next_cursor = (
        f"{result.next_after_key[0]}|{result.next_after_key[1].isoformat()}"
        if result.next_after_key is not None
        else None
    )
    return ContinuationProgress(
        stage=ContinuationStage.FEATURE,
        batch_invocations=1,
        processed_items=result.processed_items,
        complete=result.complete,
        last_completed_unit=checkpoint.last_completed_unit,
        next_cursor=next_cursor,
        checkpoint_hash=checkpoint.checkpoint_hash,
        generation=generation,
    )


def _run_replay(
    config: Mapping[str, Any],
    *,
    store: ReplayArtifactStore,
    contract: ReplayRunContract,
) -> ContinuationProgress:
    request_config = _mapping(config["request"], "replay request")
    max_seconds = float(request_config.get("max_seconds", 55.0))
    if not math.isfinite(max_seconds) or not 0 < max_seconds <= 55.0:
        raise BoundedWorkLimitError("replay max_seconds must be within (0, 55]")
    deadline = time.monotonic() + max_seconds
    run_id = str(request_config["run_id"])
    if run_id != contract.run_id:
        raise ValueError("replay request identity does not match run contract")
    after = request_config.get("after_date")
    after_date = date.fromisoformat(str(after)) if after is not None else None
    feature_checkpoint = store.load_checkpoint(
        run_id=run_id,
        stage="feature",
        expected_contract=contract,
        max_seconds=_remaining_seconds(deadline, "feature checkpoint verification"),
    )
    if feature_checkpoint is None:
        raise ValueError("replay requires a compatible feature checkpoint")
    current = store.load_checkpoint(
        run_id=run_id,
        stage="replay",
        expected_contract=contract,
        max_seconds=_remaining_seconds(deadline, "replay checkpoint load"),
    )
    if current is None and after_date is not None:
        raise ValueError("replay cursor cannot start without a checkpoint")
    if current is not None and after_date != _replay_cursor_from_checkpoint(
        current.last_completed_unit
    ):
        raise ValueError("replay request cursor does not match current checkpoint")
    max_dates = int(request_config["max_dates"])
    max_feature_rows = int(request_config["max_feature_rows"])
    page = store.load_replay_page(
        run_id=run_id,
        start_date=date.fromisoformat(str(request_config["start_date"])),
        end_date=date.fromisoformat(str(request_config["end_date"])),
        after_date=after_date,
        max_dates=max_dates,
        max_feature_rows=max_feature_rows,
        max_seconds=_remaining_seconds(deadline, "replay page load"),
    )
    _remaining_seconds(deadline, "replay page load")
    if not page.manifests:
        raise BoundedWorkLimitError("replay continuation found no sealed manifest page")
    raw_candidates = config["candidates"]
    if (
        not isinstance(raw_candidates, Sequence)
        or isinstance(raw_candidates, (str, bytes))
        or not raw_candidates
        or len(raw_candidates) > MAX_REPLAY_CANDIDATES
    ):
        raise BoundedWorkLimitError(
            f"candidates must contain 1..{MAX_REPLAY_CANDIDATES} rows"
        )
    candidates = tuple(_candidate_from_config(item) for item in raw_candidates)
    if canonical_candidate_config_hash(
        candidates,
        frozen_parameter_hash=contract.frozen_parameter_hash,
    ) != contract.candidate_config_hash:
        raise ValueError("candidate config does not match immutable run contract")
    policy_path = config.get("policy_jsonl")
    if current is not None and policy_path is not None:
        raise ValueError("policy input is immutable after replay starts")
    if current is None:
        import_limit = int(config.get("policy_import_max_rows", 1))
        imported: Iterable[ReplayPolicyOutput] = (
            _policy_jsonl(
                str(policy_path),
                max_seconds=_remaining_seconds(deadline, "policy JSONL import"),
            )
            if policy_path is not None
            else ()
        )
        store.write_policy_outputs(
            run_id=run_id,
            outputs=imported,
            expected_policy_input_hash=contract.policy_input_hash,
            candidate_config_hash=contract.candidate_config_hash,
            frozen_parameter_hash=contract.frozen_parameter_hash,
            input_snapshot_hash=contract.input_snapshot_hash,
            max_rows=import_limit,
            max_seconds=_remaining_seconds(deadline, "policy artifact import"),
        )
        _remaining_seconds(deadline, "policy artifact import")
    policies = store.read_policy_page(
        run_id=run_id,
        session_dates=tuple(item.session_date for item in page.manifests),
        expected_policy_input_hash=contract.policy_input_hash,
        candidate_config_hash=contract.candidate_config_hash,
        frozen_parameter_hash=contract.frozen_parameter_hash,
        input_snapshot_hash=contract.input_snapshot_hash,
        max_rows=int(request_config["max_policy_outputs"]),
        max_seconds=_remaining_seconds(deadline, "policy page read"),
    )
    replay_budget = _remaining_seconds(deadline, "replay computation")
    result = run_replay_batch(
        feature_rows=page.feature_rows,
        manifests=page.manifests,
        candidates=candidates,
        policy_outputs=policies,
        request=ReplayBatchRequest(
            run_id=run_id,
            frozen_parameter_hash=contract.frozen_parameter_hash,
            start_date=date.fromisoformat(str(request_config["start_date"])),
            end_date=date.fromisoformat(str(request_config["end_date"])),
            max_dates=max_dates,
            max_feature_rows=max_feature_rows,
            max_policy_outputs=int(request_config["max_policy_outputs"]),
            max_pending_fills=int(request_config["max_pending_fills"]),
            max_events=int(request_config["max_events"]),
            max_seconds=replay_budget,
            worker_count=int(request_config.get("worker_count", 1)),
            after_date=after_date,
            has_more=page.has_more,
        ),
        state=current.state if current is not None else None,
    )
    generation = 1 if current is None else current.generation + 1
    manifest_hash, output_keys = replay_artifact_identity(
        result.rankings,
        result.events,
        result.equity_curve,
    )
    checkpoint = build_replay_checkpoint(
        contract=contract,
        stage="replay",
        generation=generation,
        manifest_hash=manifest_hash,
        state=result.state,
        last_completed_unit=f"replay:{result.last_completed_date.isoformat()}",
        output_keys=output_keys,
    )
    store.commit_replay_batch(
        rankings=result.rankings,
        events=result.events,
        equity_curve=result.equity_curve,
        checkpoint=checkpoint,
        expected_generation=generation - 1,
        max_rankings=max_dates,
        max_events=int(request_config["max_events"]),
        max_equity_rows=max_dates * len(candidates),
        max_seconds=_remaining_seconds(deadline, "replay artifact commit"),
    )
    return ContinuationProgress(
        stage=ContinuationStage.REPLAY,
        batch_invocations=1,
        processed_items=result.processed_dates,
        complete=result.complete,
        last_completed_unit=checkpoint.last_completed_unit,
        next_cursor=(
            result.next_after_date.isoformat()
            if result.next_after_date is not None
            else None
        ),
        checkpoint_hash=checkpoint.checkpoint_hash,
        generation=generation,
    )


def run_continuation_request(
    request: Mapping[str, Any],
    *,
    store: ReplayArtifactStore,
) -> ContinuationProgress:
    stage = ContinuationStage(str(request["stage"]))
    contract = _contract_from_config(request["contract"])
    if stage is ContinuationStage.FEATURE:
        return _run_feature(request, store=store, contract=contract)
    return _run_replay(request, store=store, contract=contract)


def load_continuation_request(
    path: str | Path,
    *,
    max_bytes: int = MAX_REQUEST_BYTES,
) -> Mapping[str, Any]:
    request_path = Path(path)
    if max_bytes < 1:
        raise BoundedWorkLimitError("request JSON exceeds hard size limit")
    with request_path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise BoundedWorkLimitError("request JSON exceeds hard size limit")
    payload = json.loads(raw.decode("utf-8"))
    return _mapping(payload, "continuation request")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run exactly one bounded ETF replay batch")
    parser.add_argument("--request", required=True, help="small JSON config/cursor file")
    parser.add_argument("--store", required=True, help="run-local SQLite artifact path")
    arguments = parser.parse_args(argv)
    request = load_continuation_request(arguments.request)
    progress = run_continuation_request(
        request,
        store=ReplayArtifactStore(arguments.store),
    )
    print(stable_contract_json(progress))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
