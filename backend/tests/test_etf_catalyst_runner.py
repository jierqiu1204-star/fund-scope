from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import EtfCatalystReceipt, EtfCatalystRunCheckpoint
from app.services.etf_catalyst_shadow.ingestion import (
    ReceiptInput,
    ingest_receipt,
    register_approved_sources,
)
from app.services.etf_catalyst_shadow.runner import (
    CatalystRunAlreadyActiveError,
    CatalystRunAlreadyAttemptedError,
    SourceFetchBatch,
    _acquire,
    _checkpoint,
    run_receipt_processing_batch,
    run_source_fetch_batch,
)

RECEIVED = datetime(2026, 7, 19, 15, 20)
FETCHED = RECEIVED + timedelta(seconds=1)
PUBLISHED = RECEIVED - timedelta(hours=1)


def test_checkpoint_status_constraint_matches_every_runner_state() -> None:
    constraints = {
        str(constraint.sqltext)
        for constraint in EtfCatalystRunCheckpoint.__table__.constraints
        if hasattr(constraint, "sqltext")
    }

    assert (
        "status IN ('idle', 'running', 'partial', 'complete', 'failed')"
        in constraints
    )


def _item(content: str) -> ReceiptInput:
    return ReceiptInput(
        source_id="ndrc_policy",
        fetch_state="item",
        fetched_at=FETCHED,
        first_received_at=RECEIVED,
        external_id="bounded-policy-1",
        canonical_url="https://www.ndrc.gov.cn/xxgk/wjk/bounded.html",
        source_published_at=PUBLISHED,
        raw_content=content,
        raw_content_ref=f"raw://bounded/{content}",
        metadata={"supported_theme_ids": ["新能源"]},
    )


def _empty() -> ReceiptInput:
    return ReceiptInput(
        source_id="ndrc_policy",
        fetch_state="successful_empty",
        fetched_at=FETCHED,
        first_received_at=RECEIVED,
        observation_key="ndrc:2026-07-19:empty",
    )


@pytest.mark.asyncio
async def test_bounded_fetch_records_duplicates_corrections_cursor_and_latency(
    app,
) -> None:
    calls: list[tuple[str | None, int]] = []

    async def fetcher(_source_id, _endpoint, cursor, maximum_items):
        calls.append((cursor, maximum_items))
        return SourceFetchBatch(
            receipts=(_item("v1"), _item("v1"), _item("v2"), _empty()),
            next_cursor="cursor-4",
            complete=False,
        )

    async with app.state.db.session() as session:
        result = await run_source_fetch_batch(
            session,
            source_id="ndrc_policy",
            session_key="2026-07-19-close",
            fetcher=fetcher,
        )

    assert calls == [(None, 20)]
    assert result.state == "partial"
    assert result.received_count == 4
    assert result.new_receipt_count == 3
    assert result.duplicate_count == 1
    assert result.correction_count == 1
    assert result.fetch_states == {"item": 3, "successful_empty": 1}
    assert result.cursor_after == "cursor-4"
    assert result.latency_ms >= 0
    assert len(result.batch_hash) == 64

    async with app.state.db.session() as session:
        checkpoint = await session.scalar(
            select(EtfCatalystRunCheckpoint).where(
                EtfCatalystRunCheckpoint.run_kind == "fetch"
            )
        )
        assert checkpoint is not None
        assert checkpoint.details_json["worker_count"] == 1
        assert checkpoint.details_json["operation_timeout_seconds"] == 50
        with pytest.raises(CatalystRunAlreadyAttemptedError):
            await run_source_fetch_batch(
                session,
                source_id="ndrc_policy",
                session_key="2026-07-19-close",
                fetcher=fetcher,
            )


@pytest.mark.asyncio
async def test_timeout_records_one_auditable_outage_and_prevents_retry(app) -> None:
    async def stalled(_source_id, _endpoint, _cursor, _maximum_items):
        await asyncio.sleep(0.05)
        return SourceFetchBatch(receipts=(), next_cursor=None, complete=True)

    async with app.state.db.session() as session:
        result = await run_source_fetch_batch(
            session,
            source_id="csrc_announcement",
            session_key="2026-07-19-close",
            fetcher=stalled,
            timeout_seconds=0.01,
        )
        outage = await session.scalar(
            select(EtfCatalystReceipt).where(
                EtfCatalystReceipt.receipt_id == result.receipt_ids[0]
            )
        )
        assert outage is not None
        assert outage.fetch_state == "unavailable"
        assert outage.error_summary.startswith("TimeoutError:")
        with pytest.raises(CatalystRunAlreadyAttemptedError):
            await run_source_fetch_batch(
                session,
                source_id="csrc_announcement",
                session_key="2026-07-19-close",
                fetcher=stalled,
                timeout_seconds=0.01,
            )


@pytest.mark.asyncio
async def test_extraction_and_mapping_batches_resume_at_twenty_or_less(app) -> None:
    async with app.state.db.session() as session:
        await register_approved_sources(session)
        for index in range(3):
            await ingest_receipt(
                session,
                ReceiptInput(
                    source_id="miit_policy",
                    fetch_state="successful_empty",
                    fetched_at=FETCHED,
                    first_received_at=RECEIVED,
                    observation_key=f"miit:processing:{index}",
                ),
            )
        processed: list[str] = []

        async def processor(receipt: EtfCatalystReceipt) -> None:
            processed.append(receipt.receipt_id)

        first = await run_receipt_processing_batch(
            session,
            phase="extraction",
            processor=processor,
            maximum_items=2,
        )
        second = await run_receipt_processing_batch(
            session,
            phase="extraction",
            processor=processor,
            maximum_items=2,
        )

    assert len(first["processed_receipt_ids"]) <= 2
    assert set(first["processed_receipt_ids"]).isdisjoint(
        second["processed_receipt_ids"]
    )
    assert len(processed) == len(set(processed)) == 3


@pytest.mark.asyncio
async def test_concurrent_fetch_is_rejected_by_single_worker_lock(app) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def waiting(_source_id, _endpoint, _cursor, _maximum_items):
        started.set()
        await release.wait()
        return SourceFetchBatch(receipts=(), next_cursor=None, complete=True)

    async def first_run():
        async with app.state.db.session() as session:
            return await run_source_fetch_batch(
                session,
                source_id="sse_announcement",
                session_key="2026-07-20-close",
                fetcher=waiting,
            )

    task = asyncio.create_task(first_run())
    await started.wait()
    try:
        async with app.state.db.session() as session:
            with pytest.raises(CatalystRunAlreadyActiveError):
                await run_source_fetch_batch(
                    session,
                    source_id="szse_announcement",
                    session_key="2026-07-20-close",
                    fetcher=waiting,
                )
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_database_lease_rejects_a_stale_cross_session_claim(app) -> None:
    async with app.state.db.session() as session:
        checkpoint = await _checkpoint(session, run_kind="fetch")
        checkpoint_id = checkpoint.id

    async with (
        app.state.db.session() as first_session,
        app.state.db.session() as second_session,
    ):
        first = await first_session.get(EtfCatalystRunCheckpoint, checkpoint_id)
        stale = await second_session.get(EtfCatalystRunCheckpoint, checkpoint_id)
        assert first is not None
        assert stale is not None
        await second_session.commit()

        await _acquire(first_session, first, now=RECEIVED)
        with pytest.raises(CatalystRunAlreadyActiveError):
            await _acquire(second_session, stale, now=RECEIVED)
