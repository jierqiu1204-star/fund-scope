from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.entities import EtfDailyWorkflowLock
from app.services.workflows import etf_daily_research


class _CoverageBarrier:
    expected_codes = ["510300"]
    included_codes = ["510300"]
    excluded: list[dict[str, str]] = []
    coverage_ratio = 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "expected_count": len(self.expected_codes),
            "included_count": len(self.included_codes),
            "coverage_ratio": self.coverage_ratio,
        }


@pytest.mark.asyncio
async def test_etf_daily_research_workflow_uses_one_lock_and_sequences_publication(app, monkeypatch) -> None:
    events: list[str] = []

    async def fake_refresh(_session, *, as_of_date):
        assert as_of_date == date(2026, 7, 10)
        events.append("universe")
        return {"activated": 1, "authoritative": True}

    async def fake_sync(_session, **kwargs):
        assert kwargs["to_date"] == date(2026, 7, 10)
        events.append("sync")
        return {"failed": 0, "etfs": {"processed": 1, "skipped": 0}}

    async def fake_barrier(_session, *, as_of_trade_date, data_cutoff):
        assert as_of_trade_date == date(2026, 7, 10)
        assert data_cutoff == datetime(2026, 7, 10, 15, 0)
        events.append("coverage")
        return _CoverageBarrier()

    async def fake_materialize(_session, **kwargs):
        assert kwargs["trade_date"] == date(2026, 7, 10)
        assert kwargs["decision_cutoff"] == datetime(2026, 7, 10, 15, 0)
        assert kwargs["source_availability_cutoff"] == datetime(2026, 7, 10, 15, 0)
        assert callable(kwargs["batch_progress_callback"])
        events.append("score")
        return SimpleNamespace(id=12)

    async def fake_publish(_session, *, run_id):
        assert run_id == 12
        events.append("publish")
        return SimpleNamespace(id=12, publication_state="published")

    monkeypatch.setattr(etf_daily_research, "refresh_etf_universe", fake_refresh)
    monkeypatch.setattr(etf_daily_research, "sync_short_research_data_with_tracking_priority", fake_sync)
    monkeypatch.setattr(etf_daily_research, "build_etf_coverage_barrier", fake_barrier)
    monkeypatch.setattr(etf_daily_research, "materialize_dual_ranking_snapshot", fake_materialize)
    monkeypatch.setattr(etf_daily_research, "publish_dual_ranking_snapshot", fake_publish)

    async with app.state.db.session() as session:
        result = await etf_daily_research.run_daily_etf_research_workflow(
            session,
            trade_date=date(2026, 7, 10),
        )

    assert events == ["universe", "sync", "coverage", "score", "publish"]
    assert result["published_run_id"] == 12
    assert result["workflow_status"] == "success"

    async with app.state.db.session() as session:
        lock = await session.scalar(select(EtfDailyWorkflowLock).where(EtfDailyWorkflowLock.trade_date == date(2026, 7, 10)))

    assert lock is not None
    assert lock.status == "success"


@pytest.mark.asyncio
async def test_etf_daily_research_workflow_skips_duplicate_running_trade_date(app) -> None:
    async with app.state.db.session() as first_session:
        assert await etf_daily_research.try_acquire_etf_daily_workflow_lock(first_session, date(2026, 7, 10))
        async with app.state.db.session() as second_session:
            result = await etf_daily_research.run_daily_etf_research_workflow(
                second_session,
                trade_date=date(2026, 7, 10),
            )
        await etf_daily_research.finish_etf_daily_workflow_lock(first_session, date(2026, 7, 10), "success", {})

    assert result["job_status"] == "skipped"
    assert result["workflow_status"] == "locked"


