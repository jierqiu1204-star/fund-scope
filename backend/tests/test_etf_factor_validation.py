from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from app.services.strategy_lab.etf_factor_experiment import (
    PRIMARY_ENDPOINT,
    FactorExperimentContractError,
    HoldoutConsumption,
    consume_holdout_once,
)
from app.services.strategy_lab.etf_factor_validation import (
    PromotionMetrics,
    evaluate_promotion,
    expanding_walk_forward_folds,
    freeze_holdout_authorization,
    holm_bonferroni,
    moving_block_bootstrap_interval,
    moving_block_bootstrap_positive_p_value,
    split_role,
    verify_holdout_authorization,
)
from tests.test_etf_factor_experiment_contract import _manifest


def _sessions(count: int = 120) -> tuple[date, ...]:
    start = date(2025, 1, 1)
    return tuple(start + timedelta(days=offset) for offset in range(count))


def test_walk_forward_is_chronological_purged_and_embargoed() -> None:
    sessions = _sessions()
    manifest = replace(
        _manifest(),
        split=replace(
            _manifest().split,
            development_start=sessions[0],
            development_end=sessions[49],
            validation_start=sessions[50],
            validation_end=sessions[99],
            holdout_start=sessions[100],
            holdout_end=sessions[119],
        )
    )

    folds = expanding_walk_forward_folds(sessions, manifest.split, fold_sessions=10)

    assert split_role(sessions[0], manifest.split) == "development"
    assert split_role(sessions[70], manifest.split) == "validation"
    assert split_role(sessions[110], manifest.split) == "holdout"
    assert len(folds) == 5
    for fold in folds:
        assert max(fold.train_dates) < min(fold.embargoed_dates)
        assert max(fold.embargoed_dates) < min(fold.purged_dates)
        assert max(fold.purged_dates) < min(fold.test_dates)
        assert len(fold.embargoed_dates) == 10
        assert len(fold.purged_dates) == 10


def test_block_bootstrap_and_holm_are_deterministic() -> None:
    values = tuple(0.001 + (offset % 5) * 0.0001 for offset in range(80))
    first = moving_block_bootstrap_interval(
        values, block_sessions=10, resamples=200, seed=7
    )
    second = moving_block_bootstrap_interval(
        values, block_sessions=10, resamples=200, seed=7
    )

    assert first == second
    assert first.lower > 0
    assert holm_bonferroni((0.01, 0.03, 0.04)) == pytest.approx(
        (0.03, 0.06, 0.06)
    )
    with pytest.raises(ValueError, match="one to three"):
        holm_bonferroni((0.01, 0.02, 0.03, 0.04))


def test_block_bootstrap_positive_p_value_is_deterministic() -> None:
    positive = moving_block_bootstrap_positive_p_value(
        (0.01,) * 30,
        block_sessions=3,
        resamples=100,
    )
    neutral = moving_block_bootstrap_positive_p_value(
        (0.0,) * 30,
        block_sessions=3,
        resamples=100,
    )

    assert positive == pytest.approx(1 / 101)
    assert neutral == 1.0


def _passing_metrics() -> PromotionMetrics:
    return PromotionMetrics(
        endpoint=PRIMARY_ENDPOINT,
        adjusted_primary_lower_bound=0.001,
        stable_fold_ratio=0.8,
        stable_regime_ratio=0.8,
        common_support_coverage=0.9,
        turnover_deterioration=0.01,
        drawdown_deterioration=0.01,
        maximum_concentration=0.15,
        exclusion_rate=0.01,
    )


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    (
        ("adjusted_primary_lower_bound", -1.0, "adjusted_primary_lower_bound"),
        ("stable_fold_ratio", 0.0, "fold_stability"),
        ("stable_regime_ratio", 0.0, "regime_stability"),
        ("common_support_coverage", 0.0, "common_support_coverage"),
        ("turnover_deterioration", 1.0, "turnover"),
        ("drawdown_deterioration", 1.0, "drawdown"),
        ("maximum_concentration", 1.0, "concentration"),
        ("exclusion_rate", 1.0, "exclusions"),
    ),
)
def test_every_frozen_gate_is_required(field: str, value: float, failure: str) -> None:
    decision = evaluate_promotion(
        _manifest(), replace(_passing_metrics(), **{field: value})
    )

    assert decision.state == "retain_current_ranking"
    assert failure in decision.failed_gates


def test_secondary_result_cannot_replace_primary_endpoint() -> None:
    decision = evaluate_promotion(
        _manifest(),
        replace(_passing_metrics(), endpoint="top20_10_session_secondary"),
    )

    assert not decision.passed
    assert decision.endpoint == PRIMARY_ENDPOINT
    assert decision.failed_gates == ("primary_endpoint",)


def test_holdout_freeze_and_single_consumption_reject_retries() -> None:
    manifest = _manifest()
    authorization = freeze_holdout_authorization(
        manifest, frozen_non_holdout_evidence_hash="validation-evidence-v1"
    )
    verify_holdout_authorization(
        authorization,
        manifest,
        frozen_non_holdout_evidence_hash="validation-evidence-v1",
    )
    with pytest.raises(FactorExperimentContractError, match="changed after freeze"):
        verify_holdout_authorization(
            authorization,
            replace(manifest, code_version="different"),
            frozen_non_holdout_evidence_hash="validation-evidence-v1",
        )

    record = HoldoutConsumption(
        manifest_hash=manifest.manifest_hash,
        frozen_non_holdout_evidence_hash="validation-evidence-v1",
    )
    consumed = consume_holdout_once(
        record,
        manifest_hash=manifest.manifest_hash,
        frozen_non_holdout_evidence_hash="validation-evidence-v1",
        holdout_evidence_hash="holdout-v1",
        consumed_at=datetime(2026, 7, 19),
    )
    with pytest.raises(FactorExperimentContractError, match="already consumed"):
        consume_holdout_once(
            consumed,
            manifest_hash=manifest.manifest_hash,
            frozen_non_holdout_evidence_hash="validation-evidence-v1",
            holdout_evidence_hash="outcome-driven-retry",
            consumed_at=datetime(2026, 7, 20),
        )
