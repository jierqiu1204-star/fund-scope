"""Bounded, idempotent materialization for ETF validation samples."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from typing import Any

from sqlalchemy import and_, event, literal, or_, select, true, union_all
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfPriceHistory,
    EtfSignalValidationRun,
    EtfSignalValidationSourceEvent,
    EtfValidationContinuation,
    EtfValidationMaterializedSample,
    ShortResearchSignalItem,
    utcnow,
)
from app.services.short_etf.bounded_history_sync import read_process_rss_bytes

MAX_VALIDATION_PAGE_SIZE = 500
MAX_VALIDATION_SAMPLES_PER_SLICE = 5_000
VALIDATION_RSS_LIMIT_BYTES = 768 * 1024 * 1024
VALIDATION_ADMISSION_DEADLINE_SECONDS = 45.0
VALIDATION_WORKER_DEADLINE_SECONDS = 55.0
VALIDATION_PROCESS_DEADLINE_SECONDS = 60.0
MAX_VALIDATION_SQL_STATEMENTS_PER_PAGE = 8

ValidationCheckpoint = tuple[date, int, str]
INITIAL_VALIDATION_CHECKPOINT: ValidationCheckpoint = (date.min, -1, "")
PageLoader = Callable[
    [ValidationCheckpoint, int],
    Awaitable[Sequence["ValidationMaterializedSample"]],
]
Clock = Callable[[], float]
RssReader = Callable[[], int]
PageCommitHook = Callable[[], None]


class ValidationContinuationIdentityError(ValueError):
    pass


class ValidationContinuationBusyError(RuntimeError):
    pass


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


PRODUCTION_EXECUTION_CONTRACT_HASH = _canonical_hash(
    {
        "execution_model": "t_plus_1_adjusted_close_full_horizon_v2",
        "price_basis": "total_return_adjusted",
        "fee_bps_per_side": 5,
        "slippage_bps_per_side": 5,
    }
)
PRODUCTION_CANDIDATE_REGISTRY_HASH = _canonical_hash(
    {
        "primary": "top10_five_session_paired_net_excess",
        "required_paired_dates": 20,
        "minimum_paired_coverage": 0.95,
    }
)
PRODUCTION_SAMPLE_SCHEMA_HASH = _canonical_hash(
    {
        "schema_version": "etf_validation_materialized_sample_v1",
        "checkpoint": ["manifest_hash", "source_date", "horizon", "asset_key"],
    }
)


def _finite_or_none(value: float | None) -> bool:
    return value is None or math.isfinite(value)


@dataclass(frozen=True)
class ValidationContinuationRequest:
    validation_run_id: int
    manifest_hash: str
    execution_contract_hash: str
    candidate_registry_hash: str
    horizon_set: tuple[int, ...]
    schema_hash: str
    page_size: int = MAX_VALIDATION_PAGE_SIZE
    max_samples_per_slice: int = MAX_VALIDATION_SAMPLES_PER_SLICE
    admission_deadline_seconds: float = VALIDATION_ADMISSION_DEADLINE_SECONDS
    worker_deadline_seconds: float = VALIDATION_WORKER_DEADLINE_SECONDS
    process_deadline_seconds: float = VALIDATION_PROCESS_DEADLINE_SECONDS
    rss_limit_bytes: int = VALIDATION_RSS_LIMIT_BYTES

    def __post_init__(self) -> None:
        if self.validation_run_id <= 0:
            raise ValueError("validation_run_id must be positive")
        if not all(
            value
            for value in (
                self.manifest_hash,
                self.execution_contract_hash,
                self.candidate_registry_hash,
                self.schema_hash,
            )
        ):
            raise ValueError("validation continuation identity fields are required")
        if not self.horizon_set or any(horizon <= 0 for horizon in self.horizon_set):
            raise ValueError("horizon_set must contain positive sessions")
        if tuple(sorted(set(self.horizon_set))) != self.horizon_set:
            raise ValueError("horizon_set must be sorted and unique")
        if not 1 <= self.page_size <= MAX_VALIDATION_PAGE_SIZE:
            raise ValueError("page_size must be between 1 and 500")
        if not 1 <= self.max_samples_per_slice <= MAX_VALIDATION_SAMPLES_PER_SLICE:
            raise ValueError("max_samples_per_slice must be between 1 and 5000")
        if not (
            0
            < self.admission_deadline_seconds
            < self.worker_deadline_seconds
            < self.process_deadline_seconds
            <= VALIDATION_PROCESS_DEADLINE_SECONDS
        ):
            raise ValueError("validation deadlines must be ordered and <= 60 seconds")
        if not 0 < self.rss_limit_bytes <= VALIDATION_RSS_LIMIT_BYTES:
            raise ValueError("rss_limit_bytes must be between 1 and 768 MiB")

    @property
    def horizon_set_hash(self) -> str:
        return _canonical_hash(list(self.horizon_set))

    @property
    def identity_hash(self) -> str:
        return _canonical_hash(
            {
                "schema_version": "etf_validation_continuation_v1",
                "validation_run_id": self.validation_run_id,
                "manifest_hash": self.manifest_hash,
                "execution_contract_hash": self.execution_contract_hash,
                "candidate_registry_hash": self.candidate_registry_hash,
                "horizon_set_hash": self.horizon_set_hash,
                "schema_hash": self.schema_hash,
            }
        )


@dataclass(frozen=True)
class ValidationMaterializedSample:
    source_event_id: int
    source_event_hash: str
    source_date: date
    horizon_sessions: int
    asset_key: str
    status: str
    entry_date: date | None
    exit_date: date | None
    adjusted_entry_price: float | None
    adjusted_exit_price: float | None
    gross_return: float | None
    net_return: float | None
    fee_rate: float
    slippage_rate: float
    total_cost_rate: float
    adverse_drawdown: float | None
    interval_start: date | None
    interval_end: date | None
    exclusion_reason: str | None
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.source_event_id <= 0 or not self.source_event_hash or not self.asset_key:
            raise ValueError("sample source event and asset key are required")
        if self.horizon_sessions <= 0:
            raise ValueError("horizon_sessions must be positive")
        if self.status not in {"completed", "pending", "overlapping", "excluded"}:
            raise ValueError("sample status is invalid")
        values = (
            self.adjusted_entry_price,
            self.adjusted_exit_price,
            self.gross_return,
            self.net_return,
            self.fee_rate,
            self.slippage_rate,
            self.total_cost_rate,
            self.adverse_drawdown,
        )
        if not all(_finite_or_none(value) for value in values):
            raise ValueError("sample contains a non-finite numeric value")
        if self.status == "completed" and (
            self.adjusted_entry_price is None
            or self.adjusted_exit_price is None
            or self.gross_return is None
            or self.net_return is None
        ):
            raise ValueError("completed sample requires adjusted prices and returns")

    @property
    def key(self) -> ValidationCheckpoint:
        return (self.source_date, self.horizon_sessions, self.asset_key)

    @property
    def sample_hash(self) -> str:
        return _canonical_hash(
            {
                "source_event_hash": self.source_event_hash,
                "source_date": self.source_date,
                "horizon_sessions": self.horizon_sessions,
                "asset_key": self.asset_key,
                "status": self.status,
                "entry_date": self.entry_date,
                "exit_date": self.exit_date,
                "adjusted_entry_price": self.adjusted_entry_price,
                "adjusted_exit_price": self.adjusted_exit_price,
                "gross_return": self.gross_return,
                "net_return": self.net_return,
                "fee_rate": self.fee_rate,
                "slippage_rate": self.slippage_rate,
                "total_cost_rate": self.total_cost_rate,
                "adverse_drawdown": self.adverse_drawdown,
                "interval_start": self.interval_start,
                "interval_end": self.interval_end,
                "exclusion_reason": self.exclusion_reason,
                "payload": self.payload,
                "price_basis": "total_return_adjusted",
            }
        )


@dataclass(frozen=True)
class ValidationContinuationResult:
    continuation_id: int
    status: str
    stop_reason: str | None
    processed_sample_count: int
    total_sample_count: int
    page_count: int
    checkpoint: ValidationCheckpoint
    final_aggregate_hash: str | None
    elapsed_seconds: float
    peak_rss_bytes: int
    max_page_sql_statements: int


def _checkpoint(continuation: EtfValidationContinuation) -> ValidationCheckpoint:
    if continuation.checkpoint_source_date is None:
        return INITIAL_VALIDATION_CHECKPOINT
    return (
        continuation.checkpoint_source_date,
        int(continuation.checkpoint_horizon or 0),
        continuation.checkpoint_asset_key or "",
    )


def _rolling_hash(previous: str | None, sample_hashes: Sequence[str]) -> str:
    current = previous or hashlib.sha256(b"").hexdigest()
    for sample_hash in sample_hashes:
        current = hashlib.sha256(f"{current}:{sample_hash}".encode()).hexdigest()
    return current


def build_production_validation_page_loader(
    session: AsyncSession,
    *,
    request: ValidationContinuationRequest,
) -> PageLoader:
    horizon_rows = union_all(
        *(
            select(literal(horizon).label("horizon_sessions"))
            for horizon in request.horizon_set
        )
    ).subquery()
    asset_key_expression = literal("etf:") + ShortResearchSignalItem.asset_code

    async def load_page(
        checkpoint: ValidationCheckpoint,
        limit: int,
    ) -> Sequence[ValidationMaterializedSample]:
        candidates = (
            await session.execute(
                select(
                    EtfSignalValidationSourceEvent.id.label("source_event_id"),
                    EtfSignalValidationSourceEvent.immutable_hash.label(
                        "source_event_hash"
                    ),
                    EtfSignalValidationSourceEvent.source_date,
                    horizon_rows.c.horizon_sessions,
                    ShortResearchSignalItem.asset_code,
                    ShortResearchSignalItem.ranking_score,
                    ShortResearchSignalItem.global_rank,
                )
                .select_from(EtfSignalValidationSourceEvent)
                .join(
                    ShortResearchSignalItem,
                    ShortResearchSignalItem.run_id
                    == EtfSignalValidationSourceEvent.source_signal_run_id,
                )
                .join(horizon_rows, true())
                .where(
                    EtfSignalValidationSourceEvent.validation_run_id
                    == request.validation_run_id,
                    EtfSignalValidationSourceEvent.ranking_source_kind
                    == "production_published",
                    ShortResearchSignalItem.asset_type == "etf",
                    ShortResearchSignalItem.score_eligible.is_(True),
                    or_(
                        EtfSignalValidationSourceEvent.source_date > checkpoint[0],
                        and_(
                            EtfSignalValidationSourceEvent.source_date == checkpoint[0],
                            horizon_rows.c.horizon_sessions > checkpoint[1],
                        ),
                        and_(
                            EtfSignalValidationSourceEvent.source_date == checkpoint[0],
                            horizon_rows.c.horizon_sessions == checkpoint[1],
                            asset_key_expression > checkpoint[2],
                        ),
                    ),
                )
                .order_by(
                    EtfSignalValidationSourceEvent.source_date,
                    horizon_rows.c.horizon_sessions,
                    ShortResearchSignalItem.asset_code,
                )
                .limit(limit)
            )
        ).all()
        if not candidates:
            return ()

        bounded_candidates = []
        source_dates: list[date] = []
        for candidate in candidates:
            if candidate.source_date not in source_dates:
                if len(source_dates) >= 4:
                    break
                source_dates.append(candidate.source_date)
            bounded_candidates.append(candidate)

        max_horizon = max(request.horizon_set)
        calendar_dates = tuple(
            (
                await session.scalars(
                    select(EtfPriceHistory.trade_date)
                    .where(
                        or_(
                            *(
                                and_(
                                    EtfPriceHistory.trade_date >= source_date,
                                    EtfPriceHistory.trade_date
                                    <= source_date + timedelta(days=60),
                                )
                                for source_date in source_dates
                            )
                        ),
                        EtfPriceHistory.decision_eligible.is_(True),
                        EtfPriceHistory.research_price_basis
                        == "total_return_adjusted",
                    )
                    .distinct()
                    .order_by(EtfPriceHistory.trade_date)
                )
            ).all()
        )
        calendar_by_source_date = {
            source_date: tuple(
                trade_date
                for trade_date in calendar_dates
                if source_date <= trade_date <= source_date + timedelta(days=60)
            )[: max_horizon + 2]
            for source_date in source_dates
        }
        codes = tuple(
            sorted({str(candidate.asset_code) for candidate in bounded_candidates})
        )
        required_trade_dates = tuple(
            sorted(
                {
                    trade_date
                    for calendar in calendar_by_source_date.values()
                    for trade_date in calendar
                }
            )
        )
        price_by_code_and_date: dict[tuple[str, date], float] = {}
        if codes and required_trade_dates:
            price_rows = (
                await session.execute(
                    select(
                        EtfPriceHistory.etf_code,
                        EtfPriceHistory.trade_date,
                        EtfPriceHistory.research_adjusted_value,
                    ).where(
                        EtfPriceHistory.etf_code.in_(codes),
                        EtfPriceHistory.trade_date.in_(required_trade_dates),
                        EtfPriceHistory.decision_eligible.is_(True),
                        EtfPriceHistory.research_price_basis
                        == "total_return_adjusted",
                    )
                )
            ).all()
            for code, trade_date, adjusted_value in price_rows:
                if adjusted_value is None or not math.isfinite(adjusted_value):
                    continue
                price_by_code_and_date[(str(code), trade_date)] = float(
                    adjusted_value
                )

        samples: list[ValidationMaterializedSample] = []
        for candidate in bounded_candidates:
            source_date = candidate.source_date
            horizon = int(candidate.horizon_sessions)
            code = str(candidate.asset_code)
            calendar = calendar_by_source_date.get(source_date, ())
            price_by_date = {
                trade_date: price_by_code_and_date[(code, trade_date)]
                for trade_date in calendar
                if (code, trade_date) in price_by_code_and_date
            }
            status = "completed"
            exclusion_reason: str | None = None
            entry_date: date | None = None
            exit_date: date | None = None
            entry_price: float | None = None
            exit_price: float | None = None
            gross_return: float | None = None
            net_return: float | None = None
            adverse_drawdown: float | None = None
            if candidate.ranking_score is None or not math.isfinite(
                float(candidate.ranking_score)
            ):
                status = "excluded"
                exclusion_reason = "non_finite_ranking_score"
            elif not calendar or calendar[0] != source_date:
                status = "pending"
                exclusion_reason = "missing_signal_price"
            elif len(calendar) <= horizon + 1:
                status = "pending"
                exclusion_reason = "missing_future_price"
            else:
                entry_date = calendar[1]
                exit_date = calendar[horizon + 1]
                entry_price = price_by_date.get(entry_date)
                exit_price = price_by_date.get(exit_date)
                if entry_price is None:
                    status = "excluded"
                    exclusion_reason = "missing_t_plus_one_entry_price"
                elif exit_price is None:
                    status = "excluded"
                    exclusion_reason = "missing_horizon_exit_price"
                else:
                    path_prices = [
                        price_by_date.get(trade_date)
                        for trade_date in calendar[2 : horizon + 2]
                    ]
                    if any(value is None for value in path_prices):
                        status = "excluded"
                        exclusion_reason = "invalid_window_price"
                    else:
                        gross_return = exit_price / entry_price - 1.0
                        net_return = gross_return - 0.002
                        adverse_drawdown = min(
                            float(value) / entry_price - 1.0 - 0.002
                            for value in path_prices
                            if value is not None
                        )
            samples.append(
                ValidationMaterializedSample(
                    source_event_id=int(candidate.source_event_id),
                    source_event_hash=str(candidate.source_event_hash),
                    source_date=source_date,
                    horizon_sessions=horizon,
                    asset_key=f"etf:{code}",
                    status=status,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    adjusted_entry_price=entry_price,
                    adjusted_exit_price=exit_price,
                    gross_return=gross_return,
                    net_return=net_return,
                    fee_rate=0.0005,
                    slippage_rate=0.0005,
                    total_cost_rate=0.002,
                    adverse_drawdown=adverse_drawdown,
                    interval_start=entry_date,
                    interval_end=exit_date,
                    exclusion_reason=exclusion_reason,
                    payload={
                        "global_rank": candidate.global_rank,
                        "ranking_score": candidate.ranking_score,
                        "execution_model": (
                            "t_plus_1_adjusted_close_full_horizon_v2"
                        ),
                    },
                )
            )
        return samples

    return load_page


async def _load_or_create_continuation(
    session: AsyncSession,
    request: ValidationContinuationRequest,
) -> EtfValidationContinuation:
    run = await session.get(EtfSignalValidationRun, request.validation_run_id)
    if run is None:
        raise ValidationContinuationIdentityError("validation run does not exist")
    if run.ranking_source_kind != "production_published":
        raise ValidationContinuationIdentityError(
            "validation continuation requires production_published source events"
        )
    if run.source_manifest_hash != request.manifest_hash:
        raise ValidationContinuationIdentityError(
            "manifest mismatch requires a new validation identity"
        )
    continuation = await session.scalar(
        select(EtfValidationContinuation).where(
            EtfValidationContinuation.validation_run_id == request.validation_run_id
        )
    )
    if continuation is not None:
        if continuation.identity_hash != request.identity_hash:
            raise ValidationContinuationIdentityError(
                "continuation contract mismatch requires a new validation identity"
            )
        return continuation
    continuation = EtfValidationContinuation(
        validation_run_id=request.validation_run_id,
        identity_hash=request.identity_hash,
        manifest_hash=request.manifest_hash,
        execution_contract_hash=request.execution_contract_hash,
        candidate_registry_hash=request.candidate_registry_hash,
        horizon_set_hash=request.horizon_set_hash,
        schema_hash=request.schema_hash,
        status="partial",
        processed_sample_count=0,
        page_count=0,
        details_json={},
    )
    session.add(continuation)
    await session.commit()
    await session.refresh(continuation)
    return continuation


async def _persist_page(
    session: AsyncSession,
    *,
    continuation: EtfValidationContinuation,
    request: ValidationContinuationRequest,
    page: Sequence[ValidationMaterializedSample],
    before_page_commit: PageCommitHook | None,
    statement_count: list[int],
) -> int:
    source_ids = {sample.source_event_id for sample in page}
    connection = await session.connection()
    source_events = (
        await session.scalars(
            select(EtfSignalValidationSourceEvent).where(
                EtfSignalValidationSourceEvent.id.in_(source_ids),
                EtfSignalValidationSourceEvent.validation_run_id
                == request.validation_run_id,
                EtfSignalValidationSourceEvent.ranking_source_kind
                == "production_published",
            )
        )
    ).all()
    source_by_id = {source.id: source for source in source_events}
    if set(source_by_id) != source_ids:
        raise ValidationContinuationIdentityError(
            "sample source event does not belong to the registered validation manifest"
        )
    for sample in page:
        if source_by_id[sample.source_event_id].immutable_hash != sample.source_event_hash:
            raise ValidationContinuationIdentityError(
                "sample source event hash does not match the immutable manifest event"
            )
    payloads = [
        {
            "continuation_id": continuation.id,
            "validation_run_id": request.validation_run_id,
            "source_event_id": sample.source_event_id,
            "source_event_hash": sample.source_event_hash,
            "manifest_hash": request.manifest_hash,
            "price_basis": "total_return_adjusted",
            "source_date": sample.source_date,
            "horizon_sessions": sample.horizon_sessions,
            "asset_key": sample.asset_key,
            "status": sample.status,
            "entry_date": sample.entry_date,
            "exit_date": sample.exit_date,
            "adjusted_entry_price": sample.adjusted_entry_price,
            "adjusted_exit_price": sample.adjusted_exit_price,
            "gross_return": sample.gross_return,
            "net_return": sample.net_return,
            "fee_rate": sample.fee_rate,
            "slippage_rate": sample.slippage_rate,
            "total_cost_rate": sample.total_cost_rate,
            "adverse_drawdown": sample.adverse_drawdown,
            "interval_start": sample.interval_start,
            "interval_end": sample.interval_end,
            "exclusion_reason": sample.exclusion_reason,
            "sample_hash": sample.sample_hash,
            "payload_json": dict(sample.payload),
            "created_at": utcnow(),
        }
        for sample in page
    ]
    dialect_name = connection.dialect.name
    if dialect_name == "postgresql":
        statement = postgresql_insert(EtfValidationMaterializedSample).values(payloads)
    elif dialect_name == "sqlite":
        statement = sqlite_insert(EtfValidationMaterializedSample).values(payloads)
    else:
        raise RuntimeError(
            f"unsupported validation continuation dialect: {dialect_name}"
        )
    statement = statement.on_conflict_do_nothing(
        index_elements=(
            "continuation_id",
            "source_date",
            "horizon_sessions",
            "asset_key",
        )
    )
    result = await session.execute(statement)
    inserted = int(result.rowcount or 0)
    if inserted != len(page):
        raise ValidationContinuationIdentityError(
            "checkpoint/sample inconsistency requires a new validation identity"
        )
    checkpoint = page[-1].key
    continuation.checkpoint_source_date = checkpoint[0]
    continuation.checkpoint_horizon = checkpoint[1]
    continuation.checkpoint_asset_key = checkpoint[2]
    continuation.processed_sample_count += inserted
    continuation.page_count += 1
    continuation.rolling_aggregate_hash = _rolling_hash(
        continuation.rolling_aggregate_hash,
        [sample.sample_hash for sample in page],
    )
    continuation.updated_at = utcnow()
    continuation.details_json = {
        **dict(continuation.details_json or {}),
        "last_page_rows": inserted,
        "last_page_sql_statements": statement_count[0] + 1,
    }
    if before_page_commit is not None:
        before_page_commit()
    await session.commit()
    return inserted


async def run_validation_continuation_slice(
    session: AsyncSession,
    *,
    request: ValidationContinuationRequest,
    page_loader: PageLoader,
    monotonic: Clock = time.monotonic,
    rss_bytes: RssReader = read_process_rss_bytes,
    before_page_commit: PageCommitHook | None = None,
) -> ValidationContinuationResult:
    started = monotonic()
    checkpoint_reserve_seconds = (
        request.process_deadline_seconds - request.worker_deadline_seconds
    )
    process_deadline_at = started + request.process_deadline_seconds
    admission_deadline_at = started + request.admission_deadline_seconds
    checkpoint_deadline_at = (
        started
        + request.worker_deadline_seconds
        - checkpoint_reserve_seconds
    )

    async def run_before(
        operation: Callable[[], Awaitable[Any]],
        *,
        deadline_at: float,
        timeout_message: str,
    ) -> Any:
        remaining = deadline_at - monotonic()
        if remaining <= 0:
            raise TimeoutError(timeout_message)
        try:
            return await asyncio.wait_for(operation(), timeout=remaining)
        except TimeoutError as exc:
            raise TimeoutError(timeout_message) from exc

    async def rollback_before_process_deadline() -> None:
        await run_before(
            session.rollback,
            deadline_at=process_deadline_at,
            timeout_message="validation process deadline exhausted during rollback",
        )

    async def invalidate_before_process_deadline() -> None:
        remaining = process_deadline_at - monotonic()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(session.invalidate(), timeout=remaining)
        except Exception:
            return

    continuation_id: int | None = None
    try:
        continuation = await run_before(
            lambda: _load_or_create_continuation(session, request),
            deadline_at=checkpoint_deadline_at,
            timeout_message=(
                "validation process deadline exhausted during continuation setup"
            ),
        )
        continuation_id = continuation.id
        continuation = await run_before(
            lambda: session.scalar(
                select(EtfValidationContinuation)
                .where(EtfValidationContinuation.id == continuation_id)
                .with_for_update()
            ),
            deadline_at=checkpoint_deadline_at,
            timeout_message="validation process deadline exhausted during lease query",
        )
        if continuation is None:
            raise RuntimeError(
                "validation continuation disappeared before lease acquisition"
            )
        if continuation.status == "complete":
            return ValidationContinuationResult(
                continuation_id=continuation.id,
                status="complete",
                stop_reason=None,
                processed_sample_count=0,
                total_sample_count=continuation.processed_sample_count,
                page_count=0,
                checkpoint=_checkpoint(continuation),
                final_aggregate_hash=continuation.final_aggregate_hash,
                elapsed_seconds=max(0.0, monotonic() - started),
                peak_rss_bytes=max(0, int(rss_bytes())),
                max_page_sql_statements=0,
            )

        now = utcnow()
        if (
            continuation.lease_expires_at is not None
            and continuation.lease_expires_at > now
        ):
            raise ValidationContinuationBusyError(
                "validation continuation worker lease is active"
            )
        continuation.lease_token = uuid.uuid4().hex
        continuation.lease_expires_at = now + timedelta(
            seconds=request.worker_deadline_seconds
        )
        continuation.status = "running"
        continuation.updated_at = now
        await run_before(
            session.commit,
            deadline_at=checkpoint_deadline_at,
            timeout_message="validation process deadline exhausted during lease commit",
        )
    except TimeoutError:
        try:
            await rollback_before_process_deadline()
        except Exception:
            await invalidate_before_process_deadline()
        raise

    checkpoint = _checkpoint(continuation)
    processed = 0
    pages = 0
    peak_rss = max(0, int(rss_bytes()))
    max_page_sql = 0
    stop_reason: str | None = None
    try:
        while True:
            now_monotonic = monotonic()
            peak_rss = max(peak_rss, max(0, int(rss_bytes())))
            if now_monotonic >= admission_deadline_at:
                stop_reason = "admission_deadline"
                break
            if peak_rss > request.rss_limit_bytes:
                stop_reason = "rss_limit"
                break
            if processed >= request.max_samples_per_slice:
                stop_reason = "sample_limit"
                break
            page_deadline_at = min(
                admission_deadline_at,
                checkpoint_deadline_at,
            )
            if page_deadline_at <= now_monotonic:
                stop_reason = "worker_deadline"
                break

            connection = await run_before(
                session.connection,
                deadline_at=page_deadline_at,
                timeout_message="validation worker deadline exhausted before page load",
            )
            statement_count = [0]

            def count_statement(
                *_args: Any,
                _counter: list[int] = statement_count,
            ) -> None:
                _counter[0] += 1
                if _counter[0] > MAX_VALIDATION_SQL_STATEMENTS_PER_PAGE:
                    raise RuntimeError(
                        "validation page exceeded eight SQL statements"
                    )

            event.listen(
                connection.sync_connection,
                "before_cursor_execute",
                count_statement,
            )
            try:
                page = list(
                    await run_before(
                        partial(page_loader, checkpoint, request.page_size),
                        deadline_at=page_deadline_at,
                        timeout_message="validation worker deadline exhausted in page loader",
                    )
                )
                if monotonic() >= checkpoint_deadline_at:
                    stop_reason = "worker_deadline"
                    break
                if not page:
                    continuation.status = "complete"
                    continuation.final_aggregate_hash = (
                        continuation.rolling_aggregate_hash
                        or hashlib.sha256(b"").hexdigest()
                    )
                    continuation.finished_at = utcnow()
                    continuation.lease_token = None
                    continuation.lease_expires_at = None
                    continuation.updated_at = utcnow()
                    continuation.details_json = {
                        **dict(continuation.details_json or {}),
                        "stop_reason": "complete",
                        "peak_rss_bytes": peak_rss,
                        "elapsed_seconds": max(0.0, monotonic() - started),
                    }
                    await run_before(
                        session.commit,
                        deadline_at=checkpoint_deadline_at,
                        timeout_message=(
                            "validation worker deadline exhausted during final commit"
                        ),
                    )
                    stop_reason = None
                    break
                if len(page) > request.page_size:
                    raise ValueError(
                        "page_loader returned more than the requested page size"
                    )
                keys = [sample.key for sample in page]
                if keys != sorted(keys) or len(set(keys)) != len(keys):
                    raise ValueError(
                        "validation page must be strictly sorted and unique"
                    )
                if keys[0] <= checkpoint:
                    raise ValueError(
                        "validation page must start after the durable checkpoint"
                    )
                if any(
                    sample.horizon_sessions not in request.horizon_set
                    for sample in page
                ):
                    raise ValueError(
                        "validation page contains an unregistered horizon"
                    )
                remaining_samples = request.max_samples_per_slice - processed
                if len(page) > remaining_samples:
                    page = page[:remaining_samples]
                inserted = await run_before(
                    partial(
                        _persist_page,
                        session,
                        continuation=continuation,
                        request=request,
                        page=tuple(page),
                        before_page_commit=before_page_commit,
                        statement_count=statement_count,
                    ),
                    deadline_at=checkpoint_deadline_at,
                    timeout_message=(
                        "validation worker deadline exhausted during page checkpoint"
                    ),
                )
                page_sql = statement_count[0]
            finally:
                event.remove(
                    connection.sync_connection,
                    "before_cursor_execute",
                    count_statement,
                )
            processed += inserted
            pages += 1
            max_page_sql = max(max_page_sql, page_sql)
            checkpoint = _checkpoint(continuation)
    except TimeoutError as exc:
        try:
            await rollback_before_process_deadline()
            continuation = await run_before(
                lambda: session.get(
                    EtfValidationContinuation,
                    continuation_id,
                ),
                deadline_at=process_deadline_at,
                timeout_message=(
                    "validation process deadline exhausted reloading checkpoint"
                ),
            )
        except Exception:
            await invalidate_before_process_deadline()
            raise
        if continuation is None:
            raise RuntimeError(
                "validation continuation disappeared after timeout"
            ) from exc
        stop_reason = "worker_deadline"
    except Exception:
        try:
            await rollback_before_process_deadline()
            continuation = await run_before(
                lambda: session.get(
                    EtfValidationContinuation,
                    continuation_id,
                ),
                deadline_at=process_deadline_at,
                timeout_message=(
                    "validation process deadline exhausted reloading failed checkpoint"
                ),
            )
            if continuation is not None and continuation.status != "complete":
                continuation.status = "partial"
                continuation.lease_token = None
                continuation.lease_expires_at = None
                continuation.updated_at = utcnow()
                await run_before(
                    session.commit,
                    deadline_at=process_deadline_at,
                    timeout_message=(
                        "validation process deadline exhausted clearing failed lease"
                    ),
                )
        except Exception:
            await invalidate_before_process_deadline()
        raise

    if continuation.status != "complete":
        continuation.status = "partial"
        continuation.lease_token = None
        continuation.lease_expires_at = None
        continuation.updated_at = utcnow()
        continuation.details_json = {
            **dict(continuation.details_json or {}),
            "stop_reason": stop_reason,
            "peak_rss_bytes": peak_rss,
            "elapsed_seconds": max(0.0, monotonic() - started),
            "max_page_sql_statements": max_page_sql,
        }
        await run_before(
            session.commit,
            deadline_at=process_deadline_at,
            timeout_message="validation process deadline exhausted during checkpoint",
        )

    return ValidationContinuationResult(
        continuation_id=continuation.id,
        status=continuation.status,
        stop_reason=stop_reason,
        processed_sample_count=processed,
        total_sample_count=continuation.processed_sample_count,
        page_count=pages,
        checkpoint=_checkpoint(continuation),
        final_aggregate_hash=continuation.final_aggregate_hash,
        elapsed_seconds=max(0.0, monotonic() - started),
        peak_rss_bytes=peak_rss,
        max_page_sql_statements=max_page_sql,
    )


async def continue_registered_production_validation(
    session: AsyncSession,
    *,
    validation_run_id: int,
    horizons: tuple[int, ...] = (1, 3, 5, 10),
) -> ValidationContinuationResult:
    run = await session.get(EtfSignalValidationRun, validation_run_id)
    if run is None or not run.source_manifest_hash:
        raise ValidationContinuationIdentityError(
            "registered production validation run and manifest are required"
        )
    request = ValidationContinuationRequest(
        validation_run_id=run.id,
        manifest_hash=run.source_manifest_hash,
        execution_contract_hash=PRODUCTION_EXECUTION_CONTRACT_HASH,
        candidate_registry_hash=PRODUCTION_CANDIDATE_REGISTRY_HASH,
        horizon_set=tuple(sorted(set(horizons))),
        schema_hash=PRODUCTION_SAMPLE_SCHEMA_HASH,
    )
    return await run_validation_continuation_slice(
        session,
        request=request,
        page_loader=build_production_validation_page_loader(
            session,
            request=request,
        ),
    )


__all__ = [
    "MAX_VALIDATION_PAGE_SIZE",
    "MAX_VALIDATION_SAMPLES_PER_SLICE",
    "PRODUCTION_CANDIDATE_REGISTRY_HASH",
    "PRODUCTION_EXECUTION_CONTRACT_HASH",
    "PRODUCTION_SAMPLE_SCHEMA_HASH",
    "ValidationContinuationBusyError",
    "ValidationContinuationIdentityError",
    "ValidationContinuationRequest",
    "ValidationContinuationResult",
    "ValidationMaterializedSample",
    "build_production_validation_page_loader",
    "continue_registered_production_validation",
    "run_validation_continuation_slice",
]
