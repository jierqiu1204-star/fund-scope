"""add ordered lifecycle shadow streams

Revision ID: 20260715_000044
Revises: 20260714_000043
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_000044"
down_revision: str | None = "20260714_000043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE_NAME = "tracked_position_lifecycle_shadow_evidence"
HISTORY_INDEX = "ix_tracked_lifecycle_shadow_history"
STREAM_SEQUENCE_CONSTRAINT = "uq_tracked_lifecycle_shadow_stream_sequence"


def upgrade() -> None:
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        batch_op.add_column(sa.Column("position_episode_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("exposure_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("stream_sequence", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("predecessor_event_id", sa.String(length=64), nullable=True))

    op.execute(
        sa.text(
            f"UPDATE {TABLE_NAME} "
            "SET position_episode_id = 'legacy_shadow_unscoped', "
            "exposure_version = 0, stream_sequence = id"
        )
    )

    with op.batch_alter_table(TABLE_NAME) as batch_op:
        batch_op.alter_column("position_episode_id", existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column("exposure_version", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("stream_sequence", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_index(HISTORY_INDEX)
        batch_op.create_index(
            HISTORY_INDEX,
            [
                "user_id",
                "tracked_position_id",
                "policy_version",
                "position_episode_id",
                "exposure_version",
                "stream_sequence",
                "id",
            ],
        )
        batch_op.create_unique_constraint(
            STREAM_SEQUENCE_CONSTRAINT,
            [
                "user_id",
                "tracked_position_id",
                "policy_version",
                "position_episode_id",
                "exposure_version",
                "stream_sequence",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        batch_op.drop_constraint(STREAM_SEQUENCE_CONSTRAINT, type_="unique")
        batch_op.drop_index(HISTORY_INDEX)
        batch_op.create_index(
            HISTORY_INDEX,
            ["user_id", "tracked_position_id", "policy_version", "created_at", "id"],
        )
        batch_op.drop_column("predecessor_event_id")
        batch_op.drop_column("stream_sequence")
        batch_op.drop_column("exposure_version")
        batch_op.drop_column("position_episode_id")
