from __future__ import annotations

import inspect
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import SQLAlchemyError

from app.services.intraday_etf.jobs import intraday_etf_cleanup_job
from app.services.intraday_etf.retention_policy import (
    INTRADAY_FULL_DETAIL_RETENTION_TRADING_DAYS,
)
from app.services.intraday_etf.service import summarize_and_cleanup_intraday_quotes
from app.services.workflows.database_statistics import (
    DATABASE_STATISTICS_MAX_TABLES,
    _run_postgresql_statistics_slice,
    bounded_database_statistics_job,
)
from app.services.workflows.etf_intraday_retention import intraday_etf_retention_job

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260817_000071_optimize_database_storage_layout.py"


def _migration():
    spec = spec_from_file_location(MIGRATION_PATH.stem, MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_intraday_full_detail_defaults_share_the_ten_session_policy() -> None:
    assert INTRADAY_FULL_DETAIL_RETENTION_TRADING_DAYS == 10
    for function in (
        summarize_and_cleanup_intraday_quotes,
        intraday_etf_cleanup_job,
        intraday_etf_retention_job,
    ):
        assert (
            inspect.signature(function)
            .parameters["retention_trading_days"]
            .default
            == INTRADAY_FULL_DETAIL_RETENTION_TRADING_DAYS
        )


def test_storage_migration_moves_legacy_progress_and_only_drops_duplicate_index() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    migration = _migration()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE etf_intraday_quotes (
                    id INTEGER PRIMARY KEY,
                    etf_code TEXT NOT NULL,
                    quote_time DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            sa.text(
                "CREATE INDEX ix_etf_intraday_quotes_code_time "
                "ON etf_intraday_quotes (etf_code, quote_time)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_etf_intraday_quote_code_time "
                "ON etf_intraday_quotes (etf_code, quote_time)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE INDEX ix_etf_intraday_quotes_code_time_id "
                "ON etf_intraday_quotes (etf_code, quote_time, id)"
            )
        )
        connection.execute(
            sa.text(
                """
                CREATE TABLE leader_tactics_v2_checkpoints (
                    manifest_hash TEXT PRIMARY KEY,
                    completed_count INTEGER NOT NULL,
                    completed_hashes_json TEXT NOT NULL,
                    failed_codes_json TEXT NOT NULL,
                    updated_at DATETIME NOT NULL,
                    storage_version INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                CREATE TABLE leader_tactics_v2_checkpoint_items (
                    manifest_hash TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    item_state TEXT NOT NULL,
                    content_hash TEXT,
                    error_message TEXT,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (manifest_hash, asset_code)
                )
                """
            )
        )
        completed_payload = json.dumps(
            {
                "schema_version": "dual_universe_leader_tactics_v2_checkpoint_v2",
                "completed_codes": ["000001", "000002"],
                "content_hashes": [["000001", "a" * 64]],
            }
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO leader_tactics_v2_checkpoints
                    (manifest_hash, completed_count, completed_hashes_json,
                     failed_codes_json, updated_at, storage_version)
                VALUES ('manifest', 2, :completed, :failed,
                        '2026-08-17 04:00:00', 1)
                """
            ),
            {
                "completed": completed_payload,
                "failed": json.dumps([["000003", "provider timeout"]]),
            },
        )
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)
        migration.upgrade()

        indexes = {
            item["name"] for item in sa.inspect(connection).get_indexes("etf_intraday_quotes")
        }
        assert "ix_etf_intraday_quotes_code_time" not in indexes
        assert "uq_etf_intraday_quote_code_time" in indexes
        assert "ix_etf_intraday_quotes_code_time_id" in indexes
        checkpoint = connection.execute(
            sa.text(
                """
                SELECT storage_version, completed_hashes_json, failed_codes_json
                FROM leader_tactics_v2_checkpoints
                WHERE manifest_hash = 'manifest'
                """
            )
        ).mappings().one()
        assert dict(checkpoint) == {
            "storage_version": 2,
            "completed_hashes_json": "[]",
            "failed_codes_json": "[]",
        }
        items = connection.execute(
            sa.text(
                """
                SELECT asset_code, item_state, content_hash, error_message
                FROM leader_tactics_v2_checkpoint_items
                ORDER BY asset_code
                """
            )
        ).mappings().all()
        assert [item["asset_code"] for item in items] == ["000001", "000002", "000003"]

        migration.downgrade()
        indexes = {
            item["name"] for item in sa.inspect(connection).get_indexes("etf_intraday_quotes")
        }
        assert "ix_etf_intraday_quotes_code_time" in indexes
        restored = connection.execute(
            sa.text(
                """
                SELECT storage_version, completed_count, completed_hashes_json,
                       failed_codes_json
                FROM leader_tactics_v2_checkpoints
                WHERE manifest_hash = 'manifest'
                """
            )
        ).mappings().one()
        assert restored["storage_version"] == 1
        assert restored["completed_count"] == 2
        assert json.loads(restored["completed_hashes_json"])["completed_codes"] == [
            "000001",
            "000002",
        ]
        assert json.loads(restored["failed_codes_json"]) == [
            ["000003", "provider timeout"]
        ]
    engine.dispose()


def test_storage_migration_rejects_malformed_legacy_progress() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    migration = _migration()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE leader_tactics_v2_checkpoints (
                    manifest_hash TEXT PRIMARY KEY,
                    completed_count INTEGER NOT NULL,
                    completed_hashes_json TEXT NOT NULL,
                    failed_codes_json TEXT NOT NULL,
                    updated_at DATETIME NOT NULL,
                    storage_version INTEGER NOT NULL
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                CREATE TABLE leader_tactics_v2_checkpoint_items (
                    manifest_hash TEXT NOT NULL,
                    asset_code TEXT NOT NULL,
                    item_state TEXT NOT NULL,
                    content_hash TEXT,
                    error_message TEXT,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (manifest_hash, asset_code)
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO leader_tactics_v2_checkpoints
                    (manifest_hash, completed_count, completed_hashes_json,
                     failed_codes_json, updated_at, storage_version)
                VALUES ('broken', 1, '{not-json', '[]',
                        '2026-08-17 04:00:00', 1)
                """
            )
        )
        with pytest.raises(RuntimeError, match="malformed"):
            migration._migrate_legacy_checkpoints(connection)
    engine.dispose()


