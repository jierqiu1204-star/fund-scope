"""Production composition for one bounded, research-only ETF PIT page.

The workflow deliberately consumes only immutable complete-publication facts and
persisted adjusted data.  It does not request live providers, publish a ranking,
or touch allocation, tracking, risk-alert, or notification state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfFactorExperimentCheckpoint,
    EtfPitCaptureSource,
    JobRun,
    ShortResearchSignalRun,
    utcnow,
)
from app.services.etf_research_evidence import RankingSourceKind, stable_contract_hash
from app.services.short_research.coverage_policy import (
    evaluate_persisted_etf_readiness,
    persisted_etf_complete_coverage_allowed,
)
from app.services.short_research.daily_reconstructable import (
    REQUIRED_BAR_COUNT,
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_surfaces import (
    DUAL_RANKING_RULE_VERSION,
    actionable_rank_manifest,
)
from app.services.short_research.snapshot_selector import (
    resolve_current_etf_ranking_surface_snapshot,
)
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ArtifactConflictError,
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_action_replay.features import (
    BoundedWorkLimitError,
)
from app.services.strategy_lab.etf_factor_evidence import (
    build_operational_factor_evidence,
)
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    FrozenResearchLoopManifest,
    PromotionGateEvidence,
    ResearchLoopBusyError,
    ResearchLoopCheckpoint,
    ResearchLoopPhaseHandlers,
    ResearchLoopPhaseResult,
    build_frozen_research_loop_manifest,
    evaluate_research_promotion,
    research_loop_checkpoint_view,
    run_bounded_research_loop_continuation,
)
from app.services.strategy_lab.etf_policy_shadow import build_policy_shadow_surface
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    RankingCandidateSelection,
    evaluate_ranking_candidates,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    calculate_ranking_forward_outcomes,
)
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    MAX_CODES_PER_REPLAY_INPUT_PAGE,
)
from app.services.strategy_lab.etf_ranking_stage_a import (
    STAGE_A_SCHEMA_VERSION,
    NewStageAReplayIdentityRequiredError,
    StageAArtifactConflictError,
    StageABatchRequest,
    StageABoundedWorkError,
    StageAReplayContract,
    run_stage_a_loader_job,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    NewStageBReplayIdentityRequiredError,
    StageBArtifactConflictError,
    StageBBatchRequest,
    StageBBoundedWorkError,
    StageBRankingEvent,
    read_stage_b_ranking_event_page,
    read_stage_b_source_date_page_from_stage_a,
    run_stage_b_continuation,
    stage_b_contract_from_stage_a,
)
from app.services.strategy_lab.etf_ranking_validation import (
    RESEARCH_RULE_VERSION,
    RESEARCH_SCORE_FIELD,
    RESEARCH_SCORE_VERSION,
    RankingValidationSourceEvent,
    freeze_ranking_validation_source_cohort,
    freeze_ranking_validation_source_event,
)

PIT_CAPTURE_SCHEMA_VERSION = "etf_production_pit_capture_v1"
PIT_RESEARCH_PHASE_ARTIFACT_SCHEMA_VERSION = "etf_production_pit_phase_artifact_v1"
PIT_CUTOFF_TIMEZONE = "Asia/Shanghai"
PIT_SOURCE_CUTOFF_POLICY = "recorded_replay_visibility_cutoff_lte_factual_receipt_v1"
PIT_MAX_CONTINUATION_SECONDS = 50.0
PIT_SCHEDULER_CADENCE_MINUTES = 2
_PIT_HANDLER_WORK_SECONDS = 45.0
_PIT_STAGE_B_MAX_FEATURE_ROWS = 5_000
_SHANGHAI = ZoneInfo(PIT_CUTOFF_TIMEZONE)
PIT_SPLIT_CONTRACT_HASH = stable_contract_hash(
    {
        "contract": "expanding_walk_forward_v1",
        "purge_embargo_sessions": 10,
        "non_overlapping_primary_horizon_sessions": 5,
        "minimum_walk_forward_folds": 3,
    }
)
PIT_HOLDOUT_IDENTITY_HASH = stable_contract_hash(
    {
        "contract": "single_use_locked_holdout_v1",
        "consumption_policy": "manual_promotion_review_only",
    }
)
PIT_BOOTSTRAP_SEED = 20260729

PIT_UNAVAILABLE_SNAPSHOT_NOT_FOUND = "pit_source_snapshot_not_found"
PIT_UNAVAILABLE_COMPLETE_PUBLICATION = "pit_requires_complete_dual_snapshot"
PIT_UNAVAILABLE_CONTRACT = "pit_source_contract_incompatible"
PIT_UNAVAILABLE_CUTOFF_MISSING = "pit_cutoff_provenance_missing"
PIT_UNAVAILABLE_CUTOFF_INCOMPATIBLE = "pit_cutoff_provenance_incompatible"
PIT_UNAVAILABLE_PROVIDER_HEALTH = "pit_provider_health_identity_missing"
PIT_UNAVAILABLE_SOURCE_CONFLICT = "pit_capture_source_conflict"
PIT_UNAVAILABLE_SOURCE_NOT_FOUND = "pit_capture_source_not_found"
PIT_UNAVAILABLE_SOURCE_INVALID = "pit_capture_source_invalid"
PIT_UNAVAILABLE_MANIFEST_LEASE = "pit_manifest_lease_active"
PIT_UNAVAILABLE_DISABLED = "pit_capture_disabled"
PIT_UNAVAILABLE_CODE_VERSION = "pit_code_version_missing"
PIT_UNAVAILABLE_PROVIDER_HEALTH_CONTEXT = "pit_provider_health_context_missing"
PIT_UNAVAILABLE_CADENCE = "pit_capture_cadence_not_due"
PIT_UNAVAILABLE_COMPLETE = "pit_source_research_loop_complete"
PIT_UNAVAILABLE_NO_SESSION = "pit_no_completed_trading_session"
PIT_PENDING_STAGE_B_RANKING_EVENT = "pit_stage_b_ranking_event_pending"
PIT_PENDING_FUTURE_EXCHANGE_SESSIONS = "future_window_pending"
PIT_PENDING_VALIDATION_INPUTS = "pit_validation_inputs_pending"
PIT_PENDING_FACTOR_INPUTS = "pit_factor_evidence_inputs_pending"
PIT_PENDING_POLICY_INPUTS = "pit_policy_shadow_inputs_pending"
PIT_PENDING_FINAL_INPUTS = "pit_final_evidence_inputs_pending"


@dataclass(frozen=True)
class PitCutoffProvenance:
    market_decision_cutoff: datetime
    data_receipt_cutoff: datetime
    replay_visibility_cutoff: datetime
    timezone: str = PIT_CUTOFF_TIMEZONE

    def canonical_payload(self) -> dict[str, str]:
        return {
            "market_decision_cutoff": self.market_decision_cutoff.isoformat(),
            "data_receipt_cutoff": self.data_receipt_cutoff.isoformat(),
            "replay_visibility_cutoff": self.replay_visibility_cutoff.isoformat(),
            "timezone": self.timezone,
        }

    @property
    def replay_visibility_cutoff_aware(self) -> datetime:
        return self.replay_visibility_cutoff.replace(tzinfo=_SHANGHAI)


@dataclass(frozen=True)
class PitSourceCaptureResult:
    state: Literal["captured", "unavailable"]
    unavailable_reason: str | None
    source: EtfPitCaptureSource | None
    created: bool


@dataclass(frozen=True)
class PitContinuationResult:
    state: Literal["advanced", "unavailable"]
    unavailable_reason: str | None
    source_id: int | None
    manifest_hash: str | None
    checkpoint: dict[str, Any] | None


@dataclass(frozen=True)
class PitCapturePreflight:
    due: bool
    reason: str
    trade_date: date
    source_signal_run_id: int | None = None
    provider_health_hash: str | None = None
    provider_health_identity: dict[str, Any] | None = None
    replay_visibility_cutoff: datetime | None = None
    source_id: int | None = None
    manifest_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "due": self.due,
            "reason": self.reason,
            "trade_date": self.trade_date.isoformat(),
            "source_signal_run_id": self.source_signal_run_id,
            "provider_health_hash": self.provider_health_hash,
            "provider_health_identity": self.provider_health_identity,
            "replay_visibility_cutoff": (
                self.replay_visibility_cutoff.isoformat()
                if self.replay_visibility_cutoff is not None
                else None
            ),
            "source_id": self.source_id,
            "manifest_hash": self.manifest_hash,
        }


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _finite_fraction(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        return None
    return result


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_local_cutoff(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(_SHANGHAI).replace(tzinfo=None)
    return parsed


def _source_cutoff_provenance(
    run: ShortResearchSignalRun,
    *,
    replay_visibility_cutoff: datetime | None,
) -> tuple[PitCutoffProvenance | None, str | None]:
    config = _mapping(run.config_json)
    summary = _mapping(run.summary_json)
    persisted = _mapping(summary.get("cutoff_provenance"))
    market_cutoff = _parse_local_cutoff(config.get("market_decision_cutoff"))
    receipt_cutoff = _parse_local_cutoff(persisted.get("data_receipt_cutoff"))
    config_receipt_cutoff = _parse_local_cutoff(
        config.get("source_availability_cutoff")
    )
    explicit_visibility_cutoff = _parse_local_cutoff(replay_visibility_cutoff)
    persisted_visibility_cutoff = _parse_local_cutoff(
        persisted.get("replay_visibility_cutoff")
    )

    if (
        market_cutoff is None
        or receipt_cutoff is None
        or explicit_visibility_cutoff is None
    ):
        return None, PIT_UNAVAILABLE_CUTOFF_MISSING
    if (
        config_receipt_cutoff is None
        or config_receipt_cutoff != receipt_cutoff
        or (
            persisted_visibility_cutoff is not None
            and persisted_visibility_cutoff != explicit_visibility_cutoff
        )
    ):
        return None, PIT_UNAVAILABLE_CUTOFF_INCOMPATIBLE
    if run.data_cutoff is None or _parse_local_cutoff(run.data_cutoff) != receipt_cutoff:
        return None, PIT_UNAVAILABLE_CUTOFF_INCOMPATIBLE
    if run.as_of_trade_date is None or any(
        value.date() != run.as_of_trade_date
        for value in (
            market_cutoff,
            receipt_cutoff,
            explicit_visibility_cutoff,
        )
    ):
        return None, PIT_UNAVAILABLE_CUTOFF_INCOMPATIBLE
    if not market_cutoff <= explicit_visibility_cutoff <= receipt_cutoff:
        return None, PIT_UNAVAILABLE_CUTOFF_INCOMPATIBLE
    return (
        PitCutoffProvenance(
            market_decision_cutoff=market_cutoff,
            data_receipt_cutoff=receipt_cutoff,
            replay_visibility_cutoff=explicit_visibility_cutoff,
        ),
        None,
    )


async def _latest_provider_health_identity(
    session: AsyncSession,
    *,
    trade_date: date,
) -> dict[str, Any] | None:
    job_name = (
        "etf_history_continuation:"
        f"publication_readiness:{trade_date.isoformat()}"
    )
    rows = (
        await session.scalars(
            select(JobRun)
            .where(JobRun.job_name == job_name)
            .order_by(JobRun.id.desc())
            .limit(10)
        )
    ).all()
    for row in rows:
        details = _mapping(row.details_json)
        provider_health = _mapping(details.get("provider_health"))
        if not provider_health:
            continue
        return {
            "job_run_id": row.id,
            "job_name": row.job_name,
            "job_status": row.status,
            "started_at": row.started_at.isoformat(),
            "finished_at": (
                row.finished_at.isoformat() if row.finished_at is not None else None
            ),
            "provider_health": dict(provider_health),
        }
    return None


async def preflight_production_pit_capture(
    session: AsyncSession,
    *,
    trade_date: date,
    enabled: bool,
    code_version: str,
    now: datetime | None = None,
) -> PitCapturePreflight:
    """Decide whether one PIT page is due without provider or tracked-run work."""

    if not enabled:
        return PitCapturePreflight(False, PIT_UNAVAILABLE_DISABLED, trade_date)
    normalized_code_version = code_version.strip()
    if not normalized_code_version:
        return PitCapturePreflight(False, PIT_UNAVAILABLE_CODE_VERSION, trade_date)
    selection = await resolve_current_etf_ranking_surface_snapshot(
        session,
        required_trade_date=trade_date,
        ranking_surface="research",
    )
    if selection.state != "ready" or selection.run is None:
        return PitCapturePreflight(
            False,
            PIT_UNAVAILABLE_COMPLETE_PUBLICATION,
            trade_date,
        )
    run = selection.run
    unavailable_reason = complete_dual_snapshot_unavailable_reason(run)
    if unavailable_reason is not None:
        return PitCapturePreflight(False, unavailable_reason, trade_date)

    persisted_cutoffs = _mapping(
        _mapping(run.summary_json).get("cutoff_provenance")
    )
    receipt_cutoff = _parse_local_cutoff(
        persisted_cutoffs.get("data_receipt_cutoff")
    )
    cutoffs, cutoff_reason = _source_cutoff_provenance(
        run,
        replay_visibility_cutoff=receipt_cutoff,
    )
    if cutoffs is None:
        return PitCapturePreflight(
            False,
            cutoff_reason or PIT_UNAVAILABLE_CUTOFF_MISSING,
            trade_date,
            source_signal_run_id=run.id,
        )

    provider_health_identity = await _latest_provider_health_identity(
        session,
        trade_date=trade_date,
    )
    if provider_health_identity is None:
        return PitCapturePreflight(
            False,
            PIT_UNAVAILABLE_PROVIDER_HEALTH_CONTEXT,
            trade_date,
            source_signal_run_id=run.id,
            replay_visibility_cutoff=cutoffs.replay_visibility_cutoff,
        )
    provider_health_hash = stable_contract_hash(provider_health_identity)
    source = await session.scalar(
        select(EtfPitCaptureSource).where(
            EtfPitCaptureSource.source_signal_run_id == run.id
        )
    )
    if source is None:
        return PitCapturePreflight(
            True,
            "pit_capture_due",
            trade_date,
            source_signal_run_id=run.id,
            provider_health_hash=provider_health_hash,
            provider_health_identity=provider_health_identity,
            replay_visibility_cutoff=cutoffs.replay_visibility_cutoff,
        )
    if source.provider_health_hash != provider_health_hash:
        return PitCapturePreflight(
            False,
            PIT_UNAVAILABLE_SOURCE_CONFLICT,
            trade_date,
            source_signal_run_id=run.id,
            provider_health_hash=provider_health_hash,
            provider_health_identity=provider_health_identity,
            replay_visibility_cutoff=cutoffs.replay_visibility_cutoff,
            source_id=source.id,
        )

    manifest = build_production_pit_manifest(
        source,
        code_version=normalized_code_version,
        split_contract_hash=PIT_SPLIT_CONTRACT_HASH,
        holdout_identity_hash=PIT_HOLDOUT_IDENTITY_HASH,
        bootstrap_seed=PIT_BOOTSTRAP_SEED,
    )
    checkpoint = await session.scalar(
        select(EtfFactorExperimentCheckpoint).where(
            EtfFactorExperimentCheckpoint.manifest_hash == manifest.manifest_hash,
            EtfFactorExperimentCheckpoint.code_version == manifest.code_version,
        )
    )
    common = {
        "source_signal_run_id": run.id,
        "provider_health_hash": provider_health_hash,
        "provider_health_identity": provider_health_identity,
        "replay_visibility_cutoff": cutoffs.replay_visibility_cutoff,
        "source_id": source.id,
        "manifest_hash": manifest.manifest_hash,
    }
    if checkpoint is not None and checkpoint.status == "complete":
        return PitCapturePreflight(
            False,
            PIT_UNAVAILABLE_COMPLETE,
            trade_date,
            **common,
        )
    effective_now = now or utcnow()
    if (
        checkpoint is not None
        and checkpoint.status == "running"
        and checkpoint.lease_expires_at is not None
        and checkpoint.lease_expires_at > effective_now
    ):
        return PitCapturePreflight(
            False,
            PIT_UNAVAILABLE_MANIFEST_LEASE,
            trade_date,
            **common,
        )
    if (
        checkpoint is not None
        and checkpoint.updated_at is not None
        and checkpoint.updated_at
        > effective_now - timedelta(minutes=PIT_SCHEDULER_CADENCE_MINUTES)
    ):
        return PitCapturePreflight(
            False,
            PIT_UNAVAILABLE_CADENCE,
            trade_date,
            **common,
        )
    return PitCapturePreflight(
        True,
        "pit_capture_due",
        trade_date,
        **common,
    )


def complete_dual_snapshot_unavailable_reason(
    run: ShortResearchSignalRun,
) -> str | None:
    """Validate a current complete dual snapshot without selecting a fallback run."""

    research = daily_reconstructable_manifest()
    actionable = actionable_rank_manifest()
    daily_coverage = _finite_fraction(run.decision_data_coverage_ratio)
    warmup_coverage = _finite_fraction(run.coverage_ratio)
    if (
        run.status != "success"
        or run.publication_state != "published"
        or run.published_at is None
        or run.scope_kind != "full"
        or run.as_of_trade_date is None
        or run.as_of_trade_date != run.as_of_date
        or daily_coverage is None
        or warmup_coverage is None
    ):
        return PIT_UNAVAILABLE_COMPLETE_PUBLICATION
    summary = _mapping(run.summary_json)
    policy_payload = _mapping(summary.get("readiness_policy"))
    persisted_policy_version = policy_payload.get("policy_version") or summary.get(
        "readiness_policy_version"
    )
    readiness = evaluate_persisted_etf_readiness(
        policy_version=(
            str(persisted_policy_version)
            if isinstance(persisted_policy_version, str)
            else None
        ),
        daily_coverage_ratio=daily_coverage,
        warmup_coverage_ratio=warmup_coverage,
    )
    if not readiness.complete_publication_allowed:
        return PIT_UNAVAILABLE_COMPLETE_PUBLICATION
    if (
        run.score_version != research.contract_id
        or run.score_field != research.score_field
        or run.price_basis != research.price_basis
        or run.rule_version != DUAL_RANKING_RULE_VERSION
        or not _is_sha256(run.universe_snapshot_hash)
        or not _is_sha256(run.input_snapshot_hash)
        or not _is_sha256(run.ranking_contract_hash)
    ):
        return PIT_UNAVAILABLE_CONTRACT
    if (
        summary.get("readiness_state") != "complete"
        or summary.get("readiness_policy_version") != readiness.policy_version
    ):
        return PIT_UNAVAILABLE_COMPLETE_PUBLICATION
    surfaces = _mapping(summary.get("ranking_surfaces"))
    research_surface = _mapping(surfaces.get("research"))
    actionable_surface = _mapping(surfaces.get("actionable"))
    if (
        research_surface.get("contract_id") != research.contract_id
        or research_surface.get("score_field") != research.score_field
        or research_surface.get("contract_hash") != research.manifest_hash
        or actionable_surface.get("contract_id") != actionable.contract_id
        or actionable_surface.get("score_field") != actionable.score_field
        or actionable_surface.get("contract_hash") != actionable.manifest_hash
    ):
        return PIT_UNAVAILABLE_CONTRACT
    return None


def _source_snapshot_payload(run: ShortResearchSignalRun) -> dict[str, Any]:
    summary = _mapping(run.summary_json)
    return {
        "schema_version": PIT_CAPTURE_SCHEMA_VERSION,
        "source_signal_run_id": run.id,
        "source_idempotency_key": run.idempotency_key,
        "as_of_trade_date": run.as_of_trade_date.isoformat()
        if run.as_of_trade_date is not None
        else None,
        "published_at": run.published_at.isoformat() if run.published_at else None,
        "universe_manifest_hash": run.universe_snapshot_hash,
        "input_snapshot_hash": run.input_snapshot_hash,
        "ranking_contract_hash": run.ranking_contract_hash,
        "draft_seal": summary.get("draft_seal"),
    }


def _source_context_payload(
    run: ShortResearchSignalRun,
    *,
    source_snapshot_hash: str,
    cutoffs: PitCutoffProvenance,
    provider_health_hash: str,
    provider_health_identity: Mapping[str, Any],
) -> dict[str, Any]:
    summary = _mapping(run.summary_json)
    readiness = _mapping(summary.get("readiness_policy"))
    surfaces = _mapping(summary.get("ranking_surfaces"))
    return {
        "schema_version": PIT_CAPTURE_SCHEMA_VERSION,
        "source_snapshot_hash": source_snapshot_hash,
        "source_signal_run_id": run.id,
        "as_of_trade_date": run.as_of_trade_date.isoformat()
        if run.as_of_trade_date is not None
        else None,
        "universe_manifest_hash": run.universe_snapshot_hash,
        "input_snapshot_hash": run.input_snapshot_hash,
        "ranking_contract_hash": run.ranking_contract_hash,
        "research_contract_hash": _mapping(surfaces.get("research")).get(
            "contract_hash"
        ),
        "actionable_contract_hash": _mapping(surfaces.get("actionable")).get(
            "contract_hash"
        ),
        "readiness_policy_version": readiness.get("policy_version"),
        "readiness_state": summary.get("readiness_state"),
        "target_date_coverage_ratio": run.decision_data_coverage_ratio,
        "warmup_coverage_ratio": run.coverage_ratio,
        "cutoffs": cutoffs.canonical_payload(),
        "provider_health_hash": provider_health_hash,
        "provider_health_identity": dict(provider_health_identity),
        "prospective_evidence": {
            "source_trade_dates": (
                [run.as_of_trade_date.isoformat()]
                if run.as_of_trade_date is not None
                else []
            ),
            "eligible_primary_dates": [],
            "independent_primary_date_count": 0,
            "completed_walk_forward_fold_count": 0,
            "insufficient_data_reasons": [
                "future_window_pending",
                "insufficient_independent_dates",
                "insufficient_walk_forward_folds",
            ],
        },
    }


def _captured_source_is_valid(source: EtfPitCaptureSource) -> bool:
    if (
        source.readiness_state != "complete"
        or source.cutoff_timezone != PIT_CUTOFF_TIMEZONE
        or _finite_fraction(source.target_date_coverage_ratio) is None
        or _finite_fraction(source.warmup_coverage_ratio) is None
        or not persisted_etf_complete_coverage_allowed(
            policy_version=source.readiness_policy_version,
            daily_coverage_ratio=source.target_date_coverage_ratio,
            warmup_coverage_ratio=source.warmup_coverage_ratio,
        )
    ):
        return False
    if not all(
        _is_sha256(value)
        for value in (
            source.source_snapshot_hash,
            source.source_context_hash,
            source.universe_manifest_hash,
            source.input_snapshot_hash,
            source.ranking_contract_hash,
            source.research_contract_hash,
            source.actionable_contract_hash,
            source.provider_health_hash,
        )
    ):
        return False
    context = _mapping(source.source_context_json)
    if not context or stable_contract_hash(context) != source.source_context_hash:
        return False
    provider_health_identity = _mapping(context.get("provider_health_identity"))
    if (
        not provider_health_identity
        or stable_contract_hash(provider_health_identity)
        != source.provider_health_hash
    ):
        return False
    return (
        source.market_decision_cutoff
        <= source.replay_visibility_cutoff
        <= source.data_receipt_cutoff
    )


async def capture_complete_pit_source(
    session: AsyncSession,
    *,
    source_signal_run_id: int,
    provider_health_hash: str,
    replay_visibility_cutoff: datetime | None,
    provider_health_identity: Mapping[str, Any] | None = None,
) -> PitSourceCaptureResult:
    """Append one factual PIT source only after a complete current dual snapshot.

    ``replay_visibility_cutoff`` is deliberately an explicit input.  The current
    materializer leaves it null, so this workflow never invents historical
    visibility from a data-receipt time.
    """

    run = await session.get(ShortResearchSignalRun, source_signal_run_id)
    if run is None:
        return PitSourceCaptureResult(
            state="unavailable",
            unavailable_reason=PIT_UNAVAILABLE_SNAPSHOT_NOT_FOUND,
            source=None,
            created=False,
        )
    unavailable_reason = complete_dual_snapshot_unavailable_reason(run)
    if unavailable_reason is not None:
        return PitSourceCaptureResult(
            state="unavailable",
            unavailable_reason=unavailable_reason,
            source=None,
            created=False,
        )
    provider_identity = _mapping(provider_health_identity)
    if (
        not _is_sha256(provider_health_hash)
        or not provider_identity
        or stable_contract_hash(provider_identity) != provider_health_hash
    ):
        return PitSourceCaptureResult(
            state="unavailable",
            unavailable_reason=PIT_UNAVAILABLE_PROVIDER_HEALTH,
            source=None,
            created=False,
        )
    cutoffs, unavailable_reason = _source_cutoff_provenance(
        run,
        replay_visibility_cutoff=replay_visibility_cutoff,
    )
    if cutoffs is None:
        return PitSourceCaptureResult(
            state="unavailable",
            unavailable_reason=unavailable_reason,
            source=None,
            created=False,
        )

    source_snapshot_hash = stable_contract_hash(_source_snapshot_payload(run))
    source_context = _source_context_payload(
        run,
        source_snapshot_hash=source_snapshot_hash,
        cutoffs=cutoffs,
        provider_health_hash=provider_health_hash,
        provider_health_identity=provider_identity,
    )
    source_context_hash = stable_contract_hash(source_context)
    existing = await session.scalar(
        select(EtfPitCaptureSource).where(
            EtfPitCaptureSource.source_signal_run_id == source_signal_run_id
        )
    )
    if existing is not None:
        if existing.source_context_hash != source_context_hash:
            return PitSourceCaptureResult(
                state="unavailable",
                unavailable_reason=PIT_UNAVAILABLE_SOURCE_CONFLICT,
                source=None,
                created=False,
            )
        return PitSourceCaptureResult(
            state="captured",
            unavailable_reason=None,
            source=existing,
            created=False,
        )

    surfaces = _mapping(_mapping(run.summary_json).get("ranking_surfaces"))
    source = EtfPitCaptureSource(
        source_signal_run_id=run.id,
        as_of_trade_date=run.as_of_trade_date,
        source_snapshot_hash=source_snapshot_hash,
        source_context_hash=source_context_hash,
        universe_manifest_hash=str(run.universe_snapshot_hash),
        input_snapshot_hash=str(run.input_snapshot_hash),
        ranking_contract_hash=str(run.ranking_contract_hash),
        research_contract_hash=str(
            _mapping(surfaces.get("research")).get("contract_hash")
        ),
        actionable_contract_hash=str(
            _mapping(surfaces.get("actionable")).get("contract_hash")
        ),
        readiness_policy_version=str(
            _mapping(_mapping(run.summary_json).get("readiness_policy")).get(
                "policy_version"
            )
        ),
        readiness_state="complete",
        target_date_coverage_ratio=float(run.decision_data_coverage_ratio),
        warmup_coverage_ratio=float(run.coverage_ratio),
        market_decision_cutoff=cutoffs.market_decision_cutoff,
        data_receipt_cutoff=cutoffs.data_receipt_cutoff,
        replay_visibility_cutoff=cutoffs.replay_visibility_cutoff,
        cutoff_timezone=cutoffs.timezone,
        provider_health_hash=provider_health_hash,
        source_context_json=source_context,
    )
    try:
        async with session.begin_nested():
            session.add(source)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(EtfPitCaptureSource).where(
                EtfPitCaptureSource.source_signal_run_id == source_signal_run_id
            )
        )
        if existing is None or existing.source_context_hash != source_context_hash:
            return PitSourceCaptureResult(
                state="unavailable",
                unavailable_reason=PIT_UNAVAILABLE_SOURCE_CONFLICT,
                source=None,
                created=False,
            )
        return PitSourceCaptureResult(
            state="captured",
            unavailable_reason=None,
            source=existing,
            created=False,
        )
    return PitSourceCaptureResult(
        state="captured",
        unavailable_reason=None,
        source=source,
        created=True,
    )


def build_production_pit_manifest(
    source: EtfPitCaptureSource,
    *,
    code_version: str,
    split_contract_hash: str,
    holdout_identity_hash: str,
    bootstrap_seed: int,
) -> FrozenResearchLoopManifest:
    """Build a run identity from one immutable, append-only PIT source."""

    if not _captured_source_is_valid(source):
        raise ValueError(PIT_UNAVAILABLE_SOURCE_INVALID)
    return build_frozen_research_loop_manifest(
        replay_run_key=f"production-pit:{source.source_context_hash}",
        code_version=code_version,
        source_snapshot_hash=source.source_context_hash,
        universe_manifest_hash=source.universe_manifest_hash,
        split_contract_hash=split_contract_hash,
        holdout_identity_hash=holdout_identity_hash,
        bootstrap_seed=bootstrap_seed,
        source_cutoff_policy=PIT_SOURCE_CUTOFF_POLICY,
    )


def _handler_timeout(timeout_seconds: float) -> float:
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= PIT_MAX_CONTINUATION_SECONDS:
        raise ValueError("PIT continuation timeout must be within (0, 50]")
    return min(_PIT_HANDLER_WORK_SECONDS, max(1.0, timeout_seconds - 2.0))


def _source_coverage(source: EtfPitCaptureSource) -> dict[str, dict[str, float | int | None]]:
    return {
        "production_target_date": {
            "rate": source.target_date_coverage_ratio,
            "numerator": None,
            "denominator": None,
        },
        "production_warmup": {
            "rate": source.warmup_coverage_ratio,
            "numerator": None,
            "denominator": None,
        },
    }


def _phase_absence_hash(
    manifest: FrozenResearchLoopManifest,
    *,
    phase: str,
    kind: str,
) -> str:
    """Give an intentionally unavailable immutable input an explicit identity."""

    return stable_contract_hash(
        {
            "schema_version": PIT_RESEARCH_PHASE_ARTIFACT_SCHEMA_VERSION,
            "manifest_hash": manifest.manifest_hash,
            "phase": phase,
            "absent_kind": kind,
        }
    )


def _phase_predecessor_hash(
    checkpoint: ResearchLoopCheckpoint,
) -> str:
    return stable_contract_hash(
        {
            "schema_version": PIT_RESEARCH_PHASE_ARTIFACT_SCHEMA_VERSION,
            "phase_artifact_hashes": tuple(
                sorted(
                    (phase, tuple(hashes))
                    for phase, hashes in checkpoint.phase_artifact_hashes.items()
                )
            ),
        }
    )


def _source_cutoff_hash(source: EtfPitCaptureSource) -> str:
    return stable_contract_hash(
        {
            "market_decision_cutoff": source.market_decision_cutoff,
            "data_receipt_cutoff": source.data_receipt_cutoff,
            "replay_visibility_cutoff": source.replay_visibility_cutoff,
            "timezone": source.cutoff_timezone,
        }
    )


def _artifact_write_seconds(timeout_seconds: float) -> float:
    """Reserve a short bounded tail for a durable SQLite page commit."""

    work_seconds = _handler_timeout(timeout_seconds)
    return min(4.0, max(1.0, timeout_seconds - work_seconds - 0.5))


def _phase_artifact_payload(
    *,
    phase: str,
    status: Literal["completed", "pending"],
    manifest: FrozenResearchLoopManifest,
    checkpoint: ResearchLoopCheckpoint,
    source: EtfPitCaptureSource,
    page_size: int,
    candidate_hash: str | None = None,
    outcome_hash: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind a research page to immutable identity without mutating production.

    Earlier phases intentionally carry deterministic absence identities for
    candidate/outcome inputs.  This avoids a mutable checkpoint becoming the
    only place that explains why a calculation was still pending.
    """

    return {
        "schema_version": PIT_RESEARCH_PHASE_ARTIFACT_SCHEMA_VERSION,
        "phase": phase,
        "status": status,
        "manifest_hash": manifest.manifest_hash,
        "code_version": manifest.code_version,
        "code_hash": stable_contract_hash({"code_version": manifest.code_version}),
        "input_hash": source.input_snapshot_hash,
        "cutoff_hash": _source_cutoff_hash(source),
        "predecessor_hash": _phase_predecessor_hash(checkpoint),
        "candidate_hash": candidate_hash
        or _phase_absence_hash(manifest, phase=phase, kind="candidate"),
        "outcome_hash": outcome_hash
        or _phase_absence_hash(manifest, phase=phase, kind="outcome"),
        "cost_hash": manifest.ranking_cost_contract_hash,
        "source_context_hash": source.source_context_hash,
        "source_snapshot_hash": source.source_snapshot_hash,
        "replay_run_key": manifest.replay_run_key,
        "data_cutoff": source.replay_visibility_cutoff.isoformat(),
        "cutoffs": {
            "market_decision_cutoff": source.market_decision_cutoff.isoformat(),
            "data_receipt_cutoff": source.data_receipt_cutoff.isoformat(),
            "replay_visibility_cutoff": source.replay_visibility_cutoff.isoformat(),
            "timezone": source.cutoff_timezone,
        },
        "page": {
            "single_worker": True,
            "max_pages_per_trigger": 1,
            "page_size": page_size,
            "cursor": dict(checkpoint.phase_cursor),
        },
        "research_only": True,
        "production_mutation_allowed": False,
        "details": dict(details or {}),
    }


