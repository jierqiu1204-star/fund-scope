"""Add recommendation review tables."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260605_000009"
down_revision = "20260605_000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("recommendation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_recommendation_review_run"),
    )
    op.create_index("ix_recommendation_reviews_run_id", "recommendation_reviews", ["run_id"])
    op.create_table(
        "recommendation_review_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("review_id", sa.Integer(), sa.ForeignKey("recommendation_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "recommendation_item_id",
            sa.Integer(),
            sa.ForeignKey("recommendation_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("verdict", sa.String(length=64), nullable=False),
        sa.Column("agent_notes_json", sa.JSON(), nullable=False),
        sa.Column("risk_flags_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("review_id", "recommendation_item_id", name="uq_recommendation_review_item"),
    )
    op.create_index("ix_recommendation_review_items_review_id", "recommendation_review_items", ["review_id"])


def downgrade() -> None:
    op.drop_index("ix_recommendation_review_items_review_id", table_name="recommendation_review_items")
    op.drop_table("recommendation_review_items")
    op.drop_index("ix_recommendation_reviews_run_id", table_name="recommendation_reviews")
    op.drop_table("recommendation_reviews")
