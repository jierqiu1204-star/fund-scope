from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.defaults.short_research import ASSET_TYPE_ETF
from app.services.etf_research_evidence import (
    OPERATIONAL_BUCKET_THRESHOLD_VERSION,
    OPERATIONAL_EXIT_ACTION_VERSION,
    OPERATIONAL_REENTRY_RULE_VERSION,
)

ALERT_EXIT_WATCH = "exit_watch"
ALERT_RISK_WARNING = "risk_warning"
ALERT_TAKE_PROFIT_WATCH = "take_profit_watch"
ALERT_TRAILING_TAKE_PROFIT = "trailing_take_profit"
ALERT_TREND_WEAKENING = "trend_weakening"
ALERT_CONFIRMED_TREND_WEAKENING = "confirmed_trend_weakening"
ALERT_HARD_STOP = "hard_stop"

EXIT_ACTION_VERSION = OPERATIONAL_EXIT_ACTION_VERSION
REENTRY_RULE_VERSION = OPERATIONAL_REENTRY_RULE_VERSION
BUCKET_THRESHOLD_VERSION = OPERATIONAL_BUCKET_THRESHOLD_VERSION
EXIT_EVIDENCE_VERSION = "etf_exit_evidence_v2"

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
TRAILING_FIRST_TARGET_REMAINING_FRACTION = 0.75
TRAILING_SECOND_TARGET_REMAINING_FRACTION = 0.50
HARD_STOP_LOSS_PCT = -4.0
TAKE_PROFIT_WATCH_COOLDOWN_DAYS = 3
DEFAULT_ETF_TRADING_CAPITAL = 10000.0
ETF_SINGLE_WEIGHT_CAP = 0.30

POSITION_ACTION_HOLD = "hold"
POSITION_ACTION_NO_ADD = "no_add"
POSITION_ACTION_TRIM = "trim"
POSITION_ACTION_REDUCE = "reduce"
POSITION_ACTION_EXIT = "exit"
POSITION_ACTION_ADD = "add"
POSITION_ACTION_REENTRY_CANDIDATE = "reentry_candidate"

REENTRY_STATE_NOT_APPLICABLE = "not_applicable"
REENTRY_STATE_WAITING_COOLDOWN = "waiting_cooldown"
REENTRY_STATE_BLOCKED = "blocked"
REENTRY_STATE_CANDIDATE = "candidate"

BUCKET_THRESHOLD_SOURCE_DEFAULT = "default"
BUCKET_THRESHOLD_SOURCE_RULE_DYNAMIC = "rule_dynamic"
BUCKET_THRESHOLD_SOURCE_APPROVED = "approved"
BUCKET_THRESHOLD_SOURCE_INSUFFICIENT = "insufficient_evidence"

EVIDENCE_STATUS_RESEARCH_ONLY = "research_only"
EVIDENCE_STATUS_APPROVED = "approved"
EVIDENCE_STATUS_INSUFFICIENT = "insufficient_sample"
EVIDENCE_STATUS_OLD_CONTRACT = "old_contract"

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
class EvaluatedRiskRule:
    rule_id: str
    data_state: str
    data_reason_code: str
    condition_met: bool
    recovery_met: bool
    target_remaining_fraction: float | None
    reason: str
    hard_stop: bool = False
    confirmation_required: int = 2
    recovery_required: int = 2

    def __post_init__(self) -> None:
        if not self.rule_id.strip() or not self.reason.strip():
            raise ValueError("rule_id and reason are required")
        if self.data_state not in {"eligible", "data_waiting", "no_data", "error"}:
            raise ValueError("unsupported evaluation data state")
        if not self.data_reason_code.strip():
            raise ValueError("data_reason_code is required")
        target = self.target_remaining_fraction
        if target is not None and (not math.isfinite(target) or not 0.0 <= target <= 1.0):
            raise ValueError("target_remaining_fraction must be a finite fraction")
        if self.confirmation_required < 1 or self.recovery_required < 1:
            raise ValueError("confirmation and recovery thresholds must be positive")


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
    action_class: str = ACTION_CLASS_NONE
    reentry_state: str = REENTRY_STATE_NOT_APPLICABLE
    reentry_reason: str | None = None
    action_version: str = EXIT_ACTION_VERSION
    reentry_rule_version: str = REENTRY_RULE_VERSION

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
            "action_class": self.action_class,
            "reentry_state": self.reentry_state,
            "reentry_reason": self.reentry_reason,
            "exit_action_version": self.action_version,
            "reentry_rule_version": self.reentry_rule_version,
        }


