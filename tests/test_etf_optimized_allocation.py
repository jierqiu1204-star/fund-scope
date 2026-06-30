from datetime import date

from app.services.short_research.optimized_allocation import (
    OptimizerCandidate,
    cap_theme_weights,
    optimized_method_weights,
)


def _candidate(code: str, theme: str, volatility: float) -> OptimizerCandidate:
    return OptimizerCandidate(
        code=code,
        name=code,
        score=80.0,
        theme_group=theme,
        data_date=date(2026, 6, 30),
        expected_return=0.03,
        volatility=volatility,
    )


def test_cap_theme_weights_respects_single_and_theme_caps() -> None:
    weights = cap_theme_weights(
        {"A": 0.5, "B": 0.3, "C": 0.15, "D": 0.05},
        {"A": "科技", "B": "科技", "C": "金融", "D": "金融"},
        single_cap=0.3,
        theme_cap=0.6,
    )

    assert weights is not None
    assert round(sum(weights.values()), 4) == 1.0
    assert max(weights.values()) <= 0.3
    assert round(weights["A"] + weights["B"], 4) <= 0.6


def test_cap_theme_weights_returns_none_when_theme_capacity_is_not_enough() -> None:
    weights = cap_theme_weights(
        {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1},
        {"A": "科技", "B": "科技", "C": "科技", "D": "科技"},
        single_cap=0.3,
        theme_cap=0.6,
    )

    assert weights is None


def test_optimized_method_requires_enough_candidates() -> None:
    candidates = [_candidate("A", "科技", 0.02), _candidate("B", "金融", 0.03)]

    assert optimized_method_weights("risk_parity", candidates) is None


def test_risk_parity_gives_lower_volatility_asset_larger_weight() -> None:
    candidates = [
        _candidate("A", "科技", 0.01),
        _candidate("B", "金融", 0.02),
        _candidate("C", "消费", 0.03),
        _candidate("D", "红利", 0.04),
    ]

    weights = optimized_method_weights("risk_parity", candidates)

    assert weights is not None
    assert round(sum(weights.values()), 4) == 1.0
    assert weights["A"] > weights["D"]
