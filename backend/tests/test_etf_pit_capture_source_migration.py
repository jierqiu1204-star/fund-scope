from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import EtfPitCaptureSource

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260729_000057_etf_pit_capture_sources.py"


def _migration():
    spec = spec_from_file_location("etf_pit_capture_sources", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pit_capture_source_migration_is_additive_constrained_and_reversible() -> None:
    columns = EtfPitCaptureSource.__table__.c
    assert {
        "source_signal_run_id",
        "as_of_trade_date",
        "source_snapshot_hash",
        "source_context_hash",
        "market_decision_cutoff",
        "data_receipt_cutoff",
        "replay_visibility_cutoff",
        "provider_health_hash",
        "source_context_json",
    } <= {column.name for column in columns}

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        sa.Table(
            "short_research_signal_runs",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
        )
        metadata.create_all(connection)
        migration = _migration()
        assert migration.revision == "20260729_000057"
        assert migration.down_revision == "20260727_000056"
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "etf_pit_capture_sources" in inspector.get_table_names()
        assert {
            "uq_etf_pit_capture_source_signal_run",
            "uq_etf_pit_capture_source_context_hash",
        } <= {item["name"] for item in inspector.get_unique_constraints("etf_pit_capture_sources")}
        assert {
            "ck_etf_pit_capture_source_complete_readiness",
            "ck_etf_pit_capture_source_target_coverage",
            "ck_etf_pit_capture_source_warmup_coverage",
        } <= {item["name"] for item in inspector.get_check_constraints("etf_pit_capture_sources")}
        assert {item["name"] for item in inspector.get_indexes("etf_pit_capture_sources")} == {
            "ix_etf_pit_capture_sources_trade_date"
        }

        migration.downgrade()
        assert "etf_pit_capture_sources" not in sa.inspect(connection).get_table_names()


def test_pit_capture_source_chain_has_one_current_alembic_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260901_000074"]
    assert script.get_revision("20260809_000063").down_revision == "20260804_000062"
