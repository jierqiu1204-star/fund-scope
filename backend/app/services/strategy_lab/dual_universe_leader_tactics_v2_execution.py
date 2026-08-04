"""Daily-only research execution model for V2; never infers intraday fills."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from numbers import Real
from typing import Literal

from app.services.strategy_lab.dual_universe_leader_tactics_v2 import V2AdjustedBar
from app.services.strategy_lab.dual_universe_leader_tactics_v2_adapters import (
    adjusted_fact_exclusion_reason,
)

_MAX_COST_BPS_PER_SIDE = 1_000.0


def _validate_cost_bps(value: object, field_name: str) -> float:
    message = f"{field_name} must be a finite non-negative number <= {_MAX_COST_BPS_PER_SIDE:g} bps"
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(message)
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= _MAX_COST_BPS_PER_SIDE:
        raise ValueError(message)
    return normalized


@dataclass(frozen=True)
class V2SimulatedFill:
    operation: Literal["buy", "sell"]
    trade_date: date
    adjusted_close: float
    executed_price: float
    fee_bps_per_side: float
    slippage_bps_per_side: float
    execution_model: str = "next_eligible_adjusted_close_with_costs"


def next_eligible_close_fill(
    bars: Sequence[V2AdjustedBar],
    *,
    after_date: date,
    visible_through: datetime,
    operation: Literal["buy", "sell"],
    fee_bps_per_side: float = 5.0,
    slippage_bps_per_side: float = 5.0,
) -> V2SimulatedFill | None:
    """Use only the next eligible adjusted close; daily high/low never creates a fill."""

    fee_bps_per_side = _validate_cost_bps(fee_bps_per_side, "fee_bps_per_side")
    slippage_bps_per_side = _validate_cost_bps(slippage_bps_per_side, "slippage_bps_per_side")
    cost = (fee_bps_per_side + slippage_bps_per_side) / 10_000.0
    for bar in sorted(bars, key=lambda item: item.trade_date):
        if bar.trade_date <= after_date:
            continue
        observed_at = getattr(bar, "observed_at", None)
        revision_id = str(getattr(bar, "revision_id", "") or "").strip()
        if not isinstance(observed_at, datetime) or not revision_id:
            continue
        if (
            adjusted_fact_exclusion_reason(
                provider=str(getattr(bar, "provider", "") or ""),
                price_basis=str(getattr(bar, "price_basis", "") or ""),
                adjustment_version=str(getattr(bar, "adjustment_version", "") or ""),
                received_at=observed_at,
                source_cutoff=visible_through,
                decision_eligible=bar.decision_eligible,
                values=(
                    bar.adjusted_open,
                    bar.adjusted_high,
                    bar.adjusted_low,
                    bar.adjusted_close,
                    bar.volume,
                    bar.amount,
                    bar.turnover,
                ),
                trade_date=bar.trade_date,
            )
            is not None
        ):
            continue
        executed_price = bar.adjusted_close * (1.0 + cost if operation == "buy" else 1.0 - cost)
        return V2SimulatedFill(
            operation=operation,
            trade_date=bar.trade_date,
            adjusted_close=bar.adjusted_close,
            executed_price=executed_price,
            fee_bps_per_side=fee_bps_per_side,
            slippage_bps_per_side=slippage_bps_per_side,
        )
    return None


__all__ = ["V2SimulatedFill", "next_eligible_close_fill"]
