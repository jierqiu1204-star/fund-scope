from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _migration(name: str):
    path = VERSIONS_DIR / name
    spec = spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incremental_checkpoint_migration_is_additive_and_downgrades_fail_closed() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        base = _migration("20260804_000062_dual_universe_leader_tactics_v2.py")
        incremental = _migration("20260809_000063_incremental_leader_checkpoint_items.py")
        base.op = operations
        incremental.op = operations
        base.upgrade()
        incremental.upgrade()

        inspector = sa.inspect(connection)
        assert "leader_tactics_v2_checkpoint_items" in inspector.get_table_names()
        assert "storage_version" in {
            column["name"] for column in inspector.get_columns("leader_tactics_v2_checkpoints")
        }

        connection.execute(
            sa.text(
                """
                INSERT INTO leader_tactics_v2_checkpoints
                    (manifest_hash, cursor, batch_size, completed_count,
                     completed_hashes_json, failed_codes_json, status,
                     updated_at, storage_version)
                VALUES ('manifest-v2', '510001', 5, 1, '[]', '[]',
                        'paused', '2026-08-09 12:00:00', 2)
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO leader_tactics_v2_checkpoint_items
                    (manifest_hash, asset_code, item_state, content_hash,
                     error_message, updated_at)
                VALUES ('manifest-v2', '510001', 'completed', NULL, NULL,
                        '2026-08-09 12:00:00')
                """
            )
        )

        incremental.downgrade()
        row = (
            connection.execute(
                sa.text(
                    """
                SELECT cursor, completed_count, completed_hashes_json, status
                FROM leader_tactics_v2_checkpoints
                WHERE manifest_hash = 'manifest-v2'
                """
                )
            )
            .mappings()
            .one()
        )
        assert dict(row) == {
            "cursor": None,
            "completed_count": 0,
            "completed_hashes_json": "[]",
            "status": "paused",
        }
        inspector = sa.inspect(connection)
        assert "leader_tactics_v2_checkpoint_items" not in inspector.get_table_names()
        assert "storage_version" not in {
            column["name"] for column in inspector.get_columns("leader_tactics_v2_checkpoints")
        }

        base.downgrade()

    engine.dispose()
