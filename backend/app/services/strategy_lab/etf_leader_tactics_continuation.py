"""Bounded, research-only continuation for ETF leader-tactics evidence."""

from __future__ import annotations

import asyncio
import math
import time
import tracemalloc
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfFactorExperimentCheckpoint, utcnow
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_leader_tactics_evaluation import (
    LeaderExperimentRegistration,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_HYPOTHESIS_REGISTRY,
)
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    AdaptivePageProfile,
)
from app.services.tracked_positions.lifecycle import stable_contract_json

LEADER_CONTINUATION_SCHEMA_VERSION = "etf_leader_tactics_continuation_v1"
LEADER_CONTINUATION_STATE_KEY = "__etf_leader_tactics_continuation_v1__"
MAX_LEADER_CONTINUATION_SECONDS = 55.0
MAX_LEADER_HANDLER_SECONDS = 50.0
MAX_LEADER_PAGE_SIZE = 20
MAX_LEADER_PAGE_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_LEADER_TRACED_MEMORY_BYTES = 512 * 1024 * 1024

_active_lock = Lock()
_active_runs: set[tuple[str, str]] = set()


class LeaderContinuationContractError(ValueError):
    pass


class LeaderContinuationBusyError(RuntimeError):
    pass


class LeaderContinuationPhase(StrEnum):
    FEATURES = "features"
    OUTCOMES = "outcomes"
    DIAGNOSTICS = "diagnostics"
    MA5_POLICY = "ma5_policy"
    FINAL_EVIDENCE = "final_evidence"
    COMPLETE = "complete"


LEADER_CONTINUATION_PHASES = (
    LeaderContinuationPhase.FEATURES,
    LeaderContinuationPhase.OUTCOMES,
    LeaderContinuationPhase.DIAGNOSTICS,
    LeaderContinuationPhase.MA5_POLICY,
    LeaderContinuationPhase.FINAL_EVIDENCE,
)


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _error_summary(exc: BaseException) -> str:
    message = " ".join(str(exc).split()) or "no message"
    return f"{type(exc).__name__}: {message}"[:500]


@dataclass(frozen=True)
class LeaderContinuationManifest:
    run_key: str
    code_version: str
    leader_manifest_hash: str
    factor_manifest_hash: str
    source_snapshot_hash: str
    universe_manifest_hash: str
    input_snapshot_hash: str
    hypothesis_registry_hash: str
    candidate_registry_hash: str
    data_cutoff: datetime
    schema_version: str = LEADER_CONTINUATION_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.run_key.strip() or not self.code_version.strip():
            raise LeaderContinuationContractError(
                "leader continuation run and code identities are required"
            )
        hashes = (
            self.leader_manifest_hash,
            self.factor_manifest_hash,
            self.source_snapshot_hash,
            self.universe_manifest_hash,
            self.input_snapshot_hash,
            self.hypothesis_registry_hash,
            self.candidate_registry_hash,
        )
        if not all(_is_sha256(value) for value in hashes):
            raise LeaderContinuationContractError(
                "leader continuation identities must be SHA-256 hashes"
            )
        if self.hypothesis_registry_hash != LEADER_HYPOTHESIS_REGISTRY.registry_hash:
            raise LeaderContinuationContractError(
                "leader continuation hypothesis registry is incompatible"
            )
        if (
            self.candidate_registry_hash
            != FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash
        ):
            raise LeaderContinuationContractError(
                "leader continuation candidate registry is incompatible"
            )
        if self.schema_version != LEADER_CONTINUATION_SCHEMA_VERSION:
            raise LeaderContinuationContractError(
                "leader continuation schema is incompatible"
            )

    @property
    def manifest_hash(self) -> str:
        self.validate()
        return stable_contract_hash(asdict(self))