def _write_phase_page(
    *,
    artifact_store: ReplayArtifactStore,
    manifest: FrozenResearchLoopManifest,
    phase: str,
    artifacts: tuple[tuple[str, Mapping[str, Any]], ...],
    page_size: int,
    timeout_seconds: float,
) -> str:
    """Write exactly one idempotent artifact page before checkpoint advancement.

    The coordinator persists the returned page hash only after this immutable
    SQLite commit succeeds.  If process interruption occurs between stores, a
    retry writes the same key/payload and returns the same hash, so a checkpoint
    cannot advance to an absent or duplicate page.
    """

    if not 1 <= len(artifacts) <= min(20, page_size):
        raise ValueError("PIT phase artifact page exceeds its bounded page size")
    stored = artifact_store.write_research_artifacts(
        run_id=manifest.replay_run_key,
        phase=phase,
        artifacts=artifacts,
        max_seconds=_artifact_write_seconds(timeout_seconds),
    )
    return stable_contract_hash(
        {
            "schema_version": "etf_production_pit_phase_page_v1",
            "manifest_hash": manifest.manifest_hash,
            "phase": phase,
            "artifact_hashes": tuple(item.artifact_hash for item in stored),
        }
    )


def _complete_artifact_phase(
    *,
    phase: str,
    checkpoint: ResearchLoopCheckpoint,
    manifest: FrozenResearchLoopManifest,
    artifact_store: ReplayArtifactStore,
    artifacts: tuple[tuple[str, Mapping[str, Any]], ...],
    page_size: int,
    timeout_seconds: float,
    processed_count: int,
    cursor: Mapping[str, Any],
    coverage: dict[str, dict[str, float | int | None]] | None = None,
    exclusions: dict[str, int] | None = None,
    peak_rss_bytes: int = 0,
) -> ResearchLoopPhaseResult:
    try:
        artifact_hash = _write_phase_page(
            artifact_store=artifact_store,
            manifest=manifest,
            phase=phase,
            artifacts=artifacts,
            page_size=page_size,
            timeout_seconds=timeout_seconds,
        )
    except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
        return _phase_failure(
            checkpoint,
            reason=f"pit_{phase}_artifact_write_failed:{type(exc).__name__}",
        )
    return ResearchLoopPhaseResult(
        phase_complete=True,
        processed_count=processed_count,
        artifact_hash=artifact_hash,
        cursor=dict(cursor),
        coverage=coverage or {},
        exclusions=exclusions or {},
        peak_rss_bytes=peak_rss_bytes,
    )


