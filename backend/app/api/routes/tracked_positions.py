from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import TrackedPosition, TrackedPositionAlert, User, utcnow
from app.schemas.tracked_positions import (
    TrackedPositionCloseRequest,
    TrackedPositionCreate,
    TrackedPositionDetailOut,
    TrackedPositionListOut,
    TrackedPositionOut,
    TrackedPositionUpdate,
)
from app.services.tracked_positions.service import (
    alert_out,
    create_position,
    current_snapshot,
    email_configured,
    latest_alert_for_position,
    latest_signal_context,
    position_analysis,
    recalculate_entry,
)

router = APIRouter(prefix="/api/tracked-positions", tags=["tracked-positions"])


async def _position_out(session: AsyncSession, row: TrackedPosition) -> TrackedPositionOut:
    latest_alert = await latest_alert_for_position(session, row.id)
    _, item, _ = await latest_signal_context(session, row)
    analysis = await position_analysis(session, row, item=item)
    return TrackedPositionOut(
        id=row.id,
        asset_type=row.asset_type,
        asset_code=row.asset_code,
        asset_name=row.asset_name,
        buy_date=row.buy_date,
        buy_amount=row.buy_amount,
        entry_price=row.entry_price,
        entry_price_date=row.entry_price_date,
        estimated_shares=row.estimated_shares,
        status=row.status,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
        current_snapshot=await current_snapshot(session, row),
        exit_signal=analysis.exit_signal,
        max_profit_pct=analysis.max_profit_pct,
        profit_giveback_pct=analysis.profit_giveback_pct,
        holding_days=analysis.holding_days,
        technical_metrics=analysis.technical_metrics,
        latest_alert=alert_out(latest_alert) if latest_alert is not None else None,
    )


async def _position_detail_out(session: AsyncSession, row: TrackedPosition) -> TrackedPositionDetailOut:
    base = await _position_out(session, row)
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
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionListOut:
    rows = (
        await session.scalars(
            select(TrackedPosition).order_by(
                TrackedPosition.status.asc(),
                TrackedPosition.created_at.desc(),
                TrackedPosition.id.desc(),
            )
        )
    ).all()
    total = await session.scalar(select(func.count()).select_from(TrackedPosition)) or 0
    user = await session.get(User, 1)
    assert user is not None
    return TrackedPositionListOut(
        items=[await _position_out(session, row) for row in rows],
        total=total,
        email_configured=email_configured(user, request.app.state.settings),
        recipient_email=user.recipient_email,
    )


@router.post("", response_model=TrackedPositionOut, status_code=status.HTTP_201_CREATED)
async def create_tracked_position(
    payload: TrackedPositionCreate,
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionOut:
    try:
        row = await create_position(
            session,
            asset_type=payload.asset_type,
            asset_code=payload.asset_code,
            buy_amount=payload.buy_amount,
            buy_date=payload.buy_date or date.today(),
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _position_out(session, row)


@router.get("/{position_id}", response_model=TrackedPositionDetailOut)
async def get_tracked_position(
    position_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionDetailOut:
    row = await session.get(TrackedPosition, position_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到这笔追踪")
    return await _position_detail_out(session, row)


@router.patch("/{position_id}", response_model=TrackedPositionOut)
async def update_tracked_position(
    position_id: int,
    payload: TrackedPositionUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionOut:
    row = await session.get(TrackedPosition, position_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到这笔追踪")
    if payload.buy_date is not None:
        row.buy_date = payload.buy_date
    if payload.buy_amount is not None:
        row.buy_amount = round(payload.buy_amount, 2)
    if payload.note is not None:
        row.note = payload.note
    if payload.status is not None:
        row.status = payload.status
    if payload.buy_date is not None or payload.buy_amount is not None:
        await recalculate_entry(session, row)
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return await _position_out(session, row)


@router.post("/{position_id}/close", response_model=TrackedPositionOut)
async def close_tracked_position(
    position_id: int,
    payload: TrackedPositionCloseRequest,
    session: AsyncSession = Depends(get_db_session),
) -> TrackedPositionOut:
    row = await session.get(TrackedPosition, position_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到这笔追踪")
    row.status = payload.status
    if payload.note:
        row.note = payload.note
    row.updated_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return await _position_out(session, row)
