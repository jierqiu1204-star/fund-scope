"""Bounded scheduled jobs for dual-universe leader-tactics V2 research.

Provider work is confined to the capture job.  Materialization reads persisted
facts only and fails closed before allocating the full cross-section unless
coverage and host-memory gates pass.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import JobRun
from app.services.etf_research_evidence import stable_contract_hash
from app.services.intraday_etf.exchange_calendar import is_trading_day
from app.services.short_research.coverage_policy import ETF_COMPLETE_SCORE_COVERAGE
from app.services.strategy_lab.dual_universe_leader_tactics_v2 import (
    PRICE_BASIS,
    REPAIR_HISTORY,
    V2_FORMULA_REGISTRY_HASH,
    V2_SOURCE_REGISTRY,
    V2ContractError,
    screen_dual_universe,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_adapters import (
    read_ashare_asset_inputs,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_collector import (
    V2CollectorCheckpoint,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_eastmoney_provider import (
    EASTMONEY_ADJUSTMENT_VERSION,
    EASTMONEY_PROVIDER,
    EASTMONEY_TAXONOMY_VERSION,
    AshareUniverseMember,
    EastmoneyAshareProviderError,
    EastmoneyAshareV2Provider,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_etf_inputs import (
    read_etf_v2_asset_inputs,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_ingestion import (
    AshareUniverseSnapshotFact,
    persist_ashare_universe_snapshot_batch,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_lifecycle_storage import (
    load_v2_checkpoint,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_storage import (
    get_v2_materialized_manifest,
)
from app.services.strategy_lab.etf_point_in_time_decision_data import (
    latest_ready_etf_decision_data_snapshot,
)
from app.services.workflows.dual_universe_leader_tactics_v2 import (
    V2CapturedAshareFacts,
    V2CheckpointContract,
    evaluate_v2_materialization_readiness,
    materialize_v2_result,
    read_ashare_authoritative_assets,
    read_ashare_readiness,
    run_v2_fact_capture_batch,
)

V2_CAPTURE_JOB_NAME = "dual_universe_leader_tactics_v2_capture"
V2_MATERIALIZE_JOB_NAME = "dual_universe_leader_tactics_v2_materialize"
V2_ETF_MATERIALIZE_JOB_NAME = "dual_universe_leader_tactics_v2_materialize_etf"
V2_JOB_TIMEOUT_SECONDS = 55.0
V2_WORK_SECONDS = 52.0
V2_MIN_MATERIALIZATION_HEADROOM_BYTES = 768 * 1024 * 1024
V2_MAX_ASHARE_ASSETS = 6_000
V2_UNIVERSE_PERSIST_PAGE_SIZE = 500
V2_PROVIDER_FAILURE_COOLDOWN_MINUTES = 30
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _local_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(_SHANGHAI)
    if current.tzinfo is None:
        return current.replace(tzinfo=_SHANGHAI)
    return current.astimezone(_SHANGHAI)


def _capture_signal_date(local_now: datetime) -> date | None:
    if local_now.hour < 15 or not is_trading_day(local_now.date()):
        return None
    return local_now.date()


def _capture_manifest_hash(
    *,
    signal_date: date,
    members: tuple[AshareUniverseMember, ...],
    code_version: str,
) -> str:
    return stable_contract_hash(
        {
            "schema_version": "dual_universe_leader_tactics_v2_capture_manifest_v1",
            "signal_date": signal_date,
            "universe": "ashare",
            "asset_codes": [member.code for member in members],
            "source_registry_hash": V2_SOURCE_REGISTRY.registry_hash,
            "formula_registry_hash": V2_FORMULA_REGISTRY_HASH,
            "provider": EASTMONEY_PROVIDER,
            "adjustment_version": EASTMONEY_ADJUSTMENT_VERSION,
            "taxonomy_version": EASTMONEY_TAXONOMY_VERSION,
            "code_version": code_version,
        }
    )


def _checkpoint_contract(*, manifest_hash: str) -> V2CheckpointContract:
    return V2CheckpointContract(
        manifest_hash=manifest_hash,
        source_registry_hash=V2_SOURCE_REGISTRY.registry_hash,
        formula_registry_hash=V2_FORMULA_REGISTRY_HASH,
        adjustment_version=EASTMONEY_ADJUSTMENT_VERSION,
        taxonomy_version=EASTMONEY_TAXONOMY_VERSION,
        cost_model=(("fee_bps_per_side", 5.0), ("slippage_bps_per_side", 5.0)),
        state_policy="preparing_confirmed_invalidated_v2",
    )


async def _prior_ashare_members(
    session: AsyncSession,
    *,
    signal_date: date,
    as_of: datetime,
) -> dict[str, tuple[str, str | None]]:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT asset_code, asset_name, board
                FROM (
                    SELECT snapshots.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code
                               ORDER BY snapshot_date DESC, effective_at DESC,
                                        received_at DESC, fact_hash DESC
                           ) AS snapshot_rank
                    FROM ashare_research_universe_snapshots AS snapshots
                    WHERE snapshot_date < :signal_date
                      AND received_at <= :as_of
                      AND source_cutoff <= :as_of
                ) latest
                WHERE snapshot_rank = 1 AND LOWER(listing_state) = 'listed'
                ORDER BY asset_code
                LIMIT :limit
                """
                ),
                {
                    "signal_date": signal_date,
                    "as_of": as_of,
                    "limit": V2_MAX_ASHARE_ASSETS + 1,
                },
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > V2_MAX_ASHARE_ASSETS:
        raise ValueError("prior A-share universe exceeds the bounded limit")
    return {str(row["asset_code"]): (str(row["asset_name"]), row["board"]) for row in rows}