def _pending_artifact_phase(
    *,
    phase: str,
    checkpoint: ResearchLoopCheckpoint,
    manifest: FrozenResearchLoopManifest,
    artifact_store: ReplayArtifactStore,
    item_key: str,
    payload: Mapping[str, Any],
    page_size: int,
    timeout_seconds: float,
    reason: str,
) -> ResearchLoopPhaseResult:
    """Persist a retryable missing-input state without publishing a phase hash."""

    try:
        _write_phase_page(
            artifact_store=artifact_store,
            manifest=manifest,
            phase=phase,
            artifacts=((item_key, payload),),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
        )
    except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
        return _phase_failure(
            checkpoint,
            reason=f"pit_{phase}_artifact_write_failed:{type(exc).__name__}",
        )
    return ResearchLoopPhaseResult(
        phase_complete=False,
        processed_count=0,
        artifact_hash=None,
        cursor={
            "pending_reason": reason,
            "pending_artifact_item_key": item_key,
        },
        coverage={},
        # The immutable pending artifact is idempotent; counting it again on
        # every scheduler retry would fabricate growing exclusions.
        exclusions={},
    )


def _read_phase_page(
    *,
    artifact_store: ReplayArtifactStore,
    manifest: FrozenResearchLoopManifest,
    phase: str,
    page_size: int,
    timeout_seconds: float,
):
    return artifact_store.read_research_artifact_page(
        run_id=manifest.replay_run_key,
        phase=phase,
        max_rows=min(20, page_size),
        max_seconds=_artifact_write_seconds(timeout_seconds),
    )


