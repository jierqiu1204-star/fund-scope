from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.models.entities import EtfPriceHistory, JobRun
from app.services.short_etf.bounded_history_sync import (
    BoundedHistorySyncRequest,
    read_process_rss_bytes,
    run_bounded_history_sync_slice,
)
from app.services.short_etf.data import ProviderFetchResult

SCHEMA = "etf_history_sync_benchmark"
ETF_COUNT = 1_400
SESSION_COUNT = 300
MISSING_FINAL_SESSION_COUNT = 12
SEED_CHUNK_MAX = 200
PHASE_TIMEOUT_SECONDS = 55.0
RSS_LIMIT_BYTES = 768 * 1024 * 1024
CONTRACT_HASH = hashlib.sha256(b"etf-history-benchmark-contract-v1").hexdigest()


def _database_url() -> str:
    url = get_settings().database_url
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("benchmark requires a PostgreSQL asyncpg DATABASE_URL")
    return url


def _asyncpg_url() -> str:
    return _database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def _engine(*, benchmark_schema: bool):
    connect_args: dict[str, Any] = {
        "command_timeout": 50,
        "server_settings": {"statement_timeout": "50000"},
    }
    if benchmark_schema:
        connect_args["server_settings"]["search_path"] = SCHEMA
    return create_async_engine(
        _database_url(),
        future=True,
        connect_args=connect_args,
    )


def _codes() -> tuple[str, ...]:
    return tuple(f"{510000 + index:06d}" for index in range(ETF_COUNT))


def _sessions() -> tuple[date, ...]:
    output: list[date] = []
    cursor = date(2025, 5, 26)
    while len(output) < SESSION_COUNT:
        if cursor.weekday() < 5:
            output.append(cursor)
        cursor += timedelta(days=1)
    return tuple(output)