@pytest.mark.asyncio
async def test_etf_daily_research_workflow_reclaims_expired_running_lock(app) -> None:
    trade_date = date(2026, 7, 10)
    first_started_at = datetime(2026, 7, 10, 8, 0)
    within_lease = first_started_at + timedelta(minutes=29)
    after_lease = first_started_at + timedelta(minutes=31)

    async with app.state.db.session() as first_session:
        assert await etf_daily_research.try_acquire_etf_daily_workflow_lock(
            first_session,
            trade_date,
            now=first_started_at,
        )
    async with app.state.db.session() as second_session:
        assert not await etf_daily_research.try_acquire_etf_daily_workflow_lock(
            second_session,
            trade_date,
            now=within_lease,
        )
        assert await etf_daily_research.try_acquire_etf_daily_workflow_lock(
            second_session,
            trade_date,
            now=after_lease,
        )
        lock = await second_session.get(EtfDailyWorkflowLock, trade_date)

    assert lock is not None
    assert lock.status == "running"
    assert lock.started_at == after_lease


@pytest.mark.asyncio
async def test_etf_daily_research_lock_persists_ranking_batch_cursor(app) -> None:
    trade_date = date(2026, 7, 10)
    async with app.state.db.session() as session:
        assert await etf_daily_research.try_acquire_etf_daily_workflow_lock(
            session,
            trade_date,
        )
        await etf_daily_research.record_etf_ranking_batch_progress(
            session,
            trade_date,
            {"cursor": "510019", "remaining": 25, "processed": 20},
        )
        await etf_daily_research.record_etf_ranking_batch_progress(
            session,
            trade_date,
            {"cursor": "510039", "remaining": 5, "processed": 20},
        )
        lock = await session.get(EtfDailyWorkflowLock, trade_date)

    assert lock is not None
    assert lock.details_json["ranking_cursor"] == "510039"
    assert lock.details_json["ranking_remaining"] == 5
    assert [item["cursor"] for item in lock.details_json["ranking_batches"]] == [
        "510019",
        "510039",
    ]


@pytest.mark.asyncio
async def test_etf_daily_research_workflow_fails_closed_below_coverage_threshold(app, monkeypatch) -> None:
    class IncompleteCoverage(_CoverageBarrier):
        expected_codes = ["510300", "510500"]
        included_codes = ["510300"]
        excluded = [{"asset_code": "510500", "reason": "missing_trade_date_price"}]
        coverage_ratio = 0.5

    async def fake_refresh(_session, *, as_of_date):
        return {"activated": 0, "authoritative": True}

    async def fake_sync(_session, **_kwargs):
        return {"failed": 0, "etfs": {"processed": 1, "skipped": 0}}

    async def fake_barrier(_session, *, as_of_trade_date, data_cutoff):
        return IncompleteCoverage()

    monkeypatch.setattr(etf_daily_research, "refresh_etf_universe", fake_refresh)
    monkeypatch.setattr(etf_daily_research, "sync_short_research_data_with_tracking_priority", fake_sync)
    monkeypatch.setattr(etf_daily_research, "build_etf_coverage_barrier", fake_barrier)

    async with app.state.db.session() as session:
        result = await etf_daily_research.run_daily_etf_research_workflow(
            session,
            trade_date=date(2026, 7, 10),
        )

    assert result["job_status"] == "failed"
    assert result["workflow_status"] == "coverage_failed"
    assert result["coverage"]["coverage_ratio"] == 0.5


@pytest.mark.asyncio
async def test_etf_daily_research_workflow_reports_legacy_snapshot_publication_failure(app, monkeypatch) -> None:
    async def fake_refresh(_session, *, as_of_date):
        return {"activated": 0, "authoritative": True}

    async def fake_sync(_session, **_kwargs):
        return {"failed": 0, "etfs": {"processed": 1, "skipped": 0}}

    async def fake_barrier(_session, *, as_of_trade_date, data_cutoff):
        return _CoverageBarrier()

    async def fake_materialize(_session, **_kwargs):
        return SimpleNamespace(id=13)

    async def fake_publish(_session, *, run_id):
        raise etf_daily_research.SnapshotPublicationError("missing snapshot identity")

    monkeypatch.setattr(etf_daily_research, "refresh_etf_universe", fake_refresh)
    monkeypatch.setattr(etf_daily_research, "sync_short_research_data_with_tracking_priority", fake_sync)
    monkeypatch.setattr(etf_daily_research, "build_etf_coverage_barrier", fake_barrier)
    monkeypatch.setattr(etf_daily_research, "materialize_dual_ranking_snapshot", fake_materialize)
    monkeypatch.setattr(etf_daily_research, "publish_dual_ranking_snapshot", fake_publish)

    async with app.state.db.session() as session:
        result = await etf_daily_research.run_daily_etf_research_workflow(
            session,
            trade_date=date(2026, 7, 10),
        )

    assert result["signal_run_id"] == 13
    assert result["workflow_status"] == "publication_failed"
    assert result["job_status"] == "failed"


