"""add sealed notification envelope metadata

Revision ID: 20260714_000041
Revises: 20260714_000040
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_000041"
down_revision: str | None = "20260714_000040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracked_position_notification_envelopes",
        sa.Column("template_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "tracked_position_notification_envelopes",
        sa.Column("template_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tracked_position_notification_envelopes",
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "tracked_position_notification_envelopes",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "tracked_position_notification_envelopes",
        sa.Column("rendered_content_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tracked_position_notification_items",
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
    )
    op.add_column(
        "tracked_position_notification_items",
        sa.Column("suppression_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "tracked_position_notification_items",
        sa.Column("next_eligible_repeat_slot", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tracked_position_notification_items", "next_eligible_repeat_slot")
    op.drop_column("tracked_position_notification_items", "suppression_reason")
    op.drop_column("tracked_position_notification_items", "status")
    op.drop_column("tracked_position_notification_envelopes", "rendered_content_hash")
    op.drop_column("tracked_position_notification_envelopes", "attempt_count")
    op.drop_column("tracked_position_notification_envelopes", "last_attempt_at")
    op.drop_column("tracked_position_notification_envelopes", "template_version")
    op.drop_column("tracked_position_notification_envelopes", "template_name")
