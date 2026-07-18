from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, literal, select

from app.models.entities import (
    EtfPriceHistory,
    EtfSignalValidationRun,
    EtfSignalValidationSourceEvent,
    EtfValidationContinuation,
    EtfValidationMaterializedSample,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    ValidationEvidenceImmutableError,
)
from app.services.strategy_lab import etf_validation_continuation
from app.services.strategy_lab.etf_validation_continuation import (
    MAX_VALIDATION_PAGE_SIZE,
    MAX_VALIDATION_SAMPLES_PER_SLICE,
    VALIDATION_RSS_LIMIT_BYTES,
    ValidationContinuationBusyError,
    ValidationContinuationIdentityError,
    ValidationContinuationRequest,
    ValidationMaterializedSample,
    build_production_validation_page_loader,
    run_validation_continuation_slice,
)


def _request(
    run: EtfSignalValidationRun,
    *,
    page_size: int = 500,
    max_samples: int = 5_000,
    execution_contract_hash: str = "execution-v1",
    horizons: tuple[int, ...] = (1, 3, 5, 10),
    admission_deadline_seconds: float = 45.0,
    worker_deadline_seconds: float = 55.0,
    process_deadline_seconds: float = 60.0,
) -> ValidationContinuationRequest:
    return ValidationContinuationRequest(
        validation_run_id=run.id,
        manifest_hash="manifest-v1",
        execution_contract_hash=execution_contract_hash,
        candidate_registry_hash="candidate-v1",
        horizon_set=horizons,
        schema_hash="validation-sample-v1",
        page_size=page_size,
        max_samples_per_slice=max_samples,
        admission_deadline_seconds=admission_deadline_seconds,
        worker_deadline_seconds=worker_deadline_seconds,
        process_deadline_seconds=process_deadline_seconds,
    )


