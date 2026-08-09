from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import EtfAdjustedPriceRevision

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260802_000060_etf_adjusted_price_revisions.py"


def _migration():
    spec = spec_from_file_location("etf_adjusted_price_revisions", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adjusted_price_revision_migration_is_additive_and_reversible() -> None:
    columns = EtfAdjustedPriceRevision.__table__.c
    assert {
        "etf_code",
        "trade_date",
        "source_timestamp",
        "first_seen_at",
        "observed_at",
        "payload_hash",
        "revision_hash",
        "decision_eligible",
        "decision_ineligibility_reason",
        "supersedes_revision_id",
    } <= {column.name for column in columns}

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "etf_adjusted_price_revisions" in inspector.get_table_names()
        assert {
            "ix_etf_adjusted_price_revisions_pit_lookup",
            "ix_etf_adjusted_price_revisions_payload",
            "ix_etf_adjusted_price_revisions_supersession",
        } <= {index["name"] for index in inspector.get_indexes("etf_adjusted_price_revisions")}
        migration.downgrade()
        assert "etf_adjusted_price_revisions" not in sa.inspect(connection).get_table_names()


def test_adjusted_price_revision_migration_remains_in_the_linear_head_chain() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260809_000063"]
    assert script.get_revision("20260809_000063").down_revision == "20260804_000062"
    assert script.get_revision("20260802_000059") is not None
    assert script.get_revision("20260802_000060") is not None
