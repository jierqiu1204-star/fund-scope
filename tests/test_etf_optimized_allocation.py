from datetime import date
from types import SimpleNamespace

from app.services.portfolio_allocation import portfolio_risk_budget_manifest
from app.services.short_research.optimized_allocation import (
    OptimizerCandidate,
    _source_portfolio_risk_policy,
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


def test_cap_theme_weights_leaves_cash_when_theme_capacity_is_not_enough() -> None:
    weights = cap_theme_weights(
        {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1},
        {"A": "科技", "B": "科技", "C": "科技", "D": "科技"},
        single_cap=0.3,
        theme_cap=0.6,
    )

    assert weights is not None
    assert round(sum(weights.values()), 4) == 0.6
    assert max(weights.values()) <= 0.3


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


def test_equal_weight_does_not_degenerate_to_sequential_caps() -> None:
    candidates = [
        _candidate("A", "科技", 0.02),
        _candidate("B", "金融", 0.02),
        _candidate("C", "消费", 0.02),
        _candidate("D", "红利", 0.02),
    ]

    weights = optimized_method_weights("equal_weight", candidates)

    assert weights == {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}


def test_optimized_policy_inherits_source_market_state_and_cash_target() -> None:
    manifest = portfolio_risk_budget_manifest()
    snapshot = SimpleNamespace(
        summary_json={
            "market_regime": "neutral",
            "target_invested_weight": 0.6,
            "constraints_used": {
                "risk_budget_version": "portfolio_risk_budget_v1",
                "risk_budget_hash": manifest["contract_hash"],
                "market_risk_status": "ready",
            },
            "risk_summary": {"market_state_metrics": {"breadth_ratio": 0.5}},
            "allocation_contract": {"contract_hash": "source-allocation"},
        }
    )

    policy = _source_portfolio_risk_policy(snapshot)

    assert policy.status == "ready"
    assert policy.market_risk.state == "neutral"
    assert policy.max_total_exposure == 0.6
    assert policy.source_allocation_contract_hash == "source-allocation"


def test_optimized_policy_does_not_fallback_when_source_risk_policy_is_missing() -> None:
    policy = _source_portfolio_risk_policy(None)

    assert policy.status == "unavailable"
    assert policy.max_total_exposure == 0.0
    assert policy.market_risk.state == "cash_wait"
