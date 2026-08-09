"""Protect intraday decision evidence and checkpoint bounded retention."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260810_000064"
down_revision = "20260809_000063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "etf_intraday_quote_evidence_refs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=True),
        sa.Column("owner_kind", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=False),
        sa.Column("evidence_purpose", sa.String(length=64), nullable=False),
        sa.Column("evidence_state", sa.String(length=32), nullable=False),
        sa.Column("quote_hash", sa.String(length=128), nullable=True),
        sa.Column("decision_cutoff", sa.DateTime(), nullable=True),
        sa.Column("receipt_cutoff", sa.DateTime(), nullable=True),
        sa.Column("unavailable_reason", sa.String(length=128), nullable=True),
        sa.Column("protected_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "evidence_state IN ('protected', 'unavailable')",
            name="ck_etf_intraday_quote_evidence_state",
        ),
        sa.CheckConstraint(
            "(evidence_state = 'protected' AND quote_id IS NOT NULL "
            "AND quote_hash IS NOT NULL AND unavailable_reason IS NULL) OR "
            "(evidence_state = 'unavailable' AND quote_id IS NULL "
            "AND quote_hash IS NULL AND unavailable_reason IS NOT NULL)",
            name="ck_etf_intraday_quote_evidence_payload",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["etf_intraday_quotes.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_kind",
            "owner_id",
            "asset_code",
            "evidence_purpose",
            name="uq_etf_intraday_quote_evidence_owner",
        ),
    )
    op.create_index(
        "ix_etf_intraday_quote_evidence_quote_id",
        "etf_intraday_quote_evidence_refs",
        ["quote_id"],
    )
    op.create_index(
        "ix_etf_intraday_quote_evidence_owner_lookup",
        "etf_intraday_quote_evidence_refs",
        ["owner_kind", "owner_id", "evidence_purpose"],
    )
    op.create_table(
        "etf_intraday_cleanup_checkpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cutoff_date", sa.Date(), nullable=True),
        sa.Column("last_trade_date", sa.Date(), nullable=True),
        sa.Column("last_etf_code", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("deleted_rows_total", sa.Integer(), nullable=False),
        sa.Column("summarized_groups_total", sa.Integer(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("etf_intraday_cleanup_checkpoints")
    op.drop_index(
        "ix_etf_intraday_quote_evidence_owner_lookup",
        table_name="etf_intraday_quote_evidence_refs",
    )
    op.drop_index(
        "ix_etf_intraday_quote_evidence_quote_id",
        table_name="etf_intraday_quote_evidence_refs",
    )
    op.drop_table("etf_intraday_quote_evidence_refs")
