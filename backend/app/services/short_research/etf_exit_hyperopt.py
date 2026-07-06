from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import product
from statistics import pstdev
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import (
    EtfExitHyperoptItem,
    EtfExitHyperoptRun,
    EtfIntradayQuote,
    EtfPriceHistory,
    EtfThemeProfile,
    ShortResearchSignalItem,
    TradableEtf,
    utcnow,
)
from app.services.short_research.dynamic_thresholds import clamp
from app.services.short_research.service import has_available_opportunity_score, latest_signal_run

RULE_VERSION = "etf_exit_hyperopt_v1"
CALIBRATION_RULE_VERSION = "etf_exit_calibration_v1"
POLICY_VALIDATION_VERSION = "exit_policy_validation_v2"
PROTECTION_GUARD_VERSION = "etf_exit_protection_guards_v1"
EXECUTION_MODEL_DAILY_CLOSE = "daily_close"
EXECUTION_MODEL_INTRADAY_ALERT = "intraday_alert"
OBJECTIVE_STABILITY_FIRST = "stability_first"
UNIVERSE_SCOPE_ALL_ELIGIBLE = "all_eligible"
UNIVERSE_SCOPE_LATEST_OPPORTUNITY_TOP = "latest_opportunity_top"
DEFAULT_MANUAL_DELAY_MINUTES = 3
DEFAULT_BATCH_SIZE = 100
MIN_DAILY_HISTORY_POINTS = 40
MIN_INTRADAY_HISTORY_POINTS = 20
MAX_INTRADAY_HYPEROPT_DAYS = 45
INTRADAY_HYPEROPT_BAR_MINUTES = 10
STATUS_CANDIDATE = "candidate"
STATUS_APPROVED = "approved"
STATUS_EXPIRED = "expired"
STATUS_EVIDENCE_INSUFFICIENT = "evidence_insufficient"
STATUS_REJECTED = "rejected"
CONCLUSION_CANDIDATE = "候选待确认"
CONCLUSION_INSUFFICIENT = "证据不足"
CONCLUSION_OVERFIT = "疑似过拟合"
CONCLUSION_REJECTED = "不建议采用"

DEFAULT_SEARCH_SPACE: dict[str, list[float | int]] = {
    "hard_stop_multiplier": [1.2, 1.5, 1.8],
    "profit_start_multiplier": [0.9, 1.1, 1.3],
    "trailing_giveback_multiplier": [0.5, 0.65, 0.8],
    "trend_confirm_days": [1, 2],
    "take_profit_watch_pct": [3.0, 4.0],
}

INTRADAY_SEARCH_SPACE: dict[str, list[float | int]] = {
    "hard_stop_multiplier": [1.2, 1.5, 1.8],
    "profit_start_multiplier": [0.9, 1.1, 1.3],
    "trailing_giveback_multiplier": [0.5, 0.65, 0.8],
    "trend_confirm_days": [1],
    "take_profit_watch_pct": [3.0],
}

DEFAULT_LIVE_PARAMS: dict[str, float | int] = {
    "hard_stop_multiplier": 1.5,
    "profit_start_multiplier": 1.1,
    "trailing_giveback_multiplier": 0.65,
    "trend_confirm_days": 1,
    "take_profit_watch_pct": 3.0,
}

_BUCKET_LIMITS: dict[str, tuple[float, float]] = {
    "bond": (0.3, 1.2),
    "money": (0.1, 0.6),
    "broad_base": (0.6, 2.5),
    "dividend": (0.6, 2.2),
    "equity": (0.8, 3.5),
    "cross_border": (1.0, 4.5),
    "commodity": (1.0, 4.0),
    "unknown": (0.8, 3.0),
}


@dataclass(frozen=True)
class HyperoptPricePoint:
    trade_date: date
    close: float


@dataclass(frozen=True)
class HyperoptIntradayPoint:
    quote_time: datetime
    price: float
    decision_eligible: bool = True


@dataclass(frozen=True)
class HyperoptSeries:
    code: str
    name: str
    asset_bucket: str
    theme_group: str
    points: list[HyperoptPricePoint]
    intraday_points: list[HyperoptIntradayPoint] = field(default_factory=list)


@dataclass(frozen=True)
class HyperoptCoverage:
    all_etf_count: int
    eligible_count: int
    selected_count: int
    enough_daily_history_count: int
    enough_intraday_history_count: int
    final_optimized_count: int
    sampled: bool
    max_assets: int | None
    exclusions: dict[str, int]
    exclusion_examples: dict[str, list[str]]
    universe_scope: str = UNIVERSE_SCOPE_ALL_ELIGIBLE
    batch_size: int = DEFAULT_BATCH_SIZE
    source_signal_run_id: int | None = None
    source_signal_as_of_date: str | None = None
    requested_top_n: int | None = None
    selected_codes: list[str] = field(default_factory=list)
    excluded_unavailable_opportunity_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "all_etf_count": self.all_etf_count,
            "eligible_count": self.eligible_count,
            "selected_count": self.selected_count,
            "enough_daily_history_count": self.enough_daily_history_count,
            "enough_intraday_history_count": self.enough_intraday_history_count,
            "final_optimized_count": self.final_optimized_count,
            "sampled": self.sampled,
            "max_assets": self.max_assets,
            "exclusions": self.exclusions,
            "exclusion_examples": self.exclusion_examples,
            "universe_scope": self.universe_scope,
            "batch_size": self.batch_size,
            "source_signal_run_id": self.source_signal_run_id,
            "source_signal_as_of_date": self.source_signal_as_of_date,
            "ranking_sort": "opportunity"
            if self.universe_scope == UNIVERSE_SCOPE_LATEST_OPPORTUNITY_TOP
            else None,
            "requested_top_n": self.requested_top_n,
            "selected_codes": self.selected_codes,
            "excluded_unavailable_opportunity_count": self.excluded_unavailable_opportunity_count,
        }


def parameter_grid(search_space: dict[str, list[float | int]] | None = None) -> list[dict[str, float | int]]:
    space = search_space or DEFAULT_SEARCH_SPACE
    keys = list(space.keys())
    return [dict(zip(keys, values, strict=True)) for values in product(*(space[key] for key in keys))]


def search_space_for_execution_model(execution_model: str) -> dict[str, list[float | int]]:
    if execution_model == EXECUTION_MODEL_INTRADAY_ALERT:
        return INTRADAY_SEARCH_SPACE
    return DEFAULT_SEARCH_SPACE


def volatility_unit_pct(points: list[HyperoptPricePoint], asset_bucket: str) -> float:
    closes = [point.close for point in points if point.close > 0]
    returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes)) if closes[index - 1] > 0]
    recent = returns[-20:]
    raw = pstdev(recent) * 100 if len(recent) > 1 else 1.2
    lower, upper = _BUCKET_LIMITS.get(asset_bucket, _BUCKET_LIMITS["unknown"])
    return clamp(raw, lower, upper)


def thresholds_from_params(
    params: dict[str, float | int],
    *,
    volatility_pct: float,
) -> dict[str, float]:
    return {
        "hard_stop_pct": -clamp(float(params["hard_stop_multiplier"]) * volatility_pct, 1.2, 7.0),
        "profit_start_pct": clamp(float(params["profit_start_multiplier"]) * volatility_pct, 2.0, 6.0),
        "trailing_giveback_pct": clamp(float(params["trailing_giveback_multiplier"]) * volatility_pct, 1.0, 4.0),
        "take_profit_watch_pct": clamp(float(params["take_profit_watch_pct"]), 2.0, 8.0),
    }


