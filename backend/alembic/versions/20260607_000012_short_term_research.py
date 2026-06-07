"""Add short term research signal tables."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260607_000012"
down_revision = "20260606_000011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_research_signal_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_table(
        "short_research_signal_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("short_research_signal_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("conclusion", sa.String(length=64), nullable=False),
        sa.Column("score_breakdown_json", sa.JSON(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("rationale_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", "asset_type", "asset_code", name="uq_short_research_signal_item"),
    )
    op.create_index(
        "ix_short_research_signal_items_run_rank",
        "short_research_signal_items",
        ["run_id", "rank"],
    )


def downgrade() -> None:
    op.drop_index("ix_short_research_signal_items_run_rank", table_name="short_research_signal_items")
    op.drop_table("short_research_signal_items")
    op.drop_table("short_research_signal_runs")