class _MappedResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _MappedResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _NestedTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakePostgresqlSession:
    def __init__(self, *, fail_table: str | None = None) -> None:
        def quote(value: str) -> str:
            return f'"{value}"'

        self._bind = SimpleNamespace(
            dialect=SimpleNamespace(
                name="postgresql",
                identifier_preparer=SimpleNamespace(quote_identifier=quote),
            )
        )
        self.fail_table = fail_table
        self.analyze_statements: list[str] = []
        self.committed = False

    def get_bind(self) -> Any:
        return self._bind

    async def scalar(self, *_args: object, **_kwargs: object) -> bool:
        return True

    async def execute(self, statement: object, *_args: object, **_kwargs: object) -> Any:
        sql = str(statement)
        if "FROM pg_stat_user_tables" in sql:
            return _MappedResult(
                [
                    {
                        "table_name": f"table_{index}",
                        "n_live_tup": 100,
                        "n_mod_since_analyze": 100,
                        "last_analyze": None,
                        "last_autoanalyze": None,
                    }
                    for index in range(5)
                ]
            )
        if sql.startswith("ANALYZE"):
            self.analyze_statements.append(sql)
            if self.fail_table and self.fail_table in sql:
                raise SQLAlchemyError("bounded failure")
        return _MappedResult([])

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None


@pytest.mark.asyncio
async def test_database_statistics_is_unavailable_outside_postgresql(app) -> None:
    async with app.state.db.session() as session:
        result = await bounded_database_statistics_job(session)
    assert result["job_status"] == "unavailable"
    assert result["unavailable_reason"] == (
        "postgresql_required_for_statistics_maintenance"
    )


@pytest.mark.asyncio
async def test_database_statistics_limits_tables_and_continues_after_one_failure() -> None:
    session = _FakePostgresqlSession(fail_table="table_1")
    result = await _run_postgresql_statistics_slice(
        session,  # type: ignore[arg-type]
        max_tables=DATABASE_STATISTICS_MAX_TABLES,
    )
    assert len(session.analyze_statements) == DATABASE_STATISTICS_MAX_TABLES
    assert result["analyzed_count"] == DATABASE_STATISTICS_MAX_TABLES - 1
    assert result["next_table"] == "table_4"
    assert session.committed is True
    assert all(statement.startswith('ANALYZE "public".') for statement in session.analyze_statements)
