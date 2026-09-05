"""Production composition for one bounded, research-only ETF PIT page.

The workflow deliberately consumes only immutable complete-publication facts and
persisted adjusted data.  It does not request live providers, publish a ranking,
or touch allocation, tracking, risk-alert, or notification state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
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
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    utcnow,
)
from app.services import market_data
from app.services.etf_research_evidence import RankingSourceKind, stable_contract_hash
from app.services.intraday_etf.evidence import (
    PIT_CAPTURE_SOURCE_OWNER,
    seal_intraday_quote_evidence,
)
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
from app.services.strategy_lab.etf_factor_deconfounding import (
    build_deconfounding_evidence,
)
from app.services.strategy_lab.etf_factor_evidence import (
    build_operational_factor_evidence,
    persist_factor_evidence,
)
from app.services.strategy_lab.etf_factor_experiment import (
    ChronologicalSplit,
    HoldoutConsumption,
    consume_holdout_once,
    holdout_consumption_artifact,
    restore_holdout_consumption,
)
from app.services.strategy_lab.etf_factor_validation import (
    FrozenHoldoutAuthorization,
    expanding_walk_forward_folds,
    holm_bonferroni,
    moving_block_bootstrap_interval,
    moving_block_bootstrap_positive_p_value,
)
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    FrozenResearchLoopManifest,
    PromotionGateEvidence,
    ResearchLoopBusyError,
    ResearchLoopCheckpoint,
    ResearchLoopPhase,
    ResearchLoopPhaseHandlers,
    ResearchLoopPhaseResult,
    build_frozen_research_loop_manifest,
    evaluate_research_promotion,
    research_loop_checkpoint_view,
    run_bounded_research_loop_continuation,
)
from app.services.strategy_lab.etf_policy_shadow import build_policy_shadow_surface
from app.services.strategy_lab.etf_ranking_candidates import (
    CANDIDATE_DAILY_CORE_TOP10,
    CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
    CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME,
    FROZEN_RANKING_CANDIDATES,
    REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
    RankingCandidateHolding,
    RankingCandidateSelection,
    RankingCandidateState,
    RankingRegimeLiquidityGateFact,
    evaluate_ranking_candidates,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_forward_outcomes import (
    CONTINUOUS_RANKING_EXECUTION_MODEL,
    RANKING_PORTFOLIO_BASE_COST_POLICY,
    RANKING_PORTFOLIO_STRESS_COST_POLICY,
    ForwardAdjustedClose,
    RankingPortfolioLedger,
    calculate_continuous_ranking_portfolio,
    calculate_ranking_forward_outcomes,
    freeze_ranking_portfolio_target,
    select_positive_momentum_top10,
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
    CONTINUOUS_FIVE_SESSION_ENDPOINT_CONTRACT_HASH,
    PRIMARY_HORIZON_SESSIONS,
    PRIMARY_TOP_N,
    RankingEndpointResult,
    RankingPairedReturnSample,
    RankingValidationSourceCohort,
    RankingValidationSourceEvent,
    build_continuous_five_session_paired_sample,
    evaluate_ranking_endpoint,
    freeze_ranking_endpoint_contract,
    freeze_ranking_validation_source_cohort,
)
from app.services.strategy_lab.etf_validation_manifest import (
    build_production_validation_source_event,
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
PIT_CANDIDATE_STATE_CHAIN_SCHEMA_VERSION = "etf_ranking_candidate_state_chain_v1"
PIT_CANDIDATE_STATE_INITIALIZATION_POLICY = "first_compatible_complete_pit_source_v1"
PIT_PRIMARY_COMPARISON_CANDIDATE_ID = CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS

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
PIT_UNAVAILABLE_QUOTE_EVIDENCE = "pit_intraday_quote_evidence_incomplete"
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
    config_receipt_cutoff = _parse_local_cutoff(config.get("source_availability_cutoff"))
    explicit_visibility_cutoff = _parse_local_cutoff(replay_visibility_cutoff)
    persisted_visibility_cutoff = _parse_local_cutoff(persisted.get("replay_visibility_cutoff"))

    if market_cutoff is None or receipt_cutoff is None or explicit_visibility_cutoff is None:
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
    job_name = f"etf_history_continuation:publication_readiness:{trade_date.isoformat()}"
    rows = (
        await session.scalars(
            select(JobRun).where(JobRun.job_name == job_name).order_by(JobRun.id.desc()).limit(10)
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
            "finished_at": (row.finished_at.isoformat() if row.finished_at is not None else None),
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

    persisted_cutoffs = _mapping(_mapping(run.summary_json).get("cutoff_provenance"))
    receipt_cutoff = _parse_local_cutoff(persisted_cutoffs.get("data_receipt_cutoff"))
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
        select(EtfPitCaptureSource).where(EtfPitCaptureSource.source_signal_run_id == run.id)
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
        and checkpoint.updated_at > effective_now - timedelta(minutes=PIT_SCHEDULER_CADENCE_MINUTES)
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
            str(persisted_policy_version) if isinstance(persisted_policy_version, str) else None
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
        "research_contract_hash": _mapping(surfaces.get("research")).get("contract_hash"),
        "actionable_contract_hash": _mapping(surfaces.get("actionable")).get("contract_hash"),
        "readiness_policy_version": readiness.get("policy_version"),
        "readiness_state": summary.get("readiness_state"),
        "target_date_coverage_ratio": run.decision_data_coverage_ratio,
        "warmup_coverage_ratio": run.coverage_ratio,
        "cutoffs": cutoffs.canonical_payload(),
        "provider_health_hash": provider_health_hash,
        "provider_health_identity": dict(provider_health_identity),
        "prospective_evidence": {
            "source_trade_dates": (
                [run.as_of_trade_date.isoformat()] if run.as_of_trade_date is not None else []
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
        or stable_contract_hash(provider_health_identity) != source.provider_health_hash
    ):
        return False
    return (
        source.market_decision_cutoff
        <= source.replay_visibility_cutoff
        <= source.data_receipt_cutoff
    )


async def _seal_pit_source_quote_evidence(
    session: AsyncSession,
    source: EtfPitCaptureSource,
) -> bool:
    codes = (
        await session.scalars(
            select(ShortResearchSignalItem.asset_code)
            .where(
                ShortResearchSignalItem.run_id == source.source_signal_run_id,
                ShortResearchSignalItem.asset_type == "etf",
            )
            .order_by(ShortResearchSignalItem.asset_code.asc())
        )
    ).all()
    result = await seal_intraday_quote_evidence(
        session,
        owner_kind=PIT_CAPTURE_SOURCE_OWNER,
        owner_id=source.id,
        asset_codes=codes,
        trade_date=source.as_of_trade_date,
        decision_cutoff=source.market_decision_cutoff,
        receipt_cutoff=source.data_receipt_cutoff,
    )
    return result["complete"] is True


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
        if not await _seal_pit_source_quote_evidence(session, existing):
            return PitSourceCaptureResult(
                state="unavailable",
                unavailable_reason=PIT_UNAVAILABLE_QUOTE_EVIDENCE,
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
        research_contract_hash=str(_mapping(surfaces.get("research")).get("contract_hash")),
        actionable_contract_hash=str(_mapping(surfaces.get("actionable")).get("contract_hash")),
        readiness_policy_version=str(
            _mapping(_mapping(run.summary_json).get("readiness_policy")).get("policy_version")
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
        if not await _seal_pit_source_quote_evidence(session, existing):
            return PitSourceCaptureResult(
                state="unavailable",
                unavailable_reason=PIT_UNAVAILABLE_QUOTE_EVIDENCE,
                source=None,
                created=False,
            )
        return PitSourceCaptureResult(
            state="captured",
            unavailable_reason=None,
            source=existing,
            created=False,
        )
    if not await _seal_pit_source_quote_evidence(session, source):
        return PitSourceCaptureResult(
            state="unavailable",
            unavailable_reason=PIT_UNAVAILABLE_QUOTE_EVIDENCE,
            source=None,
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
    work_identity = stable_contract_hash(
        {
            "code_version": code_version,
            "split_contract_hash": split_contract_hash,
            "holdout_identity_hash": holdout_identity_hash,
            "bootstrap_seed": bootstrap_seed,
            "workflow_revision": "continuous_ranking_evidence_v2",
        }
    )
    return build_frozen_research_loop_manifest(
        replay_run_key=f"production-pit:{source.source_context_hash}:{work_identity}",
        code_version=code_version,
        source_snapshot_hash=source.source_context_hash,
        universe_manifest_hash=source.universe_manifest_hash,
        split_contract_hash=split_contract_hash,
        holdout_identity_hash=holdout_identity_hash,
        bootstrap_seed=bootstrap_seed,
        source_cutoff_policy=PIT_SOURCE_CUTOFF_POLICY,
    )


def _handler_timeout(timeout_seconds: float) -> float:
    if (
        not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= PIT_MAX_CONTINUATION_SECONDS
    ):
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
        "outcome_hash": outcome_hash or _phase_absence_hash(manifest, phase=phase, kind="outcome"),
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
            str(item) for item in selection.get("underlying_hysteresis_asset_codes") or ()
        ),
        entered_asset_codes=tuple(str(item) for item in selection.get("entered_asset_codes") or ()),
        exited_asset_codes=tuple(str(item) for item in selection.get("exited_asset_codes") or ()),
        retained_asset_codes=tuple(
            str(item) for item in selection.get("retained_asset_codes") or ()
        ),
        gate_exclusions=tuple(gate_exclusions),
        selection_hash=str(selection["selection_hash"]),
    )


def _candidate_state_from_payload(payload: Mapping[str, Any]) -> RankingCandidateState:
    details = _mapping(payload.get("details"))
    state = _mapping(details.get("state"))
    holdings = tuple(
        RankingCandidateHolding(
            asset_code=str(_mapping(item).get("asset_code")),
            sessions_held=int(_mapping(item).get("sessions_held", 0)),
        )
        for item in state.get("holdings") or ()
    )
    return RankingCandidateState(
        candidate_id=str(state["candidate_id"]),
        candidate_manifest_hash=str(state["candidate_manifest_hash"]),
        holdings=holdings,
        last_selected_asset_codes=tuple(
            str(item) for item in state.get("last_selected_asset_codes") or ()
        ),
        state_hash=str(state["state_hash"]),
    )


def _candidate_state_chain_hash(
    manifest: FrozenResearchLoopManifest,
    *,
    start_date: date,
) -> str:
    return stable_contract_hash(
        {
            "schema_version": PIT_CANDIDATE_STATE_CHAIN_SCHEMA_VERSION,
            "ranking_source_kind": RankingSourceKind.PRODUCTION_PUBLISHED.value,
            "code_version": manifest.code_version,
            "research_contract_hash": manifest.research_contract_hash,
            "candidate_registry_hash": manifest.candidate_registry_hash,
            "ranking_execution_model": manifest.ranking_execution_model,
            "cost_contract_hash": manifest.ranking_cost_contract_hash,
            "initialization_policy": PIT_CANDIDATE_STATE_INITIALIZATION_POLICY,
            "start_date": start_date,
        }
    )


async def _previous_candidate_states(
    *,
    session: AsyncSession,
    source: EtfPitCaptureSource,
    manifest: FrozenResearchLoopManifest,
    artifact_store: ReplayArtifactStore,
    timeout_seconds: float,
) -> tuple[dict[str, RankingCandidateState], dict[str, Any]]:
    predecessor = await session.scalar(
        select(EtfPitCaptureSource)
        .where(
            EtfPitCaptureSource.as_of_trade_date < source.as_of_trade_date,
            EtfPitCaptureSource.research_contract_hash == source.research_contract_hash,
            EtfPitCaptureSource.ranking_contract_hash == source.ranking_contract_hash,
            EtfPitCaptureSource.readiness_state == "complete",
        )
        .order_by(
            EtfPitCaptureSource.as_of_trade_date.desc(),
            EtfPitCaptureSource.id.desc(),
        )
        .limit(1)
    )
    start_date = await session.scalar(
        select(EtfPitCaptureSource.as_of_trade_date)
        .where(
            EtfPitCaptureSource.research_contract_hash == source.research_contract_hash,
            EtfPitCaptureSource.ranking_contract_hash == source.ranking_contract_hash,
            EtfPitCaptureSource.readiness_state == "complete",
        )
        .order_by(EtfPitCaptureSource.as_of_trade_date.asc())
        .limit(1)
    )
    chain_hash = _candidate_state_chain_hash(manifest, start_date=start_date)
    if predecessor is None:
        return {}, {
            "state_chain_hash": chain_hash,
            "state_chain_status": "initialized",
            "start_date": start_date.isoformat(),
            "predecessor_source_id": None,
            "predecessor_trade_date": None,
            "predecessor_state_hash": None,
        }
    if market_data.next_etf_exchange_trading_day(
        predecessor.as_of_trade_date
    ) != source.as_of_trade_date:
        raise ValueError("pit_candidate_predecessor_exchange_session_missing")
    predecessor_manifest = build_production_pit_manifest(
        predecessor,
        code_version=manifest.code_version,
        split_contract_hash=manifest.split_contract_hash,
        holdout_identity_hash=manifest.holdout_identity_hash,
        bootstrap_seed=manifest.bootstrap_seed,
    )
    rows = artifact_store.read_research_artifact_page(
        run_id=predecessor_manifest.replay_run_key,
        phase="candidates",
        max_rows=20,
        max_seconds=_artifact_write_seconds(timeout_seconds),
    )
    states: dict[str, RankingCandidateState] = {}
    for row in rows:
        payload = _mapping(row.payload)
        details = _mapping(payload.get("details"))
        if payload.get("status") != "completed" or not details.get("state"):
            continue
        if (
            payload.get("manifest_hash") != predecessor_manifest.manifest_hash
            or payload.get("source_context_hash") != predecessor.source_context_hash
            or _mapping(details.get("state_chain")).get("state_chain_hash") != chain_hash
        ):
            raise ValueError("pit_candidate_predecessor_chain_mismatch")
        state = _candidate_state_from_payload(payload)
        if state.candidate_id in states:
            raise ValueError("pit_candidate_predecessor_state_duplicate")
        states[state.candidate_id] = state
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    if set(states) != set(registry.by_id):
        raise ValueError("pit_candidate_predecessor_state_missing")
    predecessor_state_hash = stable_contract_hash(
        tuple(sorted((candidate_id, state.state_hash) for candidate_id, state in states.items()))
    )
    return states, {
        "state_chain_hash": chain_hash,
        "state_chain_status": "continued",
        "start_date": start_date.isoformat(),
        "predecessor_source_id": predecessor.id,
        "predecessor_trade_date": predecessor.as_of_trade_date.isoformat(),
        "predecessor_state_hash": predecessor_state_hash,
    }


async def _ranking_gate_facts(
    *,
    session: AsyncSession,
    source: EtfPitCaptureSource,
    event: StageBRankingEvent,
) -> tuple[RankingRegimeLiquidityGateFact, ...]:
    run = await session.get(ShortResearchSignalRun, source.source_signal_run_id)
    if run is None:
        return ()
    summary = _mapping(run.summary_json)
    portfolio = _mapping(summary.get("portfolio"))
    market_regime = str(
        summary.get("market_regime")
        or summary.get("portfolio_mode")
        or portfolio.get("market_regime")
        or portfolio.get("portfolio_mode")
        or ""
    )
    if market_regime not in {"risk_on", "neutral", "defensive", "cash_wait"}:
        return ()
    items = (
        await session.scalars(
            select(ShortResearchSignalItem)
            .where(
                ShortResearchSignalItem.run_id == run.id,
                ShortResearchSignalItem.asset_type == "etf",
                ShortResearchSignalItem.asset_code.in_(event.all_scored),
            )
            .order_by(ShortResearchSignalItem.asset_code.asc())
        )
    ).all()
    output: list[RankingRegimeLiquidityGateFact] = []
    for item in items:
        metrics = _mapping(item.metrics_json)
        score_breakdown = _mapping(item.score_breakdown_json)
        liquidity = metrics.get("liquidity_score")
        if liquidity is None:
            liquidity = score_breakdown.get("liquidity_score")
        liquidity_is_factual = (
            isinstance(liquidity, int | float)
            and not isinstance(liquidity, bool)
            and math.isfinite(float(liquidity))
        )
        output.append(
            RankingRegimeLiquidityGateFact(
                replay_date=event.replay_date,
                asset_code=item.asset_code,
                market_regime=market_regime,
                liquidity_decision_eligible=liquidity_is_factual,
                gate_contract_hash=REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
            )
        )
    return tuple(output)


def _exchange_sessions_through(
    signal_date: date,
    *,
    through_date: date,
    maximum_forward_sessions: int = 20,
) -> tuple[date, ...]:
    sessions = [signal_date]
    cursor = signal_date
    while cursor < through_date and len(sessions) <= maximum_forward_sessions:
        cursor = market_data.next_etf_exchange_trading_day(cursor)
        if cursor <= through_date:
            sessions.append(cursor)
    return tuple(sessions)


def _exchange_sessions_between(
    start_date: date,
    *,
    through_date: date,
    maximum_sessions: int = 384,
) -> tuple[date, ...]:
    sessions: list[date] = []
    cursor = start_date
    while cursor <= through_date and len(sessions) < maximum_sessions:
        # Validate the year before is_trading_day can silently treat unknown days as closed.
        market_data.next_etf_exchange_trading_day(date(cursor.year, 1, 1))
        if market_data.is_etf_exchange_trading_day(cursor):
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return tuple(sessions)


async def _adjusted_closes_for_codes(
    *,
    session: AsyncSession,
    asset_codes: tuple[str, ...],
    trading_sessions: tuple[date, ...],
    decision_cutoff: datetime | None = None,
) -> tuple[ForwardAdjustedClose, ...]:
    if not asset_codes or not trading_sessions:
        return ()
    cutoff = decision_cutoff or utcnow()
    eligible_sessions = set(trading_sessions)
    output: list[ForwardAdjustedClose] = []
    ordered_codes = tuple(sorted(set(asset_codes)))
    for offset in range(0, len(ordered_codes), MAX_CODES_PER_REPLAY_INPUT_PAGE):
        code_page = ordered_codes[offset : offset + MAX_CODES_PER_REPLAY_INPUT_PAGE]
        facts = await market_data.etf_adjusted_daily_facts_on_or_before(
            session,
            etf_codes=code_page,
            replay_date=trading_sessions[-1],
            rows_per_code=len(trading_sessions),
            max_source_rows=len(code_page) * len(trading_sessions),
            decision_cutoff=decision_cutoff,
        )
        for fact in facts:
            if fact.trade_date not in eligible_sessions or fact.decision_eligible is not True:
                continue
            issue = market_data.etf_adjusted_price_provenance_issue(
                adjusted_value=fact.adjusted_close,
                price_basis=fact.research_price_basis,
                data_provider=fact.data_provider,
                provider_version=fact.provider_version,
                source_timestamp=fact.source_timestamp,
                adjustment_version=fact.adjustment_version,
                data_cutoff=cutoff,
            )
            if issue is not None:
                continue
            source_hash = fact.revision_hash or stable_contract_hash(
                {
                    "asset_code": fact.etf_code,
                    "session_date": fact.trade_date,
                    "adjusted_close": fact.adjusted_close,
                    "provider": fact.data_provider,
                    "provider_version": fact.provider_version,
                    "source_timestamp": fact.source_timestamp,
                }
            )
            output.append(
                ForwardAdjustedClose(
                    asset_code=fact.etf_code,
                    session_date=fact.trade_date,
                    adjusted_close=float(fact.adjusted_close),
                    price_basis=str(fact.research_price_basis),
                    decision_eligible=True,
                    provider=str(fact.data_provider),
                    adjustment_version=str(fact.adjustment_version),
                    source_hash=source_hash,
                )
            )
    return tuple(sorted(output, key=lambda item: (item.asset_code, item.session_date)))


async def _forward_adjusted_closes(
    *,
    session: AsyncSession,
    selection: RankingCandidateSelection,
    trading_sessions: tuple[date, ...],
) -> tuple[ForwardAdjustedClose, ...]:
    if len(trading_sessions) < 2 or not selection.selected_asset_codes:
        return ()
    values = await _adjusted_closes_for_codes(
        session=session,
        asset_codes=selection.selected_asset_codes,
        trading_sessions=trading_sessions,
    )
    return tuple(item for item in values if item.session_date != selection.replay_date)


@dataclass(frozen=True)
class _RankingHistoryDate:
    source: EtfPitCaptureSource
    manifest: FrozenResearchLoopManifest
    event: StageBRankingEvent
    selections: tuple[RankingCandidateSelection, ...]


async def _load_ranking_history(
    *,
    session: AsyncSession,
    source: EtfPitCaptureSource,
    manifest: FrozenResearchLoopManifest,
    artifact_store: ReplayArtifactStore,
    timeout_seconds: float,
    validation_plan: Mapping[str, Any] | None = None,
) -> tuple[tuple[_RankingHistoryDate, ...], tuple[str, ...]]:
    """Load only compatible sealed daily candidate pages, oldest first."""

    future_sessions = _exchange_sessions_through(
        source.as_of_trade_date,
        through_date=datetime.now(_SHANGHAI).date(),
        maximum_forward_sessions=PRIMARY_HORIZON_SESSIONS + 1,
    )
    split = _plan_split(validation_plan) if validation_plan is not None else None
    last_source_date = min(future_sessions[-1], split.holdout_start - timedelta(days=1)) if split else future_sessions[-1]
    sources = (
        await session.scalars(
            select(EtfPitCaptureSource)
            .where(
                EtfPitCaptureSource.as_of_trade_date <= last_source_date,
                EtfPitCaptureSource.as_of_trade_date >= (split.development_start if split else date.min),
                EtfPitCaptureSource.research_contract_hash
                == source.research_contract_hash,
                EtfPitCaptureSource.ranking_contract_hash
                == source.ranking_contract_hash,
                EtfPitCaptureSource.readiness_state == "complete",
            )
            .order_by(
                EtfPitCaptureSource.as_of_trade_date.asc(),
                EtfPitCaptureSource.id.asc(),
            )
            .limit(321)
        )
    ).all()
    if len(sources) > 320:
        raise ValueError("pit_ranking_history_exceeds_frozen_account_window")
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    rows: list[_RankingHistoryDate] = []
    exclusions: list[str] = []
    for candidate_source in sources:
        try:
            candidate_manifest = build_production_pit_manifest(
                candidate_source,
                code_version=manifest.code_version,
                split_contract_hash=manifest.split_contract_hash,
                holdout_identity_hash=manifest.holdout_identity_hash,
                bootstrap_seed=manifest.bootstrap_seed,
            )
            candidate_rows = artifact_store.read_research_artifact_page(
                run_id=candidate_manifest.replay_run_key,
                phase="candidates",
                max_rows=20,
                max_seconds=_artifact_write_seconds(timeout_seconds),
            )
            selections = tuple(
                _selection_from_artifact_payload(_mapping(item.payload))
                for item in candidate_rows
                if item.payload.get("status") == "completed"
            )
            by_id = {item.candidate_id: item for item in selections}
            if set(by_id) != set(registry.by_id) or len(by_id) != len(selections):
                raise ValueError("candidate_page_incomplete")
            event_page = read_stage_b_ranking_event_page(
                store=artifact_store,
                replay_run_key=candidate_manifest.replay_run_key,
                after_date=None,
                max_rows=1,
                max_seconds=_artifact_write_seconds(timeout_seconds),
            )
            if not event_page.rows:
                raise ValueError("stage_b_event_missing")
            event = event_page.rows[0]
            if not _stage_b_event_matches_source(
                event=event,
                manifest=candidate_manifest,
                source=candidate_source,
            ) or any(
                item.source_ranking_event_hash != event.event_hash
                or item.replay_date != event.replay_date
                for item in selections
            ):
                raise ValueError("daily_history_identity_mismatch")
        except (ArtifactConflictError, BoundedWorkLimitError, KeyError, TypeError, ValueError) as exc:
            exclusions.append(
                f"{candidate_source.as_of_trade_date.isoformat()}:"
                f"{str(exc) or type(exc).__name__}"
            )
            continue
        rows.append(
            _RankingHistoryDate(
                source=candidate_source,
                manifest=candidate_manifest,
                event=event,
                selections=tuple(sorted(selections, key=lambda item: item.candidate_id)),
            )
        )
    return tuple(rows), tuple(exclusions)


@dataclass(frozen=True)
class _RankingEvidenceCalculation:
    source_cohort: RankingValidationSourceCohort
    trading_sessions: tuple[date, ...]
    endpoint_results: tuple[RankingEndpointResult, ...]
    samples_by_candidate: dict[str, tuple[RankingPairedReturnSample, ...]]
    base_ledgers: dict[str, RankingPortfolioLedger]
    stress_ledgers: dict[str, RankingPortfolioLedger]
    momentum_details: tuple[dict[str, Any], ...]
    history_exclusions: tuple[str, ...]
    validation_plan: dict[str, Any] | None = None
    market_regimes: tuple[tuple[str, str], ...] = ()


def _ranking_protocol_key(manifest: FrozenResearchLoopManifest) -> str:
    return "pit-ranking-protocol:" + stable_contract_hash({
        "research_contract_hash": manifest.research_contract_hash,
        "candidate_registry_hash": manifest.candidate_registry_hash,
        "code_version": manifest.code_version,
        "split_contract_hash": manifest.split_contract_hash,
        "holdout_identity_hash": manifest.holdout_identity_hash,
    })


def freeze_production_ranking_validation_plan(
    *, manifest: FrozenResearchLoopManifest, split: ChronologicalSplit,
    fold_sessions: int, registered_at: datetime,
    declared_regimes: tuple[str, ...] = ("risk_on", "neutral", "defensive", "cash_wait"),
) -> dict[str, Any]:
    """Freeze explicit operator-supplied dates; never infer a split from returns."""
    if not (
        registered_at.date() <= split.development_start
        <= split.development_end < split.validation_start
        <= split.validation_end < split.holdout_start <= split.holdout_end
        and split.purge_horizon_sessions == split.embargo_sessions == 10
        and isinstance(fold_sessions, int) and not isinstance(fold_sessions, bool)
        and fold_sessions > 0 and declared_regimes
        and len(declared_regimes) == len(set(declared_regimes))
        and set(declared_regimes) <= {"risk_on", "neutral", "defensive", "cash_wait"}
    ):
        raise ValueError("pit_validation_plan_dates_or_purge_invalid")
    payload = {
        "schema_version": "pit_ranking_validation_plan_v1",
        "protocol_key": _ranking_protocol_key(manifest),
        "registered_at": registered_at.isoformat(),
        "split": {key: value.isoformat() if isinstance(value, date) else value
                  for key, value in asdict(split).items()},
        "fold_sessions": fold_sessions,
        "declared_regimes": list(declared_regimes),
    }
    return {**payload, "plan_hash": stable_contract_hash(payload)}


def _plan_split(plan: Mapping[str, Any]) -> ChronologicalSplit:
    return ChronologicalSplit(**{
        key: date.fromisoformat(value) if key.endswith(("_start", "_end")) else value
        for key, value in plan["split"].items()
    })


def _load_ranking_validation_plan(
    artifact_store: ReplayArtifactStore, manifest: FrozenResearchLoopManifest,
) -> dict[str, Any] | None:
    rows = artifact_store.read_research_artifact_page(
        run_id=_ranking_protocol_key(manifest), phase="validation_plan",
        max_rows=2, max_seconds=5.0,
    )
    if not rows:
        return None
    if len(rows) != 1 or rows[0].item_key != "frozen-plan":
        raise ValueError("pit_validation_plan_not_unique")
    payload = dict(rows[0].payload)
    expected = freeze_production_ranking_validation_plan(
        manifest=manifest, split=_plan_split(payload),
        fold_sessions=payload["fold_sessions"],
        registered_at=datetime.fromisoformat(payload["registered_at"]),
        declared_regimes=tuple(payload["declared_regimes"]),
    )
    if payload != expected:
        raise ValueError("pit_validation_plan_identity_mismatch")
    return payload


def _ranking_fold_report(
    *, plan: Mapping[str, Any] | None, trading_sessions: tuple[date, ...],
    paired_samples: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if plan is None:
        return {"status": "unavailable", "reason": "frozen_chronological_split_missing",
                "completed_fold_count": 0, "fold_sign_stable": False, "folds": []}
    split = _plan_split(plan)
    folds = expanding_walk_forward_folds(
        trading_sessions, split, fold_sessions=plan["fold_sessions"],
    )
    reports = []
    for fold in folds:
        values = []
        last_exit = None
        for sample in sorted(paired_samples, key=lambda item: item["signal_date"]):
            signal = date.fromisoformat(sample["signal_date"])
            if sample["status"] != "completed" or signal not in fold.test_dates:
                continue
            entry = date.fromisoformat(sample["entry_session"])
            exit_date = date.fromisoformat(sample["exit_session"])
            if entry < fold.test_dates[0] or exit_date > fold.test_dates[-1]:
                continue
            if last_exit is not None and entry < last_exit:
                continue
            values.append(float(sample["candidate_net_return"]) - float(sample["baseline_net_return"]))
            last_exit = exit_date
        reports.append({
            **asdict(fold), "status": "completed" if values else "insufficient_data",
            "independent_date_count": len(values),
            "mean_paired_net_excess": sum(values) / len(values) if values else None,
        })
    completed = [item for item in reports if item["status"] == "completed"]
    return {
        "status": "calculated", "plan_hash": plan["plan_hash"], "folds": reports,
        "completed_fold_count": len(completed),
        "fold_sign_stable": bool(completed) and all(item["mean_paired_net_excess"] > 0 for item in completed),
    }


def claim_production_holdout_once(
    *,
    artifact_store: ReplayArtifactStore,
    manifest: FrozenResearchLoopManifest,
    authorization_identity_hash: str,
    frozen_non_holdout_evidence_hash: str,
    expected_holdout_evidence_hash: str,
    consumed_at: datetime,
    consumption_scope_hash: str | None = None,
) -> HoldoutConsumption:
    """Atomically claim the frozen holdout before its expected artifact is read."""

    if authorization_identity_hash != manifest.holdout_identity_hash:
        raise ValueError("holdout authorization identity mismatch")
    receipt_identity = consumption_scope_hash or manifest.manifest_hash
    receipt_run_key = (
        "pit-holdout-consumption:" + consumption_scope_hash
        if consumption_scope_hash is not None else manifest.replay_run_key
    )
    existing = artifact_store.read_research_artifact_page(
        run_id=receipt_run_key,
        phase="holdout_consumption",
        max_rows=1,
        max_seconds=5.0,
    )
    if existing:
        restored = restore_holdout_consumption(existing[0].payload)
        if (
            restored.manifest_hash != receipt_identity
            or restored.frozen_non_holdout_evidence_hash
            != frozen_non_holdout_evidence_hash
            or restored.holdout_evidence_hash != expected_holdout_evidence_hash
            or existing[0].payload.get("authorization_identity_hash")
            != authorization_identity_hash
        ):
            raise ValueError("holdout already consumed for different evidence")
        return restored
    record = consume_holdout_once(
        HoldoutConsumption(
            manifest_hash=receipt_identity,
            frozen_non_holdout_evidence_hash=frozen_non_holdout_evidence_hash,
        ),
        manifest_hash=receipt_identity,
        frozen_non_holdout_evidence_hash=frozen_non_holdout_evidence_hash,
        holdout_evidence_hash=expected_holdout_evidence_hash,
        consumed_at=consumed_at,
    )
    payload = {
        **holdout_consumption_artifact(record),
        "authorization_identity_hash": authorization_identity_hash,
        "read_policy": "claim_before_expected_holdout_artifact_read",
    }
    try:
        artifact_store.write_research_artifacts(
            run_id=receipt_run_key,
            phase="holdout_consumption",
            artifacts=(("single-use-receipt", payload),),
            max_seconds=5.0,
        )
    except ArtifactConflictError as error:
        concurrent = artifact_store.read_research_artifact_page(
            run_id=receipt_run_key,
            phase="holdout_consumption",
            max_rows=1,
            max_seconds=5.0,
        )
        if not concurrent:
            raise
        restored = restore_holdout_consumption(concurrent[0].payload)
        if (
            restored.manifest_hash != receipt_identity
            or restored.frozen_non_holdout_evidence_hash
            != frozen_non_holdout_evidence_hash
            or restored.holdout_evidence_hash != expected_holdout_evidence_hash
            or concurrent[0].payload.get("authorization_identity_hash")
            != authorization_identity_hash
        ):
            raise ValueError(
                "holdout already consumed for different evidence"
            ) from error
        return restored
    stored = artifact_store.read_research_artifact_page(
        run_id=receipt_run_key,
        phase="holdout_consumption",
        max_rows=1,
        max_seconds=5.0,
    )
    if not stored or stored[0].payload != payload:
        raise ValueError("holdout consumption receipt was not durably claimed")
    return restore_holdout_consumption(stored[0].payload)


def freeze_production_holdout_authorization(
    *, manifest: FrozenResearchLoopManifest, plan_hash: str,
    frozen_non_holdout_evidence_hash: str,
) -> FrozenHoldoutAuthorization:
    fields = {
        "manifest_hash": plan_hash,
        "code_version": manifest.code_version,
        "candidate_ids": tuple(item.candidate_id for item in FROZEN_RANKING_CANDIDATES),
        "promotion_gates_hash": stable_contract_hash({
            "sessions": manifest.minimum_promotion_sessions,
            "independent_dates": manifest.minimum_independent_dates,
            "folds": manifest.minimum_walk_forward_folds,
            "coverage": manifest.minimum_production_coverage,
            "maximum_drawdown_deterioration": manifest.maximum_drawdown_deterioration,
        }),
        "frozen_non_holdout_evidence_hash": frozen_non_holdout_evidence_hash,
    }
    return FrozenHoldoutAuthorization(**fields, authorization_hash=stable_contract_hash(fields))


def _read_authorized_ranking_holdout(
    *, artifact_store: ReplayArtifactStore, manifest: FrozenResearchLoopManifest,
    plan: Mapping[str, Any] | None, non_holdout_gates: PromotionGateEvidence,
    frozen_non_holdout_evidence_hash: str, consumed_at: datetime,
) -> dict[str, Any]:
    if plan is None:
        return {"status": "not_consumed", "reason": "frozen_chronological_split_missing"}
    gate_result = evaluate_research_promotion(replace(non_holdout_gates, holdout_consumed=True))
    if gate_result.state.value != "promotion_eligible":
        return {"status": "not_consumed", "reason": "non_holdout_gates_not_passed",
                "failed_gates": gate_result.failed_gates}
    split = _plan_split(plan)
    consumed_cutoff = (consumed_at.replace(tzinfo=UTC) if consumed_at.tzinfo is None
                       else consumed_at.astimezone(UTC))
    holdout_close = datetime.combine(split.holdout_end, datetime.min.time()).replace(
        hour=15, tzinfo=_SHANGHAI,
    ).astimezone(UTC)
    if consumed_cutoff < holdout_close:
        return {"status": "not_consumed", "reason": "holdout_window_not_mature"}
    protocol_key = _ranking_protocol_key(manifest)
    approvals = artifact_store.read_research_artifact_page(
        run_id=protocol_key, phase="holdout_authorization", max_rows=2, max_seconds=5.0,
    )
    if not approvals:
        return {"status": "not_consumed", "reason": "frozen_holdout_authorization_missing"}
    expected = freeze_production_holdout_authorization(
        manifest=manifest, plan_hash=plan["plan_hash"],
        frozen_non_holdout_evidence_hash=frozen_non_holdout_evidence_hash,
    )
    if len(approvals) != 1 or approvals[0].item_key != "frozen-approval":
        raise ValueError("pit_holdout_authorization_not_unique")
    approved = dict(approvals[0].payload["authorization"])
    approved["candidate_ids"] = tuple(approved["candidate_ids"])
    if FrozenHoldoutAuthorization(**approved) != expected:
        raise ValueError("pit_holdout_authorization_inputs_changed")
    expected_result_hash = approvals[0].payload["expected_holdout_evidence_hash"]
    split = _plan_split(plan)
    holdout_data_scope = stable_contract_hash({
        "dataset_owner": "fundscope_etf_production_pit",
        "holdout_start": split.holdout_start,
        "holdout_end": split.holdout_end,
    })
    receipt = claim_production_holdout_once(
        artifact_store=artifact_store, manifest=manifest,
        authorization_identity_hash=manifest.holdout_identity_hash,
        frozen_non_holdout_evidence_hash=frozen_non_holdout_evidence_hash,
        expected_holdout_evidence_hash=expected_result_hash,
        consumed_at=consumed_at, consumption_scope_hash=holdout_data_scope,
    )
    # This is the first read of holdout results. The receipt survives a crash
    # here; retries can only reread this exact previously authorized evidence.
    results = artifact_store.read_research_artifact_page(
        run_id=protocol_key, phase="holdout_result", max_rows=2, max_seconds=5.0,
    )
    if len(results) != 1 or results[0].item_key != "frozen-result":
        raise ValueError("pit_authorized_holdout_result_missing")
    result = dict(results[0].payload)
    if (
        stable_contract_hash(result) != expected_result_hash
        or result.get("plan_hash") != plan["plan_hash"]
        or result.get("frozen_non_holdout_evidence_hash") != frozen_non_holdout_evidence_hash
        or result.get("primary_candidate_id") != PIT_PRIMARY_COMPARISON_CANDIDATE_ID
        or result.get("split") != plan["split"]
    ):
        raise ValueError("pit_authorized_holdout_result_identity_mismatch")
    endpoint = dict(result.get("endpoint_result") or {})
    endpoint_hash = endpoint.pop("result_hash", None)
    try:
        RankingEndpointResult(**endpoint, result_hash=endpoint_hash)
    except TypeError as exc:
        raise ValueError("pit_authorized_holdout_endpoint_incomplete") from exc
    dates = tuple(date.fromisoformat(value) for value in endpoint.get("independent_dates", ()))
    result_cutoff = datetime.fromisoformat(result.get("outcome_data_cutoff", ""))
    result_cutoff = (result_cutoff.replace(tzinfo=UTC) if result_cutoff.tzinfo is None
                     else result_cutoff.astimezone(UTC))
    consumed_cutoff = (consumed_at.replace(tzinfo=UTC) if consumed_at.tzinfo is None
                       else consumed_at.astimezone(UTC))
    holdout_close = datetime.combine(split.holdout_end, datetime.min.time()).replace(
        hour=15, tzinfo=_SHANGHAI,
    ).astimezone(UTC)
    candidate = next(item for item in FROZEN_RANKING_CANDIDATES
                     if item.candidate_id == PIT_PRIMARY_COMPARISON_CANDIDATE_ID)
    if (
        not dates or tuple(sorted(set(dates))) != dates
        or not split.holdout_start <= dates[0] <= dates[-1] <= split.holdout_end
        or len(_exchange_sessions_between(dates[-1], through_date=split.holdout_end)) < 7
        or any(len(_exchange_sessions_between(previous, through_date=current)) < 8
               for previous, current in zip(dates, dates[1:], strict=False))
        or result_cutoff < holdout_close or result_cutoff > consumed_cutoff
        or stable_contract_hash(endpoint) != endpoint_hash
        or endpoint.get("candidate_id") != PIT_PRIMARY_COMPARISON_CANDIDATE_ID
        or endpoint.get("candidate_registry_hash") != manifest.candidate_registry_hash
        or endpoint.get("candidate_manifest_hash") != candidate.manifest_hash
        or endpoint.get("cost_contract_hash") != manifest.ranking_cost_contract_hash
        or endpoint.get("fee_bps_per_side") != RANKING_PORTFOLIO_BASE_COST_POLICY.fee_bps_per_side
        or endpoint.get("slippage_bps_per_side") != RANKING_PORTFOLIO_BASE_COST_POLICY.slippage_bps_per_side
        or endpoint.get("ranking_source_kind") != "production_published"
        or endpoint.get("execution_model") != CONTINUOUS_RANKING_EXECUTION_MODEL
        or endpoint.get("endpoint_contract_identity_hash") != CONTINUOUS_FIVE_SESSION_ENDPOINT_CONTRACT_HASH
        or endpoint.get("top_n") != 10 or endpoint.get("horizon_sessions") != 5
        or len(endpoint.get("accepted_sample_hashes", ())) != len(dates)
        or len(set(endpoint.get("accepted_sample_hashes", ()))) != len(dates)
        or not all(_is_sha256(value) for value in endpoint.get("accepted_sample_hashes", ()))
    ):
        raise ValueError("pit_authorized_holdout_endpoint_invalid")
    interval = endpoint.get("bootstrap_confidence_interval", ())
    numerator = endpoint.get("coverage_numerator")
    denominator = endpoint.get("coverage_denominator")
    ratio = endpoint.get("coverage_ratio")
    if not (
        isinstance(numerator, int) and not isinstance(numerator, bool)
        and isinstance(denominator, int) and not isinstance(denominator, bool)
        and 0 < len(dates) <= numerator <= denominator
        and numerator == endpoint.get("completed_outcome_count")
        and isinstance(ratio, int | float) and not isinstance(ratio, bool)
        and math.isfinite(ratio) and math.isclose(ratio, numerator / denominator)
    ):
        raise ValueError("pit_authorized_holdout_coverage_inconsistent")
    candidate_drawdown = endpoint.get("candidate_maximum_drawdown")
    baseline_drawdown = endpoint.get("baseline_maximum_drawdown")
    drawdown_passed = (
        isinstance(candidate_drawdown, int | float) and not isinstance(candidate_drawdown, bool)
        and isinstance(baseline_drawdown, int | float) and not isinstance(baseline_drawdown, bool)
        and math.isfinite(candidate_drawdown) and math.isfinite(baseline_drawdown)
        and 0.0 <= candidate_drawdown <= 1.0 and 0.0 <= baseline_drawdown <= 1.0
        and candidate_drawdown <= baseline_drawdown + manifest.maximum_drawdown_deterioration
    )
    passed = (
        endpoint.get("sample_gate_passed") is True
        and len(dates) >= manifest.minimum_independent_dates
        and float(endpoint.get("coverage_ratio", 0)) >= manifest.minimum_production_coverage
        and len(interval) == 2 and all(math.isfinite(float(value)) for value in interval)
        and float(interval[0]) > 0.0
        and drawdown_passed
    )
    return {"status": "consumed", "passed": passed,
            "result": result, "consumption_hash": receipt.consumption_hash,
            "authorization_hash": expected.authorization_hash}


def _portfolio_target_weights(
    asset_codes: tuple[str, ...],
) -> tuple[tuple[str, float], ...]:
    return tuple((asset_code, 0.1) for asset_code in asset_codes[:10])


def _ledger_summary(ledger: RankingPortfolioLedger) -> dict[str, Any]:
    return {
        "status": ledger.status,
        "execution_model": ledger.execution_model,
        "cost_scenario": ledger.cost_scenario,
        "cost_contract_hash": ledger.cost_contract_hash,
        "cost_provenance": ledger.cost_provenance,
        "net_return": ledger.net_return,
        "gross_return": ledger.gross_return,
        "net_maximum_drawdown": ledger.net_maximum_drawdown,
        "gross_maximum_drawdown": ledger.gross_maximum_drawdown,
        "turnover": ledger.turnover,
        "total_transaction_cost": ledger.total_transaction_cost,
        "rebalance_count": ledger.rebalance_count,
        "order_count": ledger.order_count,
        "final_net_cash": ledger.final_net_cash,
        "final_gross_cash": ledger.final_gross_cash,
        "unavailable_intervals": tuple(asdict(item) for item in ledger.unavailable_intervals),
        "market_data_hash": ledger.market_data_hash,
        "input_hash": ledger.input_hash,
        "ledger_hash": ledger.ledger_hash,
    }


async def _calculate_ranking_evidence(
    *,
    session: AsyncSession,
    source: EtfPitCaptureSource,
    manifest: FrozenResearchLoopManifest,
    artifact_store: ReplayArtifactStore,
    timeout_seconds: float,
) -> _RankingEvidenceCalculation:
    validation_plan = (
        _load_ranking_validation_plan(artifact_store, manifest)
        if artifact_store is not None else None
    )
    history, history_exclusions = await _load_ranking_history(
        session=session,
        source=source,
        manifest=manifest,
        artifact_store=artifact_store,
        timeout_seconds=timeout_seconds,
        validation_plan=validation_plan,
    )
    if validation_plan is not None:
        split = _plan_split(validation_plan)
        history = tuple(item for item in history
                        if split.development_start <= item.event.replay_date < split.holdout_start)
    if not history:
        raise ValueError("pit_ranking_history_missing")
    if history_exclusions:
        raise ValueError("pit_ranking_history_incomplete:" + ";".join(history_exclusions))
    sample_history = tuple(
        item for item in history if item.event.replay_date <= source.as_of_trade_date
    )
    source_events_list: list[RankingValidationSourceEvent] = []
    market_regimes: list[tuple[str, str]] = []
    for item in sample_history:
        source_run = await session.get(
            ShortResearchSignalRun,
            item.source.source_signal_run_id,
        )
        if source_run is None:
            raise ValueError("pit_ranking_source_run_missing")
        summary = _mapping(getattr(source_run, "summary_json", {}))
        portfolio = _mapping(summary.get("portfolio"))
        regime = str(summary.get("market_regime") or summary.get("portfolio_mode")
                     or portfolio.get("market_regime") or portfolio.get("portfolio_mode") or "")
        if regime in {"risk_on", "neutral", "defensive", "cash_wait"}:
            market_regimes.append((item.event.replay_date.isoformat(), regime))
        source_events_list.append(
            await build_production_validation_source_event(session, source_run)
        )
    source_events = tuple(source_events_list)
    source_cohort = freeze_ranking_validation_source_cohort(
        ranking_source_kind=RankingSourceKind.PRODUCTION_PUBLISHED,
        events=source_events,
    )
    forward_sessions = _exchange_sessions_through(
        source.as_of_trade_date,
        through_date=datetime.now(_SHANGHAI).date(),
        maximum_forward_sessions=PRIMARY_HORIZON_SESSIONS + 1,
    )
    through_date = forward_sessions[-1]
    if validation_plan is not None:
        through_date = min(through_date, split.holdout_start - timedelta(days=1))
    trading_sessions = _exchange_sessions_between(
        history[0].event.replay_date - timedelta(days=45),
        through_date=through_date,
    )
    if history[-1].event.replay_date not in trading_sessions:
        raise ValueError("pit_ranking_history_calendar_incomplete")
    required_signal_dates = tuple(
        item for item in trading_sessions[:-1]
        if item >= history[0].event.replay_date
    )
    asset_codes = tuple(
        sorted(
            {
                asset_code
                for item in history
                for asset_code in item.event.all_scored
            }
            | {
                asset_code
                for item in history
                for selection in item.selections
                for asset_code in selection.selected_asset_codes
            }
        )
    )
    adjusted_closes = await _adjusted_closes_for_codes(
        session=session,
        asset_codes=asset_codes,
        trading_sessions=trading_sessions,
    )
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
    targets_by_candidate: dict[str, tuple[Any, ...]] = {}
    for candidate in registry.candidates:
        targets_by_candidate[candidate.candidate_id] = tuple(
            freeze_ranking_portfolio_target(
                signal_date=item.event.replay_date,
                target_weights=_portfolio_target_weights(
                    {selection.candidate_id: selection for selection in item.selections}[
                        candidate.candidate_id
                    ].selected_asset_codes
                ),
                source_hash={
                    selection.candidate_id: selection for selection in item.selections
                }[candidate.candidate_id].selection_hash,
            )
            for item in history
        )
    base_ledgers = {
        candidate_id: calculate_continuous_ranking_portfolio(
            trading_sessions=trading_sessions,
            adjusted_closes=adjusted_closes,
            targets=targets,
            cost_policy=RANKING_PORTFOLIO_BASE_COST_POLICY,
            required_signal_dates=required_signal_dates,
        )
        for candidate_id, targets in targets_by_candidate.items()
    }
    stress_ledgers = {
        candidate_id: calculate_continuous_ranking_portfolio(
            trading_sessions=trading_sessions,
            adjusted_closes=adjusted_closes,
            targets=targets,
            cost_policy=RANKING_PORTFOLIO_STRESS_COST_POLICY,
            required_signal_dates=required_signal_dates,
        )
        for candidate_id, targets in targets_by_candidate.items()
    }
    momentum_selections_list = []
    for item in history:
        signal_sessions = tuple(
            value for value in trading_sessions if value <= item.event.replay_date
        )
        pit_closes = await _adjusted_closes_for_codes(
            session=session,
            asset_codes=item.event.all_scored,
            trading_sessions=signal_sessions[-21:],
            decision_cutoff=item.source.replay_visibility_cutoff,
        )
        momentum_selections_list.append(select_positive_momentum_top10(
            signal_date=item.event.replay_date,
            eligible_asset_codes=item.event.all_scored,
            trading_sessions=signal_sessions,
            adjusted_closes=pit_closes,
        ))
    momentum_selections = tuple(momentum_selections_list)
    momentum_targets = tuple(
        freeze_ranking_portfolio_target(
            signal_date=item.signal_date,
            target_weights=item.target_weights,
            source_hash=item.selection_hash,
        )
        for item in momentum_selections
    )
    momentum_base = calculate_continuous_ranking_portfolio(
        trading_sessions=trading_sessions,
        adjusted_closes=adjusted_closes,
        targets=momentum_targets,
        cost_policy=RANKING_PORTFOLIO_BASE_COST_POLICY,
        required_signal_dates=required_signal_dates,
    )
    momentum_stress = calculate_continuous_ranking_portfolio(
        trading_sessions=trading_sessions,
        adjusted_closes=adjusted_closes,
        targets=momentum_targets,
        cost_policy=RANKING_PORTFOLIO_STRESS_COST_POLICY,
        required_signal_dates=required_signal_dates,
    )
    baseline_ledger = base_ledgers[CANDIDATE_DAILY_CORE_TOP10]
    samples_by_candidate: dict[str, tuple[RankingPairedReturnSample, ...]] = {}
    for candidate in registry.candidates:
        ledger = base_ledgers[candidate.candidate_id]
        samples_by_candidate[candidate.candidate_id] = tuple(
            build_continuous_five_session_paired_sample(
                source_event_hash=source_event.source_event_hash,
                candidate_id=candidate.candidate_id,
                candidate_manifest_hash=candidate.manifest_hash,
                signal_date=item.event.replay_date,
                selected_ranked_asset_codes={
                    selection.candidate_id: selection for selection in item.selections
                }[candidate.candidate_id].selected_asset_codes,
                candidate_ledger=ledger,
                baseline_ledger=baseline_ledger,
                trading_sessions=trading_sessions,
            )
            for item, source_event in zip(sample_history, source_events, strict=True)
        )
    endpoint_contract = freeze_ranking_endpoint_contract(
        source_cohort=source_cohort,
        candidate_registry=registry,
        bootstrap_seed=manifest.bootstrap_seed,
        bootstrap_resamples=1_000,
        bootstrap_block_length=5,
        minimum_independent_dates=manifest.minimum_independent_dates,
        minimum_coverage_ratio=manifest.minimum_production_coverage,
        maximum_drawdown_noninferiority_tolerance=(
            manifest.maximum_drawdown_deterioration
        ),
    )
    endpoint_results: list[RankingEndpointResult] = []
    for candidate in registry.candidates:
        try:
            endpoint_results.append(
                evaluate_ranking_endpoint(
                    source_cohort=source_cohort,
                    candidate_registry=registry,
                    contract=endpoint_contract,
                    candidate_id=candidate.candidate_id,
                    top_n=PRIMARY_TOP_N,
                    horizon_sessions=PRIMARY_HORIZON_SESSIONS,
                    samples=samples_by_candidate[candidate.candidate_id],
                    trading_sessions=trading_sessions,
                    candidate_ledger=base_ledgers[candidate.candidate_id],
                    baseline_ledger=baseline_ledger,
                )
            )
        except ValueError as exc:
            history_exclusions = (
                *history_exclusions,
                f"endpoint:{candidate.candidate_id}:"
                f"{str(exc) or type(exc).__name__}",
            )
            continue
    momentum_details = tuple(
        (
            {
                **asdict(selection),
                "base_ledger": _ledger_summary(momentum_base),
                "stress_ledger": _ledger_summary(momentum_stress),
            }
            if index == len(momentum_selections) - 1
            else asdict(selection)
        )
        for index, selection in enumerate(momentum_selections)
    )
    return _RankingEvidenceCalculation(
        source_cohort=source_cohort,
        trading_sessions=trading_sessions,
        endpoint_results=tuple(endpoint_results),
        samples_by_candidate=samples_by_candidate,
        base_ledgers=base_ledgers,
        stress_ledgers=stress_ledgers,
        momentum_details=momentum_details,
        history_exclusions=history_exclusions,
        validation_plan=validation_plan,
        market_regimes=tuple(market_regimes),
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
    return stable_contract_hash({"key": key, "values": values, "fallback": fallback})


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
            registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)
            if registry.registry_hash != manifest.candidate_registry_hash:
                raise ValueError("frozen candidate registry is incompatible")
            previous_states, chain = await _previous_candidate_states(
                session=session,
                source=source,
                manifest=manifest,
                artifact_store=artifact_store,
                timeout_seconds=timeout_seconds,
            )
            gate_facts = await _ranking_gate_facts(
                session=session,
                source=source,
                event=event,
            )
            batch = evaluate_ranking_candidates(
                ranking_event=event,
                registry=registry,
                previous_states=previous_states,
                gate_facts=gate_facts,
            )
        except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
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
                    "reason": (
                        f"pit_candidate_contract_pending:{str(exc) or type(exc).__name__}"
                    ),
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
                            "state_chain": chain,
                            "selection": asdict(selection),
                            "state": asdict(state),
                            "gate_fact_status": (
                                "missing_fail_closed"
                                if selection.gate_exclusions
                                else "not_required_or_satisfied"
                            ),
                            "unavailable_inputs": (
                                ["regime_liquidity_gate_facts"] if selection.gate_exclusions else []
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
                "state_chain_hash": chain["state_chain_hash"],
                "predecessor_state_hash": chain["predecessor_state_hash"],
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
        candidate_artifacts = tuple(
            item for item in candidate_artifacts if item.payload.get("status") == "completed"
        )
        if len(candidate_artifacts) != len(FROZEN_RANKING_CANDIDATES):
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
        through_date = datetime.now(_SHANGHAI).date()
        pending_outcome_count = 0
        retryable_missing_count = 0
        for stored in candidate_artifacts:
            payload = _mapping(stored.payload)
            try:
                selection = _selection_from_artifact_payload(payload)
                trading_sessions = _exchange_sessions_through(
                    selection.replay_date,
                    through_date=through_date,
                )
                adjusted_closes = await _forward_adjusted_closes(
                    session=session,
                    selection=selection,
                    trading_sessions=trading_sessions,
                )
                bundle = calculate_ranking_forward_outcomes(
                    selection=selection,
                    trading_sessions=trading_sessions,
                    adjusted_closes=adjusted_closes,
                )
            except (KeyError, TypeError, ValueError) as exc:
                return _phase_failure(
                    checkpoint,
                    reason=f"pit_candidate_selection_contract_pending:{exc}",
                )
            if bundle.pending_outcome_count:
                pending_outcome_count += bundle.pending_outcome_count
                exclusions[PIT_PENDING_FUTURE_EXCHANGE_SESSIONS] = (
                    exclusions.get(PIT_PENDING_FUTURE_EXCHANGE_SESSIONS, 0)
                    + bundle.pending_outcome_count
                )
            bundle_missing_count = sum(
                outcome.status == "excluded"
                and outcome.exclusion_reason == "missing_adjusted_entry_or_exit"
                for outcome in bundle.outcomes
            )
            retryable_missing_count += bundle_missing_count
            if bundle_missing_count:
                exclusions["missing_adjusted_entry_or_exit"] = (
                    exclusions.get("missing_adjusted_entry_or_exit", 0)
                    + bundle_missing_count
                )
            retryable = bool(bundle.pending_outcome_count or bundle_missing_count)
            artifacts.append(
                (
                    (
                        f"revision:{trading_sessions[-1].isoformat()}:"
                        f"candidate:{selection.candidate_id}:"
                        f"input:{bundle.input_hash}"
                    ),
                    _phase_artifact_payload(
                        phase="forward_outcomes",
                        status=("pending" if retryable else "completed"),
                        manifest=manifest,
                        checkpoint=checkpoint,
                        source=source,
                        page_size=page_size,
                        candidate_hash=selection.selection_hash,
                        outcome_hash=bundle.bundle_hash,
                        details={
                            "selection_hash": selection.selection_hash,
                            "outcome_bundle": asdict(bundle),
                            "outcome_cutoff_date": trading_sessions[-1].isoformat(),
                            "outcome_input_revision_hash": bundle.input_hash,
                            "outcome_state": (
                                "future_windows_pending"
                                if bundle.pending_outcome_count
                                else (
                                    "matured_missing_data_retryable"
                                    if bundle_missing_count
                                    else (
                                        "completed"
                                        if bundle.completed_outcome_count
                                        else "completed_with_exclusions"
                                    )
                                )
                            ),
                            "unavailable_inputs": (
                                [
                                    "exact_next_exchange_entry_session",
                                    "exact_horizon_exit_exchange_sessions",
                                ]
                                if bundle.pending_outcome_count
                                else (
                                    ["decision_eligible_adjusted_entry_or_exit"]
                                    if bundle_missing_count
                                    else []
                                )
                            ),
                            "no_intraday_reconstruction": True,
                        },
                    ),
                )
            )
        if pending_outcome_count or retryable_missing_count:
            try:
                _write_phase_page(
                    artifact_store=artifact_store,
                    manifest=manifest,
                    phase="forward_outcomes",
                    artifacts=tuple(artifacts),
                    page_size=page_size,
                    timeout_seconds=timeout_seconds,
                )
            except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
                return _phase_failure(
                    checkpoint,
                    reason=f"pit_forward_outcomes_artifact_write_failed:{type(exc).__name__}",
                )
            return ResearchLoopPhaseResult(
                phase_complete=False,
                processed_count=len(artifacts),
                artifact_hash=None,
                cursor={
                    "candidate_artifact_count": len(candidate_artifacts),
                    "outcome_cutoff_date": through_date.isoformat(),
                    "pending_outcome_count": pending_outcome_count,
                    "matured_missing_data_count": retryable_missing_count,
                },
                coverage={},
                exclusions={},
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
            sealed = tuple(
                item for item in _read_phase_page(
                    artifact_store=artifact_store, manifest=manifest,
                    phase="ranking_validation", page_size=page_size,
                    timeout_seconds=timeout_seconds,
                )
                if item.payload.get("status") == "completed"
            )
            if sealed:
                return _complete_artifact_phase(
                    phase="ranking_validation", checkpoint=checkpoint,
                    manifest=manifest, artifact_store=artifact_store,
                    artifacts=tuple((item.item_key, item.payload) for item in sealed),
                    page_size=page_size, timeout_seconds=timeout_seconds,
                    processed_count=len(sealed),
                    cursor={"sealed_ranking_result_reused": True}, exclusions={},
                )
            calculation = await _calculate_ranking_evidence(
                session=session,
                source=source,
                manifest=manifest,
                artifact_store=artifact_store,
                timeout_seconds=timeout_seconds,
            )
        except (ArtifactConflictError, BoundedWorkLimitError, ValueError) as exc:
            payload = _phase_artifact_payload(
                phase="ranking_validation",
                status="pending",
                manifest=manifest,
                checkpoint=checkpoint,
                source=source,
                page_size=page_size,
                candidate_hash=manifest.candidate_registry_hash,
                details={
                    "unavailable_inputs": ["compatible_continuous_ranking_history"],
                    "reason": (
                        "pit_ranking_evidence_pending:"
                        f"{str(exc) or type(exc).__name__}"
                    ),
                },
            )
            return _pending_artifact_phase(
                phase="ranking_validation",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                item_key=f"pending:validation:{source.id}",
                payload=payload,
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                reason=PIT_PENDING_VALIDATION_INPUTS,
            )
        result_payloads = tuple(asdict(item) for item in calculation.endpoint_results)
        ledger_payloads = {
            candidate_id: {
                "base": _ledger_summary(calculation.base_ledgers[candidate_id]),
                "stress": _ledger_summary(calculation.stress_ledgers[candidate_id]),
            }
            for candidate_id in sorted(calculation.base_ledgers)
        }
        sample_counts = {
            candidate_id: {
                state: sum(item.status == state for item in samples)
                for state in ("completed", "pending", "excluded")
            }
            for candidate_id, samples in sorted(
                calculation.samples_by_candidate.items()
            )
        }
        outcome_hash = stable_contract_hash(
            {
                "source_cohort_hash": calculation.source_cohort.cohort_hash,
                "endpoint_results": result_payloads,
                "ledger_hashes": tuple(
                    (
                        candidate_id,
                        calculation.base_ledgers[candidate_id].ledger_hash,
                        calculation.stress_ledgers[candidate_id].ledger_hash,
                    )
                    for candidate_id in sorted(calculation.base_ledgers)
                ),
                "sample_hashes": tuple(
                    (
                        candidate_id,
                        tuple(item.sample_hash for item in samples),
                    )
                    for candidate_id, samples in sorted(
                        calculation.samples_by_candidate.items()
                    )
                ),
            }
        )
        payload = _phase_artifact_payload(
            phase="ranking_validation",
            status="completed",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=manifest.candidate_registry_hash,
            outcome_hash=outcome_hash,
            details={
                "validation_source_cohort": asdict(calculation.source_cohort),
                "source_cohort_hash": calculation.source_cohort.cohort_hash,
                "result_cutoff_date": calculation.trading_sessions[-1].isoformat(),
                "candidate_results": result_payloads,
                "paired_samples": {
                    candidate_id: tuple(asdict(item) for item in samples)
                    for candidate_id, samples in calculation.samples_by_candidate.items()
                },
                "outcome_data_cutoff": utcnow().isoformat(),
                "validation_plan": calculation.validation_plan,
                "market_regimes": dict(calculation.market_regimes),
                "trading_sessions": tuple(day.isoformat() for day in calculation.trading_sessions),
                "sample_counts": sample_counts,
                "continuous_accounts": ledger_payloads,
                "pure_momentum_control": calculation.momentum_details,
                "history_exclusions": calculation.history_exclusions,
                "unavailable_inputs": [
                    "chronological_walk_forward_folds",
                    "authorized_one_time_holdout_result",
                ],
                "reason": (
                    None
                    if calculation.endpoint_results
                    else "completed_software_with_insufficient_paired_samples"
                ),
                "production_weights_frozen": True,
            },
        )
        return _complete_artifact_phase(
            phase="ranking_validation",
            checkpoint=checkpoint,
            manifest=manifest,
            artifact_store=artifact_store,
            artifacts=(
                (
                    f"cohort:{calculation.source_cohort.cohort_hash}:"
                    f"outcome:{outcome_hash}",
                    payload,
                ),
            ),
            page_size=page_size,
            timeout_seconds=timeout_seconds,
            processed_count=1,
            cursor={
                "source_cohort_hash": calculation.source_cohort.cohort_hash,
                "endpoint_result_count": len(calculation.endpoint_results),
            },
            exclusions=(
                {PIT_PENDING_VALIDATION_INPUTS: 1}
                if not calculation.endpoint_results
                else {}
            ),
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
                    "deconfounding_evidence": build_deconfounding_evidence(),
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
        try:
            ranking = next(
                item.payload for item in validation_artifacts
                if item.payload.get("status") == "completed"
            )
            if ranking.get("manifest_hash") != manifest.manifest_hash:
                raise ValueError("ranking_manifest_mismatch")
            ranking = ranking["details"]
            sealed_cutoff = datetime.fromisoformat(ranking["outcome_data_cutoff"])
            results_by_id = {}
            for item in ranking["candidate_results"]:
                values = dict(item)
                values["ranking_source_kind"] = RankingSourceKind(values["ranking_source_kind"])
                for field in ("independent_dates", "overlapping_excluded_dates"):
                    values[field] = tuple(date.fromisoformat(value) for value in values[field])
                for field in ("bootstrap_confidence_interval", "accepted_sample_hashes"):
                    values[field] = tuple(values[field])
                result = RankingEndpointResult(**values)
                results_by_id[result.candidate_id] = result
            cohort = ranking["validation_source_cohort"]
            paired_samples = ranking["paired_samples"]
            ledger_summaries = ranking["continuous_accounts"]
            sample_counts = ranking["sample_counts"]
            history_exclusions = tuple(ranking["history_exclusions"])
        except (KeyError, TypeError, ValueError, StopIteration) as exc:
            return _phase_failure(
                checkpoint,
                reason=(
                    "pit_factor_sealed_ranking_unavailable:"
                    f"{str(exc) or type(exc).__name__}"
                ),
            )
        primary_result = results_by_id.get(PIT_PRIMARY_COMPARISON_CANDIDATE_ID)
        if primary_result is None:
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
                    "candidate_results": tuple(
                        asdict(item) for item in results_by_id.values()
                    ),
                    "unavailable_inputs": [
                        f"primary_candidate:{PIT_PRIMARY_COMPARISON_CANDIDATE_ID}"
                    ],
                    "reason": "completed_software_with_insufficient_paired_samples",
                },
            )
            return _complete_artifact_phase(
                phase="factor_evidence",
                checkpoint=checkpoint,
                manifest=manifest,
                artifact_store=artifact_store,
                artifacts=((f"factor-evidence-insufficient:{source.id}", payload),),
                page_size=page_size,
                timeout_seconds=timeout_seconds,
                processed_count=1,
                cursor={"promotion_state": "insufficient_data"},
                exclusions={PIT_PENDING_FACTOR_INPUTS: 1},
            )
        comparison_ids = (
            CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS,
            CANDIDATE_DAILY_CORE_TOP10_HYSTERESIS_REGIME,
        )
        raw_p_values: list[float] = []
        primary_values: tuple[float, ...] = ()
        for candidate_id in comparison_ids:
            result = results_by_id.get(candidate_id)
            if result is None:
                raw_p_values.append(1.0)
                continue
            independent_dates = {day.isoformat() for day in result.independent_dates}
            paired_excess = tuple(
                float(sample["candidate_net_return"])
                - float(sample["baseline_net_return"])
                for sample in paired_samples[candidate_id]
                if sample["status"] == "completed"
                and sample["signal_date"] in independent_dates
                and sample["candidate_net_return"] is not None
                and sample["baseline_net_return"] is not None
            )
            if paired_excess:
                if candidate_id == PIT_PRIMARY_COMPARISON_CANDIDATE_ID:
                    primary_values = paired_excess
                raw_p_values.append(
                    moving_block_bootstrap_positive_p_value(
                        paired_excess,
                        block_sessions=min(5, len(paired_excess)),
                        resamples=1_000,
                        seed=manifest.bootstrap_seed,
                    )
                )
            else:
                raw_p_values.append(1.0)
        if not raw_p_values:
            raw_p_values.append(1.0)
        fold_report = _ranking_fold_report(
            plan=ranking.get("validation_plan"),
            trading_sessions=tuple(date.fromisoformat(day) for day in ranking["trading_sessions"]),
            paired_samples=paired_samples[PIT_PRIMARY_COMPARISON_CANDIDATE_ID],
        )
        adjusted_lower = (
            moving_block_bootstrap_interval(
                primary_values, block_sessions=min(5, len(primary_values)),
                resamples=1_000, seed=manifest.bootstrap_seed,
                confidence=1.0 - 0.05 / len(comparison_ids),
            ).lower if primary_values else None
        )
        adjusted_p_values = holm_bonferroni(tuple(raw_p_values))
        if adjusted_p_values[0] >= 0.05 and adjusted_lower is not None:
            adjusted_lower = min(0.0, adjusted_lower)
        regime_values: dict[str, list[float]] = {}
        declared_regimes = tuple((ranking.get("validation_plan") or {}).get("declared_regimes", ()))
        primary_dates = {day.isoformat() for day in primary_result.independent_dates}
        for sample in paired_samples[PIT_PRIMARY_COMPARISON_CANDIDATE_ID]:
            regime = ranking.get("market_regimes", {}).get(sample["signal_date"])
            if sample["status"] == "completed" and sample["signal_date"] in primary_dates and regime:
                regime_values.setdefault(regime, []).append(
                    float(sample["candidate_net_return"]) - float(sample["baseline_net_return"])
                )
        regime_report = {
            regime: {"count": len(values), "mean_paired_net_excess": sum(values) / len(values)}
            for regime, values in sorted(regime_values.items())
        }
        regime_stable = bool(declared_regimes) and all(
            regime in regime_report and regime_report[regime]["mean_paired_net_excess"] > 0
            for regime in declared_regimes
        )
        gate_evidence = PromotionGateEvidence(
            decision_data_coverage_ratio=min(float(item["decision_data_coverage_ratio"]) for item in cohort["events"]),
            score_coverage_ratio=min(float(item["score_coverage_ratio"]) for item in cohort["events"]),
            eligible_point_in_time_sessions=len(cohort["events"]),
            independent_primary_dates=len(primary_result.independent_dates),
            completed_walk_forward_folds=fold_report["completed_fold_count"],
            adjusted_primary_interval_lower=adjusted_lower,
            fold_sign_stable=fold_report["fold_sign_stable"],
            regime_sign_stable=regime_stable,
            candidate_maximum_drawdown=primary_result.candidate_maximum_drawdown,
            baseline_maximum_drawdown=primary_result.baseline_maximum_drawdown,
            exclusion_gate_passed=(
                not history_exclusions and primary_result.sample_gate_passed
                and primary_result.coverage_ratio >= manifest.minimum_production_coverage
            ),
            holdout_consumed=False,
        )
        non_holdout_hash = stable_contract_hash({
            "plan_hash": (ranking.get("validation_plan") or {}).get("plan_hash"),
            "primary_result_hash": primary_result.result_hash,
            "gate_evidence": asdict(gate_evidence),
            "fold_report": fold_report, "regime_report": regime_report,
        })
        holdout_report = _read_authorized_ranking_holdout(
            artifact_store=artifact_store, manifest=manifest,
            plan=ranking.get("validation_plan"), non_holdout_gates=gate_evidence,
            frozen_non_holdout_evidence_hash=non_holdout_hash, consumed_at=utcnow(),
        )
        if holdout_report["status"] == "consumed":
            gate_evidence = replace(
                gate_evidence, holdout_consumed=True,
                adjusted_primary_interval_lower=(adjusted_lower if holdout_report["passed"] else 0.0),
            )
        promotion = evaluate_research_promotion(gate_evidence)
        candidate_diagnostics = {
            candidate_id: {
                "endpoint": asdict(result),
                "base_account": ledger_summaries[candidate_id]["base"],
                "stress_account": ledger_summaries[candidate_id]["stress"],
            }
            for candidate_id, result in sorted(results_by_id.items())
        }
        evidence_payload = build_operational_factor_evidence(
            manifest=manifest,
            data_cutoff=sealed_cutoff,
            primary_result=primary_result,
            exploratory_results=(),
            factor_diagnostics={},
            coverage={
                "source_date_count": len(cohort["events"]),
                "independent_primary_date_count": len(
                    primary_result.independent_dates
                ),
                "primary_coverage_ratio": primary_result.coverage_ratio,
                "decision_data_coverage_ratio": source.target_date_coverage_ratio,
                "score_coverage_ratio": source.warmup_coverage_ratio,
            },
            exclusion_counts={
                "history_identity_or_artifact_exclusion": len(
                    history_exclusions
                ),
                "paired_sample_exclusion": sum(
                    counts["excluded"] for counts in sample_counts.values()
                ),
            },
            split_reports={
                "development": {
                    "status": "calculated" if ranking.get("validation_plan") else "unavailable",
                    "reason": None if ranking.get("validation_plan") else "frozen_chronological_split_missing",
                    "source_cohort_hash": cohort["cohort_hash"],
                },
                "validation": {
                    "status": primary_result.evidence_status,
                    "independent_primary_date_count": len(
                        primary_result.independent_dates
                    ),
                    "required_independent_date_count": (
                        manifest.minimum_independent_dates
                    ),
                    "walk_forward_fold_count": fold_report["completed_fold_count"],
                    "walk_forward": fold_report,
                    "adjusted_primary_interval_lower": adjusted_lower,
                    "regime_report": regime_report,
                    "regime_sign_stable": regime_stable,
                    "frozen_non_holdout_evidence_hash": non_holdout_hash,
                    "required_walk_forward_fold_count": (
                        manifest.minimum_walk_forward_folds
                    ),
                },
                "holdout": {
                    **holdout_report,
                    "identity_hash": manifest.holdout_identity_hash,
                },
            },
            raw_primary_p_values=tuple(raw_p_values),
            promotion=promotion,
            limitations=(
                *( ("frozen_chronological_split_missing",) if ranking.get("validation_plan") is None else () ),
                *( ("regime_sign_stability_not_established",) if not regime_stable else () ),
                "holdout remains unconsumed until non-holdout gates pass and review is authorized",
                *history_exclusions,
            ),
            primary_diagnostics={
                "candidate_results": candidate_diagnostics,
                "sample_counts": sample_counts,
                "pure_momentum_control": ranking["pure_momentum_control"],
                "cost_provenance": (
                    RANKING_PORTFOLIO_BASE_COST_POLICY.provenance
                ),
                "result_cutoff_date": (
                    ranking["result_cutoff_date"]
                ),
                "formal_baseline_candidate_id": CANDIDATE_DAILY_CORE_TOP10,
                "predeclared_primary_comparison_candidate_id": (
                    PIT_PRIMARY_COMPARISON_CANDIDATE_ID
                ),
            },
            deconfounding_evidence=build_deconfounding_evidence(),
        )
        try:
            evidence = await persist_factor_evidence(session, evidence_payload)
        except ValueError as exc:
            return _phase_failure(
                checkpoint,
                reason=f"pit_factor_evidence_persist_failed:{type(exc).__name__}",
            )
        evidence_view = evidence_payload.canonical_payload()
        payload = _phase_artifact_payload(
            phase="factor_evidence",
            status="completed",
            manifest=manifest,
            checkpoint=checkpoint,
            source=source,
            page_size=page_size,
            candidate_hash=manifest.candidate_registry_hash,
            outcome_hash=evidence.evidence_hash,
            details={
                "component": build_operational_factor_evidence.__name__,
                "evidence_id": evidence.id,
                "evidence_hash": evidence.evidence_hash,
                "evidence": evidence_view,
                "promotion": evidence_view["promotion"],
                "unavailable_inputs": [
                    "chronological_walk_forward_folds",
                    "authorized_one_time_holdout_result",
                ],
                "reason": (
                    None
                    if promotion.state.value == "promotion_eligible"
                    else "calculated_evidence_does_not_meet_promotion_gates"
                ),
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
            cursor={
                "promotion_state": promotion.state.value,
                "evidence_hash": evidence.evidence_hash,
            },
            exclusions={},
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
            ranking_artifacts = _read_phase_page(
                artifact_store=artifact_store,
                manifest=manifest,
                phase="ranking_validation",
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
        ranking = next((
            _mapping(item.payload.get("details")) for item in ranking_artifacts
            if item.payload.get("manifest_hash") == manifest.manifest_hash
            and item.payload.get("status") == "completed"
        ), {})
        factor = next((
            _mapping(item.payload.get("details")) for item in factor_artifacts
            if item.payload.get("manifest_hash") == manifest.manifest_hash
            and item.payload.get("status") == "completed"
        ), {})
        evidence = _mapping(factor.get("evidence"))
        coverage = _mapping(_mapping(evidence.get("report")).get("coverage"))
        split_reports = _mapping(evidence.get("split_reports"))
        promotion = _mapping(factor.get("promotion"))
        readiness["ranking_diagnostics_available"] = bool(ranking.get("candidate_results"))
        cohort = _mapping(ranking.get("validation_source_cohort"))
        if cohort:
            readiness["factual_pit_session_count"] = len(cohort.get("events") or ())
        if promotion:
            readiness.update({
                "non_overlapping_primary_date_count": coverage.get(
                    "independent_primary_date_count", 0
                ),
                "completed_walk_forward_fold_count": _mapping(
                    split_reports.get("validation")
                ).get("walk_forward_fold_count", 0),
                "promotion_state": promotion.get("state"),
                "promotion_blockers": tuple(promotion.get("failed_gates") or ()),
                "ranking_research_ready": promotion.get("state") == "promotion_eligible",
            })
            policy_ready = all(item.payload.get("status") == "completed" for item in policy_artifacts)
            readiness["research_ready"] = readiness["ranking_research_ready"] and policy_ready
            if not policy_ready:
                readiness["promotion_blockers"] = (
                    *readiness["promotion_blockers"], PIT_PENDING_POLICY_INPUTS
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
                    list(readiness["promotion_blockers"]) if not readiness["research_ready"] else []
                ),
                "reason": (PIT_PENDING_FINAL_INPUTS if not readiness["research_ready"] else None),
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
            exclusions=({PIT_PENDING_FINAL_INPUTS: 1} if not readiness["research_ready"] else {}),
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
    checkpoint_view = _production_pit_checkpoint_view(source=source, checkpoint=checkpoint)
    if checkpoint.phase is ResearchLoopPhase.COMPLETE:
        final_rows = artifact_store.read_research_artifact_page(
            run_id=manifest.replay_run_key, phase="final_evidence", max_rows=5,
            max_seconds=_artifact_write_seconds(timeout_seconds),
        )
        for item in final_rows:
            readiness = _mapping(_mapping(item.payload.get("details")).get("readiness"))
            if item.payload.get("manifest_hash") == manifest.manifest_hash and readiness:
                checkpoint_view.update({
                    "readiness": dict(readiness),
                    "ranking_ready": readiness["ranking_ready"],
                    "research_ready": readiness["research_ready"],
                })
                break
    return PitContinuationResult(
        state="advanced",
        unavailable_reason=None,
        source_id=source.id,
        manifest_hash=manifest.manifest_hash,
        checkpoint=checkpoint_view,
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


async def _oldest_due_pit_source(
    session: AsyncSession,
    *,
    code_version: str,
    now: datetime | None = None,
) -> EtfPitCaptureSource | None:
    effective_now = now or utcnow()
    sources = (
        await session.scalars(
            select(EtfPitCaptureSource)
            .where(EtfPitCaptureSource.readiness_state == "complete")
            .order_by(
                EtfPitCaptureSource.as_of_trade_date.desc(),
                EtfPitCaptureSource.id.desc(),
            )
            .limit(512)
        )
    ).all()
    due: list[tuple[int, datetime, date, int, EtfPitCaptureSource]] = []
    for candidate in reversed(sources):
        try:
            manifest = build_production_pit_manifest(
                candidate,
                code_version=code_version,
                split_contract_hash=PIT_SPLIT_CONTRACT_HASH,
                holdout_identity_hash=PIT_HOLDOUT_IDENTITY_HASH,
                bootstrap_seed=PIT_BOOTSTRAP_SEED,
            )
        except ValueError:
            continue
        checkpoint = await session.scalar(
            select(EtfFactorExperimentCheckpoint).where(
                EtfFactorExperimentCheckpoint.manifest_hash == manifest.manifest_hash,
                EtfFactorExperimentCheckpoint.code_version == manifest.code_version,
            )
        )
        if checkpoint is None:
            due.append((0, datetime.min, candidate.as_of_trade_date, candidate.id, candidate))
            continue
        if checkpoint.status == "complete":
            continue
        if (
            checkpoint.status == "running"
            and checkpoint.lease_expires_at is not None
            and checkpoint.lease_expires_at > effective_now
        ):
            continue
        if (
            checkpoint.updated_at is not None
            and checkpoint.updated_at
            > effective_now - timedelta(minutes=PIT_SCHEDULER_CADENCE_MINUTES)
        ):
            continue
        state = _mapping(
            _mapping(checkpoint.cached_factor_rows_json).get(
                "__etf_point_in_time_research_loop_v1__"
            )
        )
        cursor = _mapping(state.get("phase_cursor"))
        if (
            state.get("phase") == "forward_outcomes"
            and cursor.get("pending_outcome_count", 0)
            and not cursor.get("matured_missing_data_count", 0)
            and cursor.get("outcome_cutoff_date") == effective_now.date().isoformat()
        ):
            continue
        # Unattempted dates go first. After that, retry age takes priority over
        # phase so a permanent candidate gap cannot starve mature outcome work.
        is_outcome_work = state.get("phase") not in {
            "inputs", "stage_a", "stage_b", "candidates"
        }
        due.append((
            int(is_outcome_work),
            checkpoint.updated_at or datetime.min,
            candidate.as_of_trade_date,
            candidate.id,
            candidate,
        ))
    return min(due, key=lambda item: (item[1], item[0], item[2], item[3]))[-1] if due else None


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
    if not enabled or not code_version.strip():
        return {
            "status": "skipped",
            **preflight.to_dict(),
            "research_only": True,
            "production_mutation_allowed": False,
        }
    captured_source: EtfPitCaptureSource | None = None
    if (
        preflight.due
        and preflight.source_signal_run_id is not None
        and preflight.provider_health_hash is not None
        and preflight.provider_health_identity is not None
        and preflight.replay_visibility_cutoff is not None
    ):
        captured = await capture_complete_pit_source(
            session,
            source_signal_run_id=preflight.source_signal_run_id,
            provider_health_hash=preflight.provider_health_hash,
            provider_health_identity=preflight.provider_health_identity,
            replay_visibility_cutoff=preflight.replay_visibility_cutoff,
        )
        captured_source = captured.source
        if captured_source is not None:
            await session.commit()

    due_source = await _oldest_due_pit_source(
        session,
        code_version=code_version.strip(),
    )
    if due_source is None:
        return {
            "status": "skipped",
            **preflight.to_dict(),
            "research_only": True,
            "production_mutation_allowed": False,
        }
    store_path = Path(artifact_dir) / "production-pit-research.sqlite3"
    result = await advance_production_pit_once(
        session,
        source_id=due_source.id,
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
        "trade_date": due_source.as_of_trade_date.isoformat(),
        "requested_trade_date": trade_date.isoformat(),
        "source_signal_run_id": due_source.source_signal_run_id,
        "source_id": result.source_id,
        "manifest_hash": result.manifest_hash,
        "checkpoint": result.checkpoint,
        "provider_health_hash": due_source.provider_health_hash,
        "replay_visibility_cutoff": due_source.replay_visibility_cutoff.isoformat(),
        "captured_source_id": captured_source.id if captured_source is not None else None,
        "artifact_path": str(store_path),
        "research_only": True,
        "production_mutation_allowed": False,
    }
