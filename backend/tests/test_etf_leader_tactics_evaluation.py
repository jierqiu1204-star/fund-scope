from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from app.services.strategy_lab.etf_factor_experiment import ChronologicalSplit
from app.services.strategy_lab.etf_factor_panel import (
    CandidateCommonSupportPanel,
    CandidateDateCohort,
    CommonSupportSample,
)
from app.services.strategy_lab.etf_leader_tactics_evaluation import (
    LEADER_HORIZONS,
    LEADER_MULTIPLICITY_METHOD,
    LEADER_TOP_NS,
    LeaderControlObservation,
    LeaderEndpointResult,
    LeaderHardGateFacts,
    LeaderPrimaryInference,
    build_leader_factor_experiment_registration,
    evaluate_leader_candidate,
    evaluate_leader_endpoints,
    infer_leader_primaries,
    leader_factor_diagnostics,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    LEADER_CANDIDATE_IDS,
    LEADER_EXPERIMENT_FAMILY,
    LeaderCandidateObservation,
    LeaderFeaturePanel,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    RANKING_COST_CONTRACT_HASH,
    freeze_ranking_candidate_registry,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _split() -> ChronologicalSplit:
    return ChronologicalSplit(
        development_start=date(2024, 1, 1),
        development_end=date(2024, 12, 31),
        validation_start=date(2025, 1, 1),
        validation_end=date(2025, 12, 31),
        holdout_start=date(2026, 1, 1),
        holdout_end=date(2026, 12, 31),
    )


def test_leader_registration_reuses_factor_contract_without_mutating_production_registry() -> None:
    before = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    registration = build_leader_factor_experiment_registration(
        code_version="test-v1",
        baseline_contract_id="daily_reconstructable_v1",
        baseline_contract_hash=HASH_A,
        baseline_score_field="research_score",
        research_surface_contract_hash=HASH_B,
        actionable_surface_contract_hash=HASH_C,
        split=_split(),
        split_contract_hash=HASH_D,
        holdout_identity_hash=HASH_A,
        registered_at=datetime(2026, 7, 31, 12),
    )
    after = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)

    manifest = registration.factor_experiment.manifest
    assert manifest.experiment_name == LEADER_EXPERIMENT_FAMILY
    assert tuple(item.candidate_id for item in manifest.candidates) == LEADER_CANDIDATE_IDS
    assert manifest.primary_endpoint == "paired_top10_5_session_net_excess_common_support"
    assert manifest.execution_cost.fee_bps_per_side == 5
    assert manifest.execution_cost.slippage_bps_per_side == 5
    assert before == after
    assert before.registry_hash != registration.leader_manifest.candidate_registry_hash


def _support_sample(
    *,
    signal_date: date,
    code: str,
    baseline_score: float,
    candidate_id: str | None,
    candidate_score: float | None,
    value: float,
) -> CommonSupportSample:
    return CommonSupportSample(
        asset_code=code,
        signal_date=signal_date,
        baseline_score=baseline_score,
        candidate_scores=(
            {candidate_id: candidate_score}
            if candidate_id is not None and candidate_score is not None
            else {}
        ),
        peer_bucket="technology",
        history_tier="full_history_context",
        entry_date=signal_date + timedelta(days=1),
        gross_returns={horizon: value * horizon + 0.002 for horizon in LEADER_HORIZONS},
        net_returns={horizon: value * horizon for horizon in LEADER_HORIZONS},
        pending_horizons=(),
    )


def _panels(signal_dates: tuple[date, ...]) -> dict[str, CandidateCommonSupportPanel]:
    panels: dict[str, CandidateCommonSupportPanel] = {}
    for candidate_id in LEADER_CANDIDATE_IDS:
        baseline: list[CommonSupportSample] = []
        candidate: list[CommonSupportSample] = []
        cohorts: list[CandidateDateCohort] = []
        for signal_date in signal_dates:
            codes: list[str] = []
            for index in range(20):
                code = f"{signal_date.day:02d}{index:04d}"
                codes.append(code)
                baseline.append(
                    _support_sample(
                        signal_date=signal_date,
                        code=code,
                        baseline_score=20 - index,
                        candidate_id=None,
                        candidate_score=None,
                        value=(index + 1) / 10_000,
                    )
                )
                candidate.append(
                    _support_sample(
                        signal_date=signal_date,
                        code=code,
                        baseline_score=20 - index,
                        candidate_id=candidate_id,
                        candidate_score=index,
                        value=(index + 1) / 10_000,
                    )
                )
            cohorts.append(
                CandidateDateCohort(
                    signal_date=signal_date,
                    baseline_asset_codes=tuple(codes[:10]),
                    candidate_asset_codes=tuple(reversed(codes[10:])),
                    complete=True,
                    exclusion_reason=None,
                    cohort_hash=HASH_A,
                )
            )
        panels[candidate_id] = CandidateCommonSupportPanel(
            candidate_id=candidate_id,
            baseline_samples=tuple(baseline),
            candidate_samples=tuple(candidate),
            cohorts=tuple(cohorts),
            exclusions={},
            coverage={"candidate_support_ratio": 1.0},
            panel_hash=HASH_B,
        )
    return panels


