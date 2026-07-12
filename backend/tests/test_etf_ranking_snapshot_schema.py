from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint

from app.db.base import Base
from app.models.entities import (
    EtfPriceHistory,
    EtfSignalValidationRun,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
)
from app.schemas.short_research import (
    EtfSignalValidationRunOut,
    ShortResearchAssetOut,
    ShortResearchSignalRunOut,
)


def test_signal_run_exposes_nullable_typed_snapshot_identity() -> None:
    table = ShortResearchSignalRun.__table__
    expected_types = {
        "scope_kind": String,
        "scope_hash": String,
        "universe_snapshot_hash": String,
        "input_snapshot_hash": String,
        "score_version": String,
        "rule_version": String,
        "ranking_contract_hash": String,
        "score_field": String,
        "data_cutoff": DateTime,
        "as_of_trade_date": Date,
        "price_basis": String,
        "expected_item_count": Integer,
        "eligible_item_count": Integer,
        "coverage_ratio": Float,
        "publication_state": String,
        "published_at": DateTime,
        "idempotency_key": String,
    }

    for column_name, expected_type in expected_types.items():
        column = table.c[column_name]
        assert column.nullable is True
        assert isinstance(column.type, expected_type)


def test_signal_run_snapshot_selectors_are_indexed_and_idempotent() -> None:
    table = ShortResearchSignalRun.__table__
    indexes = {index.name: index for index in table.indexes}

    selector = indexes["ix_short_research_signal_runs_canonical_snapshot"]
    assert [column.name for column in selector.columns] == [
        "scope_kind",
        "score_version",
        "ranking_contract_hash",
        "as_of_trade_date",
        "price_basis",
        "publication_state",
    ]

    idempotency = indexes["ux_short_research_signal_runs_idempotency_key"]
    assert [column.name for column in idempotency.columns] == ["idempotency_key"]
    assert idempotency.unique is True


def test_signal_item_exposes_nullable_current_ranking_fields() -> None:
    table = ShortResearchSignalItem.__table__
    expected_types = {
        "ranking_score": Float,
        "score_eligible": Boolean,
        "global_rank": Integer,
    }

    for column_name, expected_type in expected_types.items():
        column = table.c[column_name]
        assert column.nullable is True
        assert isinstance(column.type, expected_type)


def test_signal_item_run_id_remains_the_source_snapshot_identity() -> None:
    table = ShortResearchSignalItem.__table__
    foreign_keys = table.c.run_id.foreign_keys
    assert len(foreign_keys) == 1
    assert next(iter(foreign_keys)).target_fullname == "short_research_signal_runs.id"

    indexes = {index.name: index for index in table.indexes}
    global_rank_index = indexes["ix_short_research_signal_items_run_global_rank"]
    assert [column.name for column in global_rank_index.columns] == ["run_id", "global_rank"]


def test_etf_daily_history_keeps_research_price_provenance_separate_from_raw_ohlc() -> None:
    table = EtfPriceHistory.__table__
    expected_types = {
        "raw_price_basis": String,
        "research_adjusted_value": Float,
        "research_price_basis": String,
        "data_provider": String,
        "provider_version": String,
        "source_timestamp": DateTime,
        "adjustment_version": String,
        "decision_eligible": Boolean,
        "decision_ineligibility_reason": String,
    }

    for column_name, expected_type in expected_types.items():
        column = table.c[column_name]
        assert column.nullable is True
        assert isinstance(column.type, expected_type)

    assert table.c.close.nullable is False
    indexes = {index.name: index for index in table.indexes}
    eligibility_index = indexes["ix_etf_price_history_trade_date_decision_eligible"]
    assert [column.name for column in eligibility_index.columns] == ["trade_date", "decision_eligible"]


def test_etf_universe_membership_tracks_effective_history_and_provenance() -> None:
    table = Base.metadata.tables["etf_universe_memberships"]
    expected_columns = {
        "etf_code": (String, False),
        "effective_from": (Date, False),
        "effective_to": (Date, True),
        "source": (String, False),
        "tracked_underlying_id": (String, True),
        "exclusion_reason": (Text, True),
    }

    for column_name, (expected_type, nullable) in expected_columns.items():
        column = table.c[column_name]
        assert column.nullable is nullable
        assert isinstance(column.type, expected_type)

    foreign_keys = table.c.etf_code.foreign_keys
    assert len(foreign_keys) == 1
    assert next(iter(foreign_keys)).target_fullname == "tradable_etfs.code"

    unique_constraints = {
        constraint.name: constraint for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    }
    interval_key = unique_constraints["uq_etf_universe_membership_effective_from"]
    assert [column.name for column in interval_key.columns] == ["etf_code", "effective_from"]


def test_validation_run_exposes_nullable_source_ranking_identity() -> None:
    table = EtfSignalValidationRun.__table__
    expected_types = {
        "source_ranking_contract_hash": String,
        "source_scope_kind": String,
        "source_scope_hash": String,
        "source_universe_snapshot_hash": String,
        "source_input_snapshot_hash": String,
        "source_score_field": String,
        "source_score_version": String,
        "source_rule_version": String,
        "price_basis": String,
        "execution_model": String,
        "data_cutoff": DateTime,
    }

    for column_name, expected_type in expected_types.items():
        column = table.c[column_name]
        assert column.nullable is True
        assert isinstance(column.type, expected_type)

    indexes = {index.name: index for index in table.indexes}
    contract_index = indexes["ix_etf_signal_validation_runs_source_contract"]
    assert [column.name for column in contract_index.columns] == [
        "source_ranking_contract_hash",
        "source_scope_kind",
        "source_universe_snapshot_hash",
        "source_score_field",
        "price_basis",
    ]


def test_api_contracts_keep_new_snapshot_fields_nullable() -> None:
    expected_run_fields = {
        "scope_kind",
        "scope_hash",
        "universe_snapshot_hash",
        "input_snapshot_hash",
        "score_version",
        "rule_version",
        "ranking_contract_hash",
        "score_field",
        "data_cutoff",
        "as_of_trade_date",
        "price_basis",
        "expected_item_count",
        "eligible_item_count",
        "coverage_ratio",
        "publication_state",
        "published_at",
        "idempotency_key",
    }
    expected_asset_fields = {"ranking_score", "score_eligible", "global_rank"}
    expected_validation_fields = {
        "source_ranking_contract_hash",
        "source_scope_kind",
        "source_scope_hash",
        "source_universe_snapshot_hash",
        "source_input_snapshot_hash",
        "source_score_field",
        "source_score_version",
        "source_rule_version",
        "price_basis",
        "execution_model",
        "data_cutoff",
    }

    for field_name in expected_run_fields:
        assert ShortResearchSignalRunOut.model_fields[field_name].default is None
    for field_name in expected_asset_fields:
        assert ShortResearchAssetOut.model_fields[field_name].default is None
    for field_name in expected_validation_fields:
        assert EtfSignalValidationRunOut.model_fields[field_name].default is None
