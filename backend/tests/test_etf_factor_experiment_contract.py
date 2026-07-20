from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest

from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_surfaces import actionable_rank_manifest
from app.services.strategy_lab.etf_factor_experiment import (
    PRIMARY_ENDPOINT,
    ChronologicalSplit,
    ExecutionCostPolicy,
    FactorCandidate,
    FactorExperimentContractError,
    FactorExperimentManifest,
    HoldoutConsumption,
    PromotionGates,
    consume_holdout_once,
    register_factor_experiment,
)


def _candidate(candidate_id: str = "sector_residual_v1") -> FactorCandidate:
    return FactorCandidate(
        candidate_id=candidate_id,
        formula="rank(sector_trend_residual)",
        direction="higher_is_better",
        transform="cross_sectional_percentile",
        missing_value_rule="exclude",
        peer_bucket="broad-equity",
        minimum_peer_count=20,
        required_history_sessions=120,
    )


def _manifest() -> FactorExperimentManifest:
    research = daily_reconstructable_manifest()
    actionable = actionable_rank_manifest()
    return FactorExperimentManifest(
        experiment_name="etf_incremental_alpha_2026q3",
        code_version="test-code-v1",
        baseline_contract_id=research.contract_id,
        baseline_contract_hash=research.manifest_hash,
        baseline_score_field=research.score_field,
        research_surface_contract_hash=research.manifest_hash,
        actionable_surface_contract_hash=actionable.manifest_hash,
        candidates=(_candidate(),),
        universe_policy="historical_point_in_time_membership",
        adjusted_data_policy="decision_eligible_total_return_adjusted_only",
        source_cutoff_policy="source_timestamp_lte_signal_cutoff",
        peer_buckets=("broad-equity",),
        horizons=(1, 3, 5, 10),
        top_ns=(5, 10, 20),
        execution_cost=ExecutionCostPolicy(
            entry_rule="t_plus_1_adjusted_close",
            exit_rule="declared_horizon_adjusted_close",
            fee_bps_per_side=1.0,
            slippage_bps_per_side=2.0,
            turnover_charge_rule="two_sided_realized_turnover",
        ),
        split=ChronologicalSplit(
            development_start=date(2023, 1, 1),
            development_end=date(2024, 6, 30),
            validation_start=date(2024, 7, 11),
            validation_end=date(2025, 6, 30),
            holdout_start=date(2025, 7, 11),
            holdout_end=date(2026, 6, 30),
        ),
        exclusion_rules=(
            "missing_historical_membership",
            "ineligible_adjusted_provenance",
            "non_finite_factor",
            "pending_outcome_window",
        ),
        uncertainty_method="date_moving_block_bootstrap",
        bootstrap_block_sessions=10,
        multiplicity_method="holm_bonferroni",
        declared_regimes=("bull", "neutral", "bear"),
        promotion_gates=PromotionGates(
            adjusted_primary_lower_bound_min=0.0,
            minimum_common_support_coverage=0.8,
            minimum_stable_fold_ratio=0.67,
            maximum_turnover_deterioration=0.1,
            maximum_drawdown_deterioration=0.02,
            maximum_concentration=0.3,
            maximum_exclusion_rate=0.2,
        ),
        primary_endpoint=PRIMARY_ENDPOINT,
    )


def test_preregistered_manifest_freezes_ranking_contracts_and_hash() -> None:
    first = register_factor_experiment(
        _manifest(),
        registered_at=datetime(2026, 7, 19, 20, 0),
    )
    second = register_factor_experiment(
        _manifest(),
        registered_at=datetime(2026, 7, 19, 20, 1),
    )

    assert first.manifest.baseline_contract_id == "daily_reconstructable_v1"
    assert first.manifest.baseline_score_field == "research_score"
    assert first.manifest.actionable_surface_contract_hash == actionable_rank_manifest().manifest_hash
    assert first.manifest_hash == second.manifest_hash
    assert first.outcome_read_count == 0


def test_manifest_rejects_more_than_three_candidates_before_outcomes() -> None:
    manifest = replace(
        _manifest(),
        candidates=tuple(_candidate(f"candidate_{index}") for index in range(4)),
    )

    with pytest.raises(FactorExperimentContractError, match="one to three"):
        register_factor_experiment(
            manifest,
            registered_at=datetime(2026, 7, 19, 20, 0),
        )


@pytest.mark.parametrize(
    "changed",
    [
        lambda manifest: replace(
            manifest,
            execution_cost=replace(manifest.execution_cost, slippage_bps_per_side=3.0),
        ),
        lambda manifest: replace(
            manifest,
            promotion_gates=replace(
                manifest.promotion_gates,
                minimum_common_support_coverage=0.85,
            ),
        ),
        lambda manifest: replace(
            manifest,
            candidates=(replace(_candidate(), required_history_sessions=250),),
        ),
    ],
)
def test_material_manifest_edits_create_new_identity(changed) -> None:
    baseline = _manifest()

    assert baseline.manifest_hash != changed(baseline).manifest_hash


def test_holdout_is_consumed_once_and_cannot_cross_manifest_identity() -> None:
    manifest = _manifest()
    record = HoldoutConsumption(
        manifest_hash=manifest.manifest_hash,
        frozen_non_holdout_evidence_hash="a" * 64,
    )
    consumed = consume_holdout_once(
        record,
        manifest_hash=manifest.manifest_hash,
        frozen_non_holdout_evidence_hash="a" * 64,
        holdout_evidence_hash="b" * 64,
        consumed_at=datetime(2026, 7, 19, 20, 0),
    )

    with pytest.raises(FactorExperimentContractError, match="already consumed"):
        consume_holdout_once(
            consumed,
            manifest_hash=manifest.manifest_hash,
            frozen_non_holdout_evidence_hash="a" * 64,
            holdout_evidence_hash="c" * 64,
            consumed_at=datetime(2026, 7, 19, 20, 1),
        )
    with pytest.raises(FactorExperimentContractError, match="manifest identity"):
        consume_holdout_once(
            record,
            manifest_hash="0" * 64,
            frozen_non_holdout_evidence_hash="a" * 64,
            holdout_evidence_hash="c" * 64,
            consumed_at=datetime(2026, 7, 19, 20, 1),
        )
