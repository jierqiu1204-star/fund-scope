from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from statistics import median
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

from app.defaults.short_research import ASSET_TYPE_ETF
from app.services.etf_research_evidence import (
    OPERATIONAL_BUCKET_THRESHOLD_VERSION,
    OPERATIONAL_EXIT_ACTION_VERSION,
    OPERATIONAL_REENTRY_RULE_VERSION,
    stable_contract_hash,
)

ALERT_EXIT_WATCH = "exit_watch"
ALERT_RISK_WARNING = "risk_warning"
ALERT_TAKE_PROFIT_WATCH = "take_profit_watch"
ALERT_TRAILING_TAKE_PROFIT = "trailing_take_profit"
ALERT_TREND_WEAKENING = "trend_weakening"
ALERT_CONFIRMED_TREND_WEAKENING = "confirmed_trend_weakening"
ALERT_HARD_STOP = "hard_stop"
ALERT_MA5_CLOSE_BREAK_EXIT = "ma5_close_break_exit"
ALERT_LATE_DAY_T1_EXIT = "late_day_t1_exit"
ALERT_LEADER_TACTICS_EXIT = "leader_tactics_exit"

LEADER_TACTICS_EXIT_POLICY_ID = "leader_tactics_exit_v1"
LEADER_TACTICS_EXIT_POLICY_VERSION = "leader_tactics_exit_v1"
LEADER_TACTICS_EXIT_STATE_KEY = "leader_tactics_exit_v1"
LEADER_TACTICS_HARD_STOP = "leader_tactics_hard_stop"
LEADER_TACTICS_BREAKEVEN_EXIT = "leader_tactics_breakeven_exit"
LEADER_TACTICS_MA5_EXIT = "leader_tactics_ma5_exit"
LEADER_TACTICS_DATA_WAITING = "leader_tactics_data_waiting"
LEADER_TACTICS_PRICE_BASIS = "total_return_adjusted"
LEADER_TACTICS_ROUND_TRIP_COST_BPS = 20.0
LEADER_TACTICS_APPROVED_PROVIDERS = frozenset({"akshare", "eastmoney", "tickflow"})
_LEADER_TACTICS_SHANGHAI = ZoneInfo("Asia/Shanghai")

EXIT_ACTION_VERSION = OPERATIONAL_EXIT_ACTION_VERSION
REENTRY_RULE_VERSION = OPERATIONAL_REENTRY_RULE_VERSION
BUCKET_THRESHOLD_VERSION = OPERATIONAL_BUCKET_THRESHOLD_VERSION
EXIT_EVIDENCE_VERSION = "etf_exit_evidence_v2"
EXIT_EXECUTION_EVIDENCE_VERSION = "etf_exit_execution_evidence_v1"
EXIT_SLIPPAGE_RESERVE_BPS = 5.0

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
    ALERT_MA5_CLOSE_BREAK_EXIT,
    ALERT_LATE_DAY_T1_EXIT,
    ALERT_LEADER_TACTICS_EXIT,
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
ETF_DYNAMIC_THRESHOLD_VERSION = "dynamic_etf_threshold_v2"
ETF_PROFIT_PROTECTION_VERSION = "etf_profit_protection_v2"
ETF_PROFIT_PROTECTION_STATE_KEY = "etf_profit_protection_v2"
TRAILING_FIRST_TARGET_REMAINING_FRACTION = 0.75
TRAILING_SECOND_TARGET_REMAINING_FRACTION = 0.50
HARD_STOP_LOSS_PCT = -4.0
TAKE_PROFIT_WATCH_COOLDOWN_DAYS = 3
DEFAULT_ETF_TRADING_CAPITAL = 10000.0
ETF_SINGLE_WEIGHT_CAP = 0.30

ETF_SLEEVE_RISK_VERSION = "tracked_etf_sleeve_risk_v1"
ETF_LIQUIDITY_CAPACITY_VERSION = "etf_liquidity_capacity_v1"
ETF_RISK_STATE_NORMAL = "normal"
ETF_RISK_STATE_REDUCE_ONLY = "reduce_only"
ETF_RISK_STATE_DATA_HALT = "data_halt"
ETF_RISK_DRAWDOWN_TRIGGER = -0.05
ETF_RISK_DRAWDOWN_RELEASE = -0.03
ETF_RISK_STOP_CYCLE_TRIGGER = 2
ETF_RISK_RECOVERY_SESSION_COUNT = 2
ETF_LIQUIDITY_MIN_TURNOVER_SESSIONS = 10
ETF_ENTRY_MAX_ADV_PARTICIPATION = 0.01
ETF_ENTRY_STRESS_MAX_ADV_PARTICIPATION = 0.005
ETF_EXIT_NORMAL_ADV_PARTICIPATION = 0.05
ETF_EXIT_STRESS_ADV_PARTICIPATION = 0.02
ETF_LIQUIDITY_STRESS_TURNOVER_MULTIPLIER = 0.50
ETF_LIQUIDITY_MAX_SPREAD_PCT = 0.30
ETF_LIQUIDITY_MAX_ABS_PREMIUM_DISCOUNT_PCT = 0.80

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
    ALERT_MA5_CLOSE_BREAK_EXIT,
    ALERT_LATE_DAY_T1_EXIT,
    ALERT_LEADER_TACTICS_EXIT,
}