@dataclass(frozen=True)
class PositionActionDecision:
    action: str
    action_class: str
    label: str
    target_remaining_fraction: float | None
    reason: str

    @property
    def target_fraction(self) -> float | None:
        return self.target_remaining_fraction


@dataclass(frozen=True)
class ReentryDecision:
    state: str
    action: str
    label: str
    reason: str
    cooldown_end: date | None = None
    rule_version: str = REENTRY_RULE_VERSION

    def as_context(self) -> dict[str, Any]:
        return {
            "reentry_state": self.state,
            "position_action": self.action,
            "recommended_action_label": self.label,
            "reentry_reason": self.reason,
            "cooldown_end": self.cooldown_end.isoformat() if self.cooldown_end else None,
            "reentry_rule_version": self.rule_version,
        }


@dataclass(frozen=True)
class BucketThresholdContext:
    asset_bucket: str
    theme_group: str
    volatility_bucket: str
    profit_state: str
    data_reliability: str
    hard_stop_pct: float
    profit_start_pct: float
    trailing_giveback_pct: float
    take_profit_watch_pct: float
    trend_confirm_days: int
    threshold_source: str
    evidence_status: str
    sample_count: int
    intraday_coverage: float | None
    rule_version: str = BUCKET_THRESHOLD_VERSION

    def as_context(self) -> dict[str, Any]:
        return {
            "asset_bucket": self.asset_bucket,
            "theme_group": self.theme_group,
            "volatility_bucket": self.volatility_bucket,
            "profit_state": self.profit_state,
            "data_reliability": self.data_reliability,
            "hard_stop_pct": self.hard_stop_pct,
            "profit_start_pct": self.profit_start_pct,
            "trailing_giveback_pct": self.trailing_giveback_pct,
            "take_profit_watch_pct": self.take_profit_watch_pct,
            "trend_confirm_days": self.trend_confirm_days,
            "threshold_source": self.threshold_source,
            "evidence_status": self.evidence_status,
            "sample_count": self.sample_count,
            "intraday_coverage": self.intraday_coverage,
            "bucket_threshold_version": self.rule_version,
        }


def _round_trade_amount(value: float) -> float:
    return float(round(value / 10.0) * 10)


def _round_trade_shares(value: float | None) -> float | None:
    return float(round(value)) if value is not None else None


def position_action_label(action: str) -> str:
    return {
        POSITION_ACTION_HOLD: "继续观察",
        POSITION_ACTION_NO_ADD: "暂停加仓",
        POSITION_ACTION_TRIM: "轻度减仓",
        POSITION_ACTION_REDUCE: "明显减仓",
        POSITION_ACTION_EXIT: "清仓",
        POSITION_ACTION_ADD: "可加仓",
        POSITION_ACTION_REENTRY_CANDIDATE: "重新观察入场",
    }.get(action, "继续观察")


def entry_timing_allows_add(label: str | None) -> bool:
    return label not in {"冲高别追", "跌破等待", "放量转弱", "数据不足", "休市", "行情滞后"}