def _universe_hash(codes: tuple[str, ...]) -> str:
    payload = json.dumps(codes, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _provider_rows() -> tuple[dict[str, float | str], ...]:
    rows: list[dict[str, float | str]] = []
    for index, session_date in enumerate(_sessions()):
        close = 1.0 + index / 10_000
        rows.append(
            {
                "date": session_date.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000_000.0,
                "turnover": 100_000_000.0,
                "pct_change": 0.0,
                "raw_price_basis": "raw_ohlc",
                "research_adjusted_value": close,
                "research_price_basis": "total_return_adjusted",
                "provider_version": "benchmark-hfq-v1",
                "adjustment_version": "benchmark-hfq-v1",
            }
        )
    return tuple(rows)


def _request(*, scope: str, max_codes: int) -> BoundedHistorySyncRequest:
    codes = _codes()
    sessions = _sessions()
    return BoundedHistorySyncRequest(
        scope=scope,
        contract_hash=CONTRACT_HASH,
        universe_hash=_universe_hash(codes),
        eligible_codes=codes,
        from_date=sessions[0],
        to_date=sessions[-1],
        required_sessions=SESSION_COUNT,
        max_codes=max_codes,
        page_size=500,
        max_rows=5_000,
        admission_deadline_seconds=45.0,
        worker_deadline_seconds=55.0,
        process_deadline_seconds=60.0,
        rss_limit_bytes=RSS_LIMIT_BYTES,
        provider_timeout_seconds=8.0,
    )


async def _provision() -> dict[str, Any]:
    if SCHEMA != "etf_history_sync_benchmark":
        raise RuntimeError("refusing to mutate an unexpected schema")
    started = time.monotonic()
    admin_engine = _engine(benchmark_schema=False)
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
            await connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    finally:
        await admin_engine.dispose()

    benchmark_engine = _engine(benchmark_schema=True)
    try:
        async with benchmark_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await benchmark_engine.dispose()
    return {
        "phase": "provision",
        "schema": SCHEMA,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "rss_bytes": read_process_rss_bytes(),
    }


async def _seed(start_index: int, count: int) -> dict[str, Any]:
    if start_index < 0 or count < 1 or count > SEED_CHUNK_MAX:
        raise ValueError("seed range must be positive and count <= 200")
    stop_index = min(ETF_COUNT, start_index + count)
    if start_index >= stop_index:
        raise ValueError("seed range is outside the benchmark universe")
    started = time.monotonic()
    codes = _codes()[start_index:stop_index]
    sessions = _sessions()
    now = datetime.utcnow()
    connection = await asyncpg.connect(
        _asyncpg_url(),
        command_timeout=50,
        server_settings={"search_path": SCHEMA, "statement_timeout": "50000"},
    )
    try:
        async with connection.transaction():
            await connection.execute(
                "DELETE FROM etf_price_history WHERE etf_code = ANY($1::text[])",
                list(codes),
            )
            await connection.execute(
                "DELETE FROM tradable_etfs WHERE code = ANY($1::text[])",
                list(codes),
            )
            etf_records = [
                (
                    code,
                    f"Benchmark ETF {code}",
                    "SH",
                    json.dumps([]),
                    "证券账户 T+1 ETF",
                    "benchmark",
                    True,
                    False,
                    now,
                    now,
                )
                for code in codes
            ]
            await connection.copy_records_to_table(
                "tradable_etfs",
                schema_name=SCHEMA,
                records=etf_records,
                columns=(
                    "code",
                    "name",
                    "exchange",
                    "theme_tags_json",
                    "trading_rule_label",
                    "asset_class",
                    "is_short_term_eligible",
                    "is_watchlist",
                    "created_at",
                    "updated_at",
                ),
            )
            price_records: list[tuple[Any, ...]] = []
            for global_index, code in enumerate(codes, start=start_index):
                code_sessions = (
                    sessions[:-1]
                    if global_index < MISSING_FINAL_SESSION_COUNT
                    else sessions
                )
                for session_index, session_date in enumerate(code_sessions):
                    close = 1.0 + session_index / 10_000
                    price_records.append(
                        (
                            code,
                            session_date,
                            close,
                            close,
                            close,
                            close,
                            1_000_000.0,
                            100_000_000.0,
                            0.0,
                            now,
                            "raw_ohlc",
                            close,
                            "total_return_adjusted",
                            "eastmoney",
                            "benchmark-hfq-v1",
                            now,
                            "benchmark-hfq-v1",
                            True,
                            None,
                        )
                    )
            await connection.copy_records_to_table(
                "etf_price_history",
                schema_name=SCHEMA,
                records=price_records,
                columns=(
                    "etf_code",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "turnover",
                    "pct_change",
                    "created_at",
                    "raw_price_basis",
                    "research_adjusted_value",
                    "research_price_basis",
                    "data_provider",
                    "provider_version",
                    "source_timestamp",
                    "adjustment_version",
                    "decision_eligible",
                    "decision_ineligibility_reason",
                ),
            )
    finally:
        await connection.close()
    return {
        "phase": "seed",
        "start_index": start_index,
        "count": len(codes),
        "price_rows": len(price_records),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "rss_bytes": read_process_rss_bytes(),
    }


async def _shape(*, expected_gap_count: int | None = None) -> dict[str, Any]:
    engine = _engine(benchmark_schema=True)
    try:
        async with engine.connect() as connection:
            etf_count = await connection.scalar(
                text("SELECT count(*) FROM tradable_etfs")
            )
            price_count = await connection.scalar(
                text("SELECT count(*) FROM etf_price_history")
            )
            session_count = await connection.scalar(
                text("SELECT count(DISTINCT trade_date) FROM etf_price_history")
            )
    finally:
        await engine.dispose()
    expected_matrix_rows = ETF_COUNT * SESSION_COUNT
    current_gap_count = expected_matrix_rows - int(price_count or 0)
    valid_shape = (
        etf_count == ETF_COUNT
        and session_count == SESSION_COUNT
        and 0 <= current_gap_count <= MISSING_FINAL_SESSION_COUNT
    )
    if expected_gap_count is not None:
        valid_shape = valid_shape and current_gap_count == expected_gap_count
    if not valid_shape:
        raise RuntimeError(
            "benchmark shape mismatch: "
            f"etfs={etf_count}, prices={price_count}, sessions={session_count}"
        )
    return {
        "phase": "shape",
        "etf_count": etf_count,
        "price_count": price_count,
        "session_count": session_count,
        "expected_matrix_rows": expected_matrix_rows,
        "current_gap_count": current_gap_count,
    }


async def _fetch_complete_history(
    _code: str,
    _from_date: date,
    _to_date: date,
) -> ProviderFetchResult:
    return ProviderFetchResult(
        rows=_provider_rows(),
        provider="eastmoney",
        fallback_used=False,
    )


async def _run_main() -> dict[str, Any]:
    await _shape(expected_gap_count=MISSING_FINAL_SESSION_COUNT)
    engine = _engine(benchmark_schema=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    request = _request(scope=f"history_depth_required:{CONTRACT_HASH}", max_codes=10)
    try:
        async with factory() as session:
            result = await run_bounded_history_sync_slice(
                session,
                request=request,
                fetcher=_fetch_complete_history,
            )
            running_jobs = await session.scalar(
                select(func.count())
                .select_from(JobRun)
                .where(JobRun.status == "running")
            )
    finally:
        await engine.dispose()
    checks = {
        "partial_after_10_codes": result.status == "partial",
        "code_cap": len(result.attempted_codes) <= 10,
        "page_cap": result.max_page_rows <= 500,
        "row_cap": result.fetched_rows <= 5_000,
        "sql_cap": result.max_page_sql_statements <= 8,
        "rss_cap": result.peak_rss_bytes <= RSS_LIMIT_BYTES,
        "worker_deadline": result.elapsed_seconds < request.worker_deadline_seconds,
        "process_deadline": result.elapsed_seconds < request.process_deadline_seconds,
        "no_orphan_job": int(running_jobs or 0) == 0,
    }
    if not all(checks.values()):
        raise RuntimeError(f"main benchmark acceptance failed: {checks}")
    return {
        "phase": "run_main",
        "checks": checks,
        "status": result.status,
        "stop_reason": result.stop_reason,
        "attempted_code_count": len(result.attempted_codes),
        "completed_code_count": len(result.completed_codes),
        "fetched_rows": result.fetched_rows,
        "persisted_rows": result.persisted_rows,
        "max_page_rows": result.max_page_rows,
        "max_page_sql_statements": result.max_page_sql_statements,
        "elapsed_seconds": result.elapsed_seconds,
        "peak_rss_bytes": result.peak_rss_bytes,
        "remaining_candidate_count": (
            result.last_durable_checkpoint or {}
        ).get("remaining_candidate_count"),
    }


async def _run_rollback() -> dict[str, Any]:
    engine = _engine(benchmark_schema=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    request = _request(scope=f"history_depth_benchmark_rollback:{CONTRACT_HASH}", max_codes=1)
    try:
        async with factory() as session:
            before = await session.scalar(select(func.count()).select_from(EtfPriceHistory))
            error: str | None = None
            try:
                await run_bounded_history_sync_slice(
                    session,
                    request=request,
                    fetcher=_fetch_complete_history,
                    before_page_commit=lambda: (_ for _ in ()).throw(
                        RuntimeError("benchmark rollback injection")
                    ),
                )
            except RuntimeError as exc:
                error = str(exc)
            after = await session.scalar(select(func.count()).select_from(EtfPriceHistory))
            latest_job = await session.scalar(
                select(JobRun)
                .where(JobRun.job_name == f"etf_history_continuation:{request.scope}")
                .order_by(JobRun.id.desc())
                .limit(1)
            )
    finally:
        await engine.dispose()
    checks = {
        "injected_error_observed": error == "benchmark rollback injection",
        "row_count_unchanged": before == after,
        "job_not_running": latest_job is not None and latest_job.status == "failed",
        "page_checkpoint_not_advanced": (
            latest_job is not None
            and "last_trade_date" not in (latest_job.details_json or {})
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"rollback benchmark acceptance failed: {checks}")
    return {"phase": "run_rollback", "checks": checks, "row_count": after}


async def _run_cancel() -> dict[str, Any]:
    engine = _engine(benchmark_schema=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    request = _request(scope=f"history_depth_benchmark_cancel:{CONTRACT_HASH}", max_codes=1)
    fetch_started = asyncio.Event()
    fetch_cancelled = False

    async def blocked_fetch(
        _code: str,
        _from_date: date,
        _to_date: date,
    ) -> ProviderFetchResult:
        nonlocal fetch_cancelled
        fetch_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            fetch_cancelled = True

    try:
        async with factory() as session:
            before = await session.scalar(select(func.count()).select_from(EtfPriceHistory))
            task = asyncio.create_task(
                run_bounded_history_sync_slice(
                    session,
                    request=request,
                    fetcher=blocked_fetch,
                )
            )
            await asyncio.wait_for(fetch_started.wait(), timeout=40.0)
            task.cancel()
            cancelled = False
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.CancelledError:
                cancelled = True
            after = await session.scalar(select(func.count()).select_from(EtfPriceHistory))
            latest_job = await session.scalar(
                select(JobRun)
                .where(JobRun.job_name == f"etf_history_continuation:{request.scope}")
                .order_by(JobRun.id.desc())
                .limit(1)
            )
    finally:
        await engine.dispose()
    checks = {
        "worker_cancelled": cancelled,
        "provider_task_cancelled": fetch_cancelled,
        "row_count_unchanged": before == after,
        "job_not_running": latest_job is not None and latest_job.status == "partial",
        "stable_stop_reason": (
            latest_job is not None
            and (latest_job.details_json or {}).get("stop_reason") == "worker_cancelled"
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"cancel benchmark acceptance failed: {checks}")
    return {"phase": "run_cancel", "checks": checks, "row_count": after}


async def _report() -> dict[str, Any]:
    shape = await _shape()
    engine = _engine(benchmark_schema=True)
    try:
        async with engine.connect() as connection:
            jobs = (
                await connection.execute(
                    text(
                        "SELECT job_name, status, details_json "
                        "FROM job_runs "
                        "WHERE job_name LIKE 'etf_history_continuation:%benchmark%' "
                        "OR job_name = :main_job "
                        "ORDER BY id"
                    ),
                    {"main_job": f"etf_history_continuation:history_depth_required:{CONTRACT_HASH}"},
                )
            ).mappings().all()
    finally:
        await engine.dispose()
    return {
        "phase": "report",
        "shape": shape,
        "jobs": [
            {
                "job_name": row["job_name"],
                "status": row["status"],
                "stop_reason": (row["details_json"] or {}).get("stop_reason"),
                "elapsed_seconds": (row["details_json"] or {}).get("elapsed_seconds"),
                "peak_rss_bytes": (row["details_json"] or {}).get("peak_rss_bytes"),
                "fetched_rows": (row["details_json"] or {}).get("fetched_rows"),
                "max_page_sql_statements": (
                    row["details_json"] or {}
                ).get("max_page_sql_statements"),
            }
            for row in jobs
        ],
    }


async def _cleanup() -> dict[str, Any]:
    if SCHEMA != "etf_history_sync_benchmark":
        raise RuntimeError("refusing to drop an unexpected schema")
    engine = _engine(benchmark_schema=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
    finally:
        await engine.dispose()
    return {"phase": "cleanup", "schema": SCHEMA, "dropped": True}


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "provision":
        return await _provision()
    if args.phase == "seed":
        return await _seed(args.start_index, args.count)
    if args.phase == "shape":
        return await _shape()
    if args.phase == "run-main":
        return await _run_main()
    if args.phase == "run-rollback":
        return await _run_rollback()
    if args.phase == "run-cancel":
        return await _run_cancel()
    if args.phase == "report":
        return await _report()
    if args.phase == "cleanup":
        return await _cleanup()
    raise ValueError(f"unsupported phase: {args.phase}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=(
            "provision",
            "seed",
            "shape",
            "run-main",
            "run-rollback",
            "run-cancel",
            "report",
            "cleanup",
        ),
    )
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--count", type=int, default=SEED_CHUNK_MAX)
    args = parser.parse_args()
    result = asyncio.run(
        asyncio.wait_for(_run(args), timeout=PHASE_TIMEOUT_SECONDS)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