async def _persist_complete_universe_snapshot(
    session: AsyncSession,
    *,
    signal_date: date,
    members: tuple[AshareUniverseMember, ...],
    received_at: datetime,
) -> None:
    current = {member.code: member for member in members}
    prior = await _prior_ashare_members(
        session,
        signal_date=signal_date,
        as_of=received_at,
    )
    facts = [
        AshareUniverseSnapshotFact(
            snapshot_date=signal_date,
            asset_code=member.code,
            asset_name=member.name,
            listing_state="listed",
            board=member.board,
            effective_at=received_at,
            received_at=received_at,
            provider=EASTMONEY_PROVIDER,
            source_cutoff=received_at,
        )
        for member in members
    ]
    facts.extend(
        AshareUniverseSnapshotFact(
            snapshot_date=signal_date,
            asset_code=code,
            asset_name=name,
            listing_state="not_listed",
            board=board,
            effective_at=received_at,
            received_at=received_at,
            provider=EASTMONEY_PROVIDER,
            source_cutoff=received_at,
            exclusion_reason="absent_from_authoritative_snapshot",
        )
        for code, (name, board) in prior.items()
        if code not in current
    )
    for start in range(0, len(facts), V2_UNIVERSE_PERSIST_PAGE_SIZE):
        await persist_ashare_universe_snapshot_batch(
            session,
            facts[start : start + V2_UNIVERSE_PERSIST_PAGE_SIZE],
        )
    await session.commit()


