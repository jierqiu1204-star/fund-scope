"""add ETF validation ranking source provenance

Revision ID: 20260715_000046
Revises: 20260715_000045
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_000046"
down_revision: str | None = "20260715_000045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = "etf_signal_validation_runs"
    with op.batch_alter_table(table) as batch_op:
        batch_op.add_column(
            sa.Column("ranking_source_kind", sa.String(32), nullable=True),
        )
        batch_op.add_column(
            sa.Column("source_replay_run_key", sa.String(128), nullable=True),
        )
        batch_op.create_check_constraint(
            "ck_etf_validation_ranking_source_kind",
            "ranking_source_kind IS NULL OR "
            "ranking_source_kind IN ('production_published', 'research_replay')",
        )
        batch_op.create_check_constraint(
            "ck_etf_validation_ranking_source_identity",
            "status <> 'success' OR ranking_source_kind IS NULL OR "
            "(ranking_source_kind = 'research_replay' AND "
            "source_replay_run_key IS NOT NULL AND source_signal_run_id IS NULL) OR "
            "(ranking_source_kind = 'production_published' AND "
            "source_signal_run_id IS NOT NULL AND source_replay_run_key IS NULL)",
        )
        batch_op.create_index(
            "ix_etf_signal_validation_runs_ranking_source_kind",
            ["ranking_source_kind"],
        )
        batch_op.create_index(
            "ix_etf_signal_validation_runs_source_replay_run_key",
            ["source_replay_run_key"],
        )


def downgrade() -> None:
    table = "etf_signal_validation_runs"
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_index("ix_etf_signal_validation_runs_source_replay_run_key")
        batch_op.drop_index("ix_etf_signal_validation_runs_ranking_source_kind")
        batch_op.drop_constraint(
            "ck_etf_validation_ranking_source_identity",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_etf_validation_ranking_source_kind",
            type_="check",
        )
        batch_op.drop_column("source_replay_run_key")
        batch_op.drop_column("ranking_source_kind")