def _selection_from_artifact_payload(
    payload: Mapping[str, Any],
) -> RankingCandidateSelection:
    details = _mapping(payload.get("details"))
    selection = _mapping(details.get("selection"))
    gate_exclusions: list[tuple[str, str]] = []
    for item in selection.get("gate_exclusions") or ():
        if not isinstance(item, list | tuple) or len(item) != 2:
            raise ValueError("candidate gate exclusion is invalid")
        gate_exclusions.append((str(item[0]), str(item[1])))
    return RankingCandidateSelection(
        replay_run_key=str(selection["replay_run_key"]),
        replay_date=date.fromisoformat(str(selection["replay_date"])),
        candidate_id=str(selection["candidate_id"]),
        candidate_manifest_hash=str(selection["candidate_manifest_hash"]),
        candidate_registry_hash=str(selection["candidate_registry_hash"]),
        source_ranking_event_hash=str(selection["source_ranking_event_hash"]),
        selected_asset_codes=tuple(
            str(item) for item in selection.get("selected_asset_codes") or ()
        ),
        underlying_hysteresis_asset_codes=tuple(
            str(item)
            for item in selection.get("underlying_hysteresis_asset_codes") or ()
        ),
        entered_asset_codes=tuple(
            str(item) for item in selection.get("entered_asset_codes") or ()
        ),
        exited_asset_codes=tuple(
            str(item) for item in selection.get("exited_asset_codes") or ()
        ),
        retained_asset_codes=tuple(
            str(item) for item in selection.get("retained_asset_codes") or ()
        ),
        gate_exclusions=tuple(gate_exclusions),
        selection_hash=str(selection["selection_hash"]),
    )


