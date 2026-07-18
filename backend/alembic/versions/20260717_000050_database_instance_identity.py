"""Add immutable database instance identity for signed readiness.

Revision ID: 20260717_000050
Revises: 20260717_000049
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260717_000050"
down_revision = "20260717_000049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "database_instance_identity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instance_uuid", sa.String(length=36), nullable=False),
        sa.Column("declared_environment", sa.String(length=32), nullable=False),
        sa.Column("provisioned_at", sa.DateTime(), nullable=False),
        sa.Column("provisioned_by_deploy", sa.String(length=128), nullable=False),
        sa.Column("attestation_key_id", sa.String(length=128), nullable=False),
        sa.Column("creation_metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "id = 1",
            name="ck_database_instance_identity_singleton",
        ),
        sa.CheckConstraint(
            "declared_environment IN ('local', 'test', 'acceptance', 'production')",
            name="ck_database_instance_identity_environment",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_uuid"),
    )


def downgrade() -> None:
    op.drop_table("database_instance_identity")
