"""add ETF validation source identity

Revision ID: 20260712_000036
Revises: 20260712_000035
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_000036"
down_revision: str | None = "20260712_000035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = "etf_signal_validation_runs"
    for name, column_type in (
        ("source_ranking_contract_hash", sa.String(128)),
        ("source_scope_kind", sa.String(16)),
        ("source_scope_hash", sa.String(128)),
        ("source_universe_snapshot_hash", sa.String(128)),
        ("source_input_snapshot_hash", sa.String(128)),
        ("source_score_field", sa.String(64)),
        ("source_score_version", sa.String(64)),
        ("source_rule_version", sa.String(64)),
        ("price_basis", sa.String(64)),
        ("execution_model", sa.String(64)),
        ("data_cutoff", sa.DateTime()),
    ):
        op.add_column(table, sa.Column(name, column_type, nullable=True))
    op.create_index(
        "ix_etf_signal_validation_runs_source_contract",
        table,
        [
            "source_ranking_contract_hash",
            "source_scope_kind",
            "source_universe_snapshot_hash",
            "source_score_field",
            "price_basis",
        ],
    )


def downgrade() -> None:
    table = "etf_signal_validation_runs"
    op.drop_index("ix_etf_signal_validation_runs_source_contract", table_name=table)
    for name in (
        "data_cutoff",
        "execution_model",
        "price_basis",
        "source_rule_version",
        "source_score_version",
        "source_score_field",
        "source_input_snapshot_hash",
        "source_universe_snapshot_hash",
        "source_scope_hash",
        "source_scope_kind",
        "source_ranking_contract_hash",
    ):
        op.drop_column(table, name)
