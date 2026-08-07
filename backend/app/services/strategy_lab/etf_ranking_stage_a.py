"""Bounded Stage-A feature materialization for ETF point-in-time ranking replay."""

from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from datetime import time as wall_time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.short_research.daily_reconstructable import (
    REQUIRED_BAR_COUNT,
    DailyReconstructableScore,
    daily_reconstructable_manifest,
    score_daily_reconstructable,
)
from app.services.tracked_positions.lifecycle import (
    stable_contract_hash,
    stable_contract_json,
)

from .etf_action_replay.artifact_store import ReplayArtifactStore
from .etf_point_in_time_decision_data import (
    MAX_CODES_PER_REPLAY_INPUT_PAGE,
    PointInTimeAdjustedSeries,
    PointInTimeRankingInputSnapshot,
    ReplayInputExclusion,
    load_point_in_time_etf_decision_inputs,
)

STAGE_A_SCHEMA_VERSION = "etf-ranking-stage-a-v1"
MAX_STAGE_A_SECONDS = 55.0
MAX_STAGE_A_ITEMS = 200
MAX_STAGE_A_SOURCE_ROWS = REQUIRED_BAR_COUNT * MAX_STAGE_A_ITEMS
MAX_STAGE_A_PAGES = 32
MAX_STAGE_A_PEAK_RSS_BYTES = 3 * 1024**3
_STAGE_A_COMMIT_RESERVE_SECONDS = 1.0
_CHECKPOINT_FORMAT = "etf-ranking-stage-a-checkpoint-v1"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_EMPTY_ARTIFACT_CHAIN_HASH = stable_contract_hash(
    {"artifact_type": "etf-ranking-stage-a-feature", "content_hashes": []}
)


class StageABoundedWorkError(ValueError):
    pass


class StageAArtifactConflictError(ValueError):
    pass


class NewStageAReplayIdentityRequiredError(ValueError):
    pass


def _require_hash(label: str, value: str) -> None:
    try:
        valid = len(value) == 64 and int(value, 16) >= 0
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{label} must be a SHA-256 hash")


@dataclass(frozen=True)
class StageAReplayContract:
    replay_run_key: str
    score_manifest_hash: str
    source_snapshot_hash: str
    universe_manifest_hash: str
    decision_cutoff_semantics: str
    schema_version: str
    candidate_registry_hash: str

    def __post_init__(self) -> None:
        if not self.replay_run_key.strip():
            raise ValueError("replay_run_key is required")
        if not self.decision_cutoff_semantics.strip() or not self.schema_version.strip():
            raise ValueError("cutoff semantics and schema version are required")
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
class StageABatchRequest:
    max_source_rows: int
    max_items: int
    max_pages: int
    max_seconds: float = MAX_STAGE_A_SECONDS
    worker_count: int = 1
    peak_rss_limit_bytes: int = 3 * 1024**3

    def __post_init__(self) -> None:
        if self.worker_count != 1:
            raise StageABoundedWorkError("worker_count must be 1")
        if self.max_source_rows < 1:
            raise StageABoundedWorkError("max_source_rows must be positive")
        if self.max_source_rows > MAX_STAGE_A_SOURCE_ROWS:
            raise StageABoundedWorkError(
                f"max_source_rows must not exceed {MAX_STAGE_A_SOURCE_ROWS}"
            )
        if self.max_items < 1:
            raise StageABoundedWorkError("max_items must be positive")
        if self.max_items > MAX_STAGE_A_ITEMS:
            raise StageABoundedWorkError(
                f"max_items must not exceed {MAX_STAGE_A_ITEMS}"
            )
        if self.max_pages < 1:
            raise StageABoundedWorkError("max_pages must be positive")
        if self.max_pages > MAX_STAGE_A_PAGES:
            raise StageABoundedWorkError(
                f"max_pages must not exceed {MAX_STAGE_A_PAGES}"
            )
        if (
            not math.isfinite(self.max_seconds)
            or not 0 < self.max_seconds <= MAX_STAGE_A_SECONDS
        ):
            raise StageABoundedWorkError("max_seconds must be within (0, 55]")
        if self.peak_rss_limit_bytes < 1:
            raise StageABoundedWorkError("peak_rss_limit_bytes must be positive")
        if self.peak_rss_limit_bytes > MAX_STAGE_A_PEAK_RSS_BYTES:
            raise StageABoundedWorkError(
                "peak_rss_limit_bytes must not exceed "
                f"{MAX_STAGE_A_PEAK_RSS_BYTES}"
            )


@dataclass(frozen=True)
class StageASourcePage:
    replay_date: date
    decision_cutoff: datetime
    source_snapshot_hash: str
    page_source_snapshot_hash: str
    universe_manifest_hash: str
    universe_hash: str
    page_input_hash: str
    coverage_manifest_hash: str
    asset_codes: tuple[str, ...]
    eligible_inputs: tuple[PointInTimeAdjustedSeries, ...]
    exclusions: tuple[ReplayInputExclusion, ...]
    is_last_page: bool
    has_more_codes: bool = False
    next_replay_date: date | None = None


@dataclass(frozen=True)
class StageAFeatureArtifact:
    replay_run_key: str
    replay_date: date
    asset_code: str
    decision_cutoff: datetime
    score_contract_id: str
    score_manifest_hash: str
    schema_version: str
    source_snapshot_hash: str
    page_source_snapshot_hash: str
    universe_manifest_hash: str
    universe_hash: str
    unit_input_hash: str
    series_hash: str | None
    score_eligible: bool
    exclusion_reason: str | None
    exclusion_detail: str | None
    research_score: float | None
    trend_score: float | None
    risk_score: float | None
    liquidity_score: float | None
    score_payload: Mapping[str, Any] | None
    content_hash: str

    @property
    def cursor(self) -> tuple[date, str]:
        return self.replay_date, self.asset_code


@dataclass(frozen=True)
class StageAPageIdentity:
    replay_run_key: str
    replay_date: date
    first_asset_code: str
    last_asset_code: str
    asset_codes: tuple[str, ...]
    decision_cutoff: datetime
    score_manifest_hash: str
    schema_version: str
    source_snapshot_hash: str
    page_source_snapshot_hash: str
    universe_manifest_hash: str
    universe_hash: str
    page_input_hash: str
    coverage_manifest_hash: str
    page_key: str
    content_hash: str


@dataclass(frozen=True)
class StageACheckpoint:
    replay_run_key: str
    generation: int
    last_completed_date: date
    last_completed_code: str
    resume_date: date | None
    resume_code_after: str | None
    artifact_count: int
    artifact_chain_hash: str
    replay_complete: bool
    status: str
    reason: str | None
    active_replay_date: date | None
    active_decision_cutoff: datetime | None
    active_universe_hash: str | None
    active_page_source_snapshot_hash: str | None
    completion_identity_hash: str
    contract_identity_hash: str
    atomic_complete: bool
    checkpoint_hash: str
    format_version: str = _CHECKPOINT_FORMAT

    @property
    def cursor(self) -> tuple[date, str]:
        return self.last_completed_date, self.last_completed_code


@dataclass(frozen=True)
class StageAContinuationProgress:
    status: str
    reason: str | None
    complete: bool
    processed_items: int
    source_rows_read: int
    generation: int
    next_cursor: tuple[date, str] | None
    artifact_chain_hash: str
    completion_identity_hash: str
    elapsed_seconds: float
    peak_rss_bytes: int
    within_peak_rss_limit: bool
    worker_count: int = 1


