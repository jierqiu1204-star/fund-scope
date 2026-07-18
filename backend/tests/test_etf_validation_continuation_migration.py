from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import (
    EtfValidationContinuation,
    EtfValidationMaterializedSample,
)

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260717_000051_etf_validation_continuation.py"


def _migration():
    spec = spec_from_file_location("etf_validation_continuation", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validation_continuation_models_expose_atomic_checkpoint_and_samples() -> None:
    continuation = EtfValidationContinuation.__table__.c
    assert set(continuation) >= {
        continuation.validation_run_id,
        continuation.identity_hash,
        continuation.manifest_hash,
        continuation.execution_contract_hash,
        continuation.candidate_registry_hash,
        continuation.horizon_set_hash,
        continuation.schema_hash,
        continuation.checkpoint_source_date,
        continuation.checkpoint_horizon,
        continuation.checkpoint_asset_key,
        continuation.processed_sample_count,
        continuation.rolling_aggregate_hash,
        continuation.final_aggregate_hash,
        continuation.lease_token,
        continuation.lease_expires_at,
    }
    sample = EtfValidationMaterializedSample.__table__.c
    assert set(sample) >= {
        sample.continuation_id,
        sample.validation_run_id,
        sample.source_event_id,
        sample.manifest_hash,
        sample.price_basis,
        sample.source_date,
        sample.horizon_sessions,
        sample.asset_key,
        sample.status,
        sample.adjusted_entry_price,
        sample.adjusted_exit_price,
        sample.gross_return,
        sample.net_return,
        sample.total_cost_rate,
        sample.interval_start,
        sample.interval_end,
        sample.sample_hash,
    }


def test_validation_continuation_migration_is_reversible_and_single_head() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        sa.Table(
            "etf_signal_validation_runs",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
        )
        sa.Table(
            "etf_signal_validation_source_events",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
        )
        metadata.create_all(connection)
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "etf_validation_continuations" in inspector.get_table_names()
        assert "etf_validation_materialized_samples" in inspector.get_table_names()
        migration.downgrade()
        tables = sa.inspect(connection).get_table_names()
        assert "etf_validation_continuations" not in tables
        assert "etf_validation_materialized_samples" not in tables

    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    assert ScriptDirectory.from_config(config).get_heads() == ["20260717_000051"]
