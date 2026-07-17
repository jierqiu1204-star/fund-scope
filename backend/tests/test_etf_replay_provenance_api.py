from __future__ import annotations

from datetime import date

import pytest

from app.models.entities import (
    EtfSignalValidationRun,
    ShortResearchSignalRun,
    utcnow,
)


@pytest.mark.asyncio
async def test_validation_api_returns_typed_ranking_source_provenance(
    app,
    client,
) -> None:
    async with app.state.db.session() as session:
        session.add(
            EtfSignalValidationRun(
                status="success",
                started_at=utcnow(),
                finished_at=utcnow(),
                as_of_date=date(2026, 7, 15),
                validation_mode="score_bucket",
                rule_version="label_validation_v1",
                ranking_source_kind="research_replay",
                source_replay_run_key="replay-run-api-1",
                summary_json={},
            )
        )
        await session.commit()

    response = await client.get("/api/short-research/validation?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["ranking_source_kind"] == "research_replay"
    assert payload[0]["source_replay_run_key"] == "replay-run-api-1"


@pytest.mark.asyncio
async def test_validation_api_does_not_claim_production_source_for_unpublished_run(
    app,
    client,
) -> None:
    async with app.state.db.session() as session:
        source = ShortResearchSignalRun(
            status="success",
            finished_at=utcnow(),
            as_of_date=date(2026, 7, 15),
            publication_state=None,
        )
        session.add(source)
        await session.flush()
        session.add(
            EtfSignalValidationRun(
                status="success",
                started_at=utcnow(),
                finished_at=utcnow(),
                as_of_date=date(2026, 7, 15),
                source_signal_run_id=source.id,
                validation_mode="score_bucket",
                ranking_source_kind="production_published",
                rule_version="label_validation_v1",
                summary_json={},
            )
        )
        await session.commit()

    response = await client.get("/api/short-research/validation?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["source_signal_run_id"] == source.id
    assert payload[0]["ranking_source_kind"] is None
