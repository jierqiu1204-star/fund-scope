from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DCAResult:
    amount: float
    reason: str


def compute_dca_amount(base_amount: float, percentile: float | None) -> DCAResult:
    if percentile is None:
        return DCAResult(amount=round(base_amount, 2), reason="估值缺失 按固定计划或人工确认")
    if percentile < 20:
        return DCAResult(amount=round(base_amount * 1.5, 2), reason="低估 加码")
    if percentile < 50:
        return DCAResult(amount=round(base_amount, 2), reason="合理 常规定投")
    if percentile < 80:
        return DCAResult(amount=round(base_amount * 0.5, 2), reason="偏高 减量")
    return DCAResult(amount=0.0, reason="高估 暂停本月定投")
