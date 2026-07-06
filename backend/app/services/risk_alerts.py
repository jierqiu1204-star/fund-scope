from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.defaults.short_research import ASSET_TYPE_ETF

ALERT_EXIT_WATCH = "exit_watch"
ALERT_RISK_WARNING = "risk_warning"
ALERT_TAKE_PROFIT_WATCH = "take_profit_watch"
ALERT_TRAILING_TAKE_PROFIT = "trailing_take_profit"
ALERT_TREND_WEAKENING = "trend_weakening"
ALERT_CONFIRMED_TREND_WEAKENING = "confirmed_trend_weakening"
ALERT_HARD_STOP = "hard_stop"

ACTION_CLASS_NONE = "none"
ACTION_CLASS_ACTIONABLE_EXIT = "actionable_exit"
ACTION_CLASS_SOFT_WATCH = "soft_watch"
ACTION_CLASS_GUARD_ONLY = "guard_only"
ACTION_CLASS_DATA_WAITING = "data_waiting"
ACTION_CLASS_RESEARCH_ONLY = "research_only"

EMAIL_ALERT_TYPES = {
    ALERT_EXIT_WATCH,
    ALERT_TAKE_PROFIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_CONFIRMED_TREND_WEAKENING,
    ALERT_HARD_STOP,
}

TAKE_PROFIT_WATCH_PCT = 3.0
TRAILING_START_PROFIT_PCT = 5.0
TRAILING_GIVEBACK_POINTS = 2.5
TRAILING_GIVEBACK_RATIO = 0.35
ETF_TRAILING_PROFIT_START_VOL_MULTIPLIER = 1.1
ETF_TRAILING_PROFIT_START_MIN_PCT = 3.0
ETF_TRAILING_PROFIT_START_MAX_PCT = 4.0
ETF_TRAILING_GIVEBACK_VOL_MULTIPLIER = 0.65
ETF_TRAILING_GIVEBACK_MIN_PCT = 1.8
ETF_TRAILING_GIVEBACK_MAX_PCT = 2.5
HARD_STOP_LOSS_PCT = -4.0
TAKE_PROFIT_WATCH_COOLDOWN_DAYS = 3
DEFAULT_ETF_TRADING_CAPITAL = 10000.0
ETF_SINGLE_WEIGHT_CAP = 0.30

POSITION_ACTION_HOLD = "hold"
POSITION_ACTION_TRIM = "trim"
POSITION_ACTION_REDUCE = "reduce"
POSITION_ACTION_EXIT = "exit"
POSITION_ACTION_ADD = "add"

SELL_ALERT_TYPES = {
    ALERT_EXIT_WATCH,
    ALERT_TAKE_PROFIT_WATCH,
    ALERT_TRAILING_TAKE_PROFIT,
    ALERT_CONFIRMED_TREND_WEAKENING,
    ALERT_HARD_STOP,
}


@dataclass(frozen=True)
class AlertDecision:
    alert_type: str
    trigger_label: str
    reasons: list[str]
    risk_flags: list[str]
    advisor_summary: str | None
    signal_item: Any | None
    advisor_report: Any | None
    alert_level: str = "warning"
    alert_source: str = "daily_close"
    quote_time: datetime | None = None


@dataclass(frozen=True)
class PositionSizingRecommendation:
    action: str
    label: str
    current_market_value: float | None = None
    current_account_weight: float | None = None
    target_account_weight: float | None = None
    recommended_trade_amount: float | None = None
    recommended_trade_shares: float | None = None
    reason: str | None = None

    def as_context(self) -> dict[str, Any]:
        return {
            "position_action": self.action,
            "recommended_action_label": self.label,
            "current_market_value": self.current_market_value,
            "current_account_weight": self.current_account_weight,
            "target_account_weight": self.target_account_weight,
            "recommended_trade_amount": self.recommended_trade_amount,
            "recommended_trade_shares": self.recommended_trade_shares,
            "position_sizing_reason": self.reason,
        }


def _round_trade_amount(value: float) -> float:
    return float(round(value / 10.0) * 10)


def _round_trade_shares(value: float | None) -> float | None:
    return float(round(value)) if value is not None else None


def position_action_label(action: str) -> str:
    return {
        POSITION_ACTION_HOLD: "继续观察",
        POSITION_ACTION_TRIM: "轻度减仓",
        POSITION_ACTION_REDUCE: "明显减仓",
        POSITION_ACTION_EXIT: "清仓",
        POSITION_ACTION_ADD: "可加仓",
    }.get(action, "继续观察")


