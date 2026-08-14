"""Read-only API for persisted late-day turnaround research evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.services.strategy_lab.late_day_turnaround_shadow import (
    CONTRACT_HASH,
    CONTRACT_PAYLOAD,
    POLICY_MODE,
    STRATEGY_VERSION,
)
from app.services.strategy_lab.late_day_turnaround_storage import (
    API_DISABLED,
    NO_COMPLETED_MANIFEST,
    read_latest_evidence,
    unavailable_payload,
)

router = APIRouter(prefix="/api/short-research/late-day-turnaround", tags=["late-day-turnaround"])


@router.get("/contract")
async def get_contract() -> dict:
    return {
        "schema_version": STRATEGY_VERSION,
        "contract_hash": CONTRACT_HASH,
        "policy_mode": POLICY_MODE,
        "contract": CONTRACT_PAYLOAD,
        "universes": ["etf", "ashare"],
        "research_only": True,
        "production_mutation_allowed": False,
    }


@router.get("/readiness")
async def get_readiness(
    request: Request,
    universe: Literal["etf", "ashare"] = Query(default="etf"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if not request.app.state.settings.late_day_turnaround_api_enabled:
        return unavailable_payload(universe=universe, reason=API_DISABLED)
    try:
        payload = await read_latest_evidence(session, universe=universe, limit=1)
    except SQLAlchemyError:
        return unavailable_payload(universe=universe, reason=NO_COMPLETED_MANIFEST)
    return {**payload, "items": [], "next_cursor": None, "has_more": False}


@router.get("/candidates")
async def get_candidates(
    request: Request,
    universe: Literal["etf", "ashare"] = Query(default="etf"),
    kind: Literal["formal_candidate", "daily_proxy_watchlist"] = Query(
        default="formal_candidate"
    ),
    as_of: datetime | None = Query(default=None),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Read persisted evidence only; this endpoint never invokes a provider."""

    if not request.app.state.settings.late_day_turnaround_api_enabled:
        return unavailable_payload(universe=universe, reason=API_DISABLED, as_of=as_of)
    try:
        return await read_latest_evidence(
            session,
            universe=universe,
            as_of=as_of,
            observation_kind=kind,
            cursor=cursor,
            limit=limit,
        )
    except (SQLAlchemyError, ValueError):
        return unavailable_payload(
            universe=universe,
            reason=NO_COMPLETED_MANIFEST,
            as_of=as_of,
        )


__all__ = ["router"]
