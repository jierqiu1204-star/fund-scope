from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services import scheduler as scheduler_module
from app.services.strategy_lab.late_day_turnaround_eastmoney import (
    MAX_CONCURRENCY,
    MAX_DECLARED_POOL,
    MAX_RESPONSE_BYTES,
    FiveMinuteBar,
    _aggregate_pairs,
    _declared_pool_from_details,
)
from app.services.strategy_lab.late_day_turnaround_materializer import (
    _daily_proxy_observations,
)
from app.services.strategy_lab.late_day_turnaround_storage import (
    NO_COMPLETED_MANIFEST,
)
from app.services.workflows import late_day_turnaround as workflow_module

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_five_minute_pairs_form_closed_ten_minute_bars() -> None:
    started = datetime(2026, 8, 14, 9, 30, tzinfo=SHANGHAI)
    rows = [
        FiveMinuteBar(started, 10, 11, 9.5, 10.5, 100, 1_000),
        FiveMinuteBar(started + timedelta(minutes=5), 10.5, 12, 10, 11.5, 120, 1_200),
        # An unmatched final interval is not promoted into a closed ten-minute bar.
        FiveMinuteBar(started + timedelta(minutes=10), 11.5, 12, 11, 11.8, 80, 800),
    ]
    bars = _aggregate_pairs(rows)
    assert len(bars) == 1
    assert bars[0]["bar_start"] == started
    assert bars[0]["bar_end"] == started + timedelta(minutes=10)
    assert bars[0]["raw_high"] == 12
    assert bars[0]["amount"] == 2_200
    assert MAX_DECLARED_POOL <= 20
    assert MAX_CONCURRENCY <= 4
    assert MAX_RESPONSE_BYTES <= 8 * 1024 * 1024


def test_checkpoint_resume_reuses_a_bounded_valid_declared_pool() -> None:
    details = {
        "declared_pool": [
            {"asset_code": "600001", "asset_name": "first"},
            {"asset_code": "600001", "asset_name": "duplicate"},
            {"asset_code": "bad", "asset_name": "invalid"},
            *[
                {"asset_code": f"{index:06d}", "asset_name": str(index)}
                for index in range(2, 30)
            ],
        ]
    }

    pool = _declared_pool_from_details(details)

    assert pool[0] == ("600001", "first")
    assert len(pool) == MAX_DECLARED_POOL
    assert len({code for code, _name in pool}) == len(pool)
    assert _declared_pool_from_details("not-json") == []


@pytest.mark.asyncio
async def test_daily_proxy_reads_only_bounded_declared_pool_pages() -> None:
    class EmptyResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class RecordingSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def execute(self, statement, params):
            self.calls.append((str(statement), dict(params)))
            return EmptyResult()

    session = RecordingSession()
    universe = [(f"{index:06d}", str(index)) for index in range(1, 514)]
    observations = await _daily_proxy_observations(
        session,
        universe=universe,
        decision_at=datetime(2026, 8, 14, 14, 30, tzinfo=SHANGHAI),
    )

    assert observations == []
    assert len(session.calls) == 2
    assert all("WHERE asset_code IN" in statement for statement, _params in session.calls)
    page_sizes = [
        len([key for key in params if key.startswith("proxy_code_")])
        for _statement, params in session.calls
    ]
    assert page_sizes == [512, 1]


@pytest.mark.asyncio
async def test_materializer_uses_exact_fixed_checkpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_materialize(session, *, universe, decision_at):
        captured.update(
            session=session,
            universe=universe,
            decision_at=decision_at,
        )
        return {"status": "complete"}

    monkeypatch.setattr(
        workflow_module,
        "materialize_late_day_turnaround",
        fake_materialize,
    )
    session = object()
    result = await workflow_module.late_day_turnaround_materialize_job(
        session,
        universe="ashare",
        now=datetime(2026, 8, 14, 14, 40, 37, 123456, tzinfo=SHANGHAI),
    )

    assert result == {"status": "complete"}
    assert captured["session"] is session
    assert captured["universe"] == "ashare"
    assert captured["decision_at"] == datetime(2026, 8, 14, 14, 40, tzinfo=SHANGHAI)


def test_scheduler_registers_only_explicitly_enabled_late_day_stages(app) -> None:
    settings = app.state.settings.model_copy(
        update={
            "late_day_turnaround_ashare_capture_enabled": True,
            "late_day_turnaround_ashare_materialize_enabled": True,
            "late_day_turnaround_etf_materialize_enabled": False,
        }
    )
    scheduler = scheduler_module.build_scheduler("Asia/Shanghai")
    scheduler_module.register_default_jobs(scheduler, app.state.db, settings)

    capture = scheduler.get_job("late_day_turnaround_capture_ashare")
    ashare = scheduler.get_job("late_day_turnaround_materialize_ashare")
    assert capture is not None
    assert ashare is not None
    assert scheduler.get_job("late_day_turnaround_materialize_etf") is None
    assert "minute='28,38,48'" in str(capture.trigger)
    assert "minute='30,40,50'" in str(ashare.trigger)
    assert capture.max_instances == 1
    assert ashare.max_instances == 1


@pytest.mark.asyncio
async def test_enabled_api_still_does_not_fetch_when_tables_are_absent(client, app) -> None:
    app.state.settings.late_day_turnaround_api_enabled = True
    response = await client.get(
        "/api/short-research/late-day-turnaround/candidates?universe=etf"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["unavailable_reason"] == NO_COMPLETED_MANIFEST
    assert payload["items"] == []


def test_late_day_modules_cannot_reach_production_action_writers() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "services"
    paths = [
        root / "strategy_lab" / "late_day_turnaround_storage.py",
        root / "strategy_lab" / "late_day_turnaround_materializer.py",
        root / "strategy_lab" / "late_day_turnaround_eastmoney.py",
        root / "workflows" / "late_day_turnaround.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    forbidden_imports = (
        "app.services.notifications",
        "app.services.tracked_positions",
        "app.services.transactions",
        "app.services.short_research.service",
        "app.services.risk_alerts",
    )
    assert all(value not in source for value in forbidden_imports)
    forbidden_tables = (
        "short_etf_signal_runs",
        "short_etf_signal_items",
        "tracked_positions",
        "tracked_position_alerts",
        "transactions",
        "notification_logs",
    )
    assert all(value not in source for value in forbidden_tables)
