"""Register score-bucket aggregates against complete immutable source manifests."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfSignalValidationRun, ShortResearchSignalRun, utcnow
from app.services.short_research.service import _calculate_etf_score_bucket_validation

from .etf_ranking_validation import RankingValidationContractError
from .etf_validation_manifest import (
    attach_validation_source_manifest,
    build_production_validation_source_cohort,
    validate_attached_validation_source_manifest,
)
from .etf_validation_session_planner import plan_production_validation_sources


async def run_registered_etf_score_bucket_validation(
    session: AsyncSession,
    *,
    days: int,
    score_basis: str = "opportunity",
    top_n: list[int] | None = None,
) -> EtfSignalValidationRun:
    theoretical_cap = 20 * (10 + 2)
    retention_cap = min(max(days, theoretical_cap), 500)
    try:
        planned_sources = await plan_production_validation_sources(
            session,
            as_of_date=date.today(),
            retention_cap_sessions=retention_cap,
        )
    except ValueError as exc:
        planned_source_runs = []
        source_plan = {
            "status": "unavailable",
            "reason": str(exc),
            "theoretical_session_span": theoretical_cap,
            "retention_cap_sessions": retention_cap,
        }
    else:
        planned_source_runs = list(planned_sources.source_runs)
        source_plan = {
            "status": planned_sources.date_plan.status,
            "reason": planned_sources.date_plan.reason,
            "theoretical_session_span": (
                planned_sources.date_plan.theoretical_session_span
            ),
            "searched_session_span": planned_sources.date_plan.searched_session_span,
            "retention_cap_sessions": (
                planned_sources.date_plan.retention_cap_sessions
            ),
            "compatible_count": planned_sources.date_plan.compatible_count,
            "completed_count": planned_sources.date_plan.completed_count,
            "pending_count": planned_sources.date_plan.pending_count,
            "overlapping_count": planned_sources.date_plan.overlapping_count,
            "non_overlapping_count": (
                planned_sources.date_plan.non_overlapping_count
            ),
            "excluded_count": planned_sources.date_plan.excluded_count,
            "source_date_shortfall": (
                planned_sources.date_plan.source_date_shortfall
            ),
            "query_count": planned_sources.date_plan.query_count,
            "selected_source_date_count": len(
                planned_sources.date_plan.selected_source_dates
            ),
        }
    run = await _calculate_etf_score_bucket_validation(
        session,
        days=days,
        score_basis=score_basis,
        top_n=top_n,
        planned_source_runs=planned_source_runs,
        source_plan=source_plan,
    )
    if run.status != "success":
        await session.commit()
        await session.refresh(run)
        return run

    raw_source_ids = (run.summary_json or {}).get("source_signal_run_ids")
    source_ids = (
        [value for value in raw_source_ids if isinstance(value, int) and value > 0]
        if isinstance(raw_source_ids, list)
        else []
    )
    try:
        if not source_ids:
            raise RankingValidationContractError(
                "validation source cohort cannot be empty"
            )
        source_runs = list(
            (
                await session.scalars(
                    select(ShortResearchSignalRun).where(
                        ShortResearchSignalRun.id.in_(source_ids)
                    )
                )
            ).all()
        )
        by_id = {source_run.id: source_run for source_run in source_runs}
        if set(by_id) != set(source_ids):
            raise RankingValidationContractError(
                "validation source manifest contains missing source runs"
            )
        cohort = await build_production_validation_source_cohort(
            session,
            source_runs=tuple(by_id[source_id] for source_id in source_ids),
        )
        run.status = "running"
        await attach_validation_source_manifest(session, run, cohort)
        await validate_attached_validation_source_manifest(session, run)
        run.summary_json = {
            **dict(run.summary_json or {}),
            "ranking_source_kind": cohort.ranking_source_kind.value,
            "source_manifest_hash": cohort.cohort_hash,
            "source_event_count": len(cohort.events),
        }
        run.status = "success"
    except RankingValidationContractError as exc:
        run.status = "failed"
        run.ranking_source_kind = None
        run.source_signal_run_id = None
        run.source_replay_run_key = None
        run.source_manifest_hash = None
        run.source_event_count = None
        run.error_message = f"来源 manifest 不兼容：{exc}"
        run.summary_json = {
            **dict(run.summary_json or {}),
            "status": "failed",
            "unavailable_reason": "source_manifest_incompatible",
            "source_manifest_error": str(exc),
        }
    run.finished_at = utcnow()
    await session.commit()
    await session.refresh(run)
    return run
