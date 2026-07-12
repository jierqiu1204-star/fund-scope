"""add ETF research price provenance

Revision ID: 20260712_000034
Revises: 20260712_000033
Create Date: 2026-07-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260712_000034"
down_revision: str | None = "20260712_000033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = "etf_price_history"
    op.add_column(table, sa.Column("raw_price_basis", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("research_adjusted_value", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("research_price_basis", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("data_provider", sa.String(64), nullable=True))
    op.add_column(table, sa.Column("provider_version", sa.String(128), nullable=True))
    op.add_column(table, sa.Column("source_timestamp", sa.DateTime(), nullable=True))
    op.add_column(table, sa.Column("adjustment_version", sa.String(128), nullable=True))
    op.add_column(table, sa.Column("decision_eligible", sa.Boolean(), nullable=True))
    op.add_column(table, sa.Column("decision_ineligibility_reason", sa.String(255), nullable=True))
    op.create_index("ix_etf_price_history_trade_date_decision_eligible", table, ["trade_date", "decision_eligible"])


def downgrade() -> None:
    table = "etf_price_history"
    op.drop_index("ix_etf_price_history_trade_date_decision_eligible", table_name=table)
    for column in (
        "decision_ineligibility_reason",
        "decision_eligible",
        "adjustment_version",
        "source_timestamp",
        "provider_version",
        "data_provider",
        "research_price_basis",
        "research_adjusted_value",
        "raw_price_basis",
    ):
        op.drop_column(table, column)
