from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import (
    EtfAdjustedHistoryAvailability,
    EtfListingDateObservation,
    TradableEtf,
)

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
BASE_MIGRATION_PATH = VERSIONS_DIR / "20260726_000055_etf_authoritative_listing_date.py"
FOLLOWUP_MIGRATION_PATH = VERSIONS_DIR / "20260727_000056_etf_listing_observation_and_lane_scope.py"


def _migration(path: Path, module_name: str):
    spec = spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_authoritative_listing_date_migration_is_nullable_and_reversible() -> None:
    columns = TradableEtf.__table__.c
    assert {
        "listing_date",
        "listing_date_source",
        "listing_date_observed_at",
    } <= {column.name for column in columns}
    assert columns.listing_date.nullable is True
    assert columns.listing_date_source.nullable is True
    assert columns.listing_date_observed_at.nullable is True
    assert {
        "scope",
        "required_calendar_hash",
    } <= {column.name for column in EtfAdjustedHistoryAvailability.__table__.c}
    assert {
        "etf_code",
        "listing_date",
        "source",
        "provider_version",
        "observed_at",
        "universe_snapshot_hash",
        "raw_payload_hash",
        "evidence_hash",
    } <= {column.name for column in EtfListingDateObservation.__table__.c}

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    tradable = sa.Table(
        "tradable_etfs",
        metadata,
        sa.Column("code", sa.String(length=32), primary_key=True),
    )
    availability = sa.Table(
        "etf_adjusted_history_availability",
        metadata,
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("provider_policy_version", sa.String(length=128), nullable=False),
        sa.Column("retry_after", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint(
            "etf_code",
            "provider_policy_version",
            name="pk_etf_adjusted_history_availability",
        ),
        sa.ForeignKeyConstraint(["etf_code"], [tradable.c.code], ondelete="CASCADE"),
    )
    sa.Index(
        "ix_etf_adjusted_history_availability_retry",
        availability.c.provider_policy_version,
        availability.c.retry_after,
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        base_migration = _migration(
            BASE_MIGRATION_PATH,
            "etf_authoritative_listing_date",
        )
        followup_migration = _migration(
            FOLLOWUP_MIGRATION_PATH,
            "etf_listing_observation_and_lane_scope",
        )
        operations = Operations(MigrationContext.configure(connection))
        base_migration.op = operations
        followup_migration.op = operations
        base_migration.upgrade()
        followup_migration.upgrade()
        inspector = sa.inspect(connection)
        column_names = {column["name"] for column in inspector.get_columns("tradable_etfs")}
        assert {
            "listing_date",
            "listing_date_source",
            "listing_date_observed_at",
        } <= column_names
        assert "etf_listing_date_observations" in inspector.get_table_names()
        availability_columns = {
            column["name"] for column in inspector.get_columns("etf_adjusted_history_availability")
        }
        assert {"scope", "required_calendar_hash"} <= availability_columns
        assert {
            "etf_code",
            "provider_policy_version",
            "scope",
            "required_calendar_hash",
        } == set(
            inspector.get_pk_constraint("etf_adjusted_history_availability")["constrained_columns"]
        )

        followup_migration.downgrade()
        base_migration.downgrade()
        inspector = sa.inspect(connection)
        assert "etf_listing_date_observations" not in inspector.get_table_names()
        assert {"etf_code", "provider_policy_version"} == set(
            inspector.get_pk_constraint("etf_adjusted_history_availability")["constrained_columns"]
        )
        remaining = {column["name"] for column in inspector.get_columns("tradable_etfs")}
        assert remaining == {"code"}


def test_authoritative_listing_date_migration_is_the_single_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260817_000072"]
    assert script.get_revision("20260809_000063").down_revision == "20260804_000062"
    assert script.get_revision("20260727_000056").down_revision == ("20260726_000055")
    assert script.get_revision("20260726_000055").down_revision == ("20260725_000054")
