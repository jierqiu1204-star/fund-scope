from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedEtfSleeveDailySnapshot,
    TrackedEtfSleeveLedgerEvent,
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionActionExecution,
    User,
)
from app.services.market_data import (
    ASIA_SHANGHAI,
    etf_adjusted_daily_facts_on_or_before,
    is_etf_exchange_trading_day,
)
from app.services.risk_alerts import (
    ACTION_CLASS_GUARD_ONLY,
    ALERT_CONFIRMED_TREND_WEAKENING,
    ALERT_HARD_STOP,
    ETF_RISK_STATE_DATA_HALT,
    EtfSleevePositionEvidence,
    PositionSizingRecommendation,
    calculate_tracked_etf_sleeve_nav,
    etf_sleeve_risk_manifest,
    evaluate_etf_owner_risk_state,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash
from app.services.tracked_positions.sleeve_repository import (
    MAX_OWNER_LEDGER_READ,
    AppendSleeveDailySnapshotCommand,
    append_owner_daily_snapshot,
    latest_owner_daily_snapshot,
    latest_owner_ledger_event,
    owner_daily_snapshots,
)

MAX_OWNER_RISK_POSITIONS = 100
MAX_OWNER_RISK_CYCLE_ROWS = 500
MAX_OWNER_RISK_SNAPSHOT_HISTORY = 20
STOP_RULE_IDS = frozenset({ALERT_HARD_STOP, ALERT_CONFIRMED_TREND_WEAKENING})


@dataclass(frozen=True)
class EtfOwnerRiskContext:
    status: str
    state: str
    trade_session: date | None
    equity: float | None
    flow_adjusted_nav: float | None
    high_water_nav: float | None
    drawdown: float | None
    valuation_coverage: float
    execution_coverage: float
    unavailable_reasons: tuple[str, ...]
    contract_version: str
    contract_hash: str
    source_hash: str | None

    def as_context(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "state": self.state,
            "trade_session": (
                self.trade_session.isoformat() if self.trade_session is not None else None
            ),
            "equity": self.equity,
            "flow_adjusted_nav": self.flow_adjusted_nav,
            "high_water_nav": self.high_water_nav,
            "drawdown": self.drawdown,
            "valuation_coverage": self.valuation_coverage,
            "execution_coverage": self.execution_coverage,
            "unavailable_reasons": list(self.unavailable_reasons),
            "contract_version": self.contract_version,
            "contract_hash": self.contract_hash,
            "source_hash": self.source_hash,
        }


@dataclass(frozen=True)
class SleeveLedgerHolding:
    position_id: int
    asset_code: str
    quantity: float
    remaining_cost_basis: float
    adjustment_factor: float


@dataclass(frozen=True)
class ReconstructedSleeveLedger:
    status: str
    cash_balance: float | None
    realized_pnl: float | None
    holdings: tuple[SleeveLedgerHolding, ...]
    reconciliation_event_hash: str | None
    head_event_hash: str | None
    event_sequence_by_hash: dict[str, int]
    external_flows_by_sequence: tuple[tuple[int, float], ...]
    unavailable_reasons: tuple[str, ...]


@dataclass(frozen=True)
class MaterializedOwnerRiskResult:
    context: EtfOwnerRiskContext
    snapshot_created: bool
    state_changed: bool


def latest_completed_etf_trade_session(now: datetime) -> date:
    local = (
        now.replace(tzinfo=UTC).astimezone(ASIA_SHANGHAI)
        if now.tzinfo is None
        else now.astimezone(ASIA_SHANGHAI)
    )
    candidate = local.date()
    if local.hour < 15 or (local.hour == 15 and local.minute < 10):
        candidate -= timedelta(days=1)
    for _ in range(12):
        if is_etf_exchange_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise ValueError("latest completed ETF trade session is unavailable")


def unavailable_owner_risk_context(*reasons: str) -> EtfOwnerRiskContext:
    manifest = etf_sleeve_risk_manifest()
    normalized = tuple(dict.fromkeys(reason for reason in reasons if reason)) or (
        "sleeve_risk_evidence_unavailable",
    )
    return EtfOwnerRiskContext(
        status="unavailable",
        state=ETF_RISK_STATE_DATA_HALT,
        trade_session=None,
        equity=None,
        flow_adjusted_nav=None,
        high_water_nav=None,
        drawdown=None,
        valuation_coverage=0.0,
        execution_coverage=0.0,
        unavailable_reasons=normalized,
        contract_version=str(manifest["version"]),
        contract_hash=str(manifest["contract_hash"]),
        source_hash=None,
    )


def _context_from_snapshot(
    snapshot: TrackedEtfSleeveDailySnapshot,
) -> EtfOwnerRiskContext:
    return EtfOwnerRiskContext(
        status="ready" if snapshot.coverage_state == "eligible" else "unavailable",
        state=snapshot.risk_state,
        trade_session=snapshot.snapshot_date,
        equity=snapshot.equity,
        flow_adjusted_nav=snapshot.flow_adjusted_nav,
        high_water_nav=snapshot.high_water_nav,
        drawdown=snapshot.drawdown_pct,
        valuation_coverage=float(snapshot.valuation_coverage_ratio),
        execution_coverage=float(snapshot.ledger_coverage_ratio),
        unavailable_reasons=tuple(snapshot.reasons_json or ()),
        contract_version=snapshot.contract_version,
        contract_hash=snapshot.risk_policy_hash,
        source_hash=snapshot.snapshot_hash,
    )


async def build_owner_etf_risk_context(
    session: AsyncSession,
    user: User,
    *,
    now: datetime | None = None,
) -> EtfOwnerRiskContext:
    if user.etf_trading_capital_confirmed_at is None:
        return unavailable_owner_risk_context("capital_not_explicitly_confirmed")
    ledger_head = await latest_owner_ledger_event(session, owner_id=user.id)
    if ledger_head is None:
        return unavailable_owner_risk_context("holdings_reconciliation_missing")
    snapshot = await latest_owner_daily_snapshot(session, owner_id=user.id)
    if snapshot is None:
        return unavailable_owner_risk_context("sleeve_snapshot_missing")
    if snapshot.ledger_head_event_hash != ledger_head.event_hash:
        return unavailable_owner_risk_context("sleeve_snapshot_stale_after_ledger_change")
    if now is not None and snapshot.snapshot_date < latest_completed_etf_trade_session(now):
        return unavailable_owner_risk_context("sleeve_snapshot_stale_by_trade_session")
    return _context_from_snapshot(snapshot)


def apply_owner_risk_guard(
    sizing: PositionSizingRecommendation,
    owner_risk: EtfOwnerRiskContext | None,
) -> PositionSizingRecommendation:
    if owner_risk is None:
        return sizing
    context = owner_risk.as_context()
    if sizing.action not in {"add", "reentry_candidate"}:
        return replace(sizing, owner_risk_control=context)
    if owner_risk.state == "normal" and owner_risk.status == "ready":
        return replace(sizing, owner_risk_control=context)
    reasons = "、".join(owner_risk.unavailable_reasons) or owner_risk.state
    return PositionSizingRecommendation(
        action="no_add",
        label="暂停增加风险",
        current_market_value=sizing.current_market_value,
        current_account_weight=sizing.current_account_weight,
        target_account_weight=sizing.current_account_weight,
        reason=f"账户 ETF 风险状态为 {owner_risk.state}（{reasons}），仅暂停加仓/重新入场。",
        action_class=ACTION_CLASS_GUARD_ONLY,
        action_version=sizing.action_version,
        reentry_state=sizing.reentry_state,
        reentry_reason=sizing.reentry_reason,
        reentry_rule_version=sizing.reentry_rule_version,
        liquidity_capacity=sizing.liquidity_capacity,
        owner_risk_control=context,
    )


def _finite(value: object, *, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or (nonnegative and parsed < 0):
        return None
    return parsed


def reconstruct_sleeve_ledger(
    events: tuple[TrackedEtfSleeveLedgerEvent, ...],
) -> ReconstructedSleeveLedger:
    if not events:
        return ReconstructedSleeveLedger(
            status="unavailable",
            cash_balance=None,
            realized_pnl=None,
            holdings=(),
            reconciliation_event_hash=None,
            head_event_hash=None,
            event_sequence_by_hash={},
            external_flows_by_sequence=(),
            unavailable_reasons=("holdings_reconciliation_missing",),
        )
    reasons: list[str] = []
    prior_hash: str | None = None
    prior_sequence = events[0].sequence_no - 1
    for event in events:
        if event.sequence_no != prior_sequence + 1:
            reasons.append("sleeve_ledger_sequence_gap")
        if event.predecessor_event_hash != prior_hash and prior_hash is not None:
            reasons.append("sleeve_ledger_hash_chain_broken")
        prior_hash = event.event_hash
        prior_sequence = event.sequence_no

    reconciliation_indexes = [
        index
        for index, event in enumerate(events)
        if event.event_type in {"opening_reconciliation", "reconciliation"}
    ]
    if not reconciliation_indexes:
        reasons.append("holdings_reconciliation_missing")
        return ReconstructedSleeveLedger(
            status="unavailable",
            cash_balance=None,
            realized_pnl=None,
            holdings=(),
            reconciliation_event_hash=None,
            head_event_hash=events[-1].event_hash,
            event_sequence_by_hash={event.event_hash: event.sequence_no for event in events},
            external_flows_by_sequence=(),
            unavailable_reasons=tuple(dict.fromkeys(reasons)),
        )

    origin_index = reconciliation_indexes[-1]
    origin = events[origin_index]
    cash = _finite(origin.cash_balance_after, nonnegative=True)
    if cash is None:
        reasons.append("cash_balance_unavailable")
        cash = 0.0
    holdings: dict[int, SleeveLedgerHolding] = {}
    for raw in origin.holdings_after_json or ():
        position_id = raw.get("tracked_position_id")
        asset_code = str(raw.get("asset_code") or "").strip()
        quantity = _finite(raw.get("quantity"), nonnegative=True)
        remaining_cost = _finite(raw.get("remaining_cost_basis"), nonnegative=True)
        factor = _finite(raw.get("adjustment_factor"))
        if (
            not isinstance(position_id, int)
            or position_id <= 0
            or not asset_code
            or quantity is None
            or remaining_cost is None
            or factor is None
            or factor <= 0
        ):
            reasons.append("reconciled_holding_invalid")
            continue
        holdings[position_id] = SleeveLedgerHolding(
            position_id=position_id,
            asset_code=asset_code,
            quantity=quantity,
            remaining_cost_basis=remaining_cost,
            adjustment_factor=factor,
        )

    realized_pnl = 0.0
    external_flows: list[tuple[int, float]] = []
    for event in events[origin_index + 1 :]:
        cash_delta = _finite(event.cash_delta)
        if cash_delta is not None:
            cash += cash_delta
        if event.event_type in {"cash_deposit", "cash_withdrawal"}:
            if cash_delta is None:
                reasons.append("external_cash_flow_invalid")
            else:
                external_flows.append((event.sequence_no, cash_delta))
        elif event.event_type == "distribution":
            if cash_delta is None:
                reasons.append("distribution_cash_flow_invalid")
            else:
                realized_pnl += cash_delta
        elif event.event_type == "fee":
            if cash_delta is None:
                reasons.append("fee_cash_flow_invalid")
            else:
                realized_pnl += cash_delta
        elif event.event_type in {"buy", "sell", "quantity_adjustment"}:
            position_id = event.tracked_position_id
            quantity_after = _finite(event.quantity_after, nonnegative=True)
            quantity_delta = _finite(event.quantity_delta)
            if position_id is None or quantity_after is None or quantity_delta is None:
                reasons.append("ledger_quantity_evidence_invalid")
                continue
            prior = holdings.get(position_id)
            if event.event_type == "buy":
                if cash_delta is None or event.asset_code is None:
                    reasons.append("ledger_buy_evidence_invalid")
                    continue
                prior_cost = prior.remaining_cost_basis if prior is not None else 0.0
                holdings[position_id] = SleeveLedgerHolding(
                    position_id=position_id,
                    asset_code=event.asset_code,
                    quantity=quantity_after,
                    remaining_cost_basis=prior_cost - cash_delta,
                    adjustment_factor=float(event.adjustment_factor),
                )
            elif prior is None:
                reasons.append("ledger_position_predecessor_missing")
            elif event.event_type == "sell":
                if cash_delta is None or prior.quantity <= 0 or quantity_delta >= 0:
                    reasons.append("ledger_sell_evidence_invalid")
                    continue
                sold_quantity = -quantity_delta
                allocated_cost = prior.remaining_cost_basis * min(
                    1.0,
                    sold_quantity / prior.quantity,
                )
                realized_pnl += cash_delta - allocated_cost
                if quantity_after <= 1e-8:
                    holdings.pop(position_id, None)
                else:
                    holdings[position_id] = SleeveLedgerHolding(
                        position_id=position_id,
                        asset_code=prior.asset_code,
                        quantity=quantity_after,
                        remaining_cost_basis=max(
                            0.0,
                            prior.remaining_cost_basis - allocated_cost,
                        ),
                        adjustment_factor=float(event.adjustment_factor),
                    )
            else:
                holdings[position_id] = SleeveLedgerHolding(
                    position_id=position_id,
                    asset_code=prior.asset_code,
                    quantity=quantity_after,
                    remaining_cost_basis=prior.remaining_cost_basis,
                    adjustment_factor=float(event.adjustment_factor),
                )
    if cash < -0.01:
        reasons.append("sleeve_cash_balance_negative")
    if not math.isfinite(realized_pnl):
        reasons.append("realized_pnl_non_finite")
    return ReconstructedSleeveLedger(
        status="ready" if not reasons else "unavailable",
        cash_balance=round(cash, 2) if not reasons else None,
        realized_pnl=round(realized_pnl, 2) if not reasons else None,
        holdings=tuple(sorted(holdings.values(), key=lambda item: item.position_id)),
        reconciliation_event_hash=origin.event_hash,
        head_event_hash=events[-1].event_hash,
        event_sequence_by_hash={event.event_hash: event.sequence_no for event in events},
        external_flows_by_sequence=tuple(external_flows),
        unavailable_reasons=tuple(dict.fromkeys(reasons)),
    )


async def _ledger_events_from_latest_reconciliation(
    session: AsyncSession,
    *,
    owner_id: int,
) -> tuple[tuple[TrackedEtfSleeveLedgerEvent, ...], tuple[str, ...]]:
    origin = await session.scalar(
        select(TrackedEtfSleeveLedgerEvent)
        .where(
            TrackedEtfSleeveLedgerEvent.user_id == owner_id,
            TrackedEtfSleeveLedgerEvent.event_type.in_(
                ("opening_reconciliation", "reconciliation")
            ),
        )
        .order_by(TrackedEtfSleeveLedgerEvent.sequence_no.desc())
        .limit(1)
    )
    if origin is None:
        return (), ("holdings_reconciliation_missing",)
    rows = tuple(
        (
            await session.scalars(
                select(TrackedEtfSleeveLedgerEvent)
                .where(
                    TrackedEtfSleeveLedgerEvent.user_id == owner_id,
                    TrackedEtfSleeveLedgerEvent.sequence_no >= origin.sequence_no,
                )
                .order_by(TrackedEtfSleeveLedgerEvent.sequence_no.asc())
                .limit(MAX_OWNER_LEDGER_READ + 1)
            )
        ).all()
    )
    if len(rows) > MAX_OWNER_LEDGER_READ:
        return rows[:MAX_OWNER_LEDGER_READ], ("sleeve_ledger_read_bound_reached",)
    return rows, ()


async def _active_owner_etf_positions(
    session: AsyncSession,
    *,
    owner_id: int,
) -> tuple[tuple[TrackedPosition, ...], tuple[str, ...]]:
    rows = tuple(
        (
            await session.scalars(
                select(TrackedPosition)
                .where(
                    TrackedPosition.user_id == owner_id,
                    TrackedPosition.asset_type == "etf",
                    TrackedPosition.status == "active",
                )
                .order_by(TrackedPosition.id.asc())
                .limit(MAX_OWNER_RISK_POSITIONS + 1)
            )
        ).all()
    )
    if len(rows) > MAX_OWNER_RISK_POSITIONS:
        return rows[:MAX_OWNER_RISK_POSITIONS], ("owner_position_bound_reached",)
    return rows, ()


async def _stop_cycle_counts(
    session: AsyncSession,
    *,
    owner_id: int,
    trade_session: date,
) -> tuple[int, int, tuple[str, ...]]:
    since = trade_session - timedelta(days=60)
    rows = (
        await session.execute(
            select(TrackedPositionActionDecision, TrackedPositionActionExecution.id)
            .join(
                TrackedPosition,
                TrackedPosition.id == TrackedPositionActionDecision.tracked_position_id,
            )
            .outerjoin(
                TrackedPositionActionExecution,
                (
                    TrackedPositionActionExecution.action_decision_id
                    == TrackedPositionActionDecision.id
                )
                & (
                    TrackedPositionActionExecution.execution_provenance
                    == "owner_confirmed"
                ),
            )
            .where(
                TrackedPositionActionDecision.user_id == owner_id,
                TrackedPosition.asset_type == "etf",
                TrackedPositionActionDecision.data_state == "eligible",
                TrackedPositionActionDecision.created_at >= datetime.combine(
                    since,
                    datetime.min.time(),
                ),
            )
            .order_by(
                TrackedPositionActionDecision.created_at.desc(),
                TrackedPositionActionDecision.id.desc(),
            )
            .limit(MAX_OWNER_RISK_CYCLE_ROWS + 1)
        )
    ).all()
    bounded = rows[:MAX_OWNER_RISK_CYCLE_ROWS]
    signal_cycles: set[tuple[str, str]] = set()
    confirmed_cycles: set[tuple[str, str]] = set()
    for action, execution_id in bounded:
        if not STOP_RULE_IDS.intersection(action.contributing_rules_json or ()):
            continue
        identity = (action.position_episode_id, action.action_cycle_id)
        signal_cycles.add(identity)
        if execution_id is not None:
            confirmed_cycles.add(identity)
    reasons = ("stop_cycle_query_bound_reached",) if len(rows) > MAX_OWNER_RISK_CYCLE_ROWS else ()
    return len(signal_cycles), len(confirmed_cycles), reasons


def _snapshot_input_hash(
    *,
    owner_id: int,
    trade_session: date,
    ledger_head_hash: str | None,
    active_positions: tuple[TrackedPosition, ...],
    marks: dict[str, dict[str, Any]],
    signal_stop_cycles: int,
    confirmed_stop_cycles: int,
    reasons: tuple[str, ...],
) -> str:
    return stable_contract_hash(
        {
            "contract": "tracked_etf_sleeve_snapshot_input_v1",
            "owner_id": owner_id,
            "trade_session": trade_session,
            "ledger_head_hash": ledger_head_hash,
            "active_positions": [
                {
                    "position_id": position.id,
                    "asset_code": position.asset_code,
                    "confirmed_shares": position.confirmed_shares,
                    "status": position.status,
                }
                for position in active_positions
            ],
            "marks": marks,
            "signal_stop_cycles": signal_stop_cycles,
            "confirmed_stop_cycles": confirmed_stop_cycles,
            "reasons": list(reasons),
        }
    )


async def materialize_owner_etf_risk_snapshot(
    session: AsyncSession,
    user: User,
    *,
    trade_session: date,
    cutoff_at: datetime,
) -> MaterializedOwnerRiskResult:
    manifest = etf_sleeve_risk_manifest()
    latest_snapshot = await latest_owner_daily_snapshot(session, owner_id=user.id)
    current_ledger_head = await latest_owner_ledger_event(session, owner_id=user.id)
    current_ledger_head_hash = (
        current_ledger_head.event_hash if current_ledger_head is not None else None
    )
    events, ledger_read_reasons = await _ledger_events_from_latest_reconciliation(
        session,
        owner_id=user.id,
    )
    reconstructed = reconstruct_sleeve_ledger(events)
    active_positions, position_bound_reasons = await _active_owner_etf_positions(
        session,
        owner_id=user.id,
    )
    signal_cycles, confirmed_cycles, cycle_reasons = await _stop_cycle_counts(
        session,
        owner_id=user.id,
        trade_session=trade_session,
    )

    ledger_by_id = {holding.position_id: holding for holding in reconstructed.holdings}
    active_by_id = {position.id: position for position in active_positions}
    reconciliation_reasons: list[str] = []
    if set(ledger_by_id) != set(active_by_id):
        reconciliation_reasons.append("active_holdings_reconciliation_mismatch")
    for position_id, position in active_by_id.items():
        holding = ledger_by_id.get(position_id)
        confirmed_shares = _finite(position.confirmed_shares, nonnegative=True)
        if holding is None or confirmed_shares is None:
            reconciliation_reasons.append(f"position_execution_incomplete:{position_id}")
            continue
        tolerance = max(1e-6, holding.quantity * 1e-8)
        if not math.isclose(
            holding.quantity,
            confirmed_shares,
            rel_tol=0.0,
            abs_tol=tolerance,
        ):
            reconciliation_reasons.append(f"position_quantity_mismatch:{position_id}")

    codes = tuple(sorted({holding.asset_code for holding in reconstructed.holdings}))
    facts = await etf_adjusted_daily_facts_on_or_before(
        session,
        etf_codes=codes,
        replay_date=trade_session,
        rows_per_code=1,
        max_source_rows=MAX_OWNER_RISK_POSITIONS,
        decision_cutoff=cutoff_at,
    )
    facts_by_code = {fact.etf_code: fact for fact in facts}
    marks: dict[str, dict[str, Any]] = {}
    position_evidence: list[EtfSleevePositionEvidence] = []
    for holding in reconstructed.holdings:
        fact = facts_by_code.get(holding.asset_code)
        raw_close = _finite(fact.raw_close if fact is not None else None)
        eligible = bool(
            fact is not None
            and fact.trade_date == trade_session
            and fact.decision_eligible is True
            and fact.research_price_basis == "total_return_adjusted"
            and raw_close is not None
            and raw_close > 0
        )
        marks[holding.asset_code] = {
            "trade_date": fact.trade_date if fact is not None else None,
            "raw_close": raw_close,
            "revision_hash": fact.revision_hash if fact is not None else None,
            "data_provider": fact.data_provider if fact is not None else None,
            "provider_version": fact.provider_version if fact is not None else None,
            "decision_eligible": eligible,
        }
        position_evidence.append(
            EtfSleevePositionEvidence(
                position_id=holding.position_id,
                remaining_cost_basis=holding.remaining_cost_basis,
                market_value=(
                    holding.quantity * raw_close if eligible and raw_close is not None else None
                ),
                quantity=holding.quantity,
                decision_eligible=eligible,
                execution_complete=holding.position_id in active_by_id,
            )
        )

    nav = calculate_tracked_etf_sleeve_nav(
        configured_capital=user.etf_trading_capital,
        capital_confirmed=user.etf_trading_capital_confirmed_at is not None,
        opening_reconciled=reconstructed.reconciliation_event_hash is not None,
        cash_balance=reconstructed.cash_balance,
        realized_pnl=reconstructed.realized_pnl,
        positions=position_evidence,
    )
    external_reasons = tuple(
        dict.fromkeys(
            (
                *ledger_read_reasons,
                *reconstructed.unavailable_reasons,
                *position_bound_reasons,
                *cycle_reasons,
                *reconciliation_reasons,
            )
        )
    )
    all_unavailable_reasons = tuple(
        dict.fromkeys((*external_reasons, *nav.unavailable_reasons))
    )
    input_hash = _snapshot_input_hash(
        owner_id=user.id,
        trade_session=trade_session,
        ledger_head_hash=current_ledger_head_hash,
        active_positions=active_positions,
        marks=marks,
        signal_stop_cycles=signal_cycles,
        confirmed_stop_cycles=confirmed_cycles,
        reasons=all_unavailable_reasons,
    )
    snapshot_history = await owner_daily_snapshots(
        session,
        owner_id=user.id,
        limit=MAX_OWNER_RISK_SNAPSHOT_HISTORY,
    )
    predecessor_hash = latest_snapshot.snapshot_hash if latest_snapshot is not None else None
    idempotency_key = f"sleeve-snapshot:{input_hash}"
    if nav.status != "ready" or external_reasons:
        persisted = await append_owner_daily_snapshot(
            session,
            AppendSleeveDailySnapshotCommand(
                owner_id=user.id,
                idempotency_key=idempotency_key,
                snapshot_date=trade_session,
                cutoff_at=cutoff_at,
                coverage_state="unavailable",
                risk_state=ETF_RISK_STATE_DATA_HALT,
                expected_predecessor_snapshot_hash=predecessor_hash,
                ledger_head_event_hash=current_ledger_head_hash,
                input_contract_hash=input_hash,
                nav_contract_hash=nav.contract_hash,
                risk_policy_hash=str(manifest["contract_hash"]),
                holding_count=len(position_evidence),
                valued_holding_count=round(nav.valuation_coverage * len(position_evidence)),
                ledger_coverage_ratio=nav.execution_coverage,
                valuation_coverage_ratio=nav.valuation_coverage,
                signal_stop_cycle_count=signal_cycles,
                confirmed_stop_cycle_count=confirmed_cycles,
                reasons=all_unavailable_reasons or ("sleeve_nav_unavailable",),
            ),
        )
        return MaterializedOwnerRiskResult(
            context=_context_from_snapshot(persisted.snapshot),
            snapshot_created=persisted.created,
            state_changed=(latest_snapshot is None or latest_snapshot.risk_state != "data_halt"),
        )

    assert nav.equity is not None
    event_sequence = reconstructed.event_sequence_by_hash
    prior_financial_snapshot = next(
        (
            snapshot
            for snapshot in snapshot_history
            if snapshot.snapshot_date < trade_session
            and snapshot.coverage_state == "eligible"
            and snapshot.equity is not None
            and snapshot.flow_adjusted_nav is not None
            and snapshot.ledger_head_event_hash in event_sequence
        ),
        None,
    )
    flow_adjusted_nav = nav.equity
    high_water_nav = nav.equity
    drawdown: float | None = None
    net_external_flow = 0.0
    if prior_financial_snapshot is not None:
        prior_sequence = event_sequence[str(prior_financial_snapshot.ledger_head_event_hash)]
        net_external_flow = sum(
            amount
            for sequence, amount in reconstructed.external_flows_by_sequence
            if sequence > prior_sequence
        )
        adjusted_equity = nav.equity - net_external_flow
        if adjusted_equity <= 0 or prior_financial_snapshot.equity is None:
            all_unavailable_reasons = ("flow_adjusted_nav_invalid",)
        else:
            flow_adjusted_nav = float(prior_financial_snapshot.flow_adjusted_nav) * (
                adjusted_equity / float(prior_financial_snapshot.equity)
            )
            high_water_nav = max(
                float(prior_financial_snapshot.high_water_nav or flow_adjusted_nav),
                flow_adjusted_nav,
            )
            drawdown = flow_adjusted_nav / high_water_nav - 1.0

    if all_unavailable_reasons:
        persisted = await append_owner_daily_snapshot(
            session,
            AppendSleeveDailySnapshotCommand(
                owner_id=user.id,
                idempotency_key=idempotency_key,
                snapshot_date=trade_session,
                cutoff_at=cutoff_at,
                coverage_state="unavailable",
                risk_state=ETF_RISK_STATE_DATA_HALT,
                expected_predecessor_snapshot_hash=predecessor_hash,
                ledger_head_event_hash=current_ledger_head_hash,
                input_contract_hash=input_hash,
                nav_contract_hash=nav.contract_hash,
                risk_policy_hash=str(manifest["contract_hash"]),
                holding_count=len(position_evidence),
                valued_holding_count=len(position_evidence),
                ledger_coverage_ratio=1.0,
                valuation_coverage_ratio=1.0,
                signal_stop_cycle_count=signal_cycles,
                confirmed_stop_cycle_count=confirmed_cycles,
                reasons=all_unavailable_reasons,
            ),
        )
        return MaterializedOwnerRiskResult(
            context=_context_from_snapshot(persisted.snapshot),
            snapshot_created=persisted.created,
            state_changed=(latest_snapshot is None or latest_snapshot.risk_state != "data_halt"),
        )

    recovery_sessions = tuple(
        reversed(
            [
                snapshot.snapshot_date
                for snapshot in snapshot_history
                if snapshot.snapshot_date < trade_session
                and "risk_recovery_pending" in (snapshot.reasons_json or ())
            ][:2]
        )
    )
    decision = evaluate_etf_owner_risk_state(
        trade_session=trade_session,
        nav_status=nav.status,
        sleeve_drawdown=drawdown,
        distinct_stop_signal_cycles=signal_cycles,
        confirmed_stop_execution_cycles=confirmed_cycles,
        previous_state=(latest_snapshot.risk_state if latest_snapshot is not None else "normal"),
        previous_evaluated_session=(
            latest_snapshot.snapshot_date if latest_snapshot is not None else None
        ),
        previous_recovery_sessions=recovery_sessions,
        cooldown_sessions_remaining=(
            latest_snapshot.cooldown_sessions_remaining if latest_snapshot is not None else 0
        ),
        previous_signal_stop_cycles=(
            latest_snapshot.signal_stop_cycle_count if latest_snapshot is not None else 0
        ),
        previous_confirmed_stop_cycles=(
            latest_snapshot.confirmed_stop_cycle_count if latest_snapshot is not None else 0
        ),
    )
    persisted = await append_owner_daily_snapshot(
        session,
        AppendSleeveDailySnapshotCommand(
            owner_id=user.id,
            idempotency_key=idempotency_key,
            snapshot_date=trade_session,
            cutoff_at=cutoff_at,
            coverage_state="eligible",
            risk_state=decision.state,
            expected_predecessor_snapshot_hash=predecessor_hash,
            ledger_head_event_hash=current_ledger_head_hash,
            input_contract_hash=input_hash,
            nav_contract_hash=nav.contract_hash,
            risk_policy_hash=decision.contract_hash,
            cash_balance=nav.cash_balance,
            market_value=nav.market_value,
            equity=nav.equity,
            net_external_flow=net_external_flow,
            flow_adjusted_nav=flow_adjusted_nav,
            high_water_nav=high_water_nav,
            drawdown_pct=drawdown,
            holding_count=len(position_evidence),
            valued_holding_count=len(position_evidence),
            ledger_coverage_ratio=1.0,
            valuation_coverage_ratio=1.0,
            recovery_streak=len(decision.recovery_sessions),
            cooldown_sessions_remaining=decision.cooldown_sessions_remaining,
            signal_stop_cycle_count=signal_cycles,
            confirmed_stop_cycle_count=confirmed_cycles,
            reasons=decision.reason_codes,
        ),
    )
    return MaterializedOwnerRiskResult(
        context=_context_from_snapshot(persisted.snapshot),
        snapshot_created=persisted.created,
        state_changed=decision.changed,
    )


async def materialize_owner_risk_contexts(
    session: AsyncSession,
    *,
    owner_ids: tuple[int, ...],
    now: datetime,
) -> tuple[dict[int, EtfOwnerRiskContext], dict[str, int]]:
    bounded_owner_ids = tuple(sorted(set(owner_ids)))[:MAX_OWNER_RISK_POSITIONS]
    users = {
        user.id: user
        for user in (
            await session.scalars(select(User).where(User.id.in_(bounded_owner_ids)))
        ).all()
    }
    trade_session = latest_completed_etf_trade_session(now)
    contexts: dict[int, EtfOwnerRiskContext] = {}
    counters = {
        "owner_risk_owners": len(bounded_owner_ids),
        "owner_risk_ready": 0,
        "owner_risk_data_halt": 0,
        "owner_risk_state_transitions": 0,
        "owner_risk_failures": 0,
        "owner_risk_owner_bound_reached": int(len(set(owner_ids)) > len(bounded_owner_ids)),
    }
    for owner_id in bounded_owner_ids:
        user = users.get(owner_id)
        if user is None:
            counters["owner_risk_failures"] += 1
            continue
        try:
            async with session.begin_nested():
                result = await materialize_owner_etf_risk_snapshot(
                    session,
                    user,
                    trade_session=trade_session,
                    cutoff_at=now,
                )
            contexts[owner_id] = result.context
            if result.context.status == "ready":
                counters["owner_risk_ready"] += 1
            if result.context.state == "data_halt":
                counters["owner_risk_data_halt"] += 1
            if result.state_changed:
                counters["owner_risk_state_transitions"] += 1
        except Exception:  # noqa: BLE001 - isolate one owner's evidence failure
            contexts[owner_id] = unavailable_owner_risk_context(
                "owner_risk_materialization_failed"
            )
            counters["owner_risk_failures"] += 1
    return contexts, counters


async def owner_risk_contexts_for_read(
    session: AsyncSession,
    *,
    users: tuple[User, ...],
    now: datetime | None = None,
) -> dict[int, EtfOwnerRiskContext]:
    bounded_users = users[:MAX_OWNER_RISK_POSITIONS]
    owner_ids = tuple(user.id for user in bounded_users)
    if not owner_ids:
        return {}
    ledger_heads = (
        select(
            TrackedEtfSleeveLedgerEvent.user_id.label("owner_id"),
            func.max(TrackedEtfSleeveLedgerEvent.sequence_no).label("max_sequence"),
        )
        .where(TrackedEtfSleeveLedgerEvent.user_id.in_(owner_ids))
        .group_by(TrackedEtfSleeveLedgerEvent.user_id)
        .subquery()
    )
    ledgers = {
        row.user_id: row
        for row in (
            await session.scalars(
                select(TrackedEtfSleeveLedgerEvent).join(
                    ledger_heads,
                    (TrackedEtfSleeveLedgerEvent.user_id == ledger_heads.c.owner_id)
                    & (
                        TrackedEtfSleeveLedgerEvent.sequence_no
                        == ledger_heads.c.max_sequence
                    ),
                )
            )
        ).all()
    }
    snapshot_heads = (
        select(
            TrackedEtfSleeveDailySnapshot.user_id.label("owner_id"),
            func.max(TrackedEtfSleeveDailySnapshot.sequence_no).label("max_sequence"),
        )
        .where(TrackedEtfSleeveDailySnapshot.user_id.in_(owner_ids))
        .group_by(TrackedEtfSleeveDailySnapshot.user_id)
        .subquery()
    )
    snapshots = {
        row.user_id: row
        for row in (
            await session.scalars(
                select(TrackedEtfSleeveDailySnapshot).join(
                    snapshot_heads,
                    (TrackedEtfSleeveDailySnapshot.user_id == snapshot_heads.c.owner_id)
                    & (
                        TrackedEtfSleeveDailySnapshot.sequence_no
                        == snapshot_heads.c.max_sequence
                    ),
                )
            )
        ).all()
    }
    contexts: dict[int, EtfOwnerRiskContext] = {}
    for user in bounded_users:
        if user.etf_trading_capital_confirmed_at is None:
            contexts[user.id] = unavailable_owner_risk_context(
                "capital_not_explicitly_confirmed"
            )
            continue
        ledger = ledgers.get(user.id)
        snapshot = snapshots.get(user.id)
        if ledger is None:
            contexts[user.id] = unavailable_owner_risk_context(
                "holdings_reconciliation_missing"
            )
        elif snapshot is None:
            contexts[user.id] = unavailable_owner_risk_context("sleeve_snapshot_missing")
        elif snapshot.ledger_head_event_hash != ledger.event_hash:
            contexts[user.id] = unavailable_owner_risk_context(
                "sleeve_snapshot_stale_after_ledger_change"
            )
        elif (
            now is not None
            and snapshot.snapshot_date < latest_completed_etf_trade_session(now)
        ):
            contexts[user.id] = unavailable_owner_risk_context(
                "sleeve_snapshot_stale_by_trade_session"
            )
        else:
            contexts[user.id] = _context_from_snapshot(snapshot)
    return contexts
