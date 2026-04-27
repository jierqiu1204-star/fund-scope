from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(slots=True)
class PercentileResult:
    percentile: float
    effective_window: int


def compute_percentile(values: list[float], current_value: float, max_window_days: int = 3650) -> PercentileResult:
    window_size = min(len(values), max_window_days)
    if window_size == 0:
        return PercentileResult(percentile=0.0, effective_window=0)

    trailing_values = values[-window_size:]
    count = sum(1 for value in trailing_values if value <= current_value)
    percentile = round((count / window_size) * 100, 2)
    return PercentileResult(percentile=percentile, effective_window=window_size)


def is_business_days_stale(value_date: date, threshold_days: int = 2) -> bool:
    business_days = 0
    current = value_date
    today = date.today()
    while current < today:
        current += timedelta(days=1)
        if current.weekday() < 5:
            business_days += 1
    return business_days > threshold_days
