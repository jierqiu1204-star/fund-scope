from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

from app.services.tracked_positions.lifecycle import stable_contract_hash

from .contracts import MAX_REPLAY_CANDIDATES

FEATURE_SCHEMA_VERSION = "etf-action-feature-v2"
MAX_BATCH_SECONDS = 55.0
TOTAL_RETURN_ADJUSTED = "total_return_adjusted"
_FORBIDDEN_SOURCE_MARKERS = ("sina", "efinance", "raw", "fallback")


class FeatureInputError(ValueError):
    pass


class IncompleteWarmupError(FeatureInputError):
    pass


class BoundedWorkLimitError(ValueError):
    pass


@dataclass(frozen=True)
class AdjustedDailyInput:
    asset_code: str
    session_date: date
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    volume: float
    adjustment_factor: float
    adjusted_data_source: str
    adjustment_kind: str
    decision_eligible: bool
    provider_healthy: bool
    fresh_at_cutoff: bool
    known_at: datetime
    source_cutoff: datetime
    suspended: bool = False
    limit_locked: bool = False
    demonstrably_tradable: bool = False
    delisted: bool = False


@dataclass(frozen=True)
class FeatureRow:
    run_id: str
    feature_contract_hash: str
    input_snapshot_hash: str
    asset_code: str
    session_date: date
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    volume: float
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    momentum_return: float
    score: float
    warmup_sessions: int
    warmup_boundary: date
    warmup_row_hashes: tuple[str, ...]
    warmup_provenance_hash: str
    adjustment_factor: float
    adjusted_data_source: str
    adjustment_kind: str
    decision_eligible: bool
    provider_healthy: bool
    fresh_at_cutoff: bool
    known_at: datetime
    source_cutoff: datetime
    input_row_hash: str
    suspended: bool = False
    limit_locked: bool = False
    demonstrably_tradable: bool = False
    delisted: bool = False
    feature_schema_version: str = FEATURE_SCHEMA_VERSION

    @property
    def cursor_key(self) -> tuple[str, date]:
        return self.asset_code, self.session_date

    @property
    def stable_key(self) -> tuple[str, str, str, str, date]:
        return (
            self.run_id,
            self.feature_contract_hash,
            self.input_snapshot_hash,
            self.asset_code,
            self.session_date,
        )

    @property
    def feature_key(self) -> str:
        identity = {
            "run_id": self.run_id,
            "feature_contract_hash": self.feature_contract_hash,
            "input_snapshot_hash": self.input_snapshot_hash,
            "asset_code": self.asset_code,
            "session_date": self.session_date,
            "feature_schema_version": self.feature_schema_version,
        }
        return stable_contract_hash(identity)

    @property
    def feature_hash(self) -> str:
        return stable_contract_hash(self)


@dataclass(frozen=True)
class FeatureBatchRequest:
    run_id: str
    feature_contract_hash: str
    input_snapshot_hash: str
    asset_codes: tuple[str, ...]
    start_date: date
    end_date: date
    data_cutoff: datetime
    trading_sessions: tuple[date, ...]
    decision_cutoffs: tuple[tuple[date, datetime], ...]
    warmup_sessions: int
    max_source_rows: int
    max_items: int
    max_seconds: float = MAX_BATCH_SECONDS
    worker_count: int = 1
    after_key: tuple[str, date] | None = None

    def __post_init__(self) -> None:
        text_fields = (self.run_id, self.feature_contract_hash, self.input_snapshot_hash)
        if any(not value.strip() for value in text_fields):
            raise BoundedWorkLimitError("run and feature identity fields are required")
        if (
            not self.asset_codes
            or any(not code.strip() for code in self.asset_codes)
            or len(self.asset_codes) != len(set(self.asset_codes))
        ):
            raise BoundedWorkLimitError("asset_codes must be non-empty, non-blank, and unique")
        if self.start_date > self.end_date:
            raise BoundedWorkLimitError("start_date must not exceed end_date")
        if (
            not self.trading_sessions
            or tuple(sorted(set(self.trading_sessions))) != self.trading_sessions
        ):
            raise BoundedWorkLimitError("trading_sessions must be sorted and unique")
        cutoff_dates = tuple(item[0] for item in self.decision_cutoffs)
        if cutoff_dates != self.trading_sessions:
            raise BoundedWorkLimitError(
                "decision_cutoffs must cover every trading session in canonical order"
            )
        if any(
            cutoff.tzinfo is None or cutoff > self.data_cutoff
            for _, cutoff in self.decision_cutoffs
        ):
            raise BoundedWorkLimitError(
                "decision_cutoffs must be timezone-aware and not exceed data_cutoff"
            )
        if self.end_date > self.data_cutoff.date():
            raise BoundedWorkLimitError("batch end exceeds data cutoff")
        if self.warmup_sessions < 1:
            raise BoundedWorkLimitError("warmup_sessions must be positive")
        if self.max_source_rows < 1:
            raise BoundedWorkLimitError("max_source_rows must be positive")
        if self.max_items < 1:
            raise BoundedWorkLimitError("max_items must be positive")
        if not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= MAX_BATCH_SECONDS:
            raise BoundedWorkLimitError("max_seconds must be within (0, 55]")
        if self.worker_count != 1:
            raise BoundedWorkLimitError("worker_count must be 1")


