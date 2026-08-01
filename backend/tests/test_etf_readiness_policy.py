from __future__ import annotations

import pytest

from app.services.short_research.coverage_policy import (
    ETF_LEGACY_READINESS_POLICY_VERSION,
    ETF_READINESS_POLICY_VERSION,
    evaluate_etf_readiness,
    evaluate_persisted_etf_readiness,
)


@pytest.mark.parametrize(
    ("daily", "warmup", "expected_state", "expected_blockers"),
    [
        (
            0.8999,
            1.0,
            "blocked",
            ("daily_freshness_coverage_below_95pct",),
        ),
        (
            0.9499,
            0.90,
            "blocked",
            ("daily_freshness_coverage_below_95pct",),
        ),
        (
            0.95,
            0.8999,
            "blocked",
            ("history_depth_61_coverage_below_90pct",),
        ),
        (
            0.95,
            0.90,
            "complete",
            (),
        ),
        (
            0.95,
            0.9499,
            "complete",
            (),
        ),
        (0.95, 0.95, "complete", ()),
    ],
)
def test_readiness_policy_threshold_boundaries(
    daily: float,
    warmup: float,
    expected_state: str,
    expected_blockers: tuple[str, ...],
) -> None:
    result = evaluate_etf_readiness(
        daily_coverage_ratio=daily,
        warmup_coverage_ratio=warmup,
    )

    assert result.policy_version == ETF_READINESS_POLICY_VERSION
    assert result.state == expected_state
    assert result.daily_coverage_ratio == daily
    assert result.warmup_coverage_ratio == warmup
    assert result.blocker_reasons == expected_blockers
    assert result.preview_allowed is (expected_state != "blocked")
    assert result.complete_publication_allowed is (expected_state == "complete")


def test_persisted_legacy_policy_is_not_reinterpreted_by_v2() -> None:
    legacy = evaluate_persisted_etf_readiness(
        policy_version=ETF_LEGACY_READINESS_POLICY_VERSION,
        daily_coverage_ratio=0.95,
        warmup_coverage_ratio=0.94,
    )
    current = evaluate_persisted_etf_readiness(
        policy_version=ETF_READINESS_POLICY_VERSION,
        daily_coverage_ratio=0.95,
        warmup_coverage_ratio=0.94,
    )

    assert legacy.state == "degraded"
    assert legacy.complete_publication_allowed is False
    assert current.state == "complete"
    assert current.complete_publication_allowed is True


def test_readiness_policy_reports_all_factual_blockers() -> None:
    result = evaluate_etf_readiness(
        daily_coverage_ratio=None,
        warmup_coverage_ratio=None,
    )

    assert result.state == "blocked"
    assert result.blocker_reasons == (
        "daily_freshness_coverage_below_95pct",
        "history_depth_61_coverage_below_90pct",
    )
    assert result.to_dict() == {
        "policy_version": ETF_READINESS_POLICY_VERSION,
        "state": "blocked",
        "daily_coverage_ratio": 0.0,
        "warmup_coverage_ratio": 0.0,
        "blocker_reasons": [
            "daily_freshness_coverage_below_95pct",
            "history_depth_61_coverage_below_90pct",
        ],
        "preview_allowed": False,
        "complete_publication_allowed": False,
    }