def _sample(
    source_event: EtfSignalValidationSourceEvent,
    index: int,
) -> ValidationMaterializedSample:
    horizon = (1, 3, 5, 10)[index % 4]
    source_date = date(2026, 1, 1).fromordinal(date(2026, 1, 1).toordinal() + index // 40)
    asset_key = f"etf:{510000 + (index % 40):06d}"
    return ValidationMaterializedSample(
        source_event_id=source_event.id,
        source_event_hash=source_event.immutable_hash,
        source_date=source_date,
        horizon_sessions=horizon,
        asset_key=asset_key,
        status="completed",
        entry_date=source_date,
        exit_date=date.fromordinal(source_date.toordinal() + horizon + 1),
        adjusted_entry_price=1.0,
        adjusted_exit_price=1.01,
        gross_return=0.01,
        net_return=0.008,
        fee_rate=0.0005,
        slippage_rate=0.0005,
        total_cost_rate=0.002,
        adverse_drawdown=-0.01,
        interval_start=source_date,
        interval_end=date.fromordinal(source_date.toordinal() + horizon + 1),
        exclusion_reason=None,
        payload={"index": index},
    )


async def _seed_registered_run(session, *, suffix: str = ""):
    run = EtfSignalValidationRun(
        status="running",
        as_of_date=date(2026, 7, 17),
        ranking_source_kind="production_published",
        source_manifest_hash="manifest-v1",
        source_event_count=1,
    )
    session.add(run)
    await session.flush()
    event = EtfSignalValidationSourceEvent(
        validation_run_id=run.id,
        event_order=0,
        source_date=date(2026, 1, 1),
        ranking_source_kind="production_published",
        source_signal_run_id=1,
        source_replay_run_key=None,
        source_replay_contract_hash=None,
        source_event_hash=f"event-source{suffix}",
        ranking_contract_hash="ranking-v1",
        scope_hash="scope-v1",
        universe_snapshot_hash="universe-v1",
        input_snapshot_hash="input-v1",
        availability_cutoff=datetime(2026, 1, 1, 16, 0),
        score_version="final_score_v3",
        score_field="ranking_score",
        rule_version="final_score_v3_rule_v2",
        price_basis="total_return_adjusted",
        publication_state="published",
        scope_kind="full",
        source_status="success",
        immutable_hash="immutable-event",
    )
    session.add(event)
    await session.commit()
    return run, event


def test_validation_continuation_rejects_oversized_pages_and_slices() -> None:
    assert MAX_VALIDATION_PAGE_SIZE == 500
    assert MAX_VALIDATION_SAMPLES_PER_SLICE == 5_000
    with pytest.raises(ValueError, match="page_size"):
        ValidationContinuationRequest(
            validation_run_id=1,
            manifest_hash="m",
            execution_contract_hash="e",
            candidate_registry_hash="c",
            horizon_set=(5,),
            schema_hash="s",
            page_size=501,
        )
    with pytest.raises(ValueError, match="max_samples_per_slice"):
        ValidationContinuationRequest(
            validation_run_id=1,
            manifest_hash="m",
            execution_contract_hash="e",
            candidate_registry_hash="c",
            horizon_set=(5,),
            schema_hash="s",
            max_samples_per_slice=5_001,
        )


@pytest.mark.asyncio
async def test_validation_continuation_pages_resume_and_finalize_deterministically(app) -> None:
    async with app.state.db.session() as session:
        run, source_event = await _seed_registered_run(session)
        samples = tuple(
            sorted(
                (_sample(source_event, index) for index in range(1_200)),
                key=lambda item: item.key,
            )
        )

        async def load_page(checkpoint, limit):
            remaining = [sample for sample in samples if sample.key > checkpoint]
            return remaining[:limit]

        first = await run_validation_continuation_slice(
            session,
            request=_request(run, max_samples=500),
            page_loader=load_page,
        )
        second = await run_validation_continuation_slice(
            session,
            request=_request(run, max_samples=500),
            page_loader=load_page,
        )
        third = await run_validation_continuation_slice(
            session,
            request=_request(run, max_samples=500),
            page_loader=load_page,
        )
        continuation = await session.scalar(
            select(EtfValidationContinuation).where(
                EtfValidationContinuation.validation_run_id == run.id
            )
        )
        sample_count = await session.scalar(
            select(func.count())
            .select_from(EtfValidationMaterializedSample)
            .where(EtfValidationMaterializedSample.continuation_id == continuation.id)
        )

        second_run, second_source_event = await _seed_registered_run(
            session,
            suffix="-page-size",
        )
        second_samples = tuple(
            sorted(
                (_sample(second_source_event, index) for index in range(1_200)),
                key=lambda item: item.key,
            )
        )

        async def load_second_page(checkpoint, limit):
            remaining = [sample for sample in second_samples if sample.key > checkpoint]
            return remaining[:limit]

        different_page_size = await run_validation_continuation_slice(
            session,
            request=_request(second_run, page_size=200),
            page_loader=load_second_page,
        )

    assert first.status == second.status == "partial"
    assert first.final_aggregate_hash is None
    assert second.final_aggregate_hash is None
    assert third.status == "complete"
    assert third.final_aggregate_hash
    assert first.processed_sample_count == 500
    assert second.processed_sample_count == 500
    assert third.processed_sample_count == 200
    assert third.max_page_sql_statements <= 8
    assert continuation is not None
    assert continuation.processed_sample_count == sample_count == 1_200
    assert continuation.final_aggregate_hash == third.final_aggregate_hash
    assert different_page_size.status == "complete"
    assert different_page_size.final_aggregate_hash == third.final_aggregate_hash


@pytest.mark.asyncio
async def test_validation_continuation_stops_admitting_pages_after_45_seconds(app) -> None:
    clock = [0.0]
    calls = 0

    async with app.state.db.session() as session:
        run, source_event = await _seed_registered_run(session, suffix="-deadline")
        samples = tuple(
            sorted(
                (_sample(source_event, index) for index in range(700)),
                key=lambda item: item.key,
            )
        )

        async def load_page(checkpoint, limit):
            nonlocal calls
            calls += 1
            clock[0] += 46.0
            remaining = [sample for sample in samples if sample.key > checkpoint]
            return remaining[:limit]

        result = await run_validation_continuation_slice(
            session,
            request=_request(run),
            page_loader=load_page,
            monotonic=lambda: clock[0],
            rss_bytes=lambda: 1,
        )

    assert calls == 1
    assert result.status == "partial"
    assert result.processed_sample_count == 500
    assert result.stop_reason == "admission_deadline"
    assert result.final_aggregate_hash is None


@pytest.mark.asyncio
async def test_validation_continuation_keeps_checkpoint_reserve_after_loader(app) -> None:
    clock = [0.0]
    async with app.state.db.session() as session:
        run, source_event = await _seed_registered_run(session, suffix="-reserve")
        samples = tuple(
            sorted(
                (_sample(source_event, index) for index in range(20)),
                key=lambda item: item.key,
            )
        )

        async def load_page(checkpoint, limit):
            clock[0] = 52.9
            return [sample for sample in samples if sample.key > checkpoint][:limit]

        result = await run_validation_continuation_slice(
            session,
            request=_request(run),
            page_loader=load_page,
            monotonic=lambda: clock[0],
            rss_bytes=lambda: 1,
        )
        stored = await session.scalar(
            select(func.count()).select_from(EtfValidationMaterializedSample)
        )

    assert result.status == "partial"
    assert result.stop_reason == "worker_deadline"
    assert result.processed_sample_count == 0
    assert stored == 0


@pytest.mark.asyncio
async def test_validation_continuation_cancels_inflight_loader_before_worker_deadline(app) -> None:
    cancelled = False
    async with app.state.db.session() as session:
        run, _source_event = await _seed_registered_run(session, suffix="-cancel")

        async def blocked_loader(_checkpoint, _limit):
            nonlocal cancelled
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True

        result = await run_validation_continuation_slice(
            session,
            request=_request(
                run,
                admission_deadline_seconds=0.5,
                worker_deadline_seconds=0.8,
                process_deadline_seconds=1.0,
            ),
            page_loader=blocked_loader,
        )
        continuation = await session.get(
            EtfValidationContinuation,
            result.continuation_id,
        )
        connection_is_reusable = await session.scalar(select(literal(1)))

    assert cancelled is True
    assert result.status == "partial"
    assert result.stop_reason == "worker_deadline"
    assert result.processed_sample_count == 0
    assert result.elapsed_seconds < 1.0
    assert continuation is not None
    assert continuation.lease_token is None
    assert continuation.lease_expires_at is None
    assert connection_is_reusable == 1


@pytest.mark.asyncio
async def test_validation_process_deadline_includes_initial_database_work(
    app,
    monkeypatch,
) -> None:
    cancelled = False

    async def blocked_initial_load(_session, _request):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    monkeypatch.setattr(
        etf_validation_continuation,
        "_load_or_create_continuation",
        blocked_initial_load,
    )
    async with app.state.db.session() as session:
        run, _source_event = await _seed_registered_run(session, suffix="-initial-budget")

        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                run_validation_continuation_slice(
                    session,
                    request=_request(
                        run,
                        admission_deadline_seconds=0.05,
                        worker_deadline_seconds=0.1,
                        process_deadline_seconds=0.15,
                    ),
                    page_loader=lambda _checkpoint, _limit: asyncio.sleep(0),
                ),
                timeout=0.5,
            )
        elapsed = time.monotonic() - started

    assert cancelled is True
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_validation_continuation_rejects_active_lease_and_rss_before_loading(app) -> None:
    calls = 0
    async with app.state.db.session() as session:
        run, _source_event = await _seed_registered_run(session, suffix="-lease")
        request = _request(run)

        async def load_page(_checkpoint, _limit):
            nonlocal calls
            calls += 1
            return []

        rss_result = await run_validation_continuation_slice(
            session,
            request=request,
            page_loader=load_page,
            rss_bytes=lambda: VALIDATION_RSS_LIMIT_BYTES + 1,
        )
        continuation = await session.get(
            EtfValidationContinuation,
            rss_result.continuation_id,
        )
        assert continuation is not None
        continuation.lease_token = "active-worker"
        continuation.lease_expires_at = datetime.utcnow() + timedelta(seconds=30)
        continuation.status = "running"
        await session.commit()

        with pytest.raises(ValidationContinuationBusyError, match="lease"):
            await run_validation_continuation_slice(
                session,
                request=request,
                page_loader=load_page,
            )

    assert calls == 0
    assert rss_result.status == "partial"
    assert rss_result.stop_reason == "rss_limit"