async def _capture_ashare(
    session: AsyncSession,
    *,
    settings: Settings,
    local_now: datetime,
    timeout_seconds: float,
) -> dict[str, Any]:
    signal_date = _capture_signal_date(local_now)
    if signal_date is None:
        return {
            "status": "skipped",
            "reason": "leader_tactics_v2_no_completed_trading_session",
            "research_only": True,
        }
    code_version = settings.etf_leader_tactics_v2_code_version.strip()
    if not code_version:
        return {
            "status": "failed",
            "job_status": "failed",
            "job_message": "leader_tactics_v2_code_version_missing",
            "research_only": True,
        }

    previous_runs = (
        await session.scalars(
            select(JobRun.details_json)
            .where(
                JobRun.job_name == V2_CAPTURE_JOB_NAME,
                JobRun.status == "success",
            )
            .order_by(JobRun.id.desc())
            .limit(10)
        )
    ).all()
    previous = next(
        (
            details
            for details in previous_runs
            if isinstance(details, dict)
            and details.get("signal_date") == signal_date.isoformat()
            and details.get("checkpoint_status") == "complete"
        ),
        None,
    )
    if previous is not None:
        return {
            "status": "skipped",
            "reason": "leader_tactics_v2_capture_already_complete",
            "signal_date": signal_date.isoformat(),
            "manifest_hash": previous.get("manifest_hash"),
            "research_only": True,
        }
    recent_failures = (
        await session.scalars(
            select(JobRun.details_json)
            .where(
                JobRun.job_name == V2_CAPTURE_JOB_NAME,
                JobRun.status == "partial",
                JobRun.started_at
                >= _utc_naive(local_now) - timedelta(minutes=V2_PROVIDER_FAILURE_COOLDOWN_MINUTES),
            )
            .order_by(JobRun.id.desc())
            .limit(10)
        )
    ).all()
    if any(
        isinstance(details, dict)
        and details.get("unavailable_reason") == "leader_tactics_v2_eastmoney_unavailable"
        for details in recent_failures
    ):
        return {
            "status": "skipped",
            "reason": "leader_tactics_v2_provider_cooldown",
            "signal_date": signal_date.isoformat(),
            "cooldown_minutes": V2_PROVIDER_FAILURE_COOLDOWN_MINUTES,
            "research_only": True,
        }

    started = time.monotonic()
    provider_received_at = local_now
    database_received_at = _utc_naive(local_now)
    async with EastmoneyAshareV2Provider() as provider:
        members = await provider.fetch_universe(received_at=provider_received_at)
        if len(members) > V2_MAX_ASHARE_ASSETS:
            raise EastmoneyAshareProviderError("universe_exceeds_operational_limit")
        manifest_hash = _capture_manifest_hash(
            signal_date=signal_date,
            members=members,
            code_version=code_version,
        )
        checkpoint = await load_v2_checkpoint(session, manifest_hash=manifest_hash)
        if checkpoint is None:
            await _persist_complete_universe_snapshot(
                session,
                signal_date=signal_date,
                members=members,
                received_at=database_received_at,
            )
            checkpoint = V2CollectorCheckpoint(
                cursor=None,
                batch_size=5,
                completed_codes=(),
                status="paused",
                manifest_hash=manifest_hash,
            )
        remaining = timeout_seconds - (time.monotonic() - started) - 2.0
        if remaining <= 0:
            return {
                "status": "waiting",
                "job_status": "partial",
                "signal_date": signal_date.isoformat(),
                "manifest_hash": manifest_hash,
                "unavailable_reason": "leader_tactics_v2_universe_bootstrap_used_budget",
                "universe_count": len(members),
                "research_only": True,
            }
        contract = _checkpoint_contract(manifest_hash=manifest_hash)
        members_by_code = {member.code: member for member in members}
        codes = tuple(member.code for member in members)
        batch = None
        batch_failures = 0

        async def fetch_one(code: str) -> V2CapturedAshareFacts:
            facts = await provider.fetch_facts(
                members_by_code[code],
                signal_date=signal_date,
                received_at=provider_received_at,
                history_sessions=REPAIR_HISTORY,
            )
            return V2CapturedAshareFacts(
                asset_code=facts.asset_code,
                universe_fact=facts.universe_fact,
                theme_facts=facts.theme_facts,
                adjusted_price_facts=facts.adjusted_price_facts,
            )

        # Reuse the same serial provider connection and durable checkpoint for
        # as many 5-20 security pages as fit in this one hard-bounded job.  This
        # removes idle scheduler gaps without increasing concurrency or page
        # memory.
        while checkpoint.status != "complete" and remaining > 3.0:
            batch = await run_v2_fact_capture_batch(
                session,
                manifest_hash=manifest_hash,
                codes=codes,
                checkpoint=checkpoint,
                fetch_one=fetch_one,
                lease_owner="scheduler-v2-ashare-capture",
                budget_seconds=min(remaining, V2_WORK_SECONDS),
                expected_contract=contract,
                checkpoint_contract=contract,
            )
            checkpoint = batch.checkpoint
            batch_failures += len(batch.failed)
            if batch.stopped_reason in {"database_lease_busy", "single_worker_lease_busy"}:
                break
            remaining = timeout_seconds - (time.monotonic() - started) - 2.0
        if batch is None:
            return {
                "status": "waiting",
                "job_status": "partial",
                "signal_date": signal_date.isoformat(),
                "manifest_hash": manifest_hash,
                "unavailable_reason": "leader_tactics_v2_capture_budget_too_small",
                "universe_count": len(members),
                "research_only": True,
            }
    completed_count = len(batch.checkpoint.completed_codes)
    failed_count = len(batch.checkpoint.failed_codes)
    return {
        "status": "complete" if batch.checkpoint.status == "complete" else "partial",
        "job_status": "partial" if batch.checkpoint.status != "complete" else "success",
        "signal_date": signal_date.isoformat(),
        "manifest_hash": manifest_hash,
        "universe_count": len(members),
        "completed_count": completed_count,
        "failed_count": failed_count,
        "coverage": completed_count / len(members),
        "checkpoint_status": batch.checkpoint.status,
        "batch_size_next": batch.checkpoint.batch_size,
        "stopped_reason": batch.stopped_reason,
        "provider_health": {
            "provider": EASTMONEY_PROVIDER,
            "status": "healthy" if not batch.failed else "degraded",
            "batch_failures": batch_failures,
            "error_summary": batch.checkpoint.error_summary,
            "transport": provider.transport_diagnostics,
        },
        "price_basis": PRICE_BASIS,
        "raw_decision_violations": 0,
        "research_only": True,
        "notification_provenance": "none",
        "execution_provenance": "none",
    }


