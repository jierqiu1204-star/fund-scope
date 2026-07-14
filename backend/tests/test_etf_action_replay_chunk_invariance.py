from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ReplayArtifactStore,
    replay_artifact_identity,
)
from app.services.strategy_lab.etf_action_replay.checkpoint import (
    ReplayRunContract,
    build_replay_checkpoint,
)
from app.services.strategy_lab.etf_action_replay.features import (
    AdjustedDailyInput,
    FeatureBatchRequest,
    compute_feature_batch,
    merge_feature_rows,
)
from app.services.strategy_lab.etf_action_replay.replay import (
    PointInTimeUniverseDay,
    ReplayBatchRequest,
    ReplayCandidateConfig,
    ReplayPolicyOutput,
    build_completion_manifest,
    canonical_candidate_config_hash,
    canonical_membership_hash,
    canonical_policy_input_hash,
    run_replay_batch,
)

RUN_ID = "run-invariance"
ASSET_CODES = ("510001", "510002")
SESSIONS = tuple(date(2026, 3, day) for day in range(1, 6))
FROZEN_PARAMETER_HASH = "frozen-parameters-a"


def _source_rows() -> tuple[AdjustedDailyInput, ...]:
    rows = []
    for code_index, code in enumerate(ASSET_CODES):
        for day in range(1, 6):
            price = 10.0 + code_index * 2.0 + day
            available_at = datetime(2026, 3, day, 15, 0, tzinfo=UTC)
            rows.append(
                AdjustedDailyInput(
                    asset_code=code,
                    session_date=date(2026, 3, day),
                    raw_open=price,
                    raw_high=price + 0.5,
                    raw_low=price - 0.5,
                    raw_close=price,
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
            )
    return tuple(rows)


def _feature_request(asset_codes: tuple[str, ...], **overrides: object):
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "feature_contract_hash": "feature-contract-a",
        "input_snapshot_hash": "input-a",
        "asset_codes": asset_codes,
        "start_date": date(2026, 3, 3),
        "end_date": date(2026, 3, 5),
        "data_cutoff": datetime(2026, 3, 5, 15, 30, tzinfo=UTC),
        "trading_sessions": SESSIONS,
        "decision_cutoffs": tuple(
            (session, datetime(2026, 3, session.day, 15, 30, tzinfo=UTC))
            for session in SESSIONS
        ),
        "warmup_sessions": 2,
        "max_source_rows": 20,
        "max_items": 100,
        "max_seconds": 55.0,
        "worker_count": 1,
    }
    values.update(overrides)
    return FeatureBatchRequest(**values)  # type: ignore[arg-type]


def _manifests(features):
    output = []
    for session in SESSIONS[2:]:
        rows = tuple(row for row in features if row.session_date == session)
        universe = PointInTimeUniverseDay(
            session_date=session,
            eligible_asset_codes=ASSET_CODES,
            expected_universe_count=len(ASSET_CODES),
            canonical_membership_hash=canonical_membership_hash(ASSET_CODES),
            snapshot_hash=f"universe-{session.isoformat()}",
        )
        output.append(
            build_completion_manifest(
                run_id=RUN_ID,
                universe=universe,
                feature_rows=rows,
            )
        )
    return tuple(output)


def _candidate() -> ReplayCandidateConfig:
    return ReplayCandidateConfig(
        "candidate-2",
        top_n=1,
        initial_cash=10_000.0,
        fee_rate=0.001,
        lot_size=1,
    )


def _policy() -> ReplayPolicyOutput:
    candidate_config_hash = canonical_candidate_config_hash(
        (_candidate(),),
        frozen_parameter_hash=FROZEN_PARAMETER_HASH,
    )
    return ReplayPolicyOutput(
        candidate_id="candidate-2",
        candidate_config_hash=candidate_config_hash,
        frozen_parameter_hash=FROZEN_PARAMETER_HASH,
        input_snapshot_hash="input-a",
        session_date=date(2026, 3, 4),
        asset_code="510001",
        rule_id="confirmed_trend_weakening",
        alert_episode_id="alert-1",
        action_cycle_id="cycle-1",
        action_decision_id="action-1",
        target_remaining_fraction=0.5,
        decision_eligible=True,
    )


def _contract() -> ReplayRunContract:
    candidate_config_hash = canonical_candidate_config_hash(
        (_candidate(),),
        frozen_parameter_hash=FROZEN_PARAMETER_HASH,
    )
    return ReplayRunContract(
        run_id=RUN_ID,
        contract_hash="contract-a",
        input_snapshot_hash="input-a",
        code_hash="code-a",
        schema_hash="schema-a",
        candidate_config_hash=candidate_config_hash,
        frozen_parameter_hash=FROZEN_PARAMETER_HASH,
        policy_input_hash=canonical_policy_input_hash((_policy(),)),
        data_cutoff=date(2026, 3, 5),
        warmup_boundary=date(2026, 3, 1),
        candidate_ids=("candidate-2",),
    )


