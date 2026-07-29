from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import EtfDailyWorkflowLock, JobRun
from app.services.market_data import is_etf_exchange_trading_day
from app.services.short_etf.bounded_history_sync import (
    BoundedHistorySyncRequest,
    BoundedHistorySyncResult,
    read_latest_compatible_provider_health,
    run_bounded_history_sync_slice,
)
from app.services.short_etf.publication_providers import (
    PUBLICATION_PROVIDER_POLICY_VERSION,
    PublicationAdjustedHistoryFetcher,
)
from app.services.short_research.coverage_policy import (
    ETF_DAILY_DECISION_MIN_COVERAGE,
    EtfReadinessPolicyResult,
    evaluate_etf_readiness,
)
from app.services.short_research.dual_snapshot_materialization import (
    SnapshotMaterializationError,
)
from app.services.short_research.history_readiness import SCORE_WARMUP_SESSIONS
from app.services.short_research.snapshot_publication import (
    SnapshotPublicationError,
    build_etf_coverage_barrier,
)
from app.services.short_research.snapshot_selector import (
    resolve_current_etf_ranking_surface_snapshot,
)
from app.services.tracked_positions.service import active_tracked_etf_codes
from app.services.workflows.etf_daily_research import (
    ETF_DAILY_WORKFLOW_LOCK_LEASE,
    ETF_DAILY_WORKFLOW_RUNNING,
    etf_source_availability_cutoff,
    finish_etf_daily_workflow_lock,
    generate_and_publish_etf_snapshot,
    generate_provisional_etf_research_preview,
    try_acquire_etf_daily_workflow_lock,
)
from app.services.workflows.etf_history_readiness import (
    read_etf_history_readiness,
)

PUBLICATION_ADJUSTMENT_CONTRACT = "total-return-adjusted-provenance-v1"
PUBLICATION_READINESS_SCOPE_PREFIX = "publication_readiness"
PUBLICATION_RSS_LIMIT_BYTES = 512 * 1024 * 1024
COMPACT_CODE_SAMPLE_LIMIT = 20


@dataclass(frozen=True)
class PublicationReadinessProfile:
    name: str
    max_codes: int
    cadence_minutes: int


@dataclass(frozen=True)
class PublicationReadinessPreflight:
    due: bool
    reason: str
    trade_date: date
    decision_cutoff: datetime
    profile: str | None = None
    cadence_minutes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "due": self.due,
            "reason": self.reason,
            "trade_date": self.trade_date.isoformat(),
            "decision_cutoff": self.decision_cutoff.isoformat(),
            "profile": self.profile,
            "cadence_minutes": self.cadence_minutes,
        }


CONSERVATIVE_PUBLICATION_PROFILE = PublicationReadinessProfile(
    name="conservative",
    max_codes=10,
    cadence_minutes=5,
)
MAXIMUM_PUBLICATION_PROFILE = PublicationReadinessProfile(
    name="maximum",
    max_codes=20,
    cadence_minutes=2,
)


