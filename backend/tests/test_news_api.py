from __future__ import annotations

from datetime import datetime

import pytest
import respx
from httpx import Response
from sqlalchemy import func, select

from app.models.entities import JobRun, NewsItem, NewsSummary
from app.services import jobs as jobs_module
from app.services.job_runner import run_job
from app.services.jobs import daily_news_fetch_job
from app.services.news import fetch_news_for_fund


@pytest.mark.asyncio
async def test_news_feed_falls_back_to_title_when_summary_missing(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            NewsItem(
                fund_code="007339",
                published_at=datetime(2026, 5, 1, 10, 0, 0),
                title="Manager changed",
                url="https://example.com/news/1",
                raw_content="Long raw content",
            )
        )
        await session.commit()

    response = await client.get("/api/news?days=14")

    assert response.status_code == 200
    item = response.json()["groups"][0]["items"][0]
    assert item["summary"] is None
    assert item["summary_status"] == "failed"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_news_for_fund_deduplicates_and_normalizes_urls() -> None:
    html = """
    <html>
      <body>
        <a href="/news/2026-05-01/1.html">First item</a>
        <a href="https://fund.eastmoney.com/news/2026-05-01/1.html">Duplicate item</a>
      </body>
    </html>
    """
    respx.get("https://fund.eastmoney.com/007339.html").mock(
        return_value=Response(200, text=html)
    )

    items = await fetch_news_for_fund("007339")

    assert len(items) == 1
    assert items[0]["title"] == "First item"
    assert items[0]["url"] == "https://fund.eastmoney.com/news/2026-05-01/1.html"


@pytest.mark.asyncio
async def test_news_summary_backfill_retries_unsummarized_items(client, app, monkeypatch) -> None:
    async def fake_summary(self, title: str, raw_content: str) -> str:
        return "dividend|Fund announced a dividend."

    monkeypatch.setattr("app.services.llm.LLMClient.summarize_news", fake_summary)

    async with app.state.db.session() as session:
        session.add(
            NewsItem(
                fund_code="007339",
                published_at=datetime(2026, 5, 1, 10, 0, 0),
                title="Dividend issued",
                url="https://example.com/news/2",
                raw_content="Fund announced a dividend.",
            )
        )
        await session.commit()

    response = await client.post("/api/admin/jobs/news_summary_backfill/run")

    assert response.status_code == 200
    assert response.json()["succeeded"] == 1
    assert response.json()["failed"] == 0

    listing = await client.get("/api/news?event_type=dividend&days=14")
    assert listing.status_code == 200
    item = listing.json()["groups"][0]["items"][0]
    assert item["event_type"] == "dividend"
    assert item["summary"] is not None


@pytest.mark.asyncio
async def test_daily_news_fetch_records_llm_failure_in_job_details(monkeypatch, app) -> None:
    async def fake_fetch(fund_code: str) -> list[dict[str, str | datetime]]:
        if fund_code != "007339":
            return []
        return [
            {
                "title": "Manager changed",
                "url": "https://example.com/news/llm-failure",
                "published_at": datetime(2026, 5, 1, 10, 0, 0),
                "raw_content": "The fund manager changed.",
            }
        ]

    class FailingLLM:
        model_name = "test-model"

        async def summarize_news(self, title: str, raw_content: str) -> str:
            raise RuntimeError("llm unavailable")

    monkeypatch.setattr(jobs_module, "fetch_news_for_fund", fake_fetch)

    result = await run_job(
        app.state.db.session,
        "daily_news_fetch",
        lambda session: daily_news_fetch_job(session, FailingLLM()),
    )

    assert result["inserted"] == 1
    assert result["summarized"] == 0
    assert result["summary_failed"] == 1
    assert result["empty_funds"] == 3
    assert "llm unavailable" in result["summary_errors"][0]["error"]

    async with app.state.db.session() as session:
        news_count = await session.scalar(select(func.count()).select_from(NewsItem))
        summary_count = await session.scalar(select(func.count()).select_from(NewsSummary))
        job_run = await session.scalar(select(JobRun).order_by(JobRun.id.desc()))

    assert news_count == 1
    assert summary_count == 0
    assert job_run is not None
    assert job_run.details_json["summary_failed"] == 1


@pytest.mark.asyncio
async def test_daily_news_fetch_skips_duplicate_urls(monkeypatch, app) -> None:
    async def fake_fetch(_: str) -> list[dict[str, str | datetime]]:
        return [
            {
                "title": "Duplicate",
                "url": "https://example.com/news/duplicate",
                "published_at": datetime(2026, 5, 1, 10, 0, 0),
                "raw_content": "Duplicate",
            }
        ]

    class UnexpectedLLM:
        model_name = "test-model"

        async def summarize_news(self, title: str, raw_content: str) -> str:
            raise AssertionError("duplicate news should not be summarized")

    async with app.state.db.session() as session:
        session.add(
            NewsItem(
                fund_code="007339",
                published_at=datetime(2026, 5, 1, 9, 0, 0),
                title="Already stored",
                url="https://example.com/news/duplicate",
                raw_content="Already stored",
            )
        )
        await session.commit()

    monkeypatch.setattr(jobs_module, "fetch_news_for_fund", fake_fetch)

    async with app.state.db.session() as session:
        result = await daily_news_fetch_job(session, UnexpectedLLM())

    assert result["inserted"] == 0
    assert result["duplicate_skipped"] == 4
