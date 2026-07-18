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

from app.models.entities import (
    EtfSignalValidationRun,
    EtfSignalValidationSourceEvent,
)

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "20260717_000048_etf_validation_source_manifest.py"


def _migration():
    spec = spec_from_file_location("etf_validation_source_manifest", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event_values(*, order: int, source_date: date) -> dict[str, object]:
    suffix = f"{order:02d}"
    return {
        "validation_run_id": 2,
        "event_order": order,
        "source_date": source_date,
        "ranking_source_kind": "production_published",
        "source_signal_run_id": 100 + order,
        "source_replay_run_key": None,
        "source_replay_contract_hash": None,
        "source_event_hash": f"event-{suffix}",
        "ranking_contract_hash": "ranking-contract",
        "scope_hash": f"scope-{suffix}",
        "universe_snapshot_hash": f"universe-{suffix}",
        "input_snapshot_hash": f"input-{suffix}",
        "availability_cutoff": datetime(2026, 7, 1 + order, 15, 30),
        "score_version": "final_score_v3",
        "score_field": "ranking_score",
        "rule_version": "final_score_v3_rule_v2",
        "price_basis": "total_return_adjusted",
        "publication_state": "published",
        "scope_kind": "full",
        "source_status": "success",
        "idempotency_key": f"published-{suffix}",
        "expected_asset_count": 100,
        "decision_data_covered_count": 96,
        "eligible_asset_count": 95,
        "item_count": 95,
        "decision_data_coverage_ratio": 0.96,
        "score_coverage_ratio": 0.95,
        "etf_item_count": 95,
        "finite_eligible_score_count": 95,
        "contiguous_global_rank": True,
        "immutable_hash": f"immutable-{suffix}",
        "created_at": datetime(2026, 7, 17, 1, order),
    }


def test_manifest_models_expose_header_and_child_contract() -> None:
    header = EtfSignalValidationRun.__table__.c
    event = EtfSignalValidationSourceEvent.__table__

    assert header.source_manifest_hash.nullable is True
    assert header.source_event_count.nullable is True
    assert set(event.c) >= {
        event.c.validation_run_id,
        event.c.event_order,
        event.c.source_date,
        event.c.ranking_source_kind,
        event.c.source_signal_run_id,
        event.c.source_replay_run_key,
        event.c.source_replay_contract_hash,
        event.c.source_event_hash,
        event.c.ranking_contract_hash,
        event.c.scope_hash,
        event.c.universe_snapshot_hash,
        event.c.input_snapshot_hash,
        event.c.availability_cutoff,
        event.c.score_version,
        event.c.score_field,
        event.c.rule_version,
        event.c.price_basis,
        event.c.publication_state,
        event.c.scope_kind,
        event.c.source_status,
        event.c.idempotency_key,
        event.c.expected_asset_count,
        event.c.decision_data_covered_count,
        event.c.eligible_asset_count,
        event.c.item_count,
        event.c.decision_data_coverage_ratio,
        event.c.score_coverage_ratio,
        event.c.etf_item_count,
        event.c.finite_eligible_score_count,
        event.c.contiguous_global_rank,
        event.c.immutable_hash,
    }
    assert {index.name for index in event.indexes} >= {
        "ix_etf_validation_source_events_source_date",
        "ix_etf_validation_source_events_signal_run",
        "ix_etf_validation_source_events_replay_key",
    }
    assert {constraint.name for constraint in event.constraints} >= {
        "uq_etf_validation_source_event_order",
        "uq_etf_validation_source_event_date",
        "uq_etf_validation_source_event_hash",
        "ck_etf_validation_source_event_kind",
        "ck_etf_validation_source_event_identity",
        "ck_etf_validation_source_event_order",
        "fk_etf_validation_source_event_manifest_kind",
    }


def test_manifest_migration_is_additive_constrained_and_reversible() -> None:
    migration = _migration()
    engine = sa.create_engine("sqlite:///:memory:")

    assert migration.revision == "20260717_000048"
    assert migration.down_revision == "20260715_000047"
    with engine.begin() as connection:
        metadata = sa.MetaData()
        parent = sa.Table(
            "etf_signal_validation_runs",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("ranking_source_kind", sa.String(32), nullable=True),
            sa.Column("source_signal_run_id", sa.Integer, nullable=True),
            sa.Column("source_replay_run_key", sa.String(128), nullable=True),
            sa.CheckConstraint(
                "ranking_source_kind IS NULL OR "
                "ranking_source_kind IN ('production_published', 'research_replay')",
                name="ck_etf_validation_ranking_source_kind",
            ),
            sa.CheckConstraint(
                "status <> 'success' OR ranking_source_kind IS NULL OR "
                "(ranking_source_kind = 'research_replay' AND "
                "source_replay_run_key IS NOT NULL AND source_signal_run_id IS NULL) OR "
                "(ranking_source_kind = 'production_published' AND "
                "source_signal_run_id IS NOT NULL AND source_replay_run_key IS NULL)",
                name="ck_etf_validation_ranking_source_identity",
            ),
        )
        metadata.create_all(connection)
        connection.execute(parent.insert().values(id=1, status="success"))

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "etf_signal_validation_source_events" in inspector.get_table_names()
        header_columns = {
            column["name"]
            for column in inspector.get_columns("etf_signal_validation_runs")
        }
        assert {"source_manifest_hash", "source_event_count"} <= header_columns
        assert connection.execute(
            sa.text(
                "SELECT source_manifest_hash, source_event_count "
                "FROM etf_signal_validation_runs WHERE id = 1"
            )
        ).one() == (None, None)

        migrated_parent = sa.Table(
            "etf_signal_validation_runs",
            sa.MetaData(),
            autoload_with=connection,
        )
        connection.execute(
            migrated_parent.insert().values(
                id=2,
                status="success",
                ranking_source_kind="production_published",
                source_manifest_hash="manifest-1",
                source_event_count=2,
            )
        )
        events = sa.Table(
            "etf_signal_validation_source_events",
            sa.MetaData(),
            autoload_with=connection,
        )
        assert any(
            foreign_key["constrained_columns"]
            == ["validation_run_id", "ranking_source_kind"]
            and foreign_key["referred_columns"] == ["id", "ranking_source_kind"]
            for foreign_key in inspector.get_foreign_keys(
                "etf_signal_validation_source_events"
            )
        )
        connection.execute(
            events.insert(),
            [
                _event_values(order=0, source_date=date(2026, 7, 1)),
                _event_values(order=1, source_date=date(2026, 7, 2)),
            ],
        )
        assert connection.scalar(sa.select(sa.func.count()).select_from(events)) == 2

        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                events.insert().values(
                    **{
                        **_event_values(order=2, source_date=date(2026, 7, 1)),
                        "immutable_hash": "other-hash",
                    }
                )
            )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                events.insert().values(
                    **{
                        **_event_values(order=2, source_date=date(2026, 7, 3)),
                        "source_replay_run_key": "mixed-replay",
                    }
                )
            )

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "etf_signal_validation_source_events" not in inspector.get_table_names()
        remaining = {
            column["name"]
            for column in inspector.get_columns("etf_signal_validation_runs")
        }
        assert "source_manifest_hash" not in remaining
        assert "source_event_count" not in remaining
        assert connection.scalar(
            sa.text("SELECT COUNT(*) FROM etf_signal_validation_runs")
        ) == 2


def test_alembic_keeps_one_head_and_contains_manifest_revision() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert len(script.get_heads()) == 1
    assert script.get_revision("20260717_000048") is not None
