from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, date, datetime

import pytest

from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.strategy_lab import etf_ranking_stage_a as stage_a_module
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_ranking_stage_a import (
    STAGE_A_SCHEMA_VERSION,
    StageAFeatureArtifact,
    StageAReplayContract,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    STAGE_B_SCHEMA_VERSION,
    NewStageBReplayIdentityRequiredError,
    StageBArtifactConflictError,
    StageBBatchRequest,
    StageBBoundedWorkError,
    StageBIncompleteDateError,
    StageBReplayContract,
    build_stage_b_feature_input,
    build_stage_b_source_date,
    load_stage_b_checkpoint,
    read_stage_b_date_manifest_page,
    read_stage_b_ranking_event_page,
    read_stage_b_source_date_page_from_stage_a,
    run_stage_b_continuation,
    stage_b_contract_from_stage_a,
)
from app.services.tracked_positions.lifecycle import (
    stable_contract_hash,
    stable_contract_json,
)


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


def _contract(run_key: str = "pit-ranking-stage-b") -> StageBReplayContract:
    return StageBReplayContract(
        replay_run_key=run_key,
        score_contract_id="daily_reconstructable_v1",
        score_manifest_hash=daily_reconstructable_manifest().manifest_hash,
        source_snapshot_hash=_hash("source-registry"),
        universe_manifest_hash=_hash("universe-registry"),
        feature_schema_version="etf-ranking-stage-a-v1",
        candidate_registry_hash=_hash("candidate-registry"),
        decision_cutoff_semantics="asia_shanghai_post_close_v1",
        schema_version=STAGE_B_SCHEMA_VERSION,
    )


def _stage_a_contract(run_key: str = "pit-ranking-stage-b") -> StageAReplayContract:
    return StageAReplayContract(
        replay_run_key=run_key,
        score_manifest_hash=daily_reconstructable_manifest().manifest_hash,
        source_snapshot_hash=_hash("source-registry"),
        universe_manifest_hash=_hash("universe-registry"),
        decision_cutoff_semantics="asia_shanghai_post_close_v1",
        schema_version=STAGE_A_SCHEMA_VERSION,
        candidate_registry_hash=_hash("candidate-registry"),
    )


def _stage_a_feature(
    replay_date: date,
    asset_code: str,
    *,
    score: float | None,
) -> StageAFeatureArtifact:
    eligible = score is not None
    draft = StageAFeatureArtifact(
        replay_run_key=_stage_a_contract().replay_run_key,
        replay_date=replay_date,
        asset_code=asset_code,
        decision_cutoff=datetime(
            replay_date.year,
            replay_date.month,
            replay_date.day,
            7,
            0,
            tzinfo=UTC,
        ),
        score_contract_id="daily_reconstructable_v1",
        score_manifest_hash=_stage_a_contract().score_manifest_hash,
        schema_version=STAGE_A_SCHEMA_VERSION,
        source_snapshot_hash=_stage_a_contract().source_snapshot_hash,
        page_source_snapshot_hash=_hash(f"source-page:{replay_date}"),
        universe_manifest_hash=_stage_a_contract().universe_manifest_hash,
        universe_hash=_hash(f"universe:{replay_date}"),
        unit_input_hash=_hash(f"input:{replay_date}:{asset_code}"),
        series_hash=_hash(f"series:{replay_date}:{asset_code}") if eligible else None,
        score_eligible=eligible,
        exclusion_reason=None if eligible else "stale_or_ineligible_adjusted_input",
        exclusion_detail=None if eligible else "missing adjusted history",
        research_score=score,
        trend_score=score,
        risk_score=(score - 1 if score is not None else None),
        liquidity_score=(score - 2 if score is not None else None),
        score_payload=({"research_score": score} if eligible else None),
        content_hash="pending",
    )
    payload = asdict(draft)
    payload.pop("replay_run_key")
    payload.pop("content_hash")
    return replace(draft, content_hash=stable_contract_hash(payload))


