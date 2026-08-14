"""Pure, causal T+1 exit policy for manually tracked late-day ETF entries."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
POLICY_ID = "late_day_turnaround_t1_v1"
POLICY_VERSION = "late_day_turnaround_t1_v1"
POLICY_STATE_KEY = "late_day_turnaround_t1_v1"
ALERT_TYPE = "late_day_t1_exit"

AWAITING_T1 = "awaiting_t1_session"
BEFORE_MORNING_OPEN = "before_t1_morning_open"
THRESHOLD_NOT_TRIGGERED = "late_day_t1_threshold_not_triggered"
QUOTE_UNAVAILABLE = "late_day_t1_fresh_quote_unavailable"
INVALID_INPUT = "late_day_t1_invalid_input"
HARD_STOP = "late_day_t1_hard_stop"
MORNING_GIVEBACK = "late_day_t1_morning_high_giveback"
MA5_FAILURE = "late_day_t1_ma5_failure"
TIMED_EXIT = "late_day_t1_timed_exit"


@dataclass(frozen=True)
class MorningQuote:
    observed_at: datetime
    price: float


@dataclass(frozen=True)
class LateDayT1Input:
    entry_date: date
    t1_date: date
    entry_price: float | None
    evaluation_at: datetime
    current_quote_time: datetime | None
    current_price: float | None
    current_quote_eligible: bool
    morning_quotes: Sequence[MorningQuote] = ()
    hard_stop_pct: float = -4.0
    volatility_unit_pct: float = 1.5
    persisted_high_water_price: float | None = None


@dataclass(frozen=True)
class LateDayT1Decision:
    actionable: bool
    reason_code: str
    label: str
    data_eligible: bool
    alert_type: str | None
    level: str
    threshold_context: Mapping[str, Any]
    state_update: Mapping[str, Any]


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def _finite_positive(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _freeze(value: dict[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(value)


def _decision(
    *,
    actionable: bool,
    reason_code: str,
    label: str,
    data_eligible: bool,
    context: dict[str, Any],
    state: dict[str, Any],
    level: str = "none",
) -> LateDayT1Decision:
    return LateDayT1Decision(
        actionable=actionable,
        reason_code=reason_code,
        label=label,
        data_eligible=data_eligible,
        alert_type=ALERT_TYPE if actionable else None,
        level=level,
        threshold_context=_freeze(context),
        state_update=_freeze(state),
    )


def _closed_10m_closes(
    quotes: Sequence[MorningQuote], *, session_date: date, cutoff: datetime
) -> tuple[float, ...]:
    buckets: dict[datetime, list[MorningQuote]] = {}
    session_open = datetime.combine(session_date, time(9, 30), tzinfo=SHANGHAI)
    for quote in quotes:
        observed = _local(quote.observed_at)
        price = _finite_positive(quote.price)
        if price is None or observed.date() != session_date or not time(9, 30) <= observed.time():
            continue
        minutes = int((observed - session_open).total_seconds() // 60)
        if minutes < 0:
            continue
        bucket_start = session_open + timedelta(minutes=(minutes // 10) * 10)
        if bucket_start + timedelta(minutes=10) > cutoff:
            continue
        buckets.setdefault(bucket_start, []).append(
            MorningQuote(observed_at=observed, price=price)
        )
    closes: list[float] = []
    for bucket_start in sorted(buckets):
        rows = sorted(buckets[bucket_start], key=lambda item: item.observed_at)
        closes.append(rows[-1].price)
    return tuple(closes)


def evaluate_late_day_t1(value: LateDayT1Input) -> LateDayT1Decision:
    """Evaluate one point in time without using any quote after ``evaluation_at``."""

    evaluation_at = _local(value.evaluation_at)
    base_context: dict[str, Any] = {
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "entry_date": value.entry_date.isoformat(),
        "t1_date": value.t1_date.isoformat(),
        "evaluation_at": evaluation_at.isoformat(),
        "trigger_priority": [HARD_STOP, MORNING_GIVEBACK, MA5_FAILURE, TIMED_EXIT],
    }
    base_state: dict[str, Any] = {
        "policy_version": POLICY_VERSION,
        "t1_date": value.t1_date.isoformat(),
        "last_evaluated_at": evaluation_at.isoformat(),
    }
    entry_price = _finite_positive(value.entry_price)
    volatility_unit = _finite_positive(value.volatility_unit_pct)
    if (
        not isinstance(value.entry_date, date)
        or not isinstance(value.t1_date, date)
        or value.t1_date <= value.entry_date
        or entry_price is None
        or volatility_unit is None
        or not math.isfinite(value.hard_stop_pct)
        or not -20.0 <= value.hard_stop_pct < 0.0
    ):
        return _decision(
            actionable=False,
            reason_code=INVALID_INPUT,
            label="尾盘策略数据不完整",
            data_eligible=False,
            context=base_context,
            state=base_state,
        )
    if evaluation_at.date() < value.t1_date:
        return _decision(
            actionable=False,
            reason_code=AWAITING_T1,
            label="等待下一交易日早盘",
            data_eligible=True,
            context=base_context,
            state=base_state,
        )
    if evaluation_at.date() == value.t1_date and evaluation_at.time() < time(9, 30):
        return _decision(
            actionable=False,
            reason_code=BEFORE_MORNING_OPEN,
            label="等待早盘开市",
            data_eligible=True,
            context=base_context,
            state=base_state,
        )

    current_price = _finite_positive(value.current_price)
    quote_time = _local(value.current_quote_time) if value.current_quote_time else None
    if (
        not value.current_quote_eligible
        or current_price is None
        or quote_time is None
        or quote_time > evaluation_at
        or quote_time.date() != evaluation_at.date()
    ):
        return _decision(
            actionable=False,
            reason_code=QUOTE_UNAVAILABLE,
            label="等待新鲜可成交报价",
            data_eligible=False,
            context={**base_context, "quote_time": quote_time.isoformat() if quote_time else None},
            state=base_state,
        )

    bounded_quotes: list[MorningQuote] = []
    for index, quote in enumerate(value.morning_quotes, start=1):
        if index > 512:
            return _decision(
                actionable=False,
                reason_code=INVALID_INPUT,
                label="盘中证据超过读取上限",
                data_eligible=False,
                context=base_context,
                state=base_state,
            )
        observed = _local(quote.observed_at)
        price = _finite_positive(quote.price)
        if price is None or observed > evaluation_at:
            continue
        if observed.date() == value.t1_date and time(9, 30) <= observed.time() <= time(10, 30):
            bounded_quotes.append(MorningQuote(observed_at=observed, price=price))
    persisted_high = _finite_positive(value.persisted_high_water_price)
    high_candidates = [item.price for item in bounded_quotes]
    if quote_time.date() == value.t1_date and time(9, 30) <= quote_time.time() <= time(10, 30):
        high_candidates.append(current_price)
    if persisted_high is not None:
        high_candidates.append(persisted_high)
    high_water = max(high_candidates) if high_candidates else current_price

    hard_stop_price = entry_price * (1.0 + value.hard_stop_pct / 100.0)
    activation_pct = _clamp(0.5 * volatility_unit, 0.5, 1.5)
    giveback_allowance_pct = _clamp(0.6 * volatility_unit, 0.5, 1.5)
    high_profit_pct = (high_water / entry_price - 1.0) * 100.0
    giveback_pct = (high_water / current_price - 1.0) * 100.0
    common = {
        **base_context,
        "quote_time": quote_time.isoformat(),
        "current_price": current_price,
        "entry_price": entry_price,
        "hard_stop_pct": value.hard_stop_pct,
        "hard_stop_price": hard_stop_price,
        "volatility_unit_pct": volatility_unit,
        "morning_high_price": high_water,
        "morning_high_profit_pct": high_profit_pct,
        "morning_high_activation_pct": activation_pct,
        "morning_giveback_pct": giveback_pct,
        "morning_giveback_allowance_pct": giveback_allowance_pct,
        "morning_quote_count": len(bounded_quotes),
    }
    state = {**base_state, "morning_high_price": high_water}
    if current_price <= hard_stop_price:
        return _decision(
            actionable=True,
            reason_code=HARD_STOP,
            label="尾盘策略 T+1 硬止损",
            data_eligible=True,
            context=common,
            state=state,
            level="urgent",
        )
    if high_profit_pct >= activation_pct and giveback_pct >= giveback_allowance_pct:
        return _decision(
            actionable=True,
            reason_code=MORNING_GIVEBACK,
            label="尾盘策略早盘高点保护",
            data_eligible=True,
            context=common,
            state=state,
            level="urgent",
        )

    closes = _closed_10m_closes(
        bounded_quotes,
        session_date=value.t1_date,
        cutoff=min(
            evaluation_at,
            datetime.combine(value.t1_date, time(10, 30), tzinfo=SHANGHAI),
        ),
    )
    ma5_crossed = False
    current_ma5 = None
    previous_ma5 = None
    if len(closes) >= 6:
        current_ma5 = sum(closes[-5:]) / 5.0
        previous_ma5 = sum(closes[-6:-1]) / 5.0
        ma5_crossed = closes[-1] < current_ma5 and closes[-2] >= previous_ma5
    common.update(
        {
            "closed_10m_bar_count": len(closes),
            "latest_closed_10m_price": closes[-1] if closes else None,
            "current_10m_ma5": current_ma5,
            "previous_10m_ma5": previous_ma5,
        }
    )
    if ma5_crossed:
        return _decision(
            actionable=True,
            reason_code=MA5_FAILURE,
            label="尾盘策略 10 分钟 MA5 退出",
            data_eligible=True,
            context=common,
            state=state,
            level="urgent",
        )
    if evaluation_at.date() > value.t1_date or evaluation_at.time() >= time(10, 30):
        return _decision(
            actionable=True,
            reason_code=TIMED_EXIT,
            label="尾盘策略 10:30 限时退出",
            data_eligible=True,
            context=common,
            state=state,
            level="urgent",
        )
    return _decision(
        actionable=False,
        reason_code=THRESHOLD_NOT_TRIGGERED,
        label="尾盘策略等待早盘退出条件",
        data_eligible=True,
        context=common,
        state=state,
    )


__all__ = [
    "ALERT_TYPE",
    "LateDayT1Decision",
    "LateDayT1Input",
    "MorningQuote",
    "POLICY_ID",
    "POLICY_STATE_KEY",
    "POLICY_VERSION",
    "evaluate_late_day_t1",
]
