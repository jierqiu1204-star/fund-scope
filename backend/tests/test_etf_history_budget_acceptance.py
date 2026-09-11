"""The whole history slice, including preparation, must fit its outer budget."""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from test_etf_research_history_sync import (
    _Fetcher,
    _readiness_with_observed_calendar,
    _sync_result,
)

from app.services.workflows import etf_research_history_sync as coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize("input_repair", [True, False])
@pytest.mark.parametrize("preparation_delay", [0, 0.03])
@pytest.mark.parametrize("max_seconds", [None, 5])
async def test_preparation_and_worker_share_the_same_time_budget(monkeypatch, input_repair, preparation_delay, max_seconds):
    async def no_lease(_session):
        return None

    async def readiness(_session, **_kwargs):
        await asyncio.sleep(preparation_delay)
        return _readiness_with_observed_calendar()

    async def recent(_session, **_kwargs):
        return []

    captured = {}
    started = asyncio.get_running_loop().time()

    async def worker(_session, *, request, **_kwargs):
        captured["request"] = request
        captured["preparation"] = asyncio.get_running_loop().time() - started
        return _sync_result()

    monkeypatch.setattr(coordinator, "_active_history_lease", no_lease)
    monkeypatch.setattr(coordinator, "read_etf_history_readiness", readiness)
    monkeypatch.setattr(coordinator, "_recent_lane_slices", recent)
    monkeypatch.setattr(coordinator, "PublicationAdjustedHistoryFetcher", _Fetcher)
    monkeypatch.setattr(coordinator, "run_bounded_history_sync_slice", worker)

    result = await coordinator.run_post_publication_etf_research_history_slice(
        object(), target_date=date(2026, 9, 10), input_repair=input_repair, max_seconds=max_seconds,
    )
    if max_seconds is not None:
        assert not captured
        assert result["status"] == "partial"
        assert result["reason"] == "research_history_worker_budget_exhausted"
        return
    request = captured["request"]
    assert result["status"] == "partial"
    assert request.admission_deadline_seconds < request.worker_deadline_seconds < request.process_deadline_seconds
    assert captured["preparation"] + request.process_deadline_seconds < coordinator.RESEARCH_WORKFLOW_TIMEOUT_SECONDS
