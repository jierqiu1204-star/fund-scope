from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import TrackedPosition, User, utcnow
from app.services.tracked_positions.owner_risk import (
    materialize_owner_risk_contexts,
    owner_risk_contexts_for_read,
    unavailable_owner_risk_context,
)
from app.services.tracked_positions.service import (
    ACTIVE_STATUS,
    PreparedAlertEvaluation,
    batch_etf_liquidity_inputs,
    create_alert_if_needed,
    prepare_alert_evaluation,
    refresh_entry_if_waiting,
)

PositionEvaluationObserver = Callable[
    [TrackedPosition, PreparedAlertEvaluation, str],
    Awaitable[dict[str, int]],
]


def _empty_shadow_counters() -> dict[str, int]:
    return {
        "shadow_evaluated": 0,
        "shadow_data_ineligible": 0,
        "shadow_failed": 0,
        "shadow_deferred": 0,
        "shadow_duplicates": 0,
    }


def _merge_counters(result: dict[str, Any], counters: dict[str, int]) -> None:
    for key in _empty_shadow_counters():
        result[key] += int(counters.get(key, 0))


async def daily_tracked_position_alerts_job(
    session: AsyncSession,
    settings: Settings | None = None,
    *,
    post_evaluation_observer: PositionEvaluationObserver | None = None,
) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(TrackedPosition)
            .where(TrackedPosition.status == ACTIVE_STATUS)
            .order_by(TrackedPosition.created_at.asc(), TrackedPosition.id.asc())
        )
    ).all()
    result = {
        "positions_checked": len(rows),
        "alerts_created": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "emails_skipped": 0,
        "web_only": 0,
        "deduplicated": 0,
        "no_signal": 0,
        "owner_risk_blocked_adds": 0,
        "owner_risk_blocked_add_positions": 0,
        "liquidity_capacity_ready": 0,
        "liquidity_capacity_blocked": 0,
        "liquidity_capacity_stressed": 0,
        "liquidity_capacity_unavailable": 0,
        "liquidity_blocked_adds": 0,
        "liquidity_stressed_exits": 0,
        **_empty_shadow_counters(),
    }
    effective_settings = settings or get_settings()
    owner_ids = tuple(sorted({position.user_id for position in rows}))
    job_now = utcnow()
    owner_risk_by_id, owner_risk_counters = await materialize_owner_risk_contexts(
        session,
        owner_ids=owner_ids,
        now=job_now,
    )
    result.update(owner_risk_counters)
    result["owner_risk_blocked_add_positions"] = sum(
        position.asset_type == ASSET_TYPE_ETF
        and (
            (context := owner_risk_by_id.get(position.user_id)) is None
            or context.status != "ready"
            or context.state != "normal"
        )
        for position in rows
    )
    await session.commit()
    turnover_by_code, quote_by_code = await batch_etf_liquidity_inputs(session, list(rows))
    for position in rows:
        await refresh_entry_if_waiting(session, position)
        prepared = await prepare_alert_evaluation(session, position)
        alert, status = await create_alert_if_needed(
            session,
            position,
            effective_settings,
            prepared_evaluation=prepared,
            liquidity_turnovers=turnover_by_code.get(position.asset_code, ()),
            liquidity_quote=quote_by_code.get(position.asset_code),
            liquidity_inputs_loaded=True,
            owner_risk_context=owner_risk_by_id.get(position.user_id)
            or unavailable_owner_risk_context("owner_risk_owner_bound_reached"),
            risk_counters=result,
        )
        if post_evaluation_observer is not None:
            # End any read-only legacy transaction before the isolated shadow
            # session acquires its own write transaction (important on SQLite).
            await session.commit()
            _merge_counters(
                result,
                await post_evaluation_observer(position, prepared, "daily"),
            )
        if status == "deduplicated":
            result["deduplicated"] += 1
            continue
        if status == "no_signal":
            result["no_signal"] += 1
            continue
        if alert is not None:
            result["alerts_created"] += 1
        if status == "email_sent":
            result["emails_sent"] += 1
        elif status == "email_failed":
            result["emails_failed"] += 1
        elif status == "email_skipped":
            result["emails_skipped"] += 1
        elif status == "web_only":
            result["web_only"] += 1
    return result


