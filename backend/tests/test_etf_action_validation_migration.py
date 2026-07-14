from __future__ import annotations

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260714_000043_etf_action_validation_contract.py"
)


def _migration():
    spec = spec_from_file_location("etf_action_validation_contract_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validation_contract_migration_is_chained_and_enforces_one_run_key() -> None:
    migration = _migration()
    engine = sa.create_engine("sqlite:///:memory:")

    assert migration.revision == "20260714_000043"
    assert migration.down_revision == "20260714_000042"
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "etf_action_validation_runs" in inspector.get_table_names()
        columns = {
            column["name"]
            for column in inspector.get_columns("etf_action_validation_runs")
        }
        assert {
            "candidate_registry_hash",
            "validation_contract_hash",
            "sealed_at",
            "development_outcomes_calculated_at",
            "development_gate_artifact_json",
            "development_gate_artifact_hash",
            "holdout_first_consumed_at",
            "holdout_input_snapshot_hash",
        } <= columns

        table = sa.Table(
            "etf_action_validation_runs",
            sa.MetaData(),
            autoload_with=connection,
        )
        values = {
            "run_key": "frozen-run",
            "policy_version": "policy-v3",
            "candidate_registry_json": {"candidates": []},
            "candidate_registry_hash": "a" * 64,
            "validation_contract_json": {"primary_endpoint": "top20_10d"},
            "validation_contract_hash": "b" * 64,
            "sealed_at": datetime(2026, 7, 14, 9, 0),
        }
        connection.execute(table.insert().values(**values))
        savepoint = connection.begin_nested()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(table.insert().values(**values))
        finally:
            savepoint.rollback()

        migration.downgrade()
        assert "etf_action_validation_runs" not in sa.inspect(connection).get_table_names()