def _read_int(path: str) -> int | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def available_memory_bytes() -> int | None:
    """Return conservative cgroup/host headroom without spawning a process."""

    cgroup_limit = _read_int("/sys/fs/cgroup/memory.max")
    cgroup_used = _read_int("/sys/fs/cgroup/memory.current")
    cgroup_available = (
        max(0, cgroup_limit - cgroup_used)
        if cgroup_limit is not None and cgroup_used is not None
        else None
    )
    host_available: int | None = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                host_available = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    candidates = tuple(value for value in (cgroup_available, host_available) if value is not None)
    return min(candidates) if candidates else None


def _same_datetime(left: object, right: datetime) -> bool:
    if isinstance(left, str):
        try:
            left = datetime.fromisoformat(left)
        except ValueError:
            return False
    if not isinstance(left, datetime):
        return False
    return _utc_naive(left) == _utc_naive(right)


async def _materialize_etf(
    session: AsyncSession,
    *,
    settings: Settings,
    local_as_of: datetime,
) -> dict[str, Any]:
    snapshot = await latest_ready_etf_decision_data_snapshot(session, as_of=local_as_of)
    if snapshot is None:
        return {
            "status": "waiting",
            "job_status": "partial",
            "unavailable_reason": "etf_decision_data_snapshot_unavailable",
            "research_only": True,
        }
    code_version = settings.etf_leader_tactics_v2_code_version.strip()
    if not code_version:
        return {
            "status": "failed",
            "job_status": "failed",
            "job_message": "leader_tactics_v2_code_version_missing",
            "research_only": True,
        }
    decision_cutoff = snapshot.decision_cutoff
    persisted_cutoff = _utc_naive(decision_cutoff)
    existing = await get_v2_materialized_manifest(
        session,
        universe="etf",
        as_of=persisted_cutoff,
    )
    if (
        existing is not None
        and _same_datetime(existing["decision_cutoff"], persisted_cutoff)
        and str(existing["code_version"]) == code_version
    ):
        return {
            "status": "skipped",
            "reason": "leader_tactics_v2_etf_already_materialized",
            "signal_date": snapshot.trade_date.isoformat(),
            "manifest_hash": str(existing["manifest_hash"]),
            "research_only": True,
        }

    headroom = available_memory_bytes()
    if headroom is not None and headroom < V2_MIN_MATERIALIZATION_HEADROOM_BYTES:
        return {
            "status": "waiting",
            "job_status": "partial",
            "signal_date": snapshot.trade_date.isoformat(),
            "unavailable_reason": "insufficient_materialization_memory_headroom",
            "available_memory_bytes": headroom,
            "required_memory_bytes": V2_MIN_MATERIALIZATION_HEADROOM_BYTES,
            "research_only": True,
        }
    bundle = await read_etf_v2_asset_inputs(
        session,
        replay_date=snapshot.trade_date,
        decision_cutoff=snapshot.decision_cutoff,
    )
    readiness = bundle.readiness_dict(threshold=ETF_COMPLETE_SCORE_COVERAGE)
    provider_health = snapshot.provider_health or bundle.provider_health
    readiness["adjusted_data_provider_availability"] = readiness.pop(
        "provider_health",
        dict(bundle.provider_health),
    )
    readiness["provider_health"] = dict(provider_health)
    readiness["decision_data_snapshot"] = snapshot.evidence_dict()
    history_120_coverage = (
        bundle.adjusted_120_count / bundle.universe_count if bundle.universe_count else 0.0
    )
    reasons: list[str] = []
    if bundle.universe_count <= 0:
        reasons.append("etf_authoritative_universe_unavailable")
    if history_120_coverage < ETF_COMPLETE_SCORE_COVERAGE:
        reasons.append("insufficient_etf_history_120_coverage")
    if not bundle.provider_health:
        reasons.append("provider_health_unavailable")
    if bundle.raw_decision_violations:
        reasons.append("raw_decision_price_violation")
    if bundle.non_finite_violations:
        reasons.append("non_finite_adjusted_input")
    if reasons:
        return {
            "status": "waiting",
            "job_status": "partial",
            "signal_date": snapshot.trade_date.isoformat(),
            "unavailable_reason": reasons[0],
            "unavailable_reasons": reasons,
            "readiness": readiness,
            "decision_data_snapshot_id": snapshot.snapshot_id,
            "research_only": True,
        }

    result = screen_dual_universe(
        bundle.inputs,
        code_version=code_version,
        provider_health=provider_health,
    )
    manifest_hash = await materialize_v2_result(
        session,
        result,
        enabled=True,
        code_version=code_version,
    )
    qualifying = result.qualifying
    return {
        "status": "materialized",
        "signal_date": snapshot.trade_date.isoformat(),
        "manifest_hash": manifest_hash,
        "decision_data_snapshot_id": snapshot.snapshot_id,
        "universe_count": bundle.universe_count,
        "observation_count": len(result.observations),
        "candidate_count": len(qualifying),
        "candidate_codes": sorted({item.asset_code for item in qualifying}),
        "readiness": readiness,
        "available_memory_bytes": headroom,
        "research_only": True,
        "notification_provenance": "none",
        "execution_provenance": "none",
    }


