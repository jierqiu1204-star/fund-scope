from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select

from app.defaults.funds import DEFAULT_RESEARCH_FUNDS
from app.models.entities import FundNavHistory, PaperPortfolio, StrategyDefinition
from app.services import jobs


@pytest.mark.asyncio
async def test_admin_data_status_reports_nav_strategy_and_paper_counts(client, app) -> None:
    async with app.state.db.session() as session:
        strategy = StrategyDefinition(
            name="Status strategy",
            strategy_type="momentum_rotation",
            asset_type="fund",
            status="active",
            config_json={"initial_cash": 100000},
        )
        session.add(strategy)
        await session.flush()
        session.add(
            PaperPortfolio(
                strategy_id=strategy.id,
                name="Status paper",
                status="active",
                started_at=date(2026, 1, 1),
                cash=100000,
                latest_equity=100000,
            )
        )
        session.add_all(
            [
                FundNavHistory(fund_code="007339", nav_date=date(2026, 1, 1), nav=1.0, accumulated_nav=1.0),
                FundNavHistory(fund_code="007339", nav_date=date(2026, 1, 2), nav=1.1, accumulated_nav=1.1),
            ]
        )
        await session.commit()

    response = await client.get("/api/admin/data-status")

    assert response.status_code == 200
    assert response.json() == {
        "fund_count": len(DEFAULT_RESEARCH_FUNDS),
        "nav_rows": 2,
        "earliest_nav_date": "2026-01-01",
        "latest_nav_date": "2026-01-02",
        "strategy_count": 1,
        "paper_portfolio_count": 1,
    }


@pytest.mark.asyncio
async def test_fund_nav_backfill_job_writes_range_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    client,
    app,
) -> None:
    calls: list[tuple[str, date, date]] = []

    async def fake_fetch_fund_nav(fund_code: str, from_date: date, to_date: date) -> list[dict[str, Any]]:
        calls.append((fund_code, from_date, to_date))
        return [
            {"date": from_date.isoformat(), "nav": 1.0, "accumulated_nav": 1.0},
            {"date": to_date.isoformat(), "nav": 1.2, "accumulated_nav": 1.2},
        ]

    monkeypatch.setattr(jobs, "fetch_fund_nav", fake_fetch_fund_nav)

    first = await client.post("/api/admin/jobs/fund_nav_backfill/run?days=180")
    second = await client.post("/api/admin/jobs/fund_nav_backfill/run?days=180")

    async with app.state.db.session() as session:
        rows = (
            await session.scalars(
                select(FundNavHistory).order_by(FundNavHistory.fund_code.asc(), FundNavHistory.nav_date.asc())
            )
        ).all()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["rows_inserted"] == len(DEFAULT_RESEARCH_FUNDS) * 2
    assert second.json()["rows_inserted"] == 0
    assert first.json()["funds"] == len(DEFAULT_RESEARCH_FUNDS)
    assert first.json()["failed"] == 0
    assert len(calls) == len(DEFAULT_RESEARCH_FUNDS) * 2
    assert len(rows) == len(DEFAULT_RESEARCH_FUNDS) * 2
    assert all((to_date - from_date).days == 180 for _, from_date, to_date in calls)


@pytest.mark.asyncio
async def test_fund_nav_backfill_continues_when_one_fund_fails(
    monkeypatch: pytest.MonkeyPatch,
    client,
) -> None:
    async def fake_fetch_fund_nav(fund_code: str, from_date: date, to_date: date) -> list[dict[str, Any]]:
        if fund_code == "001052":
            raise RuntimeError("source unavailable")
        return [{"date": to_date.isoformat(), "nav": 1.2, "accumulated_nav": 1.2}]

    monkeypatch.setattr(jobs, "fetch_fund_nav", fake_fetch_fund_nav)

    response = await client.post("/api/admin/jobs/fund_nav_backfill/run?days=365")

    assert response.status_code == 200
    payload = response.json()
    assert payload["funds"] == len(DEFAULT_RESEARCH_FUNDS)
    assert payload["rows_inserted"] == len(DEFAULT_RESEARCH_FUNDS) - 1
    assert payload["failed"] == 1
    assert payload["failures"] == [{"fund_code": "001052", "error": "source unavailable"}]