def map_exit_signal_to_position_action(
    *,
    alert_type: str | None,
    allow_full_exit: bool,
    trend_weakening: bool = False,
    ranking_deteriorated: bool = False,
    loss_confirmed: bool = False,
    giveback_confirmed: bool = False,
    market_regime_weak: bool = False,
    evidence_eligible: bool = True,
    exit_watch_target_remaining_fraction: float | None = None,
    current_remaining_fraction: float = 1.0,
) -> PositionActionDecision:
    if not math.isfinite(current_remaining_fraction) or not 0.0 <= current_remaining_fraction <= 1.0:
        raise ValueError("current_remaining_fraction must be a finite fraction")
    if alert_type == ALERT_HARD_STOP:
        return PositionActionDecision(
            action=POSITION_ACTION_EXIT,
            action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            label=position_action_label(POSITION_ACTION_EXIT),
            target_remaining_fraction=0.0,
            reason="触发硬止损，绝对目标为当前 exposure baseline 的 0%。",
        )
    if alert_type == ALERT_EXIT_WATCH:
        if not evidence_eligible:
            return PositionActionDecision(
                action=POSITION_ACTION_HOLD,
                action_class=ACTION_CLASS_NONE,
                label=position_action_label(POSITION_ACTION_HOLD),
                target_remaining_fraction=None,
                reason="退出观察缺少同源、有效的排名或研究证据，不生成动作。",
            )
        target = (
            exit_watch_target_remaining_fraction
            if exit_watch_target_remaining_fraction is not None
            else (0.0 if allow_full_exit else 0.5)
        )
        if target not in {0.0, 0.5}:
            raise ValueError("exit-watch target must be the versioned 0% or 50% absolute target")
        action = POSITION_ACTION_EXIT if target == 0.0 else POSITION_ACTION_REDUCE
        return PositionActionDecision(
            action=action,
            action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            label=position_action_label(action),
            target_remaining_fraction=target,
            reason="有效退出观察证据触发版本化绝对持仓目标。",
        )
    if alert_type == ALERT_TRAILING_TAKE_PROFIT:
        if current_remaining_fraction > TRAILING_FIRST_TARGET_REMAINING_FRACTION:
            return PositionActionDecision(
                action=POSITION_ACTION_TRIM,
                action_class=ACTION_CLASS_ACTIONABLE_EXIT,
                label=position_action_label(POSITION_ACTION_TRIM),
                target_remaining_fraction=TRAILING_FIRST_TARGET_REMAINING_FRACTION,
                reason="首次触发移动止盈，先将持仓降至 exposure baseline 的 75%，避免一次性退出。",
            )
        return PositionActionDecision(
            action=POSITION_ACTION_REDUCE,
            action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            label=position_action_label(POSITION_ACTION_REDUCE),
            target_remaining_fraction=TRAILING_SECOND_TARGET_REMAINING_FRACTION,
            reason=(
                "移动止盈再次触发，将持仓降至 exposure baseline 的 50%；"
                "趋势转弱本身不会把该目标升级为清仓。"
            ),
        )
    if alert_type == ALERT_CONFIRMED_TREND_WEAKENING:
        return PositionActionDecision(
            action=POSITION_ACTION_REDUCE,
            action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            label=position_action_label(POSITION_ACTION_REDUCE),
            target_remaining_fraction=0.5,
            reason="趋势转弱已被亏损、回吐或榜单转弱确认，降低一半暴露。",
        )
    if alert_type == ALERT_TREND_WEAKENING:
        confirmed = loss_confirmed or giveback_confirmed or ranking_deteriorated or market_regime_weak
        if confirmed:
            return PositionActionDecision(
                action=POSITION_ACTION_REDUCE,
                action_class=ACTION_CLASS_ACTIONABLE_EXIT,
                label=position_action_label(POSITION_ACTION_REDUCE),
                target_remaining_fraction=0.5,
                reason="趋势转弱叠加亏损、回吐、排名或市场确认，升级为减仓参考。",
            )
        return PositionActionDecision(
            action=POSITION_ACTION_NO_ADD,
            action_class=ACTION_CLASS_GUARD_ONLY,
            label=position_action_label(POSITION_ACTION_NO_ADD),
            target_remaining_fraction=1.0,
            reason="趋势转弱未被确认，先暂停加仓，不作为卖出邮件。",
        )
    if alert_type == ALERT_TAKE_PROFIT_WATCH:
        return PositionActionDecision(
            action=POSITION_ACTION_HOLD,
            action_class=ACTION_CLASS_SOFT_WATCH,
            label=position_action_label(POSITION_ACTION_HOLD),
            target_remaining_fraction=1.0,
            reason="触发止盈观察，仅观察并保持当前仓位，不生成减仓动作。",
        )
    return PositionActionDecision(
        action=POSITION_ACTION_HOLD,
        action_class=ACTION_CLASS_NONE,
        label=position_action_label(POSITION_ACTION_HOLD),
        target_remaining_fraction=1.0,
        reason="暂无持仓处理信号。",
    )


