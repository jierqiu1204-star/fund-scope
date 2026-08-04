from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import V2AdjustedBar
from app.services.strategy_lab.dual_universe_leader_tactics_v2_execution import (
    next_eligible_close_fill,
)


def test_execution_uses_next_adjusted_close_and_declared_costs() -> None:
    bars = tuple(
        V2AdjustedBar(
            trade_date=date(2026, 8, 1) + timedelta(days=index),
            adjusted_open=100.0,
            adjusted_high=110.0,
            adjusted_low=90.0,
            adjusted_close=100.0 + index,
            volume=1000.0,
            amount=100000.0,
            turnover=100000.0,
            observed_at=datetime.combine(date(2026, 8, 1) + timedelta(days=index), time(15)),
            provider="eastmoney",
            adjustment_version="v1",
            revision_id=f"rev-{index}",
        )
        for index in range(3)
    )
    fill = next_eligible_close_fill(
        bars,
        after_date=date(2026, 8, 1),
        visible_through=datetime(2026, 8, 3, 15, 30),
        operation="buy",
    )
    assert fill is not None
    assert fill.trade_date == date(2026, 8, 2)
    assert fill.executed_price > fill.adjusted_close
    assert fill.execution_model == "next_eligible_adjusted_close_with_costs"


@pytest.mark.parametrize(
    "field_name, value",
    (
        ("fee_bps_per_side", float("nan")),
        ("fee_bps_per_side", float("inf")),
        ("slippage_bps_per_side", float("-inf")),
        ("fee_bps_per_side", True),
        ("slippage_bps_per_side", False),
        ("fee_bps_per_side", -0.01),
        ("slippage_bps_per_side", 1_000.01),
    ),
)
def test_execution_rejects_invalid_cost_parameters(field_name: str, value: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        next_eligible_close_fill(
            (),
            after_date=date(2026, 8, 1),
            visible_through=datetime(2026, 8, 2, 15, 30),
            operation="buy",
            **{field_name: value},
        )


@pytest.mark.parametrize(
    "overrides",
    (
        {"provider": "sina"},
        {"price_basis": "raw"},
        {"adjustment_version": ""},
        {"adjustment_version": None},
        {"revision_id": ""},
        {"revision_id": None},
        {"decision_eligible": False},
        {"observed_at": datetime(2026, 8, 1, 14, 59)},
        {"observed_at": datetime(2026, 8, 3, 16, 0)},
    ),
)
def test_execution_rejects_non_causal_or_ungoverned_bar(
    overrides: dict[str, object],
) -> None:
    bar = V2AdjustedBar(
        trade_date=date(2026, 8, 2),
        adjusted_open=100.0,
        adjusted_high=110.0,
        adjusted_low=90.0,
        adjusted_close=101.0,
        volume=1000.0,
        amount=100000.0,
        turnover=100000.0,
        observed_at=datetime(2026, 8, 2, 15),
        provider="eastmoney",
        adjustment_version="v1",
        revision_id="rev-1",
    )
    fill = next_eligible_close_fill(
        (replace(bar, **overrides),),
        after_date=date(2026, 8, 1),
        visible_through=datetime(2026, 8, 3, 15, 30),
        operation="buy",
    )
    assert fill is None
