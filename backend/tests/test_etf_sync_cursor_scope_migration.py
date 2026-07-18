from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import EtfSyncCursor

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260717_000049_etf_sync_cursor_scope.py"


def _migration():
    spec = spec_from_file_location("etf_sync_cursor_scope", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_depth_lane_scope_fits_model_and_migration() -> None:
    assert EtfSyncCursor.__table__.c.scope.type.length >= 96

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE etf_sync_cursors ("
                "scope VARCHAR(64) PRIMARY KEY, "
                "last_priority_code VARCHAR(32), "
                "last_regular_code VARCHAR(32), "
                "last_lane VARCHAR(16), "
                "updated_at DATETIME NOT NULL)"
            )
        )
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        migration = _migration()
        migration.op = operations
        migration.upgrade()

        scope_column = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("etf_sync_cursors")
        }["scope"]
        assert scope_column["type"].length >= 96
        long_scope = "history_depth_required:" + "a" * 64
        connection.execute(
            sa.text(
                "INSERT INTO etf_sync_cursors "
                "(scope, last_priority_code, last_regular_code, last_lane, updated_at) "
                "VALUES (:scope, NULL, NULL, NULL, CURRENT_TIMESTAMP)"
            ),
            {"scope": long_scope},
        )

        migration.downgrade()
        downgraded = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("etf_sync_cursors")
        }["scope"]
        assert downgraded["type"].length == 64
        assert (
            connection.scalar(
                sa.text("SELECT count(*) FROM etf_sync_cursors WHERE scope = :scope"),
                {"scope": long_scope},
            )
            == 0
        )


def test_scope_migration_remains_in_the_single_head_chain() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert len(script.get_heads()) == 1
    assert script.get_revision("20260717_000049") is not None
