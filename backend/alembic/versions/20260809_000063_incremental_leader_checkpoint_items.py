"""Store leader-tactics checkpoint progress as bounded incremental rows."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260809_000063"
down_revision = "20260804_000062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows keep version 1 and continue to use their immutable legacy
    # JSON. Newly acquired checkpoints opt into version 2 in application code.
    op.add_column(
        "leader_tactics_v2_checkpoints",
        sa.Column(
            "storage_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_table(
        "leader_tactics_v2_checkpoint_items",
        sa.Column("manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("item_state", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint(
            "manifest_hash",
            "asset_code",
            name="pk_leader_tactics_v2_checkpoint_items",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_hash"],
            ["leader_tactics_v2_checkpoints.manifest_hash"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "item_state IN ('completed', 'failed')",
            name="ck_leader_tactics_v2_checkpoint_item_state",
        ),
    )
    op.create_index(
        "ix_leader_tactics_v2_checkpoint_item_state",
        "leader_tactics_v2_checkpoint_items",
        ["manifest_hash", "item_state", "asset_code"],
    )


def downgrade() -> None:
    # A downgrade deliberately discards only resumable research progress for
    # V2 rows. It cannot fabricate the legacy cumulative JSON safely.
    op.execute(
        """
        UPDATE leader_tactics_v2_checkpoints
        SET cursor = NULL,
            completed_count = 0,
            completed_hashes_json = '[]',
            failed_codes_json = '[]',
            status = 'paused',
            error_summary = NULL
        WHERE storage_version = 2
        """
    )
    op.drop_index(
        "ix_leader_tactics_v2_checkpoint_item_state",
        table_name="leader_tactics_v2_checkpoint_items",
    )
    op.drop_table("leader_tactics_v2_checkpoint_items")
    op.drop_column("leader_tactics_v2_checkpoints", "storage_version")
