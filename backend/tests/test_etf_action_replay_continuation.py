from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
from app.services.strategy_lab.etf_action_replay.checkpoint import (
    NewRunIdentityRequiredError,
)
from app.services.strategy_lab.etf_action_replay.continuation import (
    MAX_JSONL_LINE_BYTES,
    ContinuationStage,
    _contract_from_config,
    _policy_from_mapping,
    _policy_jsonl,
    load_continuation_request,
    run_continuation_request,
)
from app.services.strategy_lab.etf_action_replay.features import (
    AdjustedDailyInput,
    BoundedWorkLimitError,
)
from app.services.strategy_lab.etf_action_replay.replay import (
    ReplayCandidateConfig,
    ReplayPolicyOutput,
    canonical_candidate_config_hash,
    canonical_membership_hash,
    canonical_policy_input_hash,
)
from app.services.tracked_positions.lifecycle import stable_contract_json

FROZEN_PARAMETER_HASH = "frozen-parameters-a"
CANDIDATE = ReplayCandidateConfig(
    candidate_id="candidate-2",
    top_n=1,
    initial_cash=10_000.0,
    fee_rate=0.001,
    lot_size=1,
)
CANDIDATE_CONFIG_HASH = canonical_candidate_config_hash(
    (CANDIDATE,),
    frozen_parameter_hash=FROZEN_PARAMETER_HASH,
)


def _source(day: int) -> AdjustedDailyInput:
    available_at = datetime(2026, 4, day, 15, 0, tzinfo=UTC)
    return AdjustedDailyInput(
        asset_code="510001",
        session_date=date(2026, 4, day),
        raw_open=10.0 + day,
        raw_high=10.5 + day,
        raw_low=9.5 + day,
        raw_close=10.0 + day,
        volume=1_000_000.0,
        adjustment_factor=1.0,
        adjusted_data_source="eastmoney_total_return",
        adjustment_kind="total_return_adjusted",
        decision_eligible=True,
        provider_healthy=True,
        fresh_at_cutoff=True,
        known_at=available_at,
        source_cutoff=available_at,
    )


def _contract_config(
    *,
    policy_input_hash: str | None = None,
    data_cutoff: str = "2026-04-03",
) -> dict[str, object]:
    return {
        "run_id": "run-cli",
        "contract_hash": "contract-a",
        "input_snapshot_hash": "input-a",
        "code_hash": "code-a",
        "schema_hash": "schema-a",
        "candidate_config_hash": CANDIDATE_CONFIG_HASH,
        "frozen_parameter_hash": FROZEN_PARAMETER_HASH,
        "policy_input_hash": policy_input_hash or canonical_policy_input_hash(()),
        "data_cutoff": data_cutoff,
        "warmup_boundary": "2026-04-01",
        "candidate_ids": ["candidate-2"],
    }


def _feature_config() -> dict[str, object]:
    return {
        "stage": "feature",
        "contract": _contract_config(),
        "request": {
            "run_id": "run-cli",
            "feature_contract_hash": "feature-contract-a",
            "input_snapshot_hash": "input-a",
            "asset_codes": ["510001"],
            "start_date": "2026-04-03",
            "end_date": "2026-04-03",
            "data_cutoff": "2026-04-03T15:30:00+00:00",
            "trading_sessions": ["2026-04-01", "2026-04-02", "2026-04-03"],
            "decision_cutoffs": [
                ["2026-04-01", "2026-04-01T15:30:00+00:00"],
                ["2026-04-02", "2026-04-02T15:30:00+00:00"],
                ["2026-04-03", "2026-04-03T15:30:00+00:00"],
            ],
            "warmup_sessions": 2,
            "max_source_rows": 3,
            "max_items": 1,
            "max_seconds": 55.0,
            "worker_count": 1,
        },
        "seal_universe_days": [
            {
                "session_date": "2026-04-03",
                "eligible_asset_codes": ["510001"],
                "expected_universe_count": 1,
                "canonical_membership_hash": canonical_membership_hash(("510001",)),
                "snapshot_hash": "universe-1",
            }
        ],
        "max_manifest_days": 1,
    }


