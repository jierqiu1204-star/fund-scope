from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfListingDateObservation,
    EtfPriceHistory,
    JobRun,
    ShortResearchSignalRun,
)
from app.services.market_data import (
    ASIA_SHANGHAI,
    etf_decision_adjusted_provider_versions,
)
from app.services.short_research.coverage_policy import (
    ETF_COMPLETE_SCORE_COVERAGE,
    ETF_DAILY_DECISION_MIN_COVERAGE,
    ETF_RESEARCH_DEPTH_MIN_COVERAGE,
    ETF_SCORE_PUBLICATION_MIN_COVERAGE,
    evaluate_etf_readiness,
)
from app.services.short_research.history_readiness import (
    DAILY_FRESHNESS_SCOPE,
    DEEP_TELEMETRY_DEPTH_SCOPE,
    DEEP_TELEMETRY_DEPTH_SESSIONS,
    SCORE_WARMUP_SCOPE,
    SCORE_WARMUP_SESSIONS,
    TELEMETRY_DEPTH_SCOPE,
    TELEMETRY_DEPTH_SESSIONS,
    derived_replay_depth_sessions,
    history_depth_scope,
)
from app.services.short_research.ranking_contract import (
    canonical_hash,
    final_score_v3_contract,
)
from app.services.short_research.universe import build_point_in_time_universe_snapshot

DEFAULT_HISTORY_HORIZONS = (1, 3, 5, 10)


def current_etf_history_contract_hash(*, horizons: Sequence[int]) -> str:
    return canonical_hash(
        {
            "schema_version": "etf_history_depth_contract_v1",
            "ranking_contract": final_score_v3_contract(),
            "horizons": tuple(sorted(set(horizons))),
        }
    )


async def _latest_attempted_codes(
    session: AsyncSession,
    *,
    scope: str,
) -> tuple[str, ...]:
    run = await session.scalar(
        select(JobRun)
        .where(JobRun.job_name == f"etf_history_continuation:{scope}")
        .order_by(JobRun.id.desc())
        .limit(1)
    )
    attempted = (run.details_json or {}).get("attempted_codes") if run is not None else None
    if not isinstance(attempted, list):
        return ()
    return tuple(str(code) for code in attempted)


async def _continuation_health_by_scope(
    session: AsyncSession,
    *,
    scopes: Sequence[str],
) -> dict[str, dict[str, Any]]:
    job_names = tuple(f"etf_history_continuation:{scope}" for scope in scopes)
    latest = (
        select(JobRun.job_name, func.max(JobRun.id).label("latest_id"))
        .where(JobRun.job_name.in_(job_names))
        .group_by(JobRun.job_name)
        .subquery()
    )
    runs = (
        await session.scalars(
            select(JobRun).join(latest, JobRun.id == latest.c.latest_id)
        )
    ).all()
    by_name = {run.job_name: run for run in runs}
    health: dict[str, dict[str, Any]] = {}
    for scope in scopes:
        run = by_name.get(f"etf_history_continuation:{scope}")
        if run is None:
            health[scope] = {
                "status": "not_started",
                "stop_reason": None,
                "checkpoint_identity_hash": None,
                "last_completed_code": None,
                "last_trade_date": None,
                "remaining_candidate_count": None,
                "elapsed_seconds": None,
                "peak_rss_bytes": None,
                "rss_limit_bytes": None,
                "configured_rss_limit_bytes": None,
                "baseline_rss_bytes": None,
                "current_rss_bytes": None,
                "slice_peak_current_rss_bytes": None,
                "lifetime_peak_rss_bytes": None,
                "rss_delta_bytes": None,
                "rows_per_second": None,
                "sql_statements": None,
                "retries": None,
                "provider_attempt_count": None,
                "circuit_state": "unknown",
                "exclusions": [],
            }
            continue
        details = run.details_json or {}
        health[scope] = {
            "status": run.status,
            "stop_reason": details.get("stop_reason") or run.error_message,
            "checkpoint_identity_hash": details.get("identity_hash"),
            "last_completed_code": details.get("last_completed_code"),
            "last_trade_date": details.get("last_trade_date"),
            "remaining_candidate_count": details.get("remaining_candidate_count"),
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
            "sql_statements": details.get("sql_statements"),
            "retries": details.get("retries"),
            "provider_attempt_count": details.get("provider_attempt_count"),
            "circuit_state": details.get("circuit_state", "unknown"),
            "exclusions": details.get("exclusions") or [],
        }
    return health


