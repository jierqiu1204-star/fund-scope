"""Add tracked position alert tables."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260607_000014"
down_revision = "20260607_000013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users
        SET recipient_email = '19535838578@163.com'
        WHERE recipient_email = 'owner@example.com'
        """
    )
    op.execute(
        """
        UPDATE users
        SET
            smtp_host = COALESCE(smtp_host, 'smtp.163.com'),
            smtp_port = COALESCE(smtp_port, 465),
            smtp_username = COALESCE(smtp_username, '19535838578@163.com'),
            smtp_from = COALESCE(smtp_from, 'FundScope <19535838578@163.com>')
        WHERE id = 1
        """
    )
    op.create_table(
        "tracked_positions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("asset_name", sa.String(length=255), nullable=False),
        sa.Column("buy_date", sa.Date(), nullable=False),
        sa.Column("buy_amount", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("entry_price_date", sa.Date(), nullable=True),
        sa.Column("estimated_shares", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_tracked_positions_status_asset",
        "tracked_positions",
        ["status", "asset_type", "asset_code"],
    )
    op.create_table(
        "tracked_position_alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tracked_position_id",
            sa.Integer(),
            sa.ForeignKey("tracked_positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alert_date", sa.Date(), nullable=False),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("trigger_label", sa.String(length=64), nullable=False),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("current_price_date", sa.Date(), nullable=True),
        sa.Column("estimated_value", sa.Float(), nullable=True),
        sa.Column("estimated_pnl", sa.Float(), nullable=True),
        sa.Column("estimated_pnl_pct", sa.Float(), nullable=True),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("advisor_summary", sa.Text(), nullable=True),
        sa.Column("email_status", sa.String(length=32), nullable=False),
        sa.Column("email_error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tracked_position_id",
            "alert_date",
            "alert_type",
            name="uq_tracked_position_alert_day_type",
        ),
    )
    op.create_index(
        "ix_tracked_position_alerts_position_date",
        "tracked_position_alerts",
        ["tracked_position_id", "alert_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_tracked_position_alerts_position_date", table_name="tracked_position_alerts")
    op.drop_table("tracked_position_alerts")
    op.drop_index("ix_tracked_positions_status_asset", table_name="tracked_positions")
    op.drop_table("tracked_positions")