def evaluate_reentry_state(
    *,
    last_action: str | None,
    last_action_date: date | None,
    today: date,
    ranking_bucket: str | None,
    entry_timing_label: str | None,
    theme_trend: str | None,
    data_reliability: str | None,
    cooldown_days: int = 3,
) -> ReentryDecision:
    if last_action not in {POSITION_ACTION_TRIM, POSITION_ACTION_REDUCE, POSITION_ACTION_EXIT}:
        return ReentryDecision(
            state=REENTRY_STATE_NOT_APPLICABLE,
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="没有减仓或清仓动作，不需要再入场判断。",
        )
    cooldown_end = last_action_date + timedelta(days=cooldown_days) if last_action_date else today
    if today < cooldown_end:
        return ReentryDecision(
            state=REENTRY_STATE_WAITING_COOLDOWN,
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason=f"仍在 {cooldown_days} 天冷却期内，先不重新加仓。",
            cooldown_end=cooldown_end,
        )
    if data_reliability not in {"verified", "alternate_provider", "single_fresh"}:
        return ReentryDecision(
            state=REENTRY_STATE_BLOCKED,
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="数据可信度不足，不能给重新入场候选。",
            cooldown_end=cooldown_end,
        )
    if ranking_bucket not in {"top5", "top10", "top20"}:
        return ReentryDecision(
            state=REENTRY_STATE_BLOCKED,
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="综合排名尚未回到可接受区间。",
            cooldown_end=cooldown_end,
        )
    if not entry_timing_allows_add(entry_timing_label):
        return ReentryDecision(
            state=REENTRY_STATE_BLOCKED,
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="今日买点仍不适合重新加仓。",
            cooldown_end=cooldown_end,
        )
    if theme_trend in {"weak", "down", "risk_off"}:
        return ReentryDecision(
            state=REENTRY_STATE_BLOCKED,
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="所属板块趋势仍弱，暂不重新加仓。",
            cooldown_end=cooldown_end,
        )
    return ReentryDecision(
        state=REENTRY_STATE_CANDIDATE,
        action=POSITION_ACTION_REENTRY_CANDIDATE,
        label=position_action_label(POSITION_ACTION_REENTRY_CANDIDATE),
        reason="冷却期结束，排名、买点、板块趋势和数据可信度满足重新观察入场条件。",
        cooldown_end=cooldown_end,
    )


