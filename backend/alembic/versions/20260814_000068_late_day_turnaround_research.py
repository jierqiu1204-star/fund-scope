"""add dual-universe late-day-turnaround research evidence"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260814_000068"
down_revision = "20260812_000067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ashare_intraday_10m_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("bar_start", sa.DateTime(), nullable=False),
        sa.Column("bar_end", sa.DateTime(), nullable=False),
        sa.Column("raw_open", sa.Float(), nullable=False),
        sa.Column("raw_high", sa.Float(), nullable=False),
        sa.Column("raw_low", sa.Float(), nullable=False),
        sa.Column("raw_close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("normalization_factor", sa.Float(), nullable=False),
        sa.Column("normalization_identity", sa.String(128), nullable=False),
        sa.Column("decision_eligible", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.CheckConstraint("bar_end > bar_start", name="ck_ashare_intraday_10m_bar_order"),
        sa.CheckConstraint(
            "raw_open > 0 AND raw_high > 0 AND raw_low > 0 AND raw_close > 0",
            name="ck_ashare_intraday_10m_positive_prices",
        ),
        sa.CheckConstraint(
            "raw_high >= raw_open AND raw_high >= raw_close "
            "AND raw_low <= raw_open AND raw_low <= raw_close "
            "AND raw_low <= raw_high",
            name="ck_ashare_intraday_10m_ohlc",
        ),
        sa.CheckConstraint(
            "volume >= 0 AND amount >= 0 AND normalization_factor > 0",
            name="ck_ashare_intraday_10m_nonnegative_values",
        ),
        sa.UniqueConstraint("content_hash", name="uq_ashare_intraday_10m_content_hash"),
    )
    op.create_index(
        "ix_ashare_intraday_10m_pit_lookup",
        "ashare_intraday_10m_facts",
        ["trade_date", "asset_code", "bar_end", "received_at"],
    )
    op.create_table(
        "late_day_turnaround_capture_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("universe", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("checkpoint_at", sa.DateTime(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("cursor_asset_code", sa.String(32), nullable=True),
        sa.Column("expected_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("manifest_hash", sa.String(128), nullable=False),
        sa.Column("error_summary", sa.String(500), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "expected_count >= 0 AND completed_count >= 0 AND failed_count >= 0",
            name="ck_late_day_capture_nonnegative_counts",
        ),
        sa.UniqueConstraint(
            "universe",
            "trade_date",
            "checkpoint_at",
            "provider",
            name="uq_late_day_capture_checkpoint",
        ),
    )
    op.create_table(
        "late_day_turnaround_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("manifest_hash", sa.String(128), nullable=False),
        sa.Column("universe", sa.String(16), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("decision_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("policy_mode", sa.String(32), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=False),
        sa.Column("contract_hash", sa.String(128), nullable=False),
        sa.Column("universe_hash", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(128), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=False),
        sa.Column("evaluated_count", sa.Integer(), nullable=False),
        sa.Column("available_count", sa.Integer(), nullable=False),
        sa.Column("qualifying_count", sa.Integer(), nullable=False),
        sa.Column("provider_health_json", sa.JSON(), nullable=False),
        sa.Column("exclusion_counts_json", sa.JSON(), nullable=False),
        sa.Column("unavailable_reason", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("universe IN ('etf','ashare')", name="ck_late_day_run_universe"),
        sa.CheckConstraint(
            "expected_count >= 0 AND evaluated_count >= 0 "
            "AND available_count >= 0 AND qualifying_count >= 0",
            name="ck_late_day_run_nonnegative_counts",
        ),
        sa.UniqueConstraint("manifest_hash", name="uq_late_day_run_manifest_hash"),
        sa.UniqueConstraint(
            "universe", "decision_at", "contract_hash", name="uq_late_day_run_contract_cutoff"
        ),
    )
    op.create_index(
        "ix_late_day_run_lookup",
        "late_day_turnaround_runs",
        ["universe", "decision_at", "status"],
    )
    op.create_table(
        "late_day_turnaround_observations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("late_day_turnaround_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_code", sa.String(32), nullable=False),
        sa.Column("asset_name", sa.String(255), nullable=False),
        sa.Column("observation_kind", sa.String(32), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("ma5", sa.Float(), nullable=True),
        sa.Column("gain_pct", sa.Float(), nullable=True),
        sa.Column("ma_deviation_pct", sa.Float(), nullable=True),
        sa.Column("amount_ratio", sa.Float(), nullable=True),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("decision_at", sa.DateTime(), nullable=False),
        sa.Column("input_hash", sa.String(128), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "observation_kind IN ('formal_candidate','formal_exclusion','daily_proxy_watchlist')",
            name="ck_late_day_observation_kind",
        ),
        sa.UniqueConstraint("run_id", "asset_code", name="uq_late_day_observation_asset"),
    )
    op.create_index(
        "ix_late_day_observation_candidates",
        "late_day_turnaround_observations",
        ["run_id", "observation_kind", "available", "score", "asset_code"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_late_day_observation_candidates",
        table_name="late_day_turnaround_observations",
    )
    op.drop_table("late_day_turnaround_observations")
    op.drop_index("ix_late_day_run_lookup", table_name="late_day_turnaround_runs")
    op.drop_table("late_day_turnaround_runs")
    op.drop_table("late_day_turnaround_capture_checkpoints")
    op.drop_index(
        "ix_ashare_intraday_10m_pit_lookup",
        table_name="ashare_intraday_10m_facts",
    )
    op.drop_table("ashare_intraday_10m_facts")