def _seed_stage_a(
    store: ReplayArtifactStore,
    features: tuple[StageAFeatureArtifact, ...],
    *,
    status: str,
) -> None:
    contract = _stage_a_contract()
    stage_a_module._initialize_stage_a(store)  # noqa: SLF001 - sealed fixture
    last_date = features[-1].replay_date if features else date(2026, 4, 1)
    last_code = features[-1].asset_code if features else "blocked"
    cutoff = datetime(last_date.year, last_date.month, last_date.day, 7, tzinfo=UTC)
    active_identity = (
        last_date,
        cutoff,
        _hash(f"universe:{last_date}"),
        _hash(f"source-page:{last_date}"),
    )
    checkpoint = stage_a_module._build_checkpoint(  # noqa: SLF001
        contract=contract,
        generation=1,
        cursor=(last_date, last_code),
        artifact_count=len(features),
        artifact_chain_hash=_hash(f"chain:{status}"),
        replay_complete=status == "complete",
        resume_cursor=None if status in {"complete", "blocked"} else (last_date, last_code),
        active_identity=None if status == "complete" else active_identity,
        status=status,
        reason="empty_point_in_time_universe" if status == "blocked" else None,
    )
    with store._connect() as connection:  # noqa: SLF001 - sealed fixture
        for item in features:
            connection.execute(
                """
                INSERT INTO ranking_stage_a_features
                    (replay_run_key, replay_date, asset_code, content_hash, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    item.replay_run_key,
                    item.replay_date.isoformat(),
                    item.asset_code,
                    item.content_hash,
                    stable_contract_json(item),
                ),
            )
        connection.execute(
            """
            INSERT INTO ranking_stage_a_checkpoints
                (replay_run_key, generation, contract_json, checkpoint_hash, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                contract.replay_run_key,
                checkpoint.generation,
                stable_contract_json(contract),
                checkpoint.checkpoint_hash,
                stable_contract_json(checkpoint),
            ),
        )


def _source_date(
    replay_date: date,
    *,
    run_key: str = "pit-ranking-stage-b",
    count: int = 25,
    missing_price: tuple[str, ...] = (),
    missing_feature: tuple[str, ...] = (),
):
    contract = _contract(run_key)
    codes = tuple(f"51{index:04d}" for index in range(1, count + 1))
    universe_hash = _hash(f"universe:{replay_date.isoformat()}")
    features = []
    for index, code in enumerate(codes):
        if code in missing_price:
            features.append(
                build_stage_b_feature_input(
                    contract=contract,
                    replay_date=replay_date,
                    asset_code=code,
                    universe_hash=universe_hash,
                    unit_input_hash=_hash(f"input:{replay_date}:{code}"),
                    series_hash=None,
                    score_eligible=False,
                    exclusion_reason="stale_or_ineligible_adjusted_input",
                    exclusion_detail="adjusted history unavailable",
                )
            )
        elif code in missing_feature:
            features.append(
                build_stage_b_feature_input(
                    contract=contract,
                    replay_date=replay_date,
                    asset_code=code,
                    universe_hash=universe_hash,
                    unit_input_hash=_hash(f"input:{replay_date}:{code}"),
                    series_hash=_hash(f"series:{replay_date}:{code}"),
                    score_eligible=False,
                    exclusion_reason="missing_feature_component",
                    exclusion_detail="risk component unavailable",
                )
            )
        else:
            # Two equal scores exercise the stable asset-code tie break.
            score = 90.0 if index < 2 else 89.0 - index
            features.append(
                build_stage_b_feature_input(
                    contract=contract,
                    replay_date=replay_date,
                    asset_code=code,
                    universe_hash=universe_hash,
                    unit_input_hash=_hash(f"input:{replay_date}:{code}"),
                    series_hash=_hash(f"series:{replay_date}:{code}"),
                    score_eligible=True,
                    research_score=score,
                    trend_score=score,
                    risk_score=score - 1.0,
                    liquidity_score=score - 2.0,
                )
            )
    return build_stage_b_source_date(
        contract=contract,
        replay_date=replay_date,
        decision_cutoff=datetime(
            replay_date.year,
            replay_date.month,
            replay_date.day,
            7,
            0,
            tzinfo=UTC,
        ),
        universe_hash=universe_hash,
        authoritative_asset_codes=codes,
        features=tuple(features),
    )


def _request(**changes: object) -> StageBBatchRequest:
    values: dict[str, object] = {
        "max_dates": 5,
        "max_feature_rows": 500,
        "max_seconds": 55.0,
        "worker_count": 1,
    }
    values.update(changes)
    return StageBBatchRequest(**values)  # type: ignore[arg-type]


def test_stage_b_enforces_single_worker_and_hard_bounds() -> None:
    with pytest.raises(StageBBoundedWorkError, match="worker_count must be 1"):
        _request(worker_count=2)
    with pytest.raises(StageBBoundedWorkError, match=r"within \(0, 55\]"):
        _request(max_seconds=55.1)
    with pytest.raises(StageBBoundedWorkError, match="max_dates"):
        _request(max_dates=0)
    with pytest.raises(StageBBoundedWorkError, match="max_feature_rows"):
        _request(max_feature_rows=0)


