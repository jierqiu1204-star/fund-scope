from __future__ import annotations

from app.services.strategy_lab.dual_universe_leader_tactics_v2_validation import (
    V2PromotionEvidence,
    validate_v2_promotion_evidence,
)


def test_deeper_negative_drawdown_is_rejected_by_absolute_degradation() -> None:
    evidence = V2PromotionEvidence(
        candidate_id="leader_breakout_proxy_v2",
        factual_pit_sessions=252,
        non_overlapping_primary_dates=40,
        walk_forward_folds=3,
        bootstrap_lower_bound_95_holm=0.001,
        max_drawdown=-0.25,
        baseline_max_drawdown=-0.20,
        coverage=0.95,
        non_finite_exclusions=0,
        raw_price_violations=0,
        clone_violations=0,
        holdout_status="success",
        holdout_access_count=1,
        turnover=0.1,
        concentration=0.2,
        regime_dependence=0.2,
    )
    assert "drawdown_degradation_exceeds_two_percent" in validate_v2_promotion_evidence(evidence)


def test_promotion_contract_rejects_substituted_primary_or_split() -> None:
    evidence = V2PromotionEvidence(
        candidate_id="leader_breakout_proxy_v2",
        factual_pit_sessions=252,
        non_overlapping_primary_dates=40,
        walk_forward_folds=3,
        bootstrap_lower_bound_95_holm=0.001,
        max_drawdown=-0.20,
        baseline_max_drawdown=-0.20,
        coverage=0.95,
        non_finite_exclusions=0,
        raw_price_violations=0,
        clone_violations=0,
        holdout_status="success",
        holdout_access_count=1,
        turnover=0.1,
        concentration=0.2,
        regime_dependence=0.2,
        purge_sessions=9,
        primary_endpoint="top20_accuracy",
        holm_adjusted=False,
    )
    failures = validate_v2_promotion_evidence(evidence)
    assert {
        "purge_contract_incompatible",
        "primary_endpoint_substituted",
        "holm_adjustment_missing",
    } <= set(failures)
