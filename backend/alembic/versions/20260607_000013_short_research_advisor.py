"""Add short research advisor report tables."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260607_000013"
down_revision = "20260607_000012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_research_advisor_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "signal_run_id",
            sa.Integer(),
            sa.ForeignKey("short_research_signal_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "signal_item_id",
            sa.Integer(),
            sa.ForeignKey("short_research_signal_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("action_label", sa.String(length=32), nullable=False),
        sa.Column("plain_summary", sa.Text(), nullable=False),
        sa.Column("opportunity_json", sa.JSON(), nullable=False),
        sa.Column("risks_json", sa.JSON(), nullable=False),
        sa.Column("opposing_view", sa.Text(), nullable=False),
        sa.Column("watch_conditions_json", sa.JSON(), nullable=False),
        sa.Column("holding_note", sa.Text(), nullable=False),
        sa.Column("data_limitations", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("deterministic_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("raw_response_json", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "signal_run_id",
            "asset_type",
            "asset_code",
            name="uq_short_research_advisor_report",
        ),
    )
    op.create_index(
        "ix_short_research_advisor_reports_run_rank",
        "short_research_advisor_reports",
        ["signal_run_id", "asset_type", "asset_code"],
    )
    op.create_table(
        "short_research_advisor_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "signal_run_id",
            sa.Integer(),
            sa.ForeignKey("short_research_signal_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "signal_item_id",
            sa.Integer(),
            sa.ForeignKey("short_research_signal_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_short_research_advisor_attempts_run_asset",
        "short_research_advisor_attempts",
        ["signal_run_id", "asset_type", "asset_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_short_research_advisor_attempts_run_asset", table_name="short_research_advisor_attempts")
    op.drop_table("short_research_advisor_attempts")
    op.drop_index("ix_short_research_advisor_reports_run_rank", table_name="short_research_advisor_reports")
    op.drop_table("short_research_advisor_reports")
