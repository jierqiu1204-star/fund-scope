from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import TradableEtf

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260726_000055_etf_authoritative_listing_date.py"


def _migration():
    spec = spec_from_file_location(
        "etf_authoritative_listing_date",
        MIGRATION_PATH,
    )
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

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "tradable_etfs",
        metadata,
        sa.Column("code", sa.String(length=32), primary_key=True),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        column_names = {
            column["name"] for column in sa.inspect(connection).get_columns("tradable_etfs")
        }
        assert {
            "listing_date",
            "listing_date_source",
            "listing_date_observed_at",
        } <= column_names
        migration.downgrade()
        remaining = {
            column["name"] for column in sa.inspect(connection).get_columns("tradable_etfs")
        }
        assert remaining == {"code"}


def test_authoritative_listing_date_migration_is_the_single_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260726_000055"]
    assert script.get_revision("20260726_000055").down_revision == "20260725_000054"