def choose_publication_profile(
    evidence: list[Mapping[str, Any]],
) -> PublicationReadinessProfile:
    recent = evidence[:3]
    if len(recent) < 3:
        return CONSERVATIVE_PUBLICATION_PROFILE
    elapsed_values: list[float] = []
    for details in recent:
        elapsed = float(details.get("elapsed_seconds") or 0.0)
        rss = int(
            details.get("slice_peak_current_rss_bytes")
            or details.get("peak_rss_bytes")
            or 0
        )
        stop_reason = details.get("stop_reason")
        # A factually unseasoned ETF is still durable progress once its exact
        # availability/cooldown checkpoint has been committed. Requiring a
        # fully completed code here unnecessarily throttles healthy slices
        # that are walking past newly listed ETFs.
        checkpoint_healthy = bool(
            details.get("last_completed_code") or details.get("active_code")
        )
        provider_health = details.get("provider_health")
        providers = (
            provider_health.get("providers")
            if isinstance(provider_health, Mapping)
            else {}
        )
        if not isinstance(providers, Mapping):
            providers = {}
        provider_degraded = any(
            state.get("circuit_state") != "closed"
            or state.get("last_error") is not None
            for state in providers.values()
            if isinstance(state, Mapping)
        )
        if (
            elapsed <= 0
            or elapsed >= 30
            or rss <= 0
            or rss >= PUBLICATION_RSS_LIMIT_BYTES
            or stop_reason
            in {
                "rss_limit",
                "current_rss_unavailable",
                "worker_deadline",
                "process_deadline",
                "provider_circuit_open",
            }
            or not checkpoint_healthy
            or provider_degraded
        ):
            return CONSERVATIVE_PUBLICATION_PROFILE
        elapsed_values.append(elapsed)
    p95_index = max(0, int(round(0.95 * (len(elapsed_values) - 1))))
    if sorted(elapsed_values)[p95_index] >= 30:
        return CONSERVATIVE_PUBLICATION_PROFILE
    return MAXIMUM_PUBLICATION_PROFILE


async def _recent_publication_slices(
    session: AsyncSession,
    *,
    trade_date: date,
) -> list[JobRun]:
    prefix = f"etf_history_continuation:{PUBLICATION_READINESS_SCOPE_PREFIX}:{trade_date.isoformat()}"
    return list(
        (
            await session.scalars(
                select(JobRun)
                .where(JobRun.job_name == prefix)
                .order_by(JobRun.id.desc())
                .limit(3)
            )
        ).all()
    )


def _profile_cadence_due(
    runs: list[JobRun],
    *,
    profile: PublicationReadinessProfile,
    now: datetime,
) -> bool:
    if not runs:
        return True
    latest = runs[0]
    return latest.started_at <= now - timedelta(minutes=profile.cadence_minutes)


def build_publication_readiness_request(
    *,
    profile: PublicationReadinessProfile,
    trade_date: date,
    from_date: date,
    contract_hash: str,
    universe_hash: str,
    eligible_codes: tuple[str, ...],
    priority_codes: tuple[str, ...] = (),
) -> BoundedHistorySyncRequest:
    required_trade_dates = tuple(
        current
        for ordinal in range(from_date.toordinal(), trade_date.toordinal() + 1)
        if is_etf_exchange_trading_day(current := date.fromordinal(ordinal))
    )[-SCORE_WARMUP_SESSIONS:]
    return BoundedHistorySyncRequest(
        scope=f"{PUBLICATION_READINESS_SCOPE_PREFIX}:{trade_date.isoformat()}",
        contract_hash=contract_hash,
        universe_hash=universe_hash,
        eligible_codes=tuple(sorted(set(eligible_codes))),
        from_date=from_date,
        to_date=trade_date,
        required_sessions=SCORE_WARMUP_SESSIONS,
        max_codes=profile.max_codes,
        page_size=500,
        max_rows=5_000,
        admission_deadline_seconds=45.0,
        worker_deadline_seconds=55.0,
        process_deadline_seconds=60.0,
        rss_limit_bytes=PUBLICATION_RSS_LIMIT_BYTES,
        provider_timeout_seconds=6.0,
        target_trade_date=trade_date,
        selection_policy="publication_readiness",
        provider_policy_version=PUBLICATION_PROVIDER_POLICY_VERSION,
        adjustment_contract=PUBLICATION_ADJUSTMENT_CONTRACT,
        price_basis="total_return_adjusted",
        required_trade_dates=required_trade_dates,
        priority_codes=tuple(sorted(set(priority_codes))),
    )


def publication_window_start(trade_date: date) -> date:
    sessions: list[date] = []
    current = trade_date
    while len(sessions) < SCORE_WARMUP_SESSIONS:
        if is_etf_exchange_trading_day(current):
            sessions.append(current)
        current -= timedelta(days=1)
    return sessions[-1] - timedelta(days=7)


