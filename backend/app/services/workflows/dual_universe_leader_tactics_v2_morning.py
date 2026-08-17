"""Bounded next-morning confirmation workflow for A-share low-base watches."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.strategy_lab.dual_universe_leader_tactics_v2_intraday_confirmation import (
    MORNING_MIN_CLOSED_BARS,
    materialize_low_base_morning_confirmations,
    read_low_base_morning_watch_pool,
)
from app.services.strategy_lab.late_day_turnaround_eastmoney import (
    capture_bounded_ashare_pool,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
V2_MORNING_CONFIRMATION_JOB_NAME = "dual_universe_leader_tactics_v2_morning_confirmation"
HARD_TIMEOUT_SECONDS = 55.0


def _local(value: datetime | None) -> datetime:
    current = value or datetime.now(SHANGHAI)
    if current.tzinfo is None:
        return current.replace(tzinfo=SHANGHAI)
    return current.astimezone(SHANGHAI)


async def dual_universe_leader_tactics_v2_morning_confirmation_job(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not settings.etf_leader_tactics_v2_morning_confirmation_enabled:
        return {
            "status": "skipped",
            "reason": "leader_tactics_v2_morning_confirmation_disabled",
            "research_only": True,
        }
    observed_at = _local(now)
    if observed_at.hour != 10 or observed_at.minute != 42:
        return {
            "status": "skipped",
            "reason": "outside_morning_confirmation_checkpoint",
            "research_only": True,
        }
    try:
        async with asyncio.timeout(HARD_TIMEOUT_SECONDS):
            pool = await read_low_base_morning_watch_pool(
                session,
                decision_at=observed_at,
            )
            if not pool:
                return {
                    "status": "complete",
                    "reason": "no_single_day_volume_watch",
                    "declared_count": 0,
                    "provider_work_skipped": True,
                    "research_only": True,
                }
            capture = await capture_bounded_ashare_pool(
                session,
                decision_at=observed_at,
                declared_pool=[
                    (candidate.asset_code, candidate.asset_name) for candidate in pool
                ],
                checkpoint_provider="eastmoney_5m_leader_confirmation",
                minimum_closed_bars=MORNING_MIN_CLOSED_BARS,
                use_receipt_time_cutoff=True,
            )
            if capture.get("status") != "complete":
                return {
                    **capture,
                    "unavailable_reason": "morning_intraday_capture_incomplete",
                }
            evidence_cutoff = capture.get("evidence_cutoff")
            decision_at = (
                datetime.fromisoformat(str(evidence_cutoff))
                if evidence_cutoff
                else datetime.now(SHANGHAI)
            )
            return await materialize_low_base_morning_confirmations(
                session,
                decision_at=decision_at,
            )
    except TimeoutError:
        await session.rollback()
        return {
            "status": "unavailable",
            "unavailable_reason": "morning_confirmation_timeout",
            "hard_timeout_seconds": HARD_TIMEOUT_SECONDS,
            "research_only": True,
            "production_mutation_allowed": False,
        }


__all__ = [
    "HARD_TIMEOUT_SECONDS",
    "V2_MORNING_CONFIRMATION_JOB_NAME",
    "dual_universe_leader_tactics_v2_morning_confirmation_job",
]