def simulate_exit_rule(
    points: list[HyperoptPricePoint],
    params: dict[str, float | int],
    *,
    asset_bucket: str = "unknown",
    enable_protection_guards: bool = False,
) -> dict[str, Any]:
    valid = [point for point in points if point.close > 0]
    if len(valid) < 20:
        return {
            "sample_count": 0,
            "trade_count": 0,
            "alert_count": 0,
            "win_rate": None,
            "total_return": None,
            "avg_trade_return": None,
            "max_drawdown": None,
            "turnover": None,
            "unfilled_count": 0,
            "false_exit_count": 0,
            "protected_exit_count": 0,
            "guard_suppressed_alert_count": 0,
            "guard_only_trend_count": 0,
            "cooldown_suppressed_count": 0,
            "repeated_stop_guard_count": 0,
            "missed_upside_rate": None,
            "protected_exit_rate": None,
        }

    vol_pct = volatility_unit_pct(valid, asset_bucket)
    thresholds = thresholds_from_params(params, volatility_pct=vol_pct)
    trend_confirm_days = max(1, int(params["trend_confirm_days"]))
    entry_price = valid[0].close
    peak_price = entry_price
    equity = 1.0
    peak_equity = 1.0
    max_drawdown = 0.0
    trade_returns: list[float] = []
    alert_count = 0
    false_exit_count = 0
    protected_exit_count = 0
    guard_suppressed_alert_count = 0
    guard_only_trend_count = 0
    cooldown_suppressed_count = 0
    repeated_stop_guard_count = 0
    cooldown_until_index = -1
    hard_stop_indexes: list[int] = []
    watch_alert_active = False
    negative_streak = 0
    index = 1

    while index < len(valid):
        current = valid[index].close
        previous = valid[index - 1].close
        if previous > 0 and current < previous:
            negative_streak += 1
        else:
            negative_streak = 0
        peak_price = max(peak_price, current)
        profit_pct = (current / entry_price - 1.0) * 100
        peak_profit_pct = (peak_price / entry_price - 1.0) * 100
        giveback_pct = peak_profit_pct - profit_pct
        in_trade_equity = equity * (current / entry_price)
        peak_equity = max(peak_equity, in_trade_equity)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, in_trade_equity / peak_equity - 1.0)

        exit_reason: str | None = None
        if profit_pct <= thresholds["hard_stop_pct"]:
            exit_reason = "hard_stop"
        elif (
            peak_profit_pct >= thresholds["profit_start_pct"]
            and giveback_pct >= thresholds["trailing_giveback_pct"]
        ):
            exit_reason = "trailing_take_profit"
        elif negative_streak >= trend_confirm_days and profit_pct < 0:
            exit_reason = "trend_weakening"
        elif profit_pct >= thresholds["take_profit_watch_pct"] and not watch_alert_active:
            alert_count += 1
            watch_alert_active = True

        if exit_reason is not None:
            if enable_protection_guards:
                recent_hard_stops = [value for value in hard_stop_indexes if index - value <= 40]
                trend_confirmed = (
                    profit_pct <= min(-1.0, thresholds["hard_stop_pct"] / 2)
                    or giveback_pct >= thresholds["trailing_giveback_pct"] * 0.75
                )
                if index < cooldown_until_index:
                    alert_count += 1
                    guard_suppressed_alert_count += 1
                    cooldown_suppressed_count += 1
                    index += 1
                    continue
                if exit_reason == "trend_weakening" and not trend_confirmed:
                    alert_count += 1
                    guard_suppressed_alert_count += 1
                    guard_only_trend_count += 1
                    index += 1
                    continue
                if exit_reason != "hard_stop" and len(recent_hard_stops) >= 2:
                    alert_count += 1
                    guard_suppressed_alert_count += 1
                    repeated_stop_guard_count += 1
                    index += 1
                    continue
            realized = current / entry_price - 1.0
            trade_returns.append(realized)
            alert_count += 1
            if exit_reason == "hard_stop":
                hard_stop_indexes.append(index)
            future = valid[index + 1 : index + 6]
            if future:
                max_future_return = max(point.close / current - 1.0 for point in future)
                min_future_return = min(point.close / current - 1.0 for point in future)
                if max_future_return * 100 >= max(1.0, vol_pct):
                    false_exit_count += 1
                if min_future_return * 100 <= -max(1.0, vol_pct):
                    protected_exit_count += 1
            equity *= 1.0 + realized
            peak_equity = max(peak_equity, equity)
            if enable_protection_guards:
                cooldown_until_index = index + 3
            index += 1
            if index >= len(valid):
                break
            entry_price = valid[index].close
            peak_price = entry_price
            watch_alert_active = False
            negative_streak = 0
        index += 1

    final_return = valid[-1].close / entry_price - 1.0
    total_return = equity * (1.0 + final_return) - 1.0
    trade_count = len(trade_returns)
    win_rate = sum(1 for value in trade_returns if value > 0) / trade_count if trade_count else None
    avg_trade_return = sum(trade_returns) / trade_count if trade_count else None
    turnover = trade_count / max(len(valid) / 20, 1)
    return {
        "sample_count": 1,
        "trade_count": trade_count,
        "alert_count": alert_count,
        "win_rate": win_rate,
        "total_return": total_return,
        "avg_trade_return": avg_trade_return,
        "max_drawdown": max_drawdown,
        "turnover": turnover,
        "unfilled_count": 0,
        "false_exit_count": false_exit_count,
        "protected_exit_count": protected_exit_count,
        "guard_suppressed_alert_count": guard_suppressed_alert_count,
        "guard_only_trend_count": guard_only_trend_count,
        "cooldown_suppressed_count": cooldown_suppressed_count,
        "repeated_stop_guard_count": repeated_stop_guard_count,
        "missed_upside_rate": false_exit_count / trade_count if trade_count else None,
        "protected_exit_rate": protected_exit_count / trade_count if trade_count else None,
        "volatility_unit_pct": vol_pct,
        "thresholds": thresholds,
        "protection_guard_version": PROTECTION_GUARD_VERSION if enable_protection_guards else None,
    }