def test_code_chunks_and_item_cursor_resume_produce_identical_features() -> None:
    source_rows = _source_rows()
    one_chunk = compute_feature_batch(
        source_rows=source_rows,
        request=_feature_request(ASSET_CODES),
        candidate_ids=("candidate-2",),
    )
    chunk_a = compute_feature_batch(
        source_rows=source_rows,
        request=_feature_request(("510001",)),
        candidate_ids=("candidate-2",),
    )
    chunk_b = compute_feature_batch(
        source_rows=source_rows,
        request=_feature_request(("510002",)),
        candidate_ids=("candidate-2",),
    )
    interrupted = compute_feature_batch(
        source_rows=source_rows,
        request=_feature_request(ASSET_CODES, max_items=2),
        candidate_ids=("candidate-2",),
    )
    resumed = compute_feature_batch(
        source_rows=source_rows,
        request=replace(
            _feature_request(ASSET_CODES),
            after_key=interrupted.next_after_key,
        ),
        candidate_ids=("candidate-2",),
    )

    assert merge_feature_rows(chunk_a.rows, chunk_b.rows) == one_chunk.rows
    assert merge_feature_rows(interrupted.rows, resumed.rows) == one_chunk.rows


def test_daily_checkpoint_resume_matches_one_batch_ranking_action_fill_and_equity(
    tmp_path,
) -> None:
    features = compute_feature_batch(
        source_rows=_source_rows(),
        request=_feature_request(ASSET_CODES),
        candidate_ids=("candidate-2",),
    ).rows
    manifests = _manifests(features)
    candidate = _candidate()
    one_shot = run_replay_batch(
        feature_rows=features,
        manifests=manifests,
        candidates=(candidate,),
        policy_outputs=(_policy(),),
        request=ReplayBatchRequest(
            run_id=RUN_ID,
            frozen_parameter_hash=FROZEN_PARAMETER_HASH,
            start_date=date(2026, 3, 3),
            end_date=date(2026, 3, 5),
            max_dates=3,
            max_feature_rows=6,
            max_policy_outputs=10,
            max_pending_fills=10,
            max_events=100,
        ),
    )

    store = ReplayArtifactStore(tmp_path / "resume.sqlite3")
    state = None
    rankings = []
    events = []
    equity = []
    generation = 0
    for index, manifest in enumerate(manifests, start=1):
        day_features = tuple(
            row for row in features if row.session_date == manifest.session_date
        )
        result = run_replay_batch(
            feature_rows=day_features,
            manifests=(manifest,),
            candidates=(candidate,),
            policy_outputs=(
                (_policy(),) if manifest.session_date == date(2026, 3, 4) else ()
            ),
            request=ReplayBatchRequest(
                run_id=RUN_ID,
                frozen_parameter_hash=FROZEN_PARAMETER_HASH,
                start_date=manifest.session_date,
                end_date=manifest.session_date,
                max_dates=1,
                max_feature_rows=2,
                max_policy_outputs=10,
                max_pending_fills=10,
                max_events=100,
                after_date=(
                    manifests[index - 2].session_date if index > 1 else None
                ),
                has_more=index < len(manifests),
            ),
            state=state,
        )
        generation += 1
        manifest_hash, output_keys = replay_artifact_identity(
            result.rankings,
            result.events,
            result.equity_curve,
        )
        checkpoint = build_replay_checkpoint(
            contract=_contract(),
            stage="replay",
            generation=generation,
            manifest_hash=manifest_hash,
            state=result.state,
            last_completed_unit=f"replay:{manifest.session_date.isoformat()}",
            output_keys=output_keys,
        )
        store.commit_replay_batch(
            rankings=result.rankings,
            events=result.events,
            equity_curve=result.equity_curve,
            checkpoint=checkpoint,
            expected_generation=generation - 1,
            max_rankings=1,
            max_events=10,
            max_equity_rows=1,
            max_seconds=55.0,
        )
        loaded = store.load_checkpoint(
            run_id=RUN_ID,
            stage="replay",
            expected_contract=_contract(),
        )
        assert loaded is not None
        state = loaded.state
        rankings.extend(result.rankings)
        events.extend(result.events)
        equity.extend(result.equity_curve)

    assert tuple(rankings) == one_shot.rankings
    assert tuple(events) == one_shot.events
    assert tuple(equity) == one_shot.equity_curve
    assert state == one_shot.state
    assert any(event.event_type == "action_decision" for event in events)
    assert any(event.event_type == "buy_filled" for event in events)
    assert any(event.event_type == "sell_filled" for event in events)
