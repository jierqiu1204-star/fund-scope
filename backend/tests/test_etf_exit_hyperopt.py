from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.etf_research_evidence import build_exit_calibration_contract
from app.services.short_research import jobs as jobs_module
from app.services.short_research.etf_exit_hyperopt import (
    CONCLUSION_INSUFFICIENT,
    CONCLUSION_OVERFIT,
    CONCLUSION_REJECTED,
    DEFAULT_SEARCH_SPACE,
    EXECUTION_MODEL_DAILY_CLOSE,
    EXECUTION_MODEL_INTRADAY_ALERT,
    OBJECTIVE_STABILITY_FIRST,
    STATUS_EVIDENCE_INSUFFICIENT,
    STATUS_REJECTED,
    HyperoptIntradayPoint,
    HyperoptPricePoint,
    calibration_contract_hash,
    classify_hyperopt_candidate,
    confidence_summary,
    parameter_grid,
    rolling_validation_metrics,
    simulate_exit_rule,
    simulate_intraday_exit_rule,
)


def _points(values: list[float]) -> list[HyperoptPricePoint]:
    start = date(2026, 1, 1)
    return [HyperoptPricePoint(start + timedelta(days=index), value) for index, value in enumerate(values)]


def test_parameter_grid_is_deterministic() -> None:
    first = parameter_grid()
    second = parameter_grid()

    assert first == second
    assert len(first) == 3 * 3 * 3 * 2 * 2
    assert first[0]["hard_stop_multiplier"] == 1.2


def test_oos_degradation_marks_overfit() -> None:
    status, conclusion = classify_hyperopt_candidate(
        {"sample_count": 8, "trade_count": 5, "total_return": 0.22, "max_drawdown": -0.04},
        {"sample_count": 8, "trade_count": 5, "total_return": 0.02, "max_drawdown": -0.05},
    )

    assert status == STATUS_REJECTED
    assert conclusion == CONCLUSION_OVERFIT


def test_sparse_oos_marks_evidence_insufficient() -> None:
    status, conclusion = classify_hyperopt_candidate(
        {"sample_count": 8, "trade_count": 4, "total_return": 0.12, "max_drawdown": -0.03},
        {"sample_count": 2, "trade_count": 1, "total_return": 0.05, "max_drawdown": -0.02},
    )

    assert status == STATUS_EVIDENCE_INSUFFICIENT
    assert conclusion == CONCLUSION_INSUFFICIENT


def test_calibration_contract_hash_is_deterministic() -> None:
    first = calibration_contract_hash(search_space=DEFAULT_SEARCH_SPACE, objective=OBJECTIVE_STABILITY_FIRST)
    second = calibration_contract_hash(search_space=dict(reversed(DEFAULT_SEARCH_SPACE.items())), objective=OBJECTIVE_STABILITY_FIRST)

    assert first == second
    assert len(first) == 64


def test_calibration_contract_hash_separates_execution_models() -> None:
    intraday = calibration_contract_hash(
        search_space=DEFAULT_SEARCH_SPACE,
        objective=OBJECTIVE_STABILITY_FIRST,
        execution_model=EXECUTION_MODEL_INTRADAY_ALERT,
    )
    daily = calibration_contract_hash(
        search_space=DEFAULT_SEARCH_SPACE,
        objective=OBJECTIVE_STABILITY_FIRST,
        execution_model=EXECUTION_MODEL_DAILY_CLOSE,
    )

    assert intraday != daily


def test_exit_calibration_contract_helper_includes_status_and_ids() -> None:
    contract = build_exit_calibration_contract(
        calibration_run_id=10,
        calibration_candidate_id=21,
        approved_parameter_id=21,
        signal_rule_version="short_research_signal_v1",
        exit_rule_version="dynamic_exit_v2",
        calibration_rule_version="etf_exit_calibration_v1",
        execution_model="daily_close",
        evidence_status="current",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
        data_cutoff=datetime(2026, 7, 1, 0, 0),
    )
    same_contract = build_exit_calibration_contract(
        calibration_run_id=10,
        calibration_candidate_id=21,
        approved_parameter_id=21,
        signal_rule_version="short_research_signal_v1",
        exit_rule_version="dynamic_exit_v2",
        calibration_rule_version="etf_exit_calibration_v1",
        execution_model="daily_close",
        evidence_status="current",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
        data_cutoff=datetime(2026, 7, 1, 0, 0),
    )

    assert contract["calibration_candidate_id"] == 21
    assert contract["approved_parameter_id"] == 21
    assert contract["evidence_status"] == "current"
    assert contract["contract_hash"] == same_contract["contract_hash"]


