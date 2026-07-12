from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.entities import JobRun, utcnow


@pytest.mark.asyncio
async def test_short_research_status_exposes_scheduled_etf_job_freshness(client, app) -> None:
    now = utcnow()
    async with app.state.db.session() as session:
        session.add_all(
            [
                JobRun(
                    job_name="daily_etf_universe",
                    status="success",
                    started_at=now,
                    finished_at=now,
                ),
                JobRun(
                    job_name="daily_etf_theme_catalyst",
                    status="success",
                    started_at=now - timedelta(days=3),
                    finished_at=now - timedelta(days=3),
                ),
                JobRun(
                    job_name="daily_short_research_data",
                    status="partial",
                    started_at=now,
                    finished_at=now,
                ),
            ]
        )
        await session.commit()

    response = await client.get("/api/short-research/status")

    assert response.status_code == 200
    freshness = response.json()["job_freshness"]
    assert freshness["daily_etf_universe"]["status"] == "fresh"
    assert freshness["daily_etf_theme_catalyst"]["status"] == "stale"
    assert freshness["daily_short_research_data"]["status"] == "degraded"
    assert freshness["intraday_etf_cleanup"]["status"] == "waiting"