def simulate_intraday_exit_rule(
    intraday_points: list[HyperoptIntradayPoint],
    params: dict[str, float | int],
    *,
    asset_bucket: str = "unknown",
    daily_points: list[HyperoptPricePoint] | None = None,
    allow_daily_fallback: bool = False,
    manual_delay_minutes: int = DEFAULT_MANUAL_DELAY_MINUTES,
    enable_protection_guards: bool = False,
) -> dict[str, Any]:
    _ = allow_daily_fallback
    eligible_points = [point for point in intraday_points if point.decision_eligible and point.price > 0]
    if not eligible_points:
        return {
            "sample_count": 0,
            "trade_count": 0,
            "alert_count": 0,
            "win_rate": None,
            "total_return": None,
            "avg_trade_return": None,
            "max_drawdown": None,
            "turnover": None,
            "unfilled_count": 0,
            "false_exit_count": 0,
            "protected_exit_count": 0,
            "guard_suppressed_alert_count": 0,
            "guard_only_trend_count": 0,
            "cooldown_suppressed_count": 0,
            "repeated_stop_guard_count": 0,
            "missed_upside_rate": None,
            "protected_exit_rate": None,
            "execution_model": EXECUTION_MODEL_INTRADAY_ALERT,
            "source_reliability": "unavailable",
            "missing_intraday_evidence_count": 1,
            "no_lookahead_exclusions": ["缺少可决策盘中行情，未用日线收盘价替代。"],
            "execution_delay_minutes": manual_delay_minutes,
        }

    if len(eligible_points) < MIN_INTRADAY_HISTORY_POINTS:
        return {
            "sample_count": 0,
            "trade_count": 0,
            "alert_count": 0,
            "win_rate": None,
            "total_return": None,
            "avg_trade_return": None,
            "max_drawdown": None,
            "turnover": None,
            "unfilled_count": 0,
            "false_exit_count": 0,
            "protected_exit_count": 0,
            "guard_suppressed_alert_count": 0,
            "guard_only_trend_count": 0,
            "cooldown_suppressed_count": 0,
            "repeated_stop_guard_count": 0,
            "missed_upside_rate": None,
            "protected_exit_rate": None,
            "execution_model": EXECUTION_MODEL_INTRADAY_ALERT,
            "source_reliability": "unavailable",
            "missing_intraday_evidence_count": 1,
            "no_lookahead_exclusions": ["可决策盘中行情样本不足，未用日线收盘价替代。"],
            "execution_delay_minutes": manual_delay_minutes,
        }

    threshold_points = daily_points if daily_points and len(daily_points) >= 20 else [
        HyperoptPricePoint(point.quote_time.date(), point.price)
        for point in eligible_points
    ]
    vol_pct = volatility_unit_pct(threshold_points, asset_bucket)
    thresholds = thresholds_from_params(params, volatility_pct=vol_pct)
    trend_confirm_days = max(1, int(params["trend_confirm_days"]))
    delay = timedelta(minutes=max(0, manual_delay_minutes))

    entry_price = eligible_points[0].price
    peak_price = entry_price
    equity = 1.0
    peak_equity = 1.0
    max_drawdown = 0.0
    trade_returns: list[float] = []
    alert_count = 0
    unfilled_count = 0
    false_exit_count = 0
    protected_exit_count = 0
    guard_suppressed_alert_count = 0
    guard_only_trend_count = 0
    cooldown_suppressed_count = 0
    repeated_stop_guard_count = 0
    cooldown_until_index = -1
    hard_stop_indexes: list[int] = []
    watch_alert_active = False
    negative_streak = 0
    index = 1

    while index < len(eligible_points):
        point = eligible_points[index]
        current = point.price
        previous = eligible_points[index - 1].price
        if previous > 0 and current < previous:
            negative_streak += 1
        else:
            negative_streak = 0
        peak_price = max(peak_price, current)
        profit_pct = (current / entry_price - 1.0) * 100
        peak_profit_pct = (peak_price / entry_price - 1.0) * 100
        giveback_pct = peak_profit_pct - profit_pct
        in_trade_equity = equity * (current / entry_price)
        peak_equity = max(peak_equity, in_trade_equity)
        if peak_equity > 0:
            max_drawdown = min(max_drawdown, in_trade_equity / peak_equity - 1.0)

        exit_reason: str | None = None
        if profit_pct <= thresholds["hard_stop_pct"]:
            exit_reason = "hard_stop"
        elif peak_profit_pct >= thresholds["profit_start_pct"] and giveback_pct >= thresholds["trailing_giveback_pct"]:
            exit_reason = "trailing_take_profit"
        elif negative_streak >= trend_confirm_days and profit_pct < 0:
            exit_reason = "trend_weakening"
        elif profit_pct >= thresholds["take_profit_watch_pct"] and not watch_alert_active:
            alert_count += 1
            watch_alert_active = True

        if exit_reason is not None:
            if enable_protection_guards:
                recent_hard_stops = [value for value in hard_stop_indexes if index - value <= 80]
                trend_confirmed = (
                    profit_pct <= min(-1.0, thresholds["hard_stop_pct"] / 2)
                    or giveback_pct >= thresholds["trailing_giveback_pct"] * 0.75
                )
                if index < cooldown_until_index:
                    alert_count += 1
                    guard_suppressed_alert_count += 1
                    cooldown_suppressed_count += 1
                    index += 1
                    continue
                if exit_reason == "trend_weakening" and not trend_confirmed:
                    alert_count += 1
                    guard_suppressed_alert_count += 1
                    guard_only_trend_count += 1
                    index += 1
                    continue
                if exit_reason != "hard_stop" and len(recent_hard_stops) >= 2:
                    alert_count += 1
                    guard_suppressed_alert_count += 1
                    repeated_stop_guard_count += 1
                    index += 1
                    continue
            alert_count += 1
            execute_after = point.quote_time + delay
            fill_index: int | None = None
            for candidate_index in range(index + 1, len(eligible_points)):
                if eligible_points[candidate_index].quote_time >= execute_after:
                    fill_index = candidate_index
                    break
            if fill_index is None:
                unfilled_count += 1
                index += 1
                continue

            fill = eligible_points[fill_index]
            realized = fill.price / entry_price - 1.0
            trade_returns.append(realized)
            if exit_reason == "hard_stop":
                hard_stop_indexes.append(index)
            future = eligible_points[fill_index + 1 : fill_index + 6]
            if future:
                max_future_return = max(point.price / fill.price - 1.0 for point in future)
                min_future_return = min(point.price / fill.price - 1.0 for point in future)
                if max_future_return * 100 >= max(1.0, vol_pct):
                    false_exit_count += 1
                if min_future_return * 100 <= -max(1.0, vol_pct):
                    protected_exit_count += 1
            equity *= 1.0 + realized
            peak_equity = max(peak_equity, equity)
            if enable_protection_guards:
                cooldown_until_index = fill_index + 12
            index = fill_index + 1
            if index >= len(eligible_points):
                break
            entry_price = eligible_points[index].price
            peak_price = entry_price
            watch_alert_active = False
            negative_streak = 0
            continue
        index += 1

    final_return = eligible_points[-1].price / entry_price - 1.0
    total_return = equity * (1.0 + final_return) - 1.0
    trade_count = len(trade_returns)
    win_rate = sum(1 for value in trade_returns if value > 0) / trade_count if trade_count else None
    avg_trade_return = sum(trade_returns) / trade_count if trade_count else None
    turnover = trade_count / max(len(eligible_points) / 80, 1)
    return {
        "sample_count": 1,
        "trade_count": trade_count,
        "alert_count": alert_count,
        "win_rate": win_rate,
        "total_return": total_return,
        "avg_trade_return": avg_trade_return,
        "max_drawdown": max_drawdown,
        "turnover": turnover,
        "unfilled_count": unfilled_count,
        "false_exit_count": false_exit_count,
        "protected_exit_count": protected_exit_count,
        "guard_suppressed_alert_count": guard_suppressed_alert_count,
        "guard_only_trend_count": guard_only_trend_count,
        "cooldown_suppressed_count": cooldown_suppressed_count,
        "repeated_stop_guard_count": repeated_stop_guard_count,
        "missed_upside_rate": false_exit_count / trade_count if trade_count else None,
        "protected_exit_rate": protected_exit_count / trade_count if trade_count else None,
        "volatility_unit_pct": vol_pct,
        "thresholds": thresholds,
        "execution_model": EXECUTION_MODEL_INTRADAY_ALERT,
        "source_reliability": "verified_intraday",
        "missing_intraday_evidence_count": 0,
        "no_lookahead_exclusions": [],
        "execution_delay_minutes": manual_delay_minutes,
        "protection_guard_version": PROTECTION_GUARD_VERSION if enable_protection_guards else None,
    }


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [item for item in results if item.get("sample_count")]
    if not usable:
        return {
            "sample_count": 0,
            "trade_count": 0,
            "alert_count": 0,
            "win_rate": None,
            "total_return": None,
            "avg_trade_return": None,
            "max_drawdown": None,
            "turnover": None,
            "unfilled_count": 0,
            "false_exit_count": 0,
            "protected_exit_count": 0,
            "guard_suppressed_alert_count": 0,
            "guard_only_trend_count": 0,
            "cooldown_suppressed_count": 0,
            "repeated_stop_guard_count": 0,
            "missed_upside_rate": None,
            "protected_exit_rate": None,
        }
    sample_count = sum(int(item.get("sample_count") or 0) for item in usable)
    trade_count = sum(int(item.get("trade_count") or 0) for item in usable)
    alert_count = sum(int(item.get("alert_count") or 0) for item in usable)
    unfilled_count = sum(int(item.get("unfilled_count") or 0) for item in usable)
    false_exit_count = sum(int(item.get("false_exit_count") or 0) for item in usable)
    protected_exit_count = sum(int(item.get("protected_exit_count") or 0) for item in usable)
    guard_suppressed_alert_count = sum(int(item.get("guard_suppressed_alert_count") or 0) for item in usable)
    guard_only_trend_count = sum(int(item.get("guard_only_trend_count") or 0) for item in usable)
    cooldown_suppressed_count = sum(int(item.get("cooldown_suppressed_count") or 0) for item in usable)
    repeated_stop_guard_count = sum(int(item.get("repeated_stop_guard_count") or 0) for item in usable)
    total_returns = [float(item["total_return"]) for item in usable if item.get("total_return") is not None]
    avg_trade_returns = [
        float(item["avg_trade_return"]) for item in usable if item.get("avg_trade_return") is not None
    ]
    drawdowns = [float(item["max_drawdown"]) for item in usable if item.get("max_drawdown") is not None]
    turnovers = [float(item["turnover"]) for item in usable if item.get("turnover") is not None]
    win_numerators = [
        float(item["win_rate"]) * int(item.get("trade_count") or 0)
        for item in usable
        if item.get("win_rate") is not None and int(item.get("trade_count") or 0) > 0
    ]
    return {
        "sample_count": sample_count,
        "trade_count": trade_count,
        "alert_count": alert_count,
        "win_rate": sum(win_numerators) / trade_count if trade_count and win_numerators else None,
        "total_return": sum(total_returns) / len(total_returns) if total_returns else None,
        "avg_trade_return": sum(avg_trade_returns) / len(avg_trade_returns) if avg_trade_returns else None,
        "max_drawdown": min(drawdowns) if drawdowns else None,
        "turnover": sum(turnovers) / len(turnovers) if turnovers else None,
        "unfilled_count": unfilled_count,
        "false_exit_count": false_exit_count,
        "protected_exit_count": protected_exit_count,
        "guard_suppressed_alert_count": guard_suppressed_alert_count,
        "guard_only_trend_count": guard_only_trend_count,
        "cooldown_suppressed_count": cooldown_suppressed_count,
        "repeated_stop_guard_count": repeated_stop_guard_count,
        "missed_upside_rate": false_exit_count / trade_count if trade_count else None,
        "protected_exit_rate": protected_exit_count / trade_count if trade_count else None,
    }