@pytest.mark.asyncio
async def test_generate_and_publish_etf_snapshot_never_starts_history_sync(app, monkeypatch) -> None:
    events: list[str] = []

    async def fail_sync(*_args, **_kwargs):
        raise AssertionError("lightweight post-close workflow must not synchronize history")

    async def fake_materialize(_session, **kwargs):
        assert kwargs == {
            "trade_date": date(2026, 7, 10),
            "decision_cutoff": datetime(2026, 7, 10, 15, 0),
            "source_availability_cutoff": datetime(2026, 7, 10, 15, 0),
        }
        events.append("materialize")
        return SimpleNamespace(id=21)

    async def fake_publish(_session, *, run_id):
        assert run_id == 21
        events.append("publish")
        return SimpleNamespace(id=21, publication_state="published")

    monkeypatch.setattr(etf_daily_research, "sync_short_research_data_with_tracking_priority", fail_sync)
    monkeypatch.setattr(etf_daily_research, "materialize_dual_ranking_snapshot", fake_materialize)
    monkeypatch.setattr(etf_daily_research, "publish_dual_ranking_snapshot", fake_publish)

    async with app.state.db.session() as session:
        run = await etf_daily_research.generate_and_publish_etf_snapshot(
            session,
            trade_date=date(2026, 7, 10),
            decision_cutoff=datetime(2026, 7, 10, 15, 0),
        )

    assert run.id == 21
    assert events == ["materialize", "publish"]


@pytest.mark.asyncio
async def test_etf_daily_research_workflow_stops_before_sync_when_universe_is_not_authoritative(
    app,
    monkeypatch,
) -> None:
    async def fake_refresh(_session, *, as_of_date):
        return {
            "authoritative": False,
            "discovery_status": "failure",
            "stale_universe": True,
            "discovery_error": "proxy connection refused",
        }

    async def fail_sync(*_args, **_kwargs):
        raise AssertionError("non-authoritative universe must stop before history sync")

    monkeypatch.setattr(etf_daily_research, "refresh_etf_universe", fake_refresh)
    monkeypatch.setattr(etf_daily_research, "sync_short_research_data_with_tracking_priority", fail_sync)

    async with app.state.db.session() as session:
        result = await etf_daily_research.run_daily_etf_research_workflow(
            session,
            trade_date=date(2026, 7, 10),
        )

    assert result["workflow_status"] == "universe_failed"
    assert result["job_status"] == "failed"
    assert result["universe"]["stale_universe"] is True


@pytest.mark.asyncio
async def test_etf_daily_research_workflow_rolls_back_before_recording_failed_lock(app, monkeypatch) -> None:
    trade_date = date(2026, 7, 11)

    async def fail_with_poisoned_transaction(session, *, as_of_date):
        assert as_of_date == trade_date
        session.add(EtfDailyWorkflowLock(trade_date=trade_date, status="running"))
        await session.flush()

    monkeypatch.setattr(etf_daily_research, "refresh_etf_universe", fail_with_poisoned_transaction)

    async with app.state.db.session() as session:
        with pytest.raises(IntegrityError):
            await etf_daily_research.run_daily_etf_research_workflow(
                session,
                trade_date=trade_date,
            )

    async with app.state.db.session() as session:
        lock = await session.get(EtfDailyWorkflowLock, trade_date)

    assert lock is not None
    assert lock.status == "failed"
    assert lock.finished_at is not None