@pytest.mark.asyncio
async def test_validation_continuation_rejects_contract_drift_and_samples_are_immutable(app) -> None:
    async with app.state.db.session() as session:
        run, source_event = await _seed_registered_run(session, suffix="-identity")
        sample = _sample(source_event, 0)

        async def load_page(checkpoint, limit):
            return [sample] if sample.key > checkpoint else []

        await run_validation_continuation_slice(
            session,
            request=_request(run),
            page_loader=load_page,
        )
        with pytest.raises(ValidationContinuationIdentityError, match="new validation"):
            await run_validation_continuation_slice(
                session,
                request=_request(run, execution_contract_hash="execution-v2"),
                page_loader=load_page,
            )

        stored = await session.scalar(select(EtfValidationMaterializedSample))
        assert stored is not None
        stored.net_return = 9.0
        with pytest.raises(ValidationEvidenceImmutableError, match="immutable"):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_validation_page_and_checkpoint_rollback_together_before_commit(app) -> None:
    async with app.state.db.session() as session:
        run, source_event = await _seed_registered_run(session, suffix="-rollback")
        run_id = run.id
        request = _request(run)
        samples = tuple(
            sorted(
                (_sample(source_event, index) for index in range(20)),
                key=lambda item: item.key,
            )
        )

        async def load_page(checkpoint, limit):
            remaining = [sample for sample in samples if sample.key > checkpoint]
            return remaining[:limit]

        with pytest.raises(RuntimeError, match="injected page cancellation"):
            await run_validation_continuation_slice(
                session,
                request=request,
                page_loader=load_page,
                before_page_commit=lambda: (_ for _ in ()).throw(
                    RuntimeError("injected page cancellation")
                ),
            )
        continuation = await session.scalar(
            select(EtfValidationContinuation).where(
                EtfValidationContinuation.validation_run_id == run_id
            )
        )
        count_after_failure = await session.scalar(
            select(func.count()).select_from(EtfValidationMaterializedSample)
        )
        assert continuation is not None
        assert continuation.checkpoint_source_date is None
        assert count_after_failure == 0

        resumed = await run_validation_continuation_slice(
            session,
            request=request,
            page_loader=load_page,
        )

    assert resumed.status == "complete"
    assert resumed.total_sample_count == 20


