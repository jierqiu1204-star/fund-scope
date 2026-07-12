from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

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
        return {"activated": 1}

    async def fake_sync(_session, **kwargs):
        assert kwargs["to_date"] == date(2026, 7, 10)
        events.append("sync")
        return {"failed": 0, "etfs": {"processed": 1, "skipped": 0}}

    async def fake_barrier(_session, *, as_of_trade_date):
        assert as_of_trade_date == date(2026, 7, 10)
        events.append("coverage")
        return _CoverageBarrier()

    async def fake_generate(_session, **kwargs):
        assert kwargs == {"as_of_date": date(2026, 7, 10), "asset_type": "etf"}
        events.append("score")
        return SimpleNamespace(id=12)

    async def fake_publish(_session, *, run_id):
        assert run_id == 12
        events.append("publish")
        return SimpleNamespace(id=12, publication_state="published")

    monkeypatch.setattr(etf_daily_research, "refresh_etf_universe", fake_refresh)
    monkeypatch.setattr(etf_daily_research, "sync_short_research_data_with_tracking_priority", fake_sync)
    monkeypatch.setattr(etf_daily_research, "build_etf_coverage_barrier", fake_barrier)
    monkeypatch.setattr(etf_daily_research, "run_signal_generation", fake_generate)
    monkeypatch.setattr(etf_daily_research, "publish_full_snapshot", fake_publish)

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
async def test_etf_daily_research_workflow_fails_closed_below_coverage_threshold(app, monkeypatch) -> None:
    class IncompleteCoverage(_CoverageBarrier):
        expected_codes = ["510300", "510500"]
        included_codes = ["510300"]
        excluded = [{"asset_code": "510500", "reason": "missing_trade_date_price"}]
        coverage_ratio = 0.5

    async def fake_refresh(_session, *, as_of_date):
        return {"activated": 0}

    async def fake_sync(_session, **_kwargs):
        return {"failed": 0, "etfs": {"processed": 1, "skipped": 0}}

    async def fake_barrier(_session, *, as_of_trade_date):
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
        return {"activated": 0}

    async def fake_sync(_session, **_kwargs):
        return {"failed": 0, "etfs": {"processed": 1, "skipped": 0}}

    async def fake_barrier(_session, *, as_of_trade_date):
        return _CoverageBarrier()

    async def fake_generate(_session, **_kwargs):
        return SimpleNamespace(id=13)

    async def fake_publish(_session, *, run_id):
        raise etf_daily_research.SnapshotPublicationError("missing snapshot identity")

    monkeypatch.setattr(etf_daily_research, "refresh_etf_universe", fake_refresh)
    monkeypatch.setattr(etf_daily_research, "sync_short_research_data_with_tracking_priority", fake_sync)
    monkeypatch.setattr(etf_daily_research, "build_etf_coverage_barrier", fake_barrier)
    monkeypatch.setattr(etf_daily_research, "run_signal_generation", fake_generate)
    monkeypatch.setattr(etf_daily_research, "publish_full_snapshot", fake_publish)

    async with app.state.db.session() as session:
        result = await etf_daily_research.run_daily_etf_research_workflow(
            session,
            trade_date=date(2026, 7, 10),
        )

    assert result["signal_run_id"] == 13
    assert result["workflow_status"] == "publication_failed"
    assert result["job_status"] == "failed"