def test_stage_b_rejects_incomplete_duplicate_and_mixed_date_manifests(
    tmp_path,
) -> None:
    source = _source_date(date(2026, 4, 1))

    incomplete = replace(
        source,
        manifest=replace(source.manifest, atomic_complete=False),
    )
    with pytest.raises(StageBIncompleteDateError, match="atomic complete"):
        run_stage_b_continuation(
            store=ReplayArtifactStore(tmp_path / "incomplete.sqlite3"),
            contract=_contract(),
            request=_request(),
            source_dates=(incomplete,),
            source_has_more=False,
        )

    duplicate = replace(source, features=(*source.features, source.features[0]))
    with pytest.raises(StageBIncompleteDateError, match="duplicate"):
        run_stage_b_continuation(
            store=ReplayArtifactStore(tmp_path / "duplicate.sqlite3"),
            contract=_contract(),
            request=_request(),
            source_dates=(duplicate,),
            source_has_more=False,
        )

    mixed_feature = replace(
        source.features[0],
        score_manifest_hash=_hash("different-score-contract"),
    )
    mixed = replace(source, features=(mixed_feature, *source.features[1:]))
    with pytest.raises(NewStageBReplayIdentityRequiredError, match="mixed contract"):
        run_stage_b_continuation(
            store=ReplayArtifactStore(tmp_path / "mixed.sqlite3"),
            contract=_contract(),
            request=_request(),
            source_dates=(mixed,),
            source_has_more=False,
        )


def test_stage_b_never_ranks_an_individual_code_chunk(tmp_path) -> None:
    source = _source_date(date(2026, 4, 1))
    partial_code_chunk = replace(source, features=source.features[:5])

    with pytest.raises(StageBIncompleteDateError, match="full authoritative universe"):
        run_stage_b_continuation(
            store=ReplayArtifactStore(tmp_path / "partial.sqlite3"),
            contract=_contract(),
            request=_request(),
            source_dates=(partial_code_chunk,),
            source_has_more=True,
        )

    page = read_stage_b_ranking_event_page(
        store=ReplayArtifactStore(tmp_path / "partial.sqlite3"),
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=10,
    )
    assert page.rows == ()


def test_stage_b_ranks_one_global_cross_section_and_reports_independent_coverage(
    tmp_path,
) -> None:
    replay_date = date(2026, 4, 1)
    source = _source_date(
        replay_date,
        count=22,
        missing_price=("510021",),
        missing_feature=("510022",),
    )
    store = ReplayArtifactStore(tmp_path / "ranked.sqlite3")

    progress = run_stage_b_continuation(
        store=store,
        contract=_contract(),
        request=_request(),
        source_dates=(source,),
        source_has_more=False,
    )
    event = read_stage_b_ranking_event_page(
        store=store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=2,
    ).rows[0]
    manifest = read_stage_b_date_manifest_page(
        store=store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=2,
    ).rows[0]

    assert progress.complete is True
    assert progress.processed_dates == 1
    assert event.ranking_source_kind == "research_replay"
    assert event.score_contract_id == "daily_reconstructable_v1"
    assert event.score_field == "research_score"
    assert event.all_scored[:2] == ("510001", "510002")
    assert len(event.all_scored) == 20
    assert event.top5 == event.all_scored[:5]
    assert event.top10 == event.all_scored[:10]
    assert event.top20 == event.all_scored[:20]
    assert tuple(item.rank for item in event.ranked_items) == tuple(range(1, 21))
    assert event.date_manifest_hash == manifest.manifest_hash
    assert event.universe_hash == source.manifest.universe_hash
    assert event.input_hash == source.manifest.input_hash

    coverage = {item.dimension: item for item in manifest.coverage}
    assert (coverage["point_in_time_universe"].numerator, coverage["point_in_time_universe"].denominator) == (22, 22)
    assert (coverage["adjusted_price"].numerator, coverage["adjusted_price"].denominator) == (21, 22)
    assert (coverage["feature_component"].numerator, coverage["feature_component"].denominator) == (20, 21)
    assert (coverage["score_eligible"].numerator, coverage["score_eligible"].denominator) == (20, 22)
    assert (coverage["forward_outcome"].numerator, coverage["forward_outcome"].denominator) == (0, 20)
    assert coverage["forward_outcome"].exclusions == (("future_window_pending", 20),)
    assert ("stale_or_ineligible_adjusted_input", 1) in coverage["adjusted_price"].exclusions
    assert ("missing_feature_component", 1) in coverage["feature_component"].exclusions


