from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from app.services.strategy_lab.etf_action_replay import features as features_module
from app.services.strategy_lab.etf_action_replay.features import (
    AdjustedDailyInput,
    BoundedWorkLimitError,
    FeatureBatchRequest,
    FeatureInputError,
    IncompleteWarmupError,
    compute_feature_batch,
    merge_feature_rows,
)


def _row(code: str, day: int, close: float) -> AdjustedDailyInput:
    return AdjustedDailyInput(
        asset_code=code,
        session_date=date(2026, 1, day),
        raw_open=close - 0.5,
        raw_high=close + 0.25,
        raw_low=close - 0.75,
        raw_close=close,
        volume=1_000_000.0,
        adjustment_factor=1.2,
        adjusted_data_source="eastmoney_total_return",
        adjustment_kind="total_return_adjusted",
        decision_eligible=True,
        provider_healthy=True,
        fresh_at_cutoff=True,
        known_at=datetime(2026, 1, day, 15, 0, tzinfo=UTC),
        source_cutoff=datetime(2026, 1, day, 15, 0, tzinfo=UTC),
    )


def _request(**overrides: object) -> FeatureBatchRequest:
    values: dict[str, object] = {
        "run_id": "run-1",
        "feature_contract_hash": "feature-contract-a",
        "input_snapshot_hash": "input-snapshot-a",
        "asset_codes": ("510001", "510002"),
        "start_date": date(2026, 1, 3),
        "end_date": date(2026, 1, 4),
        "data_cutoff": datetime(2026, 1, 10, 15, 30, tzinfo=UTC),
        "trading_sessions": tuple(date(2026, 1, day) for day in range(1, 11)),
        "decision_cutoffs": tuple(
            (
                date(2026, 1, day),
                datetime(2026, 1, day, 15, 30, tzinfo=UTC),
            )
            for day in range(1, 11)
        ),
        "warmup_sessions": 2,
        "max_source_rows": 100,
        "max_items": 100,
        "max_seconds": 55.0,
        "worker_count": 1,
    }
    values.update(overrides)
    return FeatureBatchRequest(**values)  # type: ignore[arg-type]


def test_stage_a_has_complete_warmup_stable_keys_and_adjusted_provenance() -> None:
    source_rows = [
        *[_row("510001", day, 10.0 + day) for day in range(1, 5)],
        *[_row("510002", day, 20.0 + day) for day in range(1, 5)],
    ]

    first = compute_feature_batch(
        source_rows=list(reversed(source_rows)),
        request=_request(),
        candidate_ids=("candidate-3", "candidate-2"),
    )
    second = compute_feature_batch(
        source_rows=source_rows,
        request=_request(),
        candidate_ids=("candidate-2", "candidate-3"),
    )

    assert first.rows == second.rows
    assert first.candidate_ids == ("candidate-2", "candidate-3")
    assert len(first.rows) == 4
    assert len({row.feature_key for row in first.rows}) == 4
    assert all(row.warmup_sessions == 2 for row in first.rows)
    assert all(row.adjusted_data_source == "eastmoney_total_return" for row in first.rows)
    assert all(row.source_cutoff <= _request().data_cutoff for row in first.rows)
    sample = next(
        row
        for row in first.rows
        if row.asset_code == "510001" and row.session_date == date(2026, 1, 3)
    )
    assert sample.adjusted_open == pytest.approx(12.5 * 1.2)
    assert sample.adjusted_close == pytest.approx(13.0 * 1.2)
    assert sample.momentum_return == pytest.approx(13.0 / 11.0 - 1.0)
    assert first.query_count == 1
    assert first.worker_count == 1
    assert first.batch_invocations == 1


def test_stage_a_fails_closed_when_indicator_warmup_is_incomplete() -> None:
    with pytest.raises(IncompleteWarmupError, match="510001"):
        compute_feature_batch(
            source_rows=[_row("510001", 2, 12.0), _row("510001", 3, 13.0)],
            request=_request(asset_codes=("510001",), start_date=date(2026, 1, 3)),
            candidate_ids=("candidate-2",),
        )