def _compact_lane(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    lane = raw or {}
    pending = lane.get("pending_codes")
    samples = list(pending[:COMPACT_CODE_SAMPLE_LIMIT]) if isinstance(pending, list) else []
    excluded_count = int(lane.get("excluded_count") or 0)
    return {
        "scope": lane.get("scope"),
        "required_sessions": lane.get("required_sessions"),
        "available_session_count": int(lane.get("available_session_count") or 0),
        "expected_count": int(lane.get("expected_count") or 0),
        "attempted_count": int(lane.get("attempted_count") or 0),
        "covered_count": int(lane.get("covered_count") or 0),
        "excluded_count": excluded_count,
        "coverage_ratio": float(lane.get("coverage_ratio") or 0.0),
        "reason_aggregates": {
            "missing_decision_eligible_total_return_adjusted": excluded_count
        },
        "pending_samples": samples,
    }


def compact_readiness_payload(readiness: Mapping[str, Any]) -> dict[str, Any]:
    universe = readiness.get("universe")
    universe_payload = universe if isinstance(universe, Mapping) else {}
    return {
        "target_date": readiness.get("target_date"),
        "universe": {
            "snapshot_hash": universe_payload.get("snapshot_hash"),
            "expected_count": int(universe_payload.get("expected_count") or 0),
        },
        "daily_freshness": _compact_lane(
            readiness.get("daily_freshness")
            if isinstance(readiness.get("daily_freshness"), Mapping)
            else None
        ),
        "history_depth_61": _compact_lane(
            readiness.get("history_depth_61")
            if isinstance(readiness.get("history_depth_61"), Mapping)
            else None
        ),
        "contract_depth": _compact_lane(
            readiness.get("contract_depth")
            if isinstance(readiness.get("contract_depth"), Mapping)
            else None
        ),
        "telemetry_depth_180": _compact_lane(
            readiness.get("telemetry_depth_180")
            if isinstance(readiness.get("telemetry_depth_180"), Mapping)
            else None
        ),
        "telemetry_depth_500": _compact_lane(
            readiness.get("telemetry_depth_500")
            if isinstance(readiness.get("telemetry_depth_500"), Mapping)
            else None
        ),
        "history_publication_gate_passed": bool(
            readiness.get("history_publication_gate_passed")
        ),
        "complete_publication_gate_passed": bool(
            readiness.get("complete_publication_gate_passed")
        ),
        "publication_coverage_thresholds": dict(
            readiness.get("publication_coverage_thresholds") or {}
        ),
        "readiness_policy": dict(readiness.get("readiness_policy") or {}),
        "readiness_policy_version": readiness.get("readiness_policy_version"),
        "coverage_policy_mode": readiness.get("coverage_policy_mode"),
        "blockers": list(readiness.get("blockers") or ()),
    }


def compact_sync_result(
    result: BoundedHistorySyncResult,
    *,
    request: BoundedHistorySyncRequest,
) -> dict[str, Any]:
    checkpoint = result.last_durable_checkpoint or {}
    return {
        "profile": "maximum" if request.max_codes > 10 else "conservative",
        "identity_hash": request.identity_hash,
        "target_date": (
            request.target_trade_date.isoformat() if request.target_trade_date else None
        ),
        "status": result.status,
        "stop_reason": result.stop_reason,
        "attempted_count": len(result.attempted_codes),
        "completed_count": len(result.completed_codes),
        "failed_count": len(result.exclusions),
        "attempted_samples": list(result.attempted_codes[:COMPACT_CODE_SAMPLE_LIMIT]),
        "completed_samples": list(result.completed_codes[:COMPACT_CODE_SAMPLE_LIMIT]),
        "exclusion_samples": [
            list(item) for item in result.exclusions[:COMPACT_CODE_SAMPLE_LIMIT]
        ],
        "fetched_rows": result.fetched_rows,
        "persisted_rows": result.persisted_rows,
        "elapsed_seconds": result.elapsed_seconds,
        "peak_rss_bytes": result.peak_rss_bytes,
        "rss_limit_bytes": result.rss_limit_bytes,
        "configured_rss_limit_bytes": result.configured_rss_limit_bytes,
        "baseline_rss_bytes": result.baseline_rss_bytes,
        "current_rss_bytes": result.current_rss_bytes,
        "slice_peak_current_rss_bytes": result.slice_peak_current_rss_bytes,
        "lifetime_peak_rss_bytes": result.lifetime_peak_rss_bytes,
        "rss_delta_bytes": result.rss_delta_bytes,
        "rows_per_second": round(
            result.persisted_rows / max(result.elapsed_seconds, 0.000001),
            3,
        ),
        "max_page_rows": result.max_page_rows,
        "max_page_sql_statements": result.max_page_sql_statements,
        "provider_health": checkpoint.get("provider_health"),
        "checkpoint": {
            "active_code": checkpoint.get("active_code"),
            "last_trade_date": checkpoint.get("last_trade_date"),
        },
    }


async def read_publication_readiness_status(
    session: AsyncSession,
    *,
    target_date: date | None = None,
) -> dict[str, Any]:
    effective_date = target_date or date.today()
    readiness = await read_etf_history_readiness(
        session,
        target_date=effective_date,
    )
    runs = await _recent_publication_slices(session, trade_date=effective_date)
    profile = choose_publication_profile(
        [dict(run.details_json or {}) for run in runs]
    )
    latest = runs[0] if runs else None
    details = dict(latest.details_json or {}) if latest is not None else {}
    attempted = details.get("attempted_codes")
    completed = details.get("completed_codes")
    exclusions = details.get("exclusions")
    checkpoint_age_seconds = (
        max(0.0, (datetime.utcnow() - latest.started_at).total_seconds())
        if latest is not None
        else None
    )
    remaining_candidate_count = details.get("remaining_candidate_count")
    remaining_count = (
        int(remaining_candidate_count)
        if isinstance(remaining_candidate_count, int | float)
        and remaining_candidate_count >= 0
        else None
    )
    estimated_remaining_slices = (
        (remaining_count + profile.max_codes - 1) // profile.max_codes
        if remaining_count is not None
        else None
    )
    return {
        **compact_readiness_payload(readiness),
        "synchronization": {
            "profile": profile.name,
            "max_codes": profile.max_codes,
            "cadence_minutes": profile.cadence_minutes,
            "identity_hash": details.get("identity_hash"),
            "status": latest.status if latest is not None else "not_started",
            "stop_reason": details.get("stop_reason"),
            "attempted_count": len(attempted) if isinstance(attempted, list) else 0,
            "completed_count": len(completed) if isinstance(completed, list) else 0,
            "failed_count": len(exclusions) if isinstance(exclusions, list) else 0,
            "remaining_candidate_count": remaining_count,
            "estimated_remaining_slices": estimated_remaining_slices,
            "estimated_eta_minutes": (
                estimated_remaining_slices * profile.cadence_minutes
                if estimated_remaining_slices is not None
                else None
            ),
            "lease": {
                "active": latest is not None and latest.status == "running",
                "checkpoint_age_seconds": checkpoint_age_seconds,
            },
            "checkpoint": {
                "active_code": details.get("active_code"),
                "last_completed_code": details.get("last_completed_code"),
                "last_trade_date": details.get("last_trade_date"),
            },
            "provider_health": details.get("provider_health"),
            "elapsed_seconds": details.get("elapsed_seconds"),
            "peak_rss_bytes": details.get("peak_rss_bytes"),
            "rss_limit_bytes": details.get("rss_limit_bytes"),
            "configured_rss_limit_bytes": details.get(
                "configured_rss_limit_bytes"
            ),
            "baseline_rss_bytes": details.get("baseline_rss_bytes"),
            "current_rss_bytes": details.get("current_rss_bytes"),
            "slice_peak_current_rss_bytes": details.get(
                "slice_peak_current_rss_bytes"
            ),
            "lifetime_peak_rss_bytes": details.get("lifetime_peak_rss_bytes"),
            "rss_delta_bytes": details.get("rss_delta_bytes"),
            "rows_per_second": details.get("rows_per_second"),
            "checkpoint_age_seconds": checkpoint_age_seconds,
        },
    }


def _signal_result(run: Any) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "status": run.status,
        "as_of_date": run.as_of_date.isoformat(),
        "items": int((run.summary_json or {}).get("item_count", 0)),
        "funds": int((run.summary_json or {}).get("fund_count", 0)),
        "etfs": int((run.summary_json or {}).get("etf_count", 0)),
    }