def test_stage_b_persistence_is_immutable_and_detects_tampering(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "immutable.sqlite3")
    source = _source_date(date(2026, 4, 1))
    first = run_stage_b_continuation(
        store=store,
        contract=_contract(),
        request=_request(),
        source_dates=(source,),
        source_has_more=False,
    )
    second = run_stage_b_continuation(
        store=store,
        contract=_contract(),
        request=_request(),
        source_dates=(source,),
        source_has_more=False,
    )
    assert second.processed_dates == 0
    assert second.completion_identity_hash == first.completion_identity_hash

    with store._connect() as connection:  # noqa: SLF001 - corruption fixture
        connection.execute(
            "UPDATE ranking_stage_b_events SET payload_json='{}' "
            "WHERE replay_run_key=?",
            (_contract().replay_run_key,),
        )
    with pytest.raises(StageBArtifactConflictError, match="hash mismatch"):
        read_stage_b_ranking_event_page(
            store=store,
            replay_run_key=_contract().replay_run_key,
            after_date=None,
            max_rows=2,
        )


def test_stage_b_artifacts_and_checkpoint_commit_atomically(tmp_path) -> None:
    path = tmp_path / "atomic.sqlite3"
    store = ReplayArtifactStore(path)
    read_stage_b_ranking_event_page(
        store=store,
        replay_run_key="missing",
        after_date=None,
        max_rows=1,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_stage_b_checkpoint
            BEFORE INSERT ON ranking_stage_b_checkpoints
            BEGIN
                SELECT RAISE(ABORT, 'synthetic Stage-B checkpoint failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic Stage-B"):
        run_stage_b_continuation(
            store=store,
            contract=_contract(),
            request=_request(),
            source_dates=(_source_date(date(2026, 4, 1)),),
            source_has_more=False,
        )

    assert read_stage_b_ranking_event_page(
        store=store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=1,
    ).rows == ()
    assert read_stage_b_date_manifest_page(
        store=store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=1,
    ).rows == ()
    assert load_stage_b_checkpoint(store=store, contract=_contract()) is None


def test_stage_b_resume_and_safe_batch_shapes_are_invariant(tmp_path) -> None:
    dates = (date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3))
    sources = tuple(_source_date(item) for item in dates)
    one_store = ReplayArtifactStore(tmp_path / "one.sqlite3")
    chunk_store = ReplayArtifactStore(tmp_path / "chunk.sqlite3")

    one = run_stage_b_continuation(
        store=one_store,
        contract=_contract(),
        request=_request(max_dates=3),
        source_dates=sources,
        source_has_more=False,
    )
    chunk = None
    for index, source in enumerate(sources):
        chunk = run_stage_b_continuation(
            store=chunk_store,
            contract=_contract(),
            request=_request(max_dates=1),
            source_dates=(source,),
            source_has_more=index < len(sources) - 1,
        )
    assert chunk is not None and chunk.complete is True

    one_events = read_stage_b_ranking_event_page(
        store=one_store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=3,
    ).rows
    chunk_events = read_stage_b_ranking_event_page(
        store=chunk_store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=3,
    ).rows
    one_manifests = read_stage_b_date_manifest_page(
        store=one_store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=3,
    ).rows
    chunk_manifests = read_stage_b_date_manifest_page(
        store=chunk_store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=3,
    ).rows
    one_checkpoint = load_stage_b_checkpoint(store=one_store, contract=_contract())
    chunk_checkpoint = load_stage_b_checkpoint(store=chunk_store, contract=_contract())

    assert one_events == chunk_events
    assert one_manifests == chunk_manifests
    assert chunk.manifest_chain_hash == one.manifest_chain_hash
    assert chunk.event_chain_hash == one.event_chain_hash
    assert chunk.summary_hash == one.summary_hash
    assert chunk.completion_identity_hash == one.completion_identity_hash
    assert one_checkpoint is not None and chunk_checkpoint is not None
    assert chunk_checkpoint.completion_identity_hash == one_checkpoint.completion_identity_hash


def test_stage_b_output_readers_are_keyset_paginated(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "keyset.sqlite3")
    sources = tuple(
        _source_date(item)
        for item in (date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3))
    )
    run_stage_b_continuation(
        store=store,
        contract=_contract(),
        request=_request(max_dates=3),
        source_dates=sources,
        source_has_more=False,
    )

    first = read_stage_b_ranking_event_page(
        store=store,
        replay_run_key=_contract().replay_run_key,
        after_date=None,
        max_rows=1,
    )
    second = read_stage_b_ranking_event_page(
        store=store,
        replay_run_key=_contract().replay_run_key,
        after_date=first.next_after_date,
        max_rows=1,
    )

    assert first.has_more is True
    assert first.next_after_date == date(2026, 4, 1)
    assert second.rows[0].replay_date == date(2026, 4, 2)


