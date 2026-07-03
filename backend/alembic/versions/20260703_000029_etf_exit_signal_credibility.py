"""add etf exit signal credibility evidence tables

Revision ID: 20260703_000029
Revises: 20260702_000028
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260703_000029"
down_revision: str | None = "20260702_000028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_exit_signal_credibility_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("execution_model", sa.String(length=32), nullable=False),
        sa.Column("signal_version", sa.String(length=64), nullable=False),
        sa.Column("exit_rule_version", sa.String(length=64), nullable=False),
        sa.Column("contract_hash", sa.String(length=128), nullable=True),
        sa.Column("evidence_status", sa.String(length=32), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(), nullable=True),
        sa.Column("data_window_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("insufficiency_reasons_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_exit_cred_runs_status_started",
        "etf_exit_signal_credibility_runs",
        ["status", "started_at"],
    )
    op.create_index(
        "ix_etf_exit_cred_runs_model_finished",
        "etf_exit_signal_credibility_runs",
        ["execution_model", "finished_at"],
    )

    op.create_table(
        "etf_exit_signal_credibility_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("group_type", sa.String(length=32), nullable=False),
        sa.Column("group_key", sa.String(length=128), nullable=False),
        sa.Column("evidence_level", sa.String(length=32), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("success_avoidance_rate", sa.Float(), nullable=True),
        sa.Column("false_stop_rate", sa.Float(), nullable=True),
        sa.Column("sold_too_early_rate", sa.Float(), nullable=True),
        sa.Column("avg_avoided_drawdown", sa.Float(), nullable=True),
        sa.Column("avg_missed_upside", sa.Float(), nullable=True),
        sa.Column("avg_forward_return", sa.Float(), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["etf_exit_signal_credibility_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_exit_cred_items_run_signal_group",
        "etf_exit_signal_credibility_items",
        ["run_id", "signal_type", "group_type", "group_key"],
    )

    op.create_table(
        "etf_exit_signal_credibility_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("etf_code", sa.String(length=32), nullable=False),
        sa.Column("etf_name", sa.String(length=255), nullable=True),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("signal_time", sa.DateTime(), nullable=True),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("signal_price", sa.Float(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("forward_window_days", sa.Integer(), nullable=False),
        sa.Column("forward_return", sa.Float(), nullable=True),
        sa.Column("max_favorable_return", sa.Float(), nullable=True),
        sa.Column("max_adverse_return", sa.Float(), nullable=True),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["etf_exit_signal_credibility_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["etf_exit_signal_credibility_items.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_etf_exit_cred_events_run_signal",
        "etf_exit_signal_credibility_events",
        ["run_id", "signal_type", "signal_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_etf_exit_cred_events_run_signal", table_name="etf_exit_signal_credibility_events")
    op.drop_table("etf_exit_signal_credibility_events")
    op.drop_index("ix_etf_exit_cred_items_run_signal_group", table_name="etf_exit_signal_credibility_items")
    op.drop_table("etf_exit_signal_credibility_items")
    op.drop_index("ix_etf_exit_cred_runs_model_finished", table_name="etf_exit_signal_credibility_runs")
    op.drop_index("ix_etf_exit_cred_runs_status_started", table_name="etf_exit_signal_credibility_runs")
    op.drop_table("etf_exit_signal_credibility_runs")