def _finite_number(
    value: object, *, positive: bool = False, nonnegative: bool = False
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    if positive and parsed <= 0:
        return None
    if nonnegative and parsed < 0:
        return None
    return parsed


@dataclass(frozen=True)
class EtfProfitThresholdPolicy:
    risk_min_pct: float
    risk_max_pct: float
    start_multiplier: float
    start_min_pct: float
    start_max_pct: float
    giveback_multiplier: float
    giveback_min_pct: float
    giveback_max_pct: float


_ETF_PROFIT_THRESHOLD_POLICIES: dict[str, EtfProfitThresholdPolicy] = {
    "money": EtfProfitThresholdPolicy(0.10, 0.80, 2.0, 0.60, 1.80, 1.20, 0.35, 1.00),
    "bond": EtfProfitThresholdPolicy(0.25, 1.20, 2.0, 0.90, 2.80, 1.20, 0.50, 1.60),
    "broad_base": EtfProfitThresholdPolicy(0.60, 2.50, 1.8, 1.50, 5.00, 1.10, 0.80, 3.00),
    "dividend": EtfProfitThresholdPolicy(0.55, 2.20, 1.8, 1.40, 4.50, 1.05, 0.75, 2.60),
    "equity": EtfProfitThresholdPolicy(0.80, 3.50, 1.8, 2.00, 6.50, 1.10, 1.00, 4.00),
    "cross_border": EtfProfitThresholdPolicy(1.00, 4.50, 1.8, 2.50, 8.00, 1.20, 1.40, 5.50),
    "commodity": EtfProfitThresholdPolicy(0.90, 4.00, 1.8, 2.30, 7.00, 1.15, 1.30, 4.80),
    "unknown": EtfProfitThresholdPolicy(0.80, 3.00, 1.8, 2.00, 6.00, 1.10, 1.00, 3.80),
}


@dataclass(frozen=True)
class EtfProfitThresholds:
    asset_bucket: str
    current_risk_unit_pct: float
    entry_risk_unit_pct: float
    profit_start_pct: float
    trailing_giveback_pct: float
    risk_components_pct: tuple[float, ...]
    rule_version: str = ETF_DYNAMIC_THRESHOLD_VERSION

    def as_context(self) -> dict[str, Any]:
        return {
            "asset_bucket": self.asset_bucket,
            "current_risk_unit_pct": self.current_risk_unit_pct,
            "entry_risk_unit_pct": self.entry_risk_unit_pct,
            "profit_start_pct": self.profit_start_pct,
            "trailing_giveback_pct": self.trailing_giveback_pct,
            "risk_components_pct": list(self.risk_components_pct),
            "rule_version": self.rule_version,
        }


def derive_etf_profit_thresholds(
    *,
    asset_bucket: str,
    atr_pct: float | None,
    realized_volatility_pct: float | None,
    median_abs_return_pct: float | None,
    persisted_entry_risk_unit_pct: float | None = None,
) -> EtfProfitThresholds | None:
    """Return robust ETF profit thresholds without consulting ranking state.

    All inputs are percentages derived from decision-eligible adjusted bars. At
    least two independent components are required so a single noisy proxy
    cannot silently become an actionable exit threshold.
    """

    components = tuple(
        value
        for raw in (atr_pct, realized_volatility_pct, median_abs_return_pct)
        if (value := _finite_number(raw, positive=True)) is not None
    )
    if len(components) < 2:
        return None
    bucket = asset_bucket if asset_bucket in _ETF_PROFIT_THRESHOLD_POLICIES else "unknown"
    policy = _ETF_PROFIT_THRESHOLD_POLICIES[bucket]
    current_risk = min(policy.risk_max_pct, max(policy.risk_min_pct, median(components)))
    persisted_risk = _finite_number(persisted_entry_risk_unit_pct, positive=True)
    entry_risk = (
        min(policy.risk_max_pct, max(policy.risk_min_pct, persisted_risk))
        if persisted_risk is not None
        else current_risk
    )
    profit_start = min(
        policy.start_max_pct,
        max(policy.start_min_pct, policy.start_multiplier * entry_risk),
    )
    trailing_giveback = min(
        policy.giveback_max_pct,
        max(policy.giveback_min_pct, policy.giveback_multiplier * entry_risk),
    )
    return EtfProfitThresholds(
        asset_bucket=bucket,
        current_risk_unit_pct=round(current_risk, 6),
        entry_risk_unit_pct=round(entry_risk, 6),
        profit_start_pct=round(profit_start, 6),
        trailing_giveback_pct=round(trailing_giveback, 6),
        risk_components_pct=tuple(round(value, 6) for value in components),
    )


@dataclass(frozen=True)
class LongProfitProtectionDecision:
    state: str
    data_eligible: bool
    high_water_profit_pct: float
    current_profit_pct: float
    profit_giveback_pct: float
    profit_start_pct: float | None
    trailing_giveback_pct: float | None
    previous_trailing_stop_pnl_pct: float | None
    candidate_trailing_stop_pnl_pct: float | None
    trailing_stop_pnl_pct: float | None
    distance_to_trailing_stop_pct: float | None
    triggered: bool
    rule_version: str = ETF_PROFIT_PROTECTION_VERSION

    def as_context(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "data_eligible": self.data_eligible,
            "high_water_profit_pct": self.high_water_profit_pct,
            "current_profit_pct": self.current_profit_pct,
            "profit_giveback_pct": self.profit_giveback_pct,
            "profit_start_pct": self.profit_start_pct,
            "trailing_giveback_pct": self.trailing_giveback_pct,
            "previous_trailing_stop_pnl_pct": self.previous_trailing_stop_pnl_pct,
            "candidate_trailing_stop_pnl_pct": self.candidate_trailing_stop_pnl_pct,
            "trailing_stop_pnl_pct": self.trailing_stop_pnl_pct,
            "distance_to_trailing_stop_pct": self.distance_to_trailing_stop_pct,
            "triggered": self.triggered,
            "rule_version": self.rule_version,
        }


def evaluate_long_profit_protection(
    *,
    current_profit_pct: float,
    observed_high_water_profit_pct: float,
    profit_start_pct: float | None,
    trailing_giveback_pct: float | None,
    data_eligible: bool,
    persisted_high_water_profit_pct: float | None = None,
    persisted_trailing_stop_pnl_pct: float | None = None,
    persisted_armed: bool = False,
) -> LongProfitProtectionDecision:
    """Advance one long-position profit-protection observation.

    Once armed, both the high-water profit and the effective protection line
    are monotonic for the lifetime of the position episode.
    """

    current = _finite_number(current_profit_pct)
    observed_high = _finite_number(observed_high_water_profit_pct)
    if current is None or observed_high is None:
        raise ValueError("current and observed high-water profit must be finite")
    persisted_high = _finite_number(persisted_high_water_profit_pct)
    high_water = max(
        value for value in (current, observed_high, persisted_high) if value is not None
    )
    giveback = max(0.0, high_water - current)
    start = _finite_number(profit_start_pct, positive=True)
    allowed_giveback = _finite_number(trailing_giveback_pct, positive=True)
    previous_stop = _finite_number(persisted_trailing_stop_pnl_pct)
    if not data_eligible or start is None or allowed_giveback is None:
        frozen_distance = current - previous_stop if previous_stop is not None else None
        return LongProfitProtectionDecision(
            state="data_waiting",
            data_eligible=False,
            high_water_profit_pct=round(high_water, 6),
            current_profit_pct=round(current, 6),
            profit_giveback_pct=round(giveback, 6),
            profit_start_pct=start,
            trailing_giveback_pct=allowed_giveback,
            previous_trailing_stop_pnl_pct=previous_stop,
            candidate_trailing_stop_pnl_pct=None,
            trailing_stop_pnl_pct=previous_stop,
            distance_to_trailing_stop_pct=round(frozen_distance, 6)
            if frozen_distance is not None
            else None,
            triggered=False,
        )

    armed = bool(persisted_armed or high_water >= start)
    if not armed:
        return LongProfitProtectionDecision(
            state="unarmed",
            data_eligible=True,
            high_water_profit_pct=round(high_water, 6),
            current_profit_pct=round(current, 6),
            profit_giveback_pct=round(giveback, 6),
            profit_start_pct=round(start, 6),
            trailing_giveback_pct=round(allowed_giveback, 6),
            previous_trailing_stop_pnl_pct=previous_stop,
            candidate_trailing_stop_pnl_pct=None,
            trailing_stop_pnl_pct=None,
            distance_to_trailing_stop_pct=None,
            triggered=False,
        )

    candidate_stop = max(0.0, high_water - allowed_giveback)
    effective_stop = max(
        value for value in (candidate_stop, previous_stop) if value is not None
    )
    distance = current - effective_stop
    return LongProfitProtectionDecision(
        state="triggered" if distance <= 0 else "armed",
        data_eligible=True,
        high_water_profit_pct=round(high_water, 6),
        current_profit_pct=round(current, 6),
        profit_giveback_pct=round(giveback, 6),
        profit_start_pct=round(start, 6),
        trailing_giveback_pct=round(allowed_giveback, 6),
        previous_trailing_stop_pnl_pct=previous_stop,
        candidate_trailing_stop_pnl_pct=round(candidate_stop, 6),
        trailing_stop_pnl_pct=round(effective_stop, 6),
        distance_to_trailing_stop_pct=round(distance, 6),
        triggered=distance <= 0,
    )


@dataclass(frozen=True)
class EtfSleevePositionEvidence:
    position_id: int
    remaining_cost_basis: float | None
    market_value: float | None
    quantity: float | None
    decision_eligible: bool
    execution_complete: bool


@dataclass(frozen=True)
class EtfSleeveNavEvidence:
    status: str
    configured_capital: float | None
    cash_balance: float | None
    market_value: float | None
    realized_pnl: float | None
    unrealized_pnl: float | None
    equity: float | None
    valuation_coverage: float
    execution_coverage: float
    unavailable_reasons: tuple[str, ...]
    contract_version: str
    contract_hash: str
    source_hash: str

    def as_context(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "configured_capital": self.configured_capital,
            "cash_balance": self.cash_balance,
            "market_value": self.market_value,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "equity": self.equity,
            "valuation_coverage": self.valuation_coverage,
            "execution_coverage": self.execution_coverage,
            "unavailable_reasons": list(self.unavailable_reasons),
            "contract_version": self.contract_version,
            "contract_hash": self.contract_hash,
            "source_hash": self.source_hash,
        }


def etf_sleeve_risk_manifest() -> dict[str, Any]:
    payload = {
        "version": ETF_SLEEVE_RISK_VERSION,
        "states": [
            ETF_RISK_STATE_NORMAL,
            ETF_RISK_STATE_REDUCE_ONLY,
            ETF_RISK_STATE_DATA_HALT,
        ],
        "drawdown_trigger": ETF_RISK_DRAWDOWN_TRIGGER,
        "drawdown_release": ETF_RISK_DRAWDOWN_RELEASE,
        "stop_cycle_trigger": ETF_RISK_STOP_CYCLE_TRIGGER,
        "stop_cycle_trigger_mode": "threshold_cross_or_new_distinct_cycle",
        "recovery_session_count": ETF_RISK_RECOVERY_SESSION_COUNT,
    }
    return {**payload, "contract_hash": stable_contract_hash(payload)}


def calculate_tracked_etf_sleeve_nav(
    *,
    configured_capital: float | None,
    capital_confirmed: bool,
    opening_reconciled: bool,
    cash_balance: float | None,
    realized_pnl: float | None,
    positions: Sequence[EtfSleevePositionEvidence],
) -> EtfSleeveNavEvidence:
    """Calculate an evidence-scoped ETF sleeve NAV without brokerage inference."""

    manifest = etf_sleeve_risk_manifest()
    capital = _finite_number(configured_capital, positive=True)
    cash = _finite_number(cash_balance, nonnegative=True)
    realized = _finite_number(realized_pnl)
    reasons: list[str] = []
    if not capital_confirmed:
        reasons.append("capital_not_explicitly_confirmed")
    if not opening_reconciled:
        reasons.append("holdings_reconciliation_missing")
    if capital is None:
        reasons.append("configured_capital_invalid")
    if cash is None:
        reasons.append("cash_balance_unavailable")
    if realized is None:
        reasons.append("realized_pnl_unavailable")

    valuation_ready = 0
    execution_ready = 0
    market_values: list[float] = []
    cost_bases: list[float] = []
    source_positions: list[dict[str, Any]] = []
    for position in positions:
        quantity = _finite_number(position.quantity, positive=True)
        market_value = _finite_number(position.market_value, nonnegative=True)
        cost_basis = _finite_number(position.remaining_cost_basis, nonnegative=True)
        mark_ready = bool(
            position.decision_eligible and quantity is not None and market_value is not None
        )
        if mark_ready:
            valuation_ready += 1
            market_values.append(float(market_value))
        else:
            reasons.append(f"position_mark_ineligible:{position.position_id}")
        if position.execution_complete and quantity is not None and cost_basis is not None:
            execution_ready += 1
            cost_bases.append(float(cost_basis))
        else:
            reasons.append(f"position_execution_incomplete:{position.position_id}")
        source_positions.append(
            {
                "position_id": position.position_id,
                "quantity": quantity,
                "remaining_cost_basis": cost_basis,
                "market_value": market_value,
                "decision_eligible": bool(position.decision_eligible),
                "execution_complete": bool(position.execution_complete),
            }
        )

    total_positions = len(positions)
    valuation_coverage = valuation_ready / total_positions if total_positions else 1.0
    execution_coverage = execution_ready / total_positions if total_positions else 1.0
    source_payload = {
        "contract_hash": manifest["contract_hash"],
        "configured_capital": capital,
        "capital_confirmed": bool(capital_confirmed),
        "opening_reconciled": bool(opening_reconciled),
        "cash_balance": cash,
        "realized_pnl": realized,
        "positions": source_positions,
    }
    source_hash = stable_contract_hash(source_payload)
    if reasons:
        return EtfSleeveNavEvidence(
            status="unavailable",
            configured_capital=capital,
            cash_balance=None,
            market_value=None,
            realized_pnl=realized,
            unrealized_pnl=None,
            equity=None,
            valuation_coverage=round(valuation_coverage, 8),
            execution_coverage=round(execution_coverage, 8),
            unavailable_reasons=tuple(dict.fromkeys(reasons)),
            contract_version=ETF_SLEEVE_RISK_VERSION,
            contract_hash=str(manifest["contract_hash"]),
            source_hash=source_hash,
        )

    total_market_value = sum(market_values)
    unrealized_pnl = total_market_value - sum(cost_bases)
    equity = float(cash) + total_market_value
    if not all(math.isfinite(value) for value in (total_market_value, unrealized_pnl, equity)):
        return EtfSleeveNavEvidence(
            status="unavailable",
            configured_capital=capital,
            cash_balance=None,
            market_value=None,
            realized_pnl=realized,
            unrealized_pnl=None,
            equity=None,
            valuation_coverage=round(valuation_coverage, 8),
            execution_coverage=round(execution_coverage, 8),
            unavailable_reasons=("sleeve_nav_non_finite",),
            contract_version=ETF_SLEEVE_RISK_VERSION,
            contract_hash=str(manifest["contract_hash"]),
            source_hash=source_hash,
        )
    return EtfSleeveNavEvidence(
        status="ready",
        configured_capital=round(float(capital), 2),
        cash_balance=round(float(cash), 2),
        market_value=round(total_market_value, 2),
        realized_pnl=round(float(realized), 2),
        unrealized_pnl=round(unrealized_pnl, 2),
        equity=round(equity, 2),
        valuation_coverage=1.0,
        execution_coverage=1.0,
        unavailable_reasons=(),
        contract_version=ETF_SLEEVE_RISK_VERSION,
        contract_hash=str(manifest["contract_hash"]),
        source_hash=source_hash,
    )


def calculate_sleeve_drawdown(
    current_equity: float | None,
    prior_eligible_equities: Sequence[float],
) -> float | None:
    current = _finite_number(current_equity, positive=True)
    prior = [
        value
        for raw in prior_eligible_equities
        if (value := _finite_number(raw, positive=True)) is not None
    ]
    if current is None or not prior:
        return None
    high_water = max(prior)
    return round(current / max(high_water, current) - 1.0, 8)


@dataclass(frozen=True)
class EtfOwnerRiskStateDecision:
    state: str
    reason_codes: tuple[str, ...]
    evaluated_session: date
    recovery_sessions: tuple[date, ...]
    cooldown_sessions_remaining: int
    changed: bool
    contract_version: str
    contract_hash: str

    def as_context(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason_codes": list(self.reason_codes),
            "evaluated_session": self.evaluated_session.isoformat(),
            "recovery_sessions": [item.isoformat() for item in self.recovery_sessions],
            "cooldown_sessions_remaining": self.cooldown_sessions_remaining,
            "changed": self.changed,
            "contract_version": self.contract_version,
            "contract_hash": self.contract_hash,
        }


def evaluate_etf_owner_risk_state(
    *,
    trade_session: date,
    nav_status: str,
    sleeve_drawdown: float | None,
    distinct_stop_signal_cycles: int,
    confirmed_stop_execution_cycles: int,
    previous_state: str = ETF_RISK_STATE_NORMAL,
    previous_evaluated_session: date | None = None,
    previous_recovery_sessions: Sequence[date] = (),
    cooldown_sessions_remaining: int = 0,
    previous_signal_stop_cycles: int = 0,
    previous_confirmed_stop_cycles: int = 0,
) -> EtfOwnerRiskStateDecision:
    manifest = etf_sleeve_risk_manifest()
    allowed_states = {
        ETF_RISK_STATE_NORMAL,
        ETF_RISK_STATE_REDUCE_ONLY,
        ETF_RISK_STATE_DATA_HALT,
    }
    prior_state = previous_state if previous_state in allowed_states else ETF_RISK_STATE_DATA_HALT
    is_new_session = previous_evaluated_session != trade_session
    recovery = tuple(dict.fromkeys(previous_recovery_sessions))
    cooldown = max(0, int(cooldown_sessions_remaining))
    if is_new_session and cooldown > 0:
        cooldown -= 1

    drawdown = _finite_number(sleeve_drawdown)
    if nav_status != "ready" or drawdown is None:
        reasons = [
            "sleeve_nav_unavailable" if nav_status != "ready" else "drawdown_history_insufficient"
        ]
        return EtfOwnerRiskStateDecision(
            state=ETF_RISK_STATE_DATA_HALT,
            reason_codes=tuple(reasons),
            evaluated_session=trade_session,
            recovery_sessions=(),
            cooldown_sessions_remaining=cooldown,
            changed=prior_state != ETF_RISK_STATE_DATA_HALT,
            contract_version=ETF_SLEEVE_RISK_VERSION,
            contract_hash=str(manifest["contract_hash"]),
        )

    trigger_reasons: list[str] = []
    if drawdown <= ETF_RISK_DRAWDOWN_TRIGGER:
        trigger_reasons.append("sleeve_drawdown_limit_breached")
    signal_cycles = max(0, int(distinct_stop_signal_cycles))
    confirmed_cycles = max(0, int(confirmed_stop_execution_cycles))
    prior_signal_cycles = max(0, int(previous_signal_stop_cycles))
    prior_confirmed_cycles = max(0, int(previous_confirmed_stop_cycles))
    if signal_cycles >= ETF_RISK_STOP_CYCLE_TRIGGER and (
        prior_signal_cycles < ETF_RISK_STOP_CYCLE_TRIGGER or signal_cycles > prior_signal_cycles
    ):
        trigger_reasons.append("repeated_distinct_stop_signals")
    if confirmed_cycles >= ETF_RISK_STOP_CYCLE_TRIGGER and (
        prior_confirmed_cycles < ETF_RISK_STOP_CYCLE_TRIGGER
        or confirmed_cycles > prior_confirmed_cycles
    ):
        trigger_reasons.append("repeated_confirmed_stop_executions")
    if trigger_reasons:
        return EtfOwnerRiskStateDecision(
            state=ETF_RISK_STATE_REDUCE_ONLY,
            reason_codes=tuple(trigger_reasons),
            evaluated_session=trade_session,
            recovery_sessions=(),
            cooldown_sessions_remaining=max(cooldown, ETF_RISK_RECOVERY_SESSION_COUNT),
            changed=prior_state != ETF_RISK_STATE_REDUCE_ONLY,
            contract_version=ETF_SLEEVE_RISK_VERSION,
            contract_hash=str(manifest["contract_hash"]),
        )

    recovery_eligible = drawdown > ETF_RISK_DRAWDOWN_RELEASE
    if prior_state in {ETF_RISK_STATE_REDUCE_ONLY, ETF_RISK_STATE_DATA_HALT}:
        if not recovery_eligible:
            recovery = ()
        elif is_new_session and trade_session not in recovery:
            recovery = (*recovery, trade_session)
        if cooldown > 0 or len(recovery) < ETF_RISK_RECOVERY_SESSION_COUNT:
            return EtfOwnerRiskStateDecision(
                state=prior_state,
                reason_codes=("risk_recovery_pending",),
                evaluated_session=trade_session,
                recovery_sessions=recovery,
                cooldown_sessions_remaining=cooldown,
                changed=False,
                contract_version=ETF_SLEEVE_RISK_VERSION,
                contract_hash=str(manifest["contract_hash"]),
            )

    return EtfOwnerRiskStateDecision(
        state=ETF_RISK_STATE_NORMAL,
        reason_codes=(),
        evaluated_session=trade_session,
        recovery_sessions=(),
        cooldown_sessions_remaining=0,
        changed=prior_state != ETF_RISK_STATE_NORMAL,
        contract_version=ETF_SLEEVE_RISK_VERSION,
        contract_hash=str(manifest["contract_hash"]),
    )


def etf_liquidity_capacity_manifest() -> dict[str, Any]:
    payload = {
        "version": ETF_LIQUIDITY_CAPACITY_VERSION,
        "minimum_turnover_sessions": ETF_LIQUIDITY_MIN_TURNOVER_SESSIONS,
        "entry_max_adv_participation": ETF_ENTRY_MAX_ADV_PARTICIPATION,
        "entry_stress_max_adv_participation": ETF_ENTRY_STRESS_MAX_ADV_PARTICIPATION,
        "exit_normal_adv_participation": ETF_EXIT_NORMAL_ADV_PARTICIPATION,
        "exit_stress_adv_participation": ETF_EXIT_STRESS_ADV_PARTICIPATION,
        "stress_turnover_multiplier": ETF_LIQUIDITY_STRESS_TURNOVER_MULTIPLIER,
        "max_spread_pct": ETF_LIQUIDITY_MAX_SPREAD_PCT,
        "max_abs_premium_discount_pct": ETF_LIQUIDITY_MAX_ABS_PREMIUM_DISCOUNT_PCT,
    }
    return {**payload, "contract_hash": stable_contract_hash(payload)}


@dataclass(frozen=True)
class EtfLiquidityCapacityAssessment:
    side: str
    status: str
    entry_allowed: bool
    trade_amount: float | None
    median_turnover_20d: float | None
    turnover_sample_count: int
    adv_participation: float | None
    stress_adv_participation: float | None
    normal_liquidation_days: float | None
    stress_liquidation_days: float | None
    spread_pct: float | None
    premium_discount_pct: float | None
    reason_codes: tuple[str, ...]
    contract_version: str
    contract_hash: str

    def as_context(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "status": self.status,
            "entry_allowed": self.entry_allowed,
            "trade_amount": self.trade_amount,
            "median_turnover_20d": self.median_turnover_20d,
            "turnover_sample_count": self.turnover_sample_count,
            "adv_participation": self.adv_participation,
            "stress_adv_participation": self.stress_adv_participation,
            "normal_liquidation_days": self.normal_liquidation_days,
            "stress_liquidation_days": self.stress_liquidation_days,
            "spread_pct": self.spread_pct,
            "premium_discount_pct": self.premium_discount_pct,
            "reason_codes": list(self.reason_codes),
            "contract_version": self.contract_version,
            "contract_hash": self.contract_hash,
        }


def assess_etf_liquidity_capacity(
    *,
    side: str,
    trade_amount: float | None,
    daily_turnovers: Sequence[float],
    quote_eligible: bool,
    bid_price: float | None,
    ask_price: float | None,
    premium_discount_pct: float | None,
    limit_state: str | None = None,
) -> EtfLiquidityCapacityAssessment:
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    manifest = etf_liquidity_capacity_manifest()
    amount = _finite_number(trade_amount, positive=True)
    valid_turnovers = [
        value
        for raw in daily_turnovers[-20:]
        if (value := _finite_number(raw, positive=True)) is not None
    ]
    reasons: list[str] = []
    if amount is None:
        reasons.append("trade_amount_invalid")
    adv = (
        median(valid_turnovers)
        if len(valid_turnovers) >= ETF_LIQUIDITY_MIN_TURNOVER_SESSIONS
        else None
    )
    if adv is None:
        reasons.append("turnover_history_insufficient")
    bid = _finite_number(bid_price, positive=True)
    ask = _finite_number(ask_price, positive=True)
    if not quote_eligible:
        reasons.append("quote_not_decision_eligible")
    if bid is None or ask is None or bid > ask:
        reasons.append("executable_bid_ask_unavailable")
    spread_pct = None
    if bid is not None and ask is not None and bid <= ask:
        midpoint = (bid + ask) / 2.0
        spread_pct = (ask - bid) / midpoint * 100.0
        if spread_pct > ETF_LIQUIDITY_MAX_SPREAD_PCT:
            reasons.append("spread_exceeds_limit")
    premium = _finite_number(premium_discount_pct)
    if premium is not None and abs(premium) > ETF_LIQUIDITY_MAX_ABS_PREMIUM_DISCOUNT_PCT:
        reasons.append("premium_discount_exceeds_limit")
    if side == "buy" and limit_state == "limit_up":
        reasons.append("buy_limit_up")
    if side == "sell" and limit_state == "limit_down":
        reasons.append("sell_limit_down")

    participation = amount / adv if amount is not None and adv is not None else None
    stress_adv = adv * ETF_LIQUIDITY_STRESS_TURNOVER_MULTIPLIER if adv is not None else None
    stress_participation = (
        amount / stress_adv if amount is not None and stress_adv is not None else None
    )
    normal_days = (
        amount / (adv * ETF_EXIT_NORMAL_ADV_PARTICIPATION)
        if amount is not None and adv is not None
        else None
    )
    stress_days = (
        amount / (stress_adv * ETF_EXIT_STRESS_ADV_PARTICIPATION)
        if amount is not None and stress_adv is not None
        else None
    )
    if side == "buy" and participation is not None:
        if participation > ETF_ENTRY_MAX_ADV_PARTICIPATION:
            reasons.append("entry_participation_exceeds_limit")
        if (
            stress_participation is not None
            and stress_participation > ETF_ENTRY_STRESS_MAX_ADV_PARTICIPATION
        ):
            reasons.append("entry_stress_participation_exceeds_limit")

    fundamental_unavailable = any(
        reason in reasons
        for reason in (
            "trade_amount_invalid",
            "turnover_history_insufficient",
            "quote_not_decision_eligible",
            "executable_bid_ask_unavailable",
        )
    )
    if fundamental_unavailable:
        status = "unavailable"
    elif reasons:
        status = "blocked" if side == "buy" else "stressed"
    else:
        status = "ready"
    return EtfLiquidityCapacityAssessment(
        side=side,
        status=status,
        entry_allowed=side == "buy" and status == "ready",
        trade_amount=round(amount, 2) if amount is not None else None,
        median_turnover_20d=round(adv, 2) if adv is not None else None,
        turnover_sample_count=len(valid_turnovers),
        adv_participation=round(participation, 8) if participation is not None else None,
        stress_adv_participation=(
            round(stress_participation, 8) if stress_participation is not None else None
        ),
        normal_liquidation_days=round(normal_days, 6) if normal_days is not None else None,
        stress_liquidation_days=round(stress_days, 6) if stress_days is not None else None,
        spread_pct=round(spread_pct, 6) if spread_pct is not None else None,
        premium_discount_pct=round(premium, 6) if premium is not None else None,
        reason_codes=tuple(dict.fromkeys(reasons)),
        contract_version=ETF_LIQUIDITY_CAPACITY_VERSION,
        contract_hash=str(manifest["contract_hash"]),
    )


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
class LeaderTacticsDailyBar:
    """One already-authorized total-return-adjusted daily bar."""

    trade_date: date
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    provider: str
    adjustment_version: str
    revision_id: str
    received_at: datetime | None
    decision_eligible: bool = True
    price_basis: str = LEADER_TACTICS_PRICE_BASIS


@dataclass(frozen=True)
class LeaderTacticsExitInput:
    """Pure input for one causal close-based leader-tactics evaluation."""

    entry_anchor_date: date
    evaluation_cutoff: datetime
    bars: Sequence[LeaderTacticsDailyBar]
    persisted_state: Mapping[str, Any] = field(default_factory=dict)
    source_signal_low: float | None = None
    source_strategy: str | None = None


@dataclass(frozen=True)
class LeaderTacticsExitDecision:
    actionable: bool
    data_eligible: bool
    reason_code: str
    label: str
    alert_type: str | None
    level: str
    threshold_context: Mapping[str, Any]
    state_update: Mapping[str, Any]


def _leader_decision(
    *,
    actionable: bool,
    data_eligible: bool,
    reason_code: str,
    label: str,
    context: dict[str, Any],
    state: dict[str, Any],
    level: str = "none",
) -> LeaderTacticsExitDecision:
    return LeaderTacticsExitDecision(
        actionable=actionable,
        data_eligible=data_eligible,
        reason_code=reason_code,
        label=label,
        alert_type=ALERT_LEADER_TACTICS_EXIT if actionable else None,
        level=level,
        threshold_context=MappingProxyType(context),
        state_update=MappingProxyType(state),
    )


def _leader_positive_finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _leader_utc_naive(value: object) -> datetime | None:
    """Normalize aware/naive timestamps to the database's UTC-naive convention."""

    if not isinstance(value, datetime):
        return None
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)
    except (OverflowError, TypeError, ValueError):
        return None


