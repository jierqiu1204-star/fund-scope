from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2_intraday_confirmation import (
    MorningWatchCandidate,
    read_low_base_morning_watch_pool,
)
from app.services.workflows import dual_universe_leader_tactics_v2_morning as workflow

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _settings(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        etf_leader_tactics_v2_morning_confirmation_enabled=enabled,
    )


@pytest.mark.asyncio
async def test_watch_pool_rejects_more_than_twenty_assets_before_database_work() -> None:
    session = AsyncMock()

    with pytest.raises(ValueError, match="watch pool limit"):
        await read_low_base_morning_watch_pool(
            session,
            decision_at=datetime(2026, 8, 18, 10, 42, tzinfo=SHANGHAI),
            limit=21,
        )

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_morning_confirmation_is_disabled_by_default_without_provider_work() -> None:
    result = await workflow.dual_universe_leader_tactics_v2_morning_confirmation_job(
        AsyncMock(),
        _settings(False),
        now=datetime(2026, 8, 18, 10, 42, tzinfo=SHANGHAI),
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "leader_tactics_v2_morning_confirmation_disabled"


@pytest.mark.asyncio
async def test_empty_watch_pool_skips_provider_work(monkeypatch) -> None:
    read_pool = AsyncMock(return_value=())
    capture = AsyncMock()
    monkeypatch.setattr(workflow, "read_low_base_morning_watch_pool", read_pool)
    monkeypatch.setattr(workflow, "capture_bounded_ashare_pool", capture)

    result = await workflow.dual_universe_leader_tactics_v2_morning_confirmation_job(
        AsyncMock(),
        _settings(True),
        now=datetime(2026, 8, 18, 10, 42, tzinfo=SHANGHAI),
    )

    assert result["provider_work_skipped"] is True
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_captures_only_declared_watch_pool_once(monkeypatch) -> None:
    candidate = MorningWatchCandidate(
        manifest_hash="m" * 64,
        asset_code="002437",
        asset_name="誉衡药业",
        signal_date=date(2026, 8, 17),
        source_cutoff=datetime(2026, 8, 17, 15),
        feature_hash="f" * 64,
        signal_adjusted_close=4.0,
        signal_adjusted_high=4.1,
        signal_adjusted_ma5=3.9,
        signal_adjusted_ma20=3.8,
        signal_adjusted_atr20=0.3,
    )
    capture = AsyncMock(
        return_value={
            "status": "complete",
            "evidence_cutoff": "2026-08-18T10:42:20+08:00",
        }
    )
    materialize = AsyncMock(return_value={"status": "complete", "confirmed_count": 1})
    monkeypatch.setattr(
        workflow,
        "read_low_base_morning_watch_pool",
        AsyncMock(return_value=(candidate,)),
    )
    monkeypatch.setattr(workflow, "capture_bounded_ashare_pool", capture)
    monkeypatch.setattr(
        workflow,
        "materialize_low_base_morning_confirmations",
        materialize,
    )
    session = AsyncMock()

    result = await workflow.dual_universe_leader_tactics_v2_morning_confirmation_job(
        session,
        _settings(True),
        now=datetime(2026, 8, 18, 10, 42, tzinfo=SHANGHAI),
    )

    assert result["confirmed_count"] == 1
    capture.assert_awaited_once()
    kwargs = capture.await_args.kwargs
    assert kwargs["declared_pool"] == [("002437", "誉衡药业")]
    assert kwargs["checkpoint_provider"] == "eastmoney_5m_leader_confirmation"
    assert kwargs["minimum_closed_bars"] == 7
    assert kwargs["use_receipt_time_cutoff"] is True
    materialize.assert_awaited_once()
