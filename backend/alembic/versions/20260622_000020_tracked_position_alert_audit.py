"""Add tracked position alert audit trail table

Revision ID: 20260622_000020
Revises: 20260621_000019
Create Date: 2026-06-22 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260622_000020"
down_revision = "20260621_000019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_position_alert_audits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "tracked_position_id",
            sa.Integer(),
            sa.ForeignKey("tracked_positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tracked_position_alert_id",
            sa.Integer(),
            sa.ForeignKey("tracked_position_alerts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("signal_type", sa.String(length=32), nullable=True),
        sa.Column("alert_date", sa.Date(), nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("trigger_label", sa.String(length=64), nullable=True),
        sa.Column("data_source", sa.String(length=32), nullable=False),
        sa.Column("quote_freshness", sa.String(length=32), nullable=False),
        sa.Column("threshold_context_json", sa.JSON(), nullable=True),
        sa.Column("decision_context_json", sa.JSON(), nullable=True),
        sa.Column("recipient", sa.String(length=255), nullable=True),
        sa.Column("duplicate_reason", sa.Text(), nullable=True),
        sa.Column("cooldown_reason", sa.Text(), nullable=True),
        sa.Column("smtp_result", sa.String(length=32), nullable=True),
        sa.Column("smtp_error_message", sa.Text(), nullable=True),
        sa.Column("quote_time", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tracked_position_alert_audits_position_created",
        "tracked_position_alert_audits",
        ["tracked_position_id", "created_at"],
    )
    op.create_index(
        "ix_tracked_position_alert_audits_position_outcome",
        "tracked_position_alert_audits",
        ["tracked_position_id", "outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_tracked_position_alert_audits_position_outcome", table_name="tracked_position_alert_audits")
    op.drop_index("ix_tracked_position_alert_audits_position_created", table_name="tracked_position_alert_audits")
    op.drop_table("tracked_position_alert_audits")