def _waiting(
    trade_date: date,
    reason: str,
    *,
    readiness: dict[str, Any] | None = None,
    sync: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "asset_type": ASSET_TYPE_ETF,
        "as_of_date": trade_date.isoformat(),
        "status": "waiting",
        "publication_state": "not_run",
        "reason": reason,
        "job_status": "partial",
        "job_message": reason,
        **({"coverage": readiness} if readiness is not None else {}),
        **({"sync": sync} if sync is not None else {}),
    }


async def _latest_authoritative_universe(
    session: AsyncSession,
    *,
    trade_date: date,
) -> tuple[bool, str | None]:
    run = await session.scalar(
        select(JobRun)
        .where(JobRun.job_name == "daily_etf_universe")
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .limit(1)
    )
    if run is None:
        return False, "missing_daily_universe_refresh"
    details = dict(run.details_json or {})
    universe = details.get("universe")
    universe_details = universe if isinstance(universe, dict) else details
    observed_date = details.get("as_of_date") or universe_details.get("as_of_date")
    if observed_date != trade_date.isoformat():
        return False, "stale_daily_universe_refresh"
    if run.status != "success" or universe_details.get("authoritative") is not True:
        return False, str(
            universe_details.get("discovery_error") or "universe_not_authoritative"
        )
    return True, None


