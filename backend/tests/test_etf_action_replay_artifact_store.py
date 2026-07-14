from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from app.services.strategy_lab.etf_action_replay import artifact_store as artifact_store_module
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ArtifactConflictError,
    ReplayArtifactStore,
    StaleCheckpointGenerationError,
    feature_artifact_identity,
    replay_artifact_identity,
)
from app.services.strategy_lab.etf_action_replay.checkpoint import (
    ReplayRunContract,
    build_replay_checkpoint,
)
from app.services.strategy_lab.etf_action_replay.features import (
    AdjustedDailyInput,
    BoundedWorkLimitError,
    FeatureRow,
)
from app.services.strategy_lab.etf_action_replay.replay import (
    CandidatePortfolioState,
    CandidateReplayEvent,
    DailyRankingEvent,
    EquityCurvePoint,
    PointInTimeUniverseDay,
    ReplayPolicyOutput,
    ReplayState,
    build_completion_manifest,
    canonical_membership_hash,
    canonical_policy_input_hash,
)
from app.services.tracked_positions.lifecycle import stable_contract_json


def _contract() -> ReplayRunContract:
    return ReplayRunContract(
        run_id="run-store",
        contract_hash="contract-a",
        input_snapshot_hash="input-a",
        code_hash="code-a",
        schema_hash="schema-a",
        candidate_config_hash="candidate-config-a",
        frozen_parameter_hash="frozen-parameters-a",
        policy_input_hash="policy-input-a",
        data_cutoff=date(2026, 3, 5),
        warmup_boundary=date(2026, 1, 1),
        candidate_ids=("candidate-2",),
    )


def _state(cash: float = 10_000.0) -> ReplayState:
    return ReplayState(
        candidate_states={
            "candidate-2": CandidatePortfolioState(cash=cash, equity=cash)
        }
    )


def _checkpoint(generation: int, *, cash: float = 10_000.0):
    ranking, event, equity = _outputs()
    manifest_hash, output_keys = replay_artifact_identity(
        (ranking,),
        (event,),
        (equity,),
    )
    return build_replay_checkpoint(
        contract=_contract(),
        stage="replay",
        generation=generation,
        manifest_hash=manifest_hash,
        state=_state(cash),
        last_completed_unit="replay:2026-03-05",
        output_keys=output_keys,
    )


def _outputs():
    ranking = DailyRankingEvent(
        run_id="run-store",
        session_date=date(2026, 3, 5),
        ordered_asset_codes=("510001",),
        cross_section_hash="cross-1",
        manifest_hash="manifest-1",
    )
    event = CandidateReplayEvent(
        event_key="event-1",
        run_id="run-store",
        candidate_id="candidate-2",
        candidate_config_hash="candidate-config-a",
        session_date=date(2026, 3, 5),
        event_type="buy_filled",
        asset_code="510001",
        side="buy",
        quantity=100.0,
        price=10.0,
        fee=1.0,
        cash_after=8_999.0,
    )
    equity = EquityCurvePoint(
        run_id="run-store",
        candidate_id="candidate-2",
        candidate_config_hash="candidate-config-a",
        session_date=date(2026, 3, 5),
        equity=9_999.0,
        cash=8_999.0,
        market_value=1_000.0,
        cumulative_fees=1.0,
        turnover=1_000.0,
    )
    return ranking, event, equity


