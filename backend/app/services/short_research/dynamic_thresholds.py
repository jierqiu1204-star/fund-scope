from __future__ import annotations

from dataclasses import dataclass
from statistics import median, pstdev
from typing import Any

RULE_VERSION = "dynamic_etf_threshold_v1"


@dataclass(frozen=True)
class ThresholdPricePoint:
    value: float
    high: float | None = None
    low: float | None = None
    pct_change: float | None = None


_BUCKET_BOUNDS: dict[str, tuple[float, float]] = {
    "bond": (0.003, 0.012),
    "money": (0.001, 0.006),
    "broad_base": (0.006, 0.025),
    "dividend": (0.006, 0.022),
    "equity": (0.008, 0.035),
    "cross_border": (0.010, 0.045),
    "commodity": (0.010, 0.040),
    "unknown": (0.008, 0.030),
}


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _percentile(values: list[float], target: float | None) -> float | None:
    if target is None or not values:
        return None
    ordered = sorted(values)
    less_equal = sum(1 for value in ordered if value <= target)
    return less_equal / len(ordered)


def _window_returns(values: list[float], window: int) -> list[float]:
    results: list[float] = []
    for index in range(window, len(values)):
        base = values[index - window]
        if base:
            results.append(values[index] / base - 1.0)
    return results


def classify_premium_state(value: float | None) -> str:
    if value is None:
        return "unavailable"
    abs_value = abs(value)
    if abs_value >= 8.0:
        return "extreme"
    if abs_value >= 3.0:
        return "high"
    if abs_value >= 0.8:
        return "mild"
    return "normal"


def dynamic_threshold_context(
    *,
    asset_bucket: str,
    theme_group: str = "unknown",
    points: list[ThresholdPricePoint],
    today_return: float | None = None,
    return_5d: float | None = None,
    return_20d: float | None = None,
    return_60d: float | None = None,
    volatility_20d: float | None = None,
    max_drawdown_60d: float | None = None,
    distance_to_ma5: float | None = None,
    premium_discount_pct: float | None = None,
    holding_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    values = [point.value for point in points if point.value]
    daily_returns = [
        values[index] / values[index - 1] - 1.0
        for index in range(1, len(values))
        if values[index - 1]
    ]
    range_returns: list[float] = []
    for index, point in enumerate(points):
        previous = values[index - 1] if index > 0 and index - 1 < len(values) else point.value
        if point.high is not None and point.low is not None and previous:
            range_returns.append(max(point.high - point.low, abs(point.high - previous), abs(point.low - previous)) / previous)

    realized_vol_20d = pstdev(daily_returns[-20:]) if len(daily_returns[-20:]) > 1 else None
    median_abs_return_60d = median(abs(value) for value in daily_returns[-60:]) if daily_returns[-60:] else None
    atr_style_20d = sum(range_returns[-20:]) / len(range_returns[-20:]) if len(range_returns[-20:]) >= 5 else None
    raw_unit = max(
        value
        for value in [
            atr_style_20d,
            realized_vol_20d,
            volatility_20d,
            median_abs_return_60d,
            0.008,
        ]
        if value is not None
    )
    bounds = _BUCKET_BOUNDS.get(asset_bucket, _BUCKET_BOUNDS["unknown"])
    volatility_unit = clamp(raw_unit, bounds[0], bounds[1])
    enough_history = len(values) >= 30
    threshold_mode = "dynamic" if enough_history else "conservative_default"
    premium_state = classify_premium_state(premium_discount_pct)

    one_day_percentile = _percentile(daily_returns[-120:], today_return)
    return_percentiles = {
        "one_day": _round(one_day_percentile, 3),
        "five_day": _round(_percentile(_window_returns(values, 5)[-120:], return_5d), 3),
        "twenty_day": _round(_percentile(_window_returns(values, 20)[-120:], return_20d), 3),
        "sixty_day": _round(_percentile(_window_returns(values, 60)[-120:], return_60d), 3),
    }
    thresholds = {
        "drop_wait": -clamp(1.1 * volatility_unit, 0.012, 0.06),
        "healthy_pullback_min": -clamp(1.50 * volatility_unit, 0.010, 0.035),
        "healthy_pullback_max": -max(0.0015, 0.12 * volatility_unit),
        "chase_daily": clamp(1.35 * volatility_unit, 0.015, 0.065),
        "ma_overextension": clamp(1.30 * volatility_unit, 0.018, 0.060),
        "return_5_surge": clamp(2.80 * volatility_unit, 0.035, 0.160),
        "return_20_chase": clamp(6.00 * volatility_unit, 0.080, 0.350),
        "return_60_chase": clamp(11.0 * volatility_unit, 0.180, 0.650),
        "high_volatility": clamp(1.60 * volatility_unit, 0.018, 0.070),
        "large_drawdown": -clamp(6.0 * volatility_unit, 0.080, 0.280),
        "hard_stop_pct": -clamp(1.50 * volatility_unit * 100, 1.2, 5.5),
        "profit_start_pct": clamp(1.10 * volatility_unit * 100, 3.0, 4.0),
        "trailing_giveback_pct": clamp(0.65 * volatility_unit * 100, 1.8, 2.5),
    }
    current_move_vs_normal = None
    if today_return is not None and volatility_unit:
        current_move_vs_normal = today_return / volatility_unit
    effective_realized_vol = realized_vol_20d if realized_vol_20d is not None else volatility_20d
    reasons = [
        f"按 {asset_bucket or 'unknown'} / {theme_group or 'unknown'} 口径计算，正常日波动约 {volatility_unit * 100:.2f}%。",
    ]
    if not enough_history:
        reasons.append("可用历史少于30个交易日，使用保守默认动态线。")
    if premium_state in {"high", "extreme"}:
        reasons.append(f"折溢价状态为 {premium_state}，买点和组合权重需要降级。")
    return {
        "rule_version": RULE_VERSION,
        "threshold_mode": threshold_mode,
        "asset_bucket": asset_bucket or "unknown",
        "theme_group": theme_group or "unknown",
        "volatility_unit_pct": round(volatility_unit * 100, 3),
        "volatility_source": "atr_or_realized_or_median_abs",
        "atr_style_20d_pct": _round(atr_style_20d * 100 if atr_style_20d is not None else None, 3),
        "realized_vol_20d_pct": _round(
            effective_realized_vol * 100 if effective_realized_vol is not None else None, 3
        ),
        "median_abs_return_60d_pct": _round(median_abs_return_60d * 100 if median_abs_return_60d is not None else None, 3),
        "return_percentiles": return_percentiles,
        "premium_discount_pct": premium_discount_pct,
        "premium_state": premium_state,
        "holding_state": holding_state or {},
        "current_move_vs_normal": _round(current_move_vs_normal, 3),
        "max_drawdown_60d": max_drawdown_60d,
        "distance_to_ma5": distance_to_ma5,
        "thresholds": {key: round(value, 4) for key, value in thresholds.items()},
        "decision_eligible": threshold_mode == "dynamic" and premium_state not in {"high", "extreme"},
        "ineligible_reason": None if threshold_mode == "dynamic" else "insufficient_history",
        "reason": " ".join(reasons),
    }
