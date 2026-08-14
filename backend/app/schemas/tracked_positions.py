from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from app.schemas.etf_quotes import DynamicExitThresholdsOut, TrackedEtfIntradaySnapshotOut

TrackedPositionAlertPolicyInput = Literal[
    "standard_dynamic_v2", "late_day_turnaround_t1_v1", "leader_tactics_exit_v1"
]
OrderTimeBucket = Literal["before_15", "after_15", "unknown"]
ExposureMutationIntentInput = Literal[
    "correction",
    "net_add",
    "net_reduce",
    "close",
    "reopen",
    "corporate_action",
    "tracking_status",
]
ActionTransitionInput = Literal["acknowledge", "execute", "cancel"]


class TrackedEtfSleeveHoldingReconciliationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    position_id: int = Field(gt=0)
    quantity: FiniteFloat = Field(gt=0)
    remaining_cost_basis: FiniteFloat = Field(ge=0)


class TrackedEtfSleeveReconciliationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    trade_session: date
    occurred_at: datetime
    cash_balance: FiniteFloat = Field(ge=0)
    holdings: list[TrackedEtfSleeveHoldingReconciliationInput] = Field(
        default_factory=list,
        max_length=100,
    )
    source_reference_hash: str | None = Field(default=None, min_length=16, max_length=128)

    @model_validator(mode="after")
    def validate_unique_positions(self) -> TrackedEtfSleeveReconciliationInput:
        position_ids = [item.position_id for item in self.holdings]
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("holdings must contain each tracked position exactly once")
        return self


class TrackedEtfSleeveReconciliationOut(BaseModel):
    ledger_event_id: int
    event_hash: str
    status: Literal["accepted", "replayed"]
    trade_session: date
    risk_state: Literal["normal", "reduce_only", "data_halt"] = "data_halt"
    reason_codes: list[str] = Field(default_factory=list)


class TrackedEtfSleeveRiskOut(BaseModel):
    status: Literal["ready", "unavailable"] = "unavailable"
    state: Literal["normal", "reduce_only", "data_halt"] = "data_halt"
    trade_session: date | None = None
    equity: float | None = None
    flow_adjusted_nav: float | None = None
    high_water_nav: float | None = None
    drawdown: float | None = None
    valuation_coverage: float = 0.0
    execution_coverage: float = 0.0
    unavailable_reasons: list[str] = Field(default_factory=list)
    contract_version: str | None = None
    contract_hash: str | None = None
    source_hash: str | None = None


class TrackedEtfLiquidityCapacityOut(BaseModel):
    status: str
    side: str
    entry_allowed: bool
    trade_amount: float | None = None
    median_turnover_20d: float | None = None
    turnover_sample_count: int = 0
    adv_participation: float | None = None
    stress_adv_participation: float | None = None
    normal_liquidation_days: float | None = None
    stress_liquidation_days: float | None = None
    spread_pct: float | None = None
    premium_discount_pct: float | None = None
    reason_codes: list[str] = Field(default_factory=list)
    contract_version: str
    contract_hash: str


class TrackedPositionActionExecutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    executed_at: datetime
    quantity: FiniteFloat = Field(gt=0)
    price: FiniteFloat = Field(gt=0)
    price_source: str = Field(min_length=1, max_length=64)
    fees: FiniteFloat = Field(default=0, ge=0)
    resulting_shares: FiniteFloat = Field(ge=0)
    close_fact: bool = False


class TrackedPositionActionTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    transition: ActionTransitionInput
    expected_position_state_version: int = Field(ge=0)
    execution: TrackedPositionActionExecutionInput | None = None

    @model_validator(mode="after")
    def validate_execution_shape(self) -> TrackedPositionActionTransitionRequest:
        if self.transition == "execute" and self.execution is None:
            raise ValueError("execution facts are required for execute")
        if self.transition != "execute" and self.execution is not None:
            raise ValueError("execution facts are only valid for execute")
        return self


