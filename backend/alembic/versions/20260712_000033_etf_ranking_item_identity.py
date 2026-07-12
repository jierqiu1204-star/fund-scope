"""add ETF ranking item identity

Revision ID: 20260712_000033
Revises: 20260712_000032
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_000033"
down_revision: str | None = "20260712_000032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = "short_research_signal_items"
    op.add_column(table, sa.Column("ranking_score", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("score_eligible", sa.Boolean(), nullable=True))
    op.add_column(table, sa.Column("global_rank", sa.Integer(), nullable=True))
    op.create_index("ix_short_research_signal_items_run_global_rank", table, ["run_id", "global_rank"])


def downgrade() -> None:
    table = "short_research_signal_items"
    op.drop_index("ix_short_research_signal_items_run_global_rank", table_name=table)
    op.drop_column(table, "global_rank")
    op.drop_column(table, "score_eligible")
    op.drop_column(table, "ranking_score")
