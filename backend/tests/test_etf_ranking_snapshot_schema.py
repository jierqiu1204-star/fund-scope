from sqlalchemy import Date, DateTime, Float, Integer, String

from app.models.entities import ShortResearchSignalRun


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
