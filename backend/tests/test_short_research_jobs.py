from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.defaults.short_research import ASSET_TYPE_ETF, ASSET_TYPE_FUND
from app.services.short_research import jobs as jobs_module


@pytest.mark.asyncio
async def test_daily_short_research_data_job_syncs_funds_and_etfs(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_sync_short_research_data(_session: object, **kwargs: Any) -> dict[str, Any]:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        return {"asset_count": 1, "failed": 0, "asset_type": asset_type}

    monkeypatch.setattr(jobs_module, "sync_short_research_data", fake_sync_short_research_data)

    result = await jobs_module.daily_short_research_data_job(object())  # type: ignore[arg-type]

    assert calls == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["asset_types"] == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["asset_count"] == 2
    assert result["failed"] == 0
    assert result["fund"]["asset_type"] == ASSET_TYPE_FUND
    assert result["etf"]["asset_type"] == ASSET_TYPE_ETF


@pytest.mark.asyncio
async def test_daily_short_research_signals_job_generates_fund_and_etf_runs(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run_signal_generation(_session: object, **kwargs: Any) -> SimpleNamespace:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        is_fund = asset_type == ASSET_TYPE_FUND
        return SimpleNamespace(
            id=1 if is_fund else 2,
            status="success",
            as_of_date=date(2026, 6, 12),
            summary_json={
                "item_count": 3 if is_fund else 5,
                "fund_count": 3 if is_fund else 0,
                "etf_count": 0 if is_fund else 5,
                "conclusion_counts": {"短线观察": 1},
            },
        )

    monkeypatch.setattr(jobs_module, "run_signal_generation", fake_run_signal_generation)

    result = await jobs_module.daily_short_research_signals_job(object())  # type: ignore[arg-type]

    assert calls == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["asset_types"] == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["items"] == 8
    assert result["fund"]["funds"] == 3
    assert result["etf"]["etfs"] == 5


@pytest.mark.asyncio
async def test_daily_short_research_advisor_job_generates_fund_and_etf_reports(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run_advisor_generation(_session: object, _settings: object, **kwargs: Any) -> dict[str, Any]:
        asset_type = kwargs["asset_type"]
        calls.append(asset_type)
        return {"selected": 1, "succeeded": 1, "failed": 0, "asset_type": asset_type}

    monkeypatch.setattr(jobs_module, "run_advisor_generation", fake_run_advisor_generation)

    result = await jobs_module.daily_short_research_advisor_job(object(), settings=object())  # type: ignore[arg-type]

    assert calls == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["asset_types"] == [ASSET_TYPE_FUND, ASSET_TYPE_ETF]
    assert result["selected"] == 2
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert result["fund"]["asset_type"] == ASSET_TYPE_FUND
    assert result["etf"]["asset_type"] == ASSET_TYPE_ETF
