from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import EtfPriceHistory, TrackedPosition

LIFECYCLE_SCHEMA_VERSION = "etf_alert_action_lifecycle_v1"
LIFECYCLE_POLICY_VERSION = "etf_alert_action_policy_v1"


@dataclass(frozen=True)
class LifecycleBackfillResult:
    requested: int
    processed: int
    confirmed_baseline: int
    estimated_baseline: int
    data_waiting: int
    closed: int
    legacy_unverified: int
    skipped_existing: int
    rejected: int
    processed_position_ids: tuple[int, ...]


def _positive_finite(value: float | None) -> float | None:
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


async def _decision_eligible_high_water(
    session: AsyncSession,
    position: TrackedPosition,
    cutoff: date,
) -> float | None:
    value = await session.scalar(
        select(func.max(EtfPriceHistory.research_adjusted_value)).where(
            EtfPriceHistory.etf_code == position.asset_code,
            EtfPriceHistory.trade_date >= position.buy_date,
            EtfPriceHistory.trade_date <= cutoff,
            EtfPriceHistory.decision_eligible.is_(True),
            EtfPriceHistory.research_price_basis == "total_return_adjusted",
            EtfPriceHistory.research_adjusted_value.is_not(None),
        )
    )
    return _positive_finite(float(value)) if value is not None else None


async def _write_backfill_state(
    session: AsyncSession,
    position: TrackedPosition,
    state: dict,
) -> bool:
    result = await session.execute(
        update(TrackedPosition)
        .where(
            TrackedPosition.id == position.id,
            TrackedPosition.exit_state_version == position.exit_state_version,
        )
        .values(
            exit_state_json=state,
            exit_state_version=position.exit_state_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def backfill_position_lifecycle_batch(
    session: AsyncSession,
    *,
    position_ids: list[int],
    cutoff: date,
    max_items: int = 100,
) -> LifecycleBackfillResult:
    if not 1 <= max_items <= 1000:
        raise ValueError("max_items must be between 1 and 1000")
    requested_ids = list(dict.fromkeys(position_ids))[:max_items]
    counters = {
        "processed": 0,
        "confirmed_baseline": 0,
        "estimated_baseline": 0,
        "data_waiting": 0,
        "closed": 0,
        "legacy_unverified": 0,
        "skipped_existing": 0,
        "rejected": 0,
    }
    processed_position_ids: list[int] = []

    for position_id in requested_ids:
        position = await session.scalar(
            select(TrackedPosition).where(TrackedPosition.id == position_id).with_for_update()
        )
        if position is None or position.asset_type != ASSET_TYPE_ETF:
            counters["rejected"] += 1
            continue
        previous = dict(position.exit_state_json or {})
        if previous.get("lifecycle_schema_version") == LIFECYCLE_SCHEMA_VERSION:
            counters["skipped_existing"] += 1
            continue

        state = dict(previous)
        state["lifecycle_schema_version"] = LIFECYCLE_SCHEMA_VERSION
        state["policy_version"] = LIFECYCLE_POLICY_VERSION
        state["backfill_cutoff"] = cutoff.isoformat()
        if previous.get("latest_position_action"):
            state["execution_provenance"] = "legacy_unverified"
            counters["legacy_unverified"] += 1

        if position.status != "active":
            state.update(
                {
                    "position_episode_status": "closed",
                    "position_episode_id": None,
                    "alert_rule_states": {},
                    "current_action_id": None,
                    "open_action_cycle_id": None,
                }
            )
            counters["closed"] += 1
        else:
            state["position_episode_status"] = "active"
            state["position_episode_id"] = str(uuid4())
            state["exposure_version"] = 1
            state["alert_rule_states"] = {}
            state["current_action_id"] = None
            state["open_action_cycle_id"] = None
            confirmed_quantity = _positive_finite(position.confirmed_shares)
            estimated_quantity = _positive_finite(position.estimated_shares)
            high_water = await _decision_eligible_high_water(session, position, cutoff)
            if confirmed_quantity is not None and high_water is not None:
                quantity = confirmed_quantity
                source = "confirmed_shares"
                counters["confirmed_baseline"] += 1
            elif estimated_quantity is not None and high_water is not None:
                quantity = estimated_quantity
                source = "verified_estimated_shares"
                counters["estimated_baseline"] += 1
            else:
                quantity = None
                source = None

            if quantity is None or source is None:
                reason_code = (
                    "position_quantity_unavailable"
                    if confirmed_quantity is None and estimated_quantity is None
                    else "adjusted_history_unavailable"
                )
                state["needs_user_confirmation"] = True
                state["evaluation_data_outcome"] = {
                    "state": "data_waiting",
                    "reason_code": reason_code,
                }
                state.pop("exposure_baseline", None)
                counters["data_waiting"] += 1
            else:
                state["needs_user_confirmation"] = False
                state["evaluation_data_outcome"] = {
                    "state": "eligible",
                    "reason_code": "decision_eligible",
                }
                state["exposure_baseline"] = {
                    "normalized_quantity": quantity,
                    "source": source,
                    "adjustment_factor": 1.0,
                    "cutoff": cutoff.isoformat(),
                }
                state["high_water_adjusted_price"] = high_water

        if not await _write_backfill_state(session, position, state):
            counters["rejected"] += 1
            continue
        counters["processed"] += 1
        processed_position_ids.append(position.id)

    return LifecycleBackfillResult(
        requested=len(requested_ids),
        processed=counters["processed"],
        confirmed_baseline=counters["confirmed_baseline"],
        estimated_baseline=counters["estimated_baseline"],
        data_waiting=counters["data_waiting"],
        closed=counters["closed"],
        legacy_unverified=counters["legacy_unverified"],
        skipped_existing=counters["skipped_existing"],
        rejected=counters["rejected"],
        processed_position_ids=tuple(processed_position_ids),
    )