def _source(day: int) -> AdjustedDailyInput:
    available_at = datetime(2026, 3, day, 15, 0, tzinfo=UTC)
    return AdjustedDailyInput(
        asset_code="510001",
        session_date=date(2026, 3, day),
        raw_open=10.0,
        raw_high=10.5,
        raw_low=9.5,
        raw_close=10.0,
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


def _policy(day: int) -> ReplayPolicyOutput:
    return ReplayPolicyOutput(
        candidate_id="candidate-2",
        candidate_config_hash="candidate-config-a",
        frozen_parameter_hash="frozen-parameters-a",
        input_snapshot_hash="input-a",
        session_date=date(2026, 3, day),
        asset_code="510001",
        rule_id="trend",
        alert_episode_id=f"alert-{day}",
        action_cycle_id=f"cycle-{day}",
        action_decision_id=f"action-{day}",
        target_remaining_fraction=0.5,
        decision_eligible=True,
    )


def _feature() -> FeatureRow:
    source = _source(5)
    return FeatureRow(
        run_id="run-store",
        feature_contract_hash="feature-contract-a",
        input_snapshot_hash="input-a",
        asset_code=source.asset_code,
        session_date=source.session_date,
        raw_open=source.raw_open,
        raw_high=source.raw_high,
        raw_low=source.raw_low,
        raw_close=source.raw_close,
        volume=source.volume,
        adjusted_open=10.0,
        adjusted_high=10.5,
        adjusted_low=9.5,
        adjusted_close=10.0,
        momentum_return=0.1,
        score=0.1,
        warmup_sessions=2,
        warmup_boundary=date(2026, 3, 3),
        warmup_row_hashes=("row-3", "row-4", "row-5"),
        warmup_provenance_hash="provenance-1",
        adjustment_factor=1.0,
        adjusted_data_source=source.adjusted_data_source,
        adjustment_kind=source.adjustment_kind,
        decision_eligible=True,
        provider_healthy=True,
        fresh_at_cutoff=True,
        known_at=source.known_at,
        source_cutoff=source.source_cutoff,
        input_row_hash="row-5",
    )


def test_replay_outputs_and_checkpoint_commit_in_one_transaction(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    ranking, event, equity = _outputs()

    store.commit_replay_batch(
        rankings=(ranking,),
        events=(event,),
        equity_curve=(equity,),
        checkpoint=_checkpoint(1),
        expected_generation=0,
        max_rankings=1,
        max_events=1,
        max_equity_rows=1,
    )

    assert store.artifact_counts("run-store") == {
        "policies": 0,
        "features": 0,
        "manifests": 0,
        "rankings": 1,
        "events": 1,
        "equity": 1,
        "checkpoints": 1,
    }
    loaded = store.load_checkpoint(
        run_id="run-store",
        stage="replay",
        expected_contract=_contract(),
    )
    assert loaded is not None
    assert loaded.generation == 1
    assert loaded.state == _state()


def test_older_checkpoint_generation_cannot_overwrite_newer_state(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    ranking, event, equity = _outputs()
    store.commit_replay_batch(
        rankings=(ranking,),
        events=(event,),
        equity_curve=(equity,),
        checkpoint=_checkpoint(1),
        expected_generation=0,
        max_rankings=1,
        max_events=1,
        max_equity_rows=1,
    )

    with pytest.raises(StaleCheckpointGenerationError, match="expected 0, found 1"):
        store.commit_replay_batch(
            rankings=(ranking,),
            events=(event,),
            equity_curve=(equity,),
            checkpoint=_checkpoint(1, cash=1.0),
            expected_generation=0,
            max_rankings=1,
            max_events=1,
            max_equity_rows=1,
        )

    loaded = store.load_checkpoint(
        run_id="run-store",
        stage="replay",
        expected_contract=_contract(),
    )
    assert loaded is not None
    assert loaded.state == _state()


def test_sql_failure_rolls_back_outputs_and_checkpoint_together(tmp_path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    store = ReplayArtifactStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_equity BEFORE INSERT ON replay_equity
            BEGIN
                SELECT RAISE(ABORT, 'synthetic crash');
            END
            """
        )
    ranking, event, equity = _outputs()

    with pytest.raises(sqlite3.IntegrityError, match="synthetic crash"):
        store.commit_replay_batch(
            rankings=(ranking,),
            events=(event,),
            equity_curve=(equity,),
            checkpoint=_checkpoint(1),
            expected_generation=0,
            max_rankings=1,
            max_events=1,
            max_equity_rows=1,
        )

    assert store.artifact_counts("run-store") == {
        "policies": 0,
        "features": 0,
        "manifests": 0,
        "rankings": 0,
        "events": 0,
        "equity": 0,
        "checkpoints": 0,
    }


def test_source_store_reads_a_bounded_sql_page_without_loading_the_rest(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    store.write_source_rows(
        run_id="run-store",
        rows=(_source(3), _source(4), _source(5)),
        max_rows=3,
    )

    with pytest.raises(BoundedWorkLimitError, match="source SQL page"):
        store.read_source_page(
            run_id="run-store",
            asset_codes=("510001",),
            start_date=date(2026, 3, 3),
            end_date=date(2026, 3, 5),
            max_rows=2,
        )

    assert store.read_source_page(
        run_id="run-store",
        asset_codes=("510001",),
        start_date=date(2026, 3, 3),
        end_date=date(2026, 3, 5),
        max_rows=3,
    ) == (_source(3), _source(4), _source(5))


def test_feature_manifest_and_checkpoint_commit_then_load_as_one_replay_page(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    feature = _feature()
    universe = PointInTimeUniverseDay(
        session_date=feature.session_date,
        eligible_asset_codes=(feature.asset_code,),
        expected_universe_count=1,
        canonical_membership_hash=canonical_membership_hash((feature.asset_code,)),
        snapshot_hash="universe-1",
    )
    manifest = build_completion_manifest(
        run_id="run-store",
        universe=universe,
        feature_rows=(feature,),
    )
    manifest_hash, output_keys = feature_artifact_identity(
        (feature,),
        (manifest,),
    )
    checkpoint = build_replay_checkpoint(
        contract=_contract(),
        stage="feature",
        generation=1,
        manifest_hash=manifest_hash,
        state=None,
        last_completed_unit="feature:510001:2026-03-05",
        output_keys=output_keys,
    )

    store.commit_feature_batch(
        feature_rows=(feature,),
        manifests=(manifest,),
        checkpoint=checkpoint,
        expected_generation=0,
        max_feature_rows=1,
        max_manifests=1,
    )
    page = store.load_replay_page(
        run_id="run-store",
        start_date=date(2026, 3, 5),
        end_date=date(2026, 3, 5),
        after_date=None,
        max_dates=1,
        max_feature_rows=1,
    )

    assert page.manifests == (manifest,)
    assert page.feature_rows == (feature,)
    assert page.has_more is False
    assert store.artifact_counts("run-store")["features"] == 1
    assert store.artifact_counts("run-store")["manifests"] == 1


def test_replay_commit_stops_external_generator_at_limit_plus_one(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    ranking, event, equity = _outputs()
    consumed = 0

    def events():
        nonlocal consumed
        for _ in range(10):
            consumed += 1
            yield event

    with pytest.raises(BoundedWorkLimitError, match="event commit"):
        store.commit_replay_batch(
            rankings=(ranking,),
            events=events(),
            equity_curve=(equity,),
            checkpoint=_checkpoint(1),
            expected_generation=0,
            max_rankings=1,
            max_events=1,
            max_equity_rows=1,
        )

    assert consumed == 2
    assert store.artifact_counts("run-store")["checkpoints"] == 0


def test_replay_commit_applies_one_deadline_across_all_output_iterables(
    tmp_path,
    monkeypatch,
) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    ranking, event, equity = _outputs()
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(artifact_store_module.time, "monotonic", lambda: next(clock))

    with pytest.raises(BoundedWorkLimitError, match="event commit.*max_seconds"):
        store.commit_replay_batch(
            rankings=(ranking,),
            events=(event,),
            equity_curve=(equity,),
            checkpoint=_checkpoint(1),
            expected_generation=0,
            max_rankings=1,
            max_events=1,
            max_equity_rows=1,
            max_seconds=1.0,
        )

    assert store.artifact_counts("run-store")["checkpoints"] == 0


def test_feature_commit_stops_external_generator_at_limit_plus_one(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    feature = _feature()
    consumed = 0

    def features():
        nonlocal consumed
        for _ in range(10):
            consumed += 1
            yield feature

    checkpoint = build_replay_checkpoint(
        contract=_contract(),
        stage="feature",
        generation=1,
        manifest_hash="not-reached",
        state=None,
        last_completed_unit="feature:510001:2026-03-05",
    )
    with pytest.raises(BoundedWorkLimitError, match="feature commit"):
        store.commit_feature_batch(
            feature_rows=features(),
            manifests=(),
            checkpoint=checkpoint,
            expected_generation=0,
            max_feature_rows=1,
            max_manifests=1,
        )

    assert consumed == 2


def test_forged_checkpoint_cannot_describe_different_replay_outputs(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    ranking, event, equity = _outputs()
    forged = replace(_checkpoint(1), output_keys=("forged-output",))

    with pytest.raises(ArtifactConflictError, match="does not describe"):
        store.commit_replay_batch(
            rankings=(ranking,),
            events=(event,),
            equity_curve=(equity,),
            checkpoint=forged,
            expected_generation=0,
            max_rankings=1,
            max_events=1,
            max_equity_rows=1,
        )

    assert store.artifact_counts("run-store")["checkpoints"] == 0


def test_checkpoint_identity_binds_content_not_only_stable_output_keys(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    ranking, event, equity = _outputs()
    changed_event = replace(event, quantity=999.0, fee=99.0)

    with pytest.raises(ArtifactConflictError, match="does not describe"):
        store.commit_replay_batch(
            rankings=(ranking,),
            events=(changed_event,),
            equity_curve=(equity,),
            checkpoint=_checkpoint(1),
            expected_generation=0,
            max_rankings=1,
            max_events=1,
            max_equity_rows=1,
        )

    assert store.artifact_counts("run-store")["events"] == 0


def test_feature_commit_rejects_content_from_another_input_snapshot(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    feature = replace(_feature(), input_snapshot_hash="input-b")
    universe = PointInTimeUniverseDay(
        session_date=feature.session_date,
        eligible_asset_codes=(feature.asset_code,),
        expected_universe_count=1,
        canonical_membership_hash=canonical_membership_hash((feature.asset_code,)),
        snapshot_hash="universe-b",
    )
    manifest = build_completion_manifest(
        run_id="run-store",
        universe=universe,
        feature_rows=(feature,),
    )
    manifest_hash, output_keys = feature_artifact_identity((feature,), (manifest,))
    checkpoint = build_replay_checkpoint(
        contract=_contract(),
        stage="feature",
        generation=1,
        manifest_hash=manifest_hash,
        state=None,
        last_completed_unit="feature:510001:2026-03-05",
        output_keys=output_keys,
    )

    with pytest.raises(ArtifactConflictError, match="input snapshot"):
        store.commit_feature_batch(
            feature_rows=(feature,),
            manifests=(manifest,),
            checkpoint=checkpoint,
            expected_generation=0,
            max_feature_rows=1,
            max_manifests=1,
        )

    assert store.artifact_counts("run-store")["features"] == 0


def test_replay_commit_rejects_candidate_content_outside_checkpoint(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    ranking, event, equity = _outputs()
    changed_event = replace(event, candidate_config_hash="candidate-config-b")
    changed_equity = replace(equity, candidate_config_hash="candidate-config-b")
    manifest_hash, output_keys = replay_artifact_identity(
        (ranking,),
        (changed_event,),
        (changed_equity,),
    )
    checkpoint = build_replay_checkpoint(
        contract=_contract(),
        stage="replay",
        generation=1,
        manifest_hash=manifest_hash,
        state=_state(),
        last_completed_unit="replay:2026-03-05",
        output_keys=output_keys,
    )

    with pytest.raises(ArtifactConflictError, match="candidate config"):
        store.commit_replay_batch(
            rankings=(ranking,),
            events=(changed_event,),
            equity_curve=(changed_equity,),
            checkpoint=checkpoint,
            expected_generation=0,
            max_rankings=1,
            max_events=1,
            max_equity_rows=1,
        )

    assert store.artifact_counts("run-store")["events"] == 0


def test_policy_artifacts_are_sealed_once_and_read_by_current_date_page(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    policies = tuple(_policy(day) for day in range(1, 6))
    policy_input_hash = canonical_policy_input_hash(policies)

    store.write_policy_outputs(
        run_id="run-store",
        outputs=policies,
        expected_policy_input_hash=policy_input_hash,
        candidate_config_hash="candidate-config-a",
        frozen_parameter_hash="frozen-parameters-a",
        input_snapshot_hash="input-a",
        max_rows=5,
    )

    assert store.read_policy_page(
        run_id="run-store",
        session_dates=(date(2026, 3, 5),),
        expected_policy_input_hash=policy_input_hash,
        candidate_config_hash="candidate-config-a",
        frozen_parameter_hash="frozen-parameters-a",
        input_snapshot_hash="input-a",
        max_rows=1,
    ) == (_policy(5),)
    assert store.artifact_counts("run-store")["policies"] == 5

    changed = (*policies[:-1], replace(_policy(5), target_remaining_fraction=0.0))
    with pytest.raises(ArtifactConflictError, match="immutable"):
        store.write_policy_outputs(
            run_id="run-store",
            outputs=changed,
            expected_policy_input_hash=canonical_policy_input_hash(changed),
            candidate_config_hash="candidate-config-a",
            frozen_parameter_hash="frozen-parameters-a",
            input_snapshot_hash="input-a",
            max_rows=5,
        )


def test_policy_page_rejects_payload_swapped_behind_the_sealed_hash(tmp_path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    store = ReplayArtifactStore(path)
    policy = _policy(5)
    policy_input_hash = canonical_policy_input_hash((policy,))
    store.write_policy_outputs(
        run_id="run-store",
        outputs=(policy,),
        expected_policy_input_hash=policy_input_hash,
        candidate_config_hash="candidate-config-a",
        frozen_parameter_hash="frozen-parameters-a",
        input_snapshot_hash="input-a",
        max_rows=1,
    )
    with sqlite3.connect(path) as connection:
        raw_json = str(
            connection.execute(
                "SELECT payload_json FROM policy_outputs WHERE run_id=?",
                ("run-store",),
            ).fetchone()[0]
        )
        payload = json.loads(raw_json)
        payload["target_remaining_fraction"] = 0.0
        connection.execute(
            "UPDATE policy_outputs SET payload_json=? WHERE run_id=?",
            (stable_contract_json(payload), "run-store"),
        )

    with pytest.raises(ArtifactConflictError, match="payload hash"):
        store.read_policy_page(
            run_id="run-store",
            session_dates=(date(2026, 3, 5),),
            expected_policy_input_hash=policy_input_hash,
            candidate_config_hash="candidate-config-a",
            frozen_parameter_hash="frozen-parameters-a",
            input_snapshot_hash="input-a",
            max_rows=1,
        )