def _max_drawdown_from_values(values: list[float]) -> float | None:
    if not values:
        return None
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    return max_drawdown


def simulate_hold_baseline(points: list[HyperoptPricePoint]) -> dict[str, Any]:
    valid = [point.close for point in points if point.close > 0]
    if len(valid) < 2:
        return {
            "sample_count": 0,
            "trade_count": 0,
            "alert_count": 0,
            "win_rate": None,
            "total_return": None,
            "avg_trade_return": None,
            "max_drawdown": None,
            "turnover": 0.0,
            "unfilled_count": 0,
            "false_exit_count": 0,
            "protected_exit_count": 0,
            "missed_upside_rate": None,
            "protected_exit_rate": None,
        }
    return {
        "sample_count": 1,
        "trade_count": 0,
        "alert_count": 0,
        "win_rate": None,
        "total_return": valid[-1] / valid[0] - 1.0,
        "avg_trade_return": None,
        "max_drawdown": _max_drawdown_from_values(valid),
        "turnover": 0.0,
        "unfilled_count": 0,
        "false_exit_count": 0,
        "protected_exit_count": 0,
        "missed_upside_rate": None,
        "protected_exit_rate": None,
    }


def simulate_intraday_hold_baseline(points: list[HyperoptIntradayPoint]) -> dict[str, Any]:
    valid = [point.price for point in points if point.decision_eligible and point.price > 0]
    if len(valid) < 2:
        result = simulate_hold_baseline([])
        result.update(
            {
                "execution_model": EXECUTION_MODEL_INTRADAY_ALERT,
                "source_reliability": "unavailable",
                "missing_intraday_evidence_count": 1,
            }
        )
        return result
    result = simulate_hold_baseline(
        [HyperoptPricePoint(trade_date=date.min + timedelta(days=index), close=value) for index, value in enumerate(valid)]
    )
    result.update(
        {
            "execution_model": EXECUTION_MODEL_INTRADAY_ALERT,
            "source_reliability": "verified_intraday",
            "missing_intraday_evidence_count": 0,
        }
    )
    return result


def evaluate_hold_baseline(
    series: list[HyperoptSeries],
    *,
    execution_model: str = EXECUTION_MODEL_INTRADAY_ALERT,
) -> tuple[dict[str, Any], dict[str, Any]]:
    train_results: list[dict[str, Any]] = []
    oos_results: list[dict[str, Any]] = []
    for item in series:
        train_points, oos_points = _split_points(item.points)
        if execution_model == EXECUTION_MODEL_INTRADAY_ALERT:
            train_intraday, oos_intraday = _split_intraday_points(item.intraday_points)
            train_results.append(simulate_intraday_hold_baseline(train_intraday))
            if len(oos_intraday) >= MIN_INTRADAY_HISTORY_POINTS:
                oos_results.append(simulate_intraday_hold_baseline(oos_intraday))
        else:
            train_results.append(simulate_hold_baseline(train_points))
            if len(oos_points) >= 20:
                oos_results.append(simulate_hold_baseline(oos_points))
    return aggregate_metrics(train_results), aggregate_metrics(oos_results)


def policy_evidence_level(
    metrics: dict[str, Any],
    rolling_metrics: dict[str, Any],
    baseline_comparison: dict[str, Any],
) -> str:
    trade_count = int(metrics.get("trade_count") or 0)
    window_count = int(rolling_metrics.get("window_count") or 0)
    stable_window_rate = rolling_metrics.get("stable_window_rate")
    beats_baseline = baseline_comparison.get("beats_baseline") is True
    if not beats_baseline:
        return "low"
    if (
        trade_count >= 30
        and window_count >= 8
        and stable_window_rate is not None
        and float(stable_window_rate) >= 0.65
    ):
        return "high"
    if (
        trade_count >= 12
        and window_count >= 4
        and stable_window_rate is not None
        and float(stable_window_rate) >= 0.45
    ):
        return "medium"
    return "low"


def policy_validation_summary(
    *,
    hold_metrics: dict[str, Any],
    default_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    guard_enabled_metrics: dict[str, Any],
    candidate_params: dict[str, float | int],
    rolling_metrics: dict[str, Any],
    baseline_comparison: dict[str, Any],
    execution_model: str,
    manual_delay_minutes: int,
) -> dict[str, Any]:
    level = policy_evidence_level(candidate_metrics, rolling_metrics, baseline_comparison)
    return {
        "version": POLICY_VALIDATION_VERSION,
        "policy_class": "exit_risk_validation",
        "protection_guard_version": PROTECTION_GUARD_VERSION,
        "level": level,
        "research_only": True,
        "approved_for_live": False,
        "approval_status": "candidate",
        "auto_applied": False,
        "execution_model": execution_model,
        "manual_delay_minutes": manual_delay_minutes,
        "policies": {
            "hold_baseline": {
                "description": "不触发止盈止损，仅作为机会成本基准。",
                "metrics": hold_metrics,
            },
            "current_default": {
                "description": "当前线上默认止盈止损规则。",
                "parameters": DEFAULT_LIVE_PARAMS,
                "metrics": default_metrics,
            },
            "candidate_params": {
                "description": "参数搜索得到的研究候选，不自动生效。",
                "parameters": candidate_params,
                "metrics": candidate_metrics,
            },
            "guard_enabled_policy": {
                "description": "候选参数叠加保护层：未确认趋势只警戒，退出后冷却，连续止损后压制非硬止损动作。",
                "parameters": candidate_params,
                "protection_guard_version": PROTECTION_GUARD_VERSION,
                "metrics": guard_enabled_metrics,
            },
        },
        "baseline_comparison": baseline_comparison,
        "rolling_metrics": rolling_metrics,
    }


def stability_score(
    train_metrics: dict[str, Any],
    oos_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any] | None = None,
) -> float:
    oos_return = float(oos_metrics.get("total_return") or 0.0)
    oos_drawdown = abs(float(oos_metrics.get("max_drawdown") or 0.0))
    train_return = float(train_metrics.get("total_return") or 0.0)
    trade_count = int(oos_metrics.get("trade_count") or 0)
    alert_count = int(oos_metrics.get("alert_count") or 0)
    sample_count = max(int(oos_metrics.get("sample_count") or 0), 1)
    turnover = float(oos_metrics.get("turnover") or 0.0)
    missed_upside_rate = float(oos_metrics.get("missed_upside_rate") or 0.0)
    protected_exit_rate = float(oos_metrics.get("protected_exit_rate") or 0.0)
    overfit_penalty = max(0.0, train_return - oos_return - 0.08) * 100
    sparse_penalty = 18.0 if trade_count < 3 else 0.0
    baseline_penalty = 0.0
    if baseline_metrics and baseline_metrics.get("sample_count"):
        baseline_return = float(baseline_metrics.get("total_return") or 0.0)
        baseline_drawdown = abs(float(baseline_metrics.get("max_drawdown") or 0.0))
        baseline_false_exit_count = int(baseline_metrics.get("false_exit_count") or 0)
        false_exit_count = int(oos_metrics.get("false_exit_count") or 0)
        if oos_return < baseline_return:
            baseline_penalty += (baseline_return - oos_return) * 120
        if oos_drawdown > baseline_drawdown + 0.02:
            baseline_penalty += (oos_drawdown - baseline_drawdown) * 140
        if false_exit_count > baseline_false_exit_count:
            baseline_penalty += (false_exit_count - baseline_false_exit_count) * 2
    return round(
        100
        + oos_return * 160
        - oos_drawdown * 260
        - turnover * 5
        - (alert_count / sample_count) * 3
        - missed_upside_rate * 18
        + protected_exit_rate * 8
        - overfit_penalty
        - sparse_penalty
        - baseline_penalty,
        4,
    )


