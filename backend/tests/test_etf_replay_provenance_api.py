from __future__ import annotations

from datetime import date, datetime

import pytest

from app.models.entities import (
    EtfSignalValidationRun,
    ShortResearchSignalRun,
    utcnow,
)
from app.services.etf_research_evidence import RankingSourceKind
from app.services.strategy_lab.etf_ranking_validation import (
    RankingValidationSourceEvent,
    freeze_ranking_validation_source_cohort,
    freeze_ranking_validation_source_event,
)
from app.services.strategy_lab.etf_validation_manifest import (
    attach_validation_source_manifest,
    validate_attached_validation_source_manifest,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


@pytest.mark.asyncio
async def test_validation_api_returns_typed_ranking_source_provenance(
    app,
    client,
) -> None:
    async with app.state.db.session() as session:
        signal_date = date(2026, 7, 15)
        draft = RankingValidationSourceEvent(
            ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
            signal_date=signal_date,
            source_signal_run_id=None,
            source_replay_run_key="replay-run-api-1",
            source_replay_contract_hash=stable_contract_hash({"replay": 1}),
            source_event_hash=stable_contract_hash({"event": 1}),
            ranking_contract_hash=stable_contract_hash({"ranking": 1}),
            scope_hash=stable_contract_hash({"scope": 1}),
            universe_snapshot_hash=stable_contract_hash({"universe": 1}),
            input_snapshot_hash=stable_contract_hash({"input": 1}),
            score_version="daily_reconstructable_v1",
            score_field="research_score",
            rule_version="daily_reconstructable_v1_rule_v1",
            price_basis="total_return_adjusted",
            publication_state=None,
            scope_kind="research_replay",
            availability_cutoff=datetime(2026, 7, 15, 15, 30),
            immutable_hash="pending",
        )
        cohort = freeze_ranking_validation_source_cohort(
            ranking_source_kind=RankingSourceKind.RESEARCH_REPLAY,
            events=(freeze_ranking_validation_source_event(draft),),
        )
        run = EtfSignalValidationRun(
            status="running",
            started_at=utcnow(),
            as_of_date=signal_date,
            validation_mode="score_bucket",
            rule_version="label_validation_v1",
            summary_json={},
        )
        session.add(run)
        await session.flush()
        await attach_validation_source_manifest(session, run, cohort)
        await validate_attached_validation_source_manifest(session, run)
        run.status = "success"
        run.finished_at = utcnow()
        await session.commit()

    response = await client.get("/api/short-research/validation?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["ranking_source_kind"] == "research_replay"
    assert payload[0]["source_replay_run_key"] == "replay-run-api-1"
    assert payload[0]["source_event_count"] == 1
    assert payload[0]["source_events"][0]["source_replay_run_key"] == "replay-run-api-1"


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
