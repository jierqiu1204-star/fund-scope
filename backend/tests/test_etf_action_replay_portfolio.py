from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from app.services.strategy_lab.etf_action_replay.features import (
    BoundedWorkLimitError,
    FeatureRow,
)
from app.services.strategy_lab.etf_action_replay.replay import (
    IncompleteCrossSectionError,
    PointInTimeUniverseDay,
    ReplayBatchRequest,
    ReplayCandidateConfig,
    ReplayPolicyOutput,
    build_completion_manifest,
    canonical_candidate_config_hash,
    canonical_membership_hash,
    rank_feature_cross_section,
    run_replay_batch,
)

RUN_ID = "run-replay"
CONTRACT_HASH = "feature-contract-a"
INPUT_HASH = "input-snapshot-a"
FROZEN_PARAMETER_HASH = "frozen-parameters-a"


def _feature(code: str, day: int, score: float, price: float = 10.0) -> FeatureRow:
    session_date = date(2026, 2, day)
    row_hashes = (f"warmup-{code}-{day}-1", f"warmup-{code}-{day}-2")
    return FeatureRow(
        run_id=RUN_ID,
        feature_contract_hash=CONTRACT_HASH,
        input_snapshot_hash=INPUT_HASH,
        asset_code=code,
        session_date=session_date,
        raw_open=price,
        raw_high=price + 0.5,
        raw_low=price - 0.5,
        raw_close=price,
        volume=1_000_000.0,
        adjusted_open=price,
        adjusted_high=price + 0.5,
        adjusted_low=price - 0.5,
        adjusted_close=price,
        momentum_return=score / 100.0,
        score=score,
        warmup_sessions=1,
        warmup_boundary=date(2026, 2, day - 1),
        warmup_row_hashes=row_hashes,
        warmup_provenance_hash=f"provenance-{code}-{day}",
        adjustment_factor=1.0,
        adjusted_data_source="eastmoney_total_return",
        adjustment_kind="total_return_adjusted",
        decision_eligible=True,
        provider_healthy=True,
        fresh_at_cutoff=True,
        known_at=datetime(2026, 2, day, 15, 0, tzinfo=UTC),
        source_cutoff=datetime(2026, 2, 10, 15, 30, tzinfo=UTC),
        input_row_hash=f"input-{code}-{day}",
    )


def _universe(day: int, codes: tuple[str, ...]) -> PointInTimeUniverseDay:
    canonical = tuple(sorted(codes))
    return PointInTimeUniverseDay(
        session_date=date(2026, 2, day),
        eligible_asset_codes=canonical,
        expected_universe_count=len(canonical),
        canonical_membership_hash=canonical_membership_hash(canonical),
        snapshot_hash=f"universe-{day}",
    )


def _manifest(day: int, rows: tuple[FeatureRow, ...]):
    return build_completion_manifest(
        run_id=RUN_ID,
        universe=_universe(day, tuple(row.asset_code for row in rows)),
        feature_rows=rows,
    )


def _request(**overrides: object) -> ReplayBatchRequest:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "frozen_parameter_hash": FROZEN_PARAMETER_HASH,
        "start_date": date(2026, 2, 3),
        "end_date": date(2026, 2, 5),
        "max_dates": 3,
        "max_feature_rows": 20,
        "max_policy_outputs": 20,
        "max_pending_fills": 20,
        "max_events": 200,
        "max_seconds": 55.0,
        "worker_count": 1,
        "has_more": False,
    }
    values.update(overrides)
    return ReplayBatchRequest(**values)  # type: ignore[arg-type]


def test_manifest_rejects_incomplete_or_forged_cross_section() -> None:
    universe = _universe(3, ("510001", "510002"))
    first = _feature("510001", 3, 90.0)

    with pytest.raises(IncompleteCrossSectionError, match="feature membership"):
        build_completion_manifest(
            run_id=RUN_ID,
            universe=universe,
            feature_rows=(first,),
        )

    complete = build_completion_manifest(
        run_id=RUN_ID,
        universe=universe,
        feature_rows=(first, _feature("510002", 3, 80.0)),
    )
    forged = replace(
        complete,
        feature_hashes=(("510001", "forged"), *complete.feature_hashes[1:]),
    )
    with pytest.raises(IncompleteCrossSectionError, match="manifest hash"):
        run_replay_batch(
            feature_rows=(first, _feature("510002", 3, 80.0)),
            manifests=(forged,),
            candidates=(ReplayCandidateConfig("candidate-2", top_n=1),),
            policy_outputs=(),
            request=_request(end_date=date(2026, 2, 3), max_dates=1),
        )


