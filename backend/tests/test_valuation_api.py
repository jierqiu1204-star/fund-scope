from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import Index, IndexValuationHistory, JobRun
from app.services import jobs as jobs_module
from app.services.job_runner import run_job
from app.services.valuation import compute_percentile


@pytest.mark.asyncio
async def test_default_index_seed_data_contains_six_watchlist_indices(app) -> None:
    async with app.state.db.session() as session:
        rows = (
            await session.scalars(select(Index).where(Index.is_watchlist.is_(True)).order_by(Index.code.asc()))
        ).all()

    assert [(row.code, row.name, row.region) for row in rows] == [
        ("CHINEXT", "ChiNext", "CN"),
        ("CSI300", "CSI 300", "CN"),
        ("CSI500", "CSI 500", "CN"),
        ("CSI800", "CSI 800", "CN"),
        ("NDX100", "Nasdaq 100", "US"),
        ("SP500", "S&P 500", "US"),
    ]


def test_compute_percentile_uses_available_window_when_history_is_short() -> None:
    values = [10, 12, 14, 16]

    result = compute_percentile(values=values, current_value=14, max_window_days=3650)

    assert result.percentile == 75.0
    assert result.effective_window == 4


@pytest.mark.asyncio
async def test_watchlist_api_adds_new_index_for_future_jobs(client, app) -> None:
    response = await client.post(
        "/api/valuation/watchlist",
        json={"code": "CSI1000", "name": "CSI 1000", "region": "CN"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "code": "CSI1000",
        "name": "CSI 1000",
        "region": "CN",
        "is_watchlist": True,
        "backfill_required": True,
    }

    async with app.state.db.session() as session:
        index = await session.get(Index, "CSI1000")

    assert index is not None
    assert index.is_watchlist is True


@pytest.mark.asyncio
async def test_watchlist_api_removes_index_but_retains_history(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            IndexValuationHistory(
                index_code="CSI300",
                valuation_date=date(2026, 5, 1),
                pe=12.3,
                pb=1.4,
                dividend_yield=2.1,
                pe_percentile=15,
                pb_percentile=22,
                effective_window=3650,
            )
        )
        await session.commit()

    response = await client.delete("/api/valuation/watchlist/CSI300")

    assert response.status_code == 200
    assert response.json() == {
        "code": "CSI300",
        "is_watchlist": False,
        "historical_rows_retained": 1,
    }

    async with app.state.db.session() as session:
        index = await session.get(Index, "CSI300")
        history_count = await session.scalar(select(func.count()).select_from(IndexValuationHistory))

    assert index is not None
    assert index.is_watchlist is False
    assert history_count == 1


@pytest.mark.asyncio
async def test_current_valuation_endpoint_returns_latest_snapshot(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            IndexValuationHistory(
                index_code="CSI300",
                valuation_date=date(2026, 5, 1),
                pe=12.3,
                pb=1.4,
                dividend_yield=2.1,
                pe_percentile=15,
                pb_percentile=22,
                effective_window=3650,
            )
        )
        await session.commit()

    response = await client.get("/api/valuation/current")

    assert response.status_code == 200
    assert response.json()[0]["index_code"] == "CSI300"
    assert response.json()[0]["pe_percentile"] == 15


@pytest.mark.asyncio
async def test_daily_valuation_job_inserts_rows_for_watchlist_indices(app, monkeypatch) -> None:
    async def fake_fetch(index_code: str, valuation_date: date) -> dict[str, float | date]:
        return {"date": valuation_date, "pe": 10.0, "pb": 1.1, "dividend_yield": 2.0}

    monkeypatch.setattr(jobs_module, "fetch_index_valuation", fake_fetch)

    async with app.state.db.session() as session:
        result = await jobs_module.daily_valuation_job(session)
        row_count = await session.scalar(select(func.count()).select_from(IndexValuationHistory))

    assert result == {"indices": 6, "rows_inserted": 6, "failed": 0, "failures": []}
    assert row_count == 6


@pytest.mark.asyncio
async def test_daily_valuation_job_records_partial_failures_in_job_details(app, monkeypatch) -> None:
    async def fake_fetch(index_code: str, valuation_date: date) -> dict[str, float | date]:
        if index_code == "CSI300":
            return {"date": valuation_date, "pe": 10.0, "pb": 1.1, "dividend_yield": 2.0}
        raise RuntimeError(f"{index_code} unavailable")

    monkeypatch.setattr(jobs_module, "fetch_index_valuation", fake_fetch)

    result = await run_job(app.state.db.session, "daily_valuation", jobs_module.daily_valuation_job)

    assert result["indices"] == 6
    assert result["rows_inserted"] == 1
    assert result["failed"] == 5
    assert result["failures"][0] == {"index_code": "CHINEXT", "error": "CHINEXT unavailable"}

    async with app.state.db.session() as session:
        job_run = await session.scalar(select(JobRun).order_by(JobRun.id.desc()))
        row_count = await session.scalar(select(func.count()).select_from(IndexValuationHistory))

    assert row_count == 1
    assert job_run is not None
    assert job_run.status == "success"
    assert job_run.details_json["failed"] == 5


@pytest.mark.asyncio
async def test_valuation_history_endpoint_returns_rows_sorted_ascending(client, app) -> None:
    async with app.state.db.session() as session:
        base_date = date(2026, 5, 1)
        for offset, pe in enumerate([10.0, 11.0, 12.0]):
            session.add(
                IndexValuationHistory(
                    index_code="CSI300",
                    valuation_date=base_date + timedelta(days=offset),
                    pe=pe,
                    pb=1.2,
                    dividend_yield=2.0,
                    pe_percentile=offset * 10,
                    pb_percentile=offset * 10,
                    effective_window=3650,
                )
            )
        await session.commit()

    response = await client.get("/api/valuation/CSI300/history?from=2026-05-01&to=2026-05-03")

    assert response.status_code == 200
    assert [row["date"] for row in response.json()] == ["2026-05-01", "2026-05-02", "2026-05-03"]
