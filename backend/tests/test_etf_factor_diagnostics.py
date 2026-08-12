from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.strategy_lab.etf_factor_diagnostics import (
    FactorObservation,
    FactorVectorObservation,
    cross_sectional_factor_diagnostics,
    factor_redundancy_diagnostics,
    history_tier_diagnostics,
    portfolio_diagnostics,
    redundancy_and_marginal_diagnostics,
    residualize_sector_trend,
    shadow_peer_support_report,
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
                    history_tier=("standard_history" if index < 10 else "full_history_context"),
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
    assert result["top_n"]["10"]["membership_turnover_mean"] >= 0
    assert result["top_n"]["10"]["weight_turnover_mean"] >= 0
    assert result["top_n"]["10"]["maximum_single_weight"] == 0.1
    assert result["exclusion_rate"] == pytest.approx(0.2)


def test_redundancy_residualization_and_history_tiers_are_explicit() -> None:
    rows = _rows()
    redundancy = redundancy_and_marginal_diagnostics(rows)
    residuals = residualize_sector_trend(rows)
    tiers = history_tier_diagnostics(rows)

    assert redundancy["factor_baseline_spearman"] > 0.99
    assert abs(float(redundancy["marginal_residual_spearman"])) < 0.25
    assert redundancy["residual_ic"] == redundancy["marginal_residual_spearman"]
    assert redundancy["common_support_observation_count"] == 80
    assert redundancy["common_support_date_count"] == 4
    assert max(abs(value) for value in residuals.values()) < 1e-9
    assert tiers["standard_history"]["sample_count"] == 40
    assert tiers["full_history_context"]["sample_count"] == 40
    assert tiers["stable_across_history_tiers"] is True


def test_spearman_uses_average_tie_ranks() -> None:
    assert spearman([1, 1, 2, 3], [1, 1, 2, 3]) == pytest.approx(1.0)


def test_primary_outcome_sampling_does_not_erase_daily_turnover_or_rank_churn() -> None:
    start = date(2026, 2, 2)
    ordered_codes = (
        ("A", "B", "C"),
        ("B", "A", "C"),
        ("C", "A", "B"),
        ("C", "B", "A"),
        ("D", "E", "F"),
        ("D", "B", "C"),
    )
    rows: list[FactorObservation] = []
    for offset, codes in enumerate(ordered_codes):
        signal_date = start + timedelta(days=offset)
        for rank, code in enumerate(codes):
            rows.append(
                FactorObservation(
                    signal_date=signal_date,
                    asset_code=code,
                    peer_bucket="broad-equity",
                    history_tier="standard_history",
                    factor_value=float(3 - rank),
                    baseline_value=float(3 - rank),
                    technical_momentum=float(3 - rank),
                    outcomes={5: 0.01 if offset in {0, 5} else None},
                    portfolio_weight=(0.8 if rank == 0 else 0.2 if rank == 1 else 0.0),
                )
            )

    result = portfolio_diagnostics(
        rows,
        score_attr="factor_value",
        horizon=5,
        top_ns=(2,),
        round_trip_cost=0.001,
        primary_dates=(start, start + timedelta(days=5)),
        weight_attr="portfolio_weight",
    )
    metrics = result["top_n"]["2"]

    assert metrics["date_count"] == 2
    assert metrics["daily_transition_count"] == 5
    assert metrics["common_member_rank_churn_transition_count"] == 4
    assert metrics["no_common_member_transition_count"] == 1
    assert metrics["daily_transitions"][0]["membership_turnover"] == 0.0
    assert metrics["daily_transitions"][0]["normalized_rank_churn"] == 1.0
    assert metrics["daily_transitions"][0]["weight_turnover"] == pytest.approx(0.6)
    assert result["primary_dates_are_outcome_only"] is True


def _factor_vectors(
    count: int,
    *,
    collapse_first_clone: bool = False,
) -> list[FactorVectorObservation]:
    return [
        FactorVectorObservation(
            signal_date=date(2026, 3, 2),
            asset_code=f"51{index:04d}",
            peer_bucket="broad-equity",
            factor_values={
                "baseline": float(index),
                "residual_momentum": float(index * 2),
                "breadth": float((index * 7) % max(count, 1)),
            },
            clone_group_id=(
                "same-underlying"
                if collapse_first_clone and index in {0, 1}
                else f"underlying-{index}"
            ),
        )
        for index in range(count)
    ]


def test_shadow_peer_support_uses_non_clone_common_support() -> None:
    report = shadow_peer_support_report(
        _factor_vectors(20, collapse_first_clone=True),
        factor_ids=("baseline", "residual_momentum", "breadth"),
    )

    unit = report["units"][0]
    assert unit["non_clone_peer_count"] == 19
    assert unit["weakest_required_primitive_peer_count"] == 19
    assert unit["common_support_count"] == 19
    assert unit["eligible"] is False
    assert unit["unavailable_reason"] == "insufficient_common_peer_support"


def test_redundancy_clusters_are_same_date_deterministic_and_fail_closed() -> None:
    rows = _factor_vectors(20)
    first = factor_redundancy_diagnostics(
        rows,
        factor_ids=("baseline", "residual_momentum", "breadth"),
        baseline_factor_id="baseline",
        residual_evidence={
            "residual_momentum": {
                "residual_ic": 0.04,
                "common_support_marginal_contribution": 0.02,
            }
        },
    )
    second = factor_redundancy_diagnostics(
        list(reversed(rows)),
        factor_ids=("breadth", "residual_momentum", "baseline"),
        baseline_factor_id="baseline",
        residual_evidence={
            "residual_momentum": {
                "residual_ic": 0.04,
                "common_support_marginal_contribution": 0.02,
            }
        },
    )

    assert first == second
    assert first["state"] == "available"
    assert first["effective_factor_count"] == 2
    assert any(
        cluster["factor_ids"] == ["baseline", "residual_momentum"]
        for cluster in first["correlation_clusters"]
    )
    assert first["residual_evidence"]["residual_momentum"]["residual_ic"] == 0.04
    assert first["production_mutation_allowed"] is False

    sparse = factor_redundancy_diagnostics(
        _factor_vectors(19),
        factor_ids=("baseline", "residual_momentum", "breadth"),
        baseline_factor_id="baseline",
    )
    assert sparse["state"] == "insufficient_data"
    assert sparse["unavailable_reason"] == "insufficient_common_peer_support"
    assert all(pair["aggregate_spearman"] is None for pair in sparse["pairwise_spearman"])
