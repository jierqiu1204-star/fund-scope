from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.etf_research_evidence import RankingSourceKind
from app.services.strategy_lab.etf_ranking_candidates import (
    CANDIDATE_DAILY_CORE_TOP10,
    FROZEN_RANKING_CANDIDATES,
    RANKING_COST_CONTRACT_HASH,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_validation import (
    RankingPairedReturnSample,
    RankingValidationContractError,
    RankingValidationSourceEvent,
    evaluate_ranking_endpoint,
    freeze_ranking_endpoint_contract,
    freeze_ranking_validation_source_cohort,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


def _without(value: object, field: str) -> dict[str, object]:
    payload = dict(value.__dict__)
    payload.pop(field)
    return payload


def _sessions(count: int = 35) -> tuple[date, ...]:
    start = date(2026, 1, 1)
    return tuple(start + timedelta(days=index) for index in range(count))


def _source_event(signal_date: date) -> RankingValidationSourceEvent:
    draft = RankingValidationSourceEvent(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        signal_date=signal_date,
        source_signal_run_id=None,
        source_replay_run_key="endpoint-replay",
        source_replay_contract_hash=_hash("endpoint-replay-contract"),
        source_event_hash=_hash(f"event:{signal_date}"),
        ranking_contract_hash=_hash("daily-reconstructable"),
        universe_snapshot_hash=_hash(f"universe:{signal_date}"),
        input_snapshot_hash=_hash(f"input:{signal_date}"),
        score_version="daily_reconstructable_v1",
        score_field="research_score",
        price_basis="total_return_adjusted",
        publication_state=None,
        scope_kind="research_replay",
        availability_cutoff=datetime.combine(
            signal_date,
            datetime.min.time(),
            UTC,
        ),
        immutable_hash="pending",
    )
    return replace(
        draft,
        immutable_hash=stable_contract_hash(_without(draft, "immutable_hash")),
    )


def _fixture(signal_indexes: tuple[int, ...] = (0, 3, 7, 14, 21)):
    sessions = _sessions()
    events = tuple(_source_event(sessions[index]) for index in signal_indexes)
    cohort = freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
        events=events,
    )
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    contract = freeze_ranking_endpoint_contract(
        source_cohort=cohort,
        candidate_registry=registry,
        bootstrap_seed=20260715,
        bootstrap_resamples=200,
        bootstrap_block_length=2,
        minimum_independent_dates=3,
        minimum_coverage_ratio=0.75,
        maximum_drawdown_noninferiority_tolerance=0.02,
    )
    return sessions, events, cohort, registry, contract


def _sample(
    *,
    event: RankingValidationSourceEvent,
    sessions: tuple[date, ...],
    top_n: int = 10,
    horizon: int = 5,
    candidate_net: float | None = 0.02,
    baseline_net: float | None = 0.01,
    selected: tuple[str, ...] | None = None,
    status: str = "completed",
) -> RankingPairedReturnSample:
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    candidate = registry.by_id[CANDIDATE_DAILY_CORE_TOP10]
    signal_index = sessions.index(event.signal_date)
    completed = status == "completed"
    selected_codes = selected or tuple(f"A{index:02d}" for index in range(1, top_n + 1))
    draft = RankingPairedReturnSample(
        source_event_hash=event.source_event_hash,
        candidate_id=candidate.candidate_id,
        candidate_manifest_hash=candidate.manifest_hash,
        signal_date=event.signal_date,
        entry_session=sessions[signal_index + 1] if completed else None,
        exit_session=sessions[signal_index + 1 + horizon] if completed else None,
        top_n=top_n,
        horizon_sessions=horizon,
        status=status,
        selected_ranked_asset_codes=selected_codes,
        candidate_gross_return=(
            candidate_net + 0.002 if candidate_net is not None else None
        ),
        candidate_net_return=candidate_net,
        baseline_gross_return=(
            baseline_net + 0.002 if baseline_net is not None else None
        ),
        baseline_net_return=baseline_net,
        fee_bps_per_side=5,
        slippage_bps_per_side=5,
        round_trip_cost_bps=20,
        cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        exclusion_reason=None if completed else "future_window_pending",
        sample_hash="pending",
    )
    return replace(
        draft,
        sample_hash=stable_contract_hash(_without(draft, "sample_hash")),
    )


def test_primary_endpoint_is_fixed_paired_top10_five_day_net_excess() -> None:
    sessions, events, cohort, registry, contract = _fixture()
    base = tuple(f"A{index:02d}" for index in range(1, 11))
    swapped = ("A02", "A01", *base[2:9], "B01")
    samples = (
        _sample(event=events[0], sessions=sessions, selected=base),
        _sample(event=events[1], sessions=sessions, selected=base),
        _sample(event=events[2], sessions=sessions, selected=swapped),
        _sample(event=events[3], sessions=sessions, selected=swapped),
        _sample(event=events[4], sessions=sessions, selected=base),
    )

    result = evaluate_ranking_endpoint(
        source_cohort=cohort,
        candidate_registry=registry,
        contract=contract,
        candidate_id=CANDIDATE_DAILY_CORE_TOP10,
        top_n=10,
        horizon_sessions=5,
        samples=tuple(reversed(samples)),
        trading_sessions=sessions,
    )

    assert result.endpoint_role == "primary"
    assert result.endpoint_name == "top10_five_session_paired_net_excess"
    assert result.mean_paired_net_excess == pytest.approx(0.01)
    assert result.independent_dates == (
        events[0].signal_date,
        events[2].signal_date,
        events[3].signal_date,
        events[4].signal_date,
    )
    assert result.overlapping_excluded_dates == (events[1].signal_date,)
    assert result.round_trip_cost_bps == 20
    assert result.average_turnover > 0.0
    assert result.average_rank_churn > 0.0
    assert result.bootstrap_confidence_interval == pytest.approx((0.01, 0.01))
    assert result.sample_gate_passed is True
    assert result.maximum_drawdown_gate_passed is True
    assert result.result_hash


def test_other_topn_or_horizon_cells_are_always_exploratory() -> None:
    sessions, events, cohort, registry, contract = _fixture((0, 3, 7))
    samples = tuple(
        _sample(event=event, sessions=sessions, top_n=5, horizon=1)
        for event in events
    )

    result = evaluate_ranking_endpoint(
        source_cohort=cohort,
        candidate_registry=registry,
        contract=contract,
        candidate_id=CANDIDATE_DAILY_CORE_TOP10,
        top_n=5,
        horizon_sessions=1,
        samples=samples,
        trading_sessions=sessions,
    )

    assert result.endpoint_role == "exploratory"
    assert result.endpoint_name == "top5_one_session_paired_net_excess"


def test_pending_outcomes_reduce_coverage_and_fail_sample_gate() -> None:
    sessions, events, cohort, registry, contract = _fixture((0, 7, 14, 21))
    samples = (
        *tuple(_sample(event=event, sessions=sessions) for event in events[:3]),
        _sample(
            event=events[3],
            sessions=sessions,
            candidate_net=None,
            baseline_net=None,
            status="pending",
        ),
    )

    strict_contract = replace(
        contract,
        minimum_coverage_ratio=1.0,
        contract_hash="tampered",
    )
    strict_contract = replace(
        strict_contract,
        contract_hash=stable_contract_hash(_without(strict_contract, "contract_hash")),
    )
    result = evaluate_ranking_endpoint(
        source_cohort=cohort,
        candidate_registry=registry,
        contract=strict_contract,
        candidate_id=CANDIDATE_DAILY_CORE_TOP10,
        top_n=10,
        horizon_sessions=5,
        samples=samples,
        trading_sessions=sessions,
    )

    assert result.coverage_numerator == 3
    assert result.coverage_denominator == 4
    assert result.coverage_ratio == pytest.approx(0.75)
    assert result.sample_gate_passed is False


def test_drawdown_noninferiority_gate_is_reported_separately() -> None:
    sessions, events, cohort, registry, contract = _fixture((0, 7, 14))
    samples = (
        _sample(event=events[0], sessions=sessions, candidate_net=0.02, baseline_net=0.01),
        _sample(event=events[1], sessions=sessions, candidate_net=-0.20, baseline_net=-0.01),
        _sample(event=events[2], sessions=sessions, candidate_net=0.01, baseline_net=0.01),
    )

    result = evaluate_ranking_endpoint(
        source_cohort=cohort,
        candidate_registry=registry,
        contract=contract,
        candidate_id=CANDIDATE_DAILY_CORE_TOP10,
        top_n=10,
        horizon_sessions=5,
        samples=samples,
        trading_sessions=sessions,
    )

    assert result.candidate_maximum_drawdown > result.baseline_maximum_drawdown
    assert result.maximum_drawdown_gate_passed is False
    assert result.sample_gate_passed is True


def test_endpoint_rejects_dynamic_cells_costs_and_source_mixing() -> None:
    sessions, events, cohort, registry, contract = _fixture((0, 7, 14))
    sample = _sample(event=events[0], sessions=sessions)
    production = _source_event(sessions[20])
    production = replace(
        production,
        source_event_hash=_hash("outside-event"),
        immutable_hash="pending",
    )
    production = replace(
        production,
        immutable_hash=stable_contract_hash(_without(production, "immutable_hash")),
    )

    for top_n, horizon, message in (
        (7, 5, "frozen Top-N"),
        (10, 2, "frozen horizon"),
    ):
        with pytest.raises(RankingValidationContractError, match=message):
            evaluate_ranking_endpoint(
                source_cohort=cohort,
                candidate_registry=registry,
                contract=contract,
                candidate_id=CANDIDATE_DAILY_CORE_TOP10,
                top_n=top_n,
                horizon_sessions=horizon,
                samples=(sample,),
                trading_sessions=sessions,
            )
    with pytest.raises(RankingValidationContractError, match="cost"):
        evaluate_ranking_endpoint(
            source_cohort=cohort,
            candidate_registry=registry,
            contract=contract,
            candidate_id=CANDIDATE_DAILY_CORE_TOP10,
            top_n=10,
            horizon_sessions=5,
            samples=(replace(sample, round_trip_cost_bps=19),),
            trading_sessions=sessions,
        )
    with pytest.raises(RankingValidationContractError, match="outside"):
        evaluate_ranking_endpoint(
            source_cohort=cohort,
            candidate_registry=registry,
            contract=contract,
            candidate_id=CANDIDATE_DAILY_CORE_TOP10,
            top_n=10,
            horizon_sessions=5,
            samples=(_sample(event=production, sessions=sessions),),
            trading_sessions=sessions,
        )
