"""Widen tracked-position audit schema versions."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260901_000074"
down_revision = "20260819_000073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tracked_position_alert_audits") as batch_op:
        batch_op.alter_column(
            "event_schema_version",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("tracked_position_alert_audits") as batch_op:
        batch_op.alter_column(
            "event_schema_version",
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=True,
        )