def entry_timing_allows_add(label: str | None) -> bool:
    return label not in {"冲高别追", "跌破等待", "放量转弱", "数据不足", "休市", "行情滞后"}


def calculate_position_sizing(
    *,
    asset_type: str,
    alert_type: str | None,
    current_market_value: float | None,
    current_price: float | None,
    etf_trading_capital: float | None,
    allow_full_exit: bool,
    target_portfolio_weight: float | None = None,
    entry_timing_label: str | None = None,
    trend_weakening: bool = False,
) -> PositionSizingRecommendation:
    if asset_type != ASSET_TYPE_ETF:
        return PositionSizingRecommendation(
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="仓位金额建议第一版只用于场内 ETF。",
        )
    capital = etf_trading_capital or DEFAULT_ETF_TRADING_CAPITAL
    if capital <= 0 or current_market_value is None or current_price is None or current_price <= 0:
        return PositionSizingRecommendation(
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="等待可信价格和份额后再计算仓位金额。",
        )
    current_weight = current_market_value / capital
    target_weight = current_weight
    action = POSITION_ACTION_HOLD
    reason = "暂无卖出/减仓信号；未给出额外买卖金额。"

    if alert_type in {ALERT_HARD_STOP, ALERT_EXIT_WATCH}:
        action = POSITION_ACTION_EXIT if allow_full_exit else POSITION_ACTION_REDUCE
        target_weight = 0.0 if allow_full_exit else current_weight * 0.5
        reason = "触发硬止损或退出观察，按风控规则给出清仓/半仓处理参考。"
    elif alert_type == ALERT_TRAILING_TAKE_PROFIT:
        action = POSITION_ACTION_EXIT if trend_weakening and allow_full_exit else POSITION_ACTION_REDUCE
        target_weight = 0.0 if trend_weakening and allow_full_exit else current_weight * 0.5
        reason = "触发移动止盈，先保护已获得利润；若趋势也转弱则允许清仓。"
    elif alert_type == ALERT_CONFIRMED_TREND_WEAKENING:
        action = POSITION_ACTION_REDUCE
        target_weight = current_weight * 0.5
        reason = "趋势转弱已被亏损、回吐或市场环境确认，按半仓处理参考降低暴露。"
    elif alert_type == ALERT_TREND_WEAKENING:
        reason = "趋势转弱当前只是风险警戒，不作为卖出或减仓金额建议。"
    elif alert_type == ALERT_TAKE_PROFIT_WATCH:
        action = POSITION_ACTION_TRIM
        target_weight = current_weight * 0.7
        reason = "触发止盈观察，先按轻度减仓保护利润。"
    elif target_portfolio_weight is not None and entry_timing_allows_add(entry_timing_label):
        capped_target = min(max(target_portfolio_weight, 0.0), ETF_SINGLE_WEIGHT_CAP)
        if capped_target > current_weight:
            action = POSITION_ACTION_ADD
            target_weight = capped_target
            reason = "最新 ETF 观察组合目标权重大于当前持仓，且买点状态未进入等待/追高，给出加仓参考。"

    label = position_action_label(action)
    if action == POSITION_ACTION_HOLD:
        return PositionSizingRecommendation(
            action=action,
            label=label,
            current_market_value=round(current_market_value, 2),
            current_account_weight=round(current_weight, 4),
            target_account_weight=round(target_weight, 4),
            reason=reason,
        )

    target_market_value = max(0.0, target_weight * capital)
    raw_amount = target_market_value - current_market_value
    if action in {POSITION_ACTION_TRIM, POSITION_ACTION_REDUCE, POSITION_ACTION_EXIT}:
        raw_amount = current_market_value - target_market_value
    trade_amount = _round_trade_amount(max(0.0, raw_amount))
    if trade_amount <= 0:
        trade_amount = None
    trade_shares = _round_trade_shares(trade_amount / current_price) if trade_amount is not None else None
    return PositionSizingRecommendation(
        action=action,
        label=label,
        current_market_value=round(current_market_value, 2),
        current_account_weight=round(current_weight, 4),
        target_account_weight=round(target_weight, 4),
        recommended_trade_amount=trade_amount,
        recommended_trade_shares=trade_shares,
        reason=reason,
    )
