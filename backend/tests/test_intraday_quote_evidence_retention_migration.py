from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import (
    EtfIntradayCleanupCheckpoint,
    EtfIntradayQuoteEvidenceRef,
)

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = (
    VERSIONS_DIR / "20260810_000064_intraday_quote_evidence_retention.py"
)


def _migration():
    spec = spec_from_file_location("intraday_quote_evidence_retention", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_intraday_quote_evidence_retention_migration_is_reversible() -> None:
    assert "quote_id" in EtfIntradayQuoteEvidenceRef.__table__.c
    assert "cutoff_date" in EtfIntradayCleanupCheckpoint.__table__.c

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        sa.Table(
            "etf_intraday_quotes",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("trade_date", sa.Date(), nullable=False),
        )
        metadata.create_all(connection)
        migration = _migration()
        assert migration.revision == "20260810_000064"
        assert migration.down_revision == "20260809_000063"
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "etf_intraday_quote_evidence_refs",
            "etf_intraday_cleanup_checkpoints",
        } <= set(inspector.get_table_names())
        assert {
            "ix_etf_intraday_quote_evidence_quote_id",
            "ix_etf_intraday_quote_evidence_owner_lookup",
        } <= {
            item["name"]
            for item in inspector.get_indexes("etf_intraday_quote_evidence_refs")
        }
        assert {
            "ck_etf_intraday_quote_evidence_state",
            "ck_etf_intraday_quote_evidence_payload",
        } <= {
            item["name"]
            for item in inspector.get_check_constraints(
                "etf_intraday_quote_evidence_refs"
            )
        }

        migration.downgrade()
        assert "etf_intraday_quote_evidence_refs" not in sa.inspect(
            connection
        ).get_table_names()


def test_intraday_quote_evidence_retention_is_single_migration_head() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260810_000064"]
    assert script.get_revision("20260810_000064").down_revision == "20260809_000063"
