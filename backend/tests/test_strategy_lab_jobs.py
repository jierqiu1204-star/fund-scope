from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models.entities import PaperPortfolio, StrategyDefinition, StrategyRun
from app.services.strategy_lab import jobs


@pytest.mark.asyncio
async def test_daily_strategy_paper_job_records_failed_run_and_continues(monkeypatch: pytest.MonkeyPatch, app) -> None:
    async def fail_update(*_args: object, **_kwargs: object) -> StrategyRun:
        raise RuntimeError("missing history")

    monkeypatch.setattr(jobs, "run_paper_update", fail_update)

    async with app.state.db.session() as session:
        strategy = StrategyDefinition(
            name="Broken paper",
            strategy_type="momentum_rotation",
            asset_type="fund",
            status="active",
            config_json={"initial_cash": 100000, "lookback_days": 30, "top_n": 1, "max_pe_percentile": 80, "fee_rate": 0.001},
        )
        session.add(strategy)
        await session.flush()
        session.add(
            PaperPortfolio(
                strategy_id=strategy.id,
                name="Broken paper",
                status="active",
                started_at=date(2026, 1, 1),
                cash=100000,
                latest_equity=100000,
            )
        )
        await session.commit()

        result = await jobs.daily_strategy_paper_job(session)
        failed_run = await session.scalar(select(StrategyRun).where(StrategyRun.strategy_id == strategy.id))

    assert result["paper_portfolios"] == 1
    assert result["updated"] == 0
    assert result["failed"] == 1
    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.run_type == "simulation_update"
    assert failed_run.error_message == "missing history"