def build_leader_continuation_manifest(
    *,
    registration: LeaderExperimentRegistration,
    source_snapshot_hash: str,
    universe_manifest_hash: str,
    input_snapshot_hash: str,
    data_cutoff: datetime,
) -> LeaderContinuationManifest:
    registration.leader_manifest.validate()
    manifest = LeaderContinuationManifest(
        run_key=(
            "leader-pit:"
            f"{source_snapshot_hash}:{registration.leader_manifest.manifest_hash}"
        ),
        code_version=registration.leader_manifest.code_version,
        leader_manifest_hash=registration.leader_manifest.manifest_hash,
        factor_manifest_hash=registration.factor_experiment.manifest_hash,
        source_snapshot_hash=source_snapshot_hash,
        universe_manifest_hash=universe_manifest_hash,
        input_snapshot_hash=input_snapshot_hash,
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        candidate_registry_hash=FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        data_cutoff=data_cutoff,
    )
    manifest.validate()
    return manifest


@dataclass(frozen=True)
class LeaderPageArtifact:
    item_key: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class LeaderContinuationPage:
    phase_complete: bool
    next_cursor: Mapping[str, Any]
    artifacts: tuple[LeaderPageArtifact, ...] = ()
    coverage: Mapping[str, Mapping[str, float | int | None]] = field(
        default_factory=dict
    )
    exclusions: Mapping[str, int] = field(default_factory=dict)
    outcome: Literal["healthy", "timeout", "memory_pressure", "failed"] = (
        "healthy"
    )
    peak_memory_bytes: int = 0
    error_summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage", dict(self.coverage or {}))
        object.__setattr__(self, "exclusions", dict(self.exclusions or {}))
        if len(self.artifacts) > MAX_LEADER_PAGE_SIZE:
            raise LeaderContinuationContractError(
                "leader continuation page exceeds 20 artifacts"
            )
        if self.peak_memory_bytes < 0 or any(
            value < 0 for value in self.exclusions.values()
        ):
            raise LeaderContinuationContractError(
                "leader continuation telemetry must be non-negative"
            )
        if self.outcome == "healthy" and self.error_summary is not None:
            raise LeaderContinuationContractError(
                "healthy leader continuation page cannot include an error"
            )


LeaderPhaseHandler = Callable[
    [
        LeaderContinuationManifest,
        "LeaderContinuationCheckpoint",
        int,
        float,
    ],
    Awaitable[LeaderContinuationPage],
]


