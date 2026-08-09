from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260804_000062_dual_universe_leader_tactics_v2.py"


def _migration():
    spec = spec_from_file_location(MIGRATION_PATH.stem, MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v2_migration_is_the_linear_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260810_000064"]
    revision = script.get_revision("20260809_000063")
    assert revision is not None
    assert revision.down_revision == "20260804_000062"


def test_v2_migration_preserves_same_day_universe_revisions_and_downgrades() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        table = "ashare_research_universe_snapshots"
        inspector = sa.inspect(connection)
        assert table in inspector.get_table_names()
        assert "ix_ashare_research_universe_pit_lookup" in {
            item["name"] for item in inspector.get_indexes(table)
        }

        insert = sa.text(
            """
            INSERT INTO ashare_research_universe_snapshots
                (snapshot_date, asset_code, asset_name, listing_state, effective_at,
                 received_at, provider, source_cutoff, fact_hash)
            VALUES ('2026-08-04', '000001', :asset_name, 'listed',
                    '2026-08-04 09:00:00', '2026-08-04 15:00:00', 'akshare',
                    '2026-08-04 15:00:00', :fact_hash)
            """
        )
        connection.execute(insert, {"asset_name": "初始事实", "fact_hash": "a" * 64})
        connection.execute(insert, {"asset_name": "修订事实", "fact_hash": "b" * 64})
        assert connection.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 2

        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(insert, {"asset_name": "重复事实", "fact_hash": "a" * 64})

        migration.downgrade()
        assert table not in sa.inspect(connection).get_table_names()