@pytest.mark.asyncio
async def test_production_page_loader_materializes_adjusted_t_plus_one_samples_only(app) -> None:
    source_date = date(2026, 7, 1)
    async with app.state.db.session() as session:
        source_run = ShortResearchSignalRun(
            status="success",
            as_of_date=source_date,
            as_of_trade_date=source_date,
        )
        session.add(source_run)
        await session.flush()
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=source_run.id,
                    asset_type="etf",
                    asset_code=code,
                    rank=index,
                    global_rank=index,
                    total_score=80.0 - index,
                    ranking_score=80.0 - index,
                    score_eligible=True,
                    conclusion="关注",
                )
                for index, code in enumerate(("510300", "510500"), start=1)
            ]
        )
        validation = EtfSignalValidationRun(
            status="running",
            as_of_date=date(2026, 7, 17),
            ranking_source_kind="production_published",
            source_manifest_hash="manifest-v1",
            source_event_count=1,
        )
        session.add(validation)
        await session.flush()
        source_event = EtfSignalValidationSourceEvent(
            validation_run_id=validation.id,
            event_order=0,
            source_date=source_date,
            ranking_source_kind="production_published",
            source_signal_run_id=source_run.id,
            source_event_hash="source-event",
            ranking_contract_hash="ranking-v1",
            scope_hash="scope-v1",
            universe_snapshot_hash="universe-v1",
            input_snapshot_hash="input-v1",
            availability_cutoff=datetime(2026, 7, 1, 16, 0),
            score_version="final_score_v3",
            score_field="ranking_score",
            rule_version="final_score_v3_rule_v2",
            price_basis="total_return_adjusted",
            publication_state="published",
            scope_kind="full",
            source_status="success",
            immutable_hash="immutable-event",
        )
        session.add(source_event)
        trade_dates = [
            date(2026, 7, 1),
            date(2026, 7, 2),
            date(2026, 7, 3),
            date(2026, 7, 6),
            date(2026, 7, 7),
        ]
        session.add_all(
            [
                EtfPriceHistory(
                    etf_code="510300",
                    trade_date=trade_date,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1_000_000.0,
                    turnover=100_000_000.0,
                    pct_change=0.0,
                    research_adjusted_value=price,
                    research_price_basis="total_return_adjusted",
                    data_provider="eastmoney",
                    decision_eligible=True,
                )
                for trade_date, price in zip(
                    trade_dates,
                    (1.0, 1.0, 1.01, 0.99, 1.03),
                    strict=True,
                )
            ]
        )
        session.add_all(
            [
                EtfPriceHistory(
                    etf_code="510500",
                    trade_date=trade_date,
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1_000_000.0,
                    turnover=100_000_000.0,
                    pct_change=0.0,
                    research_adjusted_value=1.0 if index < 2 else None,
                    research_price_basis=(
                        "total_return_adjusted" if index < 2 else "raw"
                    ),
                    data_provider="sina" if index >= 2 else "eastmoney",
                    decision_eligible=index < 2,
                )
                for index, trade_date in enumerate(trade_dates)
            ]
        )
        await session.commit()
        request = _request(validation, horizons=(1, 3))
        loader = build_production_validation_page_loader(
            session,
            request=request,
        )

        result = await run_validation_continuation_slice(
            session,
            request=request,
            page_loader=loader,
        )
        stored = (
            await session.scalars(
                select(EtfValidationMaterializedSample).order_by(
                    EtfValidationMaterializedSample.source_date,
                    EtfValidationMaterializedSample.horizon_sessions,
                    EtfValidationMaterializedSample.asset_key,
                )
            )
        ).all()

    assert result.status == "complete"
    assert len(stored) == 4
    assert {sample.price_basis for sample in stored} == {"total_return_adjusted"}
    completed = [sample for sample in stored if sample.asset_key == "etf:510300"]
    excluded = [sample for sample in stored if sample.asset_key == "etf:510500"]
    assert {sample.status for sample in completed} == {"completed"}
    assert {sample.status for sample in excluded} == {"excluded"}
    assert all(sample.net_return is None for sample in excluded)
    assert {sample.exclusion_reason for sample in excluded} == {
        "missing_horizon_exit_price"
    }
    one_session = next(
        sample for sample in completed if sample.horizon_sessions == 1
    )
    assert one_session.entry_date == date(2026, 7, 2)
    assert one_session.exit_date == date(2026, 7, 3)
    assert one_session.gross_return == pytest.approx(0.01)
    assert one_session.net_return == pytest.approx(0.008)