def test_stage_b_ranks_each_full_manifest_once_and_isolates_candidates() -> None:
    rank_calls: list[tuple[str, ...]] = []

    def counting_ranker(rows: tuple[FeatureRow, ...]) -> tuple[FeatureRow, ...]:
        rank_calls.append(tuple(row.asset_code for row in rows))
        return rank_feature_cross_section(rows)

    rows = (
        _feature("510001", 3, 90.0),
        _feature("510002", 3, 80.0),
        _feature("510003", 3, 70.0),
    )
    result = run_replay_batch(
        feature_rows=rows,
        manifests=(_manifest(3, rows),),
        candidates=(
            ReplayCandidateConfig("candidate-2", top_n=1, initial_cash=10_000.0),
            ReplayCandidateConfig("candidate-3", top_n=2, initial_cash=20_000.0),
        ),
        policy_outputs=(),
        request=_request(end_date=date(2026, 2, 3), max_dates=1),
        ranker=counting_ranker,
    )

    assert rank_calls == [("510001", "510002", "510003")]
    assert result.rankings[0].ordered_asset_codes == ("510001", "510002", "510003")
    assert len(result.state.candidate_states["candidate-2"].pending_fills) == 1
    assert len(result.state.candidate_states["candidate-3"].pending_fills) == 2
    assert result.state.candidate_states["candidate-2"].cash == 10_000.0
    assert result.state.candidate_states["candidate-3"].cash == 20_000.0


def test_stage_b_executes_t_plus_one_buy_then_absolute_sell_and_records_equity() -> None:
    day3 = (_feature("510001", 3, 90.0, 10.0), _feature("510002", 3, 80.0, 10.0))
    day4 = (_feature("510001", 4, 90.0, 11.0), _feature("510002", 4, 80.0, 10.0))
    day5 = (_feature("510001", 5, 90.0, 12.0), _feature("510002", 5, 80.0, 10.0))
    candidate = ReplayCandidateConfig(
        "candidate-2",
        top_n=1,
        initial_cash=10_000.0,
        fee_rate=0.001,
        lot_size=1,
    )

    first = run_replay_batch(
        feature_rows=day3,
        manifests=(_manifest(3, day3),),
        candidates=(candidate,),
        policy_outputs=(),
        request=_request(end_date=date(2026, 2, 3), max_dates=1, has_more=True),
    )
    assert first.state.candidate_states["candidate-2"].positions == {}
    assert {item.side for item in first.state.candidate_states["candidate-2"].pending_fills} == {
        "buy"
    }

    policy = ReplayPolicyOutput(
        candidate_id="candidate-2",
        candidate_config_hash=canonical_candidate_config_hash(
            (candidate,),
            frozen_parameter_hash=FROZEN_PARAMETER_HASH,
        ),
        frozen_parameter_hash=FROZEN_PARAMETER_HASH,
        input_snapshot_hash=INPUT_HASH,
        session_date=date(2026, 2, 4),
        asset_code="510001",
        rule_id="confirmed_trend_weakening",
        alert_episode_id="alert-1",
        action_cycle_id="cycle-1",
        action_decision_id="action-1",
        target_remaining_fraction=0.5,
        decision_eligible=True,
    )
    second = run_replay_batch(
        feature_rows=day4,
        manifests=(_manifest(4, day4),),
        candidates=(candidate,),
        policy_outputs=(policy,),
        request=_request(
            start_date=date(2026, 2, 4),
            end_date=date(2026, 2, 4),
            max_dates=1,
            after_date=date(2026, 2, 3),
            has_more=True,
        ),
        state=first.state,
    )
    position_after_buy = second.state.candidate_states["candidate-2"].positions["510001"]
    assert position_after_buy.shares > 0
    assert position_after_buy.exposure_baseline_shares == position_after_buy.shares
    assert position_after_buy.action_cycle_id == "cycle-1"
    assert position_after_buy.alert_episode_ids == {
        "confirmed_trend_weakening": "alert-1"
    }
    assert any(event.event_type == "buy_filled" for event in second.events)
    assert any(event.event_type == "action_decision" for event in second.events)
    assert second.equity_curve[-1].equity > 0

    third = run_replay_batch(
        feature_rows=day5,
        manifests=(_manifest(5, day5),),
        candidates=(candidate,),
        policy_outputs=(),
        request=_request(
            start_date=date(2026, 2, 5),
            end_date=date(2026, 2, 5),
            max_dates=1,
            after_date=date(2026, 2, 4),
        ),
        state=second.state,
    )
    final_state = third.state.candidate_states["candidate-2"]
    final_position = final_state.positions["510001"]
    assert final_position.shares == pytest.approx(
        position_after_buy.exposure_baseline_shares * 0.5
    )
    assert final_state.cumulative_fees > second.state.candidate_states["candidate-2"].cumulative_fees
    assert final_state.turnover > second.state.candidate_states["candidate-2"].turnover
    assert any(event.event_type == "sell_filled" for event in third.events)
    assert third.equity_curve[-1].session_date == date(2026, 2, 5)


def test_stage_b_rejects_duplicate_ranker_output() -> None:
    rows = (_feature("510001", 3, 90.0), _feature("510002", 3, 80.0))

    with pytest.raises(IncompleteCrossSectionError, match="ranker output"):
        run_replay_batch(
            feature_rows=rows,
            manifests=(_manifest(3, rows),),
            candidates=(ReplayCandidateConfig("candidate-2", top_n=1),),
            policy_outputs=(),
            request=_request(end_date=date(2026, 2, 3), max_dates=1),
            ranker=lambda ranked: (ranked[0], ranked[0], ranked[1]),
        )