@dataclass(frozen=True)
class FeatureBatchResult:
    run_id: str
    rows: tuple[FeatureRow, ...]
    candidate_ids: tuple[str, ...]
    processed_items: int
    complete: bool
    next_after_key: tuple[str, date] | None
    worker_count: int
    query_count: int
    batch_invocations: int
    elapsed_seconds: float
    source_rows_read: int


def _is_finite_number(value: object, *, positive: bool = False) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and (not positive or float(value) > 0.0)
    )


def _validate_input_row(row: AdjustedDailyInput, request: FeatureBatchRequest) -> None:
    if not row.asset_code.strip():
        raise FeatureInputError("asset code is required")
    for name, value in (
        ("raw_open", row.raw_open),
        ("raw_high", row.raw_high),
        ("raw_low", row.raw_low),
        ("raw_close", row.raw_close),
        ("adjustment_factor", row.adjustment_factor),
    ):
        if not _is_finite_number(value, positive=True):
            raise FeatureInputError(f"{name} must be finite and positive")
    if not _is_finite_number(row.volume) or row.volume < 0:
        raise FeatureInputError("volume must be finite and non-negative")
    if row.raw_high < max(row.raw_open, row.raw_close, row.raw_low):
        raise FeatureInputError("raw_high violates OHLC ordering")
    if row.raw_low > min(row.raw_open, row.raw_close, row.raw_high):
        raise FeatureInputError("raw_low violates OHLC ordering")
    source = row.adjusted_data_source.strip().lower()
    if (
        row.adjustment_kind != TOTAL_RETURN_ADJUSTED
        or not row.decision_eligible
        or not row.provider_healthy
        or not row.fresh_at_cutoff
        or not source
        or any(marker in source for marker in _FORBIDDEN_SOURCE_MARKERS)
    ):
        raise FeatureInputError("decision-grade total-return-adjusted input is required")
    if row.known_at.tzinfo is None or row.source_cutoff.tzinfo is None:
        raise FeatureInputError("provenance timestamps must be timezone-aware")
    if row.known_at > request.data_cutoff:
        raise FeatureInputError("known_at exceeds declared data cutoff")
    if row.source_cutoff > request.data_cutoff:
        raise FeatureInputError("source cutoff exceeds declared data cutoff")


def _bounded_materialize(
    rows: Iterable[AdjustedDailyInput],
    *,
    request: FeatureBatchRequest,
    started: float,
    clock: Callable[[], float],
) -> list[AdjustedDailyInput]:
    materialized: list[AdjustedDailyInput] = []
    for row in rows:
        if len(materialized) >= request.max_source_rows:
            raise BoundedWorkLimitError("source input exceeds max_source_rows")
        if clock() - started >= request.max_seconds:
            raise BoundedWorkLimitError("source input read exceeded max_seconds")
        _validate_input_row(row, request)
        materialized.append(row)
    return materialized


def _target_sessions(request: FeatureBatchRequest) -> tuple[date, ...]:
    sessions = tuple(
        session
        for session in request.trading_sessions
        if request.start_date <= session <= request.end_date
    )
    if not sessions:
        raise FeatureInputError("requested date batch has no declared trading sessions")
    return sessions


