from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.short_research.daily_reconstructable import (
    AdjustedOhlcvBar,
    AdjustmentProvenance,
    daily_reconstructable_manifest,
)
from app.services.strategy_lab import etf_ranking_stage_a as stage_a_module
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    PointInTimeAdjustedSeries,
    PointInTimeEtfMetadata,
    PointInTimeRankingInputSnapshot,
    ReplayInputExclusion,
    ReplayInputExclusionReason,
)
from app.services.strategy_lab.etf_ranking_stage_a import (
    STAGE_A_SCHEMA_VERSION,
    NewStageAReplayIdentityRequiredError,
    StageAArtifactConflictError,
    StageABatchRequest,
    StageABoundedWorkError,
    StageAReplayContract,
    StageASourcePage,
    load_stage_a_checkpoint,
    read_stage_a_feature_artifacts,
    read_stage_a_page_identities,
    run_stage_a_continuation,
    run_stage_a_loader_job,
    stage_a_source_page_from_snapshot,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


def _bars(target: date, *, scale: float = 1.0) -> tuple[AdjustedOhlcvBar, ...]:
    start = target - timedelta(days=60)
    return tuple(
        AdjustedOhlcvBar(
            session_date=start + timedelta(days=index),
            adjusted_open=(10.0 + index * 0.02) * scale,
            adjusted_high=(10.2 + index * 0.02) * scale,
            adjusted_low=(9.8 + index * 0.02) * scale,
            adjusted_close=(10.05 + index * 0.02) * scale,
            volume=1_000_000.0 + index,
        )
        for index in range(61)
    )


def _series(code: str, replay_date: date) -> PointInTimeAdjustedSeries:
    cutoff = datetime(replay_date.year, replay_date.month, replay_date.day, 7, 0, tzinfo=UTC)
    metadata = PointInTimeEtfMetadata(
        asset_code=code,
        membership_source="exchange_fact",
        membership_external_source_id=f"exchange-notice:{code}:{replay_date.isoformat()}",
        membership_provider_version="exchange-notice-v1",
        membership_evidence_hash=_hash(
            f"membership-evidence:{code}:{replay_date.isoformat()}"
        ),
        membership_raw_payload_hash=_hash(
            f"membership-raw:{code}:{replay_date.isoformat()}"
        ),
        membership_fact_hash=_hash(
            f"membership-fact:{code}:{replay_date.isoformat()}"
        ),
        tracked_underlying_id=None,
        membership_known_at=cutoff - timedelta(days=90),
        membership_last_modified_at=cutoff - timedelta(days=90),
        membership_ingested_at=cutoff - timedelta(days=89),
        eligible_from=replay_date - timedelta(days=90),
        eligible_at=replay_date,
    )
    bars = _bars(replay_date)
    return PointInTimeAdjustedSeries(
        asset_code=code,
        metadata=metadata,
        bars=bars,
        provenance=AdjustmentProvenance(
            provider="eastmoney",
            adjustment_version="eastmoney.push2his.kline.hfq_v1",
            price_basis="total_return_adjusted",
            transform_kind="constant_multiplicative",
            scale_invariance_proven=True,
        ),
        earliest_source_timestamp=cutoff - timedelta(days=60),
        latest_source_timestamp=cutoff,
        synchronized_after_cutoff=False,
        series_hash=_hash(f"series:{code}:{replay_date.isoformat()}"),
    )


def _contract(run_key: str = "ranking-stage-a") -> StageAReplayContract:
    return StageAReplayContract(
        replay_run_key=run_key,
        score_manifest_hash=daily_reconstructable_manifest().manifest_hash,
        source_snapshot_hash=_hash("source-registry"),
        universe_manifest_hash=_hash("universe-registry"),
        decision_cutoff_semantics="asia_shanghai_post_close_v1",
        schema_version=STAGE_A_SCHEMA_VERSION,
        candidate_registry_hash=_hash("candidate-registry"),
    )


def _page(
    replay_date: date,
    codes: tuple[str, ...],
    *,
    is_last_page: bool = True,
    excluded: tuple[str, ...] = (),
) -> StageASourcePage:
    eligible = tuple(_series(code, replay_date) for code in codes if code not in excluded)
    exclusions = tuple(
        ReplayInputExclusion(
            asset_code=code,
            reason=ReplayInputExclusionReason.STALE_OR_INELIGIBLE_ADJUSTED_INPUT,
            detail="fixture unavailable",
        )
        for code in excluded
    )
    return StageASourcePage(
        replay_date=replay_date,
        decision_cutoff=datetime(
            replay_date.year,
            replay_date.month,
            replay_date.day,
            7,
            0,
            tzinfo=UTC,
        ),
        source_snapshot_hash=_hash("source-registry"),
        page_source_snapshot_hash=_hash(f"source-page:{replay_date.isoformat()}"),
        universe_manifest_hash=_hash("universe-registry"),
        universe_hash=_hash(f"universe:{replay_date.isoformat()}"),
        page_input_hash=_hash(f"input:{replay_date.isoformat()}:{','.join(codes)}"),
        coverage_manifest_hash=_hash(
            f"coverage:{replay_date.isoformat()}:{','.join(codes)}"
        ),
        asset_codes=codes,
        eligible_inputs=eligible,
        exclusions=exclusions,
        is_last_page=is_last_page,
    )


def _request(**changes: object) -> StageABatchRequest:
    values: dict[str, object] = {
        "max_source_rows": 61 * 20,
        "max_items": 20,
        "max_pages": 5,
        "max_seconds": 55.0,
        "worker_count": 1,
        "peak_rss_limit_bytes": 3 * 1024**3,
    }
    values.update(changes)
    return StageABatchRequest(**values)  # type: ignore[arg-type]


def test_stage_a_enforces_single_worker_and_hard_bounds() -> None:
    with pytest.raises(StageABoundedWorkError, match="worker_count must be 1"):
        _request(worker_count=2)
    with pytest.raises(StageABoundedWorkError, match=r"within \(0, 55\]"):
        _request(max_seconds=55.1)
    with pytest.raises(StageABoundedWorkError, match="max_source_rows"):
        _request(max_source_rows=0)
    with pytest.raises(StageABoundedWorkError, match="max_items"):
        _request(max_items=0)
    for field, value in (
        ("max_source_rows", 61 * 200 + 1),
        ("max_items", 201),
        ("max_pages", 33),
        ("peak_rss_limit_bytes", 3 * 1024**3 + 1),
    ):
        with pytest.raises(StageABoundedWorkError, match=field):
            _request(**{field: value})


def test_stage_a_enforces_source_row_and_runtime_bounds(tmp_path, monkeypatch) -> None:
    page = _page(date(2026, 4, 1), ("510001", "510002"))
    with pytest.raises(StageABoundedWorkError, match="source rows"):
        run_stage_a_continuation(
            store=ReplayArtifactStore(tmp_path / "rows.sqlite3"),
            contract=_contract("rows"),
            request=_request(max_source_rows=61),
            source_pages=(page,),
        )

    clock = iter((0.0, 56.0))
    monkeypatch.setattr(stage_a_module.time, "monotonic", lambda: next(clock))
    with pytest.raises(StageABoundedWorkError, match="max_seconds"):
        run_stage_a_continuation(
            store=ReplayArtifactStore(tmp_path / "time.sqlite3"),
            contract=_contract("time"),
            request=_request(),
            source_pages=(page,),
        )


def test_stage_a_scores_in_canonical_date_code_order_and_records_exclusion(
    tmp_path,
) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    pages = (
        _page(date(2026, 4, 2), ("510002", "510003"), excluded=("510003",)),
        _page(date(2026, 4, 1), ("510001", "510002"), is_last_page=False),
    )

    progress = run_stage_a_continuation(
        store=store,
        contract=_contract(),
        request=_request(),
        source_pages=pages,
    )
    rows = read_stage_a_feature_artifacts(
        store=store,
        replay_run_key=_contract().replay_run_key,
        max_rows=4,
    )

    assert progress.complete is True
    assert [(row.replay_date, row.asset_code) for row in rows] == [
        (date(2026, 4, 1), "510001"),
        (date(2026, 4, 1), "510002"),
        (date(2026, 4, 2), "510002"),
        (date(2026, 4, 2), "510003"),
    ]
    assert [row.score_eligible for row in rows] == [True, True, True, False]
    assert rows[-1].exclusion_reason == "stale_or_ineligible_adjusted_input"
    assert rows[0].research_score is not None
    page_ids = read_stage_a_page_identities(
        store=store,
        replay_run_key=_contract().replay_run_key,
        max_rows=2,
    )
    assert len(page_ids) == 2
    assert all(item.page_input_hash for item in page_ids)


def test_stage_a_checkpoint_and_artifacts_commit_atomically(tmp_path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    store = ReplayArtifactStore(path)
    # Stage-A tables are initialized before the test-only failure trigger is added.
    read_stage_a_feature_artifacts(
        store=store,
        replay_run_key="missing",
        max_rows=1,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_stage_a_checkpoint
            BEFORE INSERT ON ranking_stage_a_checkpoints
            BEGIN
                SELECT RAISE(ABORT, 'synthetic checkpoint failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic checkpoint failure"):
        run_stage_a_continuation(
            store=store,
            contract=_contract(),
            request=_request(),
            source_pages=(_page(date(2026, 4, 1), ("510001",)),),
        )

    assert read_stage_a_feature_artifacts(
        store=store,
        replay_run_key=_contract().replay_run_key,
        max_rows=1,
    ) == ()
    assert load_stage_a_checkpoint(store=store, contract=_contract()) is None
    assert read_stage_a_page_identities(
        store=store,
        replay_run_key=_contract().replay_run_key,
        max_rows=1,
    ) == ()


def test_stage_a_retry_is_idempotent_after_complete_checkpoint(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    page = _page(date(2026, 4, 1), ("510001", "510002"))

    first = run_stage_a_continuation(
        store=store,
        contract=_contract(),
        request=_request(),
        source_pages=(page,),
    )
    second = run_stage_a_continuation(
        store=store,
        contract=_contract(),
        request=_request(),
        source_pages=(page,),
    )

    assert first.complete is second.complete is True
    assert second.processed_items == 0
    assert second.generation == first.generation
    assert second.completion_identity_hash == first.completion_identity_hash
    assert len(
        read_stage_a_feature_artifacts(
            store=store,
            replay_run_key=_contract().replay_run_key,
            max_rows=2,
        )
    ) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("score_manifest_hash", _hash("changed-score")),
        ("source_snapshot_hash", _hash("changed-input")),
        ("universe_manifest_hash", _hash("changed-universe")),
        ("decision_cutoff_semantics", "another-cutoff"),
        ("schema_version", "another-schema"),
        ("candidate_registry_hash", _hash("changed-candidates")),
    ],
)
def test_stage_a_identity_change_requires_new_replay_key(
    tmp_path,
    field: str,
    value: str,
) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    run_stage_a_continuation(
        store=store,
        contract=_contract(),
        request=_request(),
        source_pages=(_page(date(2026, 4, 1), ("510001",)),),
    )

    with pytest.raises(NewStageAReplayIdentityRequiredError, match="new replay identity"):
        load_stage_a_checkpoint(
            store=store,
            contract=replace(_contract(), **{field: value}),
        )


def test_stage_a_chunking_and_interrupted_resume_are_invariant(tmp_path) -> None:
    pages = (
        _page(date(2026, 4, 1), ("510001", "510002"), is_last_page=False),
        _page(date(2026, 4, 2), ("510001", "510002")),
    )
    one_store = ReplayArtifactStore(tmp_path / "one.sqlite3")
    chunk_store = ReplayArtifactStore(tmp_path / "chunk.sqlite3")
    one_contract = _contract("one-shot")
    chunk_contract = _contract("chunked")

    one = run_stage_a_continuation(
        store=one_store,
        contract=one_contract,
        request=_request(max_items=4),
        source_pages=pages,
    )
    partial = run_stage_a_continuation(
        store=chunk_store,
        contract=chunk_contract,
        request=_request(max_items=1),
        source_pages=pages,
    )
    assert partial.complete is False
    while not partial.complete:
        partial = run_stage_a_continuation(
            store=chunk_store,
            contract=chunk_contract,
            request=_request(max_items=1),
            source_pages=pages,
        )

    one_rows = read_stage_a_feature_artifacts(
        store=one_store,
        replay_run_key=one_contract.replay_run_key,
        max_rows=4,
    )
    chunk_rows = read_stage_a_feature_artifacts(
        store=chunk_store,
        replay_run_key=chunk_contract.replay_run_key,
        max_rows=4,
    )
    assert [row.content_hash for row in one_rows] == [row.content_hash for row in chunk_rows]
    assert partial.completion_identity_hash == one.completion_identity_hash
    assert partial.artifact_chain_hash == one.artifact_chain_hash


def test_stage_a_feature_identity_does_not_depend_on_loader_code_page_shape(
    tmp_path,
) -> None:
    replay_date = date(2026, 4, 1)
    one_page = (_page(replay_date, ("510001", "510002")),)
    split_pages = (
        _page(replay_date, ("510001",), is_last_page=False),
        _page(replay_date, ("510002",)),
    )
    one_store = ReplayArtifactStore(tmp_path / "one-page.sqlite3")
    split_store = ReplayArtifactStore(tmp_path / "split-page.sqlite3")

    one = run_stage_a_continuation(
        store=one_store,
        contract=_contract("one-page"),
        request=_request(),
        source_pages=one_page,
    )
    split = run_stage_a_continuation(
        store=split_store,
        contract=_contract("split-page"),
        request=_request(),
        source_pages=split_pages,
    )

    assert one.artifact_chain_hash == split.artifact_chain_hash
    assert one.completion_identity_hash == split.completion_identity_hash


def _snapshot(replay_date: date) -> PointInTimeRankingInputSnapshot:
    series = _series("510001", replay_date)
    metadata = series.metadata
    return PointInTimeRankingInputSnapshot(
        replay_date=replay_date,
        decision_cutoff=datetime(
            replay_date.year,
            replay_date.month,
            replay_date.day,
            7,
            0,
            tzinfo=UTC,
        ),
        authoritative_universe=(metadata,),
        eligible_inputs=(series,),
        exclusions=(),
        universe_hash=_hash(f"universe:{replay_date.isoformat()}"),
        input_hash=_hash(f"input:{replay_date.isoformat()}:510001"),
        source_snapshot_hash=_hash(f"source-page:{replay_date.isoformat()}"),
        coverage_manifest_hash=_hash(f"coverage:{replay_date.isoformat()}"),
        page_asset_codes=("510001",),
        next_code_after=None,
        has_more=False,
    )


def test_stage_a_snapshot_adapter_preserves_page_and_run_identities() -> None:
    snapshot = _snapshot(date(2026, 4, 1))

    page = stage_a_source_page_from_snapshot(
        snapshot,
        contract=_contract(),
        is_last_replay_date=True,
    )

    assert page.source_snapshot_hash == _contract().source_snapshot_hash
    assert page.page_source_snapshot_hash == snapshot.source_snapshot_hash
    assert page.page_input_hash == snapshot.input_hash
    assert page.asset_codes == snapshot.page_asset_codes
    assert page.is_last_page is True


@pytest.mark.asyncio
async def test_stage_a_loader_job_uses_one_bounded_code_page(tmp_path) -> None:
    calls: list[dict[str, object]] = []
    replay_date = date(2026, 4, 1)

    async def loader(_session, **kwargs):
        calls.append(kwargs)
        return _snapshot(replay_date)

    progress = await run_stage_a_loader_job(
        session=object(),  # type: ignore[arg-type]
        store=ReplayArtifactStore(tmp_path / "artifacts.sqlite3"),
        contract=_contract(),
        request=_request(max_pages=1, max_items=2, max_source_rows=122),
        replay_dates=(replay_date,),
        decision_cutoffs=(
            (replay_date, datetime(2026, 4, 1, 7, 0, tzinfo=UTC)),
        ),
        max_codes_per_page=2,
        loader=loader,
        peak_rss_reader=lambda: 100,
    )

    assert progress.complete is True
    assert len(calls) == 1
    assert calls[0]["code_after"] is None
    assert calls[0]["max_codes"] == 2
    assert calls[0]["max_source_rows"] == 122


@pytest.mark.asyncio
async def test_stage_a_loader_job_advances_date_without_empty_boundary_query(
    tmp_path,
) -> None:
    calls: list[tuple[date, str | None]] = []
    dates = (date(2026, 4, 1), date(2026, 4, 2))

    async def loader(_session, **kwargs):
        replay_date = kwargs["replay_date"]
        calls.append((replay_date, kwargs["code_after"]))
        return _snapshot(replay_date)

    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    kwargs = {
        "session": object(),
        "store": store,
        "contract": _contract(),
        "request": _request(max_pages=1, max_items=2, max_source_rows=122),
        "replay_dates": dates,
        "decision_cutoffs": tuple(
            (item, datetime(item.year, item.month, item.day, 7, 0, tzinfo=UTC))
            for item in dates
        ),
        "max_codes_per_page": 2,
        "loader": loader,
        "peak_rss_reader": lambda: 100,
    }

    first = await run_stage_a_loader_job(**kwargs)  # type: ignore[arg-type]
    second = await run_stage_a_loader_job(**kwargs)  # type: ignore[arg-type]

    assert first.complete is False
    assert second.complete is True
    assert calls == [(dates[0], None), (dates[1], None)]


def test_stage_a_rejects_post_cutoff_series_even_if_provider_is_whitelisted(
    tmp_path,
) -> None:
    page = _page(date(2026, 4, 1), ("510001",))
    future = replace(
        page.eligible_inputs[0],
        latest_source_timestamp=page.decision_cutoff + timedelta(seconds=1),
        synchronized_after_cutoff=True,
    )
    page = replace(page, eligible_inputs=(future,))

    with pytest.raises(StageABoundedWorkError, match="after decision cutoff"):
        run_stage_a_continuation(
            store=ReplayArtifactStore(tmp_path / "artifacts.sqlite3"),
            contract=_contract(),
            request=_request(),
            source_pages=(page,),
        )


def test_stage_a_reports_partial_progress_and_peak_rss(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "artifacts.sqlite3")
    page = _page(date(2026, 4, 1), ("510001", "510002"))

    progress = run_stage_a_continuation(
        store=store,
        contract=_contract(),
        request=_request(max_items=1),
        source_pages=(page,),
        peak_rss_reader=lambda: 123_456_789,
    )

    assert progress.complete is False
    assert progress.status == "partial"
    assert progress.processed_items == 1
    assert progress.peak_rss_bytes == 123_456_789
    assert progress.within_peak_rss_limit is True
    assert progress.next_cursor == (date(2026, 4, 1), "510001")


def test_stage_a_rejects_contradictory_last_page_with_more_codes(tmp_path) -> None:
    page = replace(
        _page(date(2026, 4, 1), ("510001",), is_last_page=True),
        has_more_codes=True,
    )

    with pytest.raises(StageABoundedWorkError, match="is_last_page"):
        run_stage_a_continuation(
            store=ReplayArtifactStore(tmp_path / "artifacts.sqlite3"),
            contract=_contract(),
            request=_request(),
            source_pages=(page,),
        )


def test_stage_a_rejects_source_page_above_hard_item_maximum(tmp_path) -> None:
    codes = tuple(f"51{index:04d}" for index in range(201))
    page = _page(date(2026, 4, 1), codes, excluded=codes)

    with pytest.raises(StageABoundedWorkError, match="hard item maximum"):
        run_stage_a_continuation(
            store=ReplayArtifactStore(tmp_path / "artifacts.sqlite3"),
            contract=_contract(),
            request=_request(max_items=200),
            source_pages=(page,),
        )


def test_stage_a_checks_peak_rss_during_scoring(tmp_path) -> None:
    calls = 0

    def peak_rss_reader() -> int:
        nonlocal calls
        calls += 1
        return 101 if calls >= 3 else 99

    with pytest.raises(StageABoundedWorkError, match="peak_rss_limit_bytes"):
        run_stage_a_continuation(
            store=ReplayArtifactStore(tmp_path / "artifacts.sqlite3"),
            contract=_contract(),
            request=_request(peak_rss_limit_bytes=100),
            source_pages=(_page(date(2026, 4, 1), ("510001",)),),
            peak_rss_reader=peak_rss_reader,
        )
    assert calls >= 3


@pytest.mark.asyncio
async def test_stage_a_loader_is_cancelled_before_commit_reserve_expires(
    tmp_path,
) -> None:
    cancelled = asyncio.Event()
    replay_date = date(2026, 4, 1)

    async def loader(_session, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(StageABoundedWorkError, match="loader.*max_seconds"):
        await run_stage_a_loader_job(
            session=object(),  # type: ignore[arg-type]
            store=ReplayArtifactStore(tmp_path / "artifacts.sqlite3"),
            contract=_contract(),
            request=_request(max_seconds=0.05),
            replay_dates=(replay_date,),
            decision_cutoffs=((replay_date, _page(replay_date, ("510001",)).decision_cutoff),),
            loader=loader,
            peak_rss_reader=lambda: 1,
        )
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_stage_a_empty_universe_is_persisted_as_stable_block(tmp_path) -> None:
    replay_date = date(2026, 4, 1)
    calls = 0
    empty = replace(
        _snapshot(replay_date),
        authoritative_universe=(),
        eligible_inputs=(),
        exclusions=(
            ReplayInputExclusion(
                asset_code=None,
                reason=ReplayInputExclusionReason.INSUFFICIENT_POINT_IN_TIME_UNIVERSE,
                detail="no factual membership covers replay_date by the decision cutoff",
            ),
        ),
        universe_hash=_hash("empty-universe"),
        input_hash=_hash("empty-input"),
        source_snapshot_hash=_hash("empty-source"),
        coverage_manifest_hash=_hash("empty-coverage"),
        page_asset_codes=(),
        next_code_after=None,
        has_more=False,
    )

    async def loader(_session, **_kwargs):
        nonlocal calls
        calls += 1
        return empty

    kwargs = {
        "session": object(),
        "store": ReplayArtifactStore(tmp_path / "artifacts.sqlite3"),
        "contract": _contract("empty-universe"),
        "request": _request(max_pages=1),
        "replay_dates": (replay_date,),
        "decision_cutoffs": ((replay_date, empty.decision_cutoff),),
        "loader": loader,
        "peak_rss_reader": lambda: 1,
    }
    first = await run_stage_a_loader_job(**kwargs)  # type: ignore[arg-type]
    second = await run_stage_a_loader_job(**kwargs)  # type: ignore[arg-type]
    checkpoint = load_stage_a_checkpoint(
        store=kwargs["store"],  # type: ignore[arg-type]
        contract=kwargs["contract"],  # type: ignore[arg-type]
    )

    assert first.status == second.status == "blocked"
    assert first.reason == second.reason == "empty_point_in_time_universe"
    assert second.generation == first.generation == 1
    assert calls == 1
    assert checkpoint is not None
    assert checkpoint.status == "blocked"
    assert checkpoint.reason == "empty_point_in_time_universe"
    assert checkpoint.active_replay_date == replay_date
    assert checkpoint.active_decision_cutoff == empty.decision_cutoff
    assert checkpoint.active_universe_hash == empty.universe_hash
    assert checkpoint.active_page_source_snapshot_hash == empty.source_snapshot_hash

    changed_cutoff = dict(kwargs)
    changed_cutoff["decision_cutoffs"] = (
        (replay_date, empty.decision_cutoff + timedelta(minutes=1)),
    )
    with pytest.raises(NewStageAReplayIdentityRequiredError, match="new replay identity"):
        await run_stage_a_loader_job(**changed_cutoff)  # type: ignore[arg-type]
    assert calls == 1


@pytest.mark.parametrize(
    "changed_field",
    ("decision_cutoff", "universe_hash", "page_source_snapshot_hash"),
)
def test_stage_a_same_date_resume_rejects_changed_active_identity(
    tmp_path,
    changed_field: str,
) -> None:
    replay_date = date(2026, 4, 1)
    first_page = replace(
        _page(replay_date, ("510001",), is_last_page=False),
        has_more_codes=True,
    )
    store = ReplayArtifactStore(tmp_path / f"{changed_field}.sqlite3")
    contract = _contract(f"resume-{changed_field}")
    first = run_stage_a_continuation(
        store=store,
        contract=contract,
        request=_request(),
        source_pages=(first_page,),
    )
    checkpoint = load_stage_a_checkpoint(store=store, contract=contract)
    second_page = _page(replay_date, ("510002",))
    replacements: dict[str, object] = {
        "decision_cutoff": second_page.decision_cutoff + timedelta(minutes=1),
        "universe_hash": _hash("changed-active-universe"),
        "page_source_snapshot_hash": _hash("changed-active-source"),
    }
    second_page = replace(second_page, **{changed_field: replacements[changed_field]})

    assert first.complete is False
    assert checkpoint is not None
    assert checkpoint.active_replay_date == replay_date
    with pytest.raises(NewStageAReplayIdentityRequiredError, match="new replay identity"):
        run_stage_a_continuation(
            store=store,
            contract=contract,
            request=_request(),
            source_pages=(second_page,),
        )


@pytest.mark.parametrize("corruption", ("generation", "checkpoint_hash"))
def test_stage_a_reads_reject_corrupt_checkpoint_db_metadata(
    tmp_path,
    corruption: str,
) -> None:
    path = tmp_path / f"checkpoint-{corruption}.sqlite3"
    store = ReplayArtifactStore(path)
    contract = _contract(f"corrupt-checkpoint-{corruption}")
    run_stage_a_continuation(
        store=store,
        contract=contract,
        request=_request(),
        source_pages=(_page(date(2026, 4, 1), ("510001",)),),
    )
    with sqlite3.connect(path) as connection:
        if corruption == "generation":
            connection.execute(
                "UPDATE ranking_stage_a_checkpoints SET generation=generation+1 "
                "WHERE replay_run_key=?",
                (contract.replay_run_key,),
            )
        else:
            connection.execute(
                "UPDATE ranking_stage_a_checkpoints SET checkpoint_hash=? "
                "WHERE replay_run_key=?",
                (_hash("forged-checkpoint"), contract.replay_run_key),
            )

    with pytest.raises(StageAArtifactConflictError, match="checkpoint"):
        load_stage_a_checkpoint(store=store, contract=contract)


@pytest.mark.parametrize("corruption", ("invalid_json", "primary_key"))
def test_stage_a_feature_reads_convert_corruption_to_artifact_conflict(
    tmp_path,
    corruption: str,
) -> None:
    path = tmp_path / f"feature-{corruption}.sqlite3"
    store = ReplayArtifactStore(path)
    contract = _contract(f"feature-{corruption}")
    run_stage_a_continuation(
        store=store,
        contract=contract,
        request=_request(),
        source_pages=(_page(date(2026, 4, 1), ("510001",)),),
    )
    with sqlite3.connect(path) as connection:
        if corruption == "invalid_json":
            connection.execute(
                "UPDATE ranking_stage_a_features SET payload_json='{' "
                "WHERE replay_run_key=?",
                (contract.replay_run_key,),
            )
        else:
            connection.execute(
                "UPDATE ranking_stage_a_features SET asset_code='599999' "
                "WHERE replay_run_key=?",
                (contract.replay_run_key,),
            )

    with pytest.raises(StageAArtifactConflictError):
        read_stage_a_feature_artifacts(
            store=store,
            replay_run_key=contract.replay_run_key,
            max_rows=1,
        )


def test_stage_a_page_reads_recompute_page_key_and_verify_db_primary_key(tmp_path) -> None:
    path = tmp_path / "page.sqlite3"
    store = ReplayArtifactStore(path)
    contract = _contract("corrupt-page")
    run_stage_a_continuation(
        store=store,
        contract=contract,
        request=_request(),
        source_pages=(_page(date(2026, 4, 1), ("510001",)),),
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE ranking_stage_a_pages SET page_key=? WHERE replay_run_key=?",
            (_hash("forged-page-key"), contract.replay_run_key),
        )

    with pytest.raises(StageAArtifactConflictError, match="page"):
        read_stage_a_page_identities(
            store=store,
            replay_run_key=contract.replay_run_key,
            max_rows=1,
        )


def test_stage_a_feature_keyset_reader_pages_without_offset(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "keyset.sqlite3")
    contract = _contract("keyset")
    run_stage_a_continuation(
        store=store,
        contract=contract,
        request=_request(max_items=4),
        source_pages=(
            _page(date(2026, 4, 1), ("510001", "510002"), is_last_page=False),
            _page(date(2026, 4, 2), ("510001", "510002")),
        ),
    )

    first = stage_a_module.read_stage_a_feature_artifact_page(
        store=store,
        replay_run_key=contract.replay_run_key,
        after_cursor=None,
        max_rows=2,
    )
    second = stage_a_module.read_stage_a_feature_artifact_page(
        store=store,
        replay_run_key=contract.replay_run_key,
        after_cursor=first.next_cursor,
        max_rows=2,
    )

    assert [item.cursor for item in first.items] == [
        (date(2026, 4, 1), "510001"),
        (date(2026, 4, 1), "510002"),
    ]
    assert first.has_more is True
    assert first.next_cursor == first.items[-1].cursor
    assert [item.cursor for item in second.items] == [
        (date(2026, 4, 2), "510001"),
        (date(2026, 4, 2), "510002"),
    ]
    assert second.has_more is False
    assert second.next_cursor is None
