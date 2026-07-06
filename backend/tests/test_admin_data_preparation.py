from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy import select

from app.api.routes import admin as admin_routes
from app.defaults.funds import DEFAULT_RESEARCH_FUNDS
from app.models.entities import (
    EtfIntradayLatestQuote,
    EtfIntradayQuote,
    EtfPriceHistory,
    FundNavHistory,
    PaperPortfolio,
    StrategyDefinition,
    TradableEtf,
)
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
async def test_admin_etf_quote_diagnostics_reports_display_source(client, app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="513520",
                    name="日经ETF",
                    exchange="SH",
                    theme_tags_json=["跨境"],
                    trading_rule_label="T+1",
                    asset_class="ETF",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
                TradableEtf(
                    code="510300",
                    name="沪深300ETF",
                    exchange="SH",
                    theme_tags_json=["宽基"],
                    trading_rule_label="T+1",
                    asset_class="ETF",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
            ]
        )
        session.add(
            EtfIntradayQuote(
                etf_code="513520",
                quote_time=datetime(2026, 6, 25, 14, 59, 0),
                trade_date=date(2026, 6, 25),
                latest_price=2.50,
                source="history",
                freshness_status="fresh",
                raw_json={},
            )
        )
        session.add(
            EtfIntradayLatestQuote(
                etf_code="513520",
                quote_time=datetime(2026, 6, 25, 14, 58, 0),
                trade_date=date(2026, 6, 25),
                latest_price=2.58,
                source="snapshot",
                freshness_status="fresh",
                raw_json={},
            )
        )
        session.add(
            EtfPriceHistory(
                etf_code="510300",
                trade_date=date(2026, 6, 25),
                open=4.0,
                high=4.1,
                low=3.9,
                close=4.05,
                volume=1000,
                turnover=4050,
                pct_change=1.2,
            )
        )
        await session.commit()

    response = await client.get("/api/admin/etf-quote-diagnostics?codes=513520&codes=510300")

    assert response.status_code == 200
    payload = response.json()
    by_code = {item["code"]: item for item in payload["items"]}
    assert by_code["513520"]["display_price"] == 2.58
    assert by_code["513520"]["display_price_source"] == "latest_snapshot"
    assert by_code["513520"]["latest_history"]["price"] == 2.5
    assert by_code["510300"]["display_price"] == 4.05
    assert by_code["510300"]["display_price_source"] == "daily_reference"
    assert by_code["510300"]["email_eligible"] is False


@pytest.mark.asyncio
async def test_admin_etf_label_replay_uses_background_job(monkeypatch: pytest.MonkeyPatch, client) -> None:
    async def fake_run_job(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "success", "ran_sync": True}

    async def fake_start_background_job(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "id": 99,
            "job_name": "etf_label_historical_replay",
            "status": "running",
            "started_at": "2026-07-05T15:30:00",
            "finished_at": None,
            "error_message": None,
            "details": {"queued": True, "batch_size": 25, "universe_scope": "all_eligible"},
        }

    monkeypatch.setattr(admin_routes, "run_job", fake_run_job)
    monkeypatch.setattr(admin_routes, "start_background_job", fake_start_background_job, raising=False)

    response = await client.post("/api/admin/jobs/etf_label_historical_replay/run?days=180")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["details"]["queued"] is True
    assert payload["details"]["batch_size"] == 25


@pytest.mark.asyncio
async def test_admin_etf_score_bucket_validation_uses_background_job(
    monkeypatch: pytest.MonkeyPatch,
    client,
) -> None:
    async def fake_run_job(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("score bucket validation must use background job")

    async def fake_start_background_job(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        details = kwargs["details"]
        return {
            "id": 100,
            "job_name": "etf_score_bucket_validation",
            "status": "running",
            "started_at": "2026-07-05T15:30:00",
            "finished_at": None,
            "error_message": None,
            "details": details,
        }

    monkeypatch.setattr(admin_routes, "run_job", fake_run_job)
    monkeypatch.setattr(admin_routes, "start_background_job", fake_start_background_job, raising=False)

    response = await client.post("/api/admin/jobs/etf_score_bucket_validation/run?days=180")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["details"]["queued"] is True
    assert payload["details"]["validation_mode"] == "score_bucket_replay"
    assert payload["details"]["score_basis"] == "opportunity"
    assert payload["details"]["top_n"] == [5, 10, 20, 50]
    assert payload["details"]["baseline"] == "all_scored"


@pytest.mark.asyncio
async def test_admin_etf_exit_v2_validation_uses_background_job(
    monkeypatch: pytest.MonkeyPatch,
    client,
) -> None:
    async def fake_run_job(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("exit V2 validation must use background job")

    async def fake_start_background_job(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        details = kwargs["details"]
        return {
            "id": 101,
            "job_name": "etf_exit_v2_validation",
            "status": "running",
            "started_at": "2026-07-05T15:30:00",
            "finished_at": None,
            "error_message": None,
            "details": details,
        }

    monkeypatch.setattr(admin_routes, "run_job", fake_run_job)
    monkeypatch.setattr(admin_routes, "start_background_job", fake_start_background_job, raising=False)

    response = await client.post("/api/admin/jobs/etf_exit_v2_validation/run?days=180")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["details"]["queued"] is True
    assert payload["details"]["top_buckets"] == [5, 10, 20, 50]
    assert payload["details"]["research_only"] is True


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
