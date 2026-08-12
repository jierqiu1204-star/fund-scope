from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

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
}


def _finite_number(value: object, *, positive: bool = False, nonnegative: bool = False) -> float | None:
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
        mark_ready = bool(position.decision_eligible and quantity is not None and market_value is not None)
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
        reasons = ["sleeve_nav_unavailable" if nav_status != "ready" else "drawdown_history_insufficient"]
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
        prior_signal_cycles < ETF_RISK_STOP_CYCLE_TRIGGER
        or signal_cycles > prior_signal_cycles
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
    adv = median(valid_turnovers) if len(valid_turnovers) >= ETF_LIQUIDITY_MIN_TURNOVER_SESSIONS else None
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
        if stress_participation is not None and stress_participation > ETF_ENTRY_STRESS_MAX_ADV_PARTICIPATION:
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

    def rule(
        *,
        rule_id: str,
        observable: bool,
        condition_met: bool,
        target: float | None,
        reason: str,
        hard_stop: bool = False,
        confirmation_required: int = 2,
    ) -> EvaluatedRiskRule:
        if not data_eligible:
            state = "data_waiting"
            reason_code = data_reason_code
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
            recovery = not condition
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
        return PositionSizingRecommendation(
            action=POSITION_ACTION_HOLD,
            label=position_action_label(POSITION_ACTION_HOLD),
            reason="仓位金额建议第一版只用于场内 ETF。",
        )
    capital = (
        _finite_number(etf_trading_capital, positive=True)
        if capital_confirmed
        else None
    )
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
    current_weight = market_value / capital if market_value is not None and capital is not None else None
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
            current_account_weight=(round(current_weight, 4) if current_weight is not None else None),
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
    trade_shares = (
        _round_trade_shares(trade_amount / price) if trade_amount is not None else None
    )
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