def _replay_config() -> dict[str, object]:
    return {
        "stage": "replay",
        "contract": _contract_config(),
        "request": {
            "run_id": "run-cli",
            "start_date": "2026-04-03",
            "end_date": "2026-04-03",
            "max_dates": 1,
            "max_feature_rows": 1,
            "max_policy_outputs": 1,
            "max_pending_fills": 10,
            "max_events": 100,
            "max_seconds": 55.0,
            "worker_count": 1,
        },
        "candidates": [
            {
                "candidate_id": "candidate-2",
                "top_n": 1,
                "initial_cash": 10_000.0,
                "fee_rate": 0.001,
                "lot_size": 1,
            }
        ],
    }


def _policy_output(day: int, index: int) -> ReplayPolicyOutput:
    return ReplayPolicyOutput(
        candidate_id="candidate-2",
        candidate_config_hash=CANDIDATE_CONFIG_HASH,
        frozen_parameter_hash=FROZEN_PARAMETER_HASH,
        input_snapshot_hash="input-a",
        session_date=date(2026, 4, day),
        asset_code="510001",
        rule_id=f"rule-{index}",
        alert_episode_id=f"alert-{index}",
        action_cycle_id=f"cycle-{index}",
        action_decision_id=f"action-{index}",
        target_remaining_fraction=0.5,
        decision_eligible=True,
    )


def _two_day_feature_config(policy_input_hash: str) -> dict[str, object]:
    sessions = tuple(date(2026, 4, day) for day in range(1, 5))
    return {
        "stage": "feature",
        "contract": _contract_config(
            policy_input_hash=policy_input_hash,
            data_cutoff="2026-04-04",
        ),
        "request": {
            "run_id": "run-cli",
            "feature_contract_hash": "feature-contract-a",
            "input_snapshot_hash": "input-a",
            "asset_codes": ["510001"],
            "start_date": "2026-04-03",
            "end_date": "2026-04-04",
            "data_cutoff": "2026-04-04T15:30:00+00:00",
            "trading_sessions": [item.isoformat() for item in sessions],
            "decision_cutoffs": [
                [item.isoformat(), f"{item.isoformat()}T15:30:00+00:00"]
                for item in sessions
            ],
            "warmup_sessions": 2,
            "max_source_rows": 4,
            "max_items": 2,
            "max_seconds": 55.0,
            "worker_count": 1,
        },
        "seal_universe_days": [
            {
                "session_date": item.isoformat(),
                "eligible_asset_codes": ["510001"],
                "expected_universe_count": 1,
                "canonical_membership_hash": canonical_membership_hash(("510001",)),
                "snapshot_hash": f"universe-{item.isoformat()}",
            }
            for item in sessions[-2:]
        ],
        "max_manifest_days": 2,
    }


def _two_day_replay_config(
    policy_input_hash: str,
    *,
    after_date: str | None = None,
    policy_jsonl: str | None = None,
    policy_import_max_rows: int | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "run_id": "run-cli",
        "start_date": "2026-04-03",
        "end_date": "2026-04-04",
        "max_dates": 1,
        "max_feature_rows": 1,
        "max_policy_outputs": 1,
        "max_pending_fills": 10,
        "max_events": 100,
        "max_seconds": 55.0,
        "worker_count": 1,
    }
    if after_date is not None:
        request["after_date"] = after_date
    config: dict[str, object] = {
        "stage": "replay",
        "contract": _contract_config(
            policy_input_hash=policy_input_hash,
            data_cutoff="2026-04-04",
        ),
        "request": request,
        "candidates": [
            {
                "candidate_id": CANDIDATE.candidate_id,
                "top_n": CANDIDATE.top_n,
                "initial_cash": CANDIDATE.initial_cash,
                "fee_rate": CANDIDATE.fee_rate,
                "lot_size": CANDIDATE.lot_size,
            }
        ],
    }
    if policy_jsonl is not None:
        config["policy_jsonl"] = policy_jsonl
    if policy_import_max_rows is not None:
        config["policy_import_max_rows"] = policy_import_max_rows
    return config


