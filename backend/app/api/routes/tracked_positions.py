from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_approved_user
from app.core.db import get_db_session
from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlert,
    User,
    utcnow,
)
from app.schemas.tracked_positions import (
    TrackedPositionActionSummaryOut,
    TrackedPositionActionTransitionOut,
    TrackedPositionActionTransitionRequest,
    TrackedPositionAlertAuditListOut,
    TrackedPositionCloseRequest,
    TrackedPositionCreate,
    TrackedPositionDetailOut,
    TrackedPositionListOut,
    TrackedPositionOut,
    TrackedPositionUpdate,
)
from app.services.tracked_positions.action_repository import StalePositionStateError
from app.services.tracked_positions.action_transition_service import (
    ActionExecutionFacts,
    ActionTransitionCommand,
    ActionTransitionConflictError,
    ActionTransitionKind,
    ActionTransitionValidationError,
    transition_position_action,
)
from app.services.tracked_positions.audit_projection import (
    lifecycle_state_for_position,
    project_alert_audits,
    sanitize_legacy_audit,
)
from app.services.tracked_positions.exposure_repository import (
    ExposureMutationCommand,
    ExposureMutationIntent,
    ExposureMutationSource,
    apply_exposure_mutation,
)
from app.services.tracked_positions.lifecycle_read_repository import (
    MAX_PAGE_SIZE,
    LatestAuditDataState,
    count_alert_audits,
    count_legacy_alerts,
    decode_timeline_cursor,
    encode_timeline_cursor,
    get_current_action,
    get_current_actions,
    get_latest_audit_data_states,
    list_action_history,
    list_alert_audit_page,
    list_legacy_alert_page,
)
from app.services.tracked_positions.service import (
    SignalContext,
    alert_out,
    cost_basis_for_position,
    create_position,
    current_snapshot,
    email_configured,
    latest_alert_for_position,
    latest_alerts_for_positions,
    latest_signal_context,
    latest_signal_contexts,
    legacy_alert_audit_out,
    position_analysis,
    position_sizing_recommendation,
    recalculate_entry,
    recent_intraday_alerts_for_position,
    recent_intraday_alerts_for_positions,
)

router = APIRouter(prefix="/api/tracked-positions", tags=["tracked-positions"])


async def _position_out(
    session: AsyncSession,
    row: TrackedPosition,
    *,
    user: User,
    signal_context: SignalContext | None = None,
    latest_alert: TrackedPositionAlert | None = None,
    latest_alert_loaded: bool = False,
    recent_intraday_alerts: list[TrackedPositionAlert] | None = None,
    recent_intraday_alerts_loaded: bool = False,
    current_action: TrackedPositionActionDecision | None = None,
    current_action_loaded: bool = False,
    latest_audit_data: LatestAuditDataState | None = None,
    latest_audit_data_loaded: bool = False,
) -> TrackedPositionOut:
    if signal_context is None:
        _run, item, report = await latest_signal_context(session, row)
        signal_context_loaded = True
    else:
        item = signal_context.item
        report = signal_context.report
        signal_context_loaded = True
    if latest_alert is None and not latest_alert_loaded:
        latest_alert = await latest_alert_for_position(session, row.id)
    if current_action is None and not current_action_loaded:
        current_action = await get_current_action(
            session,
            owner_id=user.id,
            position_id=row.id,
        )
    if latest_audit_data is None and not latest_audit_data_loaded:
        latest_audit_data = (
            await get_latest_audit_data_states(
                session,
                owner_id=user.id,
                position_ids=[row.id],
            )
        ).get(row.id)
    if recent_intraday_alerts is None and not recent_intraday_alerts_loaded:
        recent_intraday_alerts = await recent_intraday_alerts_for_position(session, row.id)
    if recent_intraday_alerts is None:
        recent_intraday_alerts = []
    analysis = await position_analysis(session, row, item=item)
    cost_basis, cost_basis_source = cost_basis_for_position(row)
    snapshot = await current_snapshot(
        session,
        row,
        item=item,
        report=report,
        signal_context_loaded=signal_context_loaded,
    )
    sizing = await position_sizing_recommendation(
        session,
        user,
        row,
        snapshot,
        analysis.exit_signal,
        trend_weakening=bool(analysis.technical_metrics.get('trend_weakening')),
    )
    analysis.exit_signal.position_action = sizing.action
    analysis.exit_signal.action_version = sizing.action_version
    analysis.exit_signal.reentry_rule_version = sizing.reentry_rule_version
    return TrackedPositionOut(
        id=row.id,
        asset_type=row.asset_type,
        asset_code=row.asset_code,
        asset_name=row.asset_name,
        buy_date=row.buy_date,
        order_time_bucket=row.order_time_bucket,
        confirmed_nav_date=row.confirmed_nav_date,
        confirmed_nav=row.confirmed_nav,
        confirmed_shares=row.confirmed_shares,
        buy_amount=row.buy_amount,
        cost_basis=round(cost_basis, 2) if cost_basis is not None else None,
        cost_basis_source=cost_basis_source,
        entry_price=row.entry_price,
        entry_price_date=row.entry_price_date,
        estimated_shares=row.estimated_shares,
        status=row.status,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
        current_snapshot=snapshot,
        exit_signal=analysis.exit_signal,
        position_action=sizing.action,
        recommended_action_label=sizing.label,
        current_market_value=sizing.current_market_value,
        current_account_weight=sizing.current_account_weight,
        target_account_weight=sizing.target_account_weight,
        recommended_trade_amount=sizing.recommended_trade_amount,
        recommended_trade_shares=sizing.recommended_trade_shares,
        position_sizing_reason=sizing.reason,
        action_class=sizing.action_class,
        exit_action_version=sizing.action_version,
        reentry_state=sizing.reentry_state,
        reentry_reason=sizing.reentry_reason,
        reentry_rule_version=sizing.reentry_rule_version,
        max_profit_pct=analysis.max_profit_pct,
        profit_giveback_pct=analysis.profit_giveback_pct,
        holding_days=analysis.holding_days,
        technical_metrics=analysis.technical_metrics,
        intraday_snapshot=analysis.intraday_snapshot,
        dynamic_thresholds=analysis.dynamic_thresholds,
        recent_intraday_alerts=[alert_out(item) for item in recent_intraday_alerts],
        latest_alert=alert_out(latest_alert) if latest_alert is not None else None,
        exit_state_version=row.exit_state_version,
        lifecycle_state=lifecycle_state_for_position(
            row,
            latest_data_state=(latest_audit_data.state if latest_audit_data else None),
            latest_data_reason_code=(
                latest_audit_data.reason_code if latest_audit_data else None
            ),
        ),
        current_action=(
            _action_summary_out(current_action) if current_action is not None else None
        ),
    )


