from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.services.portfolio_risk_shadow import (
    PORTFOLIO_RISK_FACTOR_NAMES,
    MarketRiskPoolObservation,
    PortfolioRiskAssetInput,
    build_portfolio_risk_shadow,
    calculate_factor_betas,
    calculate_marginal_risk_contribution,
    calculate_portfolio_exposures,
    evaluate_market_risk_regime_shadow,
    portfolio_risk_shadow_manifest,
    run_fixed_stress_scenarios,
    select_independent_market_risk_pool,
)


def _asset(
    code: str,
    weight: float,
    *,
    clone: str | None = None,
    theme: str | None = "technology",
    bucket: str | None = "equity",
) -> PortfolioRiskAssetInput:
    return PortfolioRiskAssetInput(
        code=code,
        weight=weight,
        clone_group_id=clone or f"clone:{code}",
        theme_group=theme,
        asset_bucket=bucket,
    )


def _return_series(seed: int, *, count: int = 80) -> dict[date, float]:
    start = date(2026, 1, 1)
    return {
        start + timedelta(days=index): (((index + 1) * seed) % 17 - 8) / 10_000
        for index in range(count)
    }


def _factor_fixture() -> tuple[dict[date, float], dict[str, dict[date, float]]]:
    start = date(2026, 1, 1)
    portfolio: dict[date, float] = {
        start + timedelta(days=index): 0.123 for index in range(6)
    }
    factors = {name: {} for name in PORTFOLIO_RISK_FACTOR_NAMES}
    for common_index in range(64):
        point_date = start + timedelta(days=common_index + 6)
        market = 0.01 if common_index % 2 == 0 else -0.01
        size = 0.008 if (common_index // 2) % 2 == 0 else -0.008
        growth = 0.006 if (common_index // 4) % 2 == 0 else -0.006
        factors["market"][point_date] = market
        factors["small_vs_large"][point_date] = size
        factors["growth_vs_large"][point_date] = growth
        portfolio[point_date] = 1.5 * market + 0.5 * size - 0.25 * growth
    return portfolio, factors


def test_market_pool_deduplicates_bucket_and_underlying_with_deterministic_fallback() -> None:
    data_date = date(2026, 8, 10)
    cutoff = datetime(2026, 8, 10, 15, 30)
    observations = [
        MarketRiskPoolObservation("A", "large", "index-x", 0.03, 0.01, True, data_date, cutoff, 1),
        MarketRiskPoolObservation("B", "large", "index-w", 0.02, 0.01, True, data_date, cutoff, 2),
        MarketRiskPoolObservation("C", "mid", "index-x", 0.01, 0.00, True, data_date, cutoff, 1),
        MarketRiskPoolObservation("D", "mid", "index-y", 0.01, 0.00, True, data_date, cutoff, 2),
        MarketRiskPoolObservation("E", "small", "index-z", -0.01, -0.01, True, data_date, cutoff, 1),
    ]

    result = select_independent_market_risk_pool(
        list(reversed(observations)),
        required_data_date=data_date,
        required_data_cutoff=cutoff,
    )

    assert result.status == "ready"
    assert [row["code"] for row in result.metrics["selected_observations"]] == ["A", "D", "E"]
    assert result.metrics["selected_bucket_count"] == 3
    assert result.metrics["selected_underlying_count"] == 3
    assert result.metrics["duplicate_underlying_count"] == 1


def test_market_pool_fails_closed_on_cutoff_mismatch_and_insufficient_buckets() -> None:
    data_date = date(2026, 8, 10)
    cutoff = datetime(2026, 8, 10, 15, 30)
    wrong_cutoff = cutoff + timedelta(minutes=1)
    result = select_independent_market_risk_pool(
        [
            MarketRiskPoolObservation("A", "large", "x", 0.03, 0.01, True, data_date, cutoff),
            MarketRiskPoolObservation("B", "mid", "y", 0.02, 0.01, True, data_date, cutoff),
            MarketRiskPoolObservation("C", "small", "z", 0.01, 0.00, True, data_date, wrong_cutoff),
        ],
        required_data_date=data_date,
        required_data_cutoff=cutoff,
    )

    assert result.status == "unavailable"
    assert result.unavailable_reasons == ("insufficient_distinct_market_buckets",)
    assert result.metrics["rejection_counts"]["cutoff_mismatch"] == 1


def test_market_regime_downgrades_immediately_and_recovers_on_two_distinct_sessions() -> None:
    cutoff = datetime(2026, 8, 7, 15, 30)

    def pool(point_date: date, positive: int) -> object:
        return select_independent_market_risk_pool(
            [
                MarketRiskPoolObservation(
                    chr(65 + index),
                    bucket,
                    f"index-{index}",
                    0.01 if index < positive else -0.01,
                    0.01 if index < positive else -0.01,
                    True,
                    point_date,
                    cutoff,
                )
                for index, bucket in enumerate(("large", "mid", "small"))
            ],
            required_data_date=point_date,
            required_data_cutoff=cutoff,
        )

    friday = date(2026, 8, 7)
    monday = date(2026, 8, 10)
    tuesday = date(2026, 8, 11)
    risk_off = evaluate_market_risk_regime_shadow(pool(friday, 1), trade_session=friday)
    first_recovery = evaluate_market_risk_regime_shadow(
        pool(monday, 3),
        trade_session=monday,
        previous_state=risk_off.state,
        previous_evaluated_session=friday,
    )
    repeated = evaluate_market_risk_regime_shadow(
        pool(monday, 3),
        trade_session=monday,
        previous_state=first_recovery.state,
        previous_evaluated_session=monday,
        previous_recovery_sessions=first_recovery.recovery_sessions,
    )
    confirmed = evaluate_market_risk_regime_shadow(
        pool(tuesday, 3),
        trade_session=tuesday,
        previous_state=repeated.state,
        previous_evaluated_session=monday,
        previous_recovery_sessions=repeated.recovery_sessions,
    )

    assert risk_off.state == "defensive"
    assert first_recovery.state == "defensive"
    assert first_recovery.recovery_sessions == (monday,)
    assert repeated.recovery_sessions == (monday,)
    assert confirmed.state == "risk_on"
    assert confirmed.recovery_sessions == (monday, tuesday)


def test_market_regime_fails_closed_when_market_pool_is_unavailable() -> None:
    point_date = date(2026, 8, 10)
    cutoff = datetime(2026, 8, 10, 15, 30)
    unavailable = select_independent_market_risk_pool(
        [],
        required_data_date=point_date,
        required_data_cutoff=cutoff,
    )

    result = evaluate_market_risk_regime_shadow(
        unavailable,
        trade_session=point_date,
        previous_state="risk_on",
    )

    assert result.state == "data_halt"
    assert result.changed is True
    assert result.reason_codes == ("market_risk_pool_unavailable",)


def test_exposure_aggregates_clone_theme_and_asset_bucket_without_counting_cash() -> None:
    result = calculate_portfolio_exposures(
        [
            _asset("A", 0.30, clone="same-index", theme="technology", bucket="equity"),
            _asset("B", 0.20, clone="same-index", theme="technology", bucket="equity"),
            _asset("C", 0.10, clone="bond-index", theme="bond", bucket="bond"),
        ]
    )

    assert result.status == "ready"
    assert result.metrics["invested_weight"] == pytest.approx(0.60)
    assert result.metrics["cash_weight"] == pytest.approx(0.40)
    assert result.metrics["clone_exposure"] == {
        "bond-index": pytest.approx(0.10),
        "same-index": pytest.approx(0.50),
    }
    assert result.metrics["theme_exposure"]["technology"] == pytest.approx(0.50)
    assert result.metrics["asset_bucket_exposure"]["equity"] == pytest.approx(0.50)


def test_exposure_keeps_unknown_weight_visible_and_fails_closed() -> None:
    result = calculate_portfolio_exposures(
        [
            PortfolioRiskAssetInput(
                code="A",
                weight=0.25,
                clone_group_id=None,
                theme_group="unknown",
                asset_bucket="unknown",
            )
        ]
    )

    assert result.status == "unavailable"
    assert result.metrics["unknown_clone_weight"] == pytest.approx(0.25)
    assert result.metrics["unknown_theme_weight"] == pytest.approx(0.25)
    assert result.metrics["unknown_asset_bucket_weight"] == pytest.approx(0.25)
    assert "clone_exposure_incomplete" in result.unavailable_reasons
    assert "theme_exposure_incomplete" in result.unavailable_reasons
    assert "asset_bucket_exposure_incomplete" in result.unavailable_reasons


def test_factor_betas_use_one_shared_date_intersection_for_all_three_factors() -> None:
    portfolio, factors = _factor_fixture()

    result = calculate_factor_betas(portfolio, factors)

    assert result.status == "ready"
    assert result.metrics["common_sample_count"] == 64
    assert result.metrics["betas"] == {
        "market": pytest.approx(1.5),
        "small_vs_large": pytest.approx(0.5),
        "growth_vs_large": pytest.approx(-0.25),
    }


def test_factor_betas_fail_closed_when_any_required_factor_is_missing() -> None:
    portfolio, factors = _factor_fixture()
    factors.pop("growth_vs_large")

    result = calculate_factor_betas(portfolio, factors)

    assert result.status == "unavailable"
    assert result.unavailable_reasons == ("missing_required_factor_returns",)
    assert result.metrics["missing_factors"] == ["growth_vs_large"]


def test_marginal_risk_uses_last_120_common_dates_and_components_sum_to_volatility() -> None:
    start = date(2025, 1, 1)
    returns = {
        "A": {
            start + timedelta(days=index): ((index % 5) - 2) * 0.001
            for index in range(130)
        },
        "B": {
            start + timedelta(days=index): (((index * 2) % 7) - 3) * 0.0008
            for index in range(5, 130)
        },
        "C": {
            start + timedelta(days=index): (((index * 3) % 11) - 5) * 0.0006
            for index in range(10, 130)
        },
    }
    weights = {"A": 0.40, "B": 0.30, "C": 0.20}

    result = calculate_marginal_risk_contribution(weights, returns)
    reordered = calculate_marginal_risk_contribution(
        {"C": 0.20, "A": 0.40, "B": 0.30},
        {"C": returns["C"], "A": returns["A"], "B": returns["B"]},
    )

    assert result.status == "ready"
    assert result.metrics["common_sample_count"] == 120
    assert result.metrics["diagonal_shrinkage"] == pytest.approx(0.25)
    assert result.metrics["component_risk_sum"] == pytest.approx(
        result.metrics["portfolio_annualized_volatility"], abs=1e-10
    )
    assert result.input_hash == reordered.input_hash
    assert result.contract_hash == reordered.contract_hash


def test_marginal_risk_rejects_more_than_twenty_assets_without_truncating() -> None:
    weights = {f"ETF{index:02d}": 1 / 21 for index in range(21)}
    returns = {
        code: _return_series(index + 2, count=70)
        for index, code in enumerate(weights)
    }

    result = calculate_marginal_risk_contribution(weights, returns)

    assert result.status == "unavailable"
    assert "asset_count_exceeds_shadow_cap" in result.unavailable_reasons
    assert result.metrics["contributions"] == {}


def test_marginal_risk_accepts_exactly_twenty_assets_with_bounded_lookback() -> None:
    weights = {f"ETF{index:02d}": 0.04 for index in range(20)}
    returns = {
        code: _return_series(index + 2, count=130)
        for index, code in enumerate(weights)
    }

    result = calculate_marginal_risk_contribution(weights, returns)

    assert result.status == "ready"
    assert result.metrics["asset_count"] == 20
    assert result.metrics["common_sample_count"] == 120
    assert len(result.metrics["contributions"]) == 20


def test_four_fixed_stress_scenarios_are_deterministic() -> None:
    assets = [
        _asset("A", 0.40, theme="technology", bucket="equity"),
        _asset("B", 0.30, theme="financial", bucket="equity"),
        _asset("C", 0.20, theme="bond", bucket="bond"),
        _asset("D", 0.10, theme="money", bucket="money"),
    ]
    returns = {item.code: _return_series(index + 2, count=70) for index, item in enumerate(assets)}

    result = run_fixed_stress_scenarios(
        assets,
        returns,
        previous_weights={"A": 0.30, "B": 0.40, "C": 0.20, "D": 0.10},
    )
    scenarios = result.metrics["scenarios"]

    assert result.status == "ready"
    assert set(scenarios) == {
        "broad_market_-5pct",
        "dominant_theme_-8pct",
        "correlation_one_2sigma",
        "liquidity_cost_3x",
    }
    assert scenarios["broad_market_-5pct"]["portfolio_return"] == pytest.approx(-0.038)
    assert scenarios["dominant_theme_-8pct"]["portfolio_return"] == pytest.approx(-0.039)
    assert scenarios["correlation_one_2sigma"]["portfolio_return"] < 0
    assert scenarios["liquidity_cost_3x"]["portfolio_return"] == pytest.approx(-0.0004)


def test_liquidity_stress_fails_closed_without_previous_weights() -> None:
    assets = [_asset("A", 0.50), _asset("B", 0.40, theme="financial")]
    returns = {item.code: _return_series(index + 2, count=70) for index, item in enumerate(assets)}

    result = run_fixed_stress_scenarios(assets, returns, previous_weights=None)

    assert result.status == "unavailable"
    assert "liquidity_scenario_requires_previous_weights" in result.unavailable_reasons
    assert result.metrics["scenarios"]["liquidity_cost_3x"]["status"] == "unavailable"


def test_composite_shadow_is_order_stable_and_does_not_mutate_inputs() -> None:
    assets = [
        _asset("A", 0.40, theme="technology"),
        _asset("B", 0.30, theme="financial"),
        _asset("C", 0.20, theme="bond", bucket="bond"),
    ]
    returns = {item.code: _return_series(index + 2, count=80) for index, item in enumerate(assets)}
    _portfolio, factors = _factor_fixture()
    previous = {"A": 0.30, "B": 0.30, "C": 0.20}
    original_assets = list(assets)
    original_previous = dict(previous)

    result = build_portfolio_risk_shadow(
        assets,
        returns,
        factors,
        previous_weights=previous,
    )
    reordered = build_portfolio_risk_shadow(
        list(reversed(assets)),
        {code: returns[code] for code in reversed(list(returns))},
        {name: factors[name] for name in reversed(PORTFOLIO_RISK_FACTOR_NAMES)},
        previous_weights={"C": 0.20, "A": 0.30, "B": 0.30},
    )

    assert result.status == "ready"
    assert result.contract_hash == portfolio_risk_shadow_manifest()["contract_hash"]
    assert result.contract_hash == reordered.contract_hash
    assert result.input_hash == reordered.input_hash
    assert assets == original_assets
    assert previous == original_previous


def test_manifest_hash_is_stable_when_a_returned_manifest_is_mutated() -> None:
    original = portfolio_risk_shadow_manifest()
    original_hash = original["contract_hash"]
    original["stress_scenarios"]["broad_market_-5pct"]["bucket_shocks"]["equity"] = 0.99

    refreshed = portfolio_risk_shadow_manifest()

    assert refreshed["contract_hash"] == original_hash
    assert refreshed["stress_scenarios"]["broad_market_-5pct"]["bucket_shocks"]["equity"] == -0.05
