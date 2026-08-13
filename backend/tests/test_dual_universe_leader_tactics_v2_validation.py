from __future__ import annotations

from datetime import date

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2_validation import (
    SentimentRiskPolicyShadowSample,
    V2PromotionEvidence,
    locked_case_status,
    paired_five_session_net_excess,
    sentiment_risk_policy_shadow_diagnostics,
    source_label,
    validate_v2_promotion_evidence,
)


def test_partial_source_labels_do_not_create_negative_unmentioned_assets() -> None:
    label = source_label(
        article_id="article",
        asset_code="603039",
        label_date=date(2026, 8, 3),
        theme="ai-application",
        label_kind="core",
        observability="observed",
        evidence_hash="a" * 64,
    )
    assert label.label_kind == "core"
    with pytest.raises(ValueError):
        source_label(
            article_id="article",
            asset_code=None,
            label_date=None,
            theme=None,
            label_kind="positive",
            observability="observed",
            evidence_hash="a" * 64,
        )


def test_paired_endpoint_uses_common_support_and_non_zero_costs() -> None:
    outcomes = paired_five_session_net_excess(
        {date(2026, 8, 1): 0.10, date(2026, 8, 2): 0.05},
        {date(2026, 8, 1): 0.02},
    )
    assert len(outcomes) == 1
    assert outcomes[0].candidate_net_return < outcomes[0].candidate_gross_return
    assert outcomes[0].net_excess_return > 0


def test_promotion_hard_gates_remain_insufficient_until_all_pass() -> None:
    evidence = V2PromotionEvidence(
        candidate_id="leader_breakout_proxy_v2",
        factual_pit_sessions=61,
        non_overlapping_primary_dates=10,
        walk_forward_folds=2,
        bootstrap_lower_bound_95_holm=None,
        max_drawdown=-0.2,
        baseline_max_drawdown=-0.2,
        coverage=0.90,
        non_finite_exclusions=0,
        raw_price_violations=0,
        clone_violations=0,
        holdout_status="unused",
        holdout_access_count=0,
        turnover=0.2,
        concentration=0.3,
        regime_dependence=0.4,
    )
    failures = validate_v2_promotion_evidence(evidence)
    assert "insufficient_factual_pit_sessions" in failures
    assert "primary_bootstrap_lower_bound_not_above_zero" in failures
    assert "holdout_not_successfully_used_once" in failures


def test_sentiment_risk_policy_shadow_keeps_primary_and_reports_tradeoffs() -> None:
    diagnostics = sentiment_risk_policy_shadow_diagnostics(
        (
            SentimentRiskPolicyShadowSample(date(2026, 8, 1), 0.01, "healthy"),
            SentimentRiskPolicyShadowSample(date(2026, 8, 8), -0.03, "warning"),
            SentimentRiskPolicyShadowSample(date(2026, 8, 15), 0.02, "risk_off"),
            SentimentRiskPolicyShadowSample(date(2026, 8, 22), -0.05, "unavailable"),
        )
    )

    assert diagnostics.primary_endpoint == "five_session_paired_net_excess"
    assert diagnostics.policy_mode == "policy_shadow"
    assert diagnostics.status == "insufficient_data"
    assert diagnostics.sample_count == 4
    assert diagnostics.evaluable_count == 3
    assert diagnostics.coverage == pytest.approx(0.75)
    assert diagnostics.avoided_loss == pytest.approx(0.03)
    assert diagnostics.missed_gain == pytest.approx(0.02)
    assert diagnostics.incremental_benefit_mean == pytest.approx((0.03 - 0.02) / 3)
    assert diagnostics.raw_max_drawdown is not None
    assert diagnostics.gated_max_drawdown is not None


def test_sentiment_risk_policy_shadow_rejects_contract_or_endpoint_tuning() -> None:
    with pytest.raises(ValueError, match="contract is not frozen"):
        sentiment_risk_policy_shadow_diagnostics(
            (SentimentRiskPolicyShadowSample(date(2026, 8, 1), 0.01, "healthy", "x" * 64),)
        )
    with pytest.raises(ValueError, match="endpoint was substituted"):
        sentiment_risk_policy_shadow_diagnostics(
            (
                SentimentRiskPolicyShadowSample(
                    date(2026, 8, 1),
                    0.01,
                    "healthy",
                    primary_endpoint="one_session_hit_rate",
                ),
            )
        )


def test_locked_case_is_checked_after_screening_and_can_be_unavailable() -> None:
    assert (
        locked_case_status(
            observed_candidate_codes=("603039", "002131"),
            locked_codes=("603039", "002131"),
            data_available=True,
        )
        == "locked_case_match"
    )
    assert (
        locked_case_status(
            observed_candidate_codes=(),
            locked_codes=("603039", "002131"),
            data_available=False,
        )
        == "locked_case_unavailable"
    )
