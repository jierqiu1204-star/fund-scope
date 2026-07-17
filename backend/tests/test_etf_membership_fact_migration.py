from __future__ import annotations

from datetime import date, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import EtfPointInTimeMembershipFact

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260715_000047_etf_pit_membership_facts.py"


def _migration():
    spec = spec_from_file_location("etf_pit_membership_facts", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_exposes_complete_receipt_without_operational_foreign_key() -> None:
    table = EtfPointInTimeMembershipFact.__table__

    assert set(table.c) >= {
        table.c.id,
        table.c.etf_code,
        table.c.external_source_id,
        table.c.provider,
        table.c.provider_version,
        table.c.observed_at,
        table.c.effective_from,
        table.c.effective_to,
        table.c.evidence_hash,
        table.c.raw_payload_hash,
        table.c.created_at,
    }
    assert not table.c.etf_code.foreign_keys
    assert table.c.evidence_hash.unique is True
    assert {index.name for index in table.indexes} == {
        "ix_etf_pit_membership_facts_effective_lookup",
        "ix_etf_pit_membership_facts_observed_cursor",
    }


def test_migration_is_additive_empty_and_reversible() -> None:
    migration = _migration()
    engine = sa.create_engine("sqlite:///:memory:")

    assert migration.revision == "20260715_000047"
    assert migration.down_revision == "20260715_000046"
    with engine.begin() as connection:
        metadata = sa.MetaData()
        operational = sa.Table(
            "etf_universe_memberships",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("etf_code", sa.String(32), nullable=False),
            sa.Column("effective_from", sa.Date, nullable=False),
            sa.Column("effective_to", sa.Date),
            sa.Column("source", sa.String(64), nullable=False),
        )
        metadata.create_all(connection)
        connection.execute(
            operational.insert().values(
                id=1,
                etf_code="510300",
                effective_from=date(2020, 1, 1),
                source="production-current",
            )
        )

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "etf_point_in_time_membership_facts" in inspector.get_table_names()
        assert connection.scalar(
            sa.text("SELECT COUNT(*) FROM etf_point_in_time_membership_facts")
        ) == 0
        table = sa.Table(
            "etf_point_in_time_membership_facts",
            sa.MetaData(),
            autoload_with=connection,
        )
        values = {
            "etf_code": "510300",
            "external_source_id": "exchange-notice-1",
            "provider": "sse",
            "provider_version": "notice-v1",
            "observed_at": datetime(2020, 1, 1, 1),
            "effective_from": date(2020, 1, 2),
            "effective_to": None,
            "evidence_hash": "a" * 64,
            "raw_payload_hash": "b" * 64,
            "created_at": datetime(2026, 7, 15, 1),
        }
        connection.execute(table.insert().values(**values))
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(table.insert().values(**values))
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                table.insert().values(
                    **{
                        **values,
                        "evidence_hash": "c" * 64,
                        "effective_from": date(2020, 1, 3),
                        "effective_to": date(2020, 1, 2),
                    }
                )
            )

        migration.downgrade()
        assert "etf_point_in_time_membership_facts" not in sa.inspect(
            connection
        ).get_table_names()
        assert connection.scalar(
            sa.text("SELECT COUNT(*) FROM etf_universe_memberships")
        ) == 1


def test_alembic_has_one_head_at_membership_fact_revision() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260715_000047"]