@dataclass(frozen=True)
class StageAFeatureArtifactPage:
    items: tuple[StageAFeatureArtifact, ...]
    has_more: bool
    next_cursor: tuple[date, str] | None


def _contract_payload(contract: StageAReplayContract) -> dict[str, Any]:
    return asdict(contract)


def _contract_content_payload(contract: StageAReplayContract) -> dict[str, Any]:
    payload = _contract_payload(contract)
    payload.pop("replay_run_key")
    return payload


def _checkpoint_payload(
    checkpoint: StageACheckpoint,
    *,
    include_hash: bool,
) -> dict[str, Any]:
    payload = asdict(checkpoint)
    if not include_hash:
        payload.pop("checkpoint_hash")
    return payload


def _build_checkpoint(
    *,
    contract: StageAReplayContract,
    generation: int,
    cursor: tuple[date, str],
    artifact_count: int,
    artifact_chain_hash: str,
    replay_complete: bool,
    resume_cursor: tuple[date, str | None] | None,
    active_identity: tuple[date, datetime, str, str] | None,
    status: str | None = None,
    reason: str | None = None,
) -> StageACheckpoint:
    checkpoint_status = status or ("complete" if replay_complete else "partial")
    completion_identity_hash = stable_contract_hash(
        {
            "contract": _contract_content_payload(contract),
            "artifact_count": artifact_count,
            "artifact_chain_hash": artifact_chain_hash,
            "replay_complete": replay_complete,
            "status": checkpoint_status,
            "reason": reason,
        }
    )
    draft = StageACheckpoint(
        replay_run_key=contract.replay_run_key,
        generation=generation,
        last_completed_date=cursor[0],
        last_completed_code=cursor[1],
        resume_date=resume_cursor[0] if resume_cursor is not None else None,
        resume_code_after=resume_cursor[1] if resume_cursor is not None else None,
        artifact_count=artifact_count,
        artifact_chain_hash=artifact_chain_hash,
        replay_complete=replay_complete,
        status=checkpoint_status,
        reason=reason,
        active_replay_date=(active_identity[0] if active_identity is not None else None),
        active_decision_cutoff=(
            active_identity[1] if active_identity is not None else None
        ),
        active_universe_hash=(active_identity[2] if active_identity is not None else None),
        active_page_source_snapshot_hash=(
            active_identity[3] if active_identity is not None else None
        ),
        completion_identity_hash=completion_identity_hash,
        contract_identity_hash=contract.content_identity_hash,
        atomic_complete=True,
        checkpoint_hash="pending",
    )
    return replace(
        draft,
        checkpoint_hash=stable_contract_hash(
            _checkpoint_payload(draft, include_hash=False)
        ),
    )