@pytest.mark.parametrize("status", ["partial", "blocked"])
def test_stage_a_adapter_rejects_non_complete_checkpoint(tmp_path, status: str) -> None:
    store = ReplayArtifactStore(tmp_path / f"{status}.sqlite3")
    features = (_stage_a_feature(date(2026, 4, 1), "510001", score=80.0),)
    _seed_stage_a(store, features, status=status)

    with pytest.raises(StageBIncompleteDateError, match=status):
        read_stage_b_source_date_page_from_stage_a(
            store=store,
            stage_a_contract=_stage_a_contract(),
            stage_b_contract=_contract(),
            after_cursor=None,
            max_dates=1,
            max_feature_rows=10,
            stage_a_page_rows=2,
        )


def test_stage_a_adapter_requires_a_sealed_checkpoint(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "missing.sqlite3")

    with pytest.raises(StageBIncompleteDateError, match="checkpoint"):
        read_stage_b_source_date_page_from_stage_a(
            store=store,
            stage_a_contract=_stage_a_contract(),
            stage_b_contract=_contract(),
            after_cursor=None,
            max_dates=1,
            max_feature_rows=10,
            stage_a_page_rows=2,
        )


def test_stage_a_adapter_reads_complete_dates_by_date_code_keyset(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "complete.sqlite3")
    dates = (date(2026, 4, 1), date(2026, 4, 2))
    features = tuple(
        _stage_a_feature(replay_date, code, score=90.0 - index)
        for replay_date in dates
        for index, code in enumerate(("510001", "510002", "510003"))
    )
    _seed_stage_a(store, features, status="complete")

    mapped = stage_b_contract_from_stage_a(_stage_a_contract())
    assert mapped == _contract()
    first = read_stage_b_source_date_page_from_stage_a(
        store=store,
        stage_a_contract=_stage_a_contract(),
        stage_b_contract=mapped,
        after_cursor=None,
        max_dates=1,
        max_feature_rows=5,
        stage_a_page_rows=2,
    )
    second = read_stage_b_source_date_page_from_stage_a(
        store=store,
        stage_a_contract=_stage_a_contract(),
        stage_b_contract=mapped,
        after_cursor=first.next_cursor,
        max_dates=1,
        max_feature_rows=5,
        stage_a_page_rows=2,
    )

    assert len(first.source_dates) == len(second.source_dates) == 1
    assert first.source_dates[0].manifest.replay_date == dates[0]
    assert second.source_dates[0].manifest.replay_date == dates[1]
    assert first.has_more is True
    assert second.has_more is False
    assert first.next_cursor == (dates[0], "510003")
    assert first.source_rows_read <= 5
    assert second.source_rows_read <= 5
    assert first.stage_a_checkpoint_hash == second.stage_a_checkpoint_hash
    assert first.stage_a_completion_identity_hash
    assert first.source_dates[0].manifest.upstream_feature_hashes == tuple(
        (item.asset_code, item.content_hash) for item in features[:3]
    )

    progress = run_stage_b_continuation(
        store=store,
        contract=mapped,
        request=_request(max_dates=2),
        source_dates=(*first.source_dates, *second.source_dates),
        source_has_more=False,
    )
    assert progress.complete is True
    assert progress.processed_dates == 2


def test_stage_a_adapter_rejects_contract_identity_mismatch(tmp_path) -> None:
    store = ReplayArtifactStore(tmp_path / "mismatch.sqlite3")
    features = (_stage_a_feature(date(2026, 4, 1), "510001", score=80.0),)
    _seed_stage_a(store, features, status="complete")

    with pytest.raises(NewStageBReplayIdentityRequiredError, match="contract"):
        read_stage_b_source_date_page_from_stage_a(
            store=store,
            stage_a_contract=_stage_a_contract(),
            stage_b_contract=replace(
                _contract(), score_manifest_hash=_hash("wrong-score-contract")
            ),
            after_cursor=None,
            max_dates=1,
            max_feature_rows=10,
            stage_a_page_rows=2,
        )