class TrackedPositionActionTransitionOut(BaseModel):
    action_id: int
    transition: str
    action_status: str
    target_remaining_fraction: float
    target_normalized_quantity: float
    cumulative_executed_quantity: float
    execution_provenance: str
    position_state_version: int
    resulting_shares: float | None = None
    close_fact: bool | None = None
    executed_at: str | None = None
    idempotent_replay: bool = False


class TrackedPositionCreate(BaseModel):
    asset_type: Literal["fund", "etf", "stock"]
    asset_code: str
    buy_date: date | None = None
    order_time_bucket: OrderTimeBucket = "unknown"
    confirmed_nav_date: date | None = None
    confirmed_nav: float | None = Field(default=None, gt=0)
    confirmed_shares: float | None = Field(default=None, gt=0)
    buy_amount: float = Field(default=3000, gt=0)
    note: str | None = None
    alert_policy_id: TrackedPositionAlertPolicyInput = "standard_dynamic_v2"
    source_manifest_hash: str | None = Field(default=None, min_length=16, max_length=128)
    source_decision_at: datetime | None = None

    @model_validator(mode="after")
    def validate_alert_policy(self) -> TrackedPositionCreate:
        if self.alert_policy_id == "late_day_turnaround_t1_v1" and self.asset_type != "etf":
            raise ValueError("late_day_turnaround_t1_v1 只支持 ETF 追踪")
        if self.alert_policy_id == "leader_tactics_exit_v1" and self.asset_type not in {
            "etf",
            "stock",
        }:
            raise ValueError("leader_tactics_exit_v1 只支持 ETF 或股票追踪")
        if self.alert_policy_id == "standard_dynamic_v2" and self.asset_type == "stock":
            raise ValueError("股票追踪必须选择 leader_tactics_exit_v1")
        if self.alert_policy_id == "standard_dynamic_v2" and (
            self.source_manifest_hash is not None or self.source_decision_at is not None
        ):
            raise ValueError("默认邮件规则不能附加尾盘候选来源")
        if (self.source_manifest_hash is None) != (self.source_decision_at is None):
            raise ValueError("source_manifest_hash 和 source_decision_at 必须同时提供")
        if self.source_decision_at is not None and self.source_decision_at.tzinfo is None:
            raise ValueError("source_decision_at 必须带时区")
        return self


class TrackedPositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buy_date: date | None = None
    order_time_bucket: OrderTimeBucket | None = None
    confirmed_nav_date: date | None = None
    confirmed_nav: float | None = Field(default=None, gt=0)
    confirmed_shares: float | None = Field(default=None, gt=0)
    buy_amount: float | None = Field(default=None, gt=0)
    note: str | None = None
    asset_type: Literal["fund", "etf", "stock"] | None = None
    status: Literal["active", "handled", "closed", "stopped"] | None = None
    expected_exit_state_version: int | None = Field(default=None, ge=0)
    exposure_mutation_intent: ExposureMutationIntentInput | None = None
    mutation_reason: str | None = None

    alert_policy_id: TrackedPositionAlertPolicyInput | None = None
    source_manifest_hash: str | None = Field(default=None, min_length=16, max_length=128)
    source_decision_at: datetime | None = None

    @model_validator(mode="after")
    def validate_policy_provenance_pair(self) -> TrackedPositionUpdate:
        fields = self.model_fields_set
        manifest_set = "source_manifest_hash" in fields
        decision_set = "source_decision_at" in fields
        if manifest_set != decision_set:
            raise ValueError("source_manifest_hash 和 source_decision_at 必须同时更新")
        if self.source_decision_at is not None and self.source_decision_at.tzinfo is None:
            raise ValueError("source_decision_at 必须带时区")
        if self.alert_policy_id == "late_day_turnaround_t1_v1" and self.asset_type not in {
            None,
            "etf",
        }:
            raise ValueError("late_day_turnaround_t1_v1 只支持 ETF 追踪")
        if self.alert_policy_id == "leader_tactics_exit_v1" and self.asset_type not in {
            None,
            "etf",
            "stock",
        }:
            raise ValueError("leader_tactics_exit_v1 只支持 ETF 或股票追踪")
        if self.alert_policy_id == "standard_dynamic_v2" and self.asset_type == "stock":
            raise ValueError("股票追踪必须选择 leader_tactics_exit_v1")
        return self

