from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.strategy_lab.etf_factor_diagnostics import (
    FactorObservation,
    cross_sectional_factor_diagnostics,
    history_tier_diagnostics,
    portfolio_diagnostics,
    redundancy_and_marginal_diagnostics,
    residualize_sector_trend,
    spearman,
)


def _rows() -> list[FactorObservation]:
    rows = []
    for day in range(4):
        for index in range(20):
            value = float(index)
            rows.append(
                FactorObservation(
                    signal_date=date(2026, 1, 1) + timedelta(days=day),
                    asset_code=f"51{index:04d}",
                    peer_bucket="broad-equity",
                    history_tier=(
                        "standard_history" if index < 10 else "full_history_context"
                    ),
                    factor_value=value + day / 10,
                    baseline_value=value,
                    technical_momentum=value * 2,
                    outcomes={
                        1: value / 1000,
                        3: value / 500,
                        5: value / 250,
                        10: value / 100,
                    },
                )
            )
    return rows


def test_cross_sectional_ic_quantiles_and_spread_are_deterministic() -> None:
    result = cross_sectional_factor_diagnostics(_rows())

    assert result["peer_count_min"] == 20
    assert result["horizons"]["5"]["ic_mean"] == pytest.approx(1.0)
    assert result["horizons"]["5"]["sign_consistency"] == 1.0
    assert result["horizons"]["5"]["date_bucket_count"] == 4
    assert result["horizons"]["5"]["top_minus_bottom_spread"] > 0


def test_portfolio_metrics_include_cost_turnover_churn_drawdown_and_concentration() -> None:
    result = portfolio_diagnostics(
        _rows(),
        score_attr="factor_value",
        horizon=5,
        round_trip_cost=0.001,
        input_count=100,
    )

    assert set(result["top_n"]) == {"5", "10", "20"}
    assert result["top_n"]["10"]["gross_return_mean"] > result["top_n"]["10"]["net_return_mean"]
    assert result["top_n"]["10"]["turnover_mean"] >= 0
    assert result["top_n"]["10"]["maximum_single_weight"] == 0.1
    assert result["exclusion_rate"] == pytest.approx(0.2)


def test_redundancy_residualization_and_history_tiers_are_explicit() -> None:
    rows = _rows()
    redundancy = redundancy_and_marginal_diagnostics(rows)
    residuals = residualize_sector_trend(rows)
    tiers = history_tier_diagnostics(rows)

    assert redundancy["factor_baseline_spearman"] > 0.99
    assert abs(float(redundancy["marginal_residual_spearman"])) < 0.25
    assert max(abs(value) for value in residuals.values()) < 1e-9
    assert tiers["standard_history"]["sample_count"] == 40
    assert tiers["full_history_context"]["sample_count"] == 40
    assert tiers["stable_across_history_tiers"] is True


def test_spearman_uses_average_tie_ranks() -> None:
    assert spearman([1, 1, 2, 3], [1, 1, 2, 3]) == pytest.approx(1.0)