def _leader_cutoff_trade_date(value: datetime) -> date:
    """Return the Shanghai trading date represented by a cutoff timestamp."""

    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_LEADER_TACTICS_SHANGHAI).date()


def _leader_atr20(bars: Sequence[LeaderTacticsDailyBar], end_index: int) -> float | None:
    if end_index < 20 or end_index >= len(bars):
        return None
    ranges: list[float] = []
    for previous, current in zip(
        bars[end_index - 20 : end_index],
        bars[end_index - 19 : end_index + 1],
        strict=True,
    ):
        high = _leader_positive_finite(current.adjusted_high)
        low = _leader_positive_finite(current.adjusted_low)
        previous_close = _leader_positive_finite(previous.adjusted_close)
        if high is None or low is None or previous_close is None or low > high:
            return None
        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        if not math.isfinite(true_range) or true_range <= 0:
            return None
        ranges.append(true_range)
    result = math.fsum(ranges) / len(ranges) if ranges else None
    return result if result is not None and math.isfinite(result) and result > 0 else None


def evaluate_leader_tactics_exit(
    value: LeaderTacticsExitInput,
) -> LeaderTacticsExitDecision:
    """Evaluate the leader-tactics full-exit policy from eligible daily bars only.

    The rule intentionally has no profit target or trailing drawdown. It freezes
    the first eligible post-entry close, the entry ATR20 and the initial risk
    line, then protects the position with MA5, hard-stop and (once armed)
    breakeven lines. Only a successfully notified episode is suppressed.
    """

    persisted = dict(value.persisted_state or {})
    entry_anchor_date = getattr(value, "entry_anchor_date", None)
    evaluation_cutoff = getattr(value, "evaluation_cutoff", None)
    if not (
        isinstance(entry_anchor_date, date)
        and not isinstance(entry_anchor_date, datetime)
        and isinstance(evaluation_cutoff, datetime)
        and _leader_utc_naive(evaluation_cutoff) is not None
    ):
        return _leader_decision(
            actionable=False,
            data_eligible=False,
            reason_code=LEADER_TACTICS_DATA_WAITING,
            label="龙头策略等待合格复权日线",
            context={
                "policy_id": LEADER_TACTICS_EXIT_POLICY_ID,
                "policy_version": LEADER_TACTICS_EXIT_POLICY_VERSION,
                "price_basis": LEADER_TACTICS_PRICE_BASIS,
                "data_reason": "invalid_evaluation_input",
            },
            state={"policy_id": LEADER_TACTICS_EXIT_POLICY_ID},
        )

    assert isinstance(entry_anchor_date, date)
    assert isinstance(evaluation_cutoff, datetime)
    cutoff_utc = _leader_utc_naive(evaluation_cutoff)
    assert cutoff_utc is not None
    cutoff_trade_date = _leader_cutoff_trade_date(evaluation_cutoff)
    base_context: dict[str, Any] = {
        "policy_id": LEADER_TACTICS_EXIT_POLICY_ID,
        "policy_version": LEADER_TACTICS_EXIT_POLICY_VERSION,
        "price_basis": LEADER_TACTICS_PRICE_BASIS,
        "evaluation_cutoff": evaluation_cutoff.isoformat(),
        "entry_anchor_date": entry_anchor_date.isoformat(),
        "source_strategy": value.source_strategy,
        "fee_bps_per_side": 5.0,
        "slippage_bps_per_side": 5.0,
        "round_trip_cost_bps": LEADER_TACTICS_ROUND_TRIP_COST_BPS,
    }
    base_state: dict[str, Any] = {
        "policy_id": LEADER_TACTICS_EXIT_POLICY_ID,
        "policy_version": LEADER_TACTICS_EXIT_POLICY_VERSION,
        "last_evaluated_at": evaluation_cutoff.isoformat(),
    }

    try:
        bars = tuple(value.bars or ())
    except TypeError:
        bars = ()
    if not bars:
        return _leader_decision(
            actionable=False,
            data_eligible=False,
            reason_code=LEADER_TACTICS_DATA_WAITING,
            label="龙头策略等待连续合格复权日线",
            context={**base_context, "data_reason": "daily_bar_order_or_duplicate"},
            state=base_state,
        )
    trade_dates: list[date] = []
    adjustment_versions: list[str] = []
    for bar in bars:
        if not isinstance(bar, LeaderTacticsDailyBar):
            return _leader_decision(
                actionable=False,
                data_eligible=False,
                reason_code=LEADER_TACTICS_DATA_WAITING,
                label="龙头策略等待合格复权日线",
                context={**base_context, "data_reason": "bar_type_invalid"},
                state=base_state,
            )
        if not isinstance(bar.trade_date, date) or isinstance(bar.trade_date, datetime):
            return _leader_decision(
                actionable=False,
                data_eligible=False,
                reason_code=LEADER_TACTICS_DATA_WAITING,
                label="龙头策略等待合格复权日线",
                context={**base_context, "data_reason": "trade_date_invalid"},
                state=base_state,
            )
        received_at_utc = _leader_utc_naive(bar.received_at)
        provider = bar.provider.strip().lower() if isinstance(bar.provider, str) else ""
        adjustment_version = (
            bar.adjustment_version.strip() if isinstance(bar.adjustment_version, str) else ""
        )
        revision_id = bar.revision_id.strip() if isinstance(bar.revision_id, str) else ""
        values = (
            bar.adjusted_open,
            bar.adjusted_high,
            bar.adjusted_low,
            bar.adjusted_close,
        )
        if (
            bar.decision_eligible is not True
            or bar.price_basis != LEADER_TACTICS_PRICE_BASIS
            or provider not in LEADER_TACTICS_APPROVED_PROVIDERS
            or not adjustment_version
            or not revision_id
            or received_at_utc is None
            or received_at_utc > cutoff_utc
            or bar.trade_date > cutoff_trade_date
            or any(_leader_positive_finite(item) is None for item in values)
            or bar.adjusted_high < max(bar.adjusted_open, bar.adjusted_close)
            or bar.adjusted_low > min(bar.adjusted_open, bar.adjusted_close)
        ):
            return _leader_decision(
                actionable=False,
                data_eligible=False,
                reason_code=LEADER_TACTICS_DATA_WAITING,
                label="龙头策略等待合格复权日线",
                context={**base_context, "data_reason": "ineligible_or_invalid_adjusted_bar"},
                state=base_state,
            )
        trade_dates.append(bar.trade_date)
        adjustment_versions.append(adjustment_version)
    if tuple(sorted(trade_dates)) != tuple(trade_dates) or len(set(trade_dates)) != len(trade_dates):
        return _leader_decision(
            actionable=False,
            data_eligible=False,
            reason_code=LEADER_TACTICS_DATA_WAITING,
            label="龙头策略等待连续合格复权日线",
            context={**base_context, "data_reason": "daily_bar_order_or_duplicate"},
            state=base_state,
        )
    if len(set(adjustment_versions)) != 1:
        return _leader_decision(
            actionable=False,
            data_eligible=False,
            reason_code=LEADER_TACTICS_DATA_WAITING,
            label="龙头策略等待统一复权口径",
            context={**base_context, "data_reason": "adjustment_version_mismatch"},
            state=base_state,
        )
    base_context["adjustment_version"] = adjustment_versions[0]

    entry_index = next(
        (index for index, bar in enumerate(bars) if bar.trade_date >= entry_anchor_date),
        None,
    )
    entry_close = _leader_positive_finite(persisted.get("entry_adjusted_close"))
    entry_atr20 = _leader_positive_finite(persisted.get("entry_atr20"))
    initial_stop = _leader_positive_finite(persisted.get("initial_stop"))
    risk_unit = _leader_positive_finite(persisted.get("risk_unit"))
    frozen_adjustment_version = (
        persisted.get("adjustment_version", "").strip()
        if isinstance(persisted.get("adjustment_version"), str)
        else ""
    )
    if any(item is not None for item in (entry_close, entry_atr20, initial_stop, risk_unit)) and not all(
        item is not None for item in (entry_close, entry_atr20, initial_stop, risk_unit)
    ):
        return _leader_decision(
            actionable=False,
            data_eligible=False,
            reason_code=LEADER_TACTICS_DATA_WAITING,
            label="龙头策略冻结状态不完整",
            context={**base_context, "data_reason": "frozen_state_incomplete"},
            state=base_state,
        )
    frozen_state = all(
        item is not None for item in (entry_close, entry_atr20, initial_stop, risk_unit)
    )
    if frozen_state and (
        not frozen_adjustment_version
        or frozen_adjustment_version != adjustment_versions[0]
        or initial_stop >= entry_close
        or not math.isclose(
            risk_unit,
            entry_close - initial_stop,
            rel_tol=1e-9,
            abs_tol=1e-12,
        )
    ):
        return _leader_decision(
            actionable=False,
            data_eligible=False,
            reason_code=LEADER_TACTICS_DATA_WAITING,
            label="龙头策略冻结风险口径不一致",
            context={**base_context, "data_reason": "frozen_state_basis_mismatch"},
            state=base_state,
        )
    if not frozen_state:
        if entry_index is None or entry_index < 20 or len(bars) - entry_index < 1:
            return _leader_decision(
                actionable=False,
                data_eligible=False,
                reason_code=LEADER_TACTICS_DATA_WAITING,
                label="龙头策略等待 ATR20 预热数据",
                context={
                    **base_context,
                    "data_reason": "entry_atr20_warmup_missing",
                    "bar_count": len(bars),
                },
                state=base_state,
            )
    elif entry_index is None:
        if not bars or bars[-1].trade_date < entry_anchor_date:
            return _leader_decision(
                actionable=False,
                data_eligible=False,
                reason_code=LEADER_TACTICS_DATA_WAITING,
                label="龙头策略等待入场后的合格复权日线",
                context={**base_context, "data_reason": "entry_not_visible_in_window"},
                state=base_state,
            )
        # The bounded window can start after a long-held position's entry.
        entry_index = 0
    if entry_close is None:
        entry_close = _leader_positive_finite(bars[entry_index].adjusted_close)
        entry_atr20 = _leader_atr20(bars, entry_index)
        if entry_close is None or entry_atr20 is None:
            return _leader_decision(
                actionable=False,
                data_eligible=False,
                reason_code=LEADER_TACTICS_DATA_WAITING,
                label="龙头策略等待 ATR20 预热数据",
                context={**base_context, "data_reason": "entry_atr20_unavailable"},
                state=base_state,
            )
        candidate_low = _leader_positive_finite(value.source_signal_low)
        usable_signal_low = candidate_low if candidate_low is not None and candidate_low < entry_close else None
        initial_stop = max(
            usable_signal_low if usable_signal_low is not None else 0.0,
            entry_close - 2.0 * entry_atr20,
        )
        if initial_stop <= 0 or initial_stop >= entry_close:
            initial_stop = entry_close - 2.0 * entry_atr20
        risk_unit = entry_close - initial_stop
        if not math.isfinite(risk_unit) or risk_unit <= 0:
            return _leader_decision(
                actionable=False,
                data_eligible=False,
                reason_code=LEADER_TACTICS_DATA_WAITING,
                label="龙头策略初始风险线无效",
                context={**base_context, "data_reason": "initial_risk_unit_invalid"},
                state=base_state,
            )
        persisted["source_signal_low_ignored"] = (
            candidate_low is not None and candidate_low >= entry_close
        )
    assert entry_close is not None
    assert entry_atr20 is not None
    assert initial_stop is not None
    assert risk_unit is not None
    current = _leader_positive_finite(bars[-1].adjusted_close)
    closes = [_leader_positive_finite(bar.adjusted_close) for bar in bars[-5:]]
    if current is None or len(closes) != 5 or any(item is None for item in closes):
        return _leader_decision(
            actionable=False,
            data_eligible=False,
            reason_code=LEADER_TACTICS_DATA_WAITING,
            label="龙头策略等待 MA5 复权收盘",
            context={**base_context, "data_reason": "ma5_unavailable"},
            state=base_state,
        )
    ma5 = math.fsum(item for item in closes if item is not None) / 5.0
    previous_high = _leader_positive_finite(persisted.get("high_water_adjusted_close"))
    visible_highs = [
        _leader_positive_finite(bar.adjusted_close)
        for bar in bars[entry_index:]
    ]
    if any(item is None for item in visible_highs):
        return _leader_decision(
            actionable=False,
            data_eligible=False,
            reason_code=LEADER_TACTICS_DATA_WAITING,
            label="龙头策略等待合格复权日线",
            context={**base_context, "data_reason": "high_water_unavailable"},
            state=base_state,
        )
    high_water = max(
        [item for item in visible_highs if item is not None]
        + ([previous_high] if previous_high is not None else [])
    )
    armed = bool(persisted.get("armed") is True or high_water >= entry_close + risk_unit)
    notification_sent = persisted.get("exit_notification_sent") is True
    breakeven = entry_close * (1.0 + LEADER_TACTICS_ROUND_TRIP_COST_BPS / 10_000.0) if armed else None
    effective_line = max(
        initial_stop,
        *(line for line in (breakeven, ma5) if line is not None),
    )
    context = {
        **base_context,
        "bar_date": bars[-1].trade_date.isoformat(),
        "bar_cutoff": bars[-1].received_at.isoformat() if bars[-1].received_at else None,
        "provider": bars[-1].provider,
        "revision": bars[-1].revision_id,
        "entry_adjusted_close": round(entry_close, 8),
        "entry_atr20": round(entry_atr20, 8),
        "initial_stop": round(initial_stop, 8),
        "risk_unit": round(risk_unit, 8),
        "high_water_adjusted_close": round(high_water, 8),
        "armed": armed,
        "breakeven_line": round(breakeven, 8) if breakeven is not None else None,
        "adjusted_ma5": round(ma5, 8),
        "effective_exit_line": round(effective_line, 8),
        "current_adjusted_close": round(current, 8),
        "source_signal_low_ignored": bool(persisted.get("source_signal_low_ignored", False)),
        "exit_notification_sent": notification_sent,
        "trigger_priority": [LEADER_TACTICS_HARD_STOP, LEADER_TACTICS_BREAKEVEN_EXIT, LEADER_TACTICS_MA5_EXIT],
    }
    state = {
        **base_state,
        "entry_adjusted_close": entry_close,
        "entry_atr20": entry_atr20,
        "initial_stop": initial_stop,
        "risk_unit": risk_unit,
        "adjustment_version": adjustment_versions[0],
        "high_water_adjusted_close": high_water,
        "armed": armed,
        "breakeven_line": breakeven,
        "last_bar_date": bars[-1].trade_date.isoformat(),
        "source_signal_low_ignored": bool(persisted.get("source_signal_low_ignored", False)),
        "exit_triggered": bool(persisted.get("exit_triggered", False)),
        "exit_notification_sent": notification_sent,
    }
    if current <= effective_line:
        if notification_sent:
            return _leader_decision(
                actionable=False,
                data_eligible=True,
                reason_code="leader_tactics_exit_already_triggered",
                label="龙头策略退出信号已触发",
                context=context,
                state=state,
            )
        hard_stop_hit = current <= initial_stop
        breakeven_hit = breakeven is not None and current <= breakeven
        ma5_hit = current <= ma5
        if hard_stop_hit:
            reason_code = LEADER_TACTICS_HARD_STOP
        elif breakeven_hit:
            reason_code = LEADER_TACTICS_BREAKEVEN_EXIT
        elif ma5_hit:
            reason_code = LEADER_TACTICS_MA5_EXIT
        else:
            reason_code = LEADER_TACTICS_MA5_EXIT
        state["exit_triggered"] = True
        state["exit_triggered_date"] = bars[-1].trade_date.isoformat()
        return _leader_decision(
            actionable=True,
            data_eligible=True,
            reason_code=reason_code,
            label="龙头策略全额退出提醒",
            context=context,
            state=state,
            level="urgent" if reason_code == LEADER_TACTICS_HARD_STOP else "warning",
        )
    return _leader_decision(
        actionable=False,
        data_eligible=True,
        reason_code="leader_tactics_exit_not_triggered",
        label="暂无龙头策略退出提醒",
        context=context,
        state=state,
    )


