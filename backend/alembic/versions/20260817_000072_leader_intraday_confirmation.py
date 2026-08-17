"""Add precise evidence time for leader-tactics intraday confirmations."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260817_000072"
down_revision = "20260817_000071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leader_tactics_v2_state_transitions",
        sa.Column("evidence_cutoff", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "leader_tactics_v2_state_transitions",
        sa.Column("projected_entry_status", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_leader_tactics_v2_transition_intraday_visibility",
        "leader_tactics_v2_state_transitions",
        ["manifest_hash", "universe", "evidence_cutoff", "projected_entry_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_leader_tactics_v2_transition_intraday_visibility",
        table_name="leader_tactics_v2_state_transitions",
    )
    op.drop_column("leader_tactics_v2_state_transitions", "projected_entry_status")
    op.drop_column("leader_tactics_v2_state_transitions", "evidence_cutoff")
