from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import JobRun
from app.services.market_data import ASIA_SHANGHAI, is_etf_exchange_trading_day
from app.services.short_etf.bounded_history_sync import (
    HISTORY_SELECTION_POLICY,
    RESEARCH_DEPTH_SELECTION_POLICY,
    BoundedHistorySyncRequest,
    BoundedHistorySyncResult,
    run_bounded_history_sync_slice,
)
from app.services.short_etf.publication_providers import (
    PUBLICATION_PROVIDER_POLICY_VERSION,
    PublicationAdjustedHistoryFetcher,
)
from app.services.short_research.coverage_policy import (
    ETF_COMPLETE_SCORE_COVERAGE,
    ETF_DAILY_DECISION_MIN_COVERAGE,
    ETF_RESEARCH_DEPTH_MIN_COVERAGE,
    ETF_SCORE_PUBLICATION_MIN_COVERAGE,
    evaluate_etf_readiness,
)
from app.services.short_research.history_readiness import (
    DEEP_TELEMETRY_DEPTH_SCOPE,
    DEEP_TELEMETRY_DEPTH_SESSIONS,
    SCORE_WARMUP_SCOPE,
    SCORE_WARMUP_SESSIONS,
    history_depth_scope,
)
from app.services.short_research.ranking_contract import canonical_hash
from app.services.workflows.etf_history_readiness import (
    DEFAULT_HISTORY_HORIZONS,
    current_etf_history_contract_hash,
    read_etf_history_readiness,
)

RESEARCH_ADJUSTMENT_CONTRACT = "total-return-adjusted-provenance-v1"
RESEARCH_RSS_LIMIT_BYTES = 512 * 1024 * 1024
RESEARCH_PROFILE_MIN_CODES = 5
RESEARCH_PROFILE_INITIAL_CODES = 10
RESEARCH_PROFILE_MAX_CODES = 20
WARMUP_REPAIR_LOOKBACK_DAYS = 730
COMPACT_SAMPLE_LIMIT = 20
_PRESSURE_STOP_REASONS = {
    "admission_deadline",
    "process_deadline",
    "provider_circuit_open",
    "rss_limit",
    "worker_deadline",
}


