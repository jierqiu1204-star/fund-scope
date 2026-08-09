from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import EtfAdjustedHistoryAvailability

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260725_000054_etf_adjusted_history_availability.py"


def _migration():
    spec = spec_from_file_location(
        "etf_adjusted_history_availability",
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adjusted_history_availability_migration_is_additive_and_reversible() -> None:
    columns = EtfAdjustedHistoryAvailability.__table__.c
    assert {
        "etf_code",
        "provider_policy_version",
        "provider",
        "provider_version",
        "adjustment_version",
        "requested_from",
        "requested_to",
        "earliest_eligible_date",
        "latest_eligible_date",
        "eligible_session_count",
        "status",
        "observed_at",
        "retry_after",
        "evidence_json",
    } <= {column.name for column in columns}
    assert "listing_date" not in {column.name for column in columns}

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "etf_adjusted_history_availability" in inspector.get_table_names()
        assert {"etf_code", "provider_policy_version"} == set(
            inspector.get_pk_constraint("etf_adjusted_history_availability")["constrained_columns"]
        )
        migration.downgrade()
        assert "etf_adjusted_history_availability" not in sa.inspect(connection).get_table_names()


def test_adjusted_history_availability_is_latest_single_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260810_000064"]
    assert script.get_revision("20260809_000063").down_revision == "20260804_000062"
    assert script.get_revision("20260725_000054") is not None
