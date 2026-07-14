"""add isolated lifecycle shadow evidence

Revision ID: 20260714_000042
Revises: 20260714_000041
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_000042"
down_revision: str | None = "20260714_000041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracked_position_lifecycle_shadow_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tracked_position_id", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_schema_version", sa.String(length=32), nullable=False),
        sa.Column("trade_session", sa.Date(), nullable=False),
        sa.Column("repeat_slot", sa.String(length=64), nullable=False),
        sa.Column("sealed_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("production_position_state_version", sa.Integer(), nullable=False),
        sa.Column("rule_states_json", sa.JSON(), nullable=False),
        sa.Column("transitions_json", sa.JSON(), nullable=False),
        sa.Column("action_evidence_json", sa.JSON(), nullable=False),
        sa.Column("data_state", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tracked_position_id"], ["tracked_positions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_tracked_lifecycle_shadow_event"),
    )
    op.create_index(
        "ix_tracked_lifecycle_shadow_history",
        "tracked_position_lifecycle_shadow_evidence",
        ["user_id", "tracked_position_id", "policy_version", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tracked_lifecycle_shadow_history",
        table_name="tracked_position_lifecycle_shadow_evidence",
    )
    op.drop_table("tracked_position_lifecycle_shadow_evidence")