def _hashes_from_phase_artifacts(
    artifacts: tuple[Any, ...],
    *,
    key: str,
    fallback: str,
) -> str:
    values = tuple(
        sorted(
            str(_mapping(item.payload).get(key))
            for item in artifacts
            if _is_sha256(_mapping(item.payload).get(key))
        )
    )
    return stable_contract_hash(
        {"key": key, "values": values, "fallback": fallback}
    )


def _prospective_count(
    source: EtfPitCaptureSource,
    *,
    key: str,
) -> int:
    prospective = _mapping(_mapping(source.source_context_json).get("prospective_evidence"))
    value = prospective.get(key)
    if isinstance(value, list | tuple):
        return len({str(item) for item in value if str(item).strip()})
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _research_readiness_evidence(
    *,
    source: EtfPitCaptureSource,
    checkpoint: ResearchLoopCheckpoint,
) -> dict[str, Any]:
    """State factual operational and statistical gates without inferring alpha."""

    factual_sessions = _prospective_count(source, key="source_trade_dates")
    if factual_sessions == 0:
        factual_sessions = 1
    independent_dates = _prospective_count(source, key="eligible_primary_dates")
    if independent_dates == 0:
        independent_dates = _prospective_count(
            source,
            key="independent_primary_date_count",
        )
    folds = _prospective_count(source, key="completed_walk_forward_fold_count")
    pending_windows = checkpoint.exclusions.get(
        PIT_PENDING_FUTURE_EXCHANGE_SESSIONS,
        0,
    )
    pending_phase_reason = checkpoint.phase_cursor.get("pending_reason")
    pending_phase_blockers = (
        (str(pending_phase_reason),)
        if isinstance(pending_phase_reason, str) and pending_phase_reason.strip()
        else ()
    )
    promotion = evaluate_research_promotion(
        PromotionGateEvidence(
            decision_data_coverage_ratio=source.target_date_coverage_ratio,
            score_coverage_ratio=source.warmup_coverage_ratio,
            eligible_point_in_time_sessions=factual_sessions,
            independent_primary_dates=independent_dates,
            completed_walk_forward_folds=folds,
            adjusted_primary_interval_lower=None,
            fold_sign_stable=False,
            regime_sign_stable=False,
            candidate_maximum_drawdown=None,
            baseline_maximum_drawdown=None,
            exclusion_gate_passed=(
                pending_windows == 0
                and not pending_phase_blockers
                and not any(
                    count
                    for reason, count in checkpoint.exclusions.items()
                    if reason != PIT_PENDING_FUTURE_EXCHANGE_SESSIONS
                )
            ),
            holdout_consumed=False,
        )
    )
    ranking_ready = persisted_etf_complete_coverage_allowed(
        policy_version=source.readiness_policy_version,
        daily_coverage_ratio=source.target_date_coverage_ratio,
        warmup_coverage_ratio=source.warmup_coverage_ratio,
    )
    exclusion_blockers = tuple(
        {
            "reason": reason,
            "count": count,
        }
        for reason, count in sorted(checkpoint.exclusions.items())
        if count
    )
    blockers = list(promotion.failed_gates)
    if pending_windows:
        blockers.append(PIT_PENDING_FUTURE_EXCHANGE_SESSIONS)
    blockers.extend(pending_phase_blockers)
    blockers.extend(item["reason"] for item in exclusion_blockers)
    research_ready = (
        promotion.state.value == "promotion_eligible"
        and pending_windows == 0
        and not pending_phase_blockers
    )
    return {
        "ranking_ready": ranking_ready,
        "research_ready": research_ready,
        "factual_pit_session_count": factual_sessions,
        "non_overlapping_primary_date_count": independent_dates,
        "completed_walk_forward_fold_count": folds,
        "pending_window_count": pending_windows,
        "pending_phase_blockers": list(pending_phase_blockers),
        "exclusion_blockers": list(exclusion_blockers),
        "promotion_state": promotion.state.value,
        "promotion_blockers": tuple(dict.fromkeys(blockers)),
        "production_weights_frozen": True,
        "research_only": True,
        "production_mutation_allowed": False,
    }


def _production_pit_checkpoint_view(
    *,
    source: EtfPitCaptureSource,
    checkpoint: ResearchLoopCheckpoint,
) -> dict[str, Any]:
    readiness = _research_readiness_evidence(
        source=source,
        checkpoint=checkpoint,
    )
    return {
        **research_loop_checkpoint_view(checkpoint),
        "ranking_ready": readiness["ranking_ready"],
        "research_ready": readiness["research_ready"],
        "readiness": readiness,
    }


def _handler_source_identity_matches(
    manifest: FrozenResearchLoopManifest,
    source: EtfPitCaptureSource,
) -> bool:
    return (
        manifest.source_snapshot_hash == source.source_context_hash
        and manifest.universe_manifest_hash == source.universe_manifest_hash
        and manifest.source_cutoff_policy == PIT_SOURCE_CUTOFF_POLICY
    )


def _stage_a_contract(
    manifest: FrozenResearchLoopManifest,
) -> StageAReplayContract:
    return StageAReplayContract(
        replay_run_key=manifest.replay_run_key,
        score_manifest_hash=manifest.research_contract_hash,
        source_snapshot_hash=manifest.source_snapshot_hash,
        universe_manifest_hash=manifest.universe_manifest_hash,
        decision_cutoff_semantics="asia_shanghai_post_close_v1",
        schema_version=STAGE_A_SCHEMA_VERSION,
        candidate_registry_hash=manifest.candidate_registry_hash,
    )


def _stage_b_event_matches_source(
    *,
    event: StageBRankingEvent,
    manifest: FrozenResearchLoopManifest,
    source: EtfPitCaptureSource,
) -> bool:
    return (
        event.replay_run_key == manifest.replay_run_key
        and event.replay_date == source.as_of_trade_date
        and event.score_manifest_hash == manifest.research_contract_hash
    )


def _phase_failure(
    checkpoint: ResearchLoopCheckpoint,
    *,
    reason: str,
    exclusions: dict[str, int] | None = None,
) -> ResearchLoopPhaseResult:
    return ResearchLoopPhaseResult(
        phase_complete=False,
        processed_count=0,
        artifact_hash=None,
        cursor={**checkpoint.phase_cursor, "unavailable_reason": reason},
        coverage={},
        exclusions=exclusions or {reason: 1},
        outcome="failed",
        error_summary=reason,
    )