def _checkpoint_from_json(raw_json: str) -> StageACheckpoint:
    try:
        payload = json.loads(raw_json)
        active_replay_date = (
            date.fromisoformat(str(payload["active_replay_date"]))
            if payload.get("active_replay_date") is not None
            else None
        )
        active_decision_cutoff = (
            datetime.fromisoformat(str(payload["active_decision_cutoff"]))
            if payload.get("active_decision_cutoff") is not None
            else None
        )
        checkpoint = StageACheckpoint(
            replay_run_key=str(payload["replay_run_key"]),
            generation=int(payload["generation"]),
            last_completed_date=date.fromisoformat(str(payload["last_completed_date"])),
            last_completed_code=str(payload["last_completed_code"]),
            resume_date=(
                date.fromisoformat(str(payload["resume_date"]))
                if payload.get("resume_date") is not None
                else None
            ),
            resume_code_after=(
                str(payload["resume_code_after"])
                if payload.get("resume_code_after") is not None
                else None
            ),
            artifact_count=int(payload["artifact_count"]),
            artifact_chain_hash=str(payload["artifact_chain_hash"]),
            replay_complete=payload["replay_complete"] is True,
            status=str(payload["status"]),
            reason=(str(payload["reason"]) if payload.get("reason") is not None else None),
            active_replay_date=active_replay_date,
            active_decision_cutoff=active_decision_cutoff,
            active_universe_hash=(
                str(payload["active_universe_hash"])
                if payload.get("active_universe_hash") is not None
                else None
            ),
            active_page_source_snapshot_hash=(
                str(payload["active_page_source_snapshot_hash"])
                if payload.get("active_page_source_snapshot_hash") is not None
                else None
            ),
            completion_identity_hash=str(payload["completion_identity_hash"]),
            contract_identity_hash=str(payload["contract_identity_hash"]),
            atomic_complete=payload["atomic_complete"] is True,
            checkpoint_hash=str(payload["checkpoint_hash"]),
            format_version=str(payload["format_version"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError) as exc:
        raise StageAArtifactConflictError("Stage-A checkpoint is incomplete") from exc
    active_values = (
        checkpoint.active_replay_date,
        checkpoint.active_decision_cutoff,
        checkpoint.active_universe_hash,
        checkpoint.active_page_source_snapshot_hash,
    )
    try:
        for label, value in (
            ("artifact_chain_hash", checkpoint.artifact_chain_hash),
            ("completion_identity_hash", checkpoint.completion_identity_hash),
            ("contract_identity_hash", checkpoint.contract_identity_hash),
            ("checkpoint_hash", checkpoint.checkpoint_hash),
        ):
            _require_hash(label, value)
        if checkpoint.active_universe_hash is not None:
            _require_hash("active_universe_hash", checkpoint.active_universe_hash)
        if checkpoint.active_page_source_snapshot_hash is not None:
            _require_hash(
                "active_page_source_snapshot_hash",
                checkpoint.active_page_source_snapshot_hash,
            )
    except ValueError as exc:
        raise StageAArtifactConflictError("Stage-A checkpoint hash is invalid") from exc
    expected_hash = stable_contract_hash(
        _checkpoint_payload(checkpoint, include_hash=False)
    )
    if (
        checkpoint.format_version != _CHECKPOINT_FORMAT
        or not checkpoint.atomic_complete
        or checkpoint.checkpoint_hash != expected_hash
        or checkpoint.generation < 1
        or checkpoint.artifact_count < 0
        or checkpoint.status not in {"partial", "blocked", "complete"}
        or checkpoint.replay_complete != (checkpoint.status == "complete")
        or (checkpoint.status == "blocked") != bool(checkpoint.reason)
        or any(value is None for value in active_values)
        != all(value is None for value in active_values)
        or (
            checkpoint.active_decision_cutoff is not None
            and (
                checkpoint.active_decision_cutoff.tzinfo is None
                or checkpoint.active_decision_cutoff.utcoffset() is None
            )
        )
    ):
        raise StageAArtifactConflictError("Stage-A checkpoint integrity check failed")
    return checkpoint


def _initialize_stage_a(store: ReplayArtifactStore) -> None:
    with store._connect() as connection:  # noqa: SLF001 - same Strategy Lab store
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ranking_stage_a_features (
                replay_run_key TEXT NOT NULL,
                replay_date TEXT NOT NULL,
                asset_code TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (replay_run_key, replay_date, asset_code)
            );
            CREATE INDEX IF NOT EXISTS ix_ranking_stage_a_features_page
                ON ranking_stage_a_features (replay_run_key, replay_date, asset_code);
            CREATE TABLE IF NOT EXISTS ranking_stage_a_pages (
                replay_run_key TEXT NOT NULL,
                page_key TEXT NOT NULL,
                replay_date TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (replay_run_key, page_key)
            );
            CREATE INDEX IF NOT EXISTS ix_ranking_stage_a_pages_order
                ON ranking_stage_a_pages (replay_run_key, replay_date, page_key);
            CREATE TABLE IF NOT EXISTS ranking_stage_a_checkpoints (
                replay_run_key TEXT NOT NULL PRIMARY KEY,
                generation INTEGER NOT NULL,
                contract_json TEXT NOT NULL,
                checkpoint_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )


def load_stage_a_checkpoint(
    *,
    store: ReplayArtifactStore,
    contract: StageAReplayContract,
) -> StageACheckpoint | None:
    _initialize_stage_a(store)
    with store._connect() as connection:  # noqa: SLF001
        row = connection.execute(
            """
            SELECT replay_run_key, generation, contract_json, checkpoint_hash,
                   payload_json
            FROM ranking_stage_a_checkpoints WHERE replay_run_key=?
            """,
            (contract.replay_run_key,),
        ).fetchone()
    if row is None:
        return None
    expected_contract_json = stable_contract_json(_contract_payload(contract))
    try:
        stored_contract = json.loads(str(row[2]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StageAArtifactConflictError(
            "Stage-A checkpoint contract is malformed"
        ) from exc
    if stable_contract_json(stored_contract) != str(row[2]):
        raise StageAArtifactConflictError(
            "Stage-A checkpoint contract is not canonical"
        )
    if str(row[2]) != expected_contract_json:
        raise NewStageAReplayIdentityRequiredError(
            "Stage-A contract changed; a new replay identity is required"
        )
    checkpoint = _checkpoint_from_json(str(row[4]))
    if (
        str(row[0]) != contract.replay_run_key
        or checkpoint.replay_run_key != str(row[0])
        or int(row[1]) != checkpoint.generation
        or str(row[3]) != checkpoint.checkpoint_hash
    ):
        raise StageAArtifactConflictError(
            "Stage-A checkpoint database metadata mismatch"
        )
    if checkpoint.contract_identity_hash != contract.content_identity_hash:
        raise NewStageAReplayIdentityRequiredError(
            "Stage-A contract changed; a new replay identity is required"
        )
    expected_completion_identity = stable_contract_hash(
        {
            "contract": _contract_content_payload(contract),
            "artifact_count": checkpoint.artifact_count,
            "artifact_chain_hash": checkpoint.artifact_chain_hash,
            "replay_complete": checkpoint.replay_complete,
            "status": checkpoint.status,
            "reason": checkpoint.reason,
        }
    )
    if checkpoint.completion_identity_hash != expected_completion_identity:
        raise StageAArtifactConflictError(
            "Stage-A checkpoint completion identity mismatch"
        )
    return checkpoint


def _score_payload(score: DailyReconstructableScore) -> dict[str, Any]:
    return asdict(score)


def _artifact_content_payload(artifact: StageAFeatureArtifact) -> dict[str, Any]:
    payload = asdict(artifact)
    payload.pop("replay_run_key")
    payload.pop("content_hash")
    return payload


def _page_key(
    *,
    replay_date: date,
    asset_codes: tuple[str, ...],
    page_input_hash: str,
) -> str:
    return stable_contract_hash(
        {
            "replay_date": replay_date,
            "asset_codes": asset_codes,
            "page_input_hash": page_input_hash,
        }
    )


def _page_identity_content_payload(identity: StageAPageIdentity) -> dict[str, Any]:
    content = asdict(identity)
    content.pop("replay_run_key")
    content.pop("page_key")
    content.pop("content_hash")
    return content


def _page_identity(
    page: StageASourcePage,
    *,
    contract: StageAReplayContract,
) -> StageAPageIdentity:
    content = {
        "replay_date": page.replay_date,
        "first_asset_code": page.asset_codes[0],
        "last_asset_code": page.asset_codes[-1],
        "asset_codes": page.asset_codes,
        "decision_cutoff": page.decision_cutoff,
        "score_manifest_hash": contract.score_manifest_hash,
        "schema_version": contract.schema_version,
        "source_snapshot_hash": contract.source_snapshot_hash,
        "page_source_snapshot_hash": page.page_source_snapshot_hash,
        "universe_manifest_hash": contract.universe_manifest_hash,
        "universe_hash": page.universe_hash,
        "page_input_hash": page.page_input_hash,
        "coverage_manifest_hash": page.coverage_manifest_hash,
    }
    return StageAPageIdentity(
        replay_run_key=contract.replay_run_key,
        replay_date=page.replay_date,
        first_asset_code=page.asset_codes[0],
        last_asset_code=page.asset_codes[-1],
        asset_codes=page.asset_codes,
        decision_cutoff=page.decision_cutoff,
        score_manifest_hash=contract.score_manifest_hash,
        schema_version=contract.schema_version,
        source_snapshot_hash=contract.source_snapshot_hash,
        page_source_snapshot_hash=page.page_source_snapshot_hash,
        universe_manifest_hash=contract.universe_manifest_hash,
        universe_hash=page.universe_hash,
        page_input_hash=page.page_input_hash,
        coverage_manifest_hash=page.coverage_manifest_hash,
        page_key=_page_key(
            replay_date=page.replay_date,
            asset_codes=page.asset_codes,
            page_input_hash=page.page_input_hash,
        ),
        content_hash=stable_contract_hash(content),
    )


def _artifact(
    *,
    contract: StageAReplayContract,
    page: StageASourcePage,
    asset_code: str,
    series: PointInTimeAdjustedSeries | None,
    exclusion: ReplayInputExclusion | None,
) -> StageAFeatureArtifact:
    if (series is None) == (exclusion is None):
        raise StageABoundedWorkError(
            "feature unit requires exactly one eligible series or exclusion"
        )
    manifest = daily_reconstructable_manifest()
    score = (
        score_daily_reconstructable(series.bars, provenance=series.provenance)
        if series is not None
        else None
    )
    payload = _score_payload(score) if score is not None else None
    if series is None:
        assert exclusion is not None
        unit_input_hash = stable_contract_hash(
            {
                "replay_date": page.replay_date,
                "asset_code": asset_code,
                "universe_hash": page.universe_hash,
                "exclusion_reason": str(exclusion.reason),
                "exclusion_detail": exclusion.detail,
            }
        )
    else:
        unit_input_hash = series.series_hash
    base: dict[str, Any] = {
        "replay_date": page.replay_date,
        "asset_code": asset_code,
        "decision_cutoff": page.decision_cutoff,
        "score_contract_id": manifest.contract_id,
        "score_manifest_hash": contract.score_manifest_hash,
        "schema_version": contract.schema_version,
        "source_snapshot_hash": contract.source_snapshot_hash,
        "page_source_snapshot_hash": page.page_source_snapshot_hash,
        "universe_manifest_hash": contract.universe_manifest_hash,
        "universe_hash": page.universe_hash,
        "unit_input_hash": unit_input_hash,
        "series_hash": series.series_hash if series is not None else None,
        "score_eligible": score is not None,
        "exclusion_reason": str(exclusion.reason) if exclusion is not None else None,
        "exclusion_detail": exclusion.detail if exclusion is not None else None,
        "score_payload": payload,
    }
    draft = StageAFeatureArtifact(
        replay_run_key=contract.replay_run_key,
        research_score=score.research_score if score is not None else None,
        trend_score=score.trend_score if score is not None else None,
        risk_score=score.risk_score if score is not None else None,
        liquidity_score=score.liquidity_score if score is not None else None,
        content_hash="pending",
        **base,
    )
    return replace(
        draft,
        content_hash=stable_contract_hash(_artifact_content_payload(draft)),
    )


def _validate_page(
    page: StageASourcePage,
    *,
    contract: StageAReplayContract,
) -> int:
    if len(page.asset_codes) > MAX_STAGE_A_ITEMS:
        raise StageABoundedWorkError(
            f"source page exceeds hard item maximum {MAX_STAGE_A_ITEMS}"
        )
    for label, value in (
        ("source_snapshot_hash", page.source_snapshot_hash),
        ("page_source_snapshot_hash", page.page_source_snapshot_hash),
        ("universe_manifest_hash", page.universe_manifest_hash),
        ("universe_hash", page.universe_hash),
        ("page_input_hash", page.page_input_hash),
        ("coverage_manifest_hash", page.coverage_manifest_hash),
    ):
        _require_hash(label, value)
    if (
        page.source_snapshot_hash != contract.source_snapshot_hash
        or page.universe_manifest_hash != contract.universe_manifest_hash
    ):
        raise NewStageAReplayIdentityRequiredError(
            "source identity changed; a new replay identity is required"
        )
    if page.is_last_page and page.has_more_codes:
        raise StageABoundedWorkError(
            "is_last_page cannot be true when has_more_codes is true"
        )
    if page.has_more_codes and page.next_replay_date is not None:
        raise StageABoundedWorkError(
            "a code-continuation page cannot also advance the replay date"
        )
    if (
        page.next_replay_date is not None
        and page.next_replay_date <= page.replay_date
    ):
        raise StageABoundedWorkError("next_replay_date must advance canonically")
    if page.decision_cutoff.tzinfo is None or page.decision_cutoff.utcoffset() is None:
        raise StageABoundedWorkError("decision cutoff must be timezone-aware")
    local_cutoff = page.decision_cutoff.astimezone(_SHANGHAI)
    if (
        local_cutoff.date() != page.replay_date
        or local_cutoff.timetz().replace(tzinfo=None) < wall_time(15, 0)
    ):
        raise StageABoundedWorkError(
            "same-day adjusted close requires an Asia/Shanghai post-close cutoff"
        )
    if (
        not page.asset_codes
        or page.asset_codes != tuple(sorted(set(page.asset_codes)))
        or any(not code.strip() for code in page.asset_codes)
    ):
        raise StageABoundedWorkError("page asset_codes must be sorted, unique and non-empty")
    series_by_code = {item.asset_code: item for item in page.eligible_inputs}
    exclusions_by_code = {
        item.asset_code: item for item in page.exclusions if item.asset_code is not None
    }
    if (
        len(series_by_code) != len(page.eligible_inputs)
        or len(exclusions_by_code) != len(page.exclusions)
        or set(series_by_code) & set(exclusions_by_code)
        or set(page.asset_codes) != set(series_by_code) | set(exclusions_by_code)
    ):
        raise StageABoundedWorkError(
            "each page code must have exactly one eligible series or exclusion"
        )
    source_rows = 0
    for code, series in series_by_code.items():
        timestamps = (
            series.metadata.membership_known_at,
            series.metadata.membership_last_modified_at,
            series.earliest_source_timestamp,
            series.latest_source_timestamp,
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise StageABoundedWorkError(
                f"point-in-time timestamps must be timezone-aware for {code}"
            )
        if (
            series.metadata.eligible_at != page.replay_date
            or not series.bars
            or series.bars[-1].session_date != page.replay_date
            or series.metadata.membership_known_at > page.decision_cutoff
            or series.metadata.membership_last_modified_at > page.decision_cutoff
        ):
            raise StageABoundedWorkError(f"point-in-time identity is invalid for {code}")
        if (
            series.synchronized_after_cutoff
            or series.latest_source_timestamp > page.decision_cutoff
        ):
            raise StageABoundedWorkError(
                f"adjusted series for {code} was known after decision cutoff"
            )
        source_rows += len(series.bars)
    return source_rows


def _validate_active_resume_identity(
    checkpoint: StageACheckpoint | None,
    pages: tuple[StageASourcePage, ...],
) -> None:
    if checkpoint is None or checkpoint.active_replay_date is None:
        return
    active_pages = tuple(
        page for page in pages if page.replay_date == checkpoint.active_replay_date
    )
    if not active_pages:
        raise NewStageAReplayIdentityRequiredError(
            "active replay date changed; a new replay identity is required"
        )
    for page in active_pages:
        if (
            page.decision_cutoff != checkpoint.active_decision_cutoff
            or page.universe_hash != checkpoint.active_universe_hash
            or page.page_source_snapshot_hash
            != checkpoint.active_page_source_snapshot_hash
        ):
            raise NewStageAReplayIdentityRequiredError(
                "active date source identity changed; a new replay identity is required"
            )


def _materialize_pages(
    source_pages: Iterable[StageASourcePage],
    *,
    contract: StageAReplayContract,
    request: StageABatchRequest,
) -> tuple[tuple[StageASourcePage, ...], int]:
    pages: list[StageASourcePage] = []
    source_rows = 0
    for page in source_pages:
        if len(pages) >= request.max_pages:
            raise StageABoundedWorkError("source pages exceed max_pages")
        source_rows += _validate_page(page, contract=contract)
        if source_rows > request.max_source_rows:
            raise StageABoundedWorkError("source rows exceed max_source_rows")
        pages.append(page)
    if not pages:
        raise StageABoundedWorkError("at least one bounded source page is required")
    pages.sort(key=lambda item: (item.replay_date, item.asset_codes[0]))
    if any(page.is_last_page for page in pages[:-1]):
        raise StageABoundedWorkError("only the final canonical page may be is_last_page")
    return tuple(pages), source_rows


def _feature_from_json(raw_json: str) -> StageAFeatureArtifact:
    try:
        payload = json.loads(raw_json)
        return StageAFeatureArtifact(
            replay_run_key=str(payload["replay_run_key"]),
            replay_date=date.fromisoformat(str(payload["replay_date"])),
            asset_code=str(payload["asset_code"]),
            decision_cutoff=datetime.fromisoformat(str(payload["decision_cutoff"])),
            score_contract_id=str(payload["score_contract_id"]),
            score_manifest_hash=str(payload["score_manifest_hash"]),
            schema_version=str(payload["schema_version"]),
            source_snapshot_hash=str(payload["source_snapshot_hash"]),
            page_source_snapshot_hash=str(payload["page_source_snapshot_hash"]),
            universe_manifest_hash=str(payload["universe_manifest_hash"]),
            universe_hash=str(payload["universe_hash"]),
            unit_input_hash=str(payload["unit_input_hash"]),
            series_hash=(
                str(payload["series_hash"]) if payload["series_hash"] else None
            ),
            score_eligible=payload["score_eligible"] is True,
            exclusion_reason=(
                str(payload["exclusion_reason"])
                if payload["exclusion_reason"]
                else None
            ),
            exclusion_detail=(
                str(payload["exclusion_detail"])
                if payload["exclusion_detail"]
                else None
            ),
            research_score=(
                float(payload["research_score"])
                if payload["research_score"] is not None
                else None
            ),
            trend_score=(
                float(payload["trend_score"])
                if payload["trend_score"] is not None
                else None
            ),
            risk_score=(
                float(payload["risk_score"])
                if payload["risk_score"] is not None
                else None
            ),
            liquidity_score=(
                float(payload["liquidity_score"])
                if payload["liquidity_score"] is not None
                else None
            ),
            score_payload=payload["score_payload"],
            content_hash=str(payload["content_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError) as exc:
        raise StageAArtifactConflictError(
            "stored Stage-A feature payload is malformed"
        ) from exc


def _page_identity_from_json(raw_json: str) -> StageAPageIdentity:
    try:
        payload = json.loads(raw_json)
        return StageAPageIdentity(
            replay_run_key=str(payload["replay_run_key"]),
            replay_date=date.fromisoformat(str(payload["replay_date"])),
            first_asset_code=str(payload["first_asset_code"]),
            last_asset_code=str(payload["last_asset_code"]),
            asset_codes=tuple(str(item) for item in payload["asset_codes"]),
            decision_cutoff=datetime.fromisoformat(str(payload["decision_cutoff"])),
            score_manifest_hash=str(payload["score_manifest_hash"]),
            schema_version=str(payload["schema_version"]),
            source_snapshot_hash=str(payload["source_snapshot_hash"]),
            page_source_snapshot_hash=str(payload["page_source_snapshot_hash"]),
            universe_manifest_hash=str(payload["universe_manifest_hash"]),
            universe_hash=str(payload["universe_hash"]),
            page_input_hash=str(payload["page_input_hash"]),
            coverage_manifest_hash=str(payload["coverage_manifest_hash"]),
            page_key=str(payload["page_key"]),
            content_hash=str(payload["content_hash"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError) as exc:
        raise StageAArtifactConflictError(
            "stored Stage-A page identity payload is malformed"
        ) from exc


def read_stage_a_feature_artifacts(
    *,
    store: ReplayArtifactStore,
    replay_run_key: str,
    max_rows: int,
) -> tuple[StageAFeatureArtifact, ...]:
    if max_rows < 1:
        raise StageABoundedWorkError("feature read max_rows must be positive")
    _initialize_stage_a(store)
    with store._connect() as connection:  # noqa: SLF001
        rows = connection.execute(
            """
            SELECT replay_run_key, replay_date, asset_code, content_hash, payload_json
            FROM ranking_stage_a_features
            WHERE replay_run_key=? ORDER BY replay_date, asset_code LIMIT ?
            """,
            (replay_run_key, max_rows + 1),
        ).fetchall()
    if len(rows) > max_rows:
        raise StageABoundedWorkError("feature read exceeds max_rows")
    return _feature_artifacts_from_rows(rows)


def _feature_artifacts_from_rows(
    rows: Iterable[tuple[Any, ...]],
) -> tuple[StageAFeatureArtifact, ...]:
    output: list[StageAFeatureArtifact] = []
    for db_run_key, db_date, db_code, expected_hash, raw_json in rows:
        artifact = _feature_from_json(str(raw_json))
        if (
            artifact.replay_run_key != str(db_run_key)
            or artifact.replay_date.isoformat() != str(db_date)
            or artifact.asset_code != str(db_code)
            or artifact.content_hash != str(expected_hash)
            or artifact.content_hash
            != stable_contract_hash(_artifact_content_payload(artifact))
        ):
            raise StageAArtifactConflictError("stored Stage-A feature hash mismatch")
        output.append(artifact)
    return tuple(output)


def read_stage_a_feature_artifact_page(
    *,
    store: ReplayArtifactStore,
    replay_run_key: str,
    after_cursor: tuple[date, str] | None,
    max_rows: int,
) -> StageAFeatureArtifactPage:
    """Read a bounded Stage-A feature page with a stable date/code keyset cursor."""

    if max_rows < 1 or max_rows > MAX_STAGE_A_ITEMS:
        raise StageABoundedWorkError(
            f"feature page max_rows must be within (0, {MAX_STAGE_A_ITEMS}]"
        )
    if after_cursor is not None and not after_cursor[1].strip():
        raise StageABoundedWorkError("feature page cursor code must be non-empty")
    _initialize_stage_a(store)
    if after_cursor is None:
        where = "replay_run_key=?"
        params: tuple[Any, ...] = (replay_run_key, max_rows + 1)
    else:
        where = (
            "replay_run_key=? AND "
            "(replay_date>? OR (replay_date=? AND asset_code>?))"
        )
        cursor_date = after_cursor[0].isoformat()
        params = (
            replay_run_key,
            cursor_date,
            cursor_date,
            after_cursor[1],
            max_rows + 1,
        )
    with store._connect() as connection:  # noqa: SLF001
        rows = connection.execute(
            f"""
            SELECT replay_run_key, replay_date, asset_code, content_hash, payload_json
            FROM ranking_stage_a_features
            WHERE {where}
            ORDER BY replay_date, asset_code LIMIT ?
            """,
            params,
        ).fetchall()
    has_more = len(rows) > max_rows
    items = _feature_artifacts_from_rows(rows[:max_rows])
    return StageAFeatureArtifactPage(
        items=items,
        has_more=has_more,
        next_cursor=items[-1].cursor if has_more and items else None,
    )


def read_stage_a_page_identities(
    *,
    store: ReplayArtifactStore,
    replay_run_key: str,
    max_rows: int,
) -> tuple[StageAPageIdentity, ...]:
    if max_rows < 1:
        raise StageABoundedWorkError("page identity read max_rows must be positive")
    _initialize_stage_a(store)
    with store._connect() as connection:  # noqa: SLF001
        rows = connection.execute(
            """
            SELECT replay_run_key, page_key, replay_date, content_hash, payload_json
            FROM ranking_stage_a_pages
            WHERE replay_run_key=? ORDER BY replay_date, page_key LIMIT ?
            """,
            (replay_run_key, max_rows + 1),
        ).fetchall()
    if len(rows) > max_rows:
        raise StageABoundedWorkError("page identity read exceeds max_rows")
    output: list[StageAPageIdentity] = []
    for db_run_key, db_page_key, db_date, expected_hash, raw_json in rows:
        identity = _page_identity_from_json(str(raw_json))
        recomputed_page_key = _page_key(
            replay_date=identity.replay_date,
            asset_codes=identity.asset_codes,
            page_input_hash=identity.page_input_hash,
        )
        if (
            identity.replay_run_key != str(db_run_key)
            or identity.replay_date.isoformat() != str(db_date)
            or identity.page_key != str(db_page_key)
            or identity.page_key != recomputed_page_key
            or identity.content_hash != str(expected_hash)
            or identity.content_hash
            != stable_contract_hash(_page_identity_content_payload(identity))
        ):
            raise StageAArtifactConflictError("stored Stage-A page identity hash mismatch")
        output.append(identity)
    return tuple(output)


def _default_peak_rss_reader() -> int:
    from .etf_action_replay.profiling import _peak_rss_bytes

    return _peak_rss_bytes()


def _rss_guard(
    *,
    request: StageABatchRequest,
    peak_rss_reader: Callable[[], int],
) -> Callable[[], int]:
    observed_peak = 0

    def check() -> int:
        nonlocal observed_peak
        current = int(peak_rss_reader())
        if current < 0:
            raise StageABoundedWorkError("peak RSS reader returned a negative value")
        observed_peak = max(observed_peak, current)
        if observed_peak > request.peak_rss_limit_bytes:
            raise StageABoundedWorkError(
                "Stage-A peak RSS exceeds peak_rss_limit_bytes"
            )
        return observed_peak

    return check


def stage_a_source_page_from_snapshot(
    snapshot: PointInTimeRankingInputSnapshot,
    *,
    contract: StageAReplayContract,
    is_last_replay_date: bool,
    next_replay_date: date | None = None,
) -> StageASourcePage:
    """Adapt one bounded loader page without weakening its point-in-time facts."""

    page_codes = set(snapshot.page_asset_codes)
    page_exclusions = tuple(
        item for item in snapshot.exclusions if item.asset_code in page_codes
    )
    return StageASourcePage(
        replay_date=snapshot.replay_date,
        decision_cutoff=snapshot.decision_cutoff,
        source_snapshot_hash=contract.source_snapshot_hash,
        page_source_snapshot_hash=snapshot.source_snapshot_hash,
        universe_manifest_hash=contract.universe_manifest_hash,
        universe_hash=snapshot.universe_hash,
        page_input_hash=snapshot.input_hash,
        coverage_manifest_hash=snapshot.coverage_manifest_hash,
        asset_codes=snapshot.page_asset_codes,
        eligible_inputs=tuple(
            item
            for item in snapshot.eligible_inputs
            if item.asset_code in page_codes
        ),
        exclusions=page_exclusions,
        is_last_page=is_last_replay_date and not snapshot.has_more,
        has_more_codes=snapshot.has_more,
        next_replay_date=(
            next_replay_date if not snapshot.has_more else None
        ),
    )


def _progress_from_checkpoint(
    checkpoint: StageACheckpoint,
    *,
    started: float,
    peak_rss: int,
) -> StageAContinuationProgress:
    return StageAContinuationProgress(
        status=checkpoint.status,
        reason=checkpoint.reason,
        complete=checkpoint.replay_complete,
        processed_items=0,
        source_rows_read=0,
        generation=checkpoint.generation,
        next_cursor=None if checkpoint.replay_complete else checkpoint.cursor,
        artifact_chain_hash=checkpoint.artifact_chain_hash,
        completion_identity_hash=checkpoint.completion_identity_hash,
        elapsed_seconds=max(time.monotonic() - started, 0.0),
        peak_rss_bytes=peak_rss,
        within_peak_rss_limit=True,
    )


def _persist_empty_universe_block(
    *,
    store: ReplayArtifactStore,
    contract: StageAReplayContract,
    checkpoint: StageACheckpoint | None,
    snapshot: PointInTimeRankingInputSnapshot,
    deadline: float,
    check_rss: Callable[[], int],
) -> StageACheckpoint:
    for label, value in (
        ("source_snapshot_hash", snapshot.source_snapshot_hash),
        ("universe_hash", snapshot.universe_hash),
        ("input_hash", snapshot.input_hash),
        ("coverage_manifest_hash", snapshot.coverage_manifest_hash),
    ):
        _require_hash(label, value)
    if (
        snapshot.authoritative_universe
        or snapshot.page_asset_codes
        or snapshot.eligible_inputs
        or snapshot.has_more
    ):
        raise StageABoundedWorkError(
            "empty-universe block requires a truly empty authoritative universe"
        )
    if checkpoint is not None and checkpoint.active_replay_date is not None:
        raise NewStageAReplayIdentityRequiredError(
            "active date became empty; a new replay identity is required"
        )
    active_identity = (
        snapshot.replay_date,
        snapshot.decision_cutoff,
        snapshot.universe_hash,
        snapshot.source_snapshot_hash,
    )
    next_checkpoint = _build_checkpoint(
        contract=contract,
        generation=(checkpoint.generation if checkpoint is not None else 0) + 1,
        cursor=(checkpoint.cursor if checkpoint is not None else (snapshot.replay_date, "")),
        artifact_count=(checkpoint.artifact_count if checkpoint is not None else 0),
        artifact_chain_hash=(
            checkpoint.artifact_chain_hash
            if checkpoint is not None
            else _EMPTY_ARTIFACT_CHAIN_HASH
        ),
        replay_complete=False,
        resume_cursor=(snapshot.replay_date, None),
        active_identity=active_identity,
        status="blocked",
        reason="empty_point_in_time_universe",
    )
    check_rss()
    if time.monotonic() >= deadline:
        raise StageABoundedWorkError("Stage-A empty-universe checkpoint exceeds max_seconds")
    connection = store._connect()  # noqa: SLF001
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT generation, contract_json, checkpoint_hash
            FROM ranking_stage_a_checkpoints WHERE replay_run_key=?
            """,
            (contract.replay_run_key,),
        ).fetchone()
        expected_generation = checkpoint.generation if checkpoint is not None else 0
        actual_generation = int(existing[0]) if existing is not None else 0
        if actual_generation != expected_generation:
            raise StageAArtifactConflictError("stale Stage-A checkpoint generation")
        contract_json = stable_contract_json(_contract_payload(contract))
        if existing is not None and (
            str(existing[1]) != contract_json
            or checkpoint is None
            or str(existing[2]) != checkpoint.checkpoint_hash
        ):
            raise StageAArtifactConflictError("Stage-A checkpoint changed concurrently")
        checkpoint_json = stable_contract_json(
            _checkpoint_payload(next_checkpoint, include_hash=True)
        )
        connection.execute(
            """
            INSERT INTO ranking_stage_a_checkpoints
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
                next_checkpoint.generation,
                contract_json,
                next_checkpoint.checkpoint_hash,
                checkpoint_json,
            ),
        )
        check_rss()
        if time.monotonic() >= deadline:
            raise StageABoundedWorkError(
                "Stage-A empty-universe checkpoint exceeds max_seconds"
            )
        connection.execute("COMMIT")
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()
    return next_checkpoint


async def run_stage_a_loader_job(
    *,
    session: AsyncSession,
    store: ReplayArtifactStore,
    contract: StageAReplayContract,
    request: StageABatchRequest,
    replay_dates: tuple[date, ...],
    decision_cutoffs: tuple[tuple[date, datetime], ...],
    max_codes_per_page: int = MAX_CODES_PER_REPLAY_INPUT_PAGE,
    loader: Callable[..., Awaitable[PointInTimeRankingInputSnapshot]] = (
        load_point_in_time_etf_decision_inputs
    ),
    peak_rss_reader: Callable[[], int] = _default_peak_rss_reader,
) -> StageAContinuationProgress:
    """Load at most one non-empty indexed code page, then commit one Stage-A batch."""

    started = time.monotonic()
    deadline = started + request.max_seconds
    check_rss = _rss_guard(request=request, peak_rss_reader=peak_rss_reader)
    check_rss()
    if (
        not replay_dates
        or replay_dates != tuple(sorted(set(replay_dates)))
        or len(decision_cutoffs) != len(replay_dates)
        or tuple(item[0] for item in decision_cutoffs) != replay_dates
    ):
        raise StageABoundedWorkError(
            "replay_dates and decision_cutoffs must be sorted, unique and complete"
        )
    if not 1 <= max_codes_per_page <= MAX_CODES_PER_REPLAY_INPUT_PAGE:
        raise StageABoundedWorkError(
            f"max_codes_per_page must be within (0, {MAX_CODES_PER_REPLAY_INPUT_PAGE}]"
        )
    effective_max_codes = min(
        max_codes_per_page,
        request.max_items,
        request.max_source_rows // REQUIRED_BAR_COUNT,
    )
    if effective_max_codes < 1:
        raise StageABoundedWorkError("bounded request cannot fit one feature input")
    cutoff_by_date = dict(decision_cutoffs)
    checkpoint = load_stage_a_checkpoint(store=store, contract=contract)
    if checkpoint is not None and checkpoint.active_replay_date is not None:
        if (
            cutoff_by_date.get(checkpoint.active_replay_date)
            != checkpoint.active_decision_cutoff
        ):
            raise NewStageAReplayIdentityRequiredError(
                "active replay cutoff changed; a new replay identity is required"
            )
    if checkpoint is not None and checkpoint.status in {"blocked", "complete"}:
        return _progress_from_checkpoint(
            checkpoint,
            started=started,
            peak_rss=check_rss(),
        )

    if checkpoint is None:
        date_index = 0
        code_after: str | None = None
    else:
        resume_date = checkpoint.resume_date or checkpoint.last_completed_date
        try:
            date_index = replay_dates.index(resume_date)
        except ValueError as exc:
            raise NewStageAReplayIdentityRequiredError(
                "checkpoint date is absent; a new replay identity is required"
            ) from exc
        code_after = (
            checkpoint.resume_code_after
            if checkpoint.resume_date is not None
            else checkpoint.last_completed_code
        )

    if date_index >= len(replay_dates):
        raise NewStageAReplayIdentityRequiredError(
            "checkpoint cursor exceeds replay dates; a new replay identity is required"
        )
    remaining = deadline - time.monotonic()
    commit_reserve = min(_STAGE_A_COMMIT_RESERVE_SECONDS, remaining / 2)
    loader_budget = remaining - commit_reserve
    if loader_budget <= 0:
        raise StageABoundedWorkError("Stage-A loader job has no bounded query budget")
    replay_date = replay_dates[date_index]
    try:
        async with asyncio.timeout(loader_budget):
            snapshot = await loader(
                session,
                replay_date=replay_date,
                decision_cutoff=cutoff_by_date[replay_date],
                max_source_rows=request.max_source_rows,
                code_after=code_after,
                max_codes=effective_max_codes,
            )
    except TimeoutError as exc:
        raise StageABoundedWorkError(
            "Stage-A loader exceeds max_seconds query budget"
        ) from exc
    check_rss()
    if (
        snapshot.replay_date != replay_date
        or snapshot.decision_cutoff != cutoff_by_date[replay_date]
    ):
        raise NewStageAReplayIdentityRequiredError(
            "loader cutoff identity changed; a new replay identity is required"
        )
    if not snapshot.page_asset_codes:
        if snapshot.has_more:
            raise StageABoundedWorkError("loader returned an empty page with has_more")
        if snapshot.authoritative_universe:
            raise NewStageAReplayIdentityRequiredError(
                "loader continuation became empty; a new replay identity is required"
            )
        blocked = _persist_empty_universe_block(
            store=store,
            contract=contract,
            checkpoint=checkpoint,
            snapshot=snapshot,
            deadline=deadline,
            check_rss=check_rss,
        )
        return _progress_from_checkpoint(
            blocked,
            started=started,
            peak_rss=check_rss(),
        )
    page = stage_a_source_page_from_snapshot(
        snapshot,
        contract=contract,
        is_last_replay_date=date_index == len(replay_dates) - 1,
        next_replay_date=(
            replay_dates[date_index + 1]
            if not snapshot.has_more and date_index + 1 < len(replay_dates)
            else None
        ),
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise StageABoundedWorkError("Stage-A loader job has no commit time budget")
    loader_peak = check_rss()
    progress = run_stage_a_continuation(
        store=store,
        contract=contract,
        request=replace(
            request,
            max_pages=1,
            max_seconds=min(remaining, MAX_STAGE_A_SECONDS),
        ),
        source_pages=(page,),
        peak_rss_reader=peak_rss_reader,
    )
    return replace(
        progress,
        peak_rss_bytes=max(loader_peak, progress.peak_rss_bytes),
        within_peak_rss_limit=True,
        elapsed_seconds=max(time.monotonic() - started, 0.0),
    )


def run_stage_a_continuation(
    *,
    store: ReplayArtifactStore,
    contract: StageAReplayContract,
    request: StageABatchRequest,
    source_pages: Iterable[StageASourcePage],
    peak_rss_reader: Callable[[], int] = _default_peak_rss_reader,
) -> StageAContinuationProgress:
    """Score and atomically persist one bounded, one-worker Stage-A continuation."""

    started = time.monotonic()
    deadline = started + request.max_seconds
    check_rss = _rss_guard(request=request, peak_rss_reader=peak_rss_reader)
    check_rss()
    if (
        contract.score_manifest_hash != daily_reconstructable_manifest().manifest_hash
        or contract.schema_version != STAGE_A_SCHEMA_VERSION
        or contract.decision_cutoff_semantics != "asia_shanghai_post_close_v1"
    ):
        raise NewStageAReplayIdentityRequiredError(
            "unsupported Stage-A contract; a new replay identity is required"
        )
    checkpoint = load_stage_a_checkpoint(store=store, contract=contract)
    if checkpoint is not None and checkpoint.status in {"blocked", "complete"}:
        return _progress_from_checkpoint(
            checkpoint,
            started=started,
            peak_rss=check_rss(),
        )

    pages, source_rows = _materialize_pages(
        source_pages,
        contract=contract,
        request=request,
    )
    check_rss()
    _validate_active_resume_identity(checkpoint, pages)
    units: list[
        tuple[
            tuple[date, str],
            StageASourcePage,
            PointInTimeAdjustedSeries | None,
            ReplayInputExclusion | None,
        ]
    ] = []
    seen: set[tuple[date, str]] = set()
    for page in pages:
        check_rss()
        series_by_code = {item.asset_code: item for item in page.eligible_inputs}
        exclusion_by_code = {
            item.asset_code: item for item in page.exclusions if item.asset_code is not None
        }
        for code in page.asset_codes:
            check_rss()
            key = (page.replay_date, code)
            if key in seen:
                raise StageABoundedWorkError(f"duplicate Stage-A source unit: {key}")
            seen.add(key)
            units.append((key, page, series_by_code.get(code), exclusion_by_code.get(code)))
    units.sort(key=lambda item: item[0])
    cursor = checkpoint.cursor if checkpoint is not None else None
    remaining = tuple(item for item in units if cursor is None or item[0] > cursor)

    artifacts: list[StageAFeatureArtifact] = []
    artifact_pages: list[StageASourcePage] = []
    for _, page, series, exclusion in remaining:
        check_rss()
        if len(artifacts) >= request.max_items:
            break
        if time.monotonic() >= deadline:
            if not artifacts:
                raise StageABoundedWorkError("Stage-A scoring exceeds max_seconds")
            break
        if series is None:
            assert exclusion is not None
            asset_code = str(exclusion.asset_code)
        else:
            asset_code = series.asset_code
        artifacts.append(
            _artifact(
                contract=contract,
                page=page,
                asset_code=asset_code,
                series=series,
                exclusion=exclusion,
            )
        )
        artifact_pages.append(page)
        check_rss()
    if not artifacts and remaining:
        raise StageABoundedWorkError("Stage-A made no progress within the bounded request")
    if not artifacts and checkpoint is not None:
        return _progress_from_checkpoint(
            checkpoint,
            started=started,
            peak_rss=check_rss(),
        )

    consumed_all_supplied = len(artifacts) == len(remaining)
    replay_complete = consumed_all_supplied and pages[-1].is_last_page
    if artifacts:
        next_cursor = artifacts[-1].cursor
    elif checkpoint is not None:
        next_cursor = checkpoint.cursor
    else:  # pragma: no cover - page validation guarantees a unit
        raise StageABoundedWorkError("Stage-A source page contains no work unit")
    if replay_complete:
        resume_cursor: tuple[date, str | None] | None = None
    elif not consumed_all_supplied or pages[-1].has_more_codes:
        resume_cursor = next_cursor
    elif pages[-1].next_replay_date is not None:
        resume_cursor = (pages[-1].next_replay_date, None)
    else:
        resume_cursor = next_cursor
    active_page = (
        artifact_pages[-1]
        if resume_cursor is not None
        and resume_cursor[0] == artifact_pages[-1].replay_date
        else None
    )
    active_identity = (
        (
            active_page.replay_date,
            active_page.decision_cutoff,
            active_page.universe_hash,
            active_page.page_source_snapshot_hash,
        )
        if active_page is not None
        else None
    )
    generation = (checkpoint.generation if checkpoint is not None else 0) + 1
    chain_hash = (
        checkpoint.artifact_chain_hash if checkpoint is not None else _EMPTY_ARTIFACT_CHAIN_HASH
    )
    artifact_count = checkpoint.artifact_count if checkpoint is not None else 0
    for artifact in artifacts:
        chain_hash = stable_contract_hash(
            {"previous": chain_hash, "artifact": artifact.content_hash}
        )
        artifact_count += 1
    next_checkpoint = _build_checkpoint(
        contract=contract,
        generation=generation,
        cursor=next_cursor,
        artifact_count=artifact_count,
        artifact_chain_hash=chain_hash,
        replay_complete=replay_complete,
        resume_cursor=resume_cursor,
        active_identity=active_identity,
    )
    page_identities = tuple(
        sorted(
            {
                item.page_key: item
                for item in (
                    _page_identity(page, contract=contract) for page in artifact_pages
                )
            }.values(),
            key=lambda item: (item.replay_date, item.page_key),
        )
    )

    check_rss()
    if time.monotonic() >= deadline:
        raise StageABoundedWorkError("Stage-A commit has no remaining time budget")
    connection = store._connect()  # noqa: SLF001
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT generation, contract_json, checkpoint_hash
            FROM ranking_stage_a_checkpoints
            WHERE replay_run_key=?
            """,
            (contract.replay_run_key,),
        ).fetchone()
        expected_generation = checkpoint.generation if checkpoint is not None else 0
        actual_generation = int(existing[0]) if existing is not None else 0
        if actual_generation != expected_generation:
            raise StageAArtifactConflictError("stale Stage-A checkpoint generation")
        contract_json = stable_contract_json(_contract_payload(contract))
        if existing is not None and str(existing[1]) != contract_json:
            raise NewStageAReplayIdentityRequiredError(
                "Stage-A contract changed; a new replay identity is required"
            )
        if existing is not None and (
            checkpoint is None or str(existing[2]) != checkpoint.checkpoint_hash
        ):
            raise StageAArtifactConflictError("Stage-A checkpoint changed concurrently")
        for identity in page_identities:
            check_rss()
            connection.execute(
                """
                INSERT INTO ranking_stage_a_pages
                    (replay_run_key, page_key, replay_date, content_hash, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (replay_run_key, page_key) DO NOTHING
                """,
                (
                    contract.replay_run_key,
                    identity.page_key,
                    identity.replay_date.isoformat(),
                    identity.content_hash,
                    stable_contract_json(identity),
                ),
            )
            stored = connection.execute(
                """
                SELECT replay_run_key, page_key, replay_date, content_hash, payload_json
                FROM ranking_stage_a_pages
                WHERE replay_run_key=? AND page_key=?
                """,
                (contract.replay_run_key, identity.page_key),
            ).fetchone()
            if stored is None or tuple(str(item) for item in stored) != (
                contract.replay_run_key,
                identity.page_key,
                identity.replay_date.isoformat(),
                identity.content_hash,
                stable_contract_json(identity),
            ):
                raise StageAArtifactConflictError("Stage-A page identity is immutable")
        for artifact in artifacts:
            check_rss()
            raw_json = stable_contract_json(artifact)
            connection.execute(
                """
                INSERT INTO ranking_stage_a_features
                    (replay_run_key, replay_date, asset_code, content_hash, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (replay_run_key, replay_date, asset_code) DO NOTHING
                """,
                (
                    contract.replay_run_key,
                    artifact.replay_date.isoformat(),
                    artifact.asset_code,
                    artifact.content_hash,
                    raw_json,
                ),
            )
            stored = connection.execute(
                """
                SELECT replay_run_key, replay_date, asset_code, content_hash, payload_json
                FROM ranking_stage_a_features
                WHERE replay_run_key=? AND replay_date=? AND asset_code=?
                """,
                (
                    contract.replay_run_key,
                    artifact.replay_date.isoformat(),
                    artifact.asset_code,
                ),
            ).fetchone()
            if stored is None or tuple(str(item) for item in stored) != (
                contract.replay_run_key,
                artifact.replay_date.isoformat(),
                artifact.asset_code,
                artifact.content_hash,
                raw_json,
            ):
                raise StageAArtifactConflictError("Stage-A feature artifact is immutable")
        check_rss()
        if time.monotonic() >= deadline:
            raise StageABoundedWorkError("Stage-A commit exceeds max_seconds")
        checkpoint_json = stable_contract_json(
            _checkpoint_payload(next_checkpoint, include_hash=True)
        )
        connection.execute(
            """
            INSERT INTO ranking_stage_a_checkpoints
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
        if time.monotonic() >= deadline:
            raise StageABoundedWorkError("Stage-A checkpoint exceeds max_seconds")
        peak_rss = check_rss()
        connection.execute("COMMIT")
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()

    return StageAContinuationProgress(
        status="complete" if replay_complete else "partial",
        reason=None,
        complete=replay_complete,
        processed_items=len(artifacts),
        source_rows_read=source_rows,
        generation=generation,
        next_cursor=None if replay_complete else next_cursor,
        artifact_chain_hash=chain_hash,
        completion_identity_hash=next_checkpoint.completion_identity_hash,
        elapsed_seconds=max(time.monotonic() - started, 0.0),
        peak_rss_bytes=peak_rss,
        within_peak_rss_limit=True,
    )
