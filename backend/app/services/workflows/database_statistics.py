"""Small, serialized PostgreSQL planner-statistics maintenance slices."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

DATABASE_STATISTICS_ADVISORY_LOCK_KEY = 2_026_081_702
DATABASE_STATISTICS_MAX_TABLES = 4
DATABASE_STATISTICS_TIMEOUT_SECONDS = 45.0
DATABASE_STATISTICS_STATEMENT_TIMEOUT_SECONDS = 8
DATABASE_STATISTICS_LOCK_TIMEOUT_SECONDS = 1


def _bounded_table_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("max_tables must be an integer")
    return max(1, min(DATABASE_STATISTICS_MAX_TABLES, value))


def _quote_table_name(session: AsyncSession, table_name: str) -> str:
    preparer = session.get_bind().dialect.identifier_preparer
    return ".".join(
        (preparer.quote_identifier("public"), preparer.quote_identifier(table_name))
    )


async def _run_postgresql_statistics_slice(
    session: AsyncSession,
    *,
    max_tables: int,
) -> dict[str, Any]:
    await session.execute(
        text(
            "SET LOCAL lock_timeout = "
            f"'{DATABASE_STATISTICS_LOCK_TIMEOUT_SECONDS}s'"
        )
    )
    await session.execute(
        text(
            "SET LOCAL statement_timeout = "
            f"'{DATABASE_STATISTICS_STATEMENT_TIMEOUT_SECONDS}s'"
        )
    )
    lock_acquired = bool(
        await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": DATABASE_STATISTICS_ADVISORY_LOCK_KEY},
        )
    )
    if not lock_acquired:
        await session.rollback()
        return {
            "job_status": "partial",
            "analyzed_count": 0,
            "attempted_tables": [],
            "unavailable_reason": "database_statistics_lock_busy",
        }

    rows = (
        await session.execute(
            text(
                """
                SELECT relname AS table_name,
                       n_live_tup,
                       n_mod_since_analyze,
                       last_analyze,
                       last_autoanalyze
                FROM pg_stat_user_tables
                WHERE schemaname = 'public'
                  AND (
                      (last_analyze IS NULL AND last_autoanalyze IS NULL)
                      OR n_mod_since_analyze >= GREATEST(50, n_live_tup * 0.10)
                  )
                ORDER BY
                    CASE WHEN last_analyze IS NULL AND last_autoanalyze IS NULL
                         THEN 0 ELSE 1 END,
                    n_mod_since_analyze DESC,
                    relname ASC
                LIMIT :candidate_limit
                """
            ),
            {"candidate_limit": max_tables + 1},
        )
    ).mappings().all()

    attempted: list[dict[str, Any]] = []
    analyzed_count = 0
    for row in rows[:max_tables]:
        table_name = str(row["table_name"])
        started = monotonic()
        try:
            async with session.begin_nested():
                await session.execute(
                    text(f"ANALYZE {_quote_table_name(session, table_name)}")
                )
        except SQLAlchemyError as exc:
            attempted.append(
                {
                    "table_name": table_name,
                    "status": "failed",
                    "duration_ms": round((monotonic() - started) * 1_000, 3),
                    "error_type": type(exc).__name__,
                }
            )
        else:
            analyzed_count += 1
            attempted.append(
                {
                    "table_name": table_name,
                    "status": "analyzed",
                    "duration_ms": round((monotonic() - started) * 1_000, 3),
                    "live_rows_estimate": int(row["n_live_tup"] or 0),
                    "modified_rows_estimate": int(row["n_mod_since_analyze"] or 0),
                }
            )
    await session.commit()
    next_table = str(rows[max_tables]["table_name"]) if len(rows) > max_tables else None
    return {
        "job_status": "success" if next_table is None else "partial",
        "analyzed_count": analyzed_count,
        "attempted_tables": attempted,
        "table_limit": max_tables,
        "next_table": next_table,
        "statement_timeout_seconds": DATABASE_STATISTICS_STATEMENT_TIMEOUT_SECONDS,
        "lock_timeout_seconds": DATABASE_STATISTICS_LOCK_TIMEOUT_SECONDS,
        "reclaim_actions": {
            "automatic_blocking_reclaim": False,
            "leader_checkpoint_manual_command": (
                "VACUUM (FULL, ANALYZE) leader_tactics_v2_checkpoints"
            ),
            "partitioning_state": "deferred_until_capacity_verified",
        },
    }


async def bounded_database_statistics_job(
    session: AsyncSession,
    *,
    max_tables: int = DATABASE_STATISTICS_MAX_TABLES,
) -> dict[str, Any]:
    """Analyze a few stale tables without creating an unbounded I/O job."""

    table_limit = _bounded_table_limit(max_tables)
    if session.get_bind().dialect.name != "postgresql":
        return {
            "job_status": "unavailable",
            "analyzed_count": 0,
            "attempted_tables": [],
            "table_limit": table_limit,
            "unavailable_reason": "postgresql_required_for_statistics_maintenance",
        }
    try:
        async with asyncio.timeout(DATABASE_STATISTICS_TIMEOUT_SECONDS):
            return await _run_postgresql_statistics_slice(
                session,
                max_tables=table_limit,
            )
    except TimeoutError:
        await session.rollback()
        return {
            "job_status": "partial",
            "analyzed_count": 0,
            "attempted_tables": [],
            "table_limit": table_limit,
            "unavailable_reason": "database_statistics_slice_timeout",
        }


__all__ = [
    "DATABASE_STATISTICS_MAX_TABLES",
    "bounded_database_statistics_job",
]