def test_feature_continuation_reads_store_and_commits_exactly_one_batch(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    store.write_source_rows(
        run_id="run-cli",
        rows=(_source(1), _source(2), _source(3)),
        max_rows=3,
    )

    progress = run_continuation_request(_feature_config(), store=store)

    assert progress.stage is ContinuationStage.FEATURE
    assert progress.batch_invocations == 1
    assert progress.processed_items == 1
    assert progress.complete is True
    assert store.artifact_counts("run-cli")["features"] == 1
    assert store.artifact_counts("run-cli")["manifests"] == 1


def test_feature_continuation_uses_one_shared_deadline_across_phases(
    tmp_path,
    monkeypatch,
) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    store.write_source_rows(
        run_id="run-cli",
        rows=(_source(1), _source(2), _source(3)),
        max_rows=3,
    )
    config = _feature_config()
    config["request"]["max_seconds"] = 1.0  # type: ignore[index]
    now = [0.0]
    original_read = store.read_source_page
    original_commit = store.commit_feature_batch
    commit_budget: list[float] = []

    def delayed_read(**kwargs):
        rows = original_read(**kwargs)
        now[0] = 0.75
        return rows

    def capture_commit(**kwargs):
        commit_budget.append(float(kwargs["max_seconds"]))
        return original_commit(**kwargs)

    monkeypatch.setattr(
        "app.services.strategy_lab.etf_action_replay.continuation.time.monotonic",
        lambda: now[0],
    )
    monkeypatch.setattr(store, "read_source_page", delayed_read)
    monkeypatch.setattr(store, "commit_feature_batch", capture_commit)

    run_continuation_request(config, store=store)

    assert commit_budget and 0 < commit_budget[0] <= 0.25


def test_feature_continuation_has_a_predeclared_manifest_day_bound(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    store.write_source_rows(
        run_id="run-cli",
        rows=(_source(1), _source(2), _source(3)),
        max_rows=3,
    )
    config = _feature_config()
    config["max_manifest_days"] = 1
    config["seal_universe_days"] = [
        *config["seal_universe_days"],  # type: ignore[misc]
        *config["seal_universe_days"],  # type: ignore[misc]
    ]

    with pytest.raises(BoundedWorkLimitError, match="seal_universe_days"):
        run_continuation_request(config, store=store)


def test_replay_continuation_reads_one_store_page_and_exits_with_checkpoint(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    store.write_source_rows(
        run_id="run-cli",
        rows=(_source(1), _source(2), _source(3)),
        max_rows=3,
    )
    run_continuation_request(_feature_config(), store=store)

    progress = run_continuation_request(_replay_config(), store=store)

    assert progress.stage is ContinuationStage.REPLAY
    assert progress.batch_invocations == 1
    assert progress.processed_items == 1
    assert progress.complete is True
    assert store.artifact_counts("run-cli")["rankings"] == 1
    assert store.artifact_counts("run-cli")["equity"] == 1
    assert store.artifact_counts("run-cli")["checkpoints"] == 2


def test_replay_continuation_rejects_feature_snapshot_from_another_contract(
    tmp_path,
) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    store.write_source_rows(
        run_id="run-cli",
        rows=(_source(1), _source(2), _source(3)),
        max_rows=3,
    )
    run_continuation_request(_feature_config(), store=store)
    config = _replay_config()
    config["contract"]["input_snapshot_hash"] = "input-b"  # type: ignore[index]

    with pytest.raises(NewRunIdentityRequiredError, match="new run identity"):
        run_continuation_request(config, store=store)


def test_continuation_request_file_has_a_small_hard_size_limit(tmp_path) -> None:
    path = tmp_path / "request.json"
    path.write_text('{"padding":"' + ("x" * 200) + '"}', encoding="utf-8")

    with pytest.raises(BoundedWorkLimitError, match="request JSON"):
        load_continuation_request(path, max_bytes=100)


def test_policy_jsonl_uses_a_byte_bounded_line_reader(monkeypatch) -> None:
    payload = (
        b'{"candidate_id":"candidate-2","session_date":"2026-04-03",'
        b'"candidate_config_hash":"candidate-config-a",'
        b'"frozen_parameter_hash":"frozen-parameters-a",'
        b'"input_snapshot_hash":"input-a",'
        b'"asset_code":"510001","rule_id":"rule-1",'
        b'"alert_episode_id":"alert-1","action_cycle_id":"cycle-1",'
        b'"action_decision_id":"action-1","target_remaining_fraction":0.5,'
        b'"decision_eligible":true}\n'
    )

    class BoundedReader:
        def __init__(self) -> None:
            self.lines = iter((payload, b""))
            self.read_sizes: list[int] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def __iter__(self):
            raise AssertionError("policy JSONL must not use unbounded file iteration")

        def readline(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return next(self.lines)

    reader = BoundedReader()
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: reader)

    rows = tuple(_policy_jsonl("ignored.jsonl", max_seconds=55.0))

    assert len(rows) == 1
    assert reader.read_sizes == [MAX_JSONL_LINE_BYTES + 1] * 2


def test_policy_decision_eligibility_rejects_string_false() -> None:
    with pytest.raises(ValueError, match="JSON boolean"):
        _policy_from_mapping(
            {
                "candidate_id": "candidate-2",
                "candidate_config_hash": CANDIDATE_CONFIG_HASH,
                "frozen_parameter_hash": FROZEN_PARAMETER_HASH,
                "input_snapshot_hash": "input-a",
                "session_date": "2026-04-03",
                "asset_code": "510001",
                "rule_id": "rule-1",
                "alert_episode_id": "alert-1",
                "action_cycle_id": "cycle-1",
                "action_decision_id": "action-1",
                "target_remaining_fraction": 0.5,
                "decision_eligible": "false",
            }
        )


def test_replay_resume_reads_only_current_policy_page_and_rejects_new_jsonl(
    tmp_path,
) -> None:
    policies = tuple(
        [_policy_output(1 + index % 2, index) for index in range(20)]
        + [_policy_output(4, 20)]
    )
    policy_input_hash = canonical_policy_input_hash(policies)
    policy_path = tmp_path / "policy.jsonl"
    policy_path.write_text(
        "\n".join(stable_contract_json(item) for item in policies) + "\n",
        encoding="utf-8",
    )
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    store.write_source_rows(
        run_id="run-cli",
        rows=tuple(_source(day) for day in range(1, 5)),
        max_rows=4,
    )
    run_continuation_request(
        _two_day_feature_config(policy_input_hash),
        store=store,
    )

    first = run_continuation_request(
        _two_day_replay_config(
            policy_input_hash,
            policy_jsonl=str(policy_path),
            policy_import_max_rows=len(policies),
        ),
        store=store,
    )
    assert first.complete is False
    assert first.next_cursor == "2026-04-03"
    assert store.artifact_counts("run-cli")["policies"] == len(policies)

    with pytest.raises(ValueError, match="immutable after replay starts"):
        run_continuation_request(
            _two_day_replay_config(
                policy_input_hash,
                after_date="2026-04-03",
                policy_jsonl=str(policy_path),
                policy_import_max_rows=len(policies),
            ),
            store=store,
        )

    second = run_continuation_request(
        _two_day_replay_config(
            policy_input_hash,
            after_date="2026-04-03",
        ),
        store=store,
    )
    assert second.complete is True
    assert second.generation == 2
    checkpoint = store.load_checkpoint(
        run_id="run-cli",
        stage="replay",
        expected_contract=_contract_from_config(
            _contract_config(
                policy_input_hash=policy_input_hash,
                data_cutoff="2026-04-04",
            )
        ),
    )
    assert checkpoint is not None
    candidate_state = checkpoint.state.candidate_states["candidate-2"]
    assert candidate_state.positions
    assert candidate_state.cumulative_fees > 0
    assert any(
        position.action_decisions
        for position in candidate_state.positions.values()
    )


def test_continuation_rejects_changed_candidate_config_with_same_id(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    store.write_source_rows(
        run_id="run-cli",
        rows=(_source(1), _source(2), _source(3)),
        max_rows=3,
    )
    run_continuation_request(_feature_config(), store=store)
    config = _replay_config()
    config["candidates"][0]["fee_rate"] = 0.002  # type: ignore[index]

    with pytest.raises(ValueError, match="candidate config"):
        run_continuation_request(config, store=store)