def test_stage_b_stops_consuming_manifest_input_at_explicit_date_bound() -> None:
    consumed = 0
    rows_by_day = {
        day: (_feature("510001", day, 90.0),)
        for day in (3, 4, 5)
    }

    def manifests():
        nonlocal consumed
        for day, rows in rows_by_day.items():
            consumed += 1
            yield _manifest(day, rows)

    with pytest.raises(IncompleteCrossSectionError, match="max_dates"):
        run_replay_batch(
            feature_rows=tuple(row for rows in rows_by_day.values() for row in rows),
            manifests=manifests(),
            candidates=(ReplayCandidateConfig("candidate-2", top_n=1),),
            policy_outputs=(),
            request=_request(max_dates=1),
        )

    assert consumed == 2


def test_stage_b_fails_closed_when_processing_exceeds_deadline() -> None:
    rows = (_feature("510001", 3, 90.0),)
    now = [0.0]

    def slow_ranker(values: tuple[FeatureRow, ...]) -> tuple[FeatureRow, ...]:
        now[0] = 2.0
        return values

    with pytest.raises(BoundedWorkLimitError, match="max_seconds"):
        run_replay_batch(
            feature_rows=rows,
            manifests=(_manifest(3, rows),),
            candidates=(ReplayCandidateConfig("candidate-2", top_n=1),),
            policy_outputs=(),
            request=_request(
                end_date=date(2026, 2, 3),
                max_dates=1,
                max_seconds=1.0,
            ),
            ranker=slow_ranker,
            clock=lambda: now[0],
        )


def test_stage_b_rejects_checkpoint_state_above_pending_fill_bound() -> None:
    day3 = (
        _feature("510001", 3, 90.0),
        _feature("510002", 3, 80.0),
    )
    candidate = ReplayCandidateConfig("candidate-2", top_n=2)
    first = run_replay_batch(
        feature_rows=day3,
        manifests=(_manifest(3, day3),),
        candidates=(candidate,),
        policy_outputs=(),
        request=_request(end_date=date(2026, 2, 3), max_dates=1, has_more=True),
    )

    with pytest.raises(BoundedWorkLimitError, match="pending fills"):
        run_replay_batch(
            feature_rows=(_feature("510001", 4, 90.0), _feature("510002", 4, 80.0)),
            manifests=(
                _manifest(
                    4,
                    (_feature("510001", 4, 90.0), _feature("510002", 4, 80.0)),
                ),
            ),
            candidates=(candidate,),
            policy_outputs=(),
            request=_request(
                start_date=date(2026, 2, 4),
                end_date=date(2026, 2, 4),
                max_dates=1,
                max_pending_fills=1,
                after_date=date(2026, 2, 3),
            ),
            state=first.state,
        )


def test_zero_cash_rejection_preserves_candidate_config_identity() -> None:
    day3 = (_feature("510001", 3, 90.0),)
    day4 = (_feature("510001", 4, 90.0),)
    candidate = ReplayCandidateConfig("candidate-2", top_n=1, initial_cash=0.0)
    first = run_replay_batch(
        feature_rows=day3,
        manifests=(_manifest(3, day3),),
        candidates=(candidate,),
        policy_outputs=(),
        request=_request(end_date=date(2026, 2, 3), max_dates=1, has_more=True),
    )

    second = run_replay_batch(
        feature_rows=day4,
        manifests=(_manifest(4, day4),),
        candidates=(candidate,),
        policy_outputs=(),
        request=_request(
            start_date=date(2026, 2, 4),
            end_date=date(2026, 2, 4),
            max_dates=1,
            after_date=date(2026, 2, 3),
        ),
        state=first.state,
    )

    rejection = next(event for event in second.events if event.event_type == "fill_rejected")
    assert rejection.candidate_config_hash == second.candidate_config_hash


def test_candidate_config_hash_changes_event_and_equity_output_identity() -> None:
    rows = (_feature("510001", 3, 90.0),)
    baseline = ReplayCandidateConfig("candidate-2", top_n=1, fee_rate=0.001)
    changed = ReplayCandidateConfig("candidate-2", top_n=1, fee_rate=0.002)

    first = run_replay_batch(
        feature_rows=rows,
        manifests=(_manifest(3, rows),),
        candidates=(baseline,),
        policy_outputs=(),
        request=_request(end_date=date(2026, 2, 3), max_dates=1),
    )
    second = run_replay_batch(
        feature_rows=rows,
        manifests=(_manifest(3, rows),),
        candidates=(changed,),
        policy_outputs=(),
        request=_request(end_date=date(2026, 2, 3), max_dates=1),
    )

    assert first.candidate_config_hash != second.candidate_config_hash
    assert first.events[0].event_key != second.events[0].event_key
    assert first.equity_curve[0].stable_key != second.equity_curve[0].stable_key