def choose_research_depth_batch_size(
    evidence: Sequence[Mapping[str, Any]],
) -> int:
    if not evidence:
        return RESEARCH_PROFILE_INITIAL_CODES
    latest = evidence[0]
    current = int(
        latest.get("profile_max_codes") or RESEARCH_PROFILE_INITIAL_CODES
    )
    current = min(
        RESEARCH_PROFILE_MAX_CODES,
        max(RESEARCH_PROFILE_MIN_CODES, current),
    )
    if _slice_has_pressure(latest):
        return max(RESEARCH_PROFILE_MIN_CODES, current // 2)
    if (
        len(evidence) >= 2
        and all(_slice_is_healthy(item) for item in evidence[:2])
        and all(
            int(item.get("profile_max_codes") or 0) == current
            for item in evidence[:2]
        )
    ):
        return min(RESEARCH_PROFILE_MAX_CODES, current + 5)
    return current


def _slice_has_pressure(details: Mapping[str, Any]) -> bool:
    stop_reason = str(details.get("stop_reason") or "")
    if (
        details.get("status") == "failed"
        or stop_reason in _PRESSURE_STOP_REASONS
        or stop_reason.startswith("page_persistence_error:")
    ):
        return True
    if int(details.get("peak_rss_bytes") or 0) >= RESEARCH_RSS_LIMIT_BYTES:
        return True
    exclusions = details.get("exclusions")
    if isinstance(exclusions, list):
        for exclusion in exclusions:
            if (
                isinstance(exclusion, list | tuple)
                and len(exclusion) >= 2
                and (
                    str(exclusion[1]).startswith("provider_error:")
                    or str(exclusion[1]) == "provider_timeout"
                )
            ):
                return True
    provider_health = details.get("provider_health")
    providers = (
        provider_health.get("providers")
        if isinstance(provider_health, Mapping)
        else None
    )
    return bool(
        isinstance(providers, Mapping)
        and any(
            isinstance(state, Mapping)
            and state.get("circuit_state") == "open"
            for state in providers.values()
        )
    )


def _slice_is_healthy(details: Mapping[str, Any]) -> bool:
    return (
        not _slice_has_pressure(details)
        and details.get("status") in {"partial", "complete"}
        and int(details.get("provider_attempt_count") or 0) > 0
        and bool(details.get("last_completed_code"))
        and float(details.get("elapsed_seconds") or 0.0) < 45.0
    )


async def _recent_lane_slices(
    session: AsyncSession,
    *,
    scope: str,
) -> list[JobRun]:
    return list(
        (
            await session.scalars(
                select(JobRun)
                .where(
                    JobRun.job_name == f"etf_history_continuation:{scope}",
                    JobRun.status.in_(("partial", "complete", "failed")),
                )
                .order_by(JobRun.id.desc())
                .limit(2)
            )
        ).all()
    )


async def _active_history_lease(session: AsyncSession) -> JobRun | None:
    stale_before = datetime.utcnow() - timedelta(seconds=120)
    lease = await session.scalar(
        select(JobRun)
        .where(
            JobRun.job_name.like("etf_history_continuation:%"),
            JobRun.status == "running",
            JobRun.started_at >= stale_before,
        )
        .order_by(JobRun.id.desc())
        .limit(1)
    )
    return lease if isinstance(lease, JobRun) else None


def _deep_telemetry_contract_hash() -> str:
    return canonical_hash(
        {
            "schema_version": "etf_adjusted_history_depth_500_v1",
            "required_sessions": DEEP_TELEMETRY_DEPTH_SESSIONS,
            "price_basis": "total_return_adjusted",
            "provider_policy_version": PUBLICATION_PROVIDER_POLICY_VERSION,
        }
    )


def _lane_completion_gate_passed(lane: Mapping[str, Any]) -> bool:
    explicit = lane.get("completion_gate_passed")
    if isinstance(explicit, bool):
        return explicit
    return float(lane.get("coverage_ratio") or 0.0) >= ETF_RESEARCH_DEPTH_MIN_COVERAGE


def _compact_lane(lane: Mapping[str, Any]) -> dict[str, Any]:
    pending = lane.get("pending_codes")
    full_universe_count = lane.get("full_universe_count")
    listing_metadata_ratio = lane.get("listing_metadata_coverage_ratio")
    listing_metadata_gate = lane.get("listing_metadata_gate_passed")
    structurally_unseasoned_count = lane.get("structurally_unseasoned_count")
    completion_gate = lane.get("completion_gate_passed")
    return {
        "scope": lane.get("scope"),
        "required_sessions": int(lane.get("required_sessions") or 0),
        "authoritative": bool(lane.get("authoritative")),
        "expected_count": int(lane.get("expected_count") or 0),
        "covered_count": int(lane.get("covered_count") or 0),
        "excluded_count": int(lane.get("excluded_count") or 0),
        "coverage_ratio": float(lane.get("coverage_ratio") or 0.0),
        "denominator_kind": lane.get("denominator_kind"),
        "full_universe_count": (
            int(full_universe_count) if full_universe_count is not None else None
        ),
        "cohort_hash": lane.get("cohort_hash"),
        "cohort_evidence_hash": lane.get("cohort_evidence_hash"),
        "exclusion_hash": lane.get("exclusion_hash"),
        "session_calendar_hash": lane.get("session_calendar_hash"),
        "unknown_listing_hash": lane.get("unknown_listing_hash"),
        "structurally_unseasoned_hash": lane.get(
            "structurally_unseasoned_hash"
        ),
        "first_required_session": lane.get("first_required_session"),
        "listing_metadata_coverage_ratio": (
            float(listing_metadata_ratio)
            if listing_metadata_ratio is not None
            else None
        ),
        "listing_metadata_gate_passed": (
            listing_metadata_gate
            if isinstance(listing_metadata_gate, bool)
            else None
        ),
        "structurally_unseasoned_count": (
            int(structurally_unseasoned_count)
            if structurally_unseasoned_count is not None
            else None
        ),
        "completion_gate_passed": (
            completion_gate if isinstance(completion_gate, bool) else None
        ),
        "completion_blockers": list(lane.get("completion_blockers") or []),
        "pending_samples": (
            list(pending[:COMPACT_SAMPLE_LIMIT])
            if isinstance(pending, list)
            else []
        ),
    }


def _compact_sync_result(
    result: BoundedHistorySyncResult,
    *,
    profile_max_codes: int,
) -> dict[str, Any]:
    checkpoint = result.last_durable_checkpoint or {}
    return {
        "status": result.status,
        "stop_reason": result.stop_reason,
        "profile_max_codes": profile_max_codes,
        "attempted_count": len(result.attempted_codes),
        "attempted_samples": list(result.attempted_codes[:COMPACT_SAMPLE_LIMIT]),
        "completed_count": len(result.completed_codes),
        "completed_samples": list(result.completed_codes[:COMPACT_SAMPLE_LIMIT]),
        "exclusion_count": len(result.exclusions),
        "exclusion_samples": [
            list(item) for item in result.exclusions[:COMPACT_SAMPLE_LIMIT]
        ],
        "fetched_rows": result.fetched_rows,
        "persisted_rows": result.persisted_rows,
        "elapsed_seconds": result.elapsed_seconds,
        "peak_rss_bytes": result.peak_rss_bytes,
        "rows_per_second": round(
            result.persisted_rows / max(result.elapsed_seconds, 0.000001),
            3,
        ),
        "checkpoint_identity_hash": checkpoint.get("identity_hash"),
        "last_completed_code": (
            result.completed_codes[-1] if result.completed_codes else None
        ),
    }


def _restored_provider_health(
    runs: Sequence[JobRun],
) -> Mapping[str, Mapping[str, Any]] | None:
    if not runs:
        return None
    details = runs[0].details_json or {}
    if (
        details.get("provider_policy_version")
        != PUBLICATION_PROVIDER_POLICY_VERSION
    ):
        return None
    health = details.get("provider_health")
    providers = health.get("providers") if isinstance(health, Mapping) else None
    return providers if isinstance(providers, Mapping) else None


async def run_post_publication_etf_research_history_slice(
    session: AsyncSession,
    *,
    target_date: date | None = None,
) -> dict[str, Any]:
    effective_date = target_date or datetime.now(ASIA_SHANGHAI).date()
    if not is_etf_exchange_trading_day(effective_date):
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "reason": "not_etf_exchange_trading_day",
            "target_date": effective_date.isoformat(),
        }
    active_lease = await _active_history_lease(session)
    if active_lease is not None:
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "reason": "overlapping_history_worker_lease",
            "target_date": effective_date.isoformat(),
        }

    readiness = await read_etf_history_readiness(
        session,
        target_date=effective_date,
        horizons=DEFAULT_HISTORY_HORIZONS,
    )
    daily = readiness.get("daily_freshness") or {}
    warmup = readiness.get("history_depth_61") or {}
    compact_gates = {
        "daily_freshness": _compact_lane(daily),
        "history_depth_61": _compact_lane(warmup),
        "thresholds": {
            "daily_freshness": ETF_DAILY_DECISION_MIN_COVERAGE,
            "history_depth_61_preview": ETF_SCORE_PUBLICATION_MIN_COVERAGE,
            "history_depth_61_complete": ETF_COMPLETE_SCORE_COVERAGE,
        },
    }
    readiness_policy = evaluate_etf_readiness(
        daily_coverage_ratio=float(daily.get("coverage_ratio") or 0.0),
        warmup_coverage_ratio=float(warmup.get("coverage_ratio") or 0.0),
    )
    if not readiness_policy.preview_allowed:
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "reason": "publication_priority_active",
            "target_date": effective_date.isoformat(),
            "publication_gates": compact_gates,
            "readiness_policy": readiness_policy.to_dict(),
        }

    repairing_warmup = not readiness_policy.complete_publication_allowed
    contract_lane = readiness.get("contract_depth") or {}
    if repairing_warmup:
        selected_lane = warmup
        scope = SCORE_WARMUP_SCOPE
        contract_hash = current_etf_history_contract_hash(
            horizons=DEFAULT_HISTORY_HORIZONS
        )
    elif not _lane_completion_gate_passed(contract_lane):
        selected_lane = contract_lane
        scope = history_depth_scope(str(readiness["contract_hash"]))
        contract_hash = current_etf_history_contract_hash(
            horizons=DEFAULT_HISTORY_HORIZONS
        )
    else:
        selected_lane = readiness.get("telemetry_depth_500") or {}
        scope = DEEP_TELEMETRY_DEPTH_SCOPE
        contract_hash = _deep_telemetry_contract_hash()

    if _lane_completion_gate_passed(selected_lane):
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "complete",
            "reason": "research_depth_targets_satisfied",
            "target_date": effective_date.isoformat(),
            "publication_gates": compact_gates,
            "lane": _compact_lane(selected_lane),
        }

    required_sessions = int(selected_lane.get("required_sessions") or 0)
    required_trade_dates: tuple[date, ...]
    if repairing_warmup:
        required_sessions = SCORE_WARMUP_SESSIONS
        required_trade_dates = ()
        from_date = effective_date - timedelta(days=WARMUP_REPAIR_LOOKBACK_DAYS)
        to_date = effective_date
    else:
        try:
            required_trade_dates = tuple(
                date.fromisoformat(str(value))
                for value in selected_lane.get("required_trade_dates") or ()
            )
        except ValueError:
            required_trade_dates = ()
        from_date = required_trade_dates[0] if required_trade_dates else effective_date
        to_date = required_trade_dates[-1] if required_trade_dates else effective_date
    if required_sessions <= 0 or (
        not repairing_warmup
        and (
            len(required_trade_dates) != required_sessions
            or required_trade_dates != tuple(sorted(set(required_trade_dates)))
        )
    ):
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "reason": "observed_session_calendar_incomplete",
            "target_date": effective_date.isoformat(),
            "publication_gates": compact_gates,
            "lane": _compact_lane(selected_lane),
        }

    universe = readiness.get("universe") or {}
    cohort_values = selected_lane.get("cohort_codes")
    code_values = cohort_values if isinstance(cohort_values, list) else universe.get("codes") or ()
    codes = tuple(sorted({str(code) for code in code_values}))
    universe_hash = str(selected_lane.get("cohort_hash") or universe.get("snapshot_hash") or "")
    if not codes:
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "reason": "seasoned_research_cohort_empty",
            "target_date": effective_date.isoformat(),
            "publication_gates": compact_gates,
            "lane": _compact_lane(selected_lane),
        }
    if len(universe_hash) != 64:
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "reason": "point_in_time_universe_unavailable",
            "target_date": effective_date.isoformat(),
            "publication_gates": compact_gates,
        }
    if (
        selected_lane.get("listing_metadata_gate_passed") is False
        and float(selected_lane.get("coverage_ratio") or 0.0) >= ETF_RESEARCH_DEPTH_MIN_COVERAGE
    ):
        return {
            "asset_type": ASSET_TYPE_ETF,
            "status": "skipped",
            "reason": "authoritative_listing_metadata_below_95pct",
            "target_date": effective_date.isoformat(),
            "publication_gates": compact_gates,
            "lane": _compact_lane(selected_lane),
        }

    recent_runs = await _recent_lane_slices(session, scope=scope)
    evidence = [dict(run.details_json or {}) for run in recent_runs]
    profile_max_codes = choose_research_depth_batch_size(evidence)
    if repairing_warmup:
        profile_max_codes = min(profile_max_codes, 10)
    request = BoundedHistorySyncRequest(
        scope=scope,
        contract_hash=contract_hash,
        universe_hash=universe_hash,
        eligible_codes=codes,
        from_date=from_date,
        to_date=to_date,
        required_sessions=required_sessions,
        required_trade_dates=required_trade_dates,
        max_codes=profile_max_codes,
        page_size=500,
        max_rows=5_000,
        admission_deadline_seconds=45.0,
        worker_deadline_seconds=55.0,
        process_deadline_seconds=60.0,
        rss_limit_bytes=RESEARCH_RSS_LIMIT_BYTES,
        provider_timeout_seconds=6.0,
        selection_policy=(
            HISTORY_SELECTION_POLICY
            if repairing_warmup
            else RESEARCH_DEPTH_SELECTION_POLICY
        ),
        provider_policy_version=PUBLICATION_PROVIDER_POLICY_VERSION,
        adjustment_contract=RESEARCH_ADJUSTMENT_CONTRACT,
        price_basis="total_return_adjusted",
    )
    async with PublicationAdjustedHistoryFetcher(
        attempt_timeout_seconds=request.provider_timeout_seconds,
        minimum_eligible_rows=request.required_sessions,
        restored_health=_restored_provider_health(recent_runs),
    ) as fetcher:
        sync_result = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetcher,
        )
    readiness_after = await read_etf_history_readiness(
        session,
        target_date=effective_date,
        horizons=DEFAULT_HISTORY_HORIZONS,
    )
    lane_key = (
        "history_depth_61"
        if repairing_warmup
        else "contract_depth"
        if scope != DEEP_TELEMETRY_DEPTH_SCOPE
        else "telemetry_depth_500"
    )
    return {
        "asset_type": ASSET_TYPE_ETF,
        "status": sync_result.status,
        "reason": sync_result.stop_reason,
        "target_date": effective_date.isoformat(),
        "publication_gates": compact_gates,
        "lane_before": _compact_lane(selected_lane),
        "lane_after": _compact_lane(readiness_after.get(lane_key) or {}),
        "sync": _compact_sync_result(
            sync_result,
            profile_max_codes=profile_max_codes,
        ),
    }


__all__ = [
    "choose_research_depth_batch_size",
    "run_post_publication_etf_research_history_slice",
]
