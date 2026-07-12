from __future__ import annotations

from datetime import date, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_NAMES = (
    "20260712_000032_etf_ranking_snapshot_identity.py",
    "20260712_000033_etf_ranking_item_identity.py",
    "20260712_000034_etf_price_research_provenance.py",
    "20260712_000035_etf_universe_memberships.py",
    "20260712_000036_etf_validation_source_identity.py",
)


def _migration(filename: str):
    spec = spec_from_file_location(filename.removesuffix(".py"), VERSIONS_DIR / filename)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("tradable_etfs", metadata, sa.Column("code", sa.String(32), primary_key=True))
    sa.Table("short_research_signal_runs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("short_research_signal_items", metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("run_id", sa.Integer))
    sa.Table("etf_price_history", metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("trade_date", sa.Date, nullable=False), sa.Column("close", sa.Float, nullable=False))
    sa.Table("etf_signal_validation_runs", metadata, sa.Column("id", sa.Integer, primary_key=True))
    metadata.create_all(connection)


def _run_migration(connection: sa.Connection, migration, method: str) -> None:
    migration.op = Operations(MigrationContext.configure(connection))
    getattr(migration, method)()


def test_snapshot_schema_migrations_upgrade_and_downgrade_without_legacy_fabrication() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    migrations = [_migration(filename) for filename in MIGRATION_NAMES]

    with engine.begin() as connection:
        _legacy_schema(connection)
        connection.execute(sa.text("INSERT INTO short_research_signal_runs (id) VALUES (1)"))
        for migration in migrations:
            _run_migration(connection, migration, "upgrade")

        inspector = sa.inspect(connection)
        run_columns = {column["name"]: column for column in inspector.get_columns("short_research_signal_runs")}
        assert run_columns["scope_kind"]["nullable"] is True
        assert run_columns["ranking_contract_hash"]["nullable"] is True
        assert connection.execute(sa.text("SELECT scope_kind, ranking_contract_hash FROM short_research_signal_runs WHERE id = 1")).one() == (None, None)

        run_table = sa.Table("short_research_signal_runs", sa.MetaData(), autoload_with=connection)
        connection.execute(run_table.insert().values(id=2, idempotency_key="same-input"))
        with pytest.raises(IntegrityError):
            connection.execute(run_table.insert().values(id=3, idempotency_key="same-input"))

        connection.execute(sa.text("INSERT INTO tradable_etfs (code) VALUES ('510300')"))
        membership = sa.Table("etf_universe_memberships", sa.MetaData(), autoload_with=connection)
        membership_values = {
            "etf_code": "510300",
            "effective_from": date(2026, 1, 1),
            "source": "fixture",
            "created_at": datetime(2026, 1, 1),
            "updated_at": datetime(2026, 1, 1),
        }
        connection.execute(membership.insert().values(**membership_values))
        with pytest.raises(IntegrityError):
            connection.execute(membership.insert().values(**membership_values))

        index_names = {index["name"] for index in inspector.get_indexes("short_research_signal_runs")}
        assert "ix_short_research_signal_runs_canonical_snapshot" in index_names
        assert "ux_short_research_signal_runs_idempotency_key" in index_names

        for migration in reversed(migrations):
            _run_migration(connection, migration, "downgrade")

        inspector = sa.inspect(connection)
        assert "etf_universe_memberships" not in inspector.get_table_names()
        assert "scope_kind" not in {column["name"] for column in inspector.get_columns("short_research_signal_runs")}
        assert "ranking_score" not in {column["name"] for column in inspector.get_columns("short_research_signal_items")}