def _readiness_policy(
    readiness: Mapping[str, Any],
) -> EtfReadinessPolicyResult:
    daily = readiness.get("daily_freshness") or {}
    warmup = readiness.get("history_depth_61") or {}
    return evaluate_etf_readiness(
        daily_coverage_ratio=float(daily.get("coverage_ratio") or 0.0),
        warmup_coverage_ratio=float(warmup.get("coverage_ratio") or 0.0),
    )


def _both_gates_pass(readiness: Mapping[str, Any]) -> bool:
    return _readiness_policy(readiness).complete_publication_allowed


async def preflight_post_close_etf_publication_readiness(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
    now: datetime | None = None,
) -> PublicationReadinessPreflight:
    authoritative, _universe_error = await _latest_authoritative_universe(
        session,
        trade_date=trade_date,
    )
    if not authoritative:
        return PublicationReadinessPreflight(
            due=False,
            reason="universe_not_authoritative",
            trade_date=trade_date,
            decision_cutoff=decision_cutoff,
        )
    current = await resolve_current_etf_ranking_surface_snapshot(
        session,
        required_trade_date=trade_date,
        ranking_surface="research",
    )
    if current.state == "ready":
        return PublicationReadinessPreflight(
            due=False,
            reason="complete_snapshot_already_published",
            trade_date=trade_date,
            decision_cutoff=decision_cutoff,
        )
    effective_now = now or datetime.utcnow()
    lock = await session.get(EtfDailyWorkflowLock, trade_date)
    if (
        lock is not None
        and lock.status == ETF_DAILY_WORKFLOW_RUNNING
        and lock.started_at is not None
        and lock.started_at >= effective_now - ETF_DAILY_WORKFLOW_LOCK_LEASE
    ):
        return PublicationReadinessPreflight(
            due=False,
            reason="publication_readiness_worker_locked",
            trade_date=trade_date,
            decision_cutoff=decision_cutoff,
        )
    recent_slices = await _recent_publication_slices(
        session,
        trade_date=trade_date,
    )
    profile = choose_publication_profile(
        [dict(run.details_json or {}) for run in recent_slices]
    )
    if not _profile_cadence_due(
        recent_slices,
        profile=profile,
        now=effective_now,
    ):
        return PublicationReadinessPreflight(
            due=False,
            reason="catch_up_cadence_not_due",
            trade_date=trade_date,
            decision_cutoff=decision_cutoff,
            profile=profile.name,
            cadence_minutes=profile.cadence_minutes,
        )
    return PublicationReadinessPreflight(
        due=True,
        reason="publication_readiness_due",
        trade_date=trade_date,
        decision_cutoff=decision_cutoff,
        profile=profile.name,
        cadence_minutes=profile.cadence_minutes,
    )