def build_production_pit_phase_handlers(
    *,
    session: AsyncSession,
    source: EtfPitCaptureSource,
    artifact_store: ReplayArtifactStore,
) -> ResearchLoopPhaseHandlers:
    """Bind bounded, persisted-data-only PIT pages to existing pure components."""

    if not _captured_source_is_valid(source):
        raise ValueError(PIT_UNAVAILABLE_SOURCE_INVALID)

    async def inputs(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        payload = _phase_artifact_payload(
            phase="inputs",
            status="completed",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=manifest.candidate_registry_hash,
            details={
                "source_id": source.id,
                "as_of_trade_date": source.as_of_trade_date.isoformat(),
                "universe_manifest_hash": source.universe_manifest_hash,
                "readiness_policy_version": source.readiness_policy_version,
                "target_date_coverage_ratio": source.target_date_coverage_ratio,
                "warmup_coverage_ratio": source.warmup_coverage_ratio,
            },
        )
        return _complete_artifact_phase(
            phase="inputs",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=((f"source:{source.id}", payload),),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=1,
            cursor={"source_id": source.id},
            coverage=_source_coverage(source),
            exclusions={},
        )

    async def stage_a(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        max_codes = min(page_size, MAX_CODES_PER_REPLAY_INPUT_PAGE)
        work_seconds = _handler_timeout(timeout_seconds)
        try:
            progress = await run_stage_a_loader_job(
                session=session,
                store=artifact_store,
                contract=_stage_a_contract(manifest),
                request=StageABatchRequest(
                    max_source_rows=REQUIRED_BAR_COUNT * max_codes,
                    max_items=max_codes,
                    max_pages=1,
                    max_seconds=work_seconds,
                    worker_count=1,
                ),
                replay_dates=(source.as_of_trade_date,),
                decision_cutoffs=(
                    (
                        source.as_of_trade_date,
                        source.replay_visibility_cutoff.replace(tzinfo=_SHANGHAI),
                    ),
                ),
                max_codes_per_page=max_codes,
            )
        except (
            StageABoundedWorkError,
            StageAArtifactConflictError,
            NewStageAReplayIdentityRequiredError,
            ValueError,
        ) as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_stage_a_unavailable:{type(exc).__name__}",
            )
        if progress.status == "blocked":
            return _phase_failure(
                checkpoint,
                reason=f"pit_stage_a_blocked:{progress.reason or 'unknown'}",
            )
        cursor = {
            "stage_a_generation": progress.generation,
            "stage_a_next_cursor": (
                [
                    progress.next_cursor[0].isoformat(),
                    progress.next_cursor[1],
                ]
                if progress.next_cursor is not None
                else None
            ),
        }
        if not progress.complete:
            return ResearchLoopPhaseResult(
                phase_complete=False,
                processed_count=progress.processed_items,
                artifact_hash=None,
                cursor=cursor,
                coverage={},
                exclusions={},
                peak_rss_bytes=progress.peak_rss_bytes,
            )
        payload = _phase_artifact_payload(
            phase="stage_a",
            status="completed",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=manifest.candidate_registry_hash,
            details={
                "stage_a_completion_identity_hash": progress.completion_identity_hash,
                "stage_a_generation": progress.generation,
                "processed_items": progress.processed_items,
            },
        )
        return _complete_artifact_phase(
            phase="stage_a",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=((f"source:{source.id}", payload),),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=progress.processed_items,
            cursor=cursor,
            peak_rss_bytes=progress.peak_rss_bytes,
        )

    async def stage_b(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        stage_a_contract = _stage_a_contract(manifest)
        stage_b_contract = stage_b_contract_from_stage_a(stage_a_contract)
        work_seconds = _handler_timeout(timeout_seconds)
        try:
            source_page = read_stage_b_source_date_page_from_stage_a(
                store=artifact_store,
                stage_a_contract=stage_a_contract,
                stage_b_contract=stage_b_contract,
                after_cursor=None,
                max_dates=1,
                max_feature_rows=_PIT_STAGE_B_MAX_FEATURE_ROWS,
                max_seconds=work_seconds,
            )
            if not source_page.source_dates:
                return _phase_failure(
                    checkpoint,
                    reason="pit_stage_b_source_dates_unavailable",
                )
            progress = run_stage_b_continuation(
                store=artifact_store,
                contract=stage_b_contract,
                request=StageBBatchRequest(
                    max_dates=1,
                    max_feature_rows=_PIT_STAGE_B_MAX_FEATURE_ROWS,
                    max_seconds=work_seconds,
                    worker_count=1,
                ),
                source_dates=source_page.source_dates,
                source_has_more=source_page.has_more,
            )
        except (
            StageBBoundedWorkError,
            StageBArtifactConflictError,
            NewStageBReplayIdentityRequiredError,
            ValueError,
        ) as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_stage_b_unavailable:{type(exc).__name__}",
            )
        cursor = {
            "stage_b_generation": progress.generation,
            "stage_b_next_after_date": (
                progress.next_after_date.isoformat()
                if progress.next_after_date is not None
                else None
            ),
        }
        if not progress.complete:
            return ResearchLoopPhaseResult(
                phase_complete=False,
                processed_count=progress.processed_dates,
                artifact_hash=None,
                cursor=cursor,
                coverage={},
                exclusions={},
            )
        payload = _phase_artifact_payload(
            phase="stage_b",
            status="completed",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=manifest.candidate_registry_hash,
            details={
                "stage_b_completion_identity_hash": progress.completion_identity_hash,
                "stage_b_generation": progress.generation,
                "processed_dates": progress.processed_dates,
                "stage_b_event_chain_hash": progress.event_chain_hash,
                "stage_b_manifest_chain_hash": progress.manifest_chain_hash,
            },
        )
        return _complete_artifact_phase(
            phase="stage_b",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=((f"source:{source.id}", payload),),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=progress.processed_dates,
            cursor=cursor,
        )

    async def candidates(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        try:
            event_page = read_stage_b_ranking_event_page(
                store=artifact_store,
                replay_run_key=manifest.replay_run_key,
                after_date=None,
                max_rows=1,
                max_seconds=_artifact_write_seconds(timeout_seconds),
            )
        except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_candidates_stage_b_read_failed:{type(exc).__name__}",
            )
        if not event_page.rows:
            payload = _phase_artifact_payload(
                phase="candidates",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "unavailable_inputs": ["stage_b_ranking_event"],
                    "reason": PIT_PENDING_STAGE_B_RANKING_EVENT,
                },
            )
            return _pending_artifact_phase(
                phase="candidates",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key="pending:stage-b-ranking-event",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason=PIT_PENDING_STAGE_B_RANKING_EVENT,
            )
        event = event_page.rows[0]
        try:
            if not _stage_b_event_matches_source(
                event=event,
                manifest=manifest,
                source=source,
            ):
                raise ValueError("Stage B event is outside the immutable PIT source")
            registry = freeze_ranking_candidate_registry(
                FROZEN_RANKING_CANDIDATES
            )
            if registry.registry_hash != manifest.candidate_registry_hash:
                raise ValueError("frozen candidate registry is incompatible")
            batch = evaluate_ranking_candidates(
                ranking_event=event,
                registry=registry,
                previous_states={},
                gate_facts=(),
            )
        except ValueError as exc:
            payload = _phase_artifact_payload(
                phase="candidates",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "stage_b_event_hash": event.event_hash,
                    "unavailable_inputs": ["valid_stage_b_ranking_event"],
                    "reason": f"pit_candidate_contract_pending:{type(exc).__name__}",
                },
            )
            return _pending_artifact_phase(
                phase="candidates",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key=f"pending:stage-b-contract:{event.replay_date.isoformat()}",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason=PIT_PENDING_STAGE_B_RANKING_EVENT,
            )

        artifacts: list[tuple[str, Mapping[str, Any]]] = []
        exclusions: dict[str, int] = {}
        for selection, state in zip(batch.selections, batch.states, strict=True):
            for _asset_code, reason in selection.gate_exclusions:
                exclusions[reason] = exclusions.get(reason, 0) + 1
            artifacts.append(
                (
                    f"candidate:{selection.candidate_id}",
                    _phase_artifact_payload(
                        phase="candidates",
                        status="completed",
                        manifest=manifest,
                        checkpoint=checkpoint,
                        source=source,
                        page_size=page_size,
                        candidate_hash=selection.selection_hash,
                        details={
                            "stage_b_event_hash": event.event_hash,
                            "candidate_batch_hash": batch.batch_hash,
                            "selection": asdict(selection),
                            "state": asdict(state),
                            "gate_fact_status": (
                                "missing_fail_closed"
                                if selection.gate_exclusions
                                else "not_required_or_satisfied"
                            ),
                            "unavailable_inputs": (
                                ["regime_liquidity_gate_facts"]
                                if selection.gate_exclusions
                                else []
                            ),
                        },
                    ),
                )
            )
        return _complete_artifact_phase(
            phase="candidates",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=tuple(artifacts),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=len(artifacts),
            cursor={
                "stage_b_replay_date": event.replay_date.isoformat(),
                "stage_b_event_hash": event.event_hash,
            },
            exclusions=exclusions,
        )

    async def forward_outcomes(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        try:
            candidate_artifacts = _read_phase_page(
                artifact_store=artifact_store,
                manifest=manifest,
                phase="candidates",
                page_size=page_size,
                timeout_seconds=timeout_seconds,
            )
        except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_forward_outcomes_candidate_read_failed:{type(exc).__name__}",
            )
        if not candidate_artifacts:
            payload = _phase_artifact_payload(
                phase="forward_outcomes",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "unavailable_inputs": ["candidate_selection_artifacts"],
                    "reason": "pit_candidate_selection_artifacts_pending",
                },
            )
            return _pending_artifact_phase(
                phase="forward_outcomes",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key="pending:candidate-selection-artifacts",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason="pit_candidate_selection_artifacts_pending",
            )

        artifacts: list[tuple[str, Mapping[str, Any]]] = []
        exclusions: dict[str, int] = {}
        for stored in candidate_artifacts:
            payload = _mapping(stored.payload)
            try:
                selection = _selection_from_artifact_payload(payload)
                bundle = calculate_ranking_forward_outcomes(
                    selection=selection,
                    trading_sessions=(selection.replay_date,),
                    adjusted_closes=(),
                )
            except (KeyError, TypeError, ValueError) as exc:
                artifacts.append(
                    (
                        f"pending:{stored.item_key}",
                        _phase_artifact_payload(
                            phase="forward_outcomes",
                            status="pending",
                            manifest=manifest,
                            checkpoint=checkpoint,
                            source=source,
                            page_size=page_size,
                            candidate_hash=stored.artifact_hash,
                            details={
                                "unavailable_inputs": ["valid_candidate_selection"],
                                "reason": (
                                    "pit_candidate_selection_contract_pending:"
                                    f"{type(exc).__name__}"
                                ),
                            },
                        ),
                    )
                )
                exclusions["pit_candidate_selection_contract_pending"] = (
                    exclusions.get("pit_candidate_selection_contract_pending", 0)
                    + 1
                )
                continue
            if bundle.pending_outcome_count:
                exclusions[PIT_PENDING_FUTURE_EXCHANGE_SESSIONS] = (
                    exclusions.get(PIT_PENDING_FUTURE_EXCHANGE_SESSIONS, 0)
                    + bundle.pending_outcome_count
                )
            artifacts.append(
                (
                    f"candidate:{selection.candidate_id}",
                    _phase_artifact_payload(
                        phase="forward_outcomes",
                        status=(
                            "pending"
                            if bundle.pending_outcome_count
                            else "completed"
                        ),
                        manifest=manifest,
                        checkpoint=checkpoint,
                        source=source,
                        page_size=page_size,
                        candidate_hash=selection.selection_hash,
                        outcome_hash=bundle.bundle_hash,
                        details={
                            "selection_hash": selection.selection_hash,
                            "outcome_bundle": asdict(bundle),
                            "outcome_state": (
                                "future_windows_pending"
                                if bundle.pending_outcome_count
                                else "completed_no_selected_assets"
                            ),
                            "unavailable_inputs": (
                                [
                                    "exact_next_exchange_entry_session",
                                    "exact_horizon_exit_exchange_sessions",
                                ]
                                if bundle.pending_outcome_count
                                else []
                            ),
                            "no_intraday_reconstruction": True,
                        },
                    ),
                )
            )
        return _complete_artifact_phase(
            phase="forward_outcomes",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=tuple(artifacts),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=len(artifacts),
            cursor={"candidate_artifact_count": len(candidate_artifacts)},
            exclusions=exclusions,
        )

    async def ranking_validation(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        try:
            forward_artifacts = _read_phase_page(
                artifact_store=artifact_store,
                manifest=manifest,
                phase="forward_outcomes",
                page_size=page_size,
                timeout_seconds=timeout_seconds,
            )
            event_page = read_stage_b_ranking_event_page(
                store=artifact_store,
                replay_run_key=manifest.replay_run_key,
                after_date=None,
                max_rows=1,
                max_seconds=_artifact_write_seconds(timeout_seconds),
            )
        except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_validation_artifact_read_failed:{type(exc).__name__}",
            )
        if not forward_artifacts or not event_page.rows:
            missing_inputs = []
            if not forward_artifacts:
                missing_inputs.append("forward_outcome_artifacts")
            if not event_page.rows:
                missing_inputs.append("stage_b_ranking_event")
            payload = _phase_artifact_payload(
                phase="ranking_validation",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "unavailable_inputs": missing_inputs,
                    "reason": PIT_PENDING_VALIDATION_INPUTS,
                },
            )
            return _pending_artifact_phase(
                phase="ranking_validation",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key="pending:validation-inputs",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason=PIT_PENDING_VALIDATION_INPUTS,
            )

        event: StageBRankingEvent = event_page.rows[0]
        try:
            if not _stage_b_event_matches_source(
                event=event,
                manifest=manifest,
                source=source,
            ):
                raise ValueError("Stage B event is outside the immutable PIT source")
            source_event = freeze_ranking_validation_source_event(
                RankingValidationSourceEvent(
                    ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
                    signal_date=event.replay_date,
                    source_signal_run_id=None,
                    source_replay_run_key=manifest.replay_run_key,
                    source_replay_contract_hash=manifest.research_contract_hash,
                    source_event_hash=event.event_hash,
                    ranking_contract_hash=manifest.research_contract_hash,
                    scope_hash=source.source_context_hash,
                    universe_snapshot_hash=source.universe_manifest_hash,
                    input_snapshot_hash=source.input_snapshot_hash,
                    score_version=RESEARCH_SCORE_VERSION,
                    score_field=RESEARCH_SCORE_FIELD,
                    rule_version=RESEARCH_RULE_VERSION,
                    price_basis="total_return_adjusted",
                    publication_state=None,
                    scope_kind="research_replay",
                    availability_cutoff=source.replay_visibility_cutoff.replace(
                        tzinfo=_SHANGHAI
                    ),
                    immutable_hash="pending",
                )
            )
            cohort = freeze_ranking_validation_source_cohort(
                ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
                events=(source_event,),
            )
        except ValueError as exc:
            payload = _phase_artifact_payload(
                phase="ranking_validation",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "stage_b_event_hash": event.event_hash,
                    "unavailable_inputs": ["valid_research_validation_source"],
                    "reason": f"pit_validation_source_contract_pending:{type(exc).__name__}",
                },
            )
            return _pending_artifact_phase(
                phase="ranking_validation",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key=f"pending:validation-source:{event.replay_date.isoformat()}",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason=PIT_PENDING_VALIDATION_INPUTS,
            )

        pending_windows = sum(
            int(
                _mapping(_mapping(item.payload).get("details"))
                .get("outcome_bundle", {})
                .get("pending_outcome_count", 0)
            )
            for item in forward_artifacts
        )
        candidate_hash = _hashes_from_phase_artifacts(
            forward_artifacts,
            key="candidate_hash",
            fallback=manifest.candidate_registry_hash,
        )
        outcome_hash = _hashes_from_phase_artifacts(
            forward_artifacts,
            key="outcome_hash",
            fallback=_phase_absence_hash(
                manifest,
                phase="ranking_validation",
                kind="outcome",
            ),
        )
        payload = _phase_artifact_payload(
            phase="ranking_validation",
            status="pending",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=candidate_hash,
            outcome_hash=outcome_hash,
            details={
                "validation_source_cohort": asdict(cohort),
                "source_cohort_hash": cohort.cohort_hash,
                "pending_window_count": pending_windows,
                "unavailable_inputs": [
                    "completed_common_support_forward_outcomes",
                    "paired_candidate_baseline_samples",
                    "chronological_walk_forward_folds",
                    "one_time_holdout_consumption_state",
                ],
                "reason": PIT_PENDING_VALIDATION_INPUTS,
            },
        )
        return _complete_artifact_phase(
            phase="ranking_validation",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=((f"cohort:{event.replay_date.isoformat()}", payload),),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=1,
            cursor={"source_cohort_hash": cohort.cohort_hash},
            exclusions={PIT_PENDING_VALIDATION_INPUTS: 1},
        )

    async def factor_evidence(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        try:
            validation_artifacts = _read_phase_page(
                artifact_store=artifact_store,
                manifest=manifest,
                phase="ranking_validation",
                page_size=page_size,
                timeout_seconds=timeout_seconds,
            )
        except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_factor_validation_read_failed:{type(exc).__name__}",
            )
        if not validation_artifacts:
            payload = _phase_artifact_payload(
                phase="factor_evidence",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "component": build_operational_factor_evidence.__name__,
                    "unavailable_inputs": ["ranking_validation_artifact"],
                    "reason": PIT_PENDING_FACTOR_INPUTS,
                },
            )
            return _pending_artifact_phase(
                phase="factor_evidence",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key="pending:ranking-validation-artifact",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason=PIT_PENDING_FACTOR_INPUTS,
            )
        candidate_hash = _hashes_from_phase_artifacts(
            validation_artifacts,
            key="candidate_hash",
            fallback=manifest.candidate_registry_hash,
        )
        outcome_hash = _hashes_from_phase_artifacts(
            validation_artifacts,
            key="outcome_hash",
            fallback=_phase_absence_hash(
                manifest,
                phase="factor_evidence",
                kind="outcome",
            ),
        )
        readiness = _research_readiness_evidence(
            source=source,
            checkpoint=checkpoint,
        )
        payload = _phase_artifact_payload(
            phase="factor_evidence",
            status="pending",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=candidate_hash,
            outcome_hash=outcome_hash,
            details={
                "component": build_operational_factor_evidence.__name__,
                "promotion": {
                    "state": readiness["promotion_state"],
                    "failed_gates": readiness["promotion_blockers"],
                    "production_mutation_allowed": False,
                },
                "unavailable_inputs": [
                    "primary_ranking_endpoint_result",
                    "exploratory_ranking_endpoint_results",
                    "residual_common_support_factor_diagnostics",
                    "holm_adjusted_primary_intervals",
                ],
                "reason": PIT_PENDING_FACTOR_INPUTS,
            },
        )
        return _complete_artifact_phase(
            phase="factor_evidence",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=(("factor-evidence", payload),),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=1,
            cursor={"promotion_state": readiness["promotion_state"]},
            exclusions={PIT_PENDING_FACTOR_INPUTS: 1},
        )

    async def policy_shadow(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        try:
            factor_artifacts = _read_phase_page(
                artifact_store=artifact_store,
                manifest=manifest,
                phase="factor_evidence",
                page_size=page_size,
                timeout_seconds=timeout_seconds,
            )
        except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_policy_factor_read_failed:{type(exc).__name__}",
            )
        if not factor_artifacts:
            payload = _phase_artifact_payload(
                phase="policy_shadow",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "component": build_policy_shadow_surface.__name__,
                    "unavailable_inputs": ["factor_evidence_artifact"],
                    "reason": PIT_PENDING_POLICY_INPUTS,
                },
            )
            return _pending_artifact_phase(
                phase="policy_shadow",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key="pending:factor-evidence-artifact",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason=PIT_PENDING_POLICY_INPUTS,
            )
        candidate_hash = _hashes_from_phase_artifacts(
            factor_artifacts,
            key="candidate_hash",
            fallback=manifest.candidate_registry_hash,
        )
        outcome_hash = _hashes_from_phase_artifacts(
            factor_artifacts,
            key="outcome_hash",
            fallback=_phase_absence_hash(
                manifest,
                phase="policy_shadow",
                kind="outcome",
            ),
        )
        payload = _phase_artifact_payload(
            phase="policy_shadow",
            status="pending",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=candidate_hash,
            outcome_hash=outcome_hash,
            details={
                "component": build_policy_shadow_surface.__name__,
                "unavailable_inputs": [
                    "complete_point_in_time_top20_date_manifests",
                    "candidate_action_cycle_endpoint_result",
                    "development_gate_artifact",
                    "directional_policy_diagnostics",
                ],
                "reason": PIT_PENDING_POLICY_INPUTS,
                "notification_provenance": "shadow_only",
                "execution_provenance": "no_execution_or_smtp",
            },
        )
        return _complete_artifact_phase(
            phase="policy_shadow",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=(("policy-shadow", payload),),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=1,
            cursor={"policy_mode": "policy_shadow"},
            exclusions={PIT_PENDING_POLICY_INPUTS: 1},
        )

    async def final_evidence(
        manifest: FrozenResearchLoopManifest,
        checkpoint: ResearchLoopCheckpoint,
        page_size: int,
        timeout_seconds: float,
    ) -> ResearchLoopPhaseResult:
        if not _handler_source_identity_matches(manifest, source):
            return _phase_failure(
                checkpoint,
                reason=PIT_UNAVAILABLE_SOURCE_INVALID,
            )
        try:
            factor_artifacts = _read_phase_page(
                artifact_store=artifact_store,
                manifest=manifest,
                phase="factor_evidence",
                page_size=page_size,
                timeout_seconds=timeout_seconds,
            )
            policy_artifacts = _read_phase_page(
                artifact_store=artifact_store,
                manifest=manifest,
                phase="policy_shadow",
                page_size=page_size,
                timeout_seconds=timeout_seconds,
            )
        except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_final_artifact_read_failed:{type(exc).__name__}",
            )
        if not factor_artifacts or not policy_artifacts:
            missing_inputs = []
            if not factor_artifacts:
                missing_inputs.append("factor_evidence_artifact")
            if not policy_artifacts:
                missing_inputs.append("policy_shadow_artifact")
            payload = _phase_artifact_payload(
                phase="final_evidence",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "unavailable_inputs": missing_inputs,
                    "reason": PIT_PENDING_FINAL_INPUTS,
                },
            )
            return _pending_artifact_phase(
                phase="final_evidence",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key="pending:final-evidence-inputs",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason=PIT_PENDING_FINAL_INPUTS,
            )
        contributing_artifacts = (*factor_artifacts, *policy_artifacts)
        candidate_hash = _hashes_from_phase_artifacts(
            contributing_artifacts,
            key="candidate_hash",
            fallback=manifest.candidate_registry_hash,
        )
        outcome_hash = _hashes_from_phase_artifacts(
            contributing_artifacts,
            key="outcome_hash",
            fallback=_phase_absence_hash(
                manifest,
                phase="final_evidence",
                kind="outcome",
            ),
        )
        readiness = _research_readiness_evidence(
            source=source,
            checkpoint=checkpoint,
        )
        payload = _phase_artifact_payload(
            phase="final_evidence",
            status="pending" if not readiness["research_ready"] else "completed",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=candidate_hash,
            outcome_hash=outcome_hash,
            details={
                "readiness": readiness,
                "unavailable_inputs": (
                    list(readiness["promotion_blockers"])
                    if not readiness["research_ready"]
                    else []
                ),
                "reason": (
                    PIT_PENDING_FINAL_INPUTS
                    if not readiness["research_ready"]
                    else None
                ),
                "holdout_mutated": False,
                "production_weights_mutated": False,
                "notification_or_execution_mutated": False,
            },
        )
        return _complete_artifact_phase(
            phase="final_evidence",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=(("final-evidence", payload),),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=1,
            cursor={
                "ranking_ready": readiness["ranking_ready"],
                "research_ready": readiness["research_ready"],
            },
            exclusions=(
                {PIT_PENDING_FINAL_INPUTS: 1}
                if not readiness["research_ready"]
                else {}
            ),
        )

    return ResearchLoopPhaseHandlers(
        inputs=inputs,
        stage_a=stage_a,
        stage_b=stage_b,
        candidates=candidates,
        forward_outcomes=forward_outcomes,
        ranking_validation=ranking_validation,
        factor_evidence=factor_evidence,
        policy_shadow=policy_shadow,
        final_evidence=final_evidence,
    )


