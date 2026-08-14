"""Persisted tracked-position alert-policy selection and provenance validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    LateDayTurnaroundObservation,
    LateDayTurnaroundRun,
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    utcnow,
)
from app.services.recommendations.stock_adjusted_prices import canonical_ashare_code
from app.services.risk_alerts import (
    LEADER_TACTICS_EXIT_POLICY_ID,
    LEADER_TACTICS_EXIT_POLICY_VERSION,
    LEADER_TACTICS_EXIT_STATE_KEY,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    get_v2_materialized_manifest,
)
from app.services.strategy_lab.late_day_turnaround_shadow import (
    CONTRACT_HASH as LATE_DAY_CONTRACT_HASH,
)
from app.services.tracked_positions.late_day_t1_policy import (
    POLICY_ID as LATE_DAY_POLICY_ID,
)
from app.services.tracked_positions.late_day_t1_policy import (
    POLICY_STATE_KEY,
)
from app.services.tracked_positions.late_day_t1_policy import (
    POLICY_VERSION as LATE_DAY_POLICY_VERSION,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash

DEFAULT_POLICY_ID = "standard_dynamic_v2"
DEFAULT_POLICY_VERSION = "standard_dynamic_v2"
LATE_DAY_SOURCE_STRATEGY = "late_day_turnaround"
LEADER_SOURCE_STRATEGY = "leader_tactics_v2"
SUPPORTED_ALERT_POLICY_IDS = frozenset(
    {DEFAULT_POLICY_ID, LATE_DAY_POLICY_ID, LEADER_TACTICS_EXIT_POLICY_ID}
)

_RESET_STATE_KEYS = frozenset(
    {
        "max_profit_pct",
        "max_profit_recorded_at",
        "profit_giveback_pct",
        "holding_days",
        "dynamic_thresholds",
        "etf_profit_protection_v2",
        "action_class",
        "guard_state",
        "guard_reasons",
        "no_alert_reason",
        "exit_signal_threshold_context",
        "ma5_close_break_state",
        "latest_price_source",
        "latest_quote_time",
        POLICY_STATE_KEY,
        LEADER_TACTICS_EXIT_STATE_KEY,
    }
)


@dataclass(frozen=True)
class AlertPolicySelection:
    policy_id: str
    policy_version: str
    provenance: str
    source_strategy: str | None
    source_manifest_hash: str | None
    source_decision_at: datetime | None
    source_context: dict[str, Any] | None = None


def _database_local(value: datetime) -> datetime:
    from app.services.intraday_etf.exchange_calendar import ASIA_SHANGHAI

    if value.tzinfo is None:
        return value
    return value.astimezone(ASIA_SHANGHAI).replace(tzinfo=None)


async def resolve_alert_policy_selection(
    session: AsyncSession,
    *,
    asset_type: str,
    asset_code: str,
    policy_id: str | None,
    source_manifest_hash: str | None = None,
    source_decision_at: datetime | None = None,
) -> AlertPolicySelection:
    selected = policy_id or DEFAULT_POLICY_ID
    if selected not in SUPPORTED_ALERT_POLICY_IDS:
        raise ValueError("不支持的邮件提醒规则")
    if selected == DEFAULT_POLICY_ID:
        if asset_type == "stock":
            raise ValueError("股票追踪只支持 leader_tactics_exit_v1")
        if source_manifest_hash is not None or source_decision_at is not None:
            raise ValueError("默认邮件规则不能附加尾盘候选来源")
        return AlertPolicySelection(
            policy_id=DEFAULT_POLICY_ID,
            policy_version=DEFAULT_POLICY_VERSION,
            provenance="default",
            source_strategy=None,
            source_manifest_hash=None,
            source_decision_at=None,
        )
    if selected == LEADER_TACTICS_EXIT_POLICY_ID:
        if asset_type not in {"etf", "stock"}:
            raise ValueError("leader_tactics_exit_v1 只支持 ETF 或股票追踪")
        if source_manifest_hash is None and source_decision_at is None:
            return AlertPolicySelection(
                policy_id=LEADER_TACTICS_EXIT_POLICY_ID,
                policy_version=LEADER_TACTICS_EXIT_POLICY_VERSION,
                provenance="manual_selection",
                source_strategy=None,
                source_manifest_hash=None,
                source_decision_at=None,
                source_context=None,
            )
        if source_manifest_hash is None or source_decision_at is None:
            raise ValueError("龙头候选 manifest 与决策时间必须同时提供")
        source_local = _database_local(source_decision_at)
        universe = "ashare" if asset_type == "stock" else "etf"
        lookup_asset_code = (
            canonical_ashare_code(asset_code)
            if asset_type == "stock"
            else asset_code.strip()
        )
        manifest = await get_v2_materialized_manifest(
            session,
            universe=universe,
            as_of=source_local,
        )
        if manifest is None or str(manifest.get("manifest_hash")) != source_manifest_hash:
            raise ValueError("龙头候选来源 manifest 不存在、未物化或不满足 PIT 截止")
        row = (
            (
                await session.execute(
                    text(
                        """
                        WITH visible_transitions AS (
                            SELECT asset_code, formula_id, signal_date, to_state,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY asset_code, formula_id, signal_date
                                       ORDER BY transition_date DESC, id DESC
                                   ) AS transition_rank
                            FROM leader_tactics_v2_state_transitions
                            WHERE manifest_hash = :manifest_hash
                              AND universe = :universe
                              AND transition_date <= :transition_date
                        )
                        SELECT o.formula_id, o.signal_date, o.asset_name,
                               o.gate_facts_json,
                               COALESCE(t.to_state, o.state) AS effective_state
                        FROM leader_tactics_v2_candidate_observations o
                        LEFT JOIN visible_transitions t
                          ON t.asset_code = o.asset_code
                         AND t.formula_id = o.formula_id
                         AND t.signal_date = o.signal_date
                         AND t.transition_rank = 1
                        WHERE o.manifest_hash = :manifest_hash
                          AND o.universe = :universe
                          AND o.asset_code = :asset_code
                          AND o.availability = 'available'
                          AND o.qualifies = :qualifies
                          AND o.source_cutoff <= :source_cutoff
                        ORDER BY o.signal_date DESC, o.id DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "manifest_hash": source_manifest_hash,
                        "universe": universe,
                        "asset_code": lookup_asset_code,
                        "qualifies": True,
                        "source_cutoff": source_local,
                        "transition_date": source_local.date(),
                    },
                )
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or str(row["effective_state"]) != "confirmed"
            or not str(row["asset_name"] or "").strip()
        ):
            raise ValueError("龙头候选来源不是截至决策时间仍有效的 confirmed 候选")
        try:
            gate_facts = json.loads(str(row["gate_facts_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("龙头候选来源 gate facts 无法验证") from exc
        if not isinstance(gate_facts, dict):
            raise ValueError("龙头候选来源 gate facts 无效")
        return AlertPolicySelection(
            policy_id=LEADER_TACTICS_EXIT_POLICY_ID,
            policy_version=LEADER_TACTICS_EXIT_POLICY_VERSION,
            provenance="candidate_backed",
            source_strategy=LEADER_SOURCE_STRATEGY,
            source_manifest_hash=source_manifest_hash,
            source_decision_at=source_local,
            source_context={
                "formula_id": str(row["formula_id"]),
                "signal_date": str(row["signal_date"]),
                "candidate_asset_name": str(row["asset_name"]).strip(),
                "signal_adjusted_low": gate_facts.get("adjusted_low"),
                "signal_adjusted_atr20": gate_facts.get("adjusted_atr20"),
                "universe": universe,
            },
        )
    if asset_type != "etf":
        raise ValueError("late_day_turnaround_t1_v1 只支持 ETF 追踪")
    if source_manifest_hash is None and source_decision_at is None:
        return AlertPolicySelection(
            policy_id=LATE_DAY_POLICY_ID,
            policy_version=LATE_DAY_POLICY_VERSION,
            provenance="manual_selection",
            source_strategy=LATE_DAY_SOURCE_STRATEGY,
            source_manifest_hash=None,
            source_decision_at=None,
        )
    if source_manifest_hash is None or source_decision_at is None:
        raise ValueError("尾盘候选 manifest 与决策时间必须同时提供")
    source_local = _database_local(source_decision_at)
    matched = await session.scalar(
        select(LateDayTurnaroundObservation.id)
        .join(LateDayTurnaroundRun, LateDayTurnaroundRun.id == LateDayTurnaroundObservation.run_id)
        .where(
            LateDayTurnaroundRun.manifest_hash == source_manifest_hash,
            LateDayTurnaroundRun.universe == "etf",
            LateDayTurnaroundRun.status == "complete",
            LateDayTurnaroundRun.contract_hash == LATE_DAY_CONTRACT_HASH,
            LateDayTurnaroundRun.decision_at == source_local,
            LateDayTurnaroundObservation.asset_code == asset_code,
            LateDayTurnaroundObservation.observation_kind == "formal_candidate",
            LateDayTurnaroundObservation.available.is_(True),
            LateDayTurnaroundObservation.reason == "ok",
            LateDayTurnaroundObservation.decision_at == source_local,
        )
        .limit(1)
    )
    if matched is None:
        raise ValueError("尾盘候选来源与当前 ETF、决策时间或完整 manifest 不匹配")
    return AlertPolicySelection(
        policy_id=LATE_DAY_POLICY_ID,
        policy_version=LATE_DAY_POLICY_VERSION,
        provenance="candidate_backed",
        source_strategy=LATE_DAY_SOURCE_STRATEGY,
        source_manifest_hash=source_manifest_hash,
        source_decision_at=source_local,
    )


async def apply_alert_policy_selection(
    session: AsyncSession,
    *,
    position: TrackedPosition,
    owner_id: int,
    policy_id: str,
    source_manifest_hash: str | None,
    source_decision_at: datetime | None,
) -> bool:
    if position.user_id != owner_id:
        raise LookupError("tracked position not found for owner")
    selection = await resolve_alert_policy_selection(
        session,
        asset_type=position.asset_type,
        asset_code=position.asset_code,
        policy_id=policy_id,
        source_manifest_hash=source_manifest_hash,
        source_decision_at=source_decision_at,
    )
    before = (
        position.alert_policy_id,
        position.alert_policy_version,
        position.alert_policy_provenance,
        position.source_strategy,
        position.source_manifest_hash,
        position.source_decision_at,
    )
    after = (
        selection.policy_id,
        selection.policy_version,
        selection.provenance,
        selection.source_strategy,
        selection.source_manifest_hash,
        selection.source_decision_at,
    )
    if before == after:
        return False

    now = utcnow()
    state = {
        key: value
        for key, value in dict(position.exit_state_json or {}).items()
        if key not in _RESET_STATE_KEYS
    }
    state["alert_rule_states"] = {}
    state["current_action_id"] = None
    state["open_action_cycle_id"] = None
    state["alert_policy_state"] = {
        "policy_id": selection.policy_id,
        "policy_version": selection.policy_version,
        "changed_at": now.isoformat(),
    }
    if selection.source_context is not None:
        state[LEADER_TACTICS_EXIT_STATE_KEY] = {
            "source_context": dict(selection.source_context),
        }
    current_action = await session.scalar(
        select(TrackedPositionActionDecision).where(
            TrackedPositionActionDecision.tracked_position_id == position.id,
            TrackedPositionActionDecision.is_current.is_(True),
        )
    )
    if current_action is not None:
        current_action.status = "superseded"
        current_action.status_reason = "alert_policy_changed"
        current_action.is_current = False
        current_action.superseded_at = now

    position.alert_policy_id = selection.policy_id
    position.alert_policy_version = selection.policy_version
    position.alert_policy_provenance = selection.provenance
    position.source_strategy = selection.source_strategy
    position.source_manifest_hash = selection.source_manifest_hash
    position.source_decision_at = selection.source_decision_at
    position.exit_state_json = state
    position.exit_state_version += 1
    position.updated_at = now
    event_id = stable_contract_hash(
        {
            "schema": "tracked_position_alert_policy_change_v1",
            "position_id": position.id,
            "state_version": position.exit_state_version,
            "policy_id": selection.policy_id,
            "changed_at": now.isoformat(),
        }
    )
    session.add(
        TrackedPositionAlertAudit(
            tracked_position_id=position.id,
            outcome="policy_changed",
            alert_date=now.replace(tzinfo=UTC).date(),
            alert_type="tracked_position_alert_policy_changed",
            trigger_label=selection.policy_id,
            data_source="owner_selection",
            quote_freshness="not_applicable",
            threshold_context_json={
                "previous_policy_id": before[0],
                "next_policy_id": selection.policy_id,
                "policy_version": selection.policy_version,
                "provenance": selection.provenance,
            },
            decision_context_json={
                "source_strategy": selection.source_strategy,
                "source_manifest_hash": selection.source_manifest_hash,
                "source_decision_at": (
                    selection.source_decision_at.isoformat()
                    if selection.source_decision_at
                    else None
                ),
            },
            event_id=event_id,
            event_schema_version="tracked_position_alert_policy_change_v1",
            policy_version=selection.policy_version,
            actor_id=owner_id,
            occurred_at=now,
            execution_provenance="none",
        )
    )
    return True


__all__ = [
    "AlertPolicySelection",
    "DEFAULT_POLICY_ID",
    "DEFAULT_POLICY_VERSION",
    "LATE_DAY_POLICY_ID",
    "LATE_DAY_POLICY_VERSION",
    "apply_alert_policy_selection",
    "resolve_alert_policy_selection",
]