def _lane_payload(
    *,
    scope: str,
    required_sessions: int,
    authoritative: bool,
    codes: tuple[str, ...],
    depth_by_code: dict[str, int],
    attempted_codes: tuple[str, ...],
    available_session_count: int,
) -> dict[str, Any]:
    covered = tuple(
        code for code in codes if depth_by_code.get(code, 0) >= required_sessions
    )
    covered_set = set(covered)
    pending = tuple(code for code in codes if code not in covered_set)
    expected_count = len(codes)
    covered_count = len(covered)
    attempted_set = set(attempted_codes)
    return {
        "scope": scope,
        "required_sessions": required_sessions,
        "available_session_count": available_session_count,
        "authoritative": authoritative,
        "denominator_kind": "full_authoritative_universe",
        "expected_count": expected_count,
        "attempted_count": sum(code in attempted_set for code in codes),
        "covered_count": covered_count,
        "eligible_count": covered_count,
        "excluded_count": expected_count - covered_count,
        "coverage_ratio": covered_count / expected_count if expected_count else 0.0,
        "covered_codes": list(covered),
        "pending_codes": list(pending),
    }


def _research_lane_payload(
    *,
    scope: str,
    required_sessions: int,
    authoritative: bool,
    full_universe_codes: tuple[str, ...],
    listing_metadata_by_code: dict[str, dict[str, Any] | None],
    universe_snapshot_hash: str,
    required_trade_dates: tuple[date, ...],
    depth_by_code: dict[str, int],
    attempted_codes: tuple[str, ...],
) -> dict[str, Any]:
    calendar_complete = len(required_trade_dates) == required_sessions
    first_required_session = required_trade_dates[0] if calendar_complete else None
    known_listing_codes = tuple(
        code for code in full_universe_codes if listing_metadata_by_code.get(code) is not None
    )
    unknown_listing_codes = tuple(
        code for code in full_universe_codes if listing_metadata_by_code.get(code) is None
    )
    seasoned_codes = (
        tuple(
            code
            for code in known_listing_codes
            if listing_metadata_by_code[code]["listing_date"] <= first_required_session
        )
        if first_required_session is not None
        else ()
    )
    structurally_unseasoned_codes = (
        tuple(
            code
            for code in known_listing_codes
            if listing_metadata_by_code[code]["listing_date"] > first_required_session
        )
        if first_required_session is not None
        else ()
    )
    full_count = len(full_universe_codes)
    metadata_ratio = len(known_listing_codes) / full_count if full_count else 0.0
    metadata_gate_passed = metadata_ratio >= ETF_RESEARCH_DEPTH_MIN_COVERAGE
    session_calendar_hash = canonical_hash(
        [item.isoformat() for item in required_trade_dates]
    )
    cohort_evidence = [
        {
            "code": code,
            **dict(listing_metadata_by_code[code] or {}),
        }
        for code in seasoned_codes
    ]
    exclusions = [
        {"code": code, "reason": "unknown_listing_metadata"}
        for code in unknown_listing_codes
    ] + [
        {
            "code": code,
            "reason": "structurally_unseasoned",
            **dict(listing_metadata_by_code[code] or {}),
        }
        for code in structurally_unseasoned_codes
    ]
    cohort_hash = canonical_hash(
        {
            "universe_snapshot_hash": universe_snapshot_hash,
            "session_calendar_hash": session_calendar_hash,
            "members": cohort_evidence,
        }
    )
    payload = _lane_payload(
        scope=scope,
        required_sessions=required_sessions,
        authoritative=authoritative,
        codes=seasoned_codes,
        depth_by_code=depth_by_code,
        attempted_codes=attempted_codes,
        available_session_count=len(required_trade_dates),
    )
    blockers: list[str] = []
    if not calendar_complete:
        blockers.append("observed_session_calendar_incomplete")
    if not metadata_gate_passed:
        blockers.append("authoritative_listing_metadata_below_95pct")
    if not seasoned_codes:
        blockers.append("seasoned_research_cohort_empty")
    if payload["coverage_ratio"] < ETF_RESEARCH_DEPTH_MIN_COVERAGE:
        blockers.append("adjusted_research_coverage_below_95pct")
    return {
        **payload,
        "denominator_kind": "authoritative_seasoned_listing_cohort",
        "full_universe_count": full_count,
        "cohort_codes": list(seasoned_codes),
        "cohort_hash": cohort_hash,
        "cohort_evidence_hash": canonical_hash(cohort_evidence),
        "exclusion_hash": canonical_hash(exclusions),
        "first_required_session": (
            first_required_session.isoformat() if first_required_session else None
        ),
        "required_trade_dates": [item.isoformat() for item in required_trade_dates],
        "session_calendar_hash": session_calendar_hash,
        "session_calendar_complete": calendar_complete,
        "listing_metadata_coverage_ratio": metadata_ratio,
        "listing_metadata_gate_passed": metadata_gate_passed,
        "unknown_listing_count": len(unknown_listing_codes),
        "unknown_listing_hash": canonical_hash(list(unknown_listing_codes)),
        "unknown_listing_samples": list(unknown_listing_codes[:20]),
        "structurally_unseasoned_count": len(structurally_unseasoned_codes),
        "structurally_unseasoned_hash": canonical_hash(list(structurally_unseasoned_codes)),
        "structurally_unseasoned_samples": list(structurally_unseasoned_codes[:20]),
        "completion_gate_passed": not blockers,
        "completion_blockers": blockers,
    }