@dataclass(frozen=True)
class LeaderContinuationHandlers:
    features: LeaderPhaseHandler
    outcomes: LeaderPhaseHandler
    diagnostics: LeaderPhaseHandler
    ma5_policy: LeaderPhaseHandler
    final_evidence: LeaderPhaseHandler

    async def execute(
        self,
        phase: LeaderContinuationPhase,
        manifest: LeaderContinuationManifest,
        checkpoint: LeaderContinuationCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> LeaderContinuationPage:
        if phase is LeaderContinuationPhase.COMPLETE:
            raise LeaderContinuationContractError(
                "complete leader continuation has no handler"
            )
        return await getattr(self, phase.value)(
            manifest,
            checkpoint,
            page_size,
            timeout_seconds,
        )


@dataclass(frozen=True)
class LeaderContinuationCheckpoint:
    run_key: str
    manifest_hash: str
    code_version: str
    phase: LeaderContinuationPhase
    generation: int
    page_profile: AdaptivePageProfile
    phase_cursor: Mapping[str, Any]
    phase_artifact_hashes: Mapping[str, str]
    phase_item_counts: Mapping[str, int]
    coverage: Mapping[str, Mapping[str, float | int | None]]
    exclusions: Mapping[str, int]
    processed_count: int
    peak_memory_bytes: int
    status: Literal["partial", "complete"]
    stop_reason: str | None
    checkpoint_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase"] = self.phase.value
        payload.pop("checkpoint_hash")
        return payload

    @property
    def result_hash(self) -> str:
        return stable_contract_hash(
            {
                "manifest_hash": self.manifest_hash,
                "phase_artifact_hashes": dict(self.phase_artifact_hashes),
                "phase_item_counts": dict(self.phase_item_counts),
                "coverage": dict(self.coverage),
                "exclusions": dict(self.exclusions),
                "status": self.status,
            }
        )


def _seal_checkpoint(
    checkpoint: LeaderContinuationCheckpoint,
) -> LeaderContinuationCheckpoint:
    return replace(
        checkpoint,
        checkpoint_hash=stable_contract_hash(checkpoint.canonical_payload()),
    )


def new_leader_continuation_checkpoint(
    manifest: LeaderContinuationManifest,
) -> LeaderContinuationCheckpoint:
    manifest.validate()
    return _seal_checkpoint(
        LeaderContinuationCheckpoint(
            run_key=manifest.run_key,
            manifest_hash=manifest.manifest_hash,
            code_version=manifest.code_version,
            phase=LeaderContinuationPhase.FEATURES,
            generation=0,
            page_profile=AdaptivePageProfile(),
            phase_cursor={},
            phase_artifact_hashes={},
            phase_item_counts={},
            coverage={},
            exclusions={},
            processed_count=0,
            peak_memory_bytes=0,
            status="partial",
            stop_reason=None,
            checkpoint_hash="pending",
        )
    )


def _assert_checkpoint_identity(
    checkpoint: LeaderContinuationCheckpoint,
    manifest: LeaderContinuationManifest,
) -> None:
    manifest.validate()
    if (
        checkpoint.run_key != manifest.run_key
        or checkpoint.manifest_hash != manifest.manifest_hash
        or checkpoint.code_version != manifest.code_version
    ):
        raise LeaderContinuationContractError(
            "leader continuation checkpoint identity changed"
        )
    if checkpoint.checkpoint_hash != stable_contract_hash(
        checkpoint.canonical_payload()
    ):
        raise LeaderContinuationContractError(
            "leader continuation checkpoint hash is invalid"
        )


def _next_phase(phase: LeaderContinuationPhase) -> LeaderContinuationPhase:
    if phase is LeaderContinuationPhase.COMPLETE:
        return phase
    index = LEADER_CONTINUATION_PHASES.index(phase)
    if index == len(LEADER_CONTINUATION_PHASES) - 1:
        return LeaderContinuationPhase.COMPLETE
    return LEADER_CONTINUATION_PHASES[index + 1]


def _page_payload_size(page: LeaderContinuationPage) -> int:
    return sum(
        len(stable_contract_json(dict(item.payload)).encode("utf-8"))
        for item in page.artifacts
    )


async def continue_leader_tactics_once(
    *,
    artifact_store: ReplayArtifactStore,
    manifest: LeaderContinuationManifest,
    checkpoint: LeaderContinuationCheckpoint,
    execute_phase: Callable[
        [
            LeaderContinuationPhase,
            LeaderContinuationManifest,
            LeaderContinuationCheckpoint,
            int,
            float,
        ],
        Awaitable[LeaderContinuationPage],
    ],
    timeout_seconds: float = MAX_LEADER_HANDLER_SECONDS,
) -> LeaderContinuationCheckpoint:
    """Advance exactly one page and publish only immutable research artifacts."""

    if (
        not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= MAX_LEADER_HANDLER_SECONDS
    ):
        raise LeaderContinuationContractError(
            "leader continuation timeout must be within (0, 50]"
        )
    _assert_checkpoint_identity(checkpoint, manifest)
    if checkpoint.phase is LeaderContinuationPhase.COMPLETE:
        return checkpoint

    deadline = time.monotonic() + timeout_seconds
    reserve_seconds = min(10.0, max(1.0, timeout_seconds * 0.2))
    handler_seconds = max(0.01, timeout_seconds - reserve_seconds)
    started_tracing = not tracemalloc.is_tracing()
    if started_tracing:
        tracemalloc.start()
    try:
        try:
            page = await asyncio.wait_for(
                execute_phase(
                    checkpoint.phase,
                    manifest,
                    checkpoint,
                    checkpoint.page_profile.page_size,
                    handler_seconds,
                ),
                timeout=handler_seconds,
            )
        except TimeoutError:
            page = LeaderContinuationPage(
                phase_complete=False,
                next_cursor=dict(checkpoint.phase_cursor),
                outcome="timeout",
                error_summary="leader continuation phase timed out",
            )
        except Exception as exc:  # fail closed and persist a reproducible summary
            page = LeaderContinuationPage(
                phase_complete=False,
                next_cursor=dict(checkpoint.phase_cursor),
                outcome="failed",
                error_summary=_error_summary(exc),
            )

        _, traced_peak = tracemalloc.get_traced_memory()
        peak_memory = max(page.peak_memory_bytes, traced_peak)
        if (
            len(page.artifacts) > checkpoint.page_profile.page_size
            or len(page.artifacts) > MAX_LEADER_PAGE_SIZE
        ):
            raise LeaderContinuationContractError(
                "leader continuation handler exceeded its page size"
            )
        payload_bytes = _page_payload_size(page)
        if (
            payload_bytes > MAX_LEADER_PAGE_PAYLOAD_BYTES
            or peak_memory > MAX_LEADER_TRACED_MEMORY_BYTES
        ):
            page = LeaderContinuationPage(
                phase_complete=False,
                next_cursor=dict(checkpoint.phase_cursor),
                outcome="memory_pressure",
                peak_memory_bytes=peak_memory,
                error_summary="leader continuation memory limit reached",
            )

        if page.outcome == "healthy" and page.artifacts:
            artifact_seconds = max(
                0.01,
                min(5.0, deadline - time.monotonic()),
            )
            artifact_store.write_research_artifacts(
                run_id=manifest.manifest_hash,
                phase=checkpoint.phase.value,
                artifacts=tuple(
                    (item.item_key, item.payload) for item in page.artifacts
                ),
                max_seconds=artifact_seconds,
            )

        artifact_hashes = dict(checkpoint.phase_artifact_hashes)
        item_counts = dict(checkpoint.phase_item_counts)
        if page.outcome == "healthy":
            item_counts[checkpoint.phase.value] = (
                item_counts.get(checkpoint.phase.value, 0) + len(page.artifacts)
            )
        if page.phase_complete and page.outcome == "healthy":
            digest_seconds = max(
                0.01,
                min(5.0, deadline - time.monotonic()),
            )
            phase_hash, phase_count = artifact_store.research_phase_digest(
                run_id=manifest.manifest_hash,
                phase=checkpoint.phase.value,
                max_seconds=digest_seconds,
            )
            artifact_hashes[checkpoint.phase.value] = phase_hash
            item_counts[checkpoint.phase.value] = phase_count

        coverage = {**checkpoint.coverage, **dict(page.coverage)}
        exclusions = dict(checkpoint.exclusions)
        for reason, count in page.exclusions.items():
            exclusions[reason] = exclusions.get(reason, 0) + count
        next_phase = (
            _next_phase(checkpoint.phase)
            if page.phase_complete and page.outcome == "healthy"
            else checkpoint.phase
        )
        status: Literal["partial", "complete"] = (
            "complete"
            if next_phase is LeaderContinuationPhase.COMPLETE
            else "partial"
        )
        return _seal_checkpoint(
            LeaderContinuationCheckpoint(
                run_key=checkpoint.run_key,
                manifest_hash=checkpoint.manifest_hash,
                code_version=checkpoint.code_version,
                phase=next_phase,
                generation=checkpoint.generation + 1,
                page_profile=checkpoint.page_profile.after(page.outcome),
                phase_cursor=(
                    {}
                    if next_phase is not checkpoint.phase
                    else dict(page.next_cursor)
                ),
                phase_artifact_hashes=artifact_hashes,
                phase_item_counts=item_counts,
                coverage=coverage,
                exclusions=exclusions,
                processed_count=(
                    checkpoint.processed_count
                    + (len(page.artifacts) if page.outcome == "healthy" else 0)
                ),
                peak_memory_bytes=max(checkpoint.peak_memory_bytes, peak_memory),
                status=status,
                stop_reason=page.error_summary,
                checkpoint_hash="pending",
            )
        )
    finally:
        if started_tracing:
            tracemalloc.stop()


def _checkpoint_from_payload(
    payload: Mapping[str, Any],
) -> LeaderContinuationCheckpoint:
    profile = dict(payload.get("page_profile") or {})
    checkpoint = LeaderContinuationCheckpoint(
        run_key=str(payload["run_key"]),
        manifest_hash=str(payload["manifest_hash"]),
        code_version=str(payload["code_version"]),
        phase=LeaderContinuationPhase(str(payload["phase"])),
        generation=int(payload["generation"]),
        page_profile=AdaptivePageProfile(
            page_size=int(profile["page_size"]),
            consecutive_healthy_pages=int(profile["consecutive_healthy_pages"]),
        ),
        phase_cursor=dict(payload.get("phase_cursor") or {}),
        phase_artifact_hashes={
            str(key): str(value)
            for key, value in dict(
                payload.get("phase_artifact_hashes") or {}
            ).items()
        },
        phase_item_counts={
            str(key): int(value)
            for key, value in dict(payload.get("phase_item_counts") or {}).items()
        },
        coverage={
            str(key): dict(value)
            for key, value in dict(payload.get("coverage") or {}).items()
        },
        exclusions={
            str(key): int(value)
            for key, value in dict(payload.get("exclusions") or {}).items()
        },
        processed_count=int(payload.get("processed_count") or 0),
        peak_memory_bytes=int(payload.get("peak_memory_bytes") or 0),
        status=str(payload["status"]),  # type: ignore[arg-type]
        stop_reason=(
            str(payload["stop_reason"])
            if payload.get("stop_reason") is not None
            else None
        ),
        checkpoint_hash=str(payload["checkpoint_hash"]),
    )
    return checkpoint


def _claim_local(identity: tuple[str, str]) -> None:
    with _active_lock:
        if identity in _active_runs:
            raise LeaderContinuationBusyError(
                "leader continuation is already running"
            )
        _active_runs.add(identity)


def _release_local(identity: tuple[str, str]) -> None:
    with _active_lock:
        _active_runs.discard(identity)


async def _load_or_claim_checkpoint(
    session: AsyncSession,
    *,
    manifest: LeaderContinuationManifest,
    timeout_seconds: float,
) -> tuple[EtfFactorExperimentCheckpoint, LeaderContinuationCheckpoint]:
    now = utcnow()
    lease_token = uuid4().hex
    lease_expires_at = now + timedelta(seconds=timeout_seconds + 5.0)
    identity = (
        EtfFactorExperimentCheckpoint.manifest_hash == manifest.manifest_hash,
        EtfFactorExperimentCheckpoint.code_version == manifest.code_version,
    )
    row = await session.scalar(
        select(EtfFactorExperimentCheckpoint).where(*identity)
    )
    if row is None:
        checkpoint = new_leader_continuation_checkpoint(manifest)
        state = {
            **checkpoint.canonical_payload(),
            "checkpoint_hash": checkpoint.checkpoint_hash,
        }
        row = EtfFactorExperimentCheckpoint(
            manifest_hash=manifest.manifest_hash,
            code_version=manifest.code_version,
            status="running",
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            processed_asset_codes_json=[],
            completed_batch_hashes_json=[],
            cached_factor_rows_json={LEADER_CONTINUATION_STATE_KEY: state},
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
        else:
            await session.refresh(row)
            return row, checkpoint
    elif row.status == "complete":
        payload = (row.cached_factor_rows_json or {}).get(
            LEADER_CONTINUATION_STATE_KEY
        )
        if not isinstance(payload, Mapping):
            raise LeaderContinuationContractError(
                "completed leader continuation checkpoint is missing"
            )
        checkpoint = _checkpoint_from_payload(payload)
        _assert_checkpoint_identity(checkpoint, manifest)
        return row, checkpoint

    claimed_id = await session.scalar(
        update(EtfFactorExperimentCheckpoint)
        .where(
            *identity,
            EtfFactorExperimentCheckpoint.status != "complete",
            or_(
                EtfFactorExperimentCheckpoint.status != "running",
                EtfFactorExperimentCheckpoint.lease_expires_at.is_(None),
                EtfFactorExperimentCheckpoint.lease_expires_at <= now,
            ),
        )
        .values(
            status="running",
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            error_summary=None,
        )
        .returning(EtfFactorExperimentCheckpoint.id)
        .execution_options(synchronize_session=False)
    )
    if claimed_id is None:
        await session.rollback()
        raise LeaderContinuationBusyError(
            "leader continuation database lease is active"
        )
    await session.commit()
    claimed = await session.get(EtfFactorExperimentCheckpoint, claimed_id)
    if claimed is None:
        raise RuntimeError("claimed leader continuation checkpoint disappeared")
    await session.refresh(claimed)
    payload = (claimed.cached_factor_rows_json or {}).get(
        LEADER_CONTINUATION_STATE_KEY
    )
    checkpoint = (
        _checkpoint_from_payload(payload)
        if isinstance(payload, Mapping)
        else new_leader_continuation_checkpoint(manifest)
    )
    _assert_checkpoint_identity(checkpoint, manifest)
    return claimed, checkpoint


async def run_bounded_leader_tactics_continuation(
    session: AsyncSession,
    *,
    artifact_store: ReplayArtifactStore,
    manifest: LeaderContinuationManifest,
    handlers: LeaderContinuationHandlers,
    timeout_seconds: float = MAX_LEADER_HANDLER_SECONDS,
) -> LeaderContinuationCheckpoint:
    """Claim one exclusive lease, advance one page, and release it durably."""

    if (
        not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= MAX_LEADER_HANDLER_SECONDS
    ):
        raise LeaderContinuationContractError(
            "bounded leader continuation timeout must be within (0, 50]"
        )
    manifest.validate()
    identity = (manifest.manifest_hash, manifest.code_version)
    _claim_local(identity)
    started = time.perf_counter()
    row: EtfFactorExperimentCheckpoint | None = None
    try:
        row, checkpoint = await _load_or_claim_checkpoint(
            session,
            manifest=manifest,
            timeout_seconds=timeout_seconds,
        )
        if checkpoint.status == "complete":
            return checkpoint
        updated = await continue_leader_tactics_once(
            artifact_store=artifact_store,
            manifest=manifest,
            checkpoint=checkpoint,
            execute_phase=handlers.execute,
            timeout_seconds=timeout_seconds,
        )
        state = {
            **updated.canonical_payload(),
            "checkpoint_hash": updated.checkpoint_hash,
        }
        row.cached_factor_rows_json = {LEADER_CONTINUATION_STATE_KEY: state}
        row.completed_batch_hashes_json = [
            updated.phase_artifact_hashes[phase.value]
            for phase in LEADER_CONTINUATION_PHASES
            if phase.value in updated.phase_artifact_hashes
        ]
        row.status = "complete" if updated.status == "complete" else "partial"
        row.batch_count = updated.generation
        row.peak_batch_size = max(
            row.peak_batch_size,
            updated.page_profile.page_size,
        )
        row.runtime_seconds += max(0.0, time.perf_counter() - started)
        row.peak_memory_bytes = max(
            row.peak_memory_bytes,
            updated.peak_memory_bytes,
        )
        rates = [
            float(item["rate"])
            for item in updated.coverage.values()
            if item.get("rate") is not None
            and math.isfinite(float(item["rate"]))
        ]
        row.coverage_ratio = min(rates) if rates else 0.0
        row.exclusion_count = sum(updated.exclusions.values())
        row.error_summary = updated.stop_reason
        row.lease_token = None
        row.lease_expires_at = None
        await session.commit()
        return updated
    except Exception as exc:
        await session.rollback()
        if row is not None:
            row.status = "partial"
            row.error_summary = _error_summary(exc)
            row.lease_token = None
            row.lease_expires_at = None
            await session.commit()
        raise
    finally:
        _release_local(identity)


def leader_continuation_view(
    checkpoint: LeaderContinuationCheckpoint,
) -> dict[str, Any]:
    return {
        "schema_version": LEADER_CONTINUATION_SCHEMA_VERSION,
        "run_key": checkpoint.run_key,
        "manifest_hash": checkpoint.manifest_hash,
        "code_version": checkpoint.code_version,
        "phase": checkpoint.phase.value,
        "status": checkpoint.status,
        "generation": checkpoint.generation,
        "page_size": checkpoint.page_profile.page_size,
        "phase_cursor": dict(checkpoint.phase_cursor),
        "phase_item_counts": dict(checkpoint.phase_item_counts),
        "processed_count": checkpoint.processed_count,
        "coverage": dict(checkpoint.coverage),
        "exclusions": dict(checkpoint.exclusions),
        "peak_memory_bytes": checkpoint.peak_memory_bytes,
        "stop_reason": checkpoint.stop_reason,
        "result_hash": checkpoint.result_hash,
        "single_worker": True,
        "maximum_page_size": MAX_LEADER_PAGE_SIZE,
        "maximum_continuation_seconds": MAX_LEADER_CONTINUATION_SECONDS,
        "live_provider_calls": 0,
        "research_only": True,
        "production_mutation_allowed": False,
    }