def test_endpoints_keep_one_primary_and_all_auxiliary_cells_exploratory() -> None:
    calendar = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(40))
    signal_dates = (calendar[0], calendar[7], calendar[14])
    results = evaluate_leader_endpoints(
        _panels(signal_dates),
        trading_sessions=calendar,
    )

    assert len(results) == len(LEADER_CANDIDATE_IDS) * len(LEADER_TOP_NS) * len(
        LEADER_HORIZONS
    )
    for candidate_id in LEADER_CANDIDATE_IDS:
        candidate_results = [item for item in results if item.candidate_id == candidate_id]
        primary = [item for item in candidate_results if item.endpoint_role == "primary"]
        assert len(primary) == 1
        assert (primary[0].top_n, primary[0].horizon_sessions) == (10, 5)
        assert primary[0].mean_paired_net_excess is not None
        assert primary[0].mean_paired_net_excess > 0
        assert primary[0].cost_contract_hash == RANKING_COST_CONTRACT_HASH
        assert all(
            item.endpoint_role == "exploratory"
            for item in candidate_results
            if item is not primary[0]
        )


def test_holm_inference_is_candidate_mapped_and_uses_adjusted_intervals() -> None:
    calendar = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(40))
    results = evaluate_leader_endpoints(
        _panels((calendar[0], calendar[7], calendar[14])),
        trading_sessions=calendar,
    )
    inference = infer_leader_primaries(results, resamples=100)

    assert tuple(item.candidate_id for item in inference) == LEADER_CANDIDATE_IDS
    assert all(item.available for item in inference)
    assert all(item.method == LEADER_MULTIPLICITY_METHOD for item in inference)
    assert all(item.holm_adjusted_p_value is not None for item in inference)
    assert all(item.simultaneous_confidence is not None for item in inference)
    assert all(item.simultaneous_confidence >= 0.95 for item in inference)


def _primary_result(*, endpoint_role: str = "primary") -> LeaderEndpointResult:
    dates = tuple(date(2024, 1, 1) + timedelta(days=7 * index) for index in range(40))
    values = tuple(0.01 for _ in dates)
    return LeaderEndpointResult(
        candidate_id=LEADER_CANDIDATE_IDS[0],
        top_n=10,
        horizon_sessions=5,
        endpoint_role=endpoint_role,  # type: ignore[arg-type]
        endpoint_name="paired_top10_5_session_net_excess_common_support",
        complete_cohort_dates=dates,
        completed_outcome_dates=dates,
        independent_dates=dates,
        overlapping_excluded_dates=(),
        selected_by_date=tuple((item, tuple(str(i) for i in range(10))) for item in dates),
        candidate_net_returns=values,
        baseline_net_returns=tuple(0.0 for _ in dates),
        paired_net_excess=values,
        mean_candidate_net_return=0.01,
        mean_baseline_net_return=0.0,
        mean_paired_net_excess=0.01,
        average_turnover=0.1,
        average_rank_churn=0.1,
        candidate_maximum_drawdown=0.01,
        baseline_maximum_drawdown=0.01,
        coverage_ratio=1.0,
        exclusion_counts={},
        cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        result_hash=HASH_A,
    )


def _inference() -> LeaderPrimaryInference:
    return LeaderPrimaryInference(
        candidate_id=LEADER_CANDIDATE_IDS[0],
        available=True,
        sample_count=40,
        estimate=0.01,
        raw_p_value=0.001,
        holm_adjusted_p_value=0.003,
        simultaneous_confidence=0.9833,
        simultaneous_interval=(0.002, 0.018),
        block_sessions=5,
        resamples=2_000,
        method=LEADER_MULTIPLICITY_METHOD,
        inference_hash=HASH_B,
    )


