from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260811_000066_tracked_etf_sleeve_risk.py"


def _migration():
    spec = importlib.util.spec_from_file_location("tracked_etf_sleeve_risk_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tracked_etf_sleeve_migration_is_additive_constrained_and_reversible() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    sa.Table(
        "tracked_positions",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "etf_trading_capital_confirmed_at" in {
            column["name"] for column in inspector.get_columns("users")
        }
        assert "tracked_etf_sleeve_ledger_events" in inspector.get_table_names()
        assert "tracked_etf_sleeve_daily_snapshots" in inspector.get_table_names()
        snapshot_columns = {
            column["name"]
            for column in inspector.get_columns("tracked_etf_sleeve_daily_snapshots")
        }
        assert "cooldown_sessions_remaining" in snapshot_columns

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "tracked_etf_sleeve_ledger_events" not in inspector.get_table_names()
        assert "tracked_etf_sleeve_daily_snapshots" not in inspector.get_table_names()
        assert "etf_trading_capital_confirmed_at" not in {
            column["name"] for column in inspector.get_columns("users")
        }


def test_tracked_etf_sleeve_migration_is_the_single_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260811_000066"]
