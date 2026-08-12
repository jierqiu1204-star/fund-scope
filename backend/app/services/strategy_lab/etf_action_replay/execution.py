from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class ExecutionStatus(StrEnum):
    FILLED = "filled"
    DEFERRED = "deferred"
    REJECTED = "rejected"


@dataclass(frozen=True)
class DailyExecutionBar:
    session_date: date
    raw_open: float | None
    adjustment_factor: float | None
    volume: float | None
    suspended: bool = False
    limit_locked: bool = False
    demonstrably_tradable: bool = False
    delisted: bool = False
    raw_high: float | None = None
    raw_low: float | None = None
    raw_close: float | None = None
    median_turnover_20d: float | None = None


@dataclass(frozen=True)
class DeferredExecutionSession:
    session_date: date
    reason: str


@dataclass(frozen=True)
class SimulatedAdjustedOpenFill:
    session_date: date
    raw_open: float
    adjustment_factor: float
    adjusted_open: float
    normalized_execution_price: float
    signal_to_fill_trading_sessions: int
    median_turnover_20d: float | None = None


@dataclass(frozen=True)
class ExecutionResolution:
    status: ExecutionStatus
    fill: SimulatedAdjustedOpenFill | None
    deferred_sessions: tuple[DeferredExecutionSession, ...] = ()
    rejection_reason: str | None = None

    @property
    def outcome_start_session(self) -> date | None:
        return self.fill.session_date if self.fill is not None else None


def _finite_positive(value: float | None) -> bool:
    return (
        value is not None
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0.0
    )


def select_adjusted_open_fill(
    *,
    signal_session: date,
    bars: Iterable[DailyExecutionBar],
) -> ExecutionResolution:
    future_bars = sorted(
        (bar for bar in bars if bar.session_date > signal_session),
        key=lambda bar: bar.session_date,
    )
    dates = [bar.session_date for bar in future_bars]
    if len(dates) != len(set(dates)):
        raise ValueError("execution bars require one row per trading session")

    deferred: list[DeferredExecutionSession] = []
    for session_number, bar in enumerate(future_bars, start=1):
        if bar.delisted:
            return ExecutionResolution(
                status=ExecutionStatus.REJECTED,
                fill=None,
                deferred_sessions=tuple(deferred),
                rejection_reason="delisted_before_fill",
            )
        if bar.suspended:
            deferred.append(DeferredExecutionSession(bar.session_date, "suspended"))
            continue
        if not _finite_positive(bar.volume):
            deferred.append(
                DeferredExecutionSession(bar.session_date, "zero_or_missing_volume")
            )
            continue
        if bar.limit_locked and not bar.demonstrably_tradable:
            deferred.append(
                DeferredExecutionSession(
                    bar.session_date,
                    "limit_lock_without_demonstrable_liquidity",
                )
            )
            continue
        if not _finite_positive(bar.raw_open):
            deferred.append(
                DeferredExecutionSession(bar.session_date, "missing_or_invalid_open")
            )
            continue
        if not _finite_positive(bar.adjustment_factor):
            deferred.append(
                DeferredExecutionSession(
                    bar.session_date,
                    "missing_or_invalid_adjustment_factor",
                )
            )
            continue
        raw_open = float(bar.raw_open)
        adjustment_factor = float(bar.adjustment_factor)
        adjusted_open = raw_open * adjustment_factor
        if not math.isfinite(adjusted_open) or adjusted_open <= 0.0:
            deferred.append(
                DeferredExecutionSession(bar.session_date, "missing_or_invalid_open")
            )
            continue
        return ExecutionResolution(
            status=ExecutionStatus.FILLED,
            fill=SimulatedAdjustedOpenFill(
                session_date=bar.session_date,
                raw_open=raw_open,
                adjustment_factor=adjustment_factor,
                adjusted_open=adjusted_open,
                normalized_execution_price=adjusted_open,
                signal_to_fill_trading_sessions=session_number,
                median_turnover_20d=(
                    float(bar.median_turnover_20d)
                    if _finite_positive(bar.median_turnover_20d)
                    else None
                ),
            ),
            deferred_sessions=tuple(deferred),
        )

    return ExecutionResolution(
        status=ExecutionStatus.DEFERRED,
        fill=None,
        deferred_sessions=tuple(deferred),
    )