def test_stage_a_processes_one_bounded_batch_and_resumes_by_stable_key() -> None:
    rows = [
        *[_row("510001", day, 10.0 + day) for day in range(1, 5)],
        *[_row("510002", day, 20.0 + day) for day in range(1, 5)],
    ]
    first = compute_feature_batch(
        source_rows=rows,
        request=_request(max_items=1),
        candidate_ids=("candidate-2",),
    )
    assert first.processed_items == 1
    assert first.complete is False
    assert first.next_after_key == first.rows[-1].cursor_key

    second = compute_feature_batch(
        source_rows=rows,
        request=replace(_request(max_items=10), after_key=first.next_after_key),
        candidate_ids=("candidate-2",),
    )
    merged = merge_feature_rows(first.rows, second.rows)
    one_batch = compute_feature_batch(
        source_rows=rows,
        request=_request(),
        candidate_ids=("candidate-2",),
    )

    assert merged == one_batch.rows
    assert second.complete is True


def test_stage_a_does_not_materialize_the_full_code_date_cartesian_product(
    monkeypatch,
) -> None:
    original_tuple = tuple
    request = _request(
        asset_codes=("510001",),
        start_date=date(2026, 1, 3),
        end_date=date(2026, 1, 3),
        max_items=1,
    )
    precomputed = compute_feature_batch(
        source_rows=[_row("510001", day, 10.0 + day) for day in range(1, 4)],
        request=request,
        candidate_ids=("candidate-2",),
    ).rows[0]

    def guarded_tuple(value=()):
        if getattr(value, "gi_code", None) is not None:
            raise AssertionError("code/date cursor must stay lazy until max_items")
        return original_tuple(value)

    monkeypatch.setattr(features_module, "_target_sessions", lambda _request: (date(2026, 1, 3),))
    monkeypatch.setattr(features_module, "_feature_row", lambda **_kwargs: precomputed)
    monkeypatch.setattr(features_module, "tuple", guarded_tuple, raising=False)
    result = compute_feature_batch(
        source_rows=[_row("510001", day, 10.0 + day) for day in range(1, 4)],
        request=request,
        candidate_ids=("candidate-2",),
    )

    assert result.processed_items == 1


def test_stage_a_candidate_iterable_stops_at_registered_limit_plus_one() -> None:
    consumed = 0

    def candidates():
        nonlocal consumed
        for index in range(10):
            consumed += 1
            yield f"candidate-{index}"

    with pytest.raises(BoundedWorkLimitError, match="candidate_ids"):
        compute_feature_batch(
            source_rows=[_row("510001", day, 10.0 + day) for day in range(1, 4)],
            request=_request(
                asset_codes=("510001",),
                start_date=date(2026, 1, 3),
                end_date=date(2026, 1, 3),
                max_items=1,
            ),
            candidate_ids=candidates(),
        )

    assert consumed == 4


def test_stage_a_stops_after_current_item_when_time_budget_is_reached() -> None:
    ticks = iter((0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 2.0))
    result = compute_feature_batch(
        source_rows=[_row("510001", day, 10.0 + day) for day in range(1, 5)],
        request=_request(
            asset_codes=("510001",),
            max_seconds=1.0,
        ),
        candidate_ids=("candidate-2",),
        clock=lambda: next(ticks),
    )

    assert result.processed_items == 1
    assert result.complete is False
    assert result.elapsed_seconds == 2.0


def test_feature_merge_is_idempotent_and_rejects_revised_same_key() -> None:
    result = compute_feature_batch(
        source_rows=[_row("510001", day, 10.0 + day) for day in range(1, 5)],
        request=_request(asset_codes=("510001",)),
        candidate_ids=("candidate-2",),
    )

    assert merge_feature_rows(result.rows, result.rows) == result.rows
    revised = replace(result.rows[0], score=result.rows[0].score + 1.0)
    with pytest.raises(FeatureInputError, match="conflicting feature row"):
        merge_feature_rows(result.rows, (revised,))


def test_stage_a_fails_closed_when_a_requested_code_has_no_target_rows() -> None:
    with pytest.raises(FeatureInputError, match="510002"):
        compute_feature_batch(
            source_rows=[_row("510001", day, 10.0 + day) for day in range(1, 5)],
            request=_request(),
            candidate_ids=("candidate-2",),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("worker_count", 2), ("max_seconds", 55.1), ("max_seconds", 0.0), ("max_items", 0)],
)
def test_stage_a_enforces_single_worker_and_hard_work_bounds(field: str, value: object) -> None:
    with pytest.raises(BoundedWorkLimitError):
        _request(**{field: value})


