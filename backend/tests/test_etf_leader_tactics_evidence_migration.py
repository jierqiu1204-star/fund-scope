from __future__ import annotations

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import EtfFactorExperimentEvidence

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260731_000058_etf_leader_tactics_evidence_family.py"


def _migration():
    spec = spec_from_file_location("etf_leader_evidence_family", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_leader_evidence_family_migration_is_nullable_indexed_and_reversible() -> None:
    columns = EtfFactorExperimentEvidence.__table__.c
    assert columns.experiment_family.nullable is True
    assert columns.hypothesis_registry_hash.nullable is True
    index_names = {item.name for item in EtfFactorExperimentEvidence.__table__.indexes}
    assert {
        "ix_etf_factor_evidence_family_latest",
        "ix_etf_factor_evidence_hypothesis_registry",
    } <= index_names

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        table = sa.Table(
            "etf_factor_experiment_evidence",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
        metadata.create_all(connection)
        connection.execute(table.insert().values(id=1, created_at=datetime(2026, 7, 31, 12)))
        migration = _migration()
        assert migration.revision == "20260731_000058"
        assert migration.down_revision == "20260729_000057"
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        migrated_columns = {item["name"]: item for item in inspector.get_columns(table.name)}
        assert migrated_columns["experiment_family"]["nullable"] is True
        assert migrated_columns["hypothesis_registry_hash"]["nullable"] is True
        assert {item["name"] for item in inspector.get_indexes(table.name)} == {
            "ix_etf_factor_evidence_family_latest",
            "ix_etf_factor_evidence_hypothesis_registry",
        }
        row = connection.execute(
            sa.select(
                sa.column("id"),
                sa.column("experiment_family"),
                sa.column("hypothesis_registry_hash"),
            ).select_from(sa.table(table.name))
        ).one()
        assert row == (1, None, None)

        migration.downgrade()
        remaining = {item["name"] for item in sa.inspect(connection).get_columns(table.name)}
        assert remaining == {"id", "created_at"}


def test_leader_evidence_migration_is_the_single_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260804_000062"]
    assert script.get_revision("20260804_000062").down_revision == "20260802_000061"