def _action_summary_out(action: TrackedPositionActionDecision) -> TrackedPositionActionSummaryOut:
    planned_execution = max(
        0.0,
        action.baseline_normalized_quantity - action.target_normalized_quantity,
    )
    return TrackedPositionActionSummaryOut(
        id=action.id,
        status=action.status,
        is_current=action.is_current,
        policy_version=action.policy_version,
        data_state=action.data_state,
        target_remaining_fraction=action.target_remaining_fraction,
        target_normalized_quantity=action.target_normalized_quantity,
        target_account_weight=action.target_account_weight,
        baseline_normalized_quantity=action.baseline_normalized_quantity,
        cumulative_executed_quantity=action.cumulative_executed_quantity,
        remaining_execution_quantity=max(
            0.0,
            planned_execution - action.cumulative_executed_quantity,
        ),
        contributing_rules=sorted(set(action.contributing_rules_json or [])),
        execution_provenance=action.execution_provenance,
        status_reason=action.status_reason,
        valid_until=action.valid_until,
        acknowledged_at=action.acknowledged_at,
        executed_at=action.executed_at,
        expired_at=action.expired_at,
        cancelled_at=action.cancelled_at,
        superseded_at=action.superseded_at,
        created_at=action.created_at,
        updated_at=action.updated_at,
    )


async def _position_detail_out(session: AsyncSession, row: TrackedPosition, *, user: User) -> TrackedPositionDetailOut:
    base = await _position_out(session, row, user=user)
    alerts = (
        await session.scalars(
            select(TrackedPositionAlert)
            .where(TrackedPositionAlert.tracked_position_id == row.id)
            .order_by(TrackedPositionAlert.alert_date.desc(), TrackedPositionAlert.id.desc())
        )
    ).all()
    action_page = await list_action_history(
        session,
        owner_id=user.id,
        position_id=row.id,
        limit=20,
    )
    next_cursor = action_page.next_cursor
    return TrackedPositionDetailOut(
        **base.model_dump(),
        chart=(await position_analysis(session, row, item=(await latest_signal_context(session, row))[1])).chart,
        alerts=[alert_out(item) for item in alerts],
        action_history=[_action_summary_out(item) for item in action_page.items],
        action_history_next_cursor=(
            None
            if next_cursor is None
            else encode_timeline_cursor(next_cursor)
        ),
    )