async def _latest_visible_ashare_signal_date(
    session: AsyncSession,
    *,
    as_of: datetime,
) -> date | None:
    value = await session.scalar(
        text(
            """
            SELECT MAX(snapshot_date)
            FROM ashare_research_universe_snapshots
            WHERE received_at <= :as_of
              AND source_cutoff <= :as_of
            """
        ),
        {"as_of": as_of},
    )
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


async def _materialize_ashare(
    session: AsyncSession,
    *,
    settings: Settings,
    as_of: datetime,
) -> dict[str, Any]:
    signal_date = await _latest_visible_ashare_signal_date(session, as_of=as_of)
    if signal_date is None:
        return {
            "status": "waiting",
            "job_status": "partial",
            "unavailable_reason": "ashare_universe_not_materialized",
            "research_only": True,
        }

    readiness = await read_ashare_readiness(
        session,
        as_of=as_of,
        required_trade_date=signal_date,
    )
    decision = evaluate_v2_materialization_readiness(readiness)
    if not decision.ready:
        return {
            "status": "waiting",
            "job_status": "partial",
            "signal_date": signal_date.isoformat(),
            "unavailable_reason": decision.reasons[0],
            "readiness": readiness.to_dict(),
            "materialization_gate": decision.to_dict(),
            "research_only": True,
        }

    headroom = available_memory_bytes()
    if headroom is not None and headroom < V2_MIN_MATERIALIZATION_HEADROOM_BYTES:
        return {
            "status": "waiting",
            "job_status": "partial",
            "signal_date": signal_date.isoformat(),
            "unavailable_reason": "insufficient_materialization_memory_headroom",
            "available_memory_bytes": headroom,
            "required_memory_bytes": V2_MIN_MATERIALIZATION_HEADROOM_BYTES,
            "readiness": readiness.to_dict(),
            "research_only": True,
        }

    assets = await read_ashare_authoritative_assets(
        session,
        signal_date=signal_date,
        as_of=as_of,
        limit=V2_MAX_ASHARE_ASSETS,
    )
    if len(assets) != readiness.universe_count:
        return {
            "status": "waiting",
            "job_status": "partial",
            "signal_date": signal_date.isoformat(),
            "unavailable_reason": "authoritative_universe_changed_during_materialization",
            "readiness_universe_count": readiness.universe_count,
            "input_universe_count": len(assets),
            "research_only": True,
        }

    inputs = await read_ashare_asset_inputs(
        session,
        assets=assets,
        signal_date=signal_date,
        source_cutoff=as_of,
        history_limit=REPAIR_HISTORY,
        page_size=100,
    )
    provider_health = tuple(
        (provider, "healthy" if count > 0 else "unavailable")
        for provider, count in readiness.provider_health
    )
    result = screen_dual_universe(
        inputs,
        code_version=settings.etf_leader_tactics_v2_code_version,
        provider_health=provider_health,
    )
    manifest_hash = await materialize_v2_result(
        session,
        result,
        enabled=True,
        code_version=settings.etf_leader_tactics_v2_code_version,
    )
    qualifying = result.qualifying
    return {
        "status": "materialized",
        "signal_date": signal_date.isoformat(),
        "manifest_hash": manifest_hash,
        "universe_count": len(inputs),
        "observation_count": len(result.observations),
        "candidate_count": len(qualifying),
        "candidate_codes": sorted({item.asset_code for item in qualifying}),
        "readiness": readiness.to_dict(),
        "materialization_gate": decision.to_dict(),
        "available_memory_bytes": headroom,
        "research_only": True,
        "notification_provenance": "none",
        "execution_provenance": "none",
    }


