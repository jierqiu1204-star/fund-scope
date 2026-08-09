"""Bounded orchestration for point-in-time ETF ranking research.

This module owns sequencing, immutable identity, checkpointing, and promotion
gates only.  Existing Stage A/B, candidate, outcome, validation, factor, and
policy-shadow modules remain the owners of their domain calculations.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import timedelta
from enum import StrEnum
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfFactorExperimentCheckpoint, utcnow
from app.services.etf_research_evidence import (
    OPERATIONAL_BUCKET_THRESHOLD_VERSION,
    OPERATIONAL_EXIT_ACTION_VERSION,
    OPERATIONAL_REENTRY_RULE_VERSION,
    stable_contract_hash,
)
from app.services.short_research.coverage_policy import (
    ETF_COMPLETE_SCORE_COVERAGE,
    ETF_DAILY_DECISION_MIN_COVERAGE,
)
from app.services.short_research.daily_reconstructable import (
    PRICE_BASIS,
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_surfaces import actionable_rank_manifest
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    RANKING_COST_CONTRACT_HASH,
    freeze_ranking_candidate_registry,
)

RESEARCH_LOOP_SCHEMA_VERSION = "etf_point_in_time_research_loop_v1"
RANKING_EXECUTION_MODEL = "t_plus_1_adjusted_close_full_horizon_v2"
POLICY_EXECUTION_MODEL = "declared_etf_action_policy_execution_model"
PRIMARY_RANKING_ENDPOINT = "paired_top10_5_session_net_excess_common_support"
PRIMARY_POLICY_ENDPOINT = "top20_ten_session_action_cycle_benefit"
MULTIPLICITY_METHOD = "holm_bonferroni"

MIN_PAGE_SIZE = 5
INITIAL_PAGE_SIZE = 10
MAX_PAGE_SIZE = 20
MAX_RESEARCH_LOOP_SECONDS = 55.0
MAX_RESEARCH_LOOP_RSS_BYTES = 3 * 1024**3

MIN_PROMOTION_SESSIONS = 252
MIN_PRIMARY_INDEPENDENT_DATES = 40
MIN_WALK_FORWARD_FOLDS = 3
MIN_DECISION_DATA_COVERAGE = ETF_DAILY_DECISION_MIN_COVERAGE
MIN_PRODUCTION_COVERAGE = ETF_COMPLETE_SCORE_COVERAGE
MAX_DRAWDOWN_DETERIORATION = 0.02

_CHECKPOINT_STATE_KEY = "__etf_point_in_time_research_loop_v1__"
_active_lock = Lock()
_active_runs: set[tuple[str, str]] = set()


class ResearchLoopContractError(ValueError):
    pass


class ResearchLoopBusyError(RuntimeError):
    pass


class ResearchLoopPhase(StrEnum):
    INPUTS = "inputs"
    STAGE_A = "stage_a"
    STAGE_B = "stage_b"
    CANDIDATES = "candidates"
    FORWARD_OUTCOMES = "forward_outcomes"
    RANKING_VALIDATION = "ranking_validation"
    FACTOR_EVIDENCE = "factor_evidence"
    POLICY_SHADOW = "policy_shadow"
    FINAL_EVIDENCE = "final_evidence"
    COMPLETE = "complete"


RESEARCH_LOOP_PHASES = (
    ResearchLoopPhase.INPUTS,
    ResearchLoopPhase.STAGE_A,
    ResearchLoopPhase.STAGE_B,
    ResearchLoopPhase.CANDIDATES,
    ResearchLoopPhase.FORWARD_OUTCOMES,
    ResearchLoopPhase.RANKING_VALIDATION,
    ResearchLoopPhase.FACTOR_EVIDENCE,
    ResearchLoopPhase.POLICY_SHADOW,
    ResearchLoopPhase.FINAL_EVIDENCE,
)


class ResearchPromotionState(StrEnum):
    INSUFFICIENT_DATA = "insufficient_data"
    PROMOTION_INELIGIBLE = "promotion_ineligible"
    PROMOTION_ELIGIBLE = "promotion_eligible"


class EvidenceAvailabilityReason(StrEnum):
    NO_PRODUCTION_PUBLISHED_SNAPSHOT = "no_production_published_snapshot"
    RESEARCH_REPLAY_NOT_MATERIALIZED = "research_replay_not_materialized"
    INCOMPATIBLE_SCORE_MANIFEST = "incompatible_score_manifest"
    INSUFFICIENT_POINT_IN_TIME_UNIVERSE = "insufficient_point_in_time_universe"
    MISSING_DECISION_ELIGIBLE_ADJUSTED_PRICE = (
        "missing_decision_eligible_adjusted_price"
    )
    INSUFFICIENT_SCORE_COVERAGE = "insufficient_score_coverage"
    INSUFFICIENT_ELIGIBLE_SESSIONS = "insufficient_eligible_sessions"
    INSUFFICIENT_INDEPENDENT_DATES = "insufficient_independent_dates"
    INSUFFICIENT_WALK_FORWARD_FOLDS = "insufficient_walk_forward_folds"
    FUTURE_WINDOW_PENDING = "future_window_pending"
    MISSING_ADJUSTED_ENTRY_OR_EXIT = "missing_adjusted_entry_or_exit"
    NO_COMPLETE_POLICY_SHADOW = "no_complete_policy_shadow"
    NO_LIVE_NOTIFICATION_SAMPLE = "no_live_notification_sample"
    PROVIDER_DELIVERY_UNCONFIRMED = "provider_delivery_unconfirmed"
    NO_USER_CONFIRMED_EXECUTION = "no_user_confirmed_execution"
    LEGACY_OR_INCOMPATIBLE = "legacy_or_incompatible"


def _require_hash(name: str, value: str) -> None:
    try:
        valid = len(value) == 64 and int(value, 16) >= 0
    except ValueError:
        valid = False
    if not valid:
        raise ResearchLoopContractError(f"{name} must be a SHA-256 hash")


def _action_policy_contract_hash() -> str:
    return stable_contract_hash(
        {
            "exit_action_version": OPERATIONAL_EXIT_ACTION_VERSION,
            "reentry_rule_version": OPERATIONAL_REENTRY_RULE_VERSION,
            "bucket_threshold_version": OPERATIONAL_BUCKET_THRESHOLD_VERSION,
            "primary_policy_endpoint": PRIMARY_POLICY_ENDPOINT,
        }
    )


@dataclass(frozen=True)
class FrozenResearchLoopManifest:
    replay_run_key: str
    code_version: str
    source_snapshot_hash: str
    universe_manifest_hash: str
    split_contract_hash: str
    holdout_identity_hash: str
    bootstrap_seed: int
    research_contract_id: str
    research_contract_hash: str
    research_score_field: str
    actionable_contract_id: str
    actionable_contract_hash: str
    candidate_registry_hash: str
    ranking_cost_contract_hash: str
    action_policy_contract_hash: str
    price_basis: str
    source_cutoff_policy: str
    ranking_execution_model: str
    policy_execution_model: str
    primary_ranking_endpoint: str
    primary_policy_endpoint: str
    multiplicity_method: str
    minimum_promotion_sessions: int = MIN_PROMOTION_SESSIONS
    minimum_independent_dates: int = MIN_PRIMARY_INDEPENDENT_DATES
    minimum_walk_forward_folds: int = MIN_WALK_FORWARD_FOLDS
    minimum_production_coverage: float = MIN_PRODUCTION_COVERAGE
    maximum_drawdown_deterioration: float = MAX_DRAWDOWN_DETERIORATION
    schema_version: str = RESEARCH_LOOP_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.replay_run_key.strip() or not self.code_version.strip():
            raise ResearchLoopContractError("replay run key and code version are required")
        if not self.source_cutoff_policy.strip():
            raise ResearchLoopContractError("source cutoff policy is required")
        for name, value in (
            ("source snapshot hash", self.source_snapshot_hash),
            ("universe manifest hash", self.universe_manifest_hash),
            ("split contract hash", self.split_contract_hash),
            ("holdout identity hash", self.holdout_identity_hash),
            ("research contract hash", self.research_contract_hash),
            ("actionable contract hash", self.actionable_contract_hash),
            ("candidate registry hash", self.candidate_registry_hash),
            ("ranking cost contract hash", self.ranking_cost_contract_hash),
            ("action policy contract hash", self.action_policy_contract_hash),
        ):
            _require_hash(name, value)

        research = daily_reconstructable_manifest()
        actionable = actionable_rank_manifest()
        registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
        required_identity = (
            research.contract_id,
            research.manifest_hash,
            research.score_field,
            actionable.contract_id,
            actionable.manifest_hash,
            registry.registry_hash,
            RANKING_COST_CONTRACT_HASH,
            _action_policy_contract_hash(),
            PRICE_BASIS,
            RANKING_EXECUTION_MODEL,
            POLICY_EXECUTION_MODEL,
            PRIMARY_RANKING_ENDPOINT,
            PRIMARY_POLICY_ENDPOINT,
            MULTIPLICITY_METHOD,
        )
        actual_identity = (
            self.research_contract_id,
            self.research_contract_hash,
            self.research_score_field,
            self.actionable_contract_id,
            self.actionable_contract_hash,
            self.candidate_registry_hash,
            self.ranking_cost_contract_hash,
            self.action_policy_contract_hash,
            self.price_basis,
            self.ranking_execution_model,
            self.policy_execution_model,
            self.primary_ranking_endpoint,
            self.primary_policy_endpoint,
            self.multiplicity_method,
        )
        if actual_identity != required_identity:
            raise ResearchLoopContractError(
                "research loop manifest does not match frozen production contracts"
            )
        if (
            self.minimum_promotion_sessions != MIN_PROMOTION_SESSIONS
            or self.minimum_independent_dates != MIN_PRIMARY_INDEPENDENT_DATES
            or self.minimum_walk_forward_folds != MIN_WALK_FORWARD_FOLDS
            or self.minimum_production_coverage != MIN_PRODUCTION_COVERAGE
            or self.maximum_drawdown_deterioration
            != MAX_DRAWDOWN_DETERIORATION
        ):
            raise ResearchLoopContractError("promotion gates are immutable")
        if self.bootstrap_seed < 0:
            raise ResearchLoopContractError("bootstrap seed must be non-negative")

    def canonical_payload(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def manifest_hash(self) -> str:
        self.validate()
        return stable_contract_hash(self.canonical_payload())


def build_frozen_research_loop_manifest(
    *,
    replay_run_key: str,
    code_version: str,
    source_snapshot_hash: str,
    universe_manifest_hash: str,
    split_contract_hash: str,
    holdout_identity_hash: str,
    bootstrap_seed: int,
    source_cutoff_policy: str = "recorded_available_at_lte_signal_cutoff",
) -> FrozenResearchLoopManifest:
    research = daily_reconstructable_manifest()
    actionable = actionable_rank_manifest()
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    manifest = FrozenResearchLoopManifest(
        replay_run_key=replay_run_key,
        code_version=code_version,
        source_snapshot_hash=source_snapshot_hash,
        universe_manifest_hash=universe_manifest_hash,
        split_contract_hash=split_contract_hash,
        holdout_identity_hash=holdout_identity_hash,
        bootstrap_seed=bootstrap_seed,
        research_contract_id=research.contract_id,
        research_contract_hash=research.manifest_hash,
        research_score_field=research.score_field,
        actionable_contract_id=actionable.contract_id,
        actionable_contract_hash=actionable.manifest_hash,
        candidate_registry_hash=registry.registry_hash,
        ranking_cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        action_policy_contract_hash=_action_policy_contract_hash(),
        price_basis=PRICE_BASIS,
        source_cutoff_policy=source_cutoff_policy,
        ranking_execution_model=RANKING_EXECUTION_MODEL,
        policy_execution_model=POLICY_EXECUTION_MODEL,
        primary_ranking_endpoint=PRIMARY_RANKING_ENDPOINT,
        primary_policy_endpoint=PRIMARY_POLICY_ENDPOINT,
        multiplicity_method=MULTIPLICITY_METHOD,
    )
    manifest.validate()
    return manifest


@dataclass(frozen=True)
class PromotionGateEvidence:
    decision_data_coverage_ratio: float
    score_coverage_ratio: float
    eligible_point_in_time_sessions: int
    independent_primary_dates: int
    completed_walk_forward_folds: int
    adjusted_primary_interval_lower: float | None
    fold_sign_stable: bool
    regime_sign_stable: bool
    candidate_maximum_drawdown: float | None
    baseline_maximum_drawdown: float | None
    non_finite_violations: int = 0
    concentration_violations: int = 0
    clone_policy_violations: int = 0
    raw_decision_price_violations: int = 0
    exclusion_gate_passed: bool = True
    holdout_consumed: bool = False


@dataclass(frozen=True)
class ResearchPromotionDecision:
    state: ResearchPromotionState
    failed_gates: tuple[str, ...]
    primary_endpoint: str = PRIMARY_RANKING_ENDPOINT
    production_mutation_allowed: Literal[False] = False


def evaluate_research_promotion(
    evidence: PromotionGateEvidence,
) -> ResearchPromotionDecision:
    numeric = (
        evidence.decision_data_coverage_ratio,
        evidence.score_coverage_ratio,
    )
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in numeric):
        raise ResearchLoopContractError("coverage ratios must be finite fractions")
    if min(
        evidence.eligible_point_in_time_sessions,
        evidence.independent_primary_dates,
        evidence.completed_walk_forward_folds,
        evidence.non_finite_violations,
        evidence.concentration_violations,
        evidence.clone_policy_violations,
        evidence.raw_decision_price_violations,
    ) < 0:
        raise ResearchLoopContractError("promotion counts must be non-negative")

    insufficient: list[str] = []
    if evidence.decision_data_coverage_ratio < MIN_DECISION_DATA_COVERAGE:
        insufficient.append("decision_data_coverage")
    if evidence.score_coverage_ratio < MIN_PRODUCTION_COVERAGE:
        insufficient.append("score_coverage")
    if evidence.eligible_point_in_time_sessions < MIN_PROMOTION_SESSIONS:
        insufficient.append("eligible_point_in_time_sessions")
    if evidence.independent_primary_dates < MIN_PRIMARY_INDEPENDENT_DATES:
        insufficient.append("independent_primary_dates")
    if evidence.completed_walk_forward_folds < MIN_WALK_FORWARD_FOLDS:
        insufficient.append("walk_forward_folds")
    if evidence.adjusted_primary_interval_lower is None:
        insufficient.append("adjusted_primary_interval")
    if not evidence.holdout_consumed:
        insufficient.append("holdout_not_consumed")
    if insufficient:
        return ResearchPromotionDecision(
            state=ResearchPromotionState.INSUFFICIENT_DATA,
            failed_gates=tuple(insufficient),
        )

    failed: list[str] = []
    assert evidence.adjusted_primary_interval_lower is not None
    if (
        not math.isfinite(evidence.adjusted_primary_interval_lower)
        or evidence.adjusted_primary_interval_lower <= 0.0
    ):
        failed.append("adjusted_primary_interval")
    if not evidence.fold_sign_stable:
        failed.append("fold_sign_stability")
    if not evidence.regime_sign_stable:
        failed.append("regime_sign_stability")
    if (
        evidence.candidate_maximum_drawdown is None
        or evidence.baseline_maximum_drawdown is None
        or not math.isfinite(evidence.candidate_maximum_drawdown)
        or not math.isfinite(evidence.baseline_maximum_drawdown)
        or evidence.candidate_maximum_drawdown
        > evidence.baseline_maximum_drawdown + MAX_DRAWDOWN_DETERIORATION
    ):
        failed.append("maximum_drawdown")
    for name, count in (
        ("non_finite", evidence.non_finite_violations),
        ("concentration", evidence.concentration_violations),
        ("clone_policy", evidence.clone_policy_violations),
        ("raw_decision_price", evidence.raw_decision_price_violations),
    ):
        if count:
            failed.append(name)
    if not evidence.exclusion_gate_passed:
        failed.append("exclusions")
    return ResearchPromotionDecision(
        state=(
            ResearchPromotionState.PROMOTION_INELIGIBLE
            if failed
            else ResearchPromotionState.PROMOTION_ELIGIBLE
        ),
        failed_gates=tuple(failed),
    )


@dataclass(frozen=True)
class AdaptivePageProfile:
    page_size: int = INITIAL_PAGE_SIZE
    consecutive_healthy_pages: int = 0

    def __post_init__(self) -> None:
        if not MIN_PAGE_SIZE <= self.page_size <= MAX_PAGE_SIZE:
            raise ResearchLoopContractError("page size must be between 5 and 20")
        if self.consecutive_healthy_pages < 0:
            raise ResearchLoopContractError("healthy page count must be non-negative")

    def after(
        self,
        outcome: Literal["healthy", "timeout", "memory_pressure", "failed"],
    ) -> AdaptivePageProfile:
        if outcome == "healthy":
            healthy = self.consecutive_healthy_pages + 1
            if healthy >= 2:
                return AdaptivePageProfile(
                    page_size=min(MAX_PAGE_SIZE, self.page_size + 5),
                    consecutive_healthy_pages=0,
                )
            return replace(self, consecutive_healthy_pages=healthy)
        if outcome in {"timeout", "memory_pressure"}:
            return AdaptivePageProfile(
                page_size=max(MIN_PAGE_SIZE, self.page_size // 2),
                consecutive_healthy_pages=0,
            )
        return AdaptivePageProfile(
            page_size=self.page_size,
            consecutive_healthy_pages=0,
        )


@dataclass(frozen=True)
class ResearchLoopPhaseResult:
    phase_complete: bool
    processed_count: int
    artifact_hash: str | None
    cursor: dict[str, Any]
    coverage: dict[str, dict[str, float | int | None]]
    exclusions: dict[str, int]
    outcome: Literal["healthy", "timeout", "memory_pressure", "failed"] = "healthy"
    peak_rss_bytes: int = 0
    error_summary: str | None = None

    def __post_init__(self) -> None:
        if self.processed_count < 0 or self.peak_rss_bytes < 0:
            raise ResearchLoopContractError("phase counts must be non-negative")
        if self.artifact_hash is not None:
            _require_hash("phase artifact hash", self.artifact_hash)
        if any(value < 0 for value in self.exclusions.values()):
            raise ResearchLoopContractError("exclusion counts must be non-negative")
        if self.outcome == "healthy" and self.error_summary is not None:
            raise ResearchLoopContractError("healthy phase cannot include an error")


PhaseExecutor = Callable[
    [
        ResearchLoopPhase,
        FrozenResearchLoopManifest,
        "ResearchLoopCheckpoint",
        int,
        float,
    ],
    Awaitable[ResearchLoopPhaseResult],
]

BoundPhaseHandler = Callable[
    [
        FrozenResearchLoopManifest,
        "ResearchLoopCheckpoint",
        int,
        float,
    ],
    Awaitable[ResearchLoopPhaseResult],
]


@dataclass(frozen=True)
class ResearchLoopPhaseHandlers:
    """Explicit adapters to existing domain-owned research components.

    The coordinator deliberately owns no Stage A/B, candidate, outcome,
    validation, factor, or action-policy calculations.  A production
    composition binds those existing functions here; tests can bind pure
    deterministic adapters without a second backtest engine.
    """

    inputs: BoundPhaseHandler
    stage_a: BoundPhaseHandler
    stage_b: BoundPhaseHandler
    candidates: BoundPhaseHandler
    forward_outcomes: BoundPhaseHandler
    ranking_validation: BoundPhaseHandler
    factor_evidence: BoundPhaseHandler
    policy_shadow: BoundPhaseHandler
    final_evidence: BoundPhaseHandler

    async def execute(
        self,
        phase: ResearchLoopPhase,
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if phase is ResearchLoopPhase.COMPLETE:
            raise ResearchLoopContractError("complete research loop has no handler")
        handler = getattr(self, phase.value)
        result = await handler(
            manifest,
            checkpoint,
            page_size,
            timeout_seconds,
        )
        if not isinstance(result, ResearchLoopPhaseResult):
            raise ResearchLoopContractError(
                f"{phase.value} handler returned an invalid phase result"
            )
        if not result.phase_complete and result.artifact_hash is not None:
            raise ResearchLoopContractError(
                "partial pages cannot publish a final phase artifact hash"
            )
        return result


@dataclass(frozen=True)
class ResearchLoopCheckpoint:
    replay_run_key: str
    manifest_hash: str
    code_version: str
    phase: ResearchLoopPhase
    generation: int
    page_profile: AdaptivePageProfile
    phase_cursor: dict[str, Any]
    phase_artifact_hashes: dict[str, tuple[str, ...]]
    coverage: dict[str, dict[str, float | int | None]]
    exclusions: dict[str, int]
    processed_count: int
    peak_rss_bytes: int
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
                "phase_artifact_hashes": self.phase_artifact_hashes,
                "coverage": self.coverage,
                "exclusions": self.exclusions,
                "status": self.status,
            }
        )


def _seal_checkpoint(checkpoint: ResearchLoopCheckpoint) -> ResearchLoopCheckpoint:
    return replace(
        checkpoint,
        checkpoint_hash=stable_contract_hash(checkpoint.canonical_payload()),
    )


def new_research_loop_checkpoint(
    manifest: FrozenResearchLoopManifest,
) -> ResearchLoopCheckpoint:
    manifest.validate()
    return _seal_checkpoint(
        ResearchLoopCheckpoint(
            replay_run_key=manifest.replay_run_key,
            manifest_hash=manifest.manifest_hash,
            code_version=manifest.code_version,
            phase=ResearchLoopPhase.INPUTS,
            generation=0,
            page_profile=AdaptivePageProfile(),
            phase_cursor={},
            phase_artifact_hashes={},
            coverage={},
            exclusions={},
            processed_count=0,
            peak_rss_bytes=0,
            status="partial",
            stop_reason=None,
            checkpoint_hash="pending",
        )
    )


def _next_phase(phase: ResearchLoopPhase) -> ResearchLoopPhase:
    if phase is ResearchLoopPhase.COMPLETE:
        return phase
    index = RESEARCH_LOOP_PHASES.index(phase)
    if index == len(RESEARCH_LOOP_PHASES) - 1:
        return ResearchLoopPhase.COMPLETE
    return RESEARCH_LOOP_PHASES[index + 1]


def _assert_checkpoint_identity(
    checkpoint: ResearchLoopCheckpoint,
    manifest: FrozenResearchLoopManifest,
) -> None:
    manifest.validate()
    if (
        checkpoint.replay_run_key != manifest.replay_run_key
        or checkpoint.manifest_hash != manifest.manifest_hash
        or checkpoint.code_version != manifest.code_version
    ):
        raise ResearchLoopContractError(
            "checkpoint manifest changed; create a new research-loop identity"
        )
    if checkpoint.checkpoint_hash != stable_contract_hash(
        checkpoint.canonical_payload()
    ):
        raise ResearchLoopContractError("checkpoint hash is invalid")


async def continue_research_loop_once(
    *,
    manifest: FrozenResearchLoopManifest,
    checkpoint: ResearchLoopCheckpoint,
    execute_phase: PhaseExecutor,
    timeout_seconds: float = 50.0,
) -> ResearchLoopCheckpoint:
    """Advance at most one complete page from the current phase."""

    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 55.0:
        raise ResearchLoopContractError("research-loop timeout must be within (0, 55]")
    _assert_checkpoint_identity(checkpoint, manifest)
    if checkpoint.phase is ResearchLoopPhase.COMPLETE:
        return checkpoint

    try:
        result = await asyncio.wait_for(
            execute_phase(
                checkpoint.phase,
                manifest,
                checkpoint,
                checkpoint.page_profile.page_size,
                timeout_seconds,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        result = ResearchLoopPhaseResult(
            phase_complete=False,
            processed_count=0,
            artifact_hash=None,
            cursor=dict(checkpoint.phase_cursor),
            coverage={},
            exclusions={},
            outcome="timeout",
            error_summary="research-loop phase timed out",
        )

    phase_artifacts = dict(checkpoint.phase_artifact_hashes)
    if result.artifact_hash is not None:
        existing = phase_artifacts.get(checkpoint.phase.value, ())
        if result.artifact_hash not in existing:
            phase_artifacts[checkpoint.phase.value] = (
                *existing,
                result.artifact_hash,
            )
    coverage = {**checkpoint.coverage, **result.coverage}
    exclusions = dict(checkpoint.exclusions)
    for reason, count in result.exclusions.items():
        exclusions[reason] = exclusions.get(reason, 0) + count
    next_phase = (
        _next_phase(checkpoint.phase)
        if result.phase_complete and result.outcome == "healthy"
        else checkpoint.phase
    )
    status: Literal["partial", "complete"] = (
        "complete" if next_phase is ResearchLoopPhase.COMPLETE else "partial"
    )
    return _seal_checkpoint(
        ResearchLoopCheckpoint(
            replay_run_key=checkpoint.replay_run_key,
            manifest_hash=checkpoint.manifest_hash,
            code_version=checkpoint.code_version,
            phase=next_phase,
            generation=checkpoint.generation + 1,
            page_profile=checkpoint.page_profile.after(result.outcome),
            phase_cursor={} if next_phase is not checkpoint.phase else dict(result.cursor),
            phase_artifact_hashes=phase_artifacts,
            coverage=coverage,
            exclusions=exclusions,
            processed_count=checkpoint.processed_count + result.processed_count,
            peak_rss_bytes=max(checkpoint.peak_rss_bytes, result.peak_rss_bytes),
            status=status,
            stop_reason=result.error_summary,
            checkpoint_hash="pending",
        )
    )


def _checkpoint_from_payload(payload: Mapping[str, Any]) -> ResearchLoopCheckpoint:
    profile = payload.get("page_profile")
    if not isinstance(profile, Mapping):
        raise ResearchLoopContractError("checkpoint page profile is missing")
    raw_artifacts = payload.get("phase_artifact_hashes")
    if not isinstance(raw_artifacts, Mapping):
        raise ResearchLoopContractError("checkpoint artifact hashes are missing")
    checkpoint = ResearchLoopCheckpoint(
        replay_run_key=str(payload["replay_run_key"]),
        manifest_hash=str(payload["manifest_hash"]),
        code_version=str(payload["code_version"]),
        phase=ResearchLoopPhase(str(payload["phase"])),
        generation=int(payload["generation"]),
        page_profile=AdaptivePageProfile(
            page_size=int(profile["page_size"]),
            consecutive_healthy_pages=int(profile["consecutive_healthy_pages"]),
        ),
        phase_cursor=dict(payload.get("phase_cursor") or {}),
        phase_artifact_hashes={
            str(key): tuple(str(item) for item in value)
            for key, value in raw_artifacts.items()
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
        peak_rss_bytes=int(payload.get("peak_rss_bytes") or 0),
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
            raise ResearchLoopBusyError("research-loop continuation is already running")
        _active_runs.add(identity)


def _release_local(identity: tuple[str, str]) -> None:
    with _active_lock:
        _active_runs.discard(identity)


async def _load_or_claim_checkpoint_row(
    session: AsyncSession,
    *,
    manifest: FrozenResearchLoopManifest,
    timeout_seconds: float,
) -> tuple[EtfFactorExperimentCheckpoint, ResearchLoopCheckpoint]:
    now = utcnow()
    lease_token = uuid4().hex
    lease_expires_at = now + timedelta(seconds=timeout_seconds + 5)
    identity = (
        EtfFactorExperimentCheckpoint.manifest_hash == manifest.manifest_hash,
        EtfFactorExperimentCheckpoint.code_version == manifest.code_version,
    )
    row = await session.scalar(
        select(EtfFactorExperimentCheckpoint).where(*identity)
    )
    if row is None:
        checkpoint = new_research_loop_checkpoint(manifest)
        row = EtfFactorExperimentCheckpoint(
            manifest_hash=manifest.manifest_hash,
            code_version=manifest.code_version,
            status="running",
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            processed_asset_codes_json=[],
            completed_batch_hashes_json=[],
            cached_factor_rows_json={
                _CHECKPOINT_STATE_KEY: {
                    **checkpoint.canonical_payload(),
                    "checkpoint_hash": checkpoint.checkpoint_hash,
                }
            },
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
        payload = (row.cached_factor_rows_json or {}).get(_CHECKPOINT_STATE_KEY)
        if not isinstance(payload, Mapping):
            raise ResearchLoopContractError("completed research-loop state is missing")
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
        raise ResearchLoopBusyError("research-loop database lease is active")
    await session.commit()
    claimed = await session.get(EtfFactorExperimentCheckpoint, claimed_id)
    if claimed is None:
        raise RuntimeError("claimed research-loop checkpoint disappeared")
    payload = (claimed.cached_factor_rows_json or {}).get(_CHECKPOINT_STATE_KEY)
    if not isinstance(payload, Mapping):
        checkpoint = new_research_loop_checkpoint(manifest)
    else:
        checkpoint = _checkpoint_from_payload(payload)
        _assert_checkpoint_identity(checkpoint, manifest)
    return claimed, checkpoint


async def run_bounded_research_loop_continuation(
    session: AsyncSession,
    *,
    manifest: FrozenResearchLoopManifest,
    execute_phase: PhaseExecutor,
    timeout_seconds: float = 50.0,
) -> ResearchLoopCheckpoint:
    """Claim one lease, advance one page, and durably release the lease."""

    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 50.0:
        raise ResearchLoopContractError(
            "bounded continuation timeout must be within (0, 50]"
        )
    manifest.validate()
    identity = (manifest.manifest_hash, manifest.code_version)
    _claim_local(identity)
    started = time.perf_counter()
    row: EtfFactorExperimentCheckpoint | None = None
    try:
        row, checkpoint = await _load_or_claim_checkpoint_row(
            session,
            manifest=manifest,
            timeout_seconds=timeout_seconds,
        )
        if checkpoint.status == "complete":
            return checkpoint
        updated = await continue_research_loop_once(
            manifest=manifest,
            checkpoint=checkpoint,
            execute_phase=execute_phase,
            timeout_seconds=timeout_seconds,
        )
        state_payload = {
            **updated.canonical_payload(),
            "checkpoint_hash": updated.checkpoint_hash,
        }
        row.cached_factor_rows_json = {_CHECKPOINT_STATE_KEY: state_payload}
        row.completed_batch_hashes_json = [
            artifact_hash
            for phase in RESEARCH_LOOP_PHASES
            for artifact_hash in updated.phase_artifact_hashes.get(phase.value, ())
        ]
        row.status = "complete" if updated.status == "complete" else "partial"
        row.batch_count = updated.generation
        row.peak_batch_size = max(
            row.peak_batch_size,
            updated.page_profile.page_size,
        )
        row.runtime_seconds += max(0.0, time.perf_counter() - started)
        row.peak_memory_bytes = max(row.peak_memory_bytes, updated.peak_rss_bytes)
        finite_coverages = [
            float(value["rate"])
            for value in updated.coverage.values()
            if value.get("rate") is not None
            and math.isfinite(float(value["rate"]))
        ]
        row.coverage_ratio = min(finite_coverages) if finite_coverages else 0.0
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
            row.error_summary = (
                f"{type(exc).__name__}: {str(exc).strip() or 'no message'}"
            )[:500]
            row.lease_token = None
            row.lease_expires_at = None
            await session.commit()
        raise
    finally:
        _release_local(identity)


def research_loop_checkpoint_view(
    checkpoint: ResearchLoopCheckpoint,
) -> dict[str, Any]:
    return {
        "schema_version": RESEARCH_LOOP_SCHEMA_VERSION,
        "replay_run_key": checkpoint.replay_run_key,
        "manifest_hash": checkpoint.manifest_hash,
        "code_version": checkpoint.code_version,
        "phase": checkpoint.phase.value,
        "status": checkpoint.status,
        "generation": checkpoint.generation,
        "page_size": checkpoint.page_profile.page_size,
        "processed_count": checkpoint.processed_count,
        "coverage": checkpoint.coverage,
        "exclusions": checkpoint.exclusions,
        "peak_rss_bytes": checkpoint.peak_rss_bytes,
        "stop_reason": checkpoint.stop_reason,
        "result_hash": checkpoint.result_hash,
        "research_only": True,
        "production_mutation_allowed": False,
    }