async def advance_production_pit_once(
    session: AsyncSession,
    *,
    source_id: int,
    artifact_store: ReplayArtifactStore,
    code_version: str,
    split_contract_hash: str,
    holdout_identity_hash: str,
    bootstrap_seed: int,
    timeout_seconds: float = PIT_MAX_CONTINUATION_SECONDS,
) -> PitContinuationResult:
    """Advance exactly one bounded research page for one captured PIT source."""

    try:
        _handler_timeout(timeout_seconds)
    except ValueError:
        raise
    source = await session.get(EtfPitCaptureSource, source_id)
    if source is None:
        return PitContinuationResult(
            state="unavailable",
            unavailable_reason=PIT_UNAVAILABLE_SOURCE_NOT_FOUND,
            source_id=None,
            manifest_hash=None,
            checkpoint=None,
        )
    captured_source_id = source.id
    try:
        manifest = build_production_pit_manifest(
            source,
            code_version=code_version,
            split_contract_hash=split_contract_hash,
            holdout_identity_hash=holdout_identity_hash,
            bootstrap_seed=bootstrap_seed,
        )
        handlers = build_production_pit_phase_handlers(
            session=session,
            source=source,
            artifact_store=artifact_store,
        )
    except ValueError as exc:
        return PitContinuationResult(
            state="unavailable",
            unavailable_reason=str(exc) or PIT_UNAVAILABLE_SOURCE_INVALID,
            source_id=source.id,
            manifest_hash=None,
            checkpoint=None,
        )
    try:
        checkpoint = await run_bounded_research_loop_continuation(
            session,
            manifest=manifest,
            execute_phase=handlers.execute,
            timeout_seconds=timeout_seconds,
        )
    except ResearchLoopBusyError:
        return PitContinuationResult(
            state="unavailable",
            unavailable_reason=PIT_UNAVAILABLE_MANIFEST_LEASE,
            source_id=captured_source_id,
            manifest_hash=manifest.manifest_hash,
            checkpoint=None,
        )
    return PitContinuationResult(
        state="advanced",
        unavailable_reason=None,
        source_id=source.id,
        manifest_hash=manifest.manifest_hash,
        checkpoint=_production_pit_checkpoint_view(
            source=source,
            checkpoint=checkpoint,
        ),
    )


