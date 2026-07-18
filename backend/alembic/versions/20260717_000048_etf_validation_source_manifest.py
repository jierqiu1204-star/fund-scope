"""add ETF validation source manifests

Revision ID: 20260717_000048
Revises: 20260715_000047
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_000048"
down_revision: str | None = "20260715_000047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("etf_signal_validation_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_etf_validation_ranking_source_identity",
            type_="check",
        )
        batch_op.add_column(
            sa.Column("source_manifest_hash", sa.String(128), nullable=True),
        )
        batch_op.add_column(
            sa.Column("source_event_count", sa.Integer(), nullable=True),
        )
        batch_op.create_index(
            "ix_etf_signal_validation_runs_source_manifest_hash",
            ["source_manifest_hash"],
        )
        batch_op.create_unique_constraint(
            "uq_etf_validation_run_manifest_kind",
            ["id", "ranking_source_kind"],
        )
        batch_op.create_check_constraint(
            "ck_etf_validation_ranking_source_identity",
            "status <> 'success' OR ranking_source_kind IS NULL OR "
            "(source_manifest_hash IS NOT NULL AND source_event_count > 0 AND "
            "((ranking_source_kind = 'research_replay' AND "
            "source_replay_run_key IS NOT NULL AND source_signal_run_id IS NULL) OR "
            "(ranking_source_kind = 'production_published' AND "
            "source_replay_run_key IS NULL)))",
        )

    op.create_table(
        "etf_signal_validation_source_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "validation_run_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("event_order", sa.Integer(), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("ranking_source_kind", sa.String(32), nullable=False),
        sa.Column("source_signal_run_id", sa.Integer(), nullable=True),
        sa.Column("source_replay_run_key", sa.String(128), nullable=True),
        sa.Column("source_replay_contract_hash", sa.String(128), nullable=True),
        sa.Column("source_event_hash", sa.String(128), nullable=False),
        sa.Column("ranking_contract_hash", sa.String(128), nullable=False),
        sa.Column("scope_hash", sa.String(128), nullable=False),
        sa.Column("universe_snapshot_hash", sa.String(128), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(128), nullable=False),
        sa.Column("availability_cutoff", sa.DateTime(), nullable=False),
        sa.Column("score_version", sa.String(64), nullable=False),
        sa.Column("score_field", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("price_basis", sa.String(64), nullable=False),
        sa.Column("publication_state", sa.String(32), nullable=True),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("source_status", sa.String(32), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("expected_asset_count", sa.Integer(), nullable=True),
        sa.Column("decision_data_covered_count", sa.Integer(), nullable=True),
        sa.Column("eligible_asset_count", sa.Integer(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=True),
        sa.Column("decision_data_coverage_ratio", sa.Float(), nullable=True),
        sa.Column("score_coverage_ratio", sa.Float(), nullable=True),
        sa.Column("etf_item_count", sa.Integer(), nullable=True),
        sa.Column("finite_eligible_score_count", sa.Integer(), nullable=True),
        sa.Column("contiguous_global_rank", sa.Boolean(), nullable=True),
        sa.Column("immutable_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "validation_run_id",
            "event_order",
            name="uq_etf_validation_source_event_order",
        ),
        sa.UniqueConstraint(
            "validation_run_id",
            "source_date",
            name="uq_etf_validation_source_event_date",
        ),
        sa.UniqueConstraint(
            "validation_run_id",
            "immutable_hash",
            name="uq_etf_validation_source_event_hash",
        ),
        sa.ForeignKeyConstraint(
            ["validation_run_id", "ranking_source_kind"],
            [
                "etf_signal_validation_runs.id",
                "etf_signal_validation_runs.ranking_source_kind",
            ],
            name="fk_etf_validation_source_event_manifest_kind",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "ranking_source_kind IN ('production_published', 'research_replay')",
            name="ck_etf_validation_source_event_kind",
        ),
        sa.CheckConstraint(
            "(ranking_source_kind = 'production_published' AND "
            "source_signal_run_id IS NOT NULL AND source_replay_run_key IS NULL AND "
            "source_replay_contract_hash IS NULL) OR "
            "(ranking_source_kind = 'research_replay' AND "
            "source_signal_run_id IS NULL AND source_replay_run_key IS NOT NULL AND "
            "source_replay_contract_hash IS NOT NULL)",
            name="ck_etf_validation_source_event_identity",
        ),
        sa.CheckConstraint(
            "event_order >= 0",
            name="ck_etf_validation_source_event_order",
        ),
    )
    op.create_index(
        "ix_etf_validation_source_events_source_date",
        "etf_signal_validation_source_events",
        ["ranking_source_kind", "source_date"],
    )
    op.create_index(
        "ix_etf_validation_source_events_signal_run",
        "etf_signal_validation_source_events",
        ["source_signal_run_id"],
    )
    op.create_index(
        "ix_etf_validation_source_events_replay_key",
        "etf_signal_validation_source_events",
        ["source_replay_run_key"],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE etf_signal_validation_runs "
            "SET ranking_source_kind = NULL, source_signal_run_id = NULL, "
            "source_replay_run_key = NULL "
            "WHERE source_manifest_hash IS NOT NULL"
        )
    )
    op.drop_index(
        "ix_etf_validation_source_events_replay_key",
        table_name="etf_signal_validation_source_events",
    )
    op.drop_index(
        "ix_etf_validation_source_events_signal_run",
        table_name="etf_signal_validation_source_events",
    )
    op.drop_index(
        "ix_etf_validation_source_events_source_date",
        table_name="etf_signal_validation_source_events",
    )
    op.drop_table("etf_signal_validation_source_events")

    with op.batch_alter_table("etf_signal_validation_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_etf_validation_ranking_source_identity",
            type_="check",
        )
        batch_op.drop_index("ix_etf_signal_validation_runs_source_manifest_hash")
        batch_op.drop_constraint(
            "uq_etf_validation_run_manifest_kind",
            type_="unique",
        )
        batch_op.drop_column("source_event_count")
        batch_op.drop_column("source_manifest_hash")
        batch_op.create_check_constraint(
            "ck_etf_validation_ranking_source_identity",
            "status <> 'success' OR ranking_source_kind IS NULL OR "
            "(ranking_source_kind = 'research_replay' AND "
            "source_replay_run_key IS NOT NULL AND source_signal_run_id IS NULL) OR "
            "(ranking_source_kind = 'production_published' AND "
            "source_signal_run_id IS NOT NULL AND source_replay_run_key IS NULL)",
        )