async def _compatible_production_source_date_count(
    session: AsyncSession,
    *,
    effective_date: date,
) -> int:
    count = await session.scalar(
        select(func.count(func.distinct(ShortResearchSignalRun.as_of_trade_date))).where(
            ShortResearchSignalRun.status == "success",
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.scope_kind == "full",
            ShortResearchSignalRun.score_version == "final_score_v3",
            ShortResearchSignalRun.rule_version == "final_score_v3_rule_v2",
            ShortResearchSignalRun.score_field == "ranking_score",
            ShortResearchSignalRun.price_basis == "total_return_adjusted",
            ShortResearchSignalRun.as_of_trade_date <= effective_date,
            ShortResearchSignalRun.ranking_contract_hash.is_not(None),
            ShortResearchSignalRun.universe_snapshot_hash.is_not(None),
            ShortResearchSignalRun.input_snapshot_hash.is_not(None),
            ShortResearchSignalRun.decision_data_coverage_ratio
            >= ETF_DAILY_DECISION_MIN_COVERAGE,
            ShortResearchSignalRun.coverage_ratio
            >= ETF_COMPLETE_SCORE_COVERAGE,
        )
    )
    return int(count or 0)


def _cutoff_utc_naive(
    *,
    effective_date: date,
    data_cutoff: datetime | None,
) -> datetime:
    cutoff = data_cutoff or datetime.combine(
        effective_date,
        time.max,
        tzinfo=ASIA_SHANGHAI,
    )
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=ASIA_SHANGHAI)
    return cutoff.astimezone(UTC).replace(tzinfo=None)


def _accepted_adjusted_provider_filter() -> Any:
    pairs = etf_decision_adjusted_provider_versions()
    return or_(
        *(
            and_(
                EtfPriceHistory.data_provider == provider,
                EtfPriceHistory.provider_version == version,
                EtfPriceHistory.adjustment_version == version,
            )
            for provider, version in pairs
        )
    )