def test_confidence_summary_shrinks_small_samples() -> None:
    confidence = confidence_summary({"sample_count": 3, "trade_count": 2, "win_rate": 1.0})

    assert confidence["level"] == "样本不足"
    assert confidence["shrunk_win_rate"] < 1.0


def test_simulation_triggers_trailing_take_profit_without_email_side_effects() -> None:
    prices = _points(
        [
            1.00,
            1.02,
            1.04,
            1.06,
            1.05,
            1.03,
            1.01,
            1.02,
            1.03,
            1.02,
            1.04,
            1.06,
            1.04,
            1.03,
            1.02,
            1.01,
            1.02,
            1.03,
            1.04,
            1.05,
            1.03,
            1.02,
        ]
    )

    result = simulate_exit_rule(
        prices,
        {
            "hard_stop_multiplier": 1.2,
            "profit_start_multiplier": 0.9,
            "trailing_giveback_multiplier": 0.5,
            "trend_confirm_days": 2,
            "take_profit_watch_pct": 3.0,
        },
        asset_bucket="equity",
    )

    assert result["trade_count"] >= 1
    assert result["alert_count"] >= result["trade_count"]
    assert result["unfilled_count"] == 0
    assert "missed_upside_rate" in result


def test_intraday_replay_missing_history_does_not_use_daily_without_opt_in() -> None:
    result = simulate_intraday_exit_rule(
        [],
        {
            "hard_stop_multiplier": 1.2,
            "profit_start_multiplier": 0.9,
            "trailing_giveback_multiplier": 0.5,
            "trend_confirm_days": 2,
            "take_profit_watch_pct": 3.0,
        },
        daily_points=_points([1.0 + index * 0.01 for index in range(25)]),
        asset_bucket="equity",
    )

    assert result["execution_model"] == EXECUTION_MODEL_INTRADAY_ALERT
    assert result["sample_count"] == 0
    assert result["missing_intraday_evidence_count"] == 1
    assert result["source_reliability"] == "unavailable"


def test_intraday_replay_uses_daily_only_when_explicitly_allowed() -> None:
    result = simulate_intraday_exit_rule(
        [],
        {
            "hard_stop_multiplier": 1.2,
            "profit_start_multiplier": 0.9,
            "trailing_giveback_multiplier": 0.5,
            "trend_confirm_days": 2,
            "take_profit_watch_pct": 3.0,
        },
        daily_points=_points([1.0 + index * 0.01 for index in range(25)]),
        asset_bucket="equity",
        allow_daily_fallback=True,
    )

    assert result["execution_model"] == EXECUTION_MODEL_DAILY_CLOSE
    assert result["source_reliability"] == "verified_daily_close"
    assert result["missing_intraday_evidence_count"] == 0


def test_intraday_replay_excludes_ineligible_quotes_from_decision() -> None:
    base_time = datetime(2026, 7, 1, 9, 30)
    params = {
        "hard_stop_multiplier": 1.2,
        "profit_start_multiplier": 0.9,
        "trailing_giveback_multiplier": 0.5,
        "trend_confirm_days": 2,
        "take_profit_watch_pct": 3.0,
    }
    result = simulate_intraday_exit_rule(
        [
            HyperoptIntradayPoint(base_time + timedelta(minutes=index), 1.0 + index * 0.01, False)
            for index in range(25)
        ],
        params,
        asset_bucket="equity",
    )

    assert result["sample_count"] == 0
    assert result["missing_intraday_evidence_count"] == 1

    eligible_result = simulate_intraday_exit_rule(
        [
            HyperoptIntradayPoint(base_time + timedelta(minutes=index), 1.0 + index * 0.01, True)
            for index in range(25)
        ],
        params,
        asset_bucket="equity",
    )

    assert eligible_result["execution_model"] == EXECUTION_MODEL_INTRADAY_ALERT
    assert eligible_result["source_reliability"] == "verified_intraday"
    assert eligible_result["missing_intraday_evidence_count"] == 0


