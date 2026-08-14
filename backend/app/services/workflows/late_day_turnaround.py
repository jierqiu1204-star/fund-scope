"""Bounded scheduler entry points for late-day turnaround research."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_lab.late_day_turnaround_eastmoney import (
    capture_bounded_ashare_pool,
)
from app.services.strategy_lab.late_day_turnaround_materializer import (
    materialize_late_day_turnaround,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
HARD_TIMEOUT_SECONDS = 55.0


async def late_day_turnaround_materialize_job(
    session: AsyncSession,
    *,
    universe: Literal["etf", "ashare"],
    now: datetime | None = None,
) -> dict:
    cutoff = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if cutoff.hour != 14 or cutoff.minute not in {30, 40, 50}:
        return {
            "status": "skipped",
            "universe": universe,
            "reason": "outside_materialize_checkpoint",
            "research_only": True,
            "production_mutation_allowed": False,
        }
    cutoff = cutoff.replace(second=0, microsecond=0)
    try:
        async with asyncio.timeout(HARD_TIMEOUT_SECONDS):
            return await materialize_late_day_turnaround(
                session,
                universe=universe,
                decision_at=cutoff,
            )
    except TimeoutError:
        await session.rollback()
        return {
            "status": "unavailable",
            "universe": universe,
            "unavailable_reason": "job_timeout",
            "hard_timeout_seconds": HARD_TIMEOUT_SECONDS,
            "research_only": True,
            "production_mutation_allowed": False,
        }


async def late_day_turnaround_ashare_capture_job(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict:
    observed_at = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    if observed_at.hour != 14 or observed_at.minute not in {28, 38, 48}:
        return {"status": "skipped", "reason": "outside_capture_checkpoint"}
    decision_at = observed_at.replace(second=0, microsecond=0) + timedelta(minutes=2)
    try:
        async with asyncio.timeout(HARD_TIMEOUT_SECONDS):
            return await capture_bounded_ashare_pool(
                session,
                decision_at=decision_at,
            )
    except TimeoutError:
        await session.rollback()
        return {
            "status": "unavailable",
            "universe": "ashare",
            "unavailable_reason": "job_timeout",
            "hard_timeout_seconds": HARD_TIMEOUT_SECONDS,
            "research_only": True,
            "production_mutation_allowed": False,
        }


__all__ = [
    "HARD_TIMEOUT_SECONDS",
    "late_day_turnaround_ashare_capture_job",
    "late_day_turnaround_materialize_job",
]
