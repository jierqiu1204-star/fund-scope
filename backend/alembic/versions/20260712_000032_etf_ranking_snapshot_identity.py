"""add ETF ranking snapshot identity

Revision ID: 20260712_000032
Revises: 20260703_000031
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_000032"
down_revision: str | None = "20260703_000031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = "short_research_signal_runs"
    op.add_column(table, sa.Column("scope_kind", sa.String(16), nullable=True))
    op.add_column(table, sa.Column("scope_hash", sa.String(128), nullable=True))
    op.add_column(table, sa.Column("universe_snapshot_hash", sa.String(128), nullable=True))
    op.add_column(table, sa.Column("input_snapshot_hash", sa.String(128), nullable=True))
    op.add_column(table, sa.Column("score_version", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("rule_version", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("ranking_contract_hash", sa.String(128), nullable=True))
    op.add_column(table, sa.Column("score_field", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("data_cutoff", sa.DateTime(), nullable=True))
    op.add_column(table, sa.Column("as_of_trade_date", sa.Date(), nullable=True))
    op.add_column(table, sa.Column("price_basis", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("expected_item_count", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("eligible_item_count", sa.Integer(), nullable=True))
    op.add_column(table, sa.Column("coverage_ratio", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("publication_state", sa.String(32), nullable=True))
    op.add_column(table, sa.Column("published_at", sa.DateTime(), nullable=True))
    op.add_column(table, sa.Column("idempotency_key", sa.String(255), nullable=True))
    op.create_index(
        "ix_short_research_signal_runs_canonical_snapshot",
        table,
        [
            "scope_kind",
            "score_version",
            "ranking_contract_hash",
            "as_of_trade_date",
            "price_basis",
            "publication_state",
        ],
    )
    op.create_index(
        "ux_short_research_signal_runs_idempotency_key",
        table,
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    table = "short_research_signal_runs"
    op.drop_index("ux_short_research_signal_runs_idempotency_key", table_name=table)
    op.drop_index("ix_short_research_signal_runs_canonical_snapshot", table_name=table)
    for column in (
        "idempotency_key",
        "published_at",
        "publication_state",
        "coverage_ratio",
        "eligible_item_count",
        "expected_item_count",
        "price_basis",
        "as_of_trade_date",
        "data_cutoff",
        "score_field",
        "ranking_contract_hash",
        "rule_version",
        "score_version",
        "input_snapshot_hash",
        "universe_snapshot_hash",
        "scope_hash",
        "scope_kind",
    ):
        op.drop_column(table, column)