def baseline_comparison(candidate_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> dict[str, Any]:
    candidate_return = candidate_metrics.get("total_return")
    baseline_return = baseline_metrics.get("total_return")
    candidate_drawdown = candidate_metrics.get("max_drawdown")
    baseline_drawdown = baseline_metrics.get("max_drawdown")
    candidate_false_exit = int(candidate_metrics.get("false_exit_count") or 0)
    baseline_false_exit = int(baseline_metrics.get("false_exit_count") or 0)
    candidate_alert_count = int(candidate_metrics.get("alert_count") or 0)
    baseline_alert_count = int(baseline_metrics.get("alert_count") or 0)
    return_delta = (
        float(candidate_return) - float(baseline_return)
        if candidate_return is not None and baseline_return is not None
        else None
    )
    drawdown_delta = (
        float(candidate_drawdown) - float(baseline_drawdown)
        if candidate_drawdown is not None and baseline_drawdown is not None
        else None
    )
    beats_baseline = True
    reasons: list[str] = []
    if return_delta is not None and return_delta < -0.01:
        beats_baseline = False
        reasons.append("样本外收益低于当前默认规则。")
    if drawdown_delta is not None and drawdown_delta < -0.02:
        beats_baseline = False
        reasons.append("样本外最大回撤比当前默认规则更差。")
    if candidate_false_exit > baseline_false_exit + max(2, int(baseline_false_exit * 0.5)):
        beats_baseline = False
        reasons.append("过早离场次数明显多于当前默认规则。")
    if candidate_alert_count > baseline_alert_count + max(5, int(baseline_alert_count * 0.5)):
        beats_baseline = False
        reasons.append("邮件/提醒次数明显多于当前默认规则。")
    return {
        "return_delta": return_delta,
        "drawdown_delta": drawdown_delta,
        "false_exit_delta": candidate_false_exit - baseline_false_exit,
        "alert_count_delta": candidate_alert_count - baseline_alert_count,
        "beats_baseline": beats_baseline,
        "reasons": reasons,
    }


def classify_hyperopt_candidate(
    train_metrics: dict[str, Any],
    oos_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any] | None = None,
    rolling_metrics: dict[str, Any] | None = None,
) -> tuple[str, str]:
    sample_count = int(oos_metrics.get("sample_count") or 0)
    trade_count = int(oos_metrics.get("trade_count") or 0)
    train_return = train_metrics.get("total_return")
    oos_return = oos_metrics.get("total_return")
    train_drawdown = train_metrics.get("max_drawdown")
    oos_drawdown = oos_metrics.get("max_drawdown")
    if sample_count < 3 or trade_count < 2:
        return STATUS_EVIDENCE_INSUFFICIENT, CONCLUSION_INSUFFICIENT
    if train_return is not None and oos_return is not None and float(oos_return) < float(train_return) - 0.12:
        return STATUS_REJECTED, CONCLUSION_OVERFIT
    if train_drawdown is not None and oos_drawdown is not None and float(oos_drawdown) < float(train_drawdown) - 0.08:
        return STATUS_REJECTED, CONCLUSION_OVERFIT
    if oos_return is not None and float(oos_return) < -0.03:
        return STATUS_REJECTED, CONCLUSION_REJECTED
    if oos_drawdown is not None and float(oos_drawdown) < -0.12:
        return STATUS_REJECTED, CONCLUSION_REJECTED
    if baseline_metrics and int(baseline_metrics.get("sample_count") or 0) > 0:
        comparison = baseline_comparison(oos_metrics, baseline_metrics)
        if not comparison["beats_baseline"]:
            return STATUS_REJECTED, CONCLUSION_REJECTED
    if rolling_metrics and int(rolling_metrics.get("window_count") or 0) > 0:
        stable_window_rate = rolling_metrics.get("stable_window_rate")
        if stable_window_rate is not None and float(stable_window_rate) < 0.4:
            return STATUS_REJECTED, CONCLUSION_REJECTED
    return STATUS_CANDIDATE, CONCLUSION_CANDIDATE


def rejection_reason_for_candidate(
    status: str,
    conclusion: str,
    *,
    train_metrics: dict[str, Any],
    oos_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    rolling_metrics: dict[str, Any],
) -> str | None:
    if status == STATUS_CANDIDATE:
        return None
    sample_count = int(oos_metrics.get("sample_count") or 0)
    trade_count = int(oos_metrics.get("trade_count") or 0)
    if status == STATUS_EVIDENCE_INSUFFICIENT:
        return f"样本不足：样本 {sample_count}，触发交易 {trade_count}，不足以替代当前默认规则。"
    comparison = baseline_comparison(oos_metrics, baseline_metrics)
    if comparison["reasons"]:
        return "；".join(str(reason) for reason in comparison["reasons"])
    if conclusion == CONCLUSION_OVERFIT:
        return "样本内表现明显好于样本外，疑似过拟合。"
    stable_window_rate = rolling_metrics.get("stable_window_rate")
    if stable_window_rate is not None and float(stable_window_rate) < 0.4:
        return "滚动窗口稳定性不足。"
    return "样本外收益或回撤未达到稳健优先要求。"


def _split_points(points: list[HyperoptPricePoint]) -> tuple[list[HyperoptPricePoint], list[HyperoptPricePoint]]:
    split_index = max(20, int(len(points) * 0.7))
    return points[:split_index], points[split_index:]


def _split_intraday_points(
    points: list[HyperoptIntradayPoint],
) -> tuple[list[HyperoptIntradayPoint], list[HyperoptIntradayPoint]]:
    split_index = max(MIN_INTRADAY_HISTORY_POINTS, int(len(points) * 0.7))
    return points[:split_index], points[split_index:]


