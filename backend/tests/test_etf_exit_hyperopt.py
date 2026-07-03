from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.services.short_research import jobs as jobs_module
from app.services.short_research.etf_exit_hyperopt import (
    CONCLUSION_OVERFIT,
    STATUS_REJECTED,
    HyperoptPricePoint,
    classify_hyperopt_candidate,
    parameter_grid,
    simulate_exit_rule,
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


@pytest.mark.asyncio
async def test_etf_exit_hyperopt_job_reports_research_only_summary(monkeypatch) -> None:
    async def fake_run_etf_exit_hyperopt(_session: object, **kwargs: object) -> SimpleNamespace:
        assert kwargs["days"] == 730
        assert kwargs["max_assets"] == 300
        return SimpleNamespace(
            id=11,
            status="success",
            as_of_date=date(2026, 7, 2),
            objective="stability_first",
            rule_version="etf_exit_hyperopt_v1",
            summary_json={
                "asset_count": 88,
                "bucket_count": 6,
                "candidate_count": 2,
                "rejected_count": 4,
            },
            error_message=None,
        )

    monkeypatch.setattr(jobs_module, "run_etf_exit_hyperopt", fake_run_etf_exit_hyperopt)

    result = await jobs_module.etf_exit_hyperopt_job(object())  # type: ignore[arg-type]

    assert result["run_id"] == 11
    assert result["candidate_count"] == 2
    assert result["auto_applied"] is False
    assert result["research_only"] is True
