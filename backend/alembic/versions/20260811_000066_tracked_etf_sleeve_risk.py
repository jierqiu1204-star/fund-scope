"""add append-only tracked ETF sleeve ledger and daily risk snapshots

Revision ID: 20260811_000066
Revises: 20260811_000065
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260811_000066"
down_revision = "20260811_000065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("etf_trading_capital_confirmed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "tracked_etf_sleeve_ledger_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tracked_position_id", sa.Integer(), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("asset_code", sa.String(length=32), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("cash_delta", sa.Float(), nullable=True),
        sa.Column("cash_balance_after", sa.Float(), nullable=True),
        sa.Column("quantity_delta", sa.Float(), nullable=True),
        sa.Column("quantity_after", sa.Float(), nullable=True),
        sa.Column("execution_price", sa.Float(), nullable=True),
        sa.Column("fees", sa.Float(), nullable=False),
        sa.Column("adjustment_factor", sa.Float(), nullable=False),
        sa.Column("holdings_after_json", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("evidence_ref", sa.String(length=255), nullable=True),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("predecessor_event_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "sequence_no >= 1",
            name="ck_tracked_etf_sleeve_ledger_sequence",
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'opening_reconciliation','reconciliation','cash_deposit','cash_withdrawal',"
            "'buy','sell','distribution','fee','quantity_adjustment')",
            name="ck_tracked_etf_sleeve_ledger_event_type",
        ),
        sa.CheckConstraint(
            "provenance IN ("
            "'owner_confirmed','owner_documented','broker_verified','provider_verified')",
            name="ck_tracked_etf_sleeve_ledger_provenance",
        ),
        sa.CheckConstraint(
            "(event_type NOT IN ('opening_reconciliation','reconciliation',"
            "'cash_deposit','cash_withdrawal','buy','sell','fee') "
            "OR provenance IN ('owner_confirmed','owner_documented','broker_verified'))",
            name="ck_tracked_etf_sleeve_ledger_account_provenance",
        ),
        sa.CheckConstraint(
            "(provenance = 'owner_confirmed' OR evidence_ref IS NOT NULL)",
            name="ck_tracked_etf_sleeve_ledger_external_evidence",
        ),
        sa.CheckConstraint(
            "cash_balance_after IS NULL OR cash_balance_after >= 0",
            name="ck_tracked_etf_sleeve_ledger_cash_balance",
        ),
        sa.CheckConstraint(
            "quantity_after IS NULL OR quantity_after >= 0",
            name="ck_tracked_etf_sleeve_ledger_quantity_after",
        ),
        sa.CheckConstraint(
            "execution_price IS NULL OR execution_price > 0",
            name="ck_tracked_etf_sleeve_ledger_execution_price",
        ),
        sa.CheckConstraint("fees >= 0", name="ck_tracked_etf_sleeve_ledger_fees"),
        sa.CheckConstraint(
            "adjustment_factor > 0",
            name="ck_tracked_etf_sleeve_ledger_adjustment_factor",
        ),
        sa.CheckConstraint(
            "(event_type NOT IN ('opening_reconciliation','reconciliation') "
            "OR cash_balance_after IS NOT NULL)",
            name="ck_tracked_etf_sleeve_ledger_reconciliation_payload",
        ),
        sa.CheckConstraint(
            "(event_type != 'buy' OR (tracked_position_id IS NOT NULL "
            "AND asset_code IS NOT NULL AND quantity_delta > 0 AND quantity_after >= 0 "
            "AND cash_delta < 0 AND execution_price > 0))",
            name="ck_tracked_etf_sleeve_ledger_buy_payload",
        ),
        sa.CheckConstraint(
            "(event_type != 'sell' OR (tracked_position_id IS NOT NULL "
            "AND asset_code IS NOT NULL AND quantity_delta < 0 AND quantity_after >= 0 "
            "AND cash_delta > 0 AND execution_price > 0))",
            name="ck_tracked_etf_sleeve_ledger_sell_payload",
        ),
        sa.CheckConstraint(
            "(event_type != 'cash_deposit' OR cash_delta > 0) "
            "AND (event_type != 'cash_withdrawal' OR cash_delta < 0) "
            "AND (event_type != 'distribution' OR (asset_code IS NOT NULL AND cash_delta > 0)) "
            "AND (event_type != 'fee' OR cash_delta < 0)",
            name="ck_tracked_etf_sleeve_ledger_cash_event_payload",
        ),
        sa.CheckConstraint(
            "(event_type != 'quantity_adjustment' OR (tracked_position_id IS NOT NULL "
            "AND asset_code IS NOT NULL AND quantity_delta IS NOT NULL "
            "AND quantity_delta != 0 AND quantity_after >= 0))",
            name="ck_tracked_etf_sleeve_ledger_quantity_event_payload",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tracked_position_id"],
            ["tracked_positions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_event_hash"],
            ["tracked_etf_sleeve_ledger_events.event_hash"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "sequence_no",
            name="uq_tracked_etf_sleeve_ledger_sequence",
        ),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_tracked_etf_sleeve_ledger_idempotency",
        ),
        sa.UniqueConstraint(
            "event_hash",
            name="uq_tracked_etf_sleeve_ledger_event_hash",
        ),
    )
    op.create_index(
        "ix_tracked_etf_sleeve_ledger_owner_effective",
        "tracked_etf_sleeve_ledger_events",
        ["user_id", "effective_date", "sequence_no"],
    )
    op.create_index(
        "ix_tracked_etf_sleeve_ledger_position",
        "tracked_etf_sleeve_ledger_events",
        ["tracked_position_id", "sequence_no"],
    )

    op.create_table(
        "tracked_etf_sleeve_daily_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("cash_balance", sa.Float(), nullable=True),
        sa.Column("market_value", sa.Float(), nullable=True),
        sa.Column("equity", sa.Float(), nullable=True),
        sa.Column("net_external_flow", sa.Float(), nullable=False),
        sa.Column("flow_adjusted_nav", sa.Float(), nullable=True),
        sa.Column("high_water_nav", sa.Float(), nullable=True),
        sa.Column("drawdown_pct", sa.Float(), nullable=True),
        sa.Column("holding_count", sa.Integer(), nullable=False),
        sa.Column("valued_holding_count", sa.Integer(), nullable=False),
        sa.Column("ledger_coverage_ratio", sa.Float(), nullable=False),
        sa.Column("valuation_coverage_ratio", sa.Float(), nullable=False),
        sa.Column("coverage_state", sa.String(length=32), nullable=False),
        sa.Column("risk_state", sa.String(length=32), nullable=False),
        sa.Column("recovery_streak", sa.Integer(), nullable=False),
        sa.Column("cooldown_sessions_remaining", sa.Integer(), nullable=False),
        sa.Column("signal_stop_cycle_count", sa.Integer(), nullable=False),
        sa.Column("confirmed_stop_cycle_count", sa.Integer(), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False),
        sa.Column("ledger_head_event_hash", sa.String(length=64), nullable=True),
        sa.Column("input_contract_hash", sa.String(length=64), nullable=False),
        sa.Column("nav_contract_hash", sa.String(length=64), nullable=False),
        sa.Column("risk_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("predecessor_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "sequence_no >= 1",
            name="ck_tracked_etf_sleeve_snapshot_sequence",
        ),
        sa.CheckConstraint(
            "coverage_state IN ('eligible','unavailable')",
            name="ck_tracked_etf_sleeve_snapshot_coverage_state",
        ),
        sa.CheckConstraint(
            "risk_state IN ('normal','reduce_only','data_halt')",
            name="ck_tracked_etf_sleeve_snapshot_risk_state",
        ),
        sa.CheckConstraint(
            "cash_balance IS NULL OR cash_balance >= 0",
            name="ck_tracked_etf_sleeve_snapshot_cash",
        ),
        sa.CheckConstraint(
            "market_value IS NULL OR market_value >= 0",
            name="ck_tracked_etf_sleeve_snapshot_market_value",
        ),
        sa.CheckConstraint(
            "equity IS NULL OR equity >= 0",
            name="ck_tracked_etf_sleeve_snapshot_equity",
        ),
        sa.CheckConstraint(
            "flow_adjusted_nav IS NULL OR flow_adjusted_nav > 0",
            name="ck_tracked_etf_sleeve_snapshot_nav",
        ),
        sa.CheckConstraint(
            "high_water_nav IS NULL OR high_water_nav > 0",
            name="ck_tracked_etf_sleeve_snapshot_high_water",
        ),
        sa.CheckConstraint(
            "drawdown_pct IS NULL OR (drawdown_pct >= -1 AND drawdown_pct <= 0)",
            name="ck_tracked_etf_sleeve_snapshot_drawdown",
        ),
        sa.CheckConstraint(
            "holding_count >= 0 AND valued_holding_count >= 0 "
            "AND valued_holding_count <= holding_count",
            name="ck_tracked_etf_sleeve_snapshot_counts",
        ),
        sa.CheckConstraint(
            "ledger_coverage_ratio >= 0 AND ledger_coverage_ratio <= 1 "
            "AND valuation_coverage_ratio >= 0 AND valuation_coverage_ratio <= 1",
            name="ck_tracked_etf_sleeve_snapshot_coverage_ratios",
        ),
        sa.CheckConstraint(
            "recovery_streak >= 0 AND signal_stop_cycle_count >= 0 "
            "AND cooldown_sessions_remaining >= 0 "
            "AND confirmed_stop_cycle_count >= 0 "
            "AND confirmed_stop_cycle_count <= signal_stop_cycle_count",
            name="ck_tracked_etf_sleeve_snapshot_risk_counters",
        ),
        sa.CheckConstraint(
            "(coverage_state != 'eligible' OR (cash_balance IS NOT NULL "
            "AND market_value IS NOT NULL AND equity IS NOT NULL "
            "AND flow_adjusted_nav IS NOT NULL AND high_water_nav IS NOT NULL "
            "AND (risk_state = 'data_halt' OR drawdown_pct IS NOT NULL) "
            "AND ledger_head_event_hash IS NOT NULL "
            "AND ledger_coverage_ratio = 1 AND valuation_coverage_ratio = 1 "
            "AND valued_holding_count = holding_count))",
            name="ck_tracked_etf_sleeve_snapshot_eligible_payload",
        ),
        sa.CheckConstraint(
            "(coverage_state != 'unavailable' OR (risk_state = 'data_halt' "
            "AND equity IS NULL AND flow_adjusted_nav IS NULL "
            "AND high_water_nav IS NULL AND drawdown_pct IS NULL))",
            name="ck_tracked_etf_sleeve_snapshot_unavailable_payload",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["ledger_head_event_hash"],
            ["tracked_etf_sleeve_ledger_events.event_hash"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_snapshot_hash"],
            ["tracked_etf_sleeve_daily_snapshots.snapshot_hash"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "sequence_no",
            name="uq_tracked_etf_sleeve_snapshot_sequence",
        ),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_tracked_etf_sleeve_snapshot_idempotency",
        ),
        sa.UniqueConstraint(
            "snapshot_hash",
            name="uq_tracked_etf_sleeve_snapshot_hash",
        ),
    )
    op.create_index(
        "ix_tracked_etf_sleeve_snapshot_owner_date",
        "tracked_etf_sleeve_daily_snapshots",
        ["user_id", "snapshot_date", "sequence_no"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tracked_etf_sleeve_snapshot_owner_date",
        table_name="tracked_etf_sleeve_daily_snapshots",
    )
    op.drop_table("tracked_etf_sleeve_daily_snapshots")
    op.drop_index(
        "ix_tracked_etf_sleeve_ledger_position",
        table_name="tracked_etf_sleeve_ledger_events",
    )
    op.drop_index(
        "ix_tracked_etf_sleeve_ledger_owner_effective",
        table_name="tracked_etf_sleeve_ledger_events",
    )
    op.drop_table("tracked_etf_sleeve_ledger_events")
    op.drop_column("users", "etf_trading_capital_confirmed_at")
