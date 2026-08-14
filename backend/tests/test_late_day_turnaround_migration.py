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
TRACKING_POLICY_PATH = VERSIONS_DIR / "20260814_000070_tracking_alert_policy.py"
ADDITIVE_PATH = VERSIONS_DIR / "20260814_000068_late_day_turnaround_research.py"
RETIRE_PATH = VERSIONS_DIR / "20260814_000069_retire_stock_price_history.py"


def _load(path: Path):
    spec = spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_late_day_additive_schema_is_reversible_and_unique() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        migration = _load(ADDITIVE_PATH)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert {
            "ashare_intraday_10m_facts",
            "late_day_turnaround_capture_checkpoints",
            "late_day_turnaround_runs",
            "late_day_turnaround_observations",
        } <= set(inspector.get_table_names())
        assert "ix_ashare_intraday_10m_pit_lookup" in {
            item["name"]
            for item in inspector.get_indexes("ashare_intraday_10m_facts")
        }

        insert = sa.text(
            """
            INSERT INTO late_day_turnaround_runs
                (manifest_hash, universe, signal_date, decision_at, status,
                 policy_mode, strategy_version, contract_hash, universe_hash,
                 input_hash, expected_count, evaluated_count, available_count,
                 qualifying_count, provider_health_json, exclusion_counts_json,
                 created_at)
            VALUES
                ('manifest', 'etf', '2026-08-14', '2026-08-14 14:30:00', 'complete',
                 'shadow_only', 'v1', 'contract', 'universe', 'input', 1, 1, 1,
                 1, '{}', '{}', '2026-08-14 14:30:01')
            """
        )
        connection.execute(insert)
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(insert)

        migration.downgrade()
        assert "late_day_turnaround_runs" not in sa.inspect(connection).get_table_names()


def test_retirement_drops_only_legacy_price_table_and_downgrades_schema() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        stocks = sa.Table(
            "stocks",
            metadata,
            sa.Column("code", sa.String(32), primary_key=True),
        )
        sa.Table(
            "stock_price_history",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("stock_code", sa.String(32), sa.ForeignKey(stocks.c.code)),
        )
        for retained in (
            "stock_fundamentals",
            "stock_metrics",
            "recommendation_runs",
            "recommendation_items",
        ):
            sa.Table(retained, metadata, sa.Column("id", sa.Integer(), primary_key=True))
        metadata.create_all(connection)

        migration = _load(RETIRE_PATH)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        tables = set(sa.inspect(connection).get_table_names())
        assert "stock_price_history" not in tables
        assert {
            "stocks",
            "stock_fundamentals",
            "stock_metrics",
            "recommendation_runs",
            "recommendation_items",
        } <= tables

        migration.downgrade()
        columns = {
            item["name"]
            for item in sa.inspect(connection).get_columns("stock_price_history")
        }
        assert {"stock_code", "trade_date", "open", "high", "low", "close"} <= columns


def test_tracking_alert_policy_migration_defaults_and_constraints() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        sa.Table(
            "tracked_positions",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("asset_type", sa.String(16), nullable=False),
        )
        metadata.create_all(connection)

        migration = _load(TRACKING_POLICY_PATH)
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        columns = {
            item["name"]: item
            for item in sa.inspect(connection).get_columns("tracked_positions")
        }
        assert columns["alert_policy_id"]["default"] == "'standard_dynamic_v2'"
        assert columns["alert_policy_version"]["default"] == "'standard_dynamic_v2'"

        connection.execute(
            sa.text("INSERT INTO tracked_positions (id, asset_type) VALUES (1, 'fund')")
        )
        row = connection.execute(
            sa.text(
                "SELECT alert_policy_id, alert_policy_provenance "
                "FROM tracked_positions WHERE id = 1"
            )
        ).one()
        assert tuple(row) == ("standard_dynamic_v2", "default")
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO tracked_positions "
                    "(id, asset_type, alert_policy_id) "
                    "VALUES (2, 'fund', 'late_day_turnaround_t1_v1')"
                )
            )

        migration.downgrade()
        remaining = {
            item["name"]
            for item in sa.inspect(connection).get_columns("tracked_positions")
        }
        assert remaining == {"id", "asset_type"}


def test_tracking_alert_policy_is_single_migration_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260814_000070"]
    assert script.get_revision("20260814_000070").down_revision == "20260814_000069"