def evaluate_parameter_set(
    series: list[HyperoptSeries],
    params: dict[str, float | int],
    *,
    execution_model: str = EXECUTION_MODEL_INTRADAY_ALERT,
    manual_delay_minutes: int = DEFAULT_MANUAL_DELAY_MINUTES,
    enable_protection_guards: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    train_results: list[dict[str, Any]] = []
    oos_results: list[dict[str, Any]] = []
    for item in series:
        train_points, oos_points = _split_points(item.points)
        if execution_model == EXECUTION_MODEL_INTRADAY_ALERT:
            train_intraday, oos_intraday = _split_intraday_points(item.intraday_points)
            train_results.append(
                simulate_intraday_exit_rule(
                    train_intraday,
                    params,
                    asset_bucket=item.asset_bucket,
                    daily_points=train_points,
                    manual_delay_minutes=manual_delay_minutes,
                    enable_protection_guards=enable_protection_guards,
                )
            )
            if len(oos_intraday) >= MIN_INTRADAY_HISTORY_POINTS:
                oos_results.append(
                    simulate_intraday_exit_rule(
                        oos_intraday,
                        params,
                        asset_bucket=item.asset_bucket,
                        daily_points=oos_points,
                        manual_delay_minutes=manual_delay_minutes,
                        enable_protection_guards=enable_protection_guards,
                    )
                )
        else:
            train_results.append(
                simulate_exit_rule(
                    train_points,
                    params,
                    asset_bucket=item.asset_bucket,
                    enable_protection_guards=enable_protection_guards,
                )
            )
            if len(oos_points) >= 20:
                oos_results.append(
                    simulate_exit_rule(
                        oos_points,
                        params,
                        asset_bucket=item.asset_bucket,
                        enable_protection_guards=enable_protection_guards,
                    )
                )
    return aggregate_metrics(train_results), aggregate_metrics(oos_results)


def rolling_validation_metrics(
    series: list[HyperoptSeries],
    params: dict[str, float | int],
    *,
    window_size: int = 60,
    step_size: int = 20,
    execution_model: str = EXECUTION_MODEL_DAILY_CLOSE,
    manual_delay_minutes: int = DEFAULT_MANUAL_DELAY_MINUTES,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in series:
        if execution_model == EXECUTION_MODEL_INTRADAY_ALERT:
            intraday_points = item.intraday_points
            if len(intraday_points) < window_size:
                continue
            for start in range(0, len(intraday_points) - window_size + 1, step_size):
                window = intraday_points[start : start + window_size]
                results.append(
                    simulate_intraday_exit_rule(
                        window,
                        params,
                        asset_bucket=item.asset_bucket,
                        daily_points=item.points,
                        manual_delay_minutes=manual_delay_minutes,
                    )
                )
            continue
        points = item.points
        if len(points) < window_size:
            continue
        for start in range(0, len(points) - window_size + 1, step_size):
            window = points[start : start + window_size]
            results.append(simulate_exit_rule(window, params, asset_bucket=item.asset_bucket))

    usable = [row for row in results if row.get("total_return") is not None]
    if not usable:
        return {
            "window_count": 0,
            "stable_window_rate": None,
            "worst_window_return": None,
            "worst_window_drawdown": None,
            "avg_window_return": None,
        }

    returns = [float(row["total_return"]) for row in usable]
    drawdowns = [float(row["max_drawdown"]) for row in usable if row.get("max_drawdown") is not None]
    stable_count = sum(
        1
        for row in usable
        if float(row.get("total_return") or 0.0) >= -0.02
        and float(row.get("max_drawdown") or 0.0) >= -0.08
    )
    return {
        "window_count": len(usable),
        "stable_window_rate": stable_count / len(usable),
        "worst_window_return": min(returns),
        "worst_window_drawdown": min(drawdowns) if drawdowns else None,
        "avg_window_return": sum(returns) / len(returns),
    }


def confidence_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    sample_count = int(metrics.get("sample_count") or 0)
    trade_count = int(metrics.get("trade_count") or 0)
    raw_win_rate = metrics.get("win_rate")
    prior_sample = 20
    prior_win_rate = 0.5
    if raw_win_rate is None or trade_count <= 0:
        shrunk_win_rate = None
    else:
        shrunk_win_rate = (float(raw_win_rate) * trade_count + prior_win_rate * prior_sample) / (
            trade_count + prior_sample
        )
    evidence_score = min(1.0, sample_count / 20) * 0.5 + min(1.0, trade_count / 12) * 0.5
    if evidence_score >= 0.8:
        level = "较充分"
    elif evidence_score >= 0.45:
        level = "一般"
    else:
        level = "样本不足"
    return {
        "level": level,
        "evidence_score": round(evidence_score, 4),
        "shrunk_win_rate": shrunk_win_rate,
        "sample_count": sample_count,
        "trade_count": trade_count,
        "prior_win_rate": prior_win_rate,
        "prior_sample": prior_sample,
    }


def calibration_contract_hash(
    *,
    search_space: dict[str, list[float | int]],
    objective: str,
    execution_model: str = EXECUTION_MODEL_INTRADAY_ALERT,
) -> str:
    payload = {
        "rule_version": RULE_VERSION,
        "calibration_rule_version": CALIBRATION_RULE_VERSION,
        "execution_model": execution_model,
        "objective": objective,
        "search_space": search_space,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def best_candidate_for_bucket(
    series: list[HyperoptSeries],
    *,
    execution_model: str,
    manual_delay_minutes: int,
) -> dict[str, Any]:
    search_space = search_space_for_execution_model(execution_model)
    _hold_train_metrics, hold_oos_metrics = evaluate_hold_baseline(series, execution_model=execution_model)
    baseline_train_metrics, baseline_oos_metrics = evaluate_parameter_set(
        series,
        DEFAULT_LIVE_PARAMS,
        execution_model=execution_model,
        manual_delay_minutes=manual_delay_minutes,
    )
    best: dict[str, Any] | None = None
    for params in parameter_grid(search_space):
        train_metrics, oos_metrics = evaluate_parameter_set(
            series,
            params,
            execution_model=execution_model,
            manual_delay_minutes=manual_delay_minutes,
        )
        score = stability_score(train_metrics, oos_metrics, baseline_oos_metrics)
        status, conclusion = classify_hyperopt_candidate(
            train_metrics,
            oos_metrics,
            baseline_oos_metrics,
            None,
        )
        comparison = baseline_comparison(oos_metrics, baseline_oos_metrics)
        rejection_reason = rejection_reason_for_candidate(
            status,
            conclusion,
            train_metrics=train_metrics,
            oos_metrics=oos_metrics,
            baseline_metrics=baseline_oos_metrics,
            rolling_metrics=None,
        )
        candidate = {
            "params": params,
            "train_metrics": train_metrics,
            "out_of_sample_metrics": oos_metrics,
            "rolling_metrics": {},
            "score": score,
            "status": status,
            "conclusion": conclusion,
            "baseline_metrics": baseline_oos_metrics,
            "baseline_comparison": comparison,
            "rejection_reason": rejection_reason,
            "sample_count": int(oos_metrics.get("sample_count") or 0),
            "trade_count": int(oos_metrics.get("trade_count") or 0),
        }
        if best is None or score > float(best["score"]):
            best = candidate
    assert best is not None
    rolling_metrics = rolling_validation_metrics(
        series,
        best["params"],
        execution_model=execution_model,
        manual_delay_minutes=manual_delay_minutes,
    )
    _guard_train_metrics, guard_oos_metrics = evaluate_parameter_set(
        series,
        best["params"],
        execution_model=execution_model,
        manual_delay_minutes=manual_delay_minutes,
        enable_protection_guards=True,
    )
    status, conclusion = classify_hyperopt_candidate(
        best["train_metrics"],
        best["out_of_sample_metrics"],
        baseline_oos_metrics,
        rolling_metrics,
    )
    best["status"] = status
    best["conclusion"] = conclusion
    best["rolling_metrics"] = rolling_metrics
    best["rejection_reason"] = rejection_reason_for_candidate(
        status,
        conclusion,
        train_metrics=best["train_metrics"],
        oos_metrics=best["out_of_sample_metrics"],
        baseline_metrics=baseline_oos_metrics,
        rolling_metrics=rolling_metrics,
    )
    confidence = confidence_summary(best["out_of_sample_metrics"])
    policy_validation = policy_validation_summary(
        hold_metrics=hold_oos_metrics,
        default_metrics=baseline_oos_metrics,
        candidate_metrics=best["out_of_sample_metrics"],
        guard_enabled_metrics=guard_oos_metrics,
        candidate_params=best["params"],
        rolling_metrics=best["rolling_metrics"],
        baseline_comparison=best["baseline_comparison"],
        execution_model=execution_model,
        manual_delay_minutes=manual_delay_minutes,
    )
    confidence.update(
        {
            "baseline_metrics": best["baseline_metrics"],
            "baseline_comparison": best["baseline_comparison"],
            "hold_baseline_metrics": hold_oos_metrics,
            "policy_validation": policy_validation,
            "guard_enabled_metrics": guard_oos_metrics,
            "protection_guard_version": PROTECTION_GUARD_VERSION,
            "policy_evidence_level": policy_validation["level"],
            "policy_class": policy_validation["policy_class"],
            "approval_status": policy_validation["approval_status"],
            "approved_for_live": False,
            "rejection_reason": best["rejection_reason"],
            "execution_model": execution_model,
            "manual_delay_minutes": manual_delay_minutes,
        }
    )
    best["confidence"] = confidence
    best["source_reliability"] = (
        "verified_intraday" if execution_model == EXECUTION_MODEL_INTRADAY_ALERT else "verified_daily_close"
    )
    return best


async def _load_series(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    max_assets: int | None,
    execution_model: str,
    universe_scope: str = UNIVERSE_SCOPE_ALL_ELIGIBLE,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[list[HyperoptSeries], HyperoptCoverage, datetime | None]:
    all_etf_count = int(await session.scalar(select(func.count()).select_from(TradableEtf)) or 0)
    eligible_count = int(
        await session.scalar(
            select(func.count())
            .select_from(TradableEtf)
            .where(TradableEtf.is_short_term_eligible.is_(True))
        )
        or 0
    )
    universe_metadata: dict[str, Any] = {}
    if universe_scope == UNIVERSE_SCOPE_LATEST_OPPORTUNITY_TOP:
        selected_codes, universe_metadata = await _latest_opportunity_codes(session, max_assets=max_assets)
        code_order = {code: index for index, code in enumerate(selected_codes)}
        etfs = (
            await session.execute(
                select(TradableEtf, EtfThemeProfile)
                .outerjoin(EtfThemeProfile, EtfThemeProfile.etf_code == TradableEtf.code)
                .where(
                    TradableEtf.is_short_term_eligible.is_(True),
                    TradableEtf.code.in_(selected_codes),
                )
            )
        ).all()
        etfs = sorted(etfs, key=lambda row: code_order.get(row[0].code, len(code_order)))
    else:
        etf_stmt = (
            select(TradableEtf, EtfThemeProfile)
            .outerjoin(EtfThemeProfile, EtfThemeProfile.etf_code == TradableEtf.code)
            .where(TradableEtf.is_short_term_eligible.is_(True))
            .order_by(TradableEtf.code.asc())
        )
        if max_assets is not None:
            etf_stmt = etf_stmt.limit(max_assets)
        etfs = (
            await session.execute(etf_stmt)
        ).all()
    codes = [row[0].code for row in etfs]
    if not codes:
        coverage = HyperoptCoverage(
            all_etf_count=all_etf_count,
            eligible_count=eligible_count,
            selected_count=0,
            enough_daily_history_count=0,
            enough_intraday_history_count=0,
            final_optimized_count=0,
            sampled=False,
            max_assets=max_assets,
            exclusions={},
            exclusion_examples={},
            universe_scope=universe_scope,
            batch_size=batch_size,
        )
        return [], coverage, None
    history_rows = []
    for code_batch in _chunks(codes, batch_size):
        rows = await session.execute(
            select(EtfPriceHistory.etf_code, EtfPriceHistory.trade_date, EtfPriceHistory.close)
            .where(
                EtfPriceHistory.etf_code.in_(code_batch),
                EtfPriceHistory.trade_date >= start_date,
                EtfPriceHistory.trade_date <= end_date,
            )
            .order_by(EtfPriceHistory.etf_code.asc(), EtfPriceHistory.trade_date.asc())
        )
        history_rows.extend(rows.all())
    history_by_code: dict[str, list[HyperoptPricePoint]] = {}
    for etf_code, trade_date, close in history_rows:
        history_by_code.setdefault(etf_code, []).append(HyperoptPricePoint(trade_date, float(close)))

    intraday_by_code: dict[str, list[HyperoptIntradayPoint]] = {}
    latest_intraday_time: datetime | None = None
    if execution_model == EXECUTION_MODEL_INTRADAY_ALERT:
        intraday_start_date = max(start_date, end_date - timedelta(days=MAX_INTRADAY_HYPEROPT_DAYS))
        intraday_rows = []
        for code_batch in _chunks(codes, batch_size):
            rows = await session.execute(
                select(EtfIntradayQuote.etf_code, EtfIntradayQuote.quote_time, EtfIntradayQuote.latest_price)
                .where(
                    EtfIntradayQuote.etf_code.in_(code_batch),
                    EtfIntradayQuote.trade_date >= intraday_start_date,
                    EtfIntradayQuote.trade_date <= end_date,
                    EtfIntradayQuote.latest_price > 0,
                    EtfIntradayQuote.freshness_status == "fresh",
                    (func.extract("minute", EtfIntradayQuote.quote_time) % INTRADAY_HYPEROPT_BAR_MINUTES) == 0,
                )
                .order_by(EtfIntradayQuote.etf_code.asc(), EtfIntradayQuote.quote_time.asc())
            )
            intraday_rows.extend(rows.all())
        for etf_code, quote_time, latest_price in intraday_rows:
            intraday_by_code.setdefault(etf_code, []).append(
                HyperoptIntradayPoint(quote_time, float(latest_price), True)
            )
            latest_intraday_time = max(latest_intraday_time, quote_time) if latest_intraday_time else quote_time

    series: list[HyperoptSeries] = []
    exclusions: dict[str, int] = {}
    exclusion_examples: dict[str, list[str]] = {}
    enough_daily_history_count = 0
    enough_intraday_history_count = 0

    def record_exclusion(reason: str, code: str) -> None:
        exclusions[reason] = exclusions.get(reason, 0) + 1
        examples = exclusion_examples.setdefault(reason, [])
        if len(examples) < 8:
            examples.append(code)

    for etf, profile in etfs:
        points = history_by_code.get(etf.code, [])
        if len(points) >= MIN_DAILY_HISTORY_POINTS:
            enough_daily_history_count += 1
        else:
            record_exclusion("insufficient_daily_history", etf.code)
            continue
        intraday_points = intraday_by_code.get(etf.code, [])
        if len(intraday_points) >= MIN_INTRADAY_HISTORY_POINTS:
            enough_intraday_history_count += 1
        elif execution_model == EXECUTION_MODEL_INTRADAY_ALERT:
            record_exclusion("insufficient_intraday_history", etf.code)
            continue
        series.append(
            HyperoptSeries(
                code=etf.code,
                name=etf.name,
                asset_bucket=(profile.asset_bucket if profile else etf.asset_class) or "unknown",
                theme_group=(profile.theme_group if profile else "unknown") or "unknown",
                points=points,
                intraday_points=intraday_points,
            )
        )
    coverage = HyperoptCoverage(
        all_etf_count=all_etf_count,
        eligible_count=eligible_count,
        selected_count=len(codes),
        enough_daily_history_count=enough_daily_history_count,
        enough_intraday_history_count=enough_intraday_history_count,
        final_optimized_count=len(series),
        sampled=max_assets is not None and len(codes) < eligible_count,
        max_assets=max_assets,
        exclusions=exclusions,
        exclusion_examples=exclusion_examples,
        universe_scope=universe_scope,
        batch_size=batch_size,
        source_signal_run_id=universe_metadata.get("source_signal_run_id"),
        source_signal_as_of_date=universe_metadata.get("source_signal_as_of_date"),
        requested_top_n=universe_metadata.get("requested_top_n"),
        selected_codes=list(universe_metadata.get("selected_codes") or codes),
        excluded_unavailable_opportunity_count=int(
            universe_metadata.get("excluded_unavailable_opportunity_count") or 0
        ),
    )
    return series, coverage, latest_intraday_time


def _bucket_series(
    series: list[HyperoptSeries],
    *,
    execution_model: str = EXECUTION_MODEL_DAILY_CLOSE,
) -> dict[tuple[str, str], list[HyperoptSeries]]:
    buckets: dict[tuple[str, str], list[HyperoptSeries]] = {("all", "all"): list(series)}
    for item in series:
        buckets.setdefault(("asset_bucket", item.asset_bucket or "unknown"), []).append(item)
        if execution_model != EXECUTION_MODEL_INTRADAY_ALERT:
            buckets.setdefault(("theme_group", item.theme_group or "unknown"), []).append(item)
        vol = volatility_unit_pct(item.points, item.asset_bucket)
        if vol < 1.0:
            volatility_bucket = "low_volatility"
        elif vol < 2.5:
            volatility_bucket = "mid_volatility"
        else:
            volatility_bucket = "high_volatility"
        buckets.setdefault(("volatility", volatility_bucket), []).append(item)
    return {key: value for key, value in buckets.items() if key == ("all", "all") or len(value) >= 3}


def _chunks(values: list[str], batch_size: int) -> list[list[str]]:
    size = max(1, batch_size)
    return [values[index : index + size] for index in range(0, len(values), size)]


async def _latest_opportunity_codes(
    session: AsyncSession,
    *,
    max_assets: int | None,
) -> tuple[list[str], dict[str, Any]]:
    requested_top_n = max_assets or 50
    source_run = await latest_signal_run(session, asset_type=ASSET_TYPE_ETF)
    if source_run is None:
        raise ValueError("等待信号生成：没有最新成功 ETF 信号 run。")

    items = (
        await session.scalars(
            select(ShortResearchSignalItem)
            .where(
                ShortResearchSignalItem.run_id == source_run.id,
                ShortResearchSignalItem.asset_type == ASSET_TYPE_ETF,
            )
            .order_by(ShortResearchSignalItem.rank.asc(), ShortResearchSignalItem.asset_code.asc())
        )
    ).all()
    ranked: list[tuple[float, str]] = []
    unavailable_count = 0
    for item in items:
        metrics = dict(item.metrics_json or {})
        score = metrics.get("opportunity_score")
        if not isinstance(score, int | float) or not has_available_opportunity_score(metrics):
            unavailable_count += 1
            continue
        ranked.append((float(score), item.asset_code))

    ranked.sort(key=lambda row: (-row[0], row[1]))
    selected = ranked[:requested_top_n]
    if not selected:
        raise ValueError(f"等待信号生成：最新 ETF 信号 run {source_run.id} 没有可用综合关注评分。")

    codes = [code for _score, code in selected]
    return codes, {
        "source_signal_run_id": source_run.id,
        "source_signal_as_of_date": source_run.as_of_date.isoformat(),
        "requested_top_n": requested_top_n,
        "selected_codes": codes,
        "excluded_unavailable_opportunity_count": unavailable_count,
    }


async def run_etf_exit_hyperopt(
    session: AsyncSession,
    *,
    days: int = 730,
    max_assets: int | None = None,
    objective: str = OBJECTIVE_STABILITY_FIRST,
    execution_model: str = EXECUTION_MODEL_INTRADAY_ALERT,
    manual_delay_minutes: int = DEFAULT_MANUAL_DELAY_MINUTES,
    universe_scope: str = UNIVERSE_SCOPE_ALL_ELIGIBLE,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> EtfExitHyperoptRun:
    search_space = search_space_for_execution_model(execution_model)
    latest_date = await session.scalar(select(EtfPriceHistory.trade_date).order_by(desc(EtfPriceHistory.trade_date)).limit(1))
    as_of_date = latest_date or date.today()
    start_date = as_of_date - timedelta(days=days)
    train_cutoff = start_date + timedelta(days=int(days * 0.7))
    run = EtfExitHyperoptRun(
        status="running",
        started_at=utcnow(),
        as_of_date=as_of_date,
        objective=objective,
        rule_version=RULE_VERSION,
        calibration_rule_version=CALIBRATION_RULE_VERSION,
        execution_model=execution_model,
        contract_hash=calibration_contract_hash(
            search_space=search_space,
            objective=objective,
            execution_model=execution_model,
        ),
        data_cutoff=utcnow(),
        train_range_json={"start_date": start_date.isoformat(), "end_date": train_cutoff.isoformat()},
        out_of_sample_range_json={"start_date": train_cutoff.isoformat(), "end_date": as_of_date.isoformat()},
        search_space_json=search_space,
        bucket_summary_json={},
        summary_json={},
        created_at=utcnow(),
    )
    session.add(run)
    await session.flush()

    try:
        series, coverage, latest_intraday_time = await _load_series(
            session,
            start_date=start_date,
            end_date=as_of_date,
            max_assets=max_assets,
            execution_model=execution_model,
            universe_scope=universe_scope,
            batch_size=batch_size,
        )
        if latest_intraday_time is not None:
            run.data_cutoff = latest_intraday_time
        buckets = _bucket_series(series, execution_model=execution_model)
        item_count = 0
        candidate_count = 0
        rejected_count = 0
        evidence_insufficient_count = 0
        bucket_summary: dict[str, int] = {}
        baseline_metrics_by_bucket: dict[str, dict[str, Any]] = {}
        policy_validation_by_bucket: dict[str, dict[str, Any]] = {}
        for (bucket_type, bucket_key), bucket_items in buckets.items():
            best = best_candidate_for_bucket(
                bucket_items,
                execution_model=execution_model,
                manual_delay_minutes=manual_delay_minutes,
            )
            if best["status"] == STATUS_CANDIDATE:
                candidate_count += 1
            elif best["status"] == STATUS_EVIDENCE_INSUFFICIENT:
                evidence_insufficient_count += 1
            else:
                rejected_count += 1
            item_count += 1
            bucket_summary[bucket_type] = bucket_summary.get(bucket_type, 0) + 1
            bucket_identifier = f"{bucket_type}:{bucket_key}"
            baseline_metrics_by_bucket[bucket_identifier] = dict(best.get("baseline_metrics") or {})
            policy_validation_by_bucket[bucket_identifier] = dict(
                (best.get("confidence") or {}).get("policy_validation") or {}
            )
            session.add(
                EtfExitHyperoptItem(
                    run_id=run.id,
                    bucket_type=bucket_type,
                    bucket_key=bucket_key,
                    status=best["status"],
                    conclusion=best["conclusion"],
                    parameter_json=best["params"],
                    train_metrics_json=best["train_metrics"],
                    out_of_sample_metrics_json=best["out_of_sample_metrics"],
                    rolling_metrics_json=best["rolling_metrics"],
                    confidence_json=best["confidence"],
                    source_reliability=best["source_reliability"],
                    score=best["score"],
                    sample_count=best["sample_count"],
                    trade_count=best["trade_count"],
                    created_at=utcnow(),
                )
            )
        run.status = "success"
        run.finished_at = utcnow()
        run.bucket_summary_json = bucket_summary
        run.summary_json = {
            "objective": objective,
            "rule_version": RULE_VERSION,
            "calibration_rule_version": CALIBRATION_RULE_VERSION,
            "policy_validation_version": POLICY_VALIDATION_VERSION,
            "protection_guard_version": PROTECTION_GUARD_VERSION,
            "policy_class": "exit_risk_validation",
            "approved_for_live": False,
            "approval_status": "research_only",
            "execution_model": execution_model,
            "contract_hash": run.contract_hash,
            "asset_count": len(series),
            "coverage": coverage.as_dict(),
            "coverage_funnel": coverage.as_dict(),
            "universe_scope": universe_scope,
            "sampled": coverage.sampled,
            "max_assets": max_assets,
            "batch_size": batch_size,
            "final_optimized_count": coverage.final_optimized_count,
            "enough_daily_history_count": coverage.enough_daily_history_count,
            "enough_intraday_history_count": coverage.enough_intraday_history_count,
            "manual_delay_minutes": manual_delay_minutes,
            "intraday_hyperopt_days": MAX_INTRADAY_HYPEROPT_DAYS
            if execution_model == EXECUTION_MODEL_INTRADAY_ALERT
            else None,
            "intraday_bar_minutes": INTRADAY_HYPEROPT_BAR_MINUTES
            if execution_model == EXECUTION_MODEL_INTRADAY_ALERT
            else None,
            "bucket_count": item_count,
            "candidate_count": candidate_count,
            "rejected_count": rejected_count,
            "evidence_insufficient_count": evidence_insufficient_count,
            "baseline_metrics_by_bucket": baseline_metrics_by_bucket,
            "policy_validation_by_bucket": policy_validation_by_bucket,
            "parameter_count": len(parameter_grid(search_space)),
            "auto_applied": False,
            "research_only": True,
            "no_trade_instruction": True,
        }
    except Exception as exc:
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_message = str(exc)
        run.summary_json = {
            "objective": objective,
            "execution_model": execution_model,
            "universe_scope": universe_scope,
            "batch_size": batch_size,
            "research_only": True,
            "auto_applied": False,
        }
        raise
    finally:
        await session.commit()
        await session.refresh(run)
    return run


async def latest_etf_exit_hyperopt_run(session: AsyncSession) -> EtfExitHyperoptRun | None:
    return await session.scalar(
        select(EtfExitHyperoptRun).order_by(desc(EtfExitHyperoptRun.finished_at), desc(EtfExitHyperoptRun.id)).limit(1)
    )


async def etf_exit_hyperopt_payload(session: AsyncSession, run: EtfExitHyperoptRun) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(EtfExitHyperoptItem)
            .where(EtfExitHyperoptItem.run_id == run.id)
            .order_by(EtfExitHyperoptItem.score.desc(), EtfExitHyperoptItem.id.asc())
        )
    ).all()
    summary = dict(run.summary_json or {})
    coverage_status = "sampled" if summary.get("sampled") else "full_universe"
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "as_of_date": run.as_of_date,
        "objective": run.objective,
        "rule_version": run.rule_version,
        "calibration_rule_version": run.calibration_rule_version,
        "execution_model": run.execution_model,
        "contract_hash": run.contract_hash,
        "data_cutoff": run.data_cutoff,
        "train_range": dict(run.train_range_json or {}),
        "out_of_sample_range": dict(run.out_of_sample_range_json or {}),
        "search_space": dict(run.search_space_json or {}),
        "bucket_summary": dict(run.bucket_summary_json or {}),
        "summary": summary,
        "error_message": run.error_message,
        "research_only": True,
        "no_trade_instruction": True,
        "items": [
            {
                "id": item.id,
                "bucket_type": item.bucket_type,
                "bucket_key": item.bucket_key,
                "status": item.status,
                "conclusion": item.conclusion,
                "parameters": dict(item.parameter_json or {}),
                "train_metrics": dict(item.train_metrics_json or {}),
                "out_of_sample_metrics": dict(item.out_of_sample_metrics_json or {}),
                "rolling_metrics": dict(item.rolling_metrics_json or {}),
                "baseline_metrics": dict((item.confidence_json or {}).get("baseline_metrics") or {}),
                "baseline_comparison": dict((item.confidence_json or {}).get("baseline_comparison") or {}),
                "rejection_reason": (item.confidence_json or {}).get("rejection_reason"),
                "coverage_status": coverage_status,
                "manual_delay_minutes": (item.confidence_json or {}).get("manual_delay_minutes"),
                "policy_class": (item.confidence_json or {}).get("policy_class"),
                "approval_status": (item.confidence_json or {}).get("approval_status"),
                "approved_for_live": bool((item.confidence_json or {}).get("approved_for_live") is True),
                "protection_guard_version": (item.confidence_json or {}).get("protection_guard_version"),
                "guard_enabled_metrics": dict((item.confidence_json or {}).get("guard_enabled_metrics") or {}),
                "confidence": dict(item.confidence_json or {}),
                "source_reliability": item.source_reliability,
                "score": item.score,
                "sample_count": item.sample_count,
                "trade_count": item.trade_count,
                "approved_at": item.approved_at,
                "created_at": item.created_at,
            }
            for item in rows
        ],
    }