def _feature_row(
    *,
    code: str,
    target: date,
    request: FeatureBatchRequest,
    rows_by_key: dict[tuple[str, date], AdjustedDailyInput],
    session_index: dict[date, int],
) -> FeatureRow:
    target_index = session_index[target]
    if target_index < request.warmup_sessions:
        raise IncompleteWarmupError(
            f"incomplete {request.warmup_sessions}-session warmup for {code} at {target}"
        )
    window_dates = request.trading_sessions[
        target_index - request.warmup_sessions : target_index + 1
    ]
    window: list[AdjustedDailyInput] = []
    target_cutoff = dict(request.decision_cutoffs)[target]
    for session in window_dates:
        row = rows_by_key.get((code, session))
        if row is None:
            if session == target:
                raise FeatureInputError(
                    f"requested code {code} has no adjusted target row at {target}"
                )
            raise IncompleteWarmupError(
                f"incomplete warmup for {code} at {target}: missing {session}"
            )
        if row.known_at > target_cutoff or row.source_cutoff > target_cutoff:
            raise FeatureInputError(
                f"warmup provenance for {code} at {session} exceeds "
                f"target decision cutoff {target_cutoff.isoformat()}"
            )
        window.append(row)

    row = window[-1]
    base = window[0]
    adjusted_open = row.raw_open * row.adjustment_factor
    adjusted_high = row.raw_high * row.adjustment_factor
    adjusted_low = row.raw_low * row.adjustment_factor
    adjusted_close = row.raw_close * row.adjustment_factor
    base_adjusted_close = base.raw_close * base.adjustment_factor
    momentum = adjusted_close / base_adjusted_close - 1.0
    row_hashes = tuple(stable_contract_hash(item) for item in window)
    provenance_hash = stable_contract_hash(
        {
            "run_id": request.run_id,
            "feature_contract_hash": request.feature_contract_hash,
            "input_snapshot_hash": request.input_snapshot_hash,
            "asset_code": code,
            "target": target,
            "target_decision_cutoff": target_cutoff,
            "trading_sessions": window_dates,
            "row_hashes": row_hashes,
        }
    )
    return FeatureRow(
        run_id=request.run_id,
        feature_contract_hash=request.feature_contract_hash,
        input_snapshot_hash=request.input_snapshot_hash,
        asset_code=code,
        session_date=target,
        raw_open=row.raw_open,
        raw_high=row.raw_high,
        raw_low=row.raw_low,
        raw_close=row.raw_close,
        volume=row.volume,
        adjusted_open=adjusted_open,
        adjusted_high=adjusted_high,
        adjusted_low=adjusted_low,
        adjusted_close=adjusted_close,
        momentum_return=momentum,
        score=momentum,
        warmup_sessions=request.warmup_sessions,
        warmup_boundary=window_dates[0],
        warmup_row_hashes=row_hashes,
        warmup_provenance_hash=provenance_hash,
        adjustment_factor=row.adjustment_factor,
        adjusted_data_source=row.adjusted_data_source,
        adjustment_kind=row.adjustment_kind,
        decision_eligible=row.decision_eligible,
        provider_healthy=row.provider_healthy,
        fresh_at_cutoff=row.fresh_at_cutoff,
        known_at=row.known_at,
        source_cutoff=row.source_cutoff,
        input_row_hash=row_hashes[-1],
        suspended=row.suspended,
        limit_locked=row.limit_locked,
        demonstrably_tradable=row.demonstrably_tradable,
        delisted=row.delisted,
    )


def compute_feature_batch(
    *,
    source_rows: Iterable[AdjustedDailyInput],
    request: FeatureBatchRequest,
    candidate_ids: Iterable[str],
    clock: Callable[[], float] = time.monotonic,
) -> FeatureBatchResult:
    started = clock()
    candidate_values: list[str] = []
    for candidate in candidate_ids:
        if len(candidate_values) >= MAX_REPLAY_CANDIDATES:
            raise BoundedWorkLimitError(
                f"candidate_ids exceeds registered limit {MAX_REPLAY_CANDIDATES}"
            )
        candidate_values.append(candidate)
    candidates = tuple(sorted(candidate_values))
    if (
        not candidates
        or any(not candidate.strip() for candidate in candidates)
        or len(candidates) != len(set(candidates))
    ):
        raise FeatureInputError("candidate_ids must be non-empty, non-blank, and unique")

    materialized = _bounded_materialize(
        source_rows,
        request=request,
        started=started,
        clock=clock,
    )
    selected_codes = set(request.asset_codes)
    rows_by_key: dict[tuple[str, date], AdjustedDailyInput] = {}
    for row in materialized:
        if row.asset_code not in selected_codes or row.session_date > request.end_date:
            continue
        key = (row.asset_code, row.session_date)
        if key in rows_by_key:
            raise FeatureInputError(f"duplicate adjusted input row: {key}")
        rows_by_key[key] = row

    targets = _target_sessions(request)
    cursor_keys = (
        (code, target)
        for code in sorted(request.asset_codes)
        for target in targets
        if request.after_key is None or (code, target) > request.after_key
    )
    session_index = {
        session: index for index, session in enumerate(request.trading_sessions)
    }
    output: list[FeatureRow] = []
    cursor = iter(cursor_keys)
    stopped_for_time = False
    while len(output) < request.max_items:
        if output and clock() - started >= request.max_seconds:
            stopped_for_time = True
            break
        try:
            code, target = next(cursor)
        except StopIteration:
            break
        output.append(
            _feature_row(
                code=code,
                target=target,
                request=request,
                rows_by_key=rows_by_key,
                session_index=session_index,
            )
        )

    elapsed = max(clock() - started, 0.0)
    sentinel = object()
    complete = not stopped_for_time and next(cursor, sentinel) is sentinel
    next_key = output[-1].cursor_key if output and not complete else None
    return FeatureBatchResult(
        run_id=request.run_id,
        rows=tuple(output),
        candidate_ids=candidates,
        processed_items=len(output),
        complete=complete,
        next_after_key=next_key,
        worker_count=request.worker_count,
        query_count=1,
        batch_invocations=1,
        elapsed_seconds=elapsed,
        source_rows_read=len(materialized),
    )


def merge_feature_rows(*groups: Iterable[FeatureRow]) -> tuple[FeatureRow, ...]:
    merged: dict[str, FeatureRow] = {}
    for group in groups:
        for row in group:
            existing = merged.get(row.feature_key)
            if existing is not None and existing != row:
                raise FeatureInputError(f"conflicting feature row for {row.feature_key}")
            merged[row.feature_key] = row
    return tuple(sorted(merged.values(), key=lambda row: row.stable_key))
