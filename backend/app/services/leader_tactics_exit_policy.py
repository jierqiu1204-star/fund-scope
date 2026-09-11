"""Shared pure math for the leader-tactics close-based exit policy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

LEADER_TACTICS_HARD_STOP: Final = "leader_tactics_hard_stop"
LEADER_TACTICS_BREAKEVEN_EXIT: Final = "leader_tactics_breakeven_exit"
LEADER_TACTICS_MA5_EXIT: Final = "leader_tactics_ma5_exit"
LEADER_TACTICS_TAKE_PROFIT: Final = "leader_tactics_take_profit"
LEADER_TACTICS_INTRADAY_MA5_WATCH: Final = "leader_tactics_intraday_ma5_watch"
LEADER_TACTICS_INTRADAY_BREAKEVEN_WATCH: Final = "leader_tactics_intraday_breakeven_watch"
LEADER_TACTICS_ROUND_TRIP_COST_BPS: Final = 0.0


@dataclass(frozen=True)
class LeaderExitThresholds:
    high_water: float
    armed: bool
    breakeven_line: float | None
    take_profit_line: float | None
    effective_exit_line: float
    reason_code: str | None


@dataclass(frozen=True)
class LeaderIntradayThresholds:
    adjusted_price: float
    projected_ma5: float
    high_water: float
    armed: bool
    breakeven_line: float | None
    reason_code: str | None


def leader_atr20(
    highs: tuple[float, ...], lows: tuple[float, ...], closes: tuple[float, ...]
) -> float | None:
    """Calculate ATR20 from aligned daily high, low, and close series."""

    if len(highs) != len(lows) or len(highs) != len(closes) or len(highs) < 21:
        return None
    prices = (*highs, *lows, *closes)
    if any(
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
        for value in prices
    ):
        return None
    if any(high < low for high, low in zip(highs, lows, strict=True)):
        return None
    ranges = [
        max(high - low, abs(high - previous_close), abs(low - previous_close))
        for high, low, previous_close in zip(
            highs[-20:], lows[-20:], closes[-21:-1], strict=True
        )
    ]
    if any(not math.isfinite(true_range) or true_range <= 0 for true_range in ranges):
        return None
    result = math.fsum(ranges) / 20.0
    return result if math.isfinite(result) and result > 0 else None


def initial_leader_risk(
    *, entry_close: float, entry_atr20: float, source_signal_low: float | None
) -> tuple[float, float, bool] | None:
    """Freeze the initial stop and risk unit used by live and replay paths."""

    if not all(math.isfinite(value) and value > 0 for value in (entry_close, entry_atr20)):
        return None
    usable_low = (
        source_signal_low
        if source_signal_low is not None
        and math.isfinite(source_signal_low)
        and 0 < source_signal_low < entry_close
        else None
    )
    initial_stop = max(usable_low or 0.0, entry_close - 2.0 * entry_atr20)
    if initial_stop <= 0 or initial_stop >= entry_close:
        initial_stop = entry_close - 2.0 * entry_atr20
    risk_unit = entry_close - initial_stop
    if not math.isfinite(risk_unit) or risk_unit <= 0:
        return None
    return initial_stop, risk_unit, source_signal_low is not None and usable_low is None


def evaluate_leader_intraday_thresholds(
    *,
    adjusted_price: float,
    previous_four_closes: tuple[float, ...],
    entry_close: float,
    initial_stop: float,
    risk_unit: float,
    previous_high: float,
    previously_armed: bool = False,
) -> LeaderIntradayThresholds:
    """Evaluate a trusted intraday ETF quote against frozen adjusted thresholds."""

    values = (
        adjusted_price,
        *previous_four_closes,
        entry_close,
        initial_stop,
        risk_unit,
        previous_high,
    )
    if (
        len(previous_four_closes) != 4
        or any(not math.isfinite(value) or value <= 0 for value in values)
        or initial_stop >= entry_close
    ):
        raise ValueError("leader intraday thresholds require positive finite inputs")
    projected_ma5 = math.fsum((*previous_four_closes, adjusted_price)) / 5.0
    high_water = max(previous_high, adjusted_price)
    armed = previously_armed or high_water >= entry_close + risk_unit
    breakeven = entry_close if armed else None
    if adjusted_price <= initial_stop:
        reason_code = LEADER_TACTICS_HARD_STOP
    elif breakeven is not None and adjusted_price <= breakeven:
        reason_code = LEADER_TACTICS_INTRADAY_BREAKEVEN_WATCH
    elif adjusted_price <= projected_ma5:
        reason_code = LEADER_TACTICS_INTRADAY_MA5_WATCH
    else:
        reason_code = None
    return LeaderIntradayThresholds(
        adjusted_price=adjusted_price,
        projected_ma5=projected_ma5,
        high_water=high_water,
        armed=armed,
        breakeven_line=breakeven,
        reason_code=reason_code,
    )


def evaluate_leader_exit_thresholds(
    *,
    entry_close: float,
    initial_stop: float,
    risk_unit: float,
    previous_high: float | None,
    visible_closes: tuple[float, ...],
    ma5: float,
    previously_armed: bool = False,
    take_profit_line: float | None = None,
) -> LeaderExitThresholds:
    """Evaluate one close using the frozen hard-stop, breakeven and MA5 rules."""

    high_water = max(visible_closes + ((previous_high,) if previous_high is not None else ()))
    armed = previously_armed or high_water >= entry_close + risk_unit
    breakeven = (
        entry_close * (1.0 + LEADER_TACTICS_ROUND_TRIP_COST_BPS / 10_000.0) if armed else None
    )
    effective_line = max(
        initial_stop,
        *(line for line in (breakeven, ma5) if line is not None),
    )
    current = visible_closes[-1]
    reason_code = None
    usable_take_profit = (
        take_profit_line
        if take_profit_line is not None
        and math.isfinite(take_profit_line)
        and take_profit_line > entry_close
        else None
    )
    if usable_take_profit is not None and current >= usable_take_profit:
        reason_code = LEADER_TACTICS_TAKE_PROFIT
    elif current <= effective_line:
        if current <= initial_stop:
            reason_code = LEADER_TACTICS_HARD_STOP
        elif breakeven is not None and current <= breakeven:
            reason_code = LEADER_TACTICS_BREAKEVEN_EXIT
        else:
            reason_code = LEADER_TACTICS_MA5_EXIT
    return LeaderExitThresholds(
        high_water=high_water,
        armed=armed,
        breakeven_line=breakeven,
        take_profit_line=usable_take_profit,
        effective_exit_line=effective_line,
        reason_code=reason_code,
    )
