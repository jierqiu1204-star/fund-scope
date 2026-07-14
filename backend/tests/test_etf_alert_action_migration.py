from __future__ import annotations

import json
from datetime import date, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app.models.entities import TrackedPositionLifecycleShadowEvidence

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260714_000039_etf_alert_action_lifecycle.py"
)
RECEIPT_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260714_000040_action_transition_receipts.py"
)
NOTIFICATION_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260714_000041_notification_envelope_sealing.py"
)
SHADOW_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260714_000042_lifecycle_shadow_evidence.py"
)
SHADOW_STREAM_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260715_000044_lifecycle_shadow_stream.py"
)


def _migration():
    spec = spec_from_file_location("etf_alert_action_lifecycle_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _receipt_migration():
    spec = spec_from_file_location("action_transition_receipt_migration", RECEIPT_MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _notification_migration():
    spec = spec_from_file_location(
        "notification_envelope_sealing_migration", NOTIFICATION_MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shadow_migration():
    spec = spec_from_file_location("lifecycle_shadow_evidence_migration", SHADOW_MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shadow_stream_migration():
    spec = spec_from_file_location(
        "lifecycle_shadow_stream_migration", SHADOW_STREAM_MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_schema(connection: sa.Connection) -> None:
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "tracked_positions",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("exit_state_json", sa.JSON, nullable=False),
    )
    sa.Table(
        "tracked_position_alerts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tracked_position_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    sa.Table(
        "tracked_position_alert_audits",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tracked_position_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    metadata.create_all(connection)


def _expect_integrity_error(connection: sa.Connection, statement) -> None:
    savepoint = connection.begin_nested()
    try:
        with pytest.raises(IntegrityError):
            connection.execute(statement)
    finally:
        savepoint.rollback()


def _action_values(*, cycle: str = "cycle-1", stage: str = "remaining_5000bp", current: bool = True):
    now = datetime(2026, 7, 14, 15, 0)
    fraction = 0.5 if stage == "remaining_5000bp" else 0.0
    return {
        "user_id": 1,
        "tracked_position_id": 1,
        "position_episode_id": "position-episode-1",
        "exposure_version": 1,
        "policy_version": "policy-v1",
        "action_cycle_id": cycle,
        "target_stage": stage,
        "target_remaining_fraction": fraction,
        "baseline_normalized_quantity": 1000.0,
        "baseline_account_weight": 0.3,
        "baseline_adjustment_factor": 1.0,
        "baseline_source": "confirmed_shares",
        "target_normalized_quantity": 1000.0 * fraction,
        "target_account_weight": 0.3 * fraction,
        "input_snapshot_hash": "a" * 64,
        "data_state": "eligible",
        "status": "proposed",
        "execution_provenance": "none",
        "cumulative_executed_quantity": 0.0,
        "contributing_rules_json": ["trailing_take_profit"],
        "alert_episode_ids_json": ["alert-episode-1"],
        "is_current": current,
        "created_at": now,
        "updated_at": now,
    }


def test_additive_migration_preserves_legacy_rows_and_enforces_identities() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    migration = _migration()
    receipt_migration = _receipt_migration()
    notification_migration = _notification_migration()
    shadow_migration = _shadow_migration()

    with engine.begin() as connection:
        _legacy_schema(connection)
        connection.execute(sa.text("INSERT INTO users (id) VALUES (1)"))
        connection.execute(
            sa.text(
                "INSERT INTO tracked_positions (id, user_id, exit_state_json) "
                "VALUES (1, 1, :state)"
            ),
            {"state": '{"latest_position_action":{"position_action":"reduce"}}'},
        )
        connection.execute(
            sa.text(
                "INSERT INTO tracked_position_alerts (id, tracked_position_id, created_at) "
                "VALUES (1, 1, :created_at)"
            ),
            {"created_at": datetime(2026, 7, 13)},
        )
        connection.execute(
            sa.text(
                "INSERT INTO tracked_position_alert_audits (id, tracked_position_id, created_at) "
                "VALUES (1, 1, :created_at)"
            ),
            {"created_at": datetime(2026, 7, 13)},
        )

        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        receipt_migration.op = Operations(MigrationContext.configure(connection))
        receipt_migration.upgrade()
        notification_migration.op = Operations(MigrationContext.configure(connection))
        notification_migration.upgrade()
        shadow_migration.op = Operations(MigrationContext.configure(connection))
        shadow_migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "tracked_position_action_decisions",
            "tracked_position_action_executions",
            "tracked_position_action_transition_receipts",
            "tracked_position_notification_items",
            "tracked_position_notification_envelopes",
            "tracked_position_lifecycle_shadow_evidence",
        } <= set(inspector.get_table_names())
        migrated_position = connection.execute(
            sa.text("SELECT exit_state_version, exit_state_json FROM tracked_positions WHERE id = 1")
        ).one()
        assert migrated_position[0] == 0
        assert json.loads(migrated_position[1]) == {"latest_position_action": {"position_action": "reduce"}}
        assert connection.execute(
            sa.text("SELECT alert_episode_id, action_decision_id FROM tracked_position_alerts WHERE id = 1")
        ).one() == (None, None)
        alert_columns = {
            column["name"] for column in inspector.get_columns("tracked_position_alerts")
        }
        audit_columns = {
            column["name"]
            for column in inspector.get_columns("tracked_position_alert_audits")
        }
        audit_only_columns = {
            "from_state",
            "to_state",
            "actor_id",
            "request_id",
            "causation_id",
            "occurred_at",
            "execution_provenance",
        }
        assert alert_columns.isdisjoint(audit_only_columns)
        assert audit_only_columns <= audit_columns

        actions = sa.Table("tracked_position_action_decisions", sa.MetaData(), autoload_with=connection)
        first_action_id = connection.execute(actions.insert().values(**_action_values())).inserted_primary_key[0]
        _expect_integrity_error(
            connection,
            actions.insert().values(**_action_values(current=False)),
        )
        _expect_integrity_error(
            connection,
            actions.insert().values(
                **_action_values(cycle="cycle-2", stage="remaining_0bp", current=True)
            ),
        )
        invalid_fraction = _action_values(cycle="cycle-invalid", current=False)
        invalid_fraction["target_remaining_fraction"] = -0.1
        _expect_integrity_error(connection, actions.insert().values(**invalid_fraction))
        invalid_status = _action_values(cycle="cycle-invalid-status", current=False)
        invalid_status["status"] = "emailed"
        _expect_integrity_error(connection, actions.insert().values(**invalid_status))
        connection.execute(actions.update().where(actions.c.id == first_action_id).values(is_current=False))
        current_action_id = connection.execute(
            actions.insert().values(**_action_values(cycle="cycle-2", stage="remaining_0bp", current=True))
        ).inserted_primary_key[0]

        executions = sa.Table("tracked_position_action_executions", sa.MetaData(), autoload_with=connection)
        execution_values = {
            "action_decision_id": current_action_id,
            "user_id": 1,
            "tracked_position_id": 1,
            "idempotency_key": "execution-request-1",
            "request_hash": "b" * 64,
            "execution_provenance": "owner_confirmed",
            "execution_quantity": 1000.0,
            "execution_price": 1.2,
            "price_source": "owner_confirmation",
            "fees": 1.0,
            "before_normalized_quantity": 1000.0,
            "resulting_normalized_quantity": 0.0,
            "resulting_position_state_version": 2,
            "executed_at": datetime(2026, 7, 14, 15, 1),
            "actor_id": 1,
            "request_id": "request-1",
            "created_at": datetime(2026, 7, 14, 15, 1),
        }
        connection.execute(executions.insert().values(**execution_values))
        _expect_integrity_error(connection, executions.insert().values(**execution_values))

        receipts = sa.Table(
            "tracked_position_action_transition_receipts",
            sa.MetaData(),
            autoload_with=connection,
        )
        receipt_values = {
            "action_decision_id": current_action_id,
            "user_id": 1,
            "tracked_position_id": 1,
            "idempotency_key": "execution-request-1",
            "transition": "execute",
            "request_hash": "b" * 64,
            "response_json": {"action_status": "executed"},
            "created_at": datetime(2026, 7, 14, 15, 1),
        }
        connection.execute(receipts.insert().values(**receipt_values))
        _expect_integrity_error(connection, receipts.insert().values(**receipt_values))

        envelopes = sa.Table("tracked_position_notification_envelopes", sa.MetaData(), autoload_with=connection)
        assert {
            "template_name",
            "template_version",
            "rendered_content_hash",
            "attempt_count",
            "last_attempt_at",
        } <= set(envelopes.c.keys())
        envelope_values = {
            "user_id": 1,
            "trade_session": date(2026, 7, 14),
            "route": "ordinary_digest",
            "severity": "warning",
            "channel": "email",
            "sealed_snapshot_hash": "c" * 64,
            "digest_revision": 1,
            "status": "pending",
            "created_at": datetime(2026, 7, 14, 15, 2),
            "updated_at": datetime(2026, 7, 14, 15, 2),
        }
        envelope_id = connection.execute(envelopes.insert().values(**envelope_values)).inserted_primary_key[0]
        _expect_integrity_error(connection, envelopes.insert().values(**envelope_values))

        items = sa.Table("tracked_position_notification_items", sa.MetaData(), autoload_with=connection)
        assert {
            "status",
            "suppression_reason",
            "next_eligible_repeat_slot",
        } <= set(items.c.keys())
        item_values = {
            "user_id": 1,
            "tracked_position_id": 1,
            "action_decision_id": current_action_id,
            "alert_episode_id": "alert-episode-1",
            "transition": "firing",
            "recipient": "owner@example.com",
            "channel": "email",
            "repeat_slot": "2026-07-14:close",
            "route": "ordinary_digest",
            "severity": "warning",
            "payload_json": {},
            "envelope_id": envelope_id,
            "created_at": datetime(2026, 7, 14, 15, 3),
        }
        connection.execute(items.insert().values(**item_values))
        _expect_integrity_error(connection, items.insert().values(**item_values))

        shadow = sa.Table(
            "tracked_position_lifecycle_shadow_evidence",
            sa.MetaData(),
            autoload_with=connection,
        )
        shadow_values = {
            "user_id": 1,
            "tracked_position_id": 1,
            "policy_version": "policy-shadow-v1",
            "event_id": "d" * 64,
            "event_schema_version": "etf_alert_action_event_v1",
            "trade_session": date(2026, 7, 14),
            "repeat_slot": "2026-07-14:close",
            "sealed_snapshot_hash": "e" * 64,
            "production_position_state_version": 0,
            "rule_states_json": {},
            "transitions_json": [],
            "action_evidence_json": {},
            "data_state": "eligible",
            "occurred_at": datetime(2026, 7, 14, 15, 0),
            "created_at": datetime(2026, 7, 14, 15, 0),
        }
        connection.execute(shadow.insert().values(**shadow_values))
        _expect_integrity_error(connection, shadow.insert().values(**shadow_values))

        action_index_names = {
            index["name"] for index in inspector.get_indexes("tracked_position_action_decisions")
        }
        assert {"uq_tracked_action_current_slot", "ix_tracked_action_history"} <= action_index_names


def test_shadow_stream_migration_preserves_legacy_evidence_and_downgrades() -> None:
    engine = sa.create_engine("sqlite:///:memory:")
    migration = _migration()
    receipt_migration = _receipt_migration()
    notification_migration = _notification_migration()
    shadow_migration = _shadow_migration()
    shadow_stream_migration = _shadow_stream_migration()
    now = datetime(2026, 7, 14, 15, 0)

    with engine.begin() as connection:
        _legacy_schema(connection)
        connection.execute(sa.text("INSERT INTO users (id) VALUES (1)"))
        connection.execute(
            sa.text(
                "INSERT INTO tracked_positions (id, user_id, exit_state_json) "
                "VALUES (1, 1, :state)"
            ),
            {"state": "{}"},
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        receipt_migration.op = Operations(MigrationContext.configure(connection))
        receipt_migration.upgrade()
        notification_migration.op = Operations(MigrationContext.configure(connection))
        notification_migration.upgrade()
        shadow_migration.op = Operations(MigrationContext.configure(connection))
        shadow_migration.upgrade()

        connection.execute(
            sa.text(
                "INSERT INTO tracked_position_lifecycle_shadow_evidence "
                "(user_id, tracked_position_id, policy_version, event_id, "
                "event_schema_version, trade_session, repeat_slot, sealed_snapshot_hash, "
                "production_position_state_version, rule_states_json, transitions_json, "
                "action_evidence_json, data_state, occurred_at, created_at) VALUES "
                "(1, 1, 'legacy-shadow', :event_id, 'v1', :trade_session, 'close', "
                ":snapshot_hash, 0, '{}', '[]', '{}', 'eligible', :now, :now)"
            ),
            {
                "event_id": "1" * 64,
                "trade_session": date(2026, 7, 14),
                "snapshot_hash": "2" * 64,
                "now": now,
            },
        )

        shadow_stream_migration.op = Operations(MigrationContext.configure(connection))
        shadow_stream_migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("tracked_position_lifecycle_shadow_evidence")
        }
        expected_columns = set(TrackedPositionLifecycleShadowEvidence.__table__.columns.keys())
        assert expected_columns <= set(columns)
        assert columns["position_episode_id"]["nullable"] is False
        assert columns["exposure_version"]["nullable"] is False
        assert columns["stream_sequence"]["nullable"] is False
        legacy = connection.execute(
            sa.text(
                "SELECT position_episode_id, exposure_version, stream_sequence, "
                "predecessor_event_id FROM tracked_position_lifecycle_shadow_evidence"
            )
        ).one()
        assert legacy == ("legacy_shadow_unscoped", 0, 1, None)
        unique_names = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "tracked_position_lifecycle_shadow_evidence"
            )
        }
        assert "uq_tracked_lifecycle_shadow_stream_sequence" in unique_names
        history_index = next(
            index
            for index in inspector.get_indexes("tracked_position_lifecycle_shadow_evidence")
            if index["name"] == "ix_tracked_lifecycle_shadow_history"
        )
        expected_history_columns = [
            "user_id",
            "tracked_position_id",
            "policy_version",
            "position_episode_id",
            "exposure_version",
            "stream_sequence",
            "id",
        ]
        assert history_index["column_names"] == expected_history_columns
        model_history_index = next(
            index
            for index in TrackedPositionLifecycleShadowEvidence.__table__.indexes
            if index.name == "ix_tracked_lifecycle_shadow_history"
        )
        assert list(model_history_index.columns.keys()) == expected_history_columns

        shadow = sa.Table(
            "tracked_position_lifecycle_shadow_evidence",
            sa.MetaData(),
            autoload_with=connection,
        )
        values = {
            "user_id": 1,
            "tracked_position_id": 1,
            "policy_version": "policy-v2",
            "position_episode_id": "episode-1",
            "exposure_version": 1,
            "stream_sequence": 1,
            "predecessor_event_id": None,
            "event_id": "3" * 64,
            "event_schema_version": "v1",
            "trade_session": date(2026, 7, 15),
            "repeat_slot": "close",
            "sealed_snapshot_hash": "4" * 64,
            "production_position_state_version": 0,
            "rule_states_json": {},
            "transitions_json": [],
            "action_evidence_json": {},
            "data_state": "eligible",
            "occurred_at": now,
            "created_at": now,
        }
        connection.execute(shadow.insert().values(**values))
        _expect_integrity_error(
            connection,
            shadow.insert().values(**{**values, "event_id": "5" * 64}),
        )

        shadow_stream_migration.downgrade()
        downgraded_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "tracked_position_lifecycle_shadow_evidence"
            )
        }
        assert {
            "position_episode_id",
            "exposure_version",
            "stream_sequence",
            "predecessor_event_id",
        }.isdisjoint(downgraded_columns)
        downgraded_history_index = next(
            index
            for index in sa.inspect(connection).get_indexes(
                "tracked_position_lifecycle_shadow_evidence"
            )
            if index["name"] == "ix_tracked_lifecycle_shadow_history"
        )
        assert downgraded_history_index["column_names"] == [
            "user_id",
            "tracked_position_id",
            "policy_version",
            "created_at",
            "id",
        ]
        assert connection.execute(
            sa.text("SELECT count(*) FROM tracked_position_lifecycle_shadow_evidence")
        ).scalar_one() == 2