@dataclass(frozen=True)
class ExitExecutionEvidence:
    status: str
    signal_price: float | None
    executable_reference_price: float | None
    price_basis: str | None
    spread_pct: float | None
    signal_to_executable_gap_bps: float | None
    gap_through_stop_bps: float | None
    slippage_reserve_bps: float
    reason_code: str
    evidence_version: str = EXIT_EXECUTION_EVIDENCE_VERSION

    def as_context(self) -> dict[str, Any]:
        return {
            "evidence_version": self.evidence_version,
            "side": "sell",
            "status": self.status,
            "signal_price": self.signal_price,
            "executable_reference_price": self.executable_reference_price,
            "price_basis": self.price_basis,
            "spread_pct": self.spread_pct,
            "signal_to_executable_gap_bps": self.signal_to_executable_gap_bps,
            "gap_through_stop_bps": self.gap_through_stop_bps,
            "slippage_reserve_bps": self.slippage_reserve_bps,
            "reason_code": self.reason_code,
            "automatic_execution": False,
            "execution_provenance": "none",
        }


def _positive_finite(value: float | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def evaluate_exit_execution_evidence(
    *,
    signal_price: float | None,
    bid_price: float | None,
    ask_price: float | None,
    entry_price: float | None,
    hard_stop_pct: float | None,
    quote_eligible: bool,
) -> ExitExecutionEvidence:
    signal = _positive_finite(signal_price)
    bid = _positive_finite(bid_price)
    ask = _positive_finite(ask_price)
    if not quote_eligible:
        return ExitExecutionEvidence(
            status="not_observable",
            signal_price=signal,
            executable_reference_price=None,
            price_basis=None,
            spread_pct=None,
            signal_to_executable_gap_bps=None,
            gap_through_stop_bps=None,
            slippage_reserve_bps=EXIT_SLIPPAGE_RESERVE_BPS,
            reason_code="quote_not_decision_eligible",
        )
    if signal is None:
        return ExitExecutionEvidence(
            status="not_observable",
            signal_price=None,
            executable_reference_price=None,
            price_basis=None,
            spread_pct=None,
            signal_to_executable_gap_bps=None,
            gap_through_stop_bps=None,
            slippage_reserve_bps=EXIT_SLIPPAGE_RESERVE_BPS,
            reason_code="missing_signal_price",
        )
    if bid is None or ask is None:
        return ExitExecutionEvidence(
            status="not_observable",
            signal_price=signal,
            executable_reference_price=None,
            price_basis=None,
            spread_pct=None,
            signal_to_executable_gap_bps=None,
            gap_through_stop_bps=None,
            slippage_reserve_bps=EXIT_SLIPPAGE_RESERVE_BPS,
            reason_code="missing_executable_bid_ask",
        )
    if bid > ask:
        return ExitExecutionEvidence(
            status="not_observable",
            signal_price=signal,
            executable_reference_price=None,
            price_basis=None,
            spread_pct=None,
            signal_to_executable_gap_bps=None,
            gap_through_stop_bps=None,
            slippage_reserve_bps=EXIT_SLIPPAGE_RESERVE_BPS,
            reason_code="crossed_or_invalid_bid_ask",
        )

    midpoint = (bid + ask) / 2.0
    spread_pct = (ask - bid) / midpoint * 100.0
    signal_gap_bps = (bid / signal - 1.0) * 10_000.0
    stop_gap_bps = None
    entry = _positive_finite(entry_price)
    if (
        entry is not None
        and hard_stop_pct is not None
        and not isinstance(hard_stop_pct, bool)
        and math.isfinite(float(hard_stop_pct))
    ):
        stop_price = entry * (1.0 + float(hard_stop_pct) / 100.0)
        if stop_price > 0:
            stop_gap_bps = (bid / stop_price - 1.0) * 10_000.0
    return ExitExecutionEvidence(
        status="observable",
        signal_price=round(signal, 6),
        executable_reference_price=round(bid, 6),
        price_basis="intraday_bid",
        spread_pct=round(spread_pct, 6),
        signal_to_executable_gap_bps=round(signal_gap_bps, 4),
        gap_through_stop_bps=(round(stop_gap_bps, 4) if stop_gap_bps is not None else None),
        slippage_reserve_bps=EXIT_SLIPPAGE_RESERVE_BPS,
        reason_code="observable_sell_bid",
    )


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


def _finite_metric(
    metrics: dict[str, object],
    key: str,
) -> float | None:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    resolved = float(value)
    return resolved if math.isfinite(resolved) else None


def legacy_exit_target_remaining_fraction(alert_type: str | None) -> float | None:
    """Map the authoritative legacy alert to its comparable absolute target.

    This is comparison evidence only. It never creates an action or changes the
    production legacy decision.
    """

    return {
        ALERT_HARD_STOP: 0.0,
        ALERT_MA5_CLOSE_BREAK_EXIT: 0.0,
        ALERT_LATE_DAY_T1_EXIT: 0.0,
        ALERT_TRAILING_TAKE_PROFIT: TRAILING_FIRST_TARGET_REMAINING_FRACTION,
        ALERT_CONFIRMED_TREND_WEAKENING: 0.5,
        ALERT_EXIT_WATCH: 0.5,
    }.get(alert_type)


def evaluate_position_risk_rule_set(
    *,
    technical_metrics: dict[str, object] | None,
    legacy_alert_type: str | None,
    data_eligible: bool,
    data_reason_code: str,
) -> tuple[EvaluatedRiskRule, ...]:
    """Build the complete frozen V2 rule set from one prepared analysis.

    Ineligible market evidence freezes every rule by returning ``data_waiting``.
    Eligible evaluations include false and recovery observations so a prior
    shadow episode can resolve without issuing another history/provider read.
    """

    if not data_reason_code.strip():
        raise ValueError("data_reason_code is required")
    metrics = technical_metrics or {}
    current_pnl_pct = _finite_metric(metrics, "current_pnl_pct")
    hard_stop_pct = _finite_metric(metrics, "hard_stop_pct")
    giveback_pct = _finite_metric(metrics, "profit_giveback_pct")
    trailing_threshold_pct = _finite_metric(metrics, "trailing_threshold_pct")
    profit_start_pct = _finite_metric(metrics, "profit_start_pct")
    trend_weakening_value = metrics.get("trend_weakening")
    confirmed_trend_value = metrics.get("confirmed_trend_weakening")
    ma5_close_break_value = metrics.get("ma5_close_break_condition_met")
    ma5_close_break_data_eligible = metrics.get("ma5_close_break_decision_eligible") is True
    ma5_close_break_observation_new = metrics.get("ma5_close_break_observation_new") is True
    ma5_close_break_reason_code = str(
        metrics.get("ma5_close_break_reason_code") or "ma5_adjusted_close_unavailable"
    )

    def rule(
        *,
        rule_id: str,
        observable: bool,
        condition_met: bool,
        target: float | None,
        reason: str,
        hard_stop: bool = False,
        confirmation_required: int = 2,
        data_eligible_override: bool | None = None,
        data_reason_code_override: str | None = None,
        recovery_met_override: bool | None = None,
    ) -> EvaluatedRiskRule:
        effective_data_eligible = (
            data_eligible if data_eligible_override is None else data_eligible_override
        )
        if not effective_data_eligible:
            state = "data_waiting"
            reason_code = data_reason_code_override or data_reason_code
            condition = False
            recovery = False
        elif not observable:
            state = "no_data"
            reason_code = f"{rule_id}_input_unavailable"
            condition = False
            recovery = False
        else:
            state = "eligible"
            reason_code = "eligible_prepared_analysis"
            condition = bool(condition_met)
            recovery = (
                not condition if recovery_met_override is None else bool(recovery_met_override)
            )
        return EvaluatedRiskRule(
            rule_id=rule_id,
            data_state=state,
            data_reason_code=reason_code,
            condition_met=condition,
            recovery_met=recovery,
            target_remaining_fraction=target,
            reason=reason,
            hard_stop=hard_stop,
            confirmation_required=confirmation_required,
            recovery_required=2,
        )

    hard_stop_observable = current_pnl_pct is not None and hard_stop_pct is not None
    trailing_observable = (
        current_pnl_pct is not None
        and giveback_pct is not None
        and trailing_threshold_pct is not None
    )
    trailing_condition = bool(
        trailing_threshold_pct is not None
        and giveback_pct is not None
        and giveback_pct >= trailing_threshold_pct
    )
    trend_observable = isinstance(trend_weakening_value, bool)
    confirmed_trend_observable = isinstance(confirmed_trend_value, bool)
    take_profit_observable = current_pnl_pct is not None and profit_start_pct is not None
    take_profit_threshold = (
        max(TAKE_PROFIT_WATCH_PCT, profit_start_pct) if profit_start_pct is not None else None
    )

    return (
        rule(
            rule_id=ALERT_HARD_STOP,
            observable=hard_stop_observable,
            condition_met=bool(
                hard_stop_observable and current_pnl_pct <= hard_stop_pct  # type: ignore[operator]
            ),
            target=0.0,
            reason="Prepared analysis reached the frozen hard-stop threshold.",
            hard_stop=True,
            confirmation_required=1,
        ),
        rule(
            rule_id=ALERT_MA5_CLOSE_BREAK_EXIT,
            observable=isinstance(ma5_close_break_value, bool),
            condition_met=ma5_close_break_value is True,
            target=0.0,
            reason=(
                "The decision-eligible total-return-adjusted close finished below its "
                "same-session adjusted MA5."
            ),
            hard_stop=True,
            confirmation_required=1,
            data_eligible_override=ma5_close_break_data_eligible,
            data_reason_code_override=ma5_close_break_reason_code,
            recovery_met_override=bool(
                ma5_close_break_observation_new and ma5_close_break_value is False
            ),
        ),
        rule(
            rule_id=ALERT_TRAILING_TAKE_PROFIT,
            observable=trailing_observable,
            condition_met=trailing_condition,
            target=TRAILING_FIRST_TARGET_REMAINING_FRACTION,
            reason="Prepared analysis reached the frozen trailing-profit giveback threshold.",
        ),
        rule(
            rule_id=ALERT_CONFIRMED_TREND_WEAKENING,
            observable=confirmed_trend_observable,
            condition_met=confirmed_trend_value is True,
            target=0.5,
            reason="Prepared analysis confirmed trend weakening with an independent risk condition.",
        ),
        rule(
            rule_id=ALERT_EXIT_WATCH,
            observable=True,
            condition_met=legacy_alert_type == ALERT_EXIT_WATCH,
            target=0.5,
            reason="The authoritative research context entered exit-watch state.",
        ),
        rule(
            rule_id=ALERT_TREND_WEAKENING,
            observable=trend_observable,
            condition_met=trend_weakening_value is True,
            target=None,
            reason="Prepared analysis entered the non-actionable trend guard state.",
        ),
        rule(
            rule_id=ALERT_TAKE_PROFIT_WATCH,
            observable=take_profit_observable,
            condition_met=bool(
                take_profit_observable
                and take_profit_threshold is not None
                and current_pnl_pct >= take_profit_threshold  # type: ignore[operator]
            ),
            target=None,
            reason="Prepared analysis reached the non-actionable take-profit watch threshold.",
        ),
    )


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
    liquidity_capacity: dict[str, Any] | None = None
    owner_risk_control: dict[str, Any] | None = None

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
            "liquidity_capacity": self.liquidity_capacity,
            "owner_risk_control": self.owner_risk_control,
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
    if (
        not math.isfinite(current_remaining_fraction)
        or not 0.0 <= current_remaining_fraction <= 1.0
    ):
        raise ValueError("current_remaining_fraction must be a finite fraction")
    if alert_type in {
        ALERT_HARD_STOP,
        ALERT_MA5_CLOSE_BREAK_EXIT,
        ALERT_LATE_DAY_T1_EXIT,
        ALERT_LEADER_TACTICS_EXIT,
    }:
        return PositionActionDecision(
            action=POSITION_ACTION_EXIT,
            action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            label=position_action_label(POSITION_ACTION_EXIT),
            target_remaining_fraction=0.0,
            reason={
                ALERT_HARD_STOP: "触发硬止损，绝对目标为当前 exposure baseline 的 0%。",
                ALERT_MA5_CLOSE_BREAK_EXIT: "复权收盘价跌破同日复权五日线，T 日确认并以 T+1 为最早可执行日，绝对目标为当前 exposure baseline 的 0%。",
                ALERT_LATE_DAY_T1_EXIT: "尾盘转强 T+1 策略触发版本化全量退出，绝对目标为当前 exposure baseline 的 0%。",
                ALERT_LEADER_TACTICS_EXIT: "龙头策略触发全额退出，绝对目标为当前 exposure baseline 的 0%；个股金额和份额请按券商实际持仓处理。",
            }[alert_type],
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
        confirmed = (
            loss_confirmed or giveback_confirmed or ranking_deteriorated or market_regime_weak
        )
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
    capital_confirmed: bool,
    allow_full_exit: bool,
    target_portfolio_weight: float | None = None,
    entry_timing_label: str | None = None,
    trend_weakening: bool = False,
    exposure_baseline_quantity: float | None = None,
) -> PositionSizingRecommendation:
    if asset_type != ASSET_TYPE_ETF:
        if alert_type == ALERT_LEADER_TACTICS_EXIT:
            return PositionSizingRecommendation(
                action=POSITION_ACTION_EXIT,
                label=position_action_label(POSITION_ACTION_EXIT),
                target_account_weight=0.0,
                reason="龙头策略触发个股全额退出；不估算金额或份额，请按券商实际持仓处理。",
                action_class=ACTION_CLASS_ACTIONABLE_EXIT,
            )
        return PositionSizingRecommendation(
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="仓位金额建议第一版只用于场内 ETF。",
        )
    capital = _finite_number(etf_trading_capital, positive=True) if capital_confirmed else None
    market_value = _finite_number(current_market_value, nonnegative=True)
    price = _finite_number(current_price, positive=True)
    baseline_quantity = _finite_number(exposure_baseline_quantity, positive=True)
    if baseline_quantity is None and market_value is not None and price is not None:
        baseline_quantity = market_value / price
    current_remaining_fraction = 1.0
    if market_value is not None and price is not None and baseline_quantity is not None:
        current_remaining_fraction = min(
            1.0,
            max(0.0, market_value / (baseline_quantity * price)),
        )
    action_decision = map_exit_signal_to_position_action(
        alert_type=alert_type,
        allow_full_exit=allow_full_exit,
        trend_weakening=trend_weakening,
        current_remaining_fraction=current_remaining_fraction,
    )
    action = action_decision.action
    reason = action_decision.reason
    current_weight = (
        market_value / capital if market_value is not None and capital is not None else None
    )
    target_weight = current_weight
    if (
        action_decision.target_remaining_fraction is not None
        and baseline_quantity is not None
        and price is not None
        and capital is not None
        and current_weight is not None
    ):
        absolute_target_weight = (
            baseline_quantity * price / capital
        ) * action_decision.target_remaining_fraction
        target_weight = min(current_weight, absolute_target_weight)

    if (
        alert_type is None
        and target_portfolio_weight is not None
        and entry_timing_allows_add(entry_timing_label)
        and capital is None
    ):
        return PositionSizingRecommendation(
            action=POSITION_ACTION_NO_ADD,
            label=position_action_label(POSITION_ACTION_NO_ADD),
            current_market_value=round(market_value, 2) if market_value is not None else None,
            reason="ETF 交易资金尚未由用户显式确认，暂停给出加仓金额和份额。",
            action_class=ACTION_CLASS_GUARD_ONLY,
        )

    if (
        alert_type is None
        and target_portfolio_weight is not None
        and entry_timing_allows_add(entry_timing_label)
        and market_value is not None
        and current_weight is not None
    ):
        capped_target = min(max(target_portfolio_weight, 0.0), ETF_SINGLE_WEIGHT_CAP)
        if capped_target > current_weight:
            action = POSITION_ACTION_ADD
            target_weight = capped_target
            reason = (
                "最新 ETF 观察组合目标权重大于当前持仓，且买点状态未进入等待/追高，给出加仓参考。"
            )

    if action == POSITION_ACTION_ADD and capital is None:
        return PositionSizingRecommendation(
            action=POSITION_ACTION_NO_ADD,
            label=position_action_label(POSITION_ACTION_NO_ADD),
            current_market_value=round(market_value, 2) if market_value is not None else None,
            reason="ETF 交易资金尚未由用户显式确认，暂停给出加仓金额和份额。",
            action_class=ACTION_CLASS_GUARD_ONLY,
        )

    missing_size_evidence: list[str] = []
    if capital is None:
        missing_size_evidence.append("ETF 交易资金尚未显式确认")
    if market_value is None or price is None:
        missing_size_evidence.append("可信价格或持仓市值不可用")
    if baseline_quantity is None:
        missing_size_evidence.append("不可变持仓份额基线不可用")
    if missing_size_evidence and action in {
        POSITION_ACTION_TRIM,
        POSITION_ACTION_REDUCE,
        POSITION_ACTION_EXIT,
    }:
        return PositionSizingRecommendation(
            action=action,
            label=position_action_label(action),
            current_market_value=round(market_value, 2) if market_value is not None else None,
            current_account_weight=(
                round(current_weight, 4) if current_weight is not None else None
            ),
            target_account_weight=(round(target_weight, 4) if target_weight is not None else None),
            reason=f"{reason}；{'；'.join(missing_size_evidence)}，保留退出动作但不生成金额或份额。",
            action_class=action_decision.action_class,
        )

    if market_value is None or price is None:
        return PositionSizingRecommendation(
            action=action,
            label=position_action_label(action),
            reason="等待可信价格和份额后再计算仓位金额。",
            action_class=action_decision.action_class,
        )

    label = position_action_label(action)
    if action == POSITION_ACTION_HOLD:
        return PositionSizingRecommendation(
            action=action,
            label=label,
            current_market_value=round(market_value, 2),
            current_account_weight=round(current_weight, 4) if current_weight is not None else None,
            target_account_weight=round(target_weight, 4) if target_weight is not None else None,
            reason=reason,
            action_class=action_decision.action_class,
        )
    if action == POSITION_ACTION_NO_ADD:
        return PositionSizingRecommendation(
            action=action,
            label=label,
            current_market_value=round(market_value, 2),
            current_account_weight=round(current_weight, 4) if current_weight is not None else None,
            target_account_weight=round(current_weight, 4) if current_weight is not None else None,
            reason=reason,
            action_class=ACTION_CLASS_GUARD_ONLY,
        )

    if capital is None or target_weight is None:
        return PositionSizingRecommendation(
            action=action,
            label=label,
            current_market_value=round(market_value, 2),
            reason="ETF 交易资金尚未显式确认，保留动作但不生成金额或份额。",
            action_class=action_decision.action_class,
        )
    target_market_value = max(0.0, target_weight * capital)
    raw_amount = target_market_value - market_value
    if action in {POSITION_ACTION_TRIM, POSITION_ACTION_REDUCE, POSITION_ACTION_EXIT}:
        raw_amount = market_value - target_market_value
    trade_amount: float | None = _round_trade_amount(max(0.0, raw_amount))
    if trade_amount is not None and trade_amount <= 0:
        trade_amount = None
    trade_shares = _round_trade_shares(trade_amount / price) if trade_amount is not None else None
    return PositionSizingRecommendation(
        action=action,
        label=label,
        current_market_value=round(market_value, 2),
        current_account_weight=round(current_weight, 4) if current_weight is not None else None,
        target_account_weight=round(target_weight, 4),
        recommended_trade_amount=trade_amount,
        recommended_trade_shares=trade_shares,
        reason=reason,
        action_class=action_decision.action_class
        if action != POSITION_ACTION_ADD
        else ACTION_CLASS_NONE,
    )
