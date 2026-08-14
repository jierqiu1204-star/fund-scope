from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.strategy_lab.late_day_turnaround_storage import API_DISABLED
from app.services.workflows.late_day_turnaround import HARD_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_late_day_api_is_read_only_and_default_off(client) -> None:
    response = await client.get(
        "/api/short-research/late-day-turnaround/candidates?universe=ashare"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["unavailable_reason"] == API_DISABLED
    assert payload["items"] == []
    assert payload["research_only"] is True
    assert payload["production_mutation_allowed"] is False
    assert payload["notification_provenance"] == "none"
    assert payload["execution_provenance"] == "none"


def test_late_day_scheduler_is_independently_gated(app) -> None:
    assert app.state.scheduler.get_job("late_day_turnaround_materialize_etf") is None
    assert app.state.scheduler.get_job("late_day_turnaround_materialize_ashare") is None
    assert HARD_TIMEOUT_SECONDS <= 55.0


@pytest.mark.asyncio
async def test_contract_endpoint_does_not_require_materialized_data(client) -> None:
    response = await client.get("/api/short-research/late-day-turnaround/contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["universes"] == ["etf", "ashare"]
    assert payload["research_only"] is True
    assert payload["production_mutation_allowed"] is False
    assert payload["contract"]["decision_window"] == ["14:30", "14:50"]


def test_materialization_checkpoints_are_shanghai_times() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    checkpoints = [
        datetime(2026, 8, 14, hour, minute, tzinfo=shanghai).time()
        for hour, minute in [(14, 30), (14, 40), (14, 50)]
    ]
    assert [value.isoformat(timespec="minutes") for value in checkpoints] == [
        "14:30",
        "14:40",
        "14:50",
    ]
