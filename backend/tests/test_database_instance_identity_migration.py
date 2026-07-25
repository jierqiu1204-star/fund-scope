from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import DatabaseInstanceIdentity

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260717_000050_database_instance_identity.py"


def _migration():
    spec = spec_from_file_location("database_instance_identity", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_database_instance_identity_model_and_migration_are_secret_free() -> None:
    columns = DatabaseInstanceIdentity.__table__.c
    assert set(columns) >= {
        columns.id,
        columns.instance_uuid,
        columns.declared_environment,
        columns.provisioned_at,
        columns.provisioned_by_deploy,
        columns.attestation_key_id,
        columns.creation_metadata_json,
    }
    assert "secret" not in {column.name for column in columns}

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "database_instance_identity" in inspector.get_table_names()
        assert "secret" not in {
            column["name"]
            for column in inspector.get_columns("database_instance_identity")
        }
        migration.downgrade()
        assert "database_instance_identity" not in sa.inspect(connection).get_table_names()


def test_database_identity_revision_remains_in_the_single_head_chain() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert len(script.get_heads()) == 1
    assert script.get_revision("20260717_000050") is not None