async def run_post_close_etf_publication_readiness(
    session: AsyncSession,
    *,
    trade_date: date,
    decision_cutoff: datetime,
) -> dict[str, Any]:
    authoritative, universe_error = await _latest_authoritative_universe(
        session,
        trade_date=trade_date,
    )
    if not authoritative:
        return _waiting(
            trade_date,
            "universe_not_authoritative",
            sync={"error": universe_error},
        )
    if not await try_acquire_etf_daily_workflow_lock(session, trade_date):
        return _waiting(trade_date, "publication_readiness_worker_locked")

    result: dict[str, Any]
    try:
        current = await resolve_current_etf_ranking_surface_snapshot(
            session,
            required_trade_date=trade_date,
            ranking_surface="research",
        )
        if current.state == "ready" and current.run is not None:
            signal = _signal_result(current.run)
            result = {
                "asset_type": ASSET_TYPE_ETF,
                "publication_state": "published",
                "already_published": True,
                "etf": signal,
                **signal,
            }
            return result

        recent_slices = await _recent_publication_slices(
            session,
            trade_date=trade_date,
        )
        profile = choose_publication_profile(
            [dict(run.details_json or {}) for run in recent_slices]
        )
        if not _profile_cadence_due(
            recent_slices,
            profile=profile,
            now=datetime.utcnow(),
        ):
            result = {
                "asset_type": ASSET_TYPE_ETF,
                "as_of_date": trade_date.isoformat(),
                "status": "skipped",
                "publication_state": "not_run",
                "reason": "catch_up_cadence_not_due",
                "profile": profile.name,
                "cadence_minutes": profile.cadence_minutes,
            }
            return result

        readiness = await read_etf_history_readiness(
            session,
            target_date=trade_date,
        )
        compact_before = compact_readiness_payload(readiness)
        universe = readiness.get("universe") or {}
        codes = tuple(str(code) for code in (universe.get("codes") or ()))
        universe_hash = str(universe.get("snapshot_hash") or "")
        if not codes or len(universe_hash) != 64:
            result = _waiting(
                trade_date,
                "publication_readiness_universe_invalid",
                readiness=compact_before,
            )
            return result

        sync_payload: dict[str, Any] | None = None
        if not _both_gates_pass(readiness):
            priority_codes = tuple(
                await active_tracked_etf_codes(session, list(codes))
            )
            request = build_publication_readiness_request(
                profile=profile,
                trade_date=trade_date,
                from_date=publication_window_start(trade_date),
                contract_hash=str(readiness["contract_hash"]),
                universe_hash=universe_hash,
                eligible_codes=codes,
                priority_codes=priority_codes,
            )
            restored = await read_latest_compatible_provider_health(
                session,
                request=request,
            )
            async with PublicationAdjustedHistoryFetcher(
                attempt_timeout_seconds=request.provider_timeout_seconds,
                minimum_eligible_rows=request.required_sessions,
                restored_health=restored.get("providers")
                if isinstance(restored.get("providers"), Mapping)
                else None,
            ) as fetcher:
                sync_result = await run_bounded_history_sync_slice(
                    session,
                    request=request,
                    fetcher=fetcher,
                )
            sync_payload = compact_sync_result(sync_result, request=request)
            readiness = await read_etf_history_readiness(
                session,
                target_date=trade_date,
            )

        compact_after = compact_readiness_payload(readiness)
        readiness_policy = _readiness_policy(readiness)
        if readiness_policy.state == "blocked":
            result = _waiting(
                trade_date,
                "adjusted_price_or_warmup_coverage_below_publication_gate",
                readiness=compact_after,
                sync=sync_payload,
            )
            return result

        source_cutoff = etf_source_availability_cutoff(trade_date)
        if readiness_policy.state == "degraded":
            try:
                preview = await generate_provisional_etf_research_preview(
                    session,
                    trade_date=trade_date,
                    decision_cutoff=decision_cutoff,
                    source_availability_cutoff=source_cutoff,
                )
            except (SnapshotMaterializationError, SnapshotPublicationError) as exc:
                await session.rollback()
                result = _waiting(
                    trade_date,
                    f"provisional_research_materialization_failed: {exc}",
                    readiness=compact_after,
                    sync=sync_payload,
                )
                return result
            signal = _signal_result(preview)
            result = {
                "asset_type": ASSET_TYPE_ETF,
                "status": "success",
                "publication_state": "provisional",
                "snapshot_state": "provisional",
                "readiness_state": readiness_policy.state,
                "readiness_policy_version": readiness_policy.policy_version,
                "coverage": compact_after,
                **({"sync": sync_payload} if sync_payload is not None else {}),
                "etf": signal,
                **signal,
            }
            return result

        coverage = await build_etf_coverage_barrier(
            session,
            as_of_trade_date=trade_date,
            data_cutoff=source_cutoff,
        )
        if (
            not coverage.expected_codes
            or coverage.coverage_ratio < ETF_DAILY_DECISION_MIN_COVERAGE
        ):
            result = _waiting(
                trade_date,
                "final_publication_validation_coverage_failed",
                readiness=compact_after,
                sync=sync_payload,
            )
            return result
        try:
            published = await generate_and_publish_etf_snapshot(
                session,
                trade_date=trade_date,
                decision_cutoff=decision_cutoff,
                source_availability_cutoff=source_cutoff,
            )
        except SnapshotPublicationError as exc:
            await session.rollback()
            result = _waiting(
                trade_date,
                f"score_coverage_or_publication_gate_failed: {exc}",
                readiness=compact_after,
                sync=sync_payload,
            )
            return result
        signal = _signal_result(published)
        result = {
            "asset_type": ASSET_TYPE_ETF,
            "publication_state": published.publication_state,
            "coverage": compact_after,
            **({"sync": sync_payload} if sync_payload is not None else {}),
            "etf": signal,
            **signal,
        }
        return result
    except Exception:
        # A provider or SQL failure can leave PostgreSQL's transaction in the
        # aborted state. Roll it back before the lock finalizer performs its
        # own SELECT/COMMIT, otherwise the finalizer masks the original error
        # and leaves a stale "running" lock behind.
        await session.rollback()
        raise
    finally:
        final_status = "success" if "result" in locals() else "failed"
        await finish_etf_daily_workflow_lock(
            session,
            trade_date,
            final_status,
            result if "result" in locals() else {"trade_date": trade_date.isoformat()},
        )


__all__ = [
    "CONSERVATIVE_PUBLICATION_PROFILE",
    "MAXIMUM_PUBLICATION_PROFILE",
    "build_publication_readiness_request",
    "compact_readiness_payload",
    "compact_sync_result",
    "choose_publication_profile",
    "publication_window_start",
    "preflight_post_close_etf_publication_readiness",
    "read_publication_readiness_status",
    "run_post_close_etf_publication_readiness",
]