@pytest.mark.asyncio
async def test_production_page_uses_one_batched_calendar_and_price_query(app) -> None:
    source_dates = (
        date(2026, 6, 1),
        date(2026, 6, 8),
        date(2026, 6, 15),
        date(2026, 6, 22),
    )
    async with app.state.db.session() as session:
        validation = EtfSignalValidationRun(
            status="running",
            as_of_date=date(2026, 7, 17),
            ranking_source_kind="production_published",
            source_manifest_hash="manifest-v1",
            source_event_count=len(source_dates),
        )
        session.add(validation)
        await session.flush()
        for event_order, source_date in enumerate(source_dates):
            source_run = ShortResearchSignalRun(
                status="success",
                as_of_date=source_date,
                as_of_trade_date=source_date,
            )
            session.add(source_run)
            await session.flush()
            code = f"{510300 + event_order:06d}"
            session.add(
                ShortResearchSignalItem(
                    run_id=source_run.id,
                    asset_type="etf",
                    asset_code=code,
                    rank=1,
                    global_rank=1,
                    total_score=80.0,
                    ranking_score=80.0,
                    score_eligible=True,
                    conclusion="关注",
                )
            )
            session.add(
                EtfSignalValidationSourceEvent(
                    validation_run_id=validation.id,
                    event_order=event_order,
                    source_date=source_date,
                    ranking_source_kind="production_published",
                    source_signal_run_id=source_run.id,
                    source_event_hash=f"source-event-{event_order}",
                    ranking_contract_hash="ranking-v1",
                    scope_hash="scope-v1",
                    universe_snapshot_hash="universe-v1",
                    input_snapshot_hash="input-v1",
                    availability_cutoff=datetime.combine(
                        source_date,
                        datetime.min.time(),
                    )
                    + timedelta(hours=16),
                    score_version="final_score_v3",
                    score_field="ranking_score",
                    rule_version="final_score_v3_rule_v2",
                    price_basis="total_return_adjusted",
                    publication_state="published",
                    scope_kind="full",
                    source_status="success",
                    immutable_hash=f"immutable-event-{event_order}",
                )
            )
            for offset, price in enumerate((1.0, 1.01, 1.02)):
                trade_date = source_date + timedelta(days=offset)
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=trade_date,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=1_000_000.0,
                        turnover=100_000_000.0,
                        pct_change=0.0,
                        research_adjusted_value=price,
                        research_price_basis="total_return_adjusted",
                        data_provider="eastmoney",
                        decision_eligible=True,
                    )
                )
        await session.commit()
        request = _request(validation, horizons=(1,))
        loader = build_production_validation_page_loader(
            session,
            request=request,
        )
        connection = await session.connection()
        loader_sql_count = 0

        def count_loader_sql(*_args) -> None:
            nonlocal loader_sql_count
            loader_sql_count += 1

        sqlalchemy_event.listen(
            connection.sync_connection,
            "before_cursor_execute",
            count_loader_sql,
        )
        try:
            page = await loader((date.min, -1, ""), 500)
        finally:
            sqlalchemy_event.remove(
                connection.sync_connection,
                "before_cursor_execute",
                count_loader_sql,
            )

        result = await run_validation_continuation_slice(
            session,
            request=request,
            page_loader=loader,
        )

    assert len(page) == 4
    assert loader_sql_count == 3
    assert 5 <= result.max_page_sql_statements <= 8