async def intraday_tracked_position_alerts_job(
    session: AsyncSession,
    settings: Settings | None = None,
    *,
    post_evaluation_observer: PositionEvaluationObserver | None = None,
) -> dict[str, int]:
    rows = (
        await session.scalars(
            select(TrackedPosition).where(
                TrackedPosition.asset_type == ASSET_TYPE_ETF,
                TrackedPosition.status == ACTIVE_STATUS,
            )
        )
    ).all()
    result = {
        "positions_checked": len(rows),
        "alerts_created": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "emails_skipped": 0,
        "web_only": 0,
        "deduplicated": 0,
        "suppressed": 0,
        "data_ineligible": 0,
        "no_signal": 0,
        "owner_risk_blocked_adds": 0,
        "owner_risk_blocked_add_positions": 0,
        "liquidity_capacity_ready": 0,
        "liquidity_capacity_blocked": 0,
        "liquidity_capacity_stressed": 0,
        "liquidity_capacity_unavailable": 0,
        "liquidity_blocked_adds": 0,
        "liquidity_stressed_exits": 0,
        **_empty_shadow_counters(),
    }
    effective_settings = settings or get_settings()
    owner_ids = tuple(sorted({position.user_id for position in rows}))
    users = tuple(
        (
            await session.scalars(
                select(User).where(User.id.in_(owner_ids)).order_by(User.id.asc())
            )
        ).all()
    )
    job_now = utcnow()
    owner_risk_by_id = await owner_risk_contexts_for_read(
        session,
        users=users,
        now=job_now,
    )
    result.update(
        {
            "owner_risk_owners": len(owner_ids),
            "owner_risk_data_halt": sum(
                context.state == "data_halt" for context in owner_risk_by_id.values()
            ),
        }
    )
    result["owner_risk_blocked_add_positions"] = sum(
        (context := owner_risk_by_id.get(position.user_id)) is None
        or context.status != "ready"
        or context.state != "normal"
        for position in rows
    )
    turnover_by_code, quote_by_code = await batch_etf_liquidity_inputs(session, list(rows))
    for position in rows:
        await refresh_entry_if_waiting(session, position)
        prepared = await prepare_alert_evaluation(session, position)
        alert, status = await create_alert_if_needed(
            session,
            position,
            effective_settings,
            evaluation_mode="intraday",
            prepared_evaluation=prepared,
            liquidity_turnovers=turnover_by_code.get(position.asset_code, ()),
            liquidity_quote=quote_by_code.get(position.asset_code),
            liquidity_inputs_loaded=True,
            owner_risk_context=owner_risk_by_id.get(position.user_id)
            or unavailable_owner_risk_context("owner_risk_owner_bound_reached"),
            risk_counters=result,
        )
        if post_evaluation_observer is not None:
            await session.commit()
            _merge_counters(
                result,
                await post_evaluation_observer(position, prepared, "intraday"),
            )
        if status == "deduplicated":
            result["deduplicated"] += 1
            continue
        if status == "suppressed":
            result["suppressed"] += 1
            continue
        if status == "no_signal":
            result["no_signal"] += 1
            continue
        if status == "data_ineligible":
            result["data_ineligible"] += 1
            continue
        if alert is not None:
            result["alerts_created"] += 1
        if status == "email_sent":
            result["emails_sent"] += 1
        elif status == "email_failed":
            result["emails_failed"] += 1
        elif status == "email_skipped":
            result["emails_skipped"] += 1
        elif status == "web_only":
            result["web_only"] += 1
    return result