def test_stage_a_rejects_future_provenance_at_the_declared_cutoff() -> None:
    row = replace(
        _row("510001", 1, 11.0),
        source_cutoff=datetime(2026, 1, 11, 9, 0, tzinfo=UTC),
    )
    with pytest.raises(FeatureInputError, match="source cutoff"):
        compute_feature_batch(
            source_rows=[row, _row("510001", 2, 12.0), _row("510001", 3, 13.0)],
            request=_request(asset_codes=("510001",), start_date=date(2026, 1, 3)),
            candidate_ids=("candidate-2",),
        )


def test_stage_a_stops_consuming_source_after_explicit_row_bound() -> None:
    consumed = 0

    def rows():
        nonlocal consumed
        for day in range(1, 11):
            consumed += 1
            yield _row("510001", day, 10.0 + day)

    with pytest.raises(BoundedWorkLimitError, match="max_source_rows"):
        compute_feature_batch(
            source_rows=rows(),
            request=_request(
                asset_codes=("510001",),
                max_source_rows=3,
                max_items=1,
            ),
            candidate_ids=("candidate-2",),
        )

    assert consumed == 4


@pytest.mark.parametrize(
    "row_override",
    [
        {"adjustment_kind": "raw"},
        {"adjusted_data_source": "sina_raw"},
        {"adjusted_data_source": "efinance_fallback"},
        {"decision_eligible": False},
        {"provider_healthy": False},
        {"fresh_at_cutoff": False},
    ],
)
def test_stage_a_rejects_non_decision_grade_adjusted_inputs(
    row_override: dict[str, object],
) -> None:
    rows = [_row("510001", day, 10.0 + day) for day in range(1, 4)]
    rows[0] = replace(rows[0], **row_override)

    with pytest.raises(FeatureInputError, match="decision-grade total-return-adjusted"):
        compute_feature_batch(
            source_rows=rows,
            request=_request(
                asset_codes=("510001",),
                start_date=date(2026, 1, 3),
                end_date=date(2026, 1, 3),
            ),
            candidate_ids=("candidate-2",),
        )


def test_stage_a_requires_every_declared_trading_session_in_warmup_window() -> None:
    rows = [_row("510001", 1, 11.0), _row("510001", 3, 13.0)]

    with pytest.raises(IncompleteWarmupError, match="2026-01-02"):
        compute_feature_batch(
            source_rows=rows,
            request=_request(
                asset_codes=("510001",),
                start_date=date(2026, 1, 3),
                end_date=date(2026, 1, 3),
            ),
            candidate_ids=("candidate-2",),
        )


def test_warmup_revision_changes_feature_identity_and_provenance_hash() -> None:
    rows = [_row("510001", day, 10.0 + day) for day in range(1, 4)]
    first = compute_feature_batch(
        source_rows=rows,
        request=_request(
            asset_codes=("510001",),
            start_date=date(2026, 1, 3),
            end_date=date(2026, 1, 3),
        ),
        candidate_ids=("candidate-2",),
    ).rows[0]
    revised = compute_feature_batch(
        source_rows=[
            replace(
                rows[0],
                raw_open=4.5,
                raw_high=5.25,
                raw_low=4.25,
                raw_close=5.0,
            ),
            *rows[1:],
        ],
        request=_request(
            run_id="run-2",
            input_snapshot_hash="input-snapshot-revised",
            asset_codes=("510001",),
            start_date=date(2026, 1, 3),
            end_date=date(2026, 1, 3),
        ),
        candidate_ids=("candidate-2",),
    ).rows[0]

    assert first.warmup_row_hashes != revised.warmup_row_hashes
    assert first.warmup_provenance_hash != revised.warmup_provenance_hash
    assert first.feature_key != revised.feature_key
    assert first.feature_hash != revised.feature_hash


def test_stage_a_rejects_warmup_revision_known_only_after_target_cutoff() -> None:
    rows = [_row("510001", day, 10.0 + day) for day in range(1, 4)]
    rows[0] = replace(
        rows[0],
        known_at=datetime(2026, 1, 4, 9, 0, tzinfo=UTC),
        source_cutoff=datetime(2026, 1, 4, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(FeatureInputError, match="target decision cutoff"):
        compute_feature_batch(
            source_rows=rows,
            request=_request(
                asset_codes=("510001",),
                start_date=date(2026, 1, 3),
                end_date=date(2026, 1, 3),
            ),
            candidate_ids=("candidate-2",),
        )
