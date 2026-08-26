"""Shared pure math for the leader-tactics close-based exit policy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

LEADER_TACTICS_HARD_STOP: Final = "leader_tactics_hard_stop"
LEADER_TACTICS_BREAKEVEN_EXIT: Final = "leader_tactics_breakeven_exit"
LEADER_TACTICS_MA5_EXIT: Final = "leader_tactics_ma5_exit"
LEADER_TACTICS_ROUND_TRIP_COST_BPS: Final = 0.0


@dataclass(frozen=True)
class LeaderExitThresholds:
    high_water: float
    armed: bool
    breakeven_line: float | None
    effective_exit_line: float
    reason_code: str | None


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


def evaluate_leader_exit_thresholds(
    *,
    entry_close: float,
    initial_stop: float,
    risk_unit: float,
    previous_high: float | None,
    visible_closes: tuple[float, ...],
    ma5: float,
    previously_armed: bool = False,
) -> LeaderExitThresholds:
    """Evaluate one close using the frozen hard-stop, breakeven and MA5 rules."""

    high_water = max(
        visible_closes + ((previous_high,) if previous_high is not None else ())
    )
    armed = previously_armed or high_water >= entry_close + risk_unit
    breakeven = (
        entry_close * (1.0 + LEADER_TACTICS_ROUND_TRIP_COST_BPS / 10_000.0)
        if armed
        else None
    )
    effective_line = max(
        initial_stop,
        *(line for line in (breakeven, ma5) if line is not None),
    )
    current = visible_closes[-1]
    reason_code = None
    if current <= effective_line:
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
        effective_exit_line=effective_line,
        reason_code=reason_code,
    )
