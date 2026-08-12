from datetime import date, timedelta

import pytest

from app.services.portfolio_allocation import (
    PORTFOLIO_LAYER_DEFENSIVE,
    PORTFOLIO_LAYER_PRIMARY,
    PORTFOLIO_MODE_CASH_WAIT,
    PORTFOLIO_MODE_NEUTRAL,
    MarketRiskObservation,
    apply_portfolio_risk_budget,
    classify_portfolio_market_risk,
    portfolio_asset_risk_cap,
    portfolio_risk_budget_manifest,
    proportional_capped_redistribution,
)


def _return_map(seed: int, *, count: int = 60) -> dict[date, float]:
    start = date(2026, 1, 1)
    return {
        start + timedelta(days=index): ((index * seed) % 17 - 8) / 10000
        for index in range(count)
    }


def _risk_on_state():
    return classify_portfolio_market_risk(
        [
            MarketRiskObservation("510300", 0.05, 0.03, True),
            MarketRiskObservation("510500", 0.03, 0.01, True),
        ]
    )


def test_proportional_cap_keeps_four_equal_weights_equal() -> None:
    result = proportional_capped_redistribution(
        {"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0},
        {"A": 0.30, "B": 0.30, "C": 0.30, "D": 0.30},
    )

    assert result.weights == {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
    assert result.cash_weight == 0.0


def test_proportional_cap_leaves_infeasible_theme_residual_as_cash() -> None:
    result = proportional_capped_redistribution(
        {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0},
        {"A": 0.30, "B": 0.30, "C": 0.30, "D": 0.30},
        group_caps={"theme:科技": (("A", "B", "C", "D"), 0.60)},
    )

    assert sum(result.weights.values()) == pytest.approx(0.60)
    assert result.cash_weight == pytest.approx(0.40)
    assert "theme:科技" in result.binding_constraints
    assert "capacity_residual_to_cash" in result.binding_constraints


def test_asset_reducer_never_raises_a_small_source_cap_and_is_hashed() -> None:
    assert portfolio_asset_risk_cap(base_cap=0.03) == pytest.approx(0.03)
    manifest = portfolio_risk_budget_manifest()
    assert manifest["market_classifier_thresholds"]
    assert manifest["asset_risk_reducer"]
    assert manifest["contract_hash"]


def test_market_state_fails_closed_without_broad_equity_evidence() -> None:
    result = classify_portfolio_market_risk(
        [MarketRiskObservation("512880", 0.08, 0.04, True, broad_equity=False)]
    )

    assert result.status == "unavailable"
    assert result.state == PORTFOLIO_MODE_CASH_WAIT
    assert result.unavailable_reasons == ("insufficient_eligible_broad_equity_evidence",)


def test_neutral_budget_preserves_minimum_cash_and_risk_cap() -> None:
    market_risk = classify_portfolio_market_risk(
        [
            MarketRiskObservation("510300", 0.02, 0.01, True),
            MarketRiskObservation("510500", -0.01, -0.01, True),
        ]
    )
    codes = ("A", "B", "C", "D")
    result = apply_portfolio_risk_budget(
        {code: 1.0 for code in codes},
        layer_by_code={
            "A": PORTFOLIO_LAYER_PRIMARY,
            "B": PORTFOLIO_LAYER_PRIMARY,
            "C": PORTFOLIO_LAYER_DEFENSIVE,
            "D": PORTFOLIO_LAYER_DEFENSIVE,
        },
        theme_by_code={code: code for code in codes},
        returns_by_code={code: _return_map(index + 2) for index, code in enumerate(codes)},
        market_risk=market_risk,
    )

    assert market_risk.state == PORTFOLIO_MODE_NEUTRAL
    assert result.status == "ready"
    assert result.invested_weight == pytest.approx(0.80)
    assert result.cash_weight == pytest.approx(0.20)
    assert result.weights["A"] + result.weights["B"] <= 0.60
    assert result.metrics["common_sample_count"] == 60
    assert result.contract_hash


def test_correlation_cluster_is_capped_at_half() -> None:
    codes = ("A", "B", "C", "D")
    correlated = _return_map(3)
    result = apply_portfolio_risk_budget(
        {"A": 5.0, "B": 3.0, "C": 1.0, "D": 1.0},
        layer_by_code={code: PORTFOLIO_LAYER_PRIMARY for code in codes},
        theme_by_code={code: code for code in codes},
        returns_by_code={
            "A": correlated,
            "B": dict(correlated),
            "C": _return_map(5),
            "D": _return_map(7),
        },
        market_risk=_risk_on_state(),
    )

    assert result.status == "ready"
    assert result.weights["A"] + result.weights["B"] <= 0.50 + 1e-8
    assert max(result.weights.values()) <= 0.30
    assert result.metrics["correlation_cluster_exposure"]


def test_missing_correlation_history_is_deterministically_all_cash() -> None:
    result = apply_portfolio_risk_budget(
        {"A": 1.0, "B": 1.0},
        layer_by_code={"A": PORTFOLIO_LAYER_PRIMARY, "B": PORTFOLIO_LAYER_PRIMARY},
        theme_by_code={"A": "科技", "B": "金融"},
        returns_by_code={"A": _return_map(3)},
        market_risk=_risk_on_state(),
    )

    assert result.status == "unavailable"
    assert result.weights == {}
    assert result.cash_weight == 1.0
    assert "correlation_evidence_insufficient" in result.unavailable_reasons
