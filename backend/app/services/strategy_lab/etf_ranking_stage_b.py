"""Complete-date Stage-B ranking for point-in-time ETF research replay."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from datetime import time as wall_time
from typing import Any, Generic, Protocol, TypeVar
from zoneinfo import ZoneInfo

from app.services.tracked_positions.lifecycle import (
    stable_contract_hash,
    stable_contract_json,
)

from .etf_action_replay.artifact_store import ReplayArtifactStore
from .etf_ranking_stage_a import (
    MAX_STAGE_A_ITEMS,
    NewStageAReplayIdentityRequiredError,
    StageAArtifactConflictError,
    StageACheckpoint,
    StageAFeatureArtifact,
    StageAReplayContract,
    load_stage_a_checkpoint,
    read_stage_a_feature_artifact_page,
)

STAGE_B_SCHEMA_VERSION = "etf-ranking-stage-b-v1"
MAX_STAGE_B_SECONDS = 55.0
_CHECKPOINT_FORMAT = "etf-ranking-stage-b-checkpoint-v1"
_RANKING_SOURCE_KIND = "research_replay"
_SCORE_FIELD = "research_score"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_COVERAGE_DIMENSIONS = (
    "point_in_time_universe",
    "adjusted_price",
    "feature_component",
    "score_eligible",
    "forward_outcome",
)
_EMPTY_MANIFEST_CHAIN_HASH = stable_contract_hash(
    {"artifact_type": "etf-ranking-stage-b-date-manifest", "content_hashes": []}
)
_EMPTY_EVENT_CHAIN_HASH = stable_contract_hash(
    {"artifact_type": "etf-ranking-stage-b-ranking-event", "content_hashes": []}
)


class _DatedArtifact(Protocol):
    @property
    def replay_date(self) -> date: ...


_T = TypeVar("_T", bound=_DatedArtifact)


class StageBBoundedWorkError(ValueError):
    pass


class StageBIncompleteDateError(ValueError):
    pass


class StageBArtifactConflictError(ValueError):
    pass


class NewStageBReplayIdentityRequiredError(ValueError):
    pass


def _require_hash(label: str, value: str) -> None:
    try:
        valid = len(value) == 64 and int(value, 16) >= 0
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{label} must be a SHA-256 hash")


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


@dataclass(frozen=True)
class StageBReplayContract:
    replay_run_key: str
    score_contract_id: str
    score_manifest_hash: str
    source_snapshot_hash: str
    universe_manifest_hash: str
    feature_schema_version: str
    candidate_registry_hash: str
    decision_cutoff_semantics: str
    schema_version: str = STAGE_B_SCHEMA_VERSION

    def __post_init__(self) -> None:
        required = (
            self.replay_run_key,
            self.score_contract_id,
            self.feature_schema_version,
            self.decision_cutoff_semantics,
            self.schema_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("Stage-B replay identity fields are required")
        for label, value in (
            ("score_manifest_hash", self.score_manifest_hash),
            ("source_snapshot_hash", self.source_snapshot_hash),
            ("universe_manifest_hash", self.universe_manifest_hash),
            ("candidate_registry_hash", self.candidate_registry_hash),
        ):
            _require_hash(label, value)

    @property
    def content_identity_hash(self) -> str:
        return stable_contract_hash(_contract_content_payload(self))


@dataclass(frozen=True)
class StageBBatchRequest:
    max_dates: int
    max_feature_rows: int
    max_seconds: float = MAX_STAGE_B_SECONDS
    worker_count: int = 1

    def __post_init__(self) -> None:
        if self.worker_count != 1:
            raise StageBBoundedWorkError("worker_count must be 1")
        if self.max_dates < 1:
            raise StageBBoundedWorkError("max_dates must be positive")
        if self.max_feature_rows < 1:
            raise StageBBoundedWorkError("max_feature_rows must be positive")
        if (
            not math.isfinite(self.max_seconds)
            or not 0 < self.max_seconds <= MAX_STAGE_B_SECONDS
        ):
            raise StageBBoundedWorkError("max_seconds must be within (0, 55]")


@dataclass(frozen=True)
class StageBFeatureInput:
    replay_run_key: str
    replay_date: date
    asset_code: str
    score_contract_id: str
    score_manifest_hash: str
    feature_schema_version: str
    source_snapshot_hash: str
    universe_manifest_hash: str
    universe_hash: str
    unit_input_hash: str
    upstream_feature_hash: str
    series_hash: str | None
    score_eligible: bool
    exclusion_reason: str | None
    exclusion_detail: str | None
    research_score: float | None
    trend_score: float | None
    risk_score: float | None
    liquidity_score: float | None
    feature_hash: str


@dataclass(frozen=True)
class StageBSourceDateManifest:
    replay_run_key: str
    replay_date: date
    decision_cutoff: datetime
    score_contract_id: str
    score_manifest_hash: str
    feature_schema_version: str
    source_snapshot_hash: str
    universe_manifest_hash: str
    universe_hash: str
    input_hash: str
    authoritative_asset_codes: tuple[str, ...]
    expected_universe_count: int
    feature_hashes: tuple[tuple[str, str], ...]
    upstream_feature_hashes: tuple[tuple[str, str], ...]
    upstream_checkpoint_hash: str | None
    upstream_completion_identity_hash: str | None
    upstream_artifact_chain_hash: str | None
    atomic_complete: bool
    manifest_hash: str


@dataclass(frozen=True)
class StageBSourceDate:
    manifest: StageBSourceDateManifest
    features: tuple[StageBFeatureInput, ...]


@dataclass(frozen=True)
class StageBCoverageDimension:
    dimension: str
    numerator: int
    denominator: int
    rate: float | None
    exclusions: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class StageBDateManifest:
    replay_run_key: str
    replay_date: date
    decision_cutoff: datetime
    score_contract_id: str
    score_manifest_hash: str
    feature_schema_version: str
    stage_b_schema_version: str
    source_snapshot_hash: str
    source_date_manifest_hash: str
    universe_manifest_hash: str
    universe_hash: str
    input_hash: str
    feature_manifest_hash: str
    authoritative_asset_codes: tuple[str, ...]
    expected_universe_count: int
    score_eligible_asset_codes: tuple[str, ...]
    exclusions: tuple[tuple[str, str, str], ...]
    coverage: tuple[StageBCoverageDimension, ...]
    manifest_hash: str


@dataclass(frozen=True)
class StageBRankedItem:
    asset_code: str
    rank: int
    research_score: float
    feature_hash: str


@dataclass(frozen=True)
class StageBRankingEvent:
    replay_run_key: str
    replay_date: date
    ranking_source_kind: str
    score_contract_id: str
    score_field: str
    score_manifest_hash: str
    stage_b_schema_version: str
    date_manifest_hash: str
    source_date_manifest_hash: str
    universe_hash: str
    input_hash: str
    feature_manifest_hash: str
    ranked_items: tuple[StageBRankedItem, ...]
    all_scored: tuple[str, ...]
    top5: tuple[str, ...]
    top10: tuple[str, ...]
    top20: tuple[str, ...]
    event_hash: str


@dataclass(frozen=True)
class StageBSummary:
    ranked_date_count: int
    first_complete_date: date
    last_complete_date: date
    coverage: tuple[StageBCoverageDimension, ...]
    manifest_chain_hash: str
    event_chain_hash: str
    summary_hash: str


@dataclass(frozen=True)
class StageBCheckpoint:
    replay_run_key: str
    generation: int
    last_complete_date: date
    manifest_count: int
    event_count: int
    manifest_chain_hash: str
    event_chain_hash: str
    summary: StageBSummary
    complete: bool
    contract_identity_hash: str
    completion_identity_hash: str
    atomic_complete: bool
    checkpoint_hash: str
    format_version: str = _CHECKPOINT_FORMAT


@dataclass(frozen=True)
class StageBContinuationProgress:
    status: str
    complete: bool
    processed_dates: int
    generation: int
    next_after_date: date | None
    manifest_chain_hash: str
    event_chain_hash: str
    summary_hash: str
    completion_identity_hash: str
    elapsed_seconds: float
    worker_count: int = 1


@dataclass(frozen=True)
class StageBReadPage(Generic[_T]):
    rows: tuple[_T, ...]
    has_more: bool
    next_after_date: date | None


@dataclass(frozen=True)
class StageBStageASourceDatePage:
    source_dates: tuple[StageBSourceDate, ...]
    has_more: bool
    next_cursor: tuple[date, str] | None
    source_rows_read: int
    stage_a_checkpoint_hash: str
    stage_a_completion_identity_hash: str


def _contract_payload(contract: StageBReplayContract) -> dict[str, Any]:
    return asdict(contract)


def _contract_content_payload(contract: StageBReplayContract) -> dict[str, Any]:
    payload = _contract_payload(contract)
    payload.pop("replay_run_key")
    return payload


def stage_b_contract_from_stage_a(
    contract: StageAReplayContract,
) -> StageBReplayContract:
    """Map one frozen Stage-A identity without weakening any contract field."""

    return StageBReplayContract(
        replay_run_key=contract.replay_run_key,
        score_contract_id="daily_reconstructable_v1",
        score_manifest_hash=contract.score_manifest_hash,
        source_snapshot_hash=contract.source_snapshot_hash,
        universe_manifest_hash=contract.universe_manifest_hash,
        feature_schema_version=contract.schema_version,
        candidate_registry_hash=contract.candidate_registry_hash,
        decision_cutoff_semantics=contract.decision_cutoff_semantics,
    )


def _feature_payload(feature: StageBFeatureInput) -> dict[str, Any]:
    payload = asdict(feature)
    payload.pop("feature_hash")
    return payload


def _source_manifest_payload(manifest: StageBSourceDateManifest) -> dict[str, Any]:
    payload = asdict(manifest)
    payload.pop("manifest_hash")
    return payload


def _date_manifest_payload(manifest: StageBDateManifest) -> dict[str, Any]:
    payload = asdict(manifest)
    payload.pop("manifest_hash")
    return payload


def _event_payload(event: StageBRankingEvent) -> dict[str, Any]:
    payload = asdict(event)
    payload.pop("event_hash")
    return payload


def _summary_payload(summary: StageBSummary) -> dict[str, Any]:
    payload = asdict(summary)
    payload.pop("summary_hash")
    return payload


def _checkpoint_payload(checkpoint: StageBCheckpoint) -> dict[str, Any]:
    payload = asdict(checkpoint)
    payload.pop("checkpoint_hash")
    return payload


def _validate_feature_shape(feature: StageBFeatureInput) -> None:
    if not feature.asset_code.strip() or not isinstance(feature.score_eligible, bool):
        raise StageBIncompleteDateError("feature identity is invalid")
    for label, value in (
        ("unit_input_hash", feature.unit_input_hash),
        ("upstream_feature_hash", feature.upstream_feature_hash),
        ("universe_hash", feature.universe_hash),
        ("feature_hash", feature.feature_hash),
    ):
        _require_hash(label, value)
    if feature.series_hash is not None:
        _require_hash("series_hash", feature.series_hash)
    component_values = (
        feature.research_score,
        feature.trend_score,
        feature.risk_score,
        feature.liquidity_score,
    )
    if any(value is not None and not math.isfinite(value) for value in component_values):
        raise StageBIncompleteDateError("feature components must be finite")
    if feature.score_eligible:
        if feature.series_hash is None or not all(_finite(value) for value in component_values):
            raise StageBIncompleteDateError(
                "score-eligible feature requires adjusted input and all components"
            )
        if feature.exclusion_reason is not None:
            raise StageBIncompleteDateError(
                "score-eligible feature cannot carry an exclusion"
            )
    elif feature.research_score is not None or not feature.exclusion_reason:
        raise StageBIncompleteDateError(
            "score-ineligible feature requires a reason and no research score"
        )


def build_stage_b_feature_input(
    *,
    contract: StageBReplayContract,
    replay_date: date,
    asset_code: str,
    universe_hash: str,
    unit_input_hash: str,
    series_hash: str | None,
    score_eligible: bool,
    upstream_feature_hash: str | None = None,
    exclusion_reason: str | None = None,
    exclusion_detail: str | None = None,
    research_score: float | None = None,
    trend_score: float | None = None,
    risk_score: float | None = None,
    liquidity_score: float | None = None,
) -> StageBFeatureInput:
    draft = StageBFeatureInput(
        replay_run_key=contract.replay_run_key,
        replay_date=replay_date,
        asset_code=asset_code.strip(),
        score_contract_id=contract.score_contract_id,
        score_manifest_hash=contract.score_manifest_hash,
        feature_schema_version=contract.feature_schema_version,
        source_snapshot_hash=contract.source_snapshot_hash,
        universe_manifest_hash=contract.universe_manifest_hash,
        universe_hash=universe_hash,
        unit_input_hash=unit_input_hash,
        upstream_feature_hash=upstream_feature_hash or unit_input_hash,
        series_hash=series_hash,
        score_eligible=score_eligible,
        exclusion_reason=exclusion_reason,
        exclusion_detail=exclusion_detail,
        research_score=research_score,
        trend_score=trend_score,
        risk_score=risk_score,
        liquidity_score=liquidity_score,
        feature_hash="pending",
    )
    value = replace(draft, feature_hash=stable_contract_hash(_feature_payload(draft)))
    _validate_feature_shape(value)
    return value


def _validate_decision_cutoff(replay_date: date, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StageBIncompleteDateError("decision cutoff must be timezone-aware")
    local = value.astimezone(_SHANGHAI)
    if local.date() != replay_date or local.timetz().replace(tzinfo=None) < wall_time(15):
        raise StageBIncompleteDateError(
            "decision cutoff must identify the completed Shanghai session"
        )


def build_stage_b_source_date(
    *,
    contract: StageBReplayContract,
    replay_date: date,
    decision_cutoff: datetime,
    universe_hash: str,
    authoritative_asset_codes: Iterable[str],
    features: Iterable[StageBFeatureInput],
    upstream_checkpoint_hash: str | None = None,
    upstream_completion_identity_hash: str | None = None,
    upstream_artifact_chain_hash: str | None = None,
) -> StageBSourceDate:
    _validate_decision_cutoff(replay_date, decision_cutoff)
    _require_hash("universe_hash", universe_hash)
    codes = tuple(sorted(code.strip() for code in authoritative_asset_codes))
    feature_values = tuple(sorted(features, key=lambda item: item.asset_code))
    feature_codes = tuple(item.asset_code for item in feature_values)
    if (
        not codes
        or any(not code for code in codes)
        or len(codes) != len(set(codes))
        or feature_codes != codes
    ):
        raise StageBIncompleteDateError(
            "source date requires the full authoritative universe"
        )
    for feature in feature_values:
        _validate_source_feature(feature, contract=contract, replay_date=replay_date)
        if feature.universe_hash != universe_hash:
            raise NewStageBReplayIdentityRequiredError(
                "source date contains a mixed contract"
            )
    feature_hashes = tuple((item.asset_code, item.feature_hash) for item in feature_values)
    upstream_feature_hashes = tuple(
        (item.asset_code, item.upstream_feature_hash) for item in feature_values
    )
    upstream_checkpoint_values = (
        upstream_checkpoint_hash,
        upstream_completion_identity_hash,
        upstream_artifact_chain_hash,
    )
    if any(value is None for value in upstream_checkpoint_values) != all(
        value is None for value in upstream_checkpoint_values
    ):
        raise StageBIncompleteDateError(
            "upstream checkpoint identity must be complete or absent"
        )
    for label, value in (
        ("upstream_checkpoint_hash", upstream_checkpoint_hash),
        (
            "upstream_completion_identity_hash",
            upstream_completion_identity_hash,
        ),
        ("upstream_artifact_chain_hash", upstream_artifact_chain_hash),
    ):
        if value is not None:
            _require_hash(label, value)
    input_hash = stable_contract_hash(
        {
            "replay_date": replay_date,
            "universe_hash": universe_hash,
            "unit_inputs": tuple(
                (item.asset_code, item.unit_input_hash) for item in feature_values
            ),
            "upstream_features": upstream_feature_hashes,
        }
    )
    draft = StageBSourceDateManifest(
        replay_run_key=contract.replay_run_key,
        replay_date=replay_date,
        decision_cutoff=decision_cutoff,
        score_contract_id=contract.score_contract_id,
        score_manifest_hash=contract.score_manifest_hash,
        feature_schema_version=contract.feature_schema_version,
        source_snapshot_hash=contract.source_snapshot_hash,
        universe_manifest_hash=contract.universe_manifest_hash,
        universe_hash=universe_hash,
        input_hash=input_hash,
        authoritative_asset_codes=codes,
        expected_universe_count=len(codes),
        feature_hashes=feature_hashes,
        upstream_feature_hashes=upstream_feature_hashes,
        upstream_checkpoint_hash=upstream_checkpoint_hash,
        upstream_completion_identity_hash=upstream_completion_identity_hash,
        upstream_artifact_chain_hash=upstream_artifact_chain_hash,
        atomic_complete=True,
        manifest_hash="pending",
    )
    manifest = replace(
        draft,
        manifest_hash=stable_contract_hash(_source_manifest_payload(draft)),
    )
    return StageBSourceDate(manifest=manifest, features=feature_values)


def _stage_a_feature_to_stage_b(
    artifact: StageAFeatureArtifact,
    *,
    stage_a_contract: StageAReplayContract,
    stage_b_contract: StageBReplayContract,
) -> StageBFeatureInput:
    actual_identity = (
        artifact.replay_run_key,
        artifact.score_contract_id,
        artifact.score_manifest_hash,
        artifact.schema_version,
        artifact.source_snapshot_hash,
        artifact.universe_manifest_hash,
    )
    expected_identity = (
        stage_a_contract.replay_run_key,
        stage_b_contract.score_contract_id,
        stage_a_contract.score_manifest_hash,
        stage_a_contract.schema_version,
        stage_a_contract.source_snapshot_hash,
        stage_a_contract.universe_manifest_hash,
    )
    if actual_identity != expected_identity:
        raise NewStageBReplayIdentityRequiredError(
            "Stage-A feature contract does not match the Stage-B contract"
        )
    return build_stage_b_feature_input(
        contract=stage_b_contract,
        replay_date=artifact.replay_date,
        asset_code=artifact.asset_code,
        universe_hash=artifact.universe_hash,
        unit_input_hash=artifact.unit_input_hash,
        upstream_feature_hash=artifact.content_hash,
        series_hash=artifact.series_hash,
        score_eligible=artifact.score_eligible,
        exclusion_reason=artifact.exclusion_reason,
        exclusion_detail=artifact.exclusion_detail,
        research_score=artifact.research_score,
        trend_score=artifact.trend_score,
        risk_score=artifact.risk_score,
        liquidity_score=artifact.liquidity_score,
    )


def _load_complete_stage_a_checkpoint(
    *,
    store: ReplayArtifactStore,
    stage_a_contract: StageAReplayContract,
) -> StageACheckpoint:
    try:
        checkpoint = load_stage_a_checkpoint(
            store=store,
            contract=stage_a_contract,
        )
    except NewStageAReplayIdentityRequiredError as exc:
        raise NewStageBReplayIdentityRequiredError(
            "Stage-A checkpoint contract changed"
        ) from exc
    except StageAArtifactConflictError as exc:
        raise StageBArtifactConflictError(
            "Stage-A checkpoint integrity check failed"
        ) from exc
    if checkpoint is None:
        raise StageBIncompleteDateError("Stage-A checkpoint is not materialized")
    if checkpoint.status == "blocked":
        raise StageBIncompleteDateError(
            f"Stage-A checkpoint is blocked: {checkpoint.reason}"
        )
    if (
        checkpoint.status != "complete"
        or not checkpoint.replay_complete
        or not checkpoint.atomic_complete
    ):
        raise StageBIncompleteDateError(
            f"Stage-A checkpoint is {checkpoint.status}, not complete"
        )
    if checkpoint.artifact_count < 1:
        raise StageBIncompleteDateError(
            "Stage-A complete checkpoint contains no feature artifact"
        )
    return checkpoint


def read_stage_b_source_date_page_from_stage_a(
    *,
    store: ReplayArtifactStore,
    stage_a_contract: StageAReplayContract,
    stage_b_contract: StageBReplayContract,
    after_cursor: tuple[date, str] | None,
    max_dates: int,
    max_feature_rows: int,
    stage_a_page_rows: int = MAX_STAGE_A_ITEMS,
    max_seconds: float = MAX_STAGE_B_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> StageBStageASourceDatePage:
    """Map only atomically completed Stage-A dates through bounded keyset pages."""

    if max_dates < 1:
        raise StageBBoundedWorkError("Stage-A adapter max_dates must be positive")
    if max_feature_rows < 2:
        raise StageBBoundedWorkError(
            "Stage-A adapter max_feature_rows must reserve a date-boundary row"
        )
    if not 1 <= stage_a_page_rows <= min(MAX_STAGE_A_ITEMS, max_feature_rows):
        raise StageBBoundedWorkError(
            "Stage-A adapter page rows exceed the bounded feature page"
        )
    if not math.isfinite(max_seconds) or not 0 < max_seconds <= MAX_STAGE_B_SECONDS:
        raise StageBBoundedWorkError(
            "Stage-A adapter max_seconds must be within (0, 55]"
        )
    if stage_b_contract != stage_b_contract_from_stage_a(stage_a_contract):
        raise NewStageBReplayIdentityRequiredError(
            "Stage-A and Stage-B contract identities do not match"
        )
    started = clock()
    checkpoint = _load_complete_stage_a_checkpoint(
        store=store,
        stage_a_contract=stage_a_contract,
    )
    artifacts: list[StageAFeatureArtifact] = []
    cursor = after_cursor
    exhausted = False
    while len(artifacts) < max_feature_rows:
        if clock() - started >= max_seconds:
            raise StageBBoundedWorkError("Stage-A adapter exceeds max_seconds")
        page_size = min(stage_a_page_rows, max_feature_rows - len(artifacts))
        try:
            page = read_stage_a_feature_artifact_page(
                store=store,
                replay_run_key=stage_a_contract.replay_run_key,
                after_cursor=cursor,
                max_rows=page_size,
            )
        except StageAArtifactConflictError as exc:
            raise StageBArtifactConflictError(
                "Stage-A feature artifact integrity check failed"
            ) from exc
        except NewStageAReplayIdentityRequiredError as exc:
            raise NewStageBReplayIdentityRequiredError(
                "Stage-A feature contract changed"
            ) from exc
        if not page.items:
            exhausted = True
            break
        artifacts.extend(page.items)
        cursor = page.items[-1].cursor
        if not page.has_more:
            exhausted = True
            break
        observed_dates = tuple(dict.fromkeys(item.replay_date for item in artifacts))
        if len(observed_dates) > max_dates:
            break

    if not artifacts:
        return StageBStageASourceDatePage(
            source_dates=(),
            has_more=False,
            next_cursor=None,
            source_rows_read=0,
            stage_a_checkpoint_hash=checkpoint.checkpoint_hash,
            stage_a_completion_identity_hash=checkpoint.completion_identity_hash,
        )
    observed_dates = tuple(dict.fromkeys(item.replay_date for item in artifacts))
    complete_dates = observed_dates if exhausted else observed_dates[:-1]
    selected_dates = complete_dates[:max_dates]
    if not selected_dates:
        raise StageBBoundedWorkError(
            "one complete Stage-A date exceeds max_feature_rows"
        )
    selected_set = set(selected_dates)
    selected_artifacts = tuple(
        item for item in artifacts if item.replay_date in selected_set
    )
    sources: list[StageBSourceDate] = []
    for replay_date in selected_dates:
        daily = tuple(
            item for item in selected_artifacts if item.replay_date == replay_date
        )
        decision_cutoffs = {item.decision_cutoff for item in daily}
        universe_hashes = {item.universe_hash for item in daily}
        if len(decision_cutoffs) != 1 or len(universe_hashes) != 1:
            raise NewStageBReplayIdentityRequiredError(
                "Stage-A complete date contains mixed cutoff or universe identity"
            )
        mapped = tuple(
            _stage_a_feature_to_stage_b(
                item,
                stage_a_contract=stage_a_contract,
                stage_b_contract=stage_b_contract,
            )
            for item in daily
        )
        sources.append(
            build_stage_b_source_date(
                contract=stage_b_contract,
                replay_date=replay_date,
                decision_cutoff=next(iter(decision_cutoffs)),
                universe_hash=next(iter(universe_hashes)),
                authoritative_asset_codes=tuple(item.asset_code for item in daily),
                features=mapped,
                upstream_checkpoint_hash=checkpoint.checkpoint_hash,
                upstream_completion_identity_hash=(
                    checkpoint.completion_identity_hash
                ),
                upstream_artifact_chain_hash=checkpoint.artifact_chain_hash,
            )
        )
    next_cursor = selected_artifacts[-1].cursor
    has_unselected_observation = any(
        item.replay_date not in selected_set for item in artifacts
    )
    return StageBStageASourceDatePage(
        source_dates=tuple(sources),
        has_more=not exhausted or has_unselected_observation,
        next_cursor=next_cursor,
        source_rows_read=len(artifacts),
        stage_a_checkpoint_hash=checkpoint.checkpoint_hash,
        stage_a_completion_identity_hash=checkpoint.completion_identity_hash,
    )


def _validate_source_feature(
    feature: StageBFeatureInput,
    *,
    contract: StageBReplayContract,
    replay_date: date,
) -> None:
    actual_identity = (
        feature.replay_run_key,
        feature.replay_date,
        feature.score_contract_id,
        feature.score_manifest_hash,
        feature.feature_schema_version,
        feature.source_snapshot_hash,
        feature.universe_manifest_hash,
    )
    expected_identity = (
        contract.replay_run_key,
        replay_date,
        contract.score_contract_id,
        contract.score_manifest_hash,
        contract.feature_schema_version,
        contract.source_snapshot_hash,
        contract.universe_manifest_hash,
    )
    if actual_identity != expected_identity:
        raise NewStageBReplayIdentityRequiredError(
            "source date contains a mixed contract; a new replay identity is required"
        )
    _validate_feature_shape(feature)
    if feature.feature_hash != stable_contract_hash(_feature_payload(feature)):
        raise StageBArtifactConflictError("Stage-B source feature hash mismatch")


def _reason_counts(
    features: Iterable[StageBFeatureInput],
) -> tuple[tuple[str, int], ...]:
    values = Counter(
        item.exclusion_reason or "unspecified_exclusion" for item in features
    )
    return tuple(sorted(values.items()))


def _coverage(
    dimension: str,
    *,
    numerator: int,
    denominator: int,
    exclusions: tuple[tuple[str, int], ...] = (),
) -> StageBCoverageDimension:
    if dimension not in _COVERAGE_DIMENSIONS:
        raise ValueError(f"unsupported coverage dimension: {dimension}")
    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("coverage counts are invalid")
    return StageBCoverageDimension(
        dimension=dimension,
        numerator=numerator,
        denominator=denominator,
        rate=(numerator / denominator if denominator else None),
        exclusions=exclusions,
    )


def _feature_components_complete(feature: StageBFeatureInput) -> bool:
    return feature.series_hash is not None and all(
        _finite(value)
        for value in (
            feature.research_score,
            feature.trend_score,
            feature.risk_score,
            feature.liquidity_score,
        )
    )


def _required_research_score(feature: StageBFeatureInput) -> float:
    if feature.research_score is None:  # guarded by score eligibility validation
        raise StageBIncompleteDateError("ranked feature has no research score")
    return feature.research_score


def _validate_source_date(
    source: StageBSourceDate,
    *,
    contract: StageBReplayContract,
) -> tuple[StageBDateManifest, StageBRankingEvent]:
    manifest = source.manifest
    if not manifest.atomic_complete:
        raise StageBIncompleteDateError(
            "Stage-B requires an atomic complete date manifest"
        )
    if manifest.manifest_hash != stable_contract_hash(_source_manifest_payload(manifest)):
        raise StageBArtifactConflictError("source date manifest hash mismatch")
    manifest_identity = (
        manifest.replay_run_key,
        manifest.score_contract_id,
        manifest.score_manifest_hash,
        manifest.feature_schema_version,
        manifest.source_snapshot_hash,
        manifest.universe_manifest_hash,
    )
    expected_identity = (
        contract.replay_run_key,
        contract.score_contract_id,
        contract.score_manifest_hash,
        contract.feature_schema_version,
        contract.source_snapshot_hash,
        contract.universe_manifest_hash,
    )
    if manifest_identity != expected_identity:
        raise NewStageBReplayIdentityRequiredError(
            "source date contains a mixed contract; a new replay identity is required"
        )
    _validate_decision_cutoff(manifest.replay_date, manifest.decision_cutoff)
    _require_hash("universe_hash", manifest.universe_hash)
    _require_hash("input_hash", manifest.input_hash)
    codes = manifest.authoritative_asset_codes
    if (
        not codes
        or codes != tuple(sorted(codes))
        or len(codes) != len(set(codes))
        or manifest.expected_universe_count != len(codes)
    ):
        raise StageBIncompleteDateError("authoritative universe is not canonical")
    features = source.features
    feature_codes = tuple(item.asset_code for item in features)
    if len(feature_codes) != len(set(feature_codes)):
        raise StageBIncompleteDateError("source date contains duplicate feature codes")
    if len(features) != manifest.expected_universe_count or tuple(
        sorted(feature_codes)
    ) != codes:
        raise StageBIncompleteDateError(
            "Stage-B requires the full authoritative universe, not a code chunk"
        )
    by_code = {item.asset_code: item for item in features}
    for feature in features:
        _validate_source_feature(
            feature,
            contract=contract,
            replay_date=manifest.replay_date,
        )
        if feature.universe_hash != manifest.universe_hash:
            raise NewStageBReplayIdentityRequiredError(
                "source date contains a mixed contract; a new replay identity is required"
            )
    actual_feature_hashes = tuple((code, by_code[code].feature_hash) for code in codes)
    if actual_feature_hashes != manifest.feature_hashes:
        raise StageBIncompleteDateError("source date feature manifest is incomplete")
    actual_upstream_hashes = tuple(
        (code, by_code[code].upstream_feature_hash) for code in codes
    )
    if actual_upstream_hashes != manifest.upstream_feature_hashes:
        raise StageBIncompleteDateError(
            "source date upstream feature manifest is incomplete"
        )
    actual_input_hash = stable_contract_hash(
        {
            "replay_date": manifest.replay_date,
            "universe_hash": manifest.universe_hash,
            "unit_inputs": tuple(
                (code, by_code[code].unit_input_hash) for code in codes
            ),
            "upstream_features": actual_upstream_hashes,
        }
    )
    if actual_input_hash != manifest.input_hash:
        raise StageBArtifactConflictError("source date input hash mismatch")

    adjusted = tuple(item for item in features if item.series_hash is not None)
    missing_adjusted = tuple(item for item in features if item.series_hash is None)
    component_ready = tuple(item for item in adjusted if _feature_components_complete(item))
    missing_components = tuple(
        item for item in adjusted if not _feature_components_complete(item)
    )
    score_eligible = tuple(item for item in features if item.score_eligible)
    score_ineligible = tuple(item for item in features if not item.score_eligible)
    coverage = (
        _coverage(
            "point_in_time_universe",
            numerator=len(codes),
            denominator=manifest.expected_universe_count,
        ),
        _coverage(
            "adjusted_price",
            numerator=len(adjusted),
            denominator=manifest.expected_universe_count,
            exclusions=_reason_counts(missing_adjusted),
        ),
        _coverage(
            "feature_component",
            numerator=len(component_ready),
            denominator=len(adjusted),
            exclusions=_reason_counts(missing_components),
        ),
        _coverage(
            "score_eligible",
            numerator=len(score_eligible),
            denominator=manifest.expected_universe_count,
            exclusions=_reason_counts(score_ineligible),
        ),
        _coverage(
            "forward_outcome",
            numerator=0,
            denominator=len(score_eligible),
            exclusions=(
                (("future_window_pending", len(score_eligible)),)
                if score_eligible
                else ()
            ),
        ),
    )
    feature_manifest_hash = stable_contract_hash(
        {"features": actual_feature_hashes}
    )
    exclusions = tuple(
        sorted(
            (
                item.asset_code,
                item.exclusion_reason or "unspecified_exclusion",
                item.exclusion_detail or "",
            )
            for item in score_ineligible
        )
    )
    date_draft = StageBDateManifest(
        replay_run_key=contract.replay_run_key,
        replay_date=manifest.replay_date,
        decision_cutoff=manifest.decision_cutoff,
        score_contract_id=contract.score_contract_id,
        score_manifest_hash=contract.score_manifest_hash,
        feature_schema_version=contract.feature_schema_version,
        stage_b_schema_version=contract.schema_version,
        source_snapshot_hash=contract.source_snapshot_hash,
        source_date_manifest_hash=manifest.manifest_hash,
        universe_manifest_hash=contract.universe_manifest_hash,
        universe_hash=manifest.universe_hash,
        input_hash=manifest.input_hash,
        feature_manifest_hash=feature_manifest_hash,
        authoritative_asset_codes=codes,
        expected_universe_count=manifest.expected_universe_count,
        score_eligible_asset_codes=tuple(
            sorted(item.asset_code for item in score_eligible)
        ),
        exclusions=exclusions,
        coverage=coverage,
        manifest_hash="pending",
    )
    date_manifest = replace(
        date_draft,
        manifest_hash=stable_contract_hash(_date_manifest_payload(date_draft)),
    )

    ranked_features = tuple(
        sorted(
            score_eligible,
            key=lambda item: (-_required_research_score(item), item.asset_code),
        )
    )
    ranked_items = tuple(
        StageBRankedItem(
            asset_code=item.asset_code,
            rank=index,
            research_score=_required_research_score(item),
            feature_hash=item.feature_hash,
        )
        for index, item in enumerate(ranked_features, start=1)
    )
    all_scored = tuple(item.asset_code for item in ranked_items)
    event_draft = StageBRankingEvent(
        replay_run_key=contract.replay_run_key,
        replay_date=manifest.replay_date,
        ranking_source_kind=_RANKING_SOURCE_KIND,
        score_contract_id=contract.score_contract_id,
        score_field=_SCORE_FIELD,
        score_manifest_hash=contract.score_manifest_hash,
        stage_b_schema_version=contract.schema_version,
        date_manifest_hash=date_manifest.manifest_hash,
        source_date_manifest_hash=manifest.manifest_hash,
        universe_hash=manifest.universe_hash,
        input_hash=manifest.input_hash,
        feature_manifest_hash=feature_manifest_hash,
        ranked_items=ranked_items,
        all_scored=all_scored,
        top5=all_scored[:5],
        top10=all_scored[:10],
        top20=all_scored[:20],
        event_hash="pending",
    )
    event = replace(
        event_draft,
        event_hash=stable_contract_hash(_event_payload(event_draft)),
    )
    return date_manifest, event


def _merge_coverage(
    previous: Sequence[StageBCoverageDimension],
    current: Iterable[StageBDateManifest],
) -> tuple[StageBCoverageDimension, ...]:
    totals: dict[str, list[Any]] = {
        name: [0, 0, Counter()] for name in _COVERAGE_DIMENSIONS
    }
    for value in previous:
        totals[value.dimension][0] += value.numerator
        totals[value.dimension][1] += value.denominator
        totals[value.dimension][2].update(dict(value.exclusions))
    for manifest in current:
        for value in manifest.coverage:
            totals[value.dimension][0] += value.numerator
            totals[value.dimension][1] += value.denominator
            totals[value.dimension][2].update(dict(value.exclusions))
    return tuple(
        _coverage(
            name,
            numerator=int(totals[name][0]),
            denominator=int(totals[name][1]),
            exclusions=tuple(sorted(totals[name][2].items())),
        )
        for name in _COVERAGE_DIMENSIONS
    )


def _build_summary(
    *,
    previous: StageBSummary | None,
    manifests: tuple[StageBDateManifest, ...],
    manifest_chain_hash: str,
    event_chain_hash: str,
) -> StageBSummary:
    first = (
        previous.first_complete_date if previous is not None else manifests[0].replay_date
    )
    count = (previous.ranked_date_count if previous is not None else 0) + len(manifests)
    draft = StageBSummary(
        ranked_date_count=count,
        first_complete_date=first,
        last_complete_date=manifests[-1].replay_date,
        coverage=_merge_coverage(
            previous.coverage if previous is not None else (), manifests
        ),
        manifest_chain_hash=manifest_chain_hash,
        event_chain_hash=event_chain_hash,
        summary_hash="pending",
    )
    return replace(draft, summary_hash=stable_contract_hash(_summary_payload(draft)))


def _build_checkpoint(
    *,
    contract: StageBReplayContract,
    generation: int,
    previous: StageBCheckpoint | None,
    manifests: tuple[StageBDateManifest, ...],
    events: tuple[StageBRankingEvent, ...],
    complete: bool,
) -> StageBCheckpoint:
    manifest_chain = (
        previous.manifest_chain_hash
        if previous is not None
        else _EMPTY_MANIFEST_CHAIN_HASH
    )
    event_chain = (
        previous.event_chain_hash if previous is not None else _EMPTY_EVENT_CHAIN_HASH
    )
    for manifest in manifests:
        manifest_chain = stable_contract_hash(
            {"previous": manifest_chain, "artifact": manifest.manifest_hash}
        )
    for event in events:
        event_chain = stable_contract_hash(
            {"previous": event_chain, "artifact": event.event_hash}
        )
    summary = _build_summary(
        previous=previous.summary if previous is not None else None,
        manifests=manifests,
        manifest_chain_hash=manifest_chain,
        event_chain_hash=event_chain,
    )
    completion_identity_hash = stable_contract_hash(
        {
            "contract": _contract_content_payload(contract),
            "manifest_chain_hash": manifest_chain,
            "event_chain_hash": event_chain,
            "summary_hash": summary.summary_hash,
            "complete": complete,
        }
    )
    draft = StageBCheckpoint(
        replay_run_key=contract.replay_run_key,
        generation=generation,
        last_complete_date=manifests[-1].replay_date,
        manifest_count=(previous.manifest_count if previous is not None else 0)
        + len(manifests),
        event_count=(previous.event_count if previous is not None else 0) + len(events),
        manifest_chain_hash=manifest_chain,
        event_chain_hash=event_chain,
        summary=summary,
        complete=complete,
        contract_identity_hash=contract.content_identity_hash,
        completion_identity_hash=completion_identity_hash,
        atomic_complete=True,
        checkpoint_hash="pending",
    )
    return replace(
        draft,
        checkpoint_hash=stable_contract_hash(_checkpoint_payload(draft)),
    )


def _initialize_stage_b(store: ReplayArtifactStore) -> None:
    with store._connect() as connection:  # noqa: SLF001 - run-local artifact store
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ranking_stage_b_date_manifests (
                replay_run_key TEXT NOT NULL,
                replay_date TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (replay_run_key, replay_date)
            );
            CREATE INDEX IF NOT EXISTS ix_ranking_stage_b_manifest_page
                ON ranking_stage_b_date_manifests (replay_run_key, replay_date);
            CREATE TABLE IF NOT EXISTS ranking_stage_b_events (
                replay_run_key TEXT NOT NULL,
                replay_date TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (replay_run_key, replay_date)
            );
            CREATE INDEX IF NOT EXISTS ix_ranking_stage_b_event_page
                ON ranking_stage_b_events (replay_run_key, replay_date);
            CREATE TABLE IF NOT EXISTS ranking_stage_b_checkpoints (
                replay_run_key TEXT NOT NULL PRIMARY KEY,
                generation INTEGER NOT NULL,
                contract_json TEXT NOT NULL,
                checkpoint_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )


def _coverage_from_payload(payload: Mapping[str, Any]) -> StageBCoverageDimension:
    return StageBCoverageDimension(
        dimension=str(payload["dimension"]),
        numerator=int(payload["numerator"]),
        denominator=int(payload["denominator"]),
        rate=(float(payload["rate"]) if payload.get("rate") is not None else None),
        exclusions=tuple(
            (str(item[0]), int(item[1])) for item in payload["exclusions"]
        ),
    )


def _date_manifest_from_json(raw_json: str) -> StageBDateManifest:
    try:
        payload = json.loads(raw_json)
        return StageBDateManifest(
            replay_run_key=str(payload["replay_run_key"]),
            replay_date=date.fromisoformat(str(payload["replay_date"])),
            decision_cutoff=datetime.fromisoformat(str(payload["decision_cutoff"])),
            score_contract_id=str(payload["score_contract_id"]),
            score_manifest_hash=str(payload["score_manifest_hash"]),
            feature_schema_version=str(payload["feature_schema_version"]),
            stage_b_schema_version=str(payload["stage_b_schema_version"]),
            source_snapshot_hash=str(payload["source_snapshot_hash"]),
            source_date_manifest_hash=str(payload["source_date_manifest_hash"]),
            universe_manifest_hash=str(payload["universe_manifest_hash"]),
            universe_hash=str(payload["universe_hash"]),
            input_hash=str(payload["input_hash"]),
            feature_manifest_hash=str(payload["feature_manifest_hash"]),
            authoritative_asset_codes=tuple(
                str(item) for item in payload["authoritative_asset_codes"]
            ),
            expected_universe_count=int(payload["expected_universe_count"]),
            score_eligible_asset_codes=tuple(
                str(item) for item in payload["score_eligible_asset_codes"]
            ),
            exclusions=tuple(
                (str(item[0]), str(item[1]), str(item[2]))
                for item in payload["exclusions"]
            ),
            coverage=tuple(
                _coverage_from_payload(item) for item in payload["coverage"]
            ),
            manifest_hash=str(payload["manifest_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StageBArtifactConflictError(
            "stored Stage-B date manifest is incomplete"
        ) from exc


def _event_from_json(raw_json: str) -> StageBRankingEvent:
    try:
        payload = json.loads(raw_json)
        return StageBRankingEvent(
            replay_run_key=str(payload["replay_run_key"]),
            replay_date=date.fromisoformat(str(payload["replay_date"])),
            ranking_source_kind=str(payload["ranking_source_kind"]),
            score_contract_id=str(payload["score_contract_id"]),
            score_field=str(payload["score_field"]),
            score_manifest_hash=str(payload["score_manifest_hash"]),
            stage_b_schema_version=str(payload["stage_b_schema_version"]),
            date_manifest_hash=str(payload["date_manifest_hash"]),
            source_date_manifest_hash=str(payload["source_date_manifest_hash"]),
            universe_hash=str(payload["universe_hash"]),
            input_hash=str(payload["input_hash"]),
            feature_manifest_hash=str(payload["feature_manifest_hash"]),
            ranked_items=tuple(
                StageBRankedItem(
                    asset_code=str(item["asset_code"]),
                    rank=int(item["rank"]),
                    research_score=float(item["research_score"]),
                    feature_hash=str(item["feature_hash"]),
                )
                for item in payload["ranked_items"]
            ),
            all_scored=tuple(str(item) for item in payload["all_scored"]),
            top5=tuple(str(item) for item in payload["top5"]),
            top10=tuple(str(item) for item in payload["top10"]),
            top20=tuple(str(item) for item in payload["top20"]),
            event_hash=str(payload["event_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StageBArtifactConflictError(
            "stored Stage-B ranking event hash mismatch or incomplete payload"
        ) from exc


def _summary_from_payload(payload: Mapping[str, Any]) -> StageBSummary:
    summary = StageBSummary(
        ranked_date_count=int(payload["ranked_date_count"]),
        first_complete_date=date.fromisoformat(str(payload["first_complete_date"])),
        last_complete_date=date.fromisoformat(str(payload["last_complete_date"])),
        coverage=tuple(_coverage_from_payload(item) for item in payload["coverage"]),
        manifest_chain_hash=str(payload["manifest_chain_hash"]),
        event_chain_hash=str(payload["event_chain_hash"]),
        summary_hash=str(payload["summary_hash"]),
    )
    if summary.summary_hash != stable_contract_hash(_summary_payload(summary)):
        raise StageBArtifactConflictError("stored Stage-B summary hash mismatch")
    return summary


def _checkpoint_from_json(raw_json: str) -> StageBCheckpoint:
    try:
        payload = json.loads(raw_json)
        checkpoint = StageBCheckpoint(
            replay_run_key=str(payload["replay_run_key"]),
            generation=int(payload["generation"]),
            last_complete_date=date.fromisoformat(str(payload["last_complete_date"])),
            manifest_count=int(payload["manifest_count"]),
            event_count=int(payload["event_count"]),
            manifest_chain_hash=str(payload["manifest_chain_hash"]),
            event_chain_hash=str(payload["event_chain_hash"]),
            summary=_summary_from_payload(payload["summary"]),
            complete=payload["complete"] is True,
            contract_identity_hash=str(payload["contract_identity_hash"]),
            completion_identity_hash=str(payload["completion_identity_hash"]),
            atomic_complete=payload["atomic_complete"] is True,
            checkpoint_hash=str(payload["checkpoint_hash"]),
            format_version=str(payload["format_version"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, StageBArtifactConflictError):
            raise
        raise StageBArtifactConflictError(
            "stored Stage-B checkpoint is incomplete"
        ) from exc
    if (
        checkpoint.format_version != _CHECKPOINT_FORMAT
        or not checkpoint.atomic_complete
        or checkpoint.checkpoint_hash
        != stable_contract_hash(_checkpoint_payload(checkpoint))
    ):
        raise StageBArtifactConflictError("stored Stage-B checkpoint hash mismatch")
    return checkpoint


def load_stage_b_checkpoint(
    *,
    store: ReplayArtifactStore,
    contract: StageBReplayContract,
) -> StageBCheckpoint | None:
    _initialize_stage_b(store)
    with store._connect() as connection:  # noqa: SLF001
        row = connection.execute(
            """
            SELECT contract_json, payload_json FROM ranking_stage_b_checkpoints
            WHERE replay_run_key=?
            """,
            (contract.replay_run_key,),
        ).fetchone()
    if row is None:
        return None
    if str(row[0]) != stable_contract_json(_contract_payload(contract)):
        raise NewStageBReplayIdentityRequiredError(
            "Stage-B contract changed; a new replay identity is required"
        )
    checkpoint = _checkpoint_from_json(str(row[1]))
    if checkpoint.contract_identity_hash != contract.content_identity_hash:
        raise NewStageBReplayIdentityRequiredError(
            "Stage-B contract changed; a new replay identity is required"
        )
    return checkpoint


def _read_page(
    *,
    store: ReplayArtifactStore,
    table: str,
    replay_run_key: str,
    after_date: date | None,
    max_rows: int,
    max_seconds: float,
    decoder: Callable[[str], _T],
    content_hash: Callable[[_T], str],
    computed_hash: Callable[[_T], str],
) -> StageBReadPage[_T]:
    if max_rows < 1:
        raise StageBBoundedWorkError("Stage-B page max_rows must be positive")
    if not math.isfinite(max_seconds) or not 0 < max_seconds <= MAX_STAGE_B_SECONDS:
        raise StageBBoundedWorkError("Stage-B page max_seconds must be within (0, 55]")
    started = time.monotonic()
    _initialize_stage_b(store)
    clause = "replay_run_key=?"
    values: list[object] = [replay_run_key]
    if after_date is not None:
        clause += " AND replay_date>?"
        values.append(after_date.isoformat())
    with store._connect() as connection:  # noqa: SLF001
        rows = connection.execute(
            f"""
            SELECT content_hash, payload_json FROM {table}
            WHERE {clause} ORDER BY replay_date LIMIT ?
            """,
            (*values, max_rows + 1),
        ).fetchall()
    if time.monotonic() - started >= max_seconds:
        raise StageBBoundedWorkError("Stage-B page read exceeds max_seconds")
    has_more = len(rows) > max_rows
    output: list[_T] = []
    for expected_hash, raw_json in rows[:max_rows]:
        value = decoder(str(raw_json))
        if str(expected_hash) != content_hash(value) or content_hash(
            value
        ) != computed_hash(value):
            raise StageBArtifactConflictError("stored Stage-B artifact hash mismatch")
        output.append(value)
    next_after = output[-1].replay_date if output else None
    return StageBReadPage(tuple(output), has_more, next_after)


def read_stage_b_date_manifest_page(
    *,
    store: ReplayArtifactStore,
    replay_run_key: str,
    after_date: date | None,
    max_rows: int,
    max_seconds: float = MAX_STAGE_B_SECONDS,
) -> StageBReadPage[StageBDateManifest]:
    return _read_page(
        store=store,
        table="ranking_stage_b_date_manifests",
        replay_run_key=replay_run_key,
        after_date=after_date,
        max_rows=max_rows,
        max_seconds=max_seconds,
        decoder=_date_manifest_from_json,
        content_hash=lambda item: item.manifest_hash,
        computed_hash=lambda item: stable_contract_hash(_date_manifest_payload(item)),
    )


def read_stage_b_ranking_event_page(
    *,
    store: ReplayArtifactStore,
    replay_run_key: str,
    after_date: date | None,
    max_rows: int,
    max_seconds: float = MAX_STAGE_B_SECONDS,
) -> StageBReadPage[StageBRankingEvent]:
    return _read_page(
        store=store,
        table="ranking_stage_b_events",
        replay_run_key=replay_run_key,
        after_date=after_date,
        max_rows=max_rows,
        max_seconds=max_seconds,
        decoder=_event_from_json,
        content_hash=lambda item: item.event_hash,
        computed_hash=lambda item: stable_contract_hash(_event_payload(item)),
    )


def _progress(
    checkpoint: StageBCheckpoint,
    *,
    processed_dates: int,
    elapsed_seconds: float,
) -> StageBContinuationProgress:
    return StageBContinuationProgress(
        status="complete" if checkpoint.complete else "partial",
        complete=checkpoint.complete,
        processed_dates=processed_dates,
        generation=checkpoint.generation,
        next_after_date=None if checkpoint.complete else checkpoint.last_complete_date,
        manifest_chain_hash=checkpoint.manifest_chain_hash,
        event_chain_hash=checkpoint.event_chain_hash,
        summary_hash=checkpoint.summary.summary_hash,
        completion_identity_hash=checkpoint.completion_identity_hash,
        elapsed_seconds=max(elapsed_seconds, 0.0),
    )


def run_stage_b_continuation(
    *,
    store: ReplayArtifactStore,
    contract: StageBReplayContract,
    request: StageBBatchRequest,
    source_dates: Iterable[StageBSourceDate],
    source_has_more: bool,
    clock: Callable[[], float] = time.monotonic,
) -> StageBContinuationProgress:
    """Rank and atomically persist one bounded page of complete replay dates."""

    started = clock()
    deadline = started + request.max_seconds
    if (
        contract.schema_version != STAGE_B_SCHEMA_VERSION
        or contract.score_contract_id != "daily_reconstructable_v1"
        or contract.decision_cutoff_semantics != "asia_shanghai_post_close_v1"
    ):
        raise NewStageBReplayIdentityRequiredError(
            "unsupported Stage-B contract; a new replay identity is required"
        )
    if not isinstance(source_has_more, bool):
        raise StageBBoundedWorkError("source_has_more must be boolean")
    checkpoint = load_stage_b_checkpoint(store=store, contract=contract)
    if checkpoint is not None and checkpoint.complete:
        return _progress(
            checkpoint,
            processed_dates=0,
            elapsed_seconds=clock() - started,
        )

    sources: list[StageBSourceDate] = []
    feature_count = 0
    for source in source_dates:
        if len(sources) >= request.max_dates:
            raise StageBBoundedWorkError("source dates exceed max_dates")
        feature_count += len(source.features)
        if feature_count > request.max_feature_rows:
            raise StageBBoundedWorkError("source features exceed max_feature_rows")
        if clock() >= deadline:
            raise StageBBoundedWorkError("Stage-B source read exceeds max_seconds")
        sources.append(source)
    if not sources:
        raise StageBBoundedWorkError("Stage-B requires at least one complete source date")
    source_tuple = tuple(sources)
    dates = tuple(item.manifest.replay_date for item in source_tuple)
    if dates != tuple(sorted(set(dates))):
        raise StageBIncompleteDateError("source dates must be sorted and unique")
    if checkpoint is not None and dates[0] <= checkpoint.last_complete_date:
        raise StageBIncompleteDateError(
            "source date must use the checkpoint last-complete-date keyset"
        )

    manifests: list[StageBDateManifest] = []
    events: list[StageBRankingEvent] = []
    for source in source_tuple:
        if clock() >= deadline:
            raise StageBBoundedWorkError("Stage-B ranking exceeds max_seconds")
        manifest, event = _validate_source_date(source, contract=contract)
        manifests.append(manifest)
        events.append(event)
    manifest_tuple = tuple(manifests)
    event_tuple = tuple(events)
    generation = (checkpoint.generation if checkpoint is not None else 0) + 1
    next_checkpoint = _build_checkpoint(
        contract=contract,
        generation=generation,
        previous=checkpoint,
        manifests=manifest_tuple,
        events=event_tuple,
        complete=not source_has_more,
    )
    if clock() >= deadline:
        raise StageBBoundedWorkError("Stage-B commit has no remaining time budget")

    connection = store._connect()  # noqa: SLF001
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT generation, contract_json FROM ranking_stage_b_checkpoints
            WHERE replay_run_key=?
            """,
            (contract.replay_run_key,),
        ).fetchone()
        expected_generation = checkpoint.generation if checkpoint is not None else 0
        actual_generation = int(existing[0]) if existing is not None else 0
        if actual_generation != expected_generation:
            raise StageBArtifactConflictError("stale Stage-B checkpoint generation")
        contract_json = stable_contract_json(_contract_payload(contract))
        if existing is not None and str(existing[1]) != contract_json:
            raise NewStageBReplayIdentityRequiredError(
                "Stage-B contract changed; a new replay identity is required"
            )
        for manifest in manifest_tuple:
            connection.execute(
                """
                INSERT INTO ranking_stage_b_date_manifests
                    (replay_run_key, replay_date, content_hash, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (replay_run_key, replay_date) DO NOTHING
                """,
                (
                    contract.replay_run_key,
                    manifest.replay_date.isoformat(),
                    manifest.manifest_hash,
                    stable_contract_json(manifest),
                ),
            )
            stored = connection.execute(
                """
                SELECT content_hash FROM ranking_stage_b_date_manifests
                WHERE replay_run_key=? AND replay_date=?
                """,
                (contract.replay_run_key, manifest.replay_date.isoformat()),
            ).fetchone()
            if stored is None or str(stored[0]) != manifest.manifest_hash:
                raise StageBArtifactConflictError(
                    "Stage-B date manifest is immutable"
                )
        for event in event_tuple:
            connection.execute(
                """
                INSERT INTO ranking_stage_b_events
                    (replay_run_key, replay_date, content_hash, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (replay_run_key, replay_date) DO NOTHING
                """,
                (
                    contract.replay_run_key,
                    event.replay_date.isoformat(),
                    event.event_hash,
                    stable_contract_json(event),
                ),
            )
            stored = connection.execute(
                """
                SELECT content_hash FROM ranking_stage_b_events
                WHERE replay_run_key=? AND replay_date=?
                """,
                (contract.replay_run_key, event.replay_date.isoformat()),
            ).fetchone()
            if stored is None or str(stored[0]) != event.event_hash:
                raise StageBArtifactConflictError(
                    "Stage-B ranking event is immutable"
                )
        if clock() >= deadline:
            raise StageBBoundedWorkError("Stage-B artifact commit exceeds max_seconds")
        checkpoint_json = stable_contract_json(next_checkpoint)
        connection.execute(
            """
            INSERT INTO ranking_stage_b_checkpoints
                (replay_run_key, generation, contract_json, checkpoint_hash, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (replay_run_key) DO UPDATE SET
                generation=excluded.generation,
                contract_json=excluded.contract_json,
                checkpoint_hash=excluded.checkpoint_hash,
                payload_json=excluded.payload_json
            """,
            (
                contract.replay_run_key,
                generation,
                contract_json,
                next_checkpoint.checkpoint_hash,
                checkpoint_json,
            ),
        )
        if clock() >= deadline:
            raise StageBBoundedWorkError("Stage-B checkpoint exceeds max_seconds")
        connection.execute("COMMIT")
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()

    return _progress(
        next_checkpoint,
        processed_dates=len(manifest_tuple),
        elapsed_seconds=clock() - started,
    )
