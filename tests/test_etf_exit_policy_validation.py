from datetime import date, timedelta

from app.schemas.short_research import EtfExitHyperoptRequest
from app.services.short_research.etf_exit_hyperopt import (
    DEFAULT_LIVE_PARAMS,
    EXECUTION_MODEL_DAILY_CLOSE,
    HyperoptPricePoint,
    HyperoptSeries,
    best_candidate_for_bucket,
    policy_evidence_level,
    simulate_intraday_exit_rule,
)


def _points(values: list[float]) -> list[HyperoptPricePoint]:
    start = date(2026, 1, 1)
    return [
        HyperoptPricePoint(trade_date=start + timedelta(days=index), close=value)
        for index, value in enumerate(values)
    ]


def test_hyperopt_request_defaults_to_full_universe_batches() -> None:
    request = EtfExitHyperoptRequest()

    assert request.universe_scope == "all_eligible"
    assert request.batch_size == 100


def test_policy_validation_compares_hold_default_and_candidate() -> None:
    trend = [1 + index * 0.002 for index in range(90)]
    pullbacks = [value * (0.96 if index in {35, 58, 73} else 1.0) for index, value in enumerate(trend)]
    series = [
        HyperoptSeries(
            code="510300",
            name="沪深300ETF",
            asset_bucket="equity",
            theme_group="broad",
            points=_points(pullbacks),
        )
    ]

    candidate = best_candidate_for_bucket(
        series,
        execution_model=EXECUTION_MODEL_DAILY_CLOSE,
        manual_delay_minutes=3,
    )

    validation = candidate["confidence"]["policy_validation"]
    assert validation["version"] == "exit_policy_validation_v2"
    assert set(validation["policies"]) == {"hold_baseline", "current_default", "candidate_params"}
    assert validation["policies"]["current_default"]["parameters"] == DEFAULT_LIVE_PARAMS
    assert validation["policies"]["candidate_params"]["parameters"] == candidate["params"]
    assert validation["policies"]["hold_baseline"]["metrics"]["trade_count"] == 0


def test_policy_evidence_level_uses_trade_count_rolling_stability_and_baseline() -> None:
    assert (
        policy_evidence_level(
            {"trade_count": 30},
            {"window_count": 8, "stable_window_rate": 0.65},
            {"beats_baseline": True},
        )
        == "high"
    )
    assert (
        policy_evidence_level(
            {"trade_count": 12},
            {"window_count": 4, "stable_window_rate": 0.45},
            {"beats_baseline": True},
        )
        == "medium"
    )
    assert (
        policy_evidence_level(
            {"trade_count": 30},
            {"window_count": 8, "stable_window_rate": 0.65},
            {"beats_baseline": False},
        )
        == "low"
    )


def test_intraday_policy_validation_does_not_fallback_to_daily_close() -> None:
    result = simulate_intraday_exit_rule(
        [],
        DEFAULT_LIVE_PARAMS,
        asset_bucket="equity",
        daily_points=_points([1.0 + index * 0.01 for index in range(60)]),
        allow_daily_fallback=True,
    )

    assert result["execution_model"] == "intraday_alert"
    assert result["source_reliability"] == "unavailable"
    assert result["missing_intraday_evidence_count"] == 1
    assert result["sample_count"] == 0