def _hard_gates(**overrides) -> LeaderHardGateFacts:
    facts = LeaderHardGateFacts(
        decision_data_coverage_ratio=0.96,
        score_coverage_ratio=0.96,
        eligible_point_in_time_sessions=252,
        completed_walk_forward_folds=3,
        fold_sign_stable=True,
        regime_sign_stable=True,
        residual_incremental_alpha_positive=True,
        maximum_concentration=0.30,
        holdout_consumed=True,
    )
    return replace(facts, **overrides)


def test_only_primary_and_all_hard_gates_can_become_proposal_eligible() -> None:
    eligible = evaluate_leader_candidate(
        primary_result=_primary_result(),
        inference=_inference(),
        hard_gates=_hard_gates(),
    )
    rejected = evaluate_leader_candidate(
        primary_result=_primary_result(),
        inference=_inference(),
        hard_gates=_hard_gates(residual_incremental_alpha_positive=False),
    )

    assert eligible.status == "eligible_for_v4_proposal"
    assert eligible.production_mutation_allowed is False
    assert rejected.status == "rejected"
    assert "residual_incremental_alpha" in rejected.failed_gates
    with pytest.raises(ValueError, match="primary endpoint"):
        evaluate_leader_candidate(
            primary_result=_primary_result(endpoint_role="exploratory"),
            inference=_inference(),
            hard_gates=_hard_gates(),
        )


def test_data_shortage_is_insufficient_and_pre_holdout_result_is_unconfirmed() -> None:
    insufficient = evaluate_leader_candidate(
        primary_result=replace(_primary_result(), independent_dates=()),
        inference=replace(_inference(), simultaneous_interval=None),
        hard_gates=_hard_gates(
            eligible_point_in_time_sessions=100,
            completed_walk_forward_folds=1,
            holdout_consumed=False,
        ),
    )
    unconfirmed = evaluate_leader_candidate(
        primary_result=_primary_result(),
        inference=_inference(),
        hard_gates=_hard_gates(holdout_consumed=False),
    )

    assert insufficient.status == "insufficient_data"
    assert unconfirmed.status == "unconfirmed"


def test_diagnostics_report_scarcity_overlap_slices_and_concentration() -> None:
    signal_date = date(2026, 1, 1)
    rows = tuple(
        LeaderCandidateObservation(
            asset_code=f"51000{index}",
            signal_date=signal_date,
            candidate_id=candidate_id,
            availability="available",
            qualifies=True,
            score=0.5 + index / 100,
            components=(),
            gate_reasons=(),
            unavailable_reasons=(),
            baseline_score=0.4,
            peer_group="technology",
            clone_group=f"clone-{index}",
            tracked_index="index-tech",
            issuer="issuer-a" if index < 4 else "issuer-b",
            theme="人工智能",
            sector="科技",
            history_tier="full_history_context",
            source_cutoff=datetime(2026, 1, 1, 15),
            feature_hash=HASH_A,
        )
        for candidate_id in LEADER_CANDIDATE_IDS
        for index in range(5)
    )
    panel = LeaderFeaturePanel(
        signal_date=signal_date,
        source_cutoff=datetime(2026, 1, 1, 15),
        observations=rows,
        panel_hash=HASH_B,
    )
    controls = tuple(
        LeaderControlObservation(
            signal_date=signal_date,
            asset_code=f"51000{index}",
            candidate_id=candidate_id,
            candidate_score=0.5 + index / 100,
            outcome_5_session=0.01 * index,
            momentum=0.1 * index,
            sector_trend=0.2 * index,
            risk=-0.1 * index,
            liquidity=0.3 * index,
            overextension=0.05 * index,
            fold_id="fold-1",
            regime="risk_on",
            peer_group="technology",
            history_tier="full_history_context",
        )
        for candidate_id in LEADER_CANDIDATE_IDS
        for index in range(5)
    )
    calendar = tuple(date(2026, 1, 1) + timedelta(days=index) for index in range(40))
    endpoints = evaluate_leader_endpoints(
        _panels((calendar[0], calendar[7], calendar[14])),
        trading_sessions=calendar,
    )
    diagnostics = leader_factor_diagnostics(
        feature_panels=(panel,),
        endpoint_results=endpoints,
        controls=controls,
    )

    breakout = diagnostics[LEADER_CANDIDATE_IDS[0]]
    assert breakout["signal_frequency"] == 1.0
    assert breakout["qualifying_count"] == 5
    assert set(breakout["control_spearman"]) == {
        "momentum",
        "sector_trend",
        "risk",
        "liquidity",
        "overextension",
    }
    assert set(breakout["stability_slices"]) == {
        "fold_id",
        "regime",
        "peer_group",
        "history_tier",
        "asset_code",
    }
    assert breakout["concentration"]["issuer"] == 0.8
