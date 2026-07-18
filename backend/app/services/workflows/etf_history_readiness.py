from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfPriceHistory,
    JobRun,
    ShortResearchSignalRun,
)
from app.services.short_research.history_readiness import (
    DAILY_FRESHNESS_SCOPE,
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

PUBLICATION_COVERAGE_THRESHOLD = 0.95
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
        "expected_count": expected_count,
        "attempted_count": sum(code in attempted_set for code in codes),
        "covered_count": covered_count,
        "eligible_count": covered_count,
        "excluded_count": expected_count - covered_count,
        "coverage_ratio": covered_count / expected_count if expected_count else 0.0,
        "covered_codes": list(covered),
        "pending_codes": list(pending),
    }


async def _compatible_production_source_date_count(session: AsyncSession) -> int:
    count = await session.scalar(
        select(func.count(func.distinct(ShortResearchSignalRun.as_of_trade_date))).where(
            ShortResearchSignalRun.status == "success",
            ShortResearchSignalRun.publication_state == "published",
            ShortResearchSignalRun.scope_kind == "full",
            ShortResearchSignalRun.score_version == "final_score_v3",
            ShortResearchSignalRun.rule_version == "final_score_v3_rule_v2",
            ShortResearchSignalRun.score_field == "ranking_score",
            ShortResearchSignalRun.price_basis == "total_return_adjusted",
            ShortResearchSignalRun.ranking_contract_hash.is_not(None),
            ShortResearchSignalRun.universe_snapshot_hash.is_not(None),
            ShortResearchSignalRun.input_snapshot_hash.is_not(None),
            ShortResearchSignalRun.decision_data_coverage_ratio
            >= PUBLICATION_COVERAGE_THRESHOLD,
            ShortResearchSignalRun.coverage_ratio >= PUBLICATION_COVERAGE_THRESHOLD,
        )
    )
    return int(count or 0)


async def read_etf_history_readiness(
    session: AsyncSession,
    *,
    target_date: date | None = None,
    horizons: Sequence[int] = DEFAULT_HISTORY_HORIZONS,
) -> dict[str, Any]:
    effective_date = target_date or date.today()
    frozen_horizons = tuple(sorted(set(horizons)))
    contract_hash = current_etf_history_contract_hash(horizons=frozen_horizons)
    contract_scope = history_depth_scope(contract_hash)
    contract_required = derived_replay_depth_sessions(horizons=frozen_horizons)
    universe = await build_point_in_time_universe_snapshot(
        session,
        as_of_date=effective_date,
    )
    codes = tuple(str(member["asset_code"]) for member in universe.members)

    session_dates_result = await session.scalars(
        select(EtfPriceHistory.trade_date)
        .where(
            EtfPriceHistory.etf_code.in_(codes) if codes else False,
            EtfPriceHistory.trade_date <= effective_date,
            EtfPriceHistory.decision_eligible.is_(True),
            EtfPriceHistory.research_price_basis == "total_return_adjusted",
        )
        .distinct()
        .order_by(EtfPriceHistory.trade_date.desc())
        .limit(contract_required)
    )
    session_dates = tuple(reversed(session_dates_result.all()))
    warmup_dates = frozenset(session_dates[-SCORE_WARMUP_SESSIONS:])
    telemetry_dates = frozenset(session_dates[-TELEMETRY_DEPTH_SESSIONS:])
    contract_dates = frozenset(session_dates[-contract_required:])

    daily_by_code = {code: 0 for code in codes}
    warmup_by_code = {code: 0 for code in codes}
    telemetry_by_code = {code: 0 for code in codes}
    contract_by_code = {code: 0 for code in codes}
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
            )
            .where(
                EtfPriceHistory.etf_code.in_(codes),
                EtfPriceHistory.trade_date.in_(contract_dates),
                EtfPriceHistory.decision_eligible.is_(True),
                EtfPriceHistory.research_price_basis == "total_return_adjusted",
            )
            .group_by(EtfPriceHistory.etf_code)
        )
        for code, daily, warmup, telemetry, contract in rows:
            key = str(code)
            daily_by_code[key] = int(daily or 0)
            warmup_by_code[key] = int(warmup or 0)
            telemetry_by_code[key] = int(telemetry or 0)
            contract_by_code[key] = int(contract or 0)

    daily_attempted = await _latest_attempted_codes(session, scope=DAILY_FRESHNESS_SCOPE)
    warmup_attempted = await _latest_attempted_codes(session, scope=SCORE_WARMUP_SCOPE)
    contract_attempted = await _latest_attempted_codes(session, scope=contract_scope)
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
    contract = _lane_payload(
        scope=contract_scope,
        required_sessions=contract_required,
        authoritative=True,
        codes=codes,
        depth_by_code=contract_by_code,
        attempted_codes=contract_attempted,
        available_session_count=len(contract_dates),
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
    source_date_count = await _compatible_production_source_date_count(session)
    continuation_health = await _continuation_health_by_scope(
        session,
        scopes=(DAILY_FRESHNESS_SCOPE, SCORE_WARMUP_SCOPE, contract_scope),
    )
    history_publication_gate_passed = (
        daily["coverage_ratio"] >= PUBLICATION_COVERAGE_THRESHOLD
        and warmup["coverage_ratio"] >= PUBLICATION_COVERAGE_THRESHOLD
    )
    blockers: list[str] = []
    if daily["coverage_ratio"] < PUBLICATION_COVERAGE_THRESHOLD:
        blockers.append("daily_freshness_coverage_below_95pct")
    if warmup["coverage_ratio"] < PUBLICATION_COVERAGE_THRESHOLD:
        blockers.append("history_depth_61_coverage_below_95pct")
    if source_date_count < 20:
        blockers.append("compatible_production_source_dates_below_20")
    return {
        "target_date": effective_date.isoformat(),
        "contract_hash": contract_hash,
        "horizons": list(frozen_horizons),
        "universe": {
            "source": "ranking_point_in_time_snapshot",
            "snapshot_hash": universe.universe_snapshot_hash,
            "expected_count": len(codes),
            "codes": list(codes),
        },
        "daily_freshness": daily,
        "history_depth_61": warmup,
        "contract_depth": contract,
        "telemetry_depth_180": telemetry,
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
        },
        "history_publication_gate_passed": history_publication_gate_passed,
        "publication_coverage_threshold": PUBLICATION_COVERAGE_THRESHOLD,
        "blockers": blockers,
    }


__all__ = [
    "DEFAULT_HISTORY_HORIZONS",
    "PUBLICATION_COVERAGE_THRESHOLD",
    "current_etf_history_contract_hash",
    "read_etf_history_readiness",
]
