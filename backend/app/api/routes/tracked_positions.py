from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_approved_user
from app.core.db import get_db_session
from app.models.entities import (
    TrackedPosition,
    TrackedPositionAlert,
    TrackedPositionAlertAudit,
    User,
    utcnow,
)
from app.schemas.tracked_positions import (
    TrackedPositionAlertAuditListOut,
    TrackedPositionCloseRequest,
    TrackedPositionCreate,
    TrackedPositionDetailOut,
    TrackedPositionListOut,
    TrackedPositionOut,
    TrackedPositionUpdate,
)
from app.services.tracked_positions.service import (
    SignalContext,
    alert_audit_out,
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
    refresh_entry_if_waiting,
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
) -> TrackedPositionOut:
    await refresh_entry_if_waiting(session, row)
    if signal_context is None:
        _run, item, report = await latest_signal_context(session, row)
        signal_context_loaded = True
    else:
        item = signal_context.item
        report = signal_context.report
        signal_context_loaded = True
    if latest_alert is None and not latest_alert_loaded:
        latest_alert = await latest_alert_for_position(session, row.id)
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
        max_profit_pct=analysis.max_profit_pct,
        profit_giveback_pct=analysis.profit_giveback_pct,
        holding_days=analysis.holding_days,
        technical_metrics=analysis.technical_metrics,
        intraday_snapshot=analysis.intraday_snapshot,
        dynamic_thresholds=analysis.dynamic_thresholds,
        recent_intraday_alerts=[alert_out(item) for item in recent_intraday_alerts],
        latest_alert=alert_out(latest_alert) if latest_alert is not None else None,
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
    return TrackedPositionDetailOut(
        **base.model_dump(),
        chart=(await position_analysis(session, row, item=(await latest_signal_context(session, row))[1])).chart,
        alerts=[alert_out(item) for item in alerts],
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
    user: User = Depends(require_approved_user),
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionAlertAuditListOut:
    row = await session.get(TrackedPosition, position_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="未找到这笔追踪")
    audits = list(
        (
            await session.scalars(
                select(TrackedPositionAlertAudit)
                .where(TrackedPositionAlertAudit.tracked_position_id == row.id)
                .order_by(TrackedPositionAlertAudit.created_at.desc(), TrackedPositionAlertAudit.id.desc())
            )
        ).all()
    )
    if audits:
        return TrackedPositionAlertAuditListOut(items=[alert_audit_out(item) for item in audits], total=len(audits))
    legacy_alerts = list(
        (
            await session.scalars(
                select(TrackedPositionAlert)
                .where(TrackedPositionAlert.tracked_position_id == row.id)
                .order_by(TrackedPositionAlert.created_at.desc(), TrackedPositionAlert.id.desc())
            )
        ).all()
    )
    return TrackedPositionAlertAuditListOut(
        items=[legacy_alert_audit_out(item) for item in legacy_alerts],
        total=len(legacy_alerts),
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
    if "confirmed_shares" in payload.model_fields_set:
        row.confirmed_shares = payload.confirmed_shares
        recalculate_needed = True
    if payload.buy_amount is not None:
        row.buy_amount = round(payload.buy_amount, 2)
        recalculate_needed = True
    if payload.note is not None:
        row.note = payload.note
    if payload.status is not None:
        row.status = payload.status
    if recalculate_needed:
        await recalculate_entry(session, row)
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return await _position_out(session, row, user=user)


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
    row.status = payload.status
    if payload.note:
        row.note = payload.note
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return await _position_out(session, row, user=user)