@router.get("", response_model=TrackedPositionListOut)
async def list_tracked_positions(
    request: Request,
    user: User = Depends(require_approved_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionListOut:
    rows = list(
        (await session.scalars(
            select(TrackedPosition).where(TrackedPosition.user_id == user.id).order_by(
                TrackedPosition.status.asc(),
                TrackedPosition.created_at.desc(),
                TrackedPosition.id.desc(),
            )
        ))
        .all()
    )
    total = (
        await session.scalar(
            select(func.count()).select_from(TrackedPosition).where(TrackedPosition.user_id == user.id)
        )
        or 0
    )
    position_ids = [row.id for row in rows]
    signal_context_by_id = await latest_signal_contexts(session, rows)
    latest_alert_by_id = await latest_alerts_for_positions(session, position_ids)
    recent_intraday_alerts_by_id = await recent_intraday_alerts_for_positions(session, position_ids)
    current_action_by_id = await get_current_actions(
        session,
        owner_id=user.id,
        position_ids=position_ids,
    )
    latest_audit_data_by_id = await get_latest_audit_data_states(
        session,
        owner_id=user.id,
        position_ids=position_ids,
    )
    return TrackedPositionListOut(
        items=[
            await _position_out(
                session,
                row,
                user=user,
                signal_context=signal_context_by_id.get(row.id),
                latest_alert=latest_alert_by_id.get(row.id),
                latest_alert_loaded=True,
                recent_intraday_alerts=recent_intraday_alerts_by_id.get(row.id, []),
                recent_intraday_alerts_loaded=True,
                current_action=current_action_by_id.get(row.id),
                current_action_loaded=True,
                latest_audit_data=latest_audit_data_by_id.get(row.id),
                latest_audit_data_loaded=True,
            )
            for row in rows
        ],
        total=total,
        email_configured=email_configured(user, request.app.state.settings),
        recipient_email=user.recipient_email,
    )


@router.post("", response_model=TrackedPositionOut, status_code=status.HTTP_201_CREATED)
async def create_tracked_position(
    payload: TrackedPositionCreate,
    user: User = Depends(require_approved_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionOut:
    try:
        row = await create_position(
            session,
            asset_type=payload.asset_type,
            asset_code=payload.asset_code,
            user_id=user.id,
            buy_amount=payload.buy_amount,
            buy_date=payload.buy_date or date.today(),
            order_time_bucket=payload.order_time_bucket,
            confirmed_nav_date=payload.confirmed_nav_date,
            confirmed_nav=payload.confirmed_nav,
            confirmed_shares=payload.confirmed_shares,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _position_out(session, row, user=user)


@router.get("/{position_id}/audit", response_model=TrackedPositionAlertAuditListOut)
async def get_tracked_position_alert_audit(
    position_id: int,
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
    user: User = Depends(require_approved_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionAlertAuditListOut:
    row = await session.get(TrackedPosition, position_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="未找到这笔追踪")
    try:
        before = decode_timeline_cursor(cursor) if cursor else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="无效的审计游标") from exc

    audit_total = await count_alert_audits(
        session,
        owner_id=user.id,
        position_id=row.id,
    )
    if audit_total:
        page = await list_alert_audit_page(
            session,
            owner_id=user.id,
            position_id=row.id,
            before=before,
            limit=limit,
        )
        return TrackedPositionAlertAuditListOut(
            items=await project_alert_audits(
                session,
                owner_id=user.id,
                rows=page.items,
            ),
            total=audit_total,
            next_cursor=(
                encode_timeline_cursor(page.next_cursor) if page.next_cursor else None
            ),
        )

    page = await list_legacy_alert_page(
        session,
        owner_id=user.id,
        position_id=row.id,
        before=before,
        limit=limit,
    )
    return TrackedPositionAlertAuditListOut(
        items=[sanitize_legacy_audit(legacy_alert_audit_out(item)) for item in page.items],
        total=await count_legacy_alerts(
            session,
            owner_id=user.id,
            position_id=row.id,
        ),
        next_cursor=(
            encode_timeline_cursor(page.next_cursor) if page.next_cursor else None
        ),
    )


@router.get("/{position_id}", response_model=TrackedPositionDetailOut)
async def get_tracked_position(
    position_id: int,
    user: User = Depends(require_approved_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionDetailOut:
    row = await session.get(TrackedPosition, position_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="未找到这笔追踪")
    return await _position_detail_out(session, row, user=user)


@router.patch("/{position_id}", response_model=TrackedPositionOut)
async def update_tracked_position(
    position_id: int,
    payload: TrackedPositionUpdate,
    user: User = Depends(require_approved_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionOut:
    row = await session.get(TrackedPosition, position_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="未找到这笔追踪")
    recalculate_needed = False
    changed_execution_rule = False
    if payload.buy_date is not None:
        row.buy_date = payload.buy_date
        recalculate_needed = True
        changed_execution_rule = True
    if payload.order_time_bucket is not None:
        row.order_time_bucket = payload.order_time_bucket
        recalculate_needed = True
        changed_execution_rule = True
    if "confirmed_nav_date" in payload.model_fields_set:
        row.confirmed_nav_date = payload.confirmed_nav_date
        recalculate_needed = True
    elif changed_execution_rule:
        row.confirmed_nav_date = None
    if "confirmed_nav" in payload.model_fields_set:
        row.confirmed_nav = payload.confirmed_nav
        recalculate_needed = True
    if payload.note is not None:
        row.note = payload.note
    protected_change = (
        "confirmed_shares" in payload.model_fields_set
        or payload.buy_amount is not None
        or payload.status is not None
    )
    if protected_change:
        intent = ExposureMutationIntent(
            payload.exposure_mutation_intent
            or ("tracking_status" if payload.status is not None and "confirmed_shares" not in payload.model_fields_set and payload.buy_amount is None else "correction")
        )
        mutation_kwargs = {}
        if "confirmed_shares" in payload.model_fields_set:
            mutation_kwargs["new_confirmed_shares"] = payload.confirmed_shares
            recalculate_needed = True
        if payload.buy_amount is not None:
            mutation_kwargs["new_buy_amount"] = round(payload.buy_amount, 2)
            recalculate_needed = True
        if payload.status is not None:
            mutation_kwargs["new_status"] = payload.status
        now = utcnow()
        try:
            receipt = await apply_exposure_mutation(
                session,
                ExposureMutationCommand(
                    owner_id=user.id,
                    position_id=row.id,
                    expected_exit_state_version=(
                        payload.expected_exit_state_version
                        if payload.expected_exit_state_version is not None
                        else row.exit_state_version
                    ),
                    intent=intent,
                    source=ExposureMutationSource.PATCH,
                    occurred_at=now,
                    request_id=f"patch:{row.id}:{now.isoformat()}",
                    actor_id=user.id,
                    reason_code=payload.mutation_reason or "owner_position_edit",
                    **mutation_kwargs,
                ),
            )
            row = receipt.position
        except StalePositionStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if recalculate_needed:
        await recalculate_entry(session, row)
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return await _position_out(session, row, user=user)


@router.post(
    "/{position_id}/actions/{action_id}/transitions",
    response_model=TrackedPositionActionTransitionOut,
)
async def transition_tracked_position_action(
    position_id: int,
    action_id: int,
    payload: TrackedPositionActionTransitionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    user: User = Depends(require_approved_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionActionTransitionOut:
    now = utcnow()
    execution = payload.execution
    try:
        receipt = await transition_position_action(
            session,
            ActionTransitionCommand(
                owner_id=user.id,
                position_id=position_id,
                action_id=action_id,
                transition=ActionTransitionKind(payload.transition),
                idempotency_key=idempotency_key,
                expected_position_state_version=payload.expected_position_state_version,
                occurred_at=now,
                actor_id=user.id,
                execution=(
                    None
                    if execution is None
                    else ActionExecutionFacts(
                        executed_at=execution.executed_at,
                        quantity=float(execution.quantity),
                        price=float(execution.price),
                        price_source=execution.price_source,
                        fees=float(execution.fees),
                        resulting_shares=float(execution.resulting_shares),
                        close_fact=execution.close_fact,
                    )
                ),
            ),
        )
        await session.commit()
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail="未找到当前持仓动作") from exc
    except (StalePositionStateError, ActionTransitionConflictError) as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ActionTransitionValidationError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TrackedPositionActionTransitionOut(**receipt.as_response())


@router.post("/{position_id}/close", response_model=TrackedPositionOut)
async def close_tracked_position(
    position_id: int,
    payload: TrackedPositionCloseRequest,
    user: User = Depends(require_approved_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionOut:
    row = await session.get(TrackedPosition, position_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="未找到这笔追踪")
    now = utcnow()
    try:
        receipt = await apply_exposure_mutation(
            session,
            ExposureMutationCommand(
                owner_id=user.id,
                position_id=row.id,
                expected_exit_state_version=(
                    payload.expected_exit_state_version
                    if payload.expected_exit_state_version is not None
                    else row.exit_state_version
                ),
                intent=ExposureMutationIntent.TRACKING_STATUS,
                source=ExposureMutationSource.PATCH,
                occurred_at=now,
                request_id=f"close-tracking:{row.id}:{now.isoformat()}",
                actor_id=user.id,
                reason_code="owner_stopped_tracking",
                new_status=payload.status,
            ),
        )
        row = receipt.position
    except StalePositionStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if payload.note:
        row.note = payload.note
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return await _position_out(session, row, user=user)