def build_bucket_threshold_context(
    *,
    asset_bucket: str | None,
    theme_group: str | None,
    volatility_pct: float | None,
    current_profit_pct: float | None,
    data_reliability: str | None,
    sample_count: int = 0,
    intraday_coverage: float | None = None,
    approved_params: dict[str, Any] | None = None,
) -> BucketThresholdContext:
    volatility = abs(float(volatility_pct or 2.5))
    asset_bucket_value = asset_bucket or "unknown"
    theme_group_value = theme_group or "unknown"
    volatility_bucket = "high" if volatility >= 3.5 else "low" if volatility <= 1.2 else "medium"
    profit = float(current_profit_pct or 0.0)
    profit_state = "loss" if profit < 0 else "large_profit" if profit >= 5 else "small_profit"
    coverage = intraday_coverage if intraday_coverage is not None else 0.0
    enough_evidence = sample_count >= 30 and coverage >= 0.6
    if approved_params and enough_evidence:
        return BucketThresholdContext(
            asset_bucket=asset_bucket_value,
            theme_group=theme_group_value,
            volatility_bucket=volatility_bucket,
            profit_state=profit_state,
            data_reliability=data_reliability or "unavailable",
            hard_stop_pct=float(approved_params.get("hard_stop_pct", -4.0)),
            profit_start_pct=float(approved_params.get("profit_start_pct", 4.0)),
            trailing_giveback_pct=float(approved_params.get("trailing_giveback_pct", 2.2)),
            take_profit_watch_pct=float(approved_params.get("take_profit_watch_pct", 3.0)),
            trend_confirm_days=int(approved_params.get("trend_confirm_days", 2)),
            threshold_source=BUCKET_THRESHOLD_SOURCE_APPROVED,
            evidence_status=EVIDENCE_STATUS_APPROVED,
            sample_count=sample_count,
            intraday_coverage=intraday_coverage,
        )
    if not enough_evidence:
        source = BUCKET_THRESHOLD_SOURCE_INSUFFICIENT
        status = EVIDENCE_STATUS_INSUFFICIENT
    else:
        source = BUCKET_THRESHOLD_SOURCE_RULE_DYNAMIC
        status = EVIDENCE_STATUS_RESEARCH_ONLY
    hard_stop = -max(1.5, min(7.0, 1.5 * volatility))
    if asset_bucket_value in {"bond", "money", "defensive"}:
        hard_stop = -max(0.8, min(3.0, 1.2 * volatility))
    elif volatility_bucket == "high":
        hard_stop = -max(3.0, min(7.0, 1.7 * volatility))
    profit_start = max(2.0, min(5.0, 1.1 * volatility))
    trailing_giveback = max(1.2, min(3.2, 0.65 * volatility))
    return BucketThresholdContext(
        asset_bucket=asset_bucket_value,
        theme_group=theme_group_value,
        volatility_bucket=volatility_bucket,
        profit_state=profit_state,
        data_reliability=data_reliability or "unavailable",
        hard_stop_pct=round(hard_stop, 4),
        profit_start_pct=round(profit_start, 4),
        trailing_giveback_pct=round(trailing_giveback, 4),
        take_profit_watch_pct=round(max(3.0, profit_start), 4),
        trend_confirm_days=2 if volatility_bucket != "high" else 3,
        threshold_source=source,
        evidence_status=status,
        sample_count=sample_count,
        intraday_coverage=intraday_coverage,
    )


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
    exposure_baseline_quantity: float | None = None,
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
    if exposure_baseline_quantity is not None and (
        not math.isfinite(exposure_baseline_quantity) or exposure_baseline_quantity <= 0
    ):
        return PositionSizingRecommendation(
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            current_market_value=round(current_market_value, 2),
            current_account_weight=round(current_weight, 4),
            target_account_weight=round(current_weight, 4),
            reason="等待有效的不可变 exposure baseline 后再计算仓位金额。",
        )
    baseline_quantity = exposure_baseline_quantity or (current_market_value / current_price)
    baseline_weight = baseline_quantity * current_price / capital
    current_remaining_fraction = min(
        1.0,
        max(0.0, current_market_value / (baseline_quantity * current_price)),
    )
    action_decision = map_exit_signal_to_position_action(
        alert_type=alert_type,
        allow_full_exit=allow_full_exit,
        trend_weakening=trend_weakening,
        current_remaining_fraction=current_remaining_fraction,
    )
    action = action_decision.action
    reason = action_decision.reason
    if action_decision.target_remaining_fraction is not None:
        absolute_target_weight = baseline_weight * action_decision.target_remaining_fraction
        target_weight = (
            min(current_weight, absolute_target_weight)
            if action in {POSITION_ACTION_TRIM, POSITION_ACTION_REDUCE, POSITION_ACTION_EXIT}
            else current_weight
        )

    if alert_type is None and target_portfolio_weight is not None and entry_timing_allows_add(entry_timing_label):
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
            action_class=action_decision.action_class,
        )
    if action == POSITION_ACTION_NO_ADD:
        return PositionSizingRecommendation(
            action=action,
            label=label,
            current_market_value=round(current_market_value, 2),
            current_account_weight=round(current_weight, 4),
            target_account_weight=round(current_weight, 4),
            reason=reason,
            action_class=ACTION_CLASS_GUARD_ONLY,
        )

    target_market_value = max(0.0, target_weight * capital)
    raw_amount = target_market_value - current_market_value
    if action in {POSITION_ACTION_TRIM, POSITION_ACTION_REDUCE, POSITION_ACTION_EXIT}:
        raw_amount = current_market_value - target_market_value
    trade_amount: float | None = _round_trade_amount(max(0.0, raw_amount))
    if trade_amount is not None and trade_amount <= 0:
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
        action_class=action_decision.action_class if action != POSITION_ACTION_ADD else ACTION_CLASS_NONE,
    )
