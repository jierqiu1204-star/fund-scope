from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import EtfCanonicalPublicationRegistry

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = (
    VERSIONS_DIR / "20260802_000061_etf_canonical_publication_registry.py"
)


def _migration():
    spec = spec_from_file_location("etf_canonical_publication_registry", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_publication_registry_migration_is_additive_and_reversible() -> None:
    columns = EtfCanonicalPublicationRegistry.__table__.c
    assert {
        "research_contract_hash",
        "actionable_contract_hash",
        "readiness_policy_version",
        "universe_snapshot_hash",
        "input_snapshot_hash",
        "data_cutoff",
        "provider_health_seal_hash",
        "surface_group_hash",
        "publication_identity_hash",
        "supersedes_publication_id",
        "is_current",
    } <= {column.name for column in columns}

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        sa.Table(
            "short_research_signal_runs",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
        )
        metadata.create_all(connection)
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "etf_canonical_publication_registry" in inspector.get_table_names()
        indexes = {
            index["name"]
            for index in inspector.get_indexes(
                "etf_canonical_publication_registry"
            )
        }
        assert {
            "ix_etf_canonical_publication_registry_trade_lookup",
            "ix_etf_canonical_publication_registry_supersession",
            "ux_etf_canonical_publication_registry_current_slot",
        } <= indexes
        migration.downgrade()
        assert "etf_canonical_publication_registry" not in sa.inspect(
            connection
        ).get_table_names()


def test_canonical_publication_registry_is_the_single_migration_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260802_000061"]
    assert script.get_revision("20260802_000061").down_revision == "20260802_000060"
