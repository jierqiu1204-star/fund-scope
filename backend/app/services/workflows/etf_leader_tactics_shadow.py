"""Production-safe orchestration entry for leader-tactics research pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import EtfPitCaptureSource
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_leader_tactics_continuation import (
    LeaderContinuationHandlers,
    LeaderContinuationManifest,
    leader_continuation_view,
    run_bounded_leader_tactics_continuation,
)

LEADER_CONTINUATION_JOB_NAME = "etf_leader_tactics_shadow_continue"
LEADER_CONTINUATION_DISABLED = "leader_tactics_continuation_disabled"
LEADER_CODE_VERSION_MISSING = "leader_tactics_code_version_missing"
LEADER_PIT_SESSIONS_INSUFFICIENT = "leader_tactics_requires_252_pit_sessions"
LEADER_HISTORICAL_TAXONOMY_MISSING = (
    "leader_historical_taxonomy_artifacts_unavailable"
)
MINIMUM_LEADER_PIT_SESSIONS = 252


async def _pit_session_count(session: AsyncSession) -> int:
    value = await session.scalar(
        select(func.count(distinct(EtfPitCaptureSource.as_of_trade_date))).where(
            EtfPitCaptureSource.readiness_state == "complete",
            EtfPitCaptureSource.target_date_coverage_ratio >= 0.95,
            EtfPitCaptureSource.warmup_coverage_ratio >= 0.95,
        )
    )
    return int(value or 0)


def _unavailable(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "job_name": LEADER_CONTINUATION_JOB_NAME,
        "status": "insufficient_data" if details else "disabled",
        "unavailable_reason": reason,
        "live_provider_calls": 0,
        "advanced_pages": 0,
        "single_worker": True,
        "research_only": True,
        "production_mutation_allowed": False,
        **details,
    }


async def continue_etf_leader_tactics_shadow_job(
    session: AsyncSession,
    *,
    settings: Settings,
    manifest: LeaderContinuationManifest | None = None,
    handlers: LeaderContinuationHandlers | None = None,
    artifact_store: ReplayArtifactStore | None = None,
    timeout_seconds: float = 50.0,
) -> dict[str, Any]:
    """Advance one injected/registered page; discovery never calls a provider."""

    if not settings.etf_leader_tactics_continuation_enabled:
        return _unavailable(LEADER_CONTINUATION_DISABLED)
    if not settings.etf_leader_tactics_code_version.strip():
        return _unavailable(LEADER_CODE_VERSION_MISSING)
    if manifest is None or handlers is None:
        pit_sessions = await _pit_session_count(session)
        await session.rollback()
        if pit_sessions < MINIMUM_LEADER_PIT_SESSIONS:
            return _unavailable(
                LEADER_PIT_SESSIONS_INSUFFICIENT,
                eligible_pit_sessions=pit_sessions,
                required_pit_sessions=MINIMUM_LEADER_PIT_SESSIONS,
            )
        # Historical taxonomy/regime facts are deliberately not backfilled from
        # current metadata. Until immutable PIT facts are materialized, stop here.
        return _unavailable(
            LEADER_HISTORICAL_TAXONOMY_MISSING,
            eligible_pit_sessions=pit_sessions,
            required_pit_sessions=MINIMUM_LEADER_PIT_SESSIONS,
        )

    if manifest.code_version != settings.etf_leader_tactics_code_version:
        return _unavailable(
            "leader_tactics_code_version_incompatible",
            manifest_code_version=manifest.code_version,
        )
    store = artifact_store or ReplayArtifactStore(
        Path(settings.etf_leader_tactics_artifact_dir)
        / f"{manifest.manifest_hash}.sqlite3"
    )
    checkpoint = await run_bounded_leader_tactics_continuation(
        session,
        artifact_store=store,
        manifest=manifest,
        handlers=handlers,
        timeout_seconds=timeout_seconds,
    )
    return {
        "job_name": LEADER_CONTINUATION_JOB_NAME,
        "advanced_pages": 1,
        **leader_continuation_view(checkpoint),
    }