def test_intraday_replay_fills_after_manual_delay() -> None:
    base_time = datetime(2026, 7, 1, 9, 30)
    prices = [1.00, 1.02, 1.04, 1.06, 1.03, 1.02, 1.01, 1.00] + [1.0 + index * 0.001 for index in range(20)]
    params = {
        "hard_stop_multiplier": 1.2,
        "profit_start_multiplier": 0.9,
        "trailing_giveback_multiplier": 0.5,
        "trend_confirm_days": 2,
        "take_profit_watch_pct": 3.0,
    }

    result = simulate_intraday_exit_rule(
        [
            HyperoptIntradayPoint(base_time + timedelta(minutes=index), price, True)
            for index, price in enumerate(prices)
        ],
        params,
        asset_bucket="equity",
        manual_delay_minutes=3,
    )

    assert result["execution_model"] == EXECUTION_MODEL_INTRADAY_ALERT
    assert result["trade_count"] >= 1
    assert result["unfilled_count"] == 0
    assert result["execution_delay_minutes"] == 3


def test_intraday_replay_does_not_use_daily_close_when_fill_missing() -> None:
    base_time = datetime(2026, 7, 1, 9, 30)
    prices = [1.00 + index * 0.004 for index in range(24)] + [1.04]
    params = {
        "hard_stop_multiplier": 1.2,
        "profit_start_multiplier": 0.9,
        "trailing_giveback_multiplier": 0.5,
        "trend_confirm_days": 2,
        "take_profit_watch_pct": 3.0,
    }

    result = simulate_intraday_exit_rule(
        [
            HyperoptIntradayPoint(base_time + timedelta(minutes=index), price, True)
            for index, price in enumerate(prices)
        ],
        params,
        asset_bucket="equity",
        manual_delay_minutes=3,
    )

    assert result["execution_model"] == EXECUTION_MODEL_INTRADAY_ALERT
    assert result["unfilled_count"] >= 1
    assert result["source_reliability"] == "verified_intraday"


def test_candidate_worse_than_baseline_is_rejected() -> None:
    status, conclusion = classify_hyperopt_candidate(
        {"sample_count": 8, "trade_count": 5, "total_return": 0.12, "max_drawdown": -0.04},
        {"sample_count": 8, "trade_count": 5, "total_return": 0.02, "max_drawdown": -0.08},
        {"sample_count": 8, "trade_count": 5, "total_return": 0.08, "max_drawdown": -0.04},
    )

    assert status == STATUS_REJECTED
    assert conclusion == CONCLUSION_REJECTED


def test_rolling_validation_reports_window_stability() -> None:
    from app.services.short_research.etf_exit_hyperopt import HyperoptSeries

    prices = _points([1.0 + index * 0.003 for index in range(90)])
    metrics = rolling_validation_metrics(
        [HyperoptSeries("510300", "沪深300ETF", "broad_base", "宽基", prices)],
        {
            "hard_stop_multiplier": 1.2,
            "profit_start_multiplier": 0.9,
            "trailing_giveback_multiplier": 0.5,
            "trend_confirm_days": 2,
            "take_profit_watch_pct": 3.0,
        },
    )

    assert metrics["window_count"] >= 1
    assert metrics["stable_window_rate"] is not None


@pytest.mark.asyncio
async def test_etf_exit_hyperopt_job_reports_research_only_summary(monkeypatch) -> None:
    async def fake_run_etf_exit_hyperopt(_session: object, **kwargs: object) -> SimpleNamespace:
        assert kwargs["days"] == 730
        assert kwargs["max_assets"] is None
        assert kwargs["execution_model"] == EXECUTION_MODEL_INTRADAY_ALERT
        assert kwargs["manual_delay_minutes"] == 3
        return SimpleNamespace(
            id=11,
            status="success",
            as_of_date=date(2026, 7, 2),
            objective="stability_first",
            rule_version="etf_exit_hyperopt_v1",
            execution_model=EXECUTION_MODEL_INTRADAY_ALERT,
            summary_json={
                "asset_count": 88,
                "bucket_count": 6,
                "candidate_count": 2,
                "rejected_count": 4,
                "coverage": {"all_etf_count": 1000, "final_optimized_count": 88},
                "sampled": False,
                "final_optimized_count": 88,
                "enough_intraday_history_count": 88,
                "manual_delay_minutes": 3,
            },
            error_message=None,
        )

    monkeypatch.setattr(jobs_module, "run_etf_exit_hyperopt", fake_run_etf_exit_hyperopt)

    result = await jobs_module.etf_exit_hyperopt_job(object())  # type: ignore[arg-type]

    assert result["run_id"] == 11
    assert result["candidate_count"] == 2
    assert result["execution_model"] == EXECUTION_MODEL_INTRADAY_ALERT
    assert result["sampled"] is False
    assert result["final_optimized_count"] == 88
    assert result["auto_applied"] is False
    assert result["research_only"] is True