class TrackedPositionCloseRequest(BaseModel):
    status: Literal["handled", "closed", "stopped"] = "closed"
    note: str | None = None
    expected_exit_state_version: int | None = Field(default=None, ge=0)


class TrackedPositionSnapshot(BaseModel):
    current_price: float | None = None
    current_price_date: date | None = None
    estimated_value: float | None = None
    estimated_pnl: float | None = None
    estimated_pnl_pct: float | None = None
    current_label: str | None = None
    advisor_label: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    explanation: str | None = None
    data_reliability: str = "unavailable"
    price_source: str = "unavailable"
    decision_eligible: bool = False
    display_only_reason: str | None = None


class TrackedPositionExitSignal(BaseModel):
    alert_type: str | None = None
    label: str = "暂无卖出/减仓提醒"
    level: Literal["none", "watch", "warning", "urgent"] = "none"
    action_class: Literal["none", "actionable_exit", "soft_watch", "guard_only", "data_waiting", "research_only"] = "none"
    guard_state: str | None = None
    guard_reasons: list[str] = Field(default_factory=list)
    threshold_context: dict[str, Any] = Field(default_factory=dict)
    approved_for_live: bool = False
    no_alert_reason: str | None = None
    reason: str | None = None
    reasons: list[str] = Field(default_factory=list)
    email_eligible: bool = False
    email_eligibility_reason: str | None = None
    data_reliability: str | None = None
    position_action: str | None = None
    action_version: str | None = None
    reentry_rule_version: str | None = None


class TrackedPositionAlertOut(BaseModel):
    id: int
    tracked_position_id: int
    alert_date: date
    alert_type: str
    trigger_label: str
    current_price: float | None
    current_price_date: date | None
    estimated_value: float | None
    estimated_pnl: float | None
    estimated_pnl_pct: float | None
    reasons: list[str]
    risk_flags: list[str]
    advisor_summary: str | None
    alert_level: str | None = None
    quote_time: datetime | None = None
    alert_source: str | None = None
    suppression_status: str | None = None
    email_status: str
    email_error_message: str | None
    sent_at: datetime | None
    created_at: datetime
    threshold_context: dict[str, Any] = Field(default_factory=dict)


class TrackedPositionAuditCorrelationOut(BaseModel):
    event_id: str | None = None
    event_schema_version: str | None = None
    position_episode_id: str | None = None
    exposure_version: int | None = None
    action_cycle_id: str | None = None
    alert_episode_id: str | None = None
    action_decision_id: int | None = None
    notification_item_id: int | None = None
    notification_envelope_id: int | None = None
    policy_version: str | None = None
    input_snapshot_hash: str | None = None
    from_state: str | None = None
    to_state: str | None = None
    actor_id: int | None = None
    request_id: str | None = None
    causation_id: str | None = None
    occurred_at: datetime | None = None
    recorded_at: datetime
    execution_provenance: str | None = None


class TrackedPositionAuditDeliveryOut(BaseModel):
    item_status: str | None = None
    suppression_reason: str | None = None
    repeat_slot: str | None = None
    envelope_status: str | None = None
    message_id: str | None = None
    attempt_count: int | None = None
    first_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    smtp_accepted_at: datetime | None = None


class TrackedPositionAlertAuditOut(BaseModel):
    id: int
    tracked_position_id: int
    tracked_position_alert_id: int | None = None
    outcome: str
    signal_type: str | None = None
    alert_date: date
    alert_type: str
    trigger_label: str | None = None
    data_source: str
    quote_freshness: str
    threshold_context: dict[str, Any] = Field(default_factory=dict)
    decision_context: dict[str, Any] = Field(default_factory=dict)
    recipient: str | None = None
    duplicate_reason: str | None = None
    cooldown_reason: str | None = None
    smtp_result: str | None = None
    smtp_error_message: str | None = None
    quote_time: datetime | None = None
    created_at: datetime
    audit_summary: str
    correlation: TrackedPositionAuditCorrelationOut | None = None
    delivery: TrackedPositionAuditDeliveryOut | None = None


