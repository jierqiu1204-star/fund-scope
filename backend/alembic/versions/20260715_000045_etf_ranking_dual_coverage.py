"""add ETF ranking decision-data coverage

Revision ID: 20260715_000045
Revises: 20260715_000044
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_000045"
down_revision: str | None = "20260715_000044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("short_research_signal_runs") as batch_op:
        batch_op.add_column(sa.Column("decision_data_item_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("decision_data_coverage_ratio", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("short_research_signal_runs") as batch_op:
        batch_op.drop_column("decision_data_coverage_ratio")
        batch_op.drop_column("decision_data_item_count")