async def capture_and_advance_production_pit_once(
    session: AsyncSession,
    *,
    source_signal_run_id: int,
    provider_health_hash: str,
    replay_visibility_cutoff: datetime | None,
    provider_health_identity: Mapping[str, Any] | None = None,
    artifact_store: ReplayArtifactStore,
    code_version: str,
    split_contract_hash: str,
    holdout_identity_hash: str,
    bootstrap_seed: int,
    timeout_seconds: float = PIT_MAX_CONTINUATION_SECONDS,
) -> PitContinuationResult:
    """Capture one complete source idempotently, then advance no more than one page."""

    captured = await capture_complete_pit_source(
        session,
        source_signal_run_id=source_signal_run_id,
        provider_health_hash=provider_health_hash,
        provider_health_identity=provider_health_identity,
        replay_visibility_cutoff=replay_visibility_cutoff,
    )
    if captured.source is None:
        return PitContinuationResult(
            state="unavailable",
            unavailable_reason=captured.unavailable_reason,
            source_id=None,
            manifest_hash=None,
            checkpoint=None,
        )
    return await advance_production_pit_once(
        session,
        source_id=captured.source.id,
        artifact_store=artifact_store,
        code_version=code_version,
        split_contract_hash=split_contract_hash,
        holdout_identity_hash=holdout_identity_hash,
        bootstrap_seed=bootstrap_seed,
        timeout_seconds=timeout_seconds,
    )


async def run_scheduled_production_pit_capture(
    session: AsyncSession,
    *,
    trade_date: date,
    enabled: bool,
    code_version: str,
    artifact_dir: str,
    timeout_seconds: float = PIT_MAX_CONTINUATION_SECONDS,
) -> dict[str, Any]:
    """Run one due production PIT page using persisted facts only."""

    preflight = await preflight_production_pit_capture(
        session,
        trade_date=trade_date,
        enabled=enabled,
        code_version=code_version,
    )
    if (
        not preflight.due
        or preflight.source_signal_run_id is None
        or preflight.provider_health_hash is None
        or preflight.provider_health_identity is None
        or preflight.replay_visibility_cutoff is None
    ):
        return {
            "status": "skipped",
            **preflight.to_dict(),
            "research_only": True,
            "production_mutation_allowed": False,
        }
    store_path = (
        Path(artifact_dir)
        / trade_date.isoformat()
        / f"source-{preflight.source_signal_run_id}.sqlite3"
    )
    result = await capture_and_advance_production_pit_once(
        session,
        source_signal_run_id=preflight.source_signal_run_id,
        provider_health_hash=preflight.provider_health_hash,
        provider_health_identity=preflight.provider_health_identity,
        replay_visibility_cutoff=preflight.replay_visibility_cutoff,
        artifact_store=ReplayArtifactStore(store_path),
        code_version=code_version.strip(),
        split_contract_hash=PIT_SPLIT_CONTRACT_HASH,
        holdout_identity_hash=PIT_HOLDOUT_IDENTITY_HASH,
        bootstrap_seed=PIT_BOOTSTRAP_SEED,
        timeout_seconds=timeout_seconds,
    )
    return {
        "status": "success" if result.state == "advanced" else "skipped",
        "state": result.state,
        "unavailable_reason": result.unavailable_reason,
        "trade_date": trade_date.isoformat(),
        "source_signal_run_id": preflight.source_signal_run_id,
        "source_id": result.source_id,
        "manifest_hash": result.manifest_hash,
        "checkpoint": result.checkpoint,
        "provider_health_hash": preflight.provider_health_hash,
        "replay_visibility_cutoff": (
            preflight.replay_visibility_cutoff.isoformat()
        ),
        "artifact_path": str(store_path),
        "research_only": True,
        "production_mutation_allowed": False,
    }