class TrackedPositionAlertAuditListOut(BaseModel):
    items: list[TrackedPositionAlertAuditOut]
    total: int
    next_cursor: str | None = None


class TrackedPositionChartPoint(BaseModel):
    date: date
    price: float
    estimated_value: float | None = None
    estimated_pnl_pct: float | None = None
    is_entry: bool = False
    is_high: bool = False
    is_current: bool = False
    trailing_stop_pnl_pct: float | None = None


class TrackedPositionActionSummaryOut(BaseModel):
    id: int
    status: str
    is_current: bool
    policy_version: str
    data_state: str
    target_remaining_fraction: float
    target_normalized_quantity: float
    target_account_weight: float | None = None
    baseline_normalized_quantity: float
    cumulative_executed_quantity: float
    remaining_execution_quantity: float
    contributing_rules: list[str] = Field(default_factory=list)
    execution_provenance: str
    status_reason: str | None = None
    valid_until: datetime | None = None
    acknowledged_at: datetime | None = None
    executed_at: datetime | None = None
    expired_at: datetime | None = None
    cancelled_at: datetime | None = None
    superseded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TrackedPositionLifecycleStateOut(BaseModel):
    alert_state: str
    data_state: str
    data_reason_code: str | None = None


class TrackedPositionOut(BaseModel):
    id: int
    asset_type: str
    asset_code: str
    asset_name: str
    buy_date: date
    order_time_bucket: str
    confirmed_nav_date: date | None = None
    confirmed_nav: float | None = None
    confirmed_shares: float | None = None
    buy_amount: float
    cost_basis: float | None = None
    cost_basis_source: str | None = None
    entry_price: float | None
    entry_price_date: date | None
    estimated_shares: float | None
    alert_policy_id: str = "standard_dynamic_v2"
    alert_policy_version: str = "standard_dynamic_v2"
    alert_policy_provenance: str = "default"
    source_strategy: str | None = None
    source_manifest_hash: str | None = None
    source_decision_at: datetime | None = None
    status: str
    note: str | None
    created_at: datetime
    updated_at: datetime
    current_snapshot: TrackedPositionSnapshot
    exit_signal: TrackedPositionExitSignal
    position_action: str = 'hold'
    recommended_action_label: str = '继续观察'
    current_market_value: float | None = None
    current_account_weight: float | None = None
    target_account_weight: float | None = None
    recommended_trade_amount: float | None = None
    recommended_trade_shares: float | None = None
    position_sizing_reason: str | None = None
    action_class: str | None = None
    exit_action_version: str | None = None
    reentry_state: str = "not_applicable"
    reentry_reason: str | None = None
    reentry_rule_version: str | None = None
    max_profit_pct: float | None = None
    profit_giveback_pct: float | None = None
    holding_days: int | None = None
    technical_metrics: dict[str, Any] = Field(default_factory=dict)
    intraday_snapshot: TrackedEtfIntradaySnapshotOut | None = None
    dynamic_thresholds: DynamicExitThresholdsOut | None = None
    recent_intraday_alerts: list[TrackedPositionAlertOut] = Field(default_factory=list)
    latest_alert: TrackedPositionAlertOut | None = None
    exit_state_version: int = 0
    lifecycle_state: TrackedPositionLifecycleStateOut
    current_action: TrackedPositionActionSummaryOut | None = None
    etf_liquidity_capacity: TrackedEtfLiquidityCapacityOut | None = None
    risk_control: TrackedEtfSleeveRiskOut | None = None


class TrackedPositionDetailOut(TrackedPositionOut):
    chart: list[TrackedPositionChartPoint]
    alerts: list[TrackedPositionAlertOut]
    action_history: list[TrackedPositionActionSummaryOut] = Field(default_factory=list)
    action_history_next_cursor: str | None = None


class TrackedPositionListOut(BaseModel):
    items: list[TrackedPositionOut]
    total: int
    email_configured: bool
    recipient_email: str
    owner_etf_risk: TrackedEtfSleeveRiskOut | None = None