async def dual_universe_leader_tactics_v2_etf_materialize_job(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
    timeout_seconds: float = V2_JOB_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Materialize ETF V2 evidence from persisted PIT facts without provider work."""

    if not settings.etf_leader_tactics_v2_materialize_enabled:
        return {
            "status": "skipped",
            "reason": "leader_tactics_v2_materialization_disabled",
            "research_only": True,
        }
    if timeout_seconds <= 0 or timeout_seconds > V2_JOB_TIMEOUT_SECONDS:
        raise ValueError("V2 materialization timeout must be in (0, 55] seconds")
    try:
        return await asyncio.wait_for(
            _materialize_etf(
                session,
                settings=settings,
                local_as_of=_local_now(now),
            ),
            timeout=min(V2_WORK_SECONDS, timeout_seconds),
        )
    except TimeoutError:
        await session.rollback()
        return {
            "status": "waiting",
            "job_status": "partial",
            "unavailable_reason": "leader_tactics_v2_etf_materialization_timeout",
            "timeout_seconds": min(V2_WORK_SECONDS, timeout_seconds),
            "research_only": True,
        }
    except (V2ContractError, ValueError) as exc:
        await session.rollback()
        return {
            "status": "waiting",
            "job_status": "partial",
            "unavailable_reason": "leader_tactics_v2_etf_inputs_incompatible",
            "error_summary": f"{type(exc).__name__}: {exc}"[:500],
            "research_only": True,
        }


async def dual_universe_leader_tactics_v2_materialize_job(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
    timeout_seconds: float = V2_JOB_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Materialize persisted A-share facts only after the hard readiness gate."""

    if not settings.etf_leader_tactics_v2_materialize_enabled:
        return {
            "status": "skipped",
            "reason": "leader_tactics_v2_materialization_disabled",
            "research_only": True,
        }
    if timeout_seconds <= 0 or timeout_seconds > V2_JOB_TIMEOUT_SECONDS:
        raise ValueError("V2 materialization timeout must be in (0, 55] seconds")
    as_of = _utc_naive(_local_now(now))
    try:
        return await asyncio.wait_for(
            _materialize_ashare(session, settings=settings, as_of=as_of),
            timeout=min(V2_WORK_SECONDS, timeout_seconds),
        )
    except TimeoutError:
        await session.rollback()
        return {
            "status": "waiting",
            "job_status": "partial",
            "unavailable_reason": "leader_tactics_v2_materialization_timeout",
            "timeout_seconds": min(V2_WORK_SECONDS, timeout_seconds),
            "research_only": True,
        }


async def dual_universe_leader_tactics_v2_capture_job(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
    timeout_seconds: float = V2_JOB_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Capture one bounded serial page of factual Eastmoney A-share inputs."""

    if not settings.etf_leader_tactics_v2_capture_enabled:
        return {
            "status": "skipped",
            "reason": "leader_tactics_v2_capture_disabled",
            "research_only": True,
        }
    if timeout_seconds <= 0 or timeout_seconds > V2_JOB_TIMEOUT_SECONDS:
        raise ValueError("V2 capture timeout must be in (0, 55] seconds")
    local_now = _local_now(now)
    try:
        return await asyncio.wait_for(
            _capture_ashare(
                session,
                settings=settings,
                local_now=local_now,
                timeout_seconds=min(V2_WORK_SECONDS, timeout_seconds),
            ),
            timeout=min(V2_WORK_SECONDS, timeout_seconds),
        )
    except TimeoutError:
        await session.rollback()
        return {
            "status": "waiting",
            "job_status": "partial",
            "unavailable_reason": "leader_tactics_v2_capture_timeout",
            "timeout_seconds": min(V2_WORK_SECONDS, timeout_seconds),
            "research_only": True,
        }
    except EastmoneyAshareProviderError as exc:
        await session.rollback()
        return {
            "status": "waiting",
            "job_status": "partial",
            "unavailable_reason": "leader_tactics_v2_eastmoney_unavailable",
            "provider_health": {
                "provider": EASTMONEY_PROVIDER,
                "status": "unavailable",
                "error_summary": f"{type(exc).__name__}: {exc}"[:500],
            },
            "research_only": True,
        }


__all__ = [
    "V2_CAPTURE_JOB_NAME",
    "V2_ETF_MATERIALIZE_JOB_NAME",
    "V2_MATERIALIZE_JOB_NAME",
    "available_memory_bytes",
    "dual_universe_leader_tactics_v2_capture_job",
    "dual_universe_leader_tactics_v2_etf_materialize_job",
    "dual_universe_leader_tactics_v2_materialize_job",
]