async def read_etf_history_readiness(
    session: AsyncSession,
    *,
    target_date: date | None = None,
    horizons: Sequence[int] = DEFAULT_HISTORY_HORIZONS,
    data_cutoff: datetime | None = None,
) -> dict[str, Any]:
    effective_date = target_date or date.today()
    cutoff_utc = _cutoff_utc_naive(
        effective_date=effective_date,
        data_cutoff=data_cutoff,
    )
    frozen_horizons = tuple(sorted(set(horizons)))
    contract_hash = current_etf_history_contract_hash(horizons=frozen_horizons)
    contract_scope = history_depth_scope(contract_hash)
    contract_required = derived_replay_depth_sessions(horizons=frozen_horizons)
    maximum_required = max(contract_required, DEEP_TELEMETRY_DEPTH_SESSIONS)
    universe = await build_point_in_time_universe_snapshot(
        session,
        as_of_date=effective_date,
    )
    codes = tuple(str(member["asset_code"]) for member in universe.members)

    ranked_listing_observations = (
        select(
            EtfListingDateObservation.etf_code.label("etf_code"),
            EtfListingDateObservation.listing_date.label("listing_date"),
            EtfListingDateObservation.source.label("source"),
            EtfListingDateObservation.provider_version.label("provider_version"),
            EtfListingDateObservation.observed_at.label("observed_at"),
            EtfListingDateObservation.universe_snapshot_hash.label("snapshot_hash"),
            EtfListingDateObservation.raw_payload_hash.label("raw_payload_hash"),
            EtfListingDateObservation.evidence_hash.label("evidence_hash"),
            func.row_number()
            .over(
                partition_by=EtfListingDateObservation.etf_code,
                order_by=(
                    EtfListingDateObservation.observed_at.desc(),
                    EtfListingDateObservation.id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(
            EtfListingDateObservation.etf_code.in_(codes) if codes else false(),
            EtfListingDateObservation.observed_at <= cutoff_utc,
        )
        .subquery()
    )
    listing_rows = (
        await session.execute(
            select(ranked_listing_observations).where(
                ranked_listing_observations.c.rank == 1
            )
        )
    ).mappings().all()
    listing_metadata_by_code: dict[str, dict[str, Any] | None] = {
        code: None for code in codes
    }
    for row in listing_rows:
        listing_metadata_by_code[str(row["etf_code"])] = {
            "listing_date": row["listing_date"],
            "source": str(row["source"]),
            "provider_version": str(row["provider_version"]),
            "observed_at": row["observed_at"],
            "universe_snapshot_hash": str(row["snapshot_hash"]),
            "raw_payload_hash": str(row["raw_payload_hash"]),
            "evidence_hash": str(row["evidence_hash"]),
        }
    known_listing_codes = tuple(
        code for code in codes if listing_metadata_by_code.get(code) is not None
    )
    unknown_listing_codes = tuple(
        code for code in codes if listing_metadata_by_code.get(code) is None
    )
    listing_sources = sorted(
        {
            str(item["source"])
            for item in listing_metadata_by_code.values()
            if item is not None
        }
    )
    observed_times = [
        item["observed_at"]
        for item in listing_metadata_by_code.values()
        if item is not None
    ]

    # Raw daily rows may prove an exchange session existed. Per-code coverage
    # below remains strictly decision-eligible total-return-adjusted data.
    session_dates_result = await session.scalars(
        select(EtfPriceHistory.trade_date)
        .where(
            EtfPriceHistory.etf_code.in_(codes) if codes else false(),
            EtfPriceHistory.trade_date <= effective_date,
            EtfPriceHistory.source_timestamp.is_not(None),
            EtfPriceHistory.source_timestamp <= cutoff_utc,
        )
        .distinct()
        .order_by(EtfPriceHistory.trade_date.desc())
        .limit(maximum_required)
    )
    session_dates = tuple(reversed(session_dates_result.all()))
    warmup_dates = tuple(session_dates[-SCORE_WARMUP_SESSIONS:])
    telemetry_dates = tuple(session_dates[-TELEMETRY_DEPTH_SESSIONS:])
    contract_dates = tuple(session_dates[-contract_required:])
    deep_telemetry_dates = tuple(
        session_dates[-DEEP_TELEMETRY_DEPTH_SESSIONS:]
    )

    daily_by_code = {code: 0 for code in codes}
    warmup_by_code = {code: 0 for code in codes}
    telemetry_by_code = {code: 0 for code in codes}
    contract_by_code = {code: 0 for code in codes}
    deep_telemetry_by_code = {code: 0 for code in codes}
    if codes and session_dates:
        rows = await session.execute(
            select(
                EtfPriceHistory.etf_code,
                func.sum(
                    case((EtfPriceHistory.trade_date == effective_date, 1), else_=0)
                ),
                func.sum(
                    case((EtfPriceHistory.trade_date.in_(warmup_dates), 1), else_=0)
                ),
                func.sum(
                    case((EtfPriceHistory.trade_date.in_(telemetry_dates), 1), else_=0)
                ),
                func.sum(
                    case((EtfPriceHistory.trade_date.in_(contract_dates), 1), else_=0)
                ),
                func.sum(
                    case(
                        (
                            EtfPriceHistory.trade_date.in_(deep_telemetry_dates),
                            1,
                        ),
                        else_=0,
                    )
                ),
            )
            .where(
                EtfPriceHistory.etf_code.in_(codes),
                EtfPriceHistory.trade_date.in_(deep_telemetry_dates),
                EtfPriceHistory.decision_eligible.is_(True),
                EtfPriceHistory.research_price_basis == "total_return_adjusted",
                EtfPriceHistory.source_timestamp.is_not(None),
                EtfPriceHistory.source_timestamp <= cutoff_utc,
                _accepted_adjusted_provider_filter(),
            )
            .group_by(EtfPriceHistory.etf_code)
        )
        for code, daily, warmup, telemetry, contract, deep_telemetry in rows:
            key = str(code)
            daily_by_code[key] = int(daily or 0)
            warmup_by_code[key] = int(warmup or 0)
            telemetry_by_code[key] = int(telemetry or 0)
            contract_by_code[key] = int(contract or 0)
            deep_telemetry_by_code[key] = int(deep_telemetry or 0)

    daily_attempted = await _latest_attempted_codes(session, scope=DAILY_FRESHNESS_SCOPE)
    warmup_attempted = await _latest_attempted_codes(session, scope=SCORE_WARMUP_SCOPE)
    contract_attempted = await _latest_attempted_codes(session, scope=contract_scope)
    deep_telemetry_attempted = await _latest_attempted_codes(
        session,
        scope=DEEP_TELEMETRY_DEPTH_SCOPE,
    )
    daily = _lane_payload(
        scope=DAILY_FRESHNESS_SCOPE,
        required_sessions=1,
        authoritative=True,
        codes=codes,
        depth_by_code=daily_by_code,
        attempted_codes=daily_attempted,
        available_session_count=1 if effective_date in set(session_dates) else 0,
    )
    warmup = _lane_payload(
        scope=SCORE_WARMUP_SCOPE,
        required_sessions=SCORE_WARMUP_SESSIONS,
        authoritative=True,
        codes=codes,
        depth_by_code=warmup_by_code,
        attempted_codes=warmup_attempted,
        available_session_count=len(warmup_dates),
    )
    contract = _research_lane_payload(
        scope=contract_scope,
        required_sessions=contract_required,
        authoritative=True,
        full_universe_codes=codes,
        listing_metadata_by_code=listing_metadata_by_code,
        universe_snapshot_hash=universe.universe_snapshot_hash,
        required_trade_dates=contract_dates,
        depth_by_code=contract_by_code,
        attempted_codes=contract_attempted,
    )
    telemetry = _lane_payload(
        scope=TELEMETRY_DEPTH_SCOPE,
        required_sessions=TELEMETRY_DEPTH_SESSIONS,
        authoritative=False,
        codes=codes,
        depth_by_code=telemetry_by_code,
        attempted_codes=(),
        available_session_count=len(telemetry_dates),
    )
    deep_telemetry = _research_lane_payload(
        scope=DEEP_TELEMETRY_DEPTH_SCOPE,
        required_sessions=DEEP_TELEMETRY_DEPTH_SESSIONS,
        authoritative=False,
        full_universe_codes=codes,
        listing_metadata_by_code=listing_metadata_by_code,
        universe_snapshot_hash=universe.universe_snapshot_hash,
        required_trade_dates=deep_telemetry_dates,
        depth_by_code=deep_telemetry_by_code,
        attempted_codes=deep_telemetry_attempted,
    )
    source_date_count = await _compatible_production_source_date_count(
        session,
        effective_date=effective_date,
    )
    continuation_health = await _continuation_health_by_scope(
        session,
        scopes=(
            DAILY_FRESHNESS_SCOPE,
            SCORE_WARMUP_SCOPE,
            contract_scope,
            DEEP_TELEMETRY_DEPTH_SCOPE,
        ),
    )
    readiness_policy = evaluate_etf_readiness(
        daily_coverage_ratio=daily["coverage_ratio"],
        warmup_coverage_ratio=warmup["coverage_ratio"],
    )
    blockers = list(readiness_policy.blocker_reasons)
    if source_date_count < 20:
        blockers.append("compatible_production_source_dates_below_20")
    listing_metadata_ratio = len(known_listing_codes) / len(codes) if codes else 0.0
    return {
        "target_date": effective_date.isoformat(),
        "data_cutoff_utc": cutoff_utc.isoformat(),
        "contract_hash": contract_hash,
        "horizons": list(frozen_horizons),
        "universe": {
            "source": "ranking_point_in_time_snapshot",
            "snapshot_hash": universe.universe_snapshot_hash,
            "expected_count": len(codes),
            "codes": list(codes),
        },
        "observed_session_calendar": {
            "source": "etf_price_history_observed_dates_all_price_bases",
            "session_count": len(session_dates),
            "session_hash": canonical_hash([item.isoformat() for item in session_dates]),
            "dates": [item.isoformat() for item in session_dates],
            "raw_dates_allowed": True,
            "raw_rows_count_as_adjusted_coverage": False,
        },
        "listing_metadata": {
            "source_kind": "authoritative_universe_provider_observation",
            "sources": listing_sources,
            "expected_count": len(codes),
            "observed_count": len(known_listing_codes),
            "coverage_ratio": listing_metadata_ratio,
            "required_coverage_ratio": ETF_RESEARCH_DEPTH_MIN_COVERAGE,
            "gate_passed": (listing_metadata_ratio >= ETF_RESEARCH_DEPTH_MIN_COVERAGE),
            "unknown_count": len(unknown_listing_codes),
            "unknown_hash": canonical_hash(list(unknown_listing_codes)),
            "unknown_samples": list(unknown_listing_codes[:20]),
            "latest_observed_at": (max(observed_times).isoformat() if observed_times else None),
        },
        "daily_freshness": daily,
        "history_depth_61": warmup,
        "contract_depth": contract,
        "telemetry_depth_180": telemetry,
        "telemetry_depth_500": deep_telemetry,
        "historical_production_snapshots": {
            "compatible_source_date_count": source_date_count,
            "required_source_date_count": 20,
            "ready": source_date_count >= 20,
        },
        "score_eligible_codes": warmup["covered_codes"],
        "synchronization_health": {
            "daily_freshness": continuation_health[DAILY_FRESHNESS_SCOPE],
            "history_depth_61": continuation_health[SCORE_WARMUP_SCOPE],
            "contract_depth": continuation_health[contract_scope],
            "telemetry_depth_500": continuation_health[
                DEEP_TELEMETRY_DEPTH_SCOPE
            ],
        },
        "history_publication_gate_passed": readiness_policy.preview_allowed,
        "complete_publication_gate_passed": (
            readiness_policy.complete_publication_allowed
        ),
        "publication_coverage_threshold": ETF_SCORE_PUBLICATION_MIN_COVERAGE,
        "publication_coverage_thresholds": {
            "daily_freshness": ETF_DAILY_DECISION_MIN_COVERAGE,
            "history_depth_61_preview": ETF_SCORE_PUBLICATION_MIN_COVERAGE,
            "history_depth_61_complete": ETF_COMPLETE_SCORE_COVERAGE,
        },
        "readiness_policy": readiness_policy.to_dict(),
        "readiness_policy_version": readiness_policy.policy_version,
        "coverage_policy_mode": readiness_policy.state,
        "blockers": blockers,
        "research_depth_gate_passed": contract["completion_gate_passed"],
        "research_depth_blockers": contract["completion_blockers"],
    }


__all__ = [
    "DEFAULT_HISTORY_HORIZONS",
    "current_etf_history_contract_hash",
    "read_etf_history_readiness",
]
