"""Add email authentication and user scoped tracking."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260617_000017"
down_revision = "20260615_000016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=512), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("is_super_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))

    op.execute(
        """
        UPDATE users
        SET
            email = '19535838578@163.com',
            recipient_email = '19535838578@163.com',
            display_name = 'qje',
            is_approved = true,
            is_super_admin = true
        WHERE id = 1
        """
    )

    op.add_column("tracked_positions", sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute("UPDATE tracked_positions SET user_id = 1 WHERE user_id IS NULL")
    op.alter_column("tracked_positions", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_tracked_positions_user_id",
        "tracked_positions",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_tracked_positions_user_status",
        "tracked_positions",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_tracked_positions_user_status", table_name="tracked_positions")
    op.drop_constraint("fk_tracked_positions_user_id", "tracked_positions", type_="foreignkey")
    op.drop_column("tracked_positions", "user_id")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_super_admin")
    op.drop_column("users", "is_approved")
    op.drop_column("users", "display_name")
    op.drop_column("users", "password_hash")
