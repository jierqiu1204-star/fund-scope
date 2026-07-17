from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.models.entities import EtfSignalValidationRun
from app.schemas.short_research import EtfSignalValidationRunOut

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = (
    VERSIONS_DIR / "20260715_000046_etf_validation_ranking_source_provenance.py"
)


def _migration():
    spec = spec_from_file_location("etf_validation_ranking_source_provenance", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validation_run_model_and_schema_expose_nullable_replay_provenance() -> None:
    columns = EtfSignalValidationRun.__table__.c
    assert columns.ranking_source_kind.nullable is True
    assert columns.source_replay_run_key.nullable is True
    index_names = {index.name for index in EtfSignalValidationRun.__table__.indexes}
    assert "ix_etf_signal_validation_runs_ranking_source_kind" in index_names
    assert "ix_etf_signal_validation_runs_source_replay_run_key" in index_names
    constraint_names = {
        constraint.name
        for constraint in EtfSignalValidationRun.__table__.constraints
    }
    assert "ck_etf_validation_ranking_source_kind" in constraint_names
    assert "ck_etf_validation_ranking_source_identity" in constraint_names

    output = EtfSignalValidationRunOut(
        id=1,
        status="success",
        as_of_date="2026-07-15",
        validation_mode="score_bucket",
        rule_version="label_validation_v1",
        ranking_source_kind="research_replay",
        source_replay_run_key="replay-run-1",
        created_at="2026-07-15T09:30:00",
    )

    assert output.ranking_source_kind == "research_replay"
    assert output.source_replay_run_key == "replay-run-1"


def test_validation_provenance_migration_preserves_legacy_nulls_indexes_and_downgrade() -> None:
    migration = _migration()
    engine = sa.create_engine("sqlite:///:memory:")

    assert migration.revision == "20260715_000046"
    assert migration.down_revision == "20260715_000045"
    with engine.begin() as connection:
        metadata = sa.MetaData()
        table = sa.Table(
            "etf_signal_validation_runs",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("source_signal_run_id", sa.Integer, nullable=True),
        )
        metadata.create_all(connection)
        connection.execute(table.insert().values(id=1, status="success"))

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("etf_signal_validation_runs")
        }
        assert columns["ranking_source_kind"]["nullable"] is True
        assert columns["source_replay_run_key"]["nullable"] is True
        assert connection.execute(
            sa.text(
                "SELECT ranking_source_kind, source_replay_run_key "
                "FROM etf_signal_validation_runs WHERE id = 1"
            )
        ).one() == (None, None)
        assert {
            "ix_etf_signal_validation_runs_ranking_source_kind",
            "ix_etf_signal_validation_runs_source_replay_run_key",
        } <= {
            index["name"]
            for index in inspector.get_indexes("etf_signal_validation_runs")
        }
        assert {
            "ck_etf_validation_ranking_source_kind",
            "ck_etf_validation_ranking_source_identity",
        } <= {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "etf_signal_validation_runs"
            )
        }
        migrated = sa.Table(
            "etf_signal_validation_runs",
            sa.MetaData(),
            autoload_with=connection,
        )
        connection.execute(
            migrated.insert().values(
                id=2,
                status="success",
                ranking_source_kind="research_replay",
                source_replay_run_key="replay-2",
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                migrated.insert().values(
                    id=3,
                    status="success",
                    ranking_source_kind="research_replay",
                )
            )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                migrated.insert().values(
                    id=4,
                    status="success",
                    ranking_source_kind="misspelled",
                )
            )

        migration.downgrade()
        remaining = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "etf_signal_validation_runs"
            )
        }
        assert "ranking_source_kind" not in remaining
        assert "source_replay_run_key" not in remaining


def test_alembic_has_one_head_after_replay_provenance_revision() -> None:
    config = Config()
    config.set_main_option("script_location", str(VERSIONS_DIR.parent))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["20260715_000047"]


@pytest.mark.parametrize(
    "values",
    [
        {"ranking_source_kind": "research_replay"},
        {"ranking_source_kind": "production_published"},
        {
            "ranking_source_kind": "research_replay",
            "source_replay_run_key": "replay-1",
            "source_signal_run_id": 7,
        },
    ],
)
def test_success_schema_rejects_incomplete_or_mixed_ranking_source_identity(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        EtfSignalValidationRunOut(
            id=1,
            status="success",
            as_of_date="2026-07-15",
            validation_mode="score_bucket",
            rule_version="label_validation_v1",
            created_at="2026-07-15T09:30:00",
            **values,
        )
