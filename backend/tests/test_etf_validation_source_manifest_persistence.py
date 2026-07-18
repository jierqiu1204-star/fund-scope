from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.entities import (
    EtfSignalValidationRun,
    EtfSignalValidationSourceEvent,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    authorize_snapshot_publication,
)
from app.services.strategy_lab.etf_ranking_validation import (
    RankingValidationContractError,
)
from app.services.strategy_lab.etf_validation_manifest import (
    attach_validation_source_manifest,
    build_production_validation_source_cohort,
    validate_attached_validation_source_manifest,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


def _published_run(signal_date: date, *, suffix: str) -> ShortResearchSignalRun:
    return ShortResearchSignalRun(
        status="success",
        as_of_date=signal_date,
        as_of_trade_date=signal_date,
        finished_at=datetime(signal_date.year, signal_date.month, signal_date.day, 15, 35),
        scope_kind="full",
        scope_hash=_hash(f"scope:{suffix}"),
        universe_snapshot_hash=_hash(f"universe:{suffix}"),
        input_snapshot_hash=_hash(f"input:{suffix}"),
        score_version="final_score_v3",
        score_field="ranking_score",
        rule_version="final_score_v3_rule_v2",
        ranking_contract_hash=_hash("final-score-v3-contract"),
        data_cutoff=datetime(signal_date.year, signal_date.month, signal_date.day, 15, 0),
        price_basis="total_return_adjusted",
        expected_item_count=3,
        decision_data_item_count=3,
        decision_data_coverage_ratio=1.0,
        eligible_item_count=3,
        coverage_ratio=1.0,
        publication_state=None,
        published_at=None,
        idempotency_key=f"published:{suffix}",
    )


def _items(run_id: int) -> list[ShortResearchSignalItem]:
    return [
        ShortResearchSignalItem(
            run_id=run_id,
            asset_type="etf",
            asset_code=f"51000{rank}",
            rank=rank,
            global_rank=rank,
            total_score=90.0 - rank,
            ranking_score=90.0 - rank,
            score_eligible=True,
            conclusion="短线观察",
        )
        for rank in range(1, 4)
    ]


async def _seal(session, run: ShortResearchSignalRun) -> None:
    with authorize_snapshot_publication(session.sync_session, run_id=run.id):
        run.publication_state = "published"
        run.published_at = datetime(
            run.as_of_date.year,
            run.as_of_date.month,
            run.as_of_date.day,
            15,
            35,
        )
        await session.flush()


@pytest.mark.asyncio
async def test_production_manifest_persists_every_date_without_representative_run(app) -> None:
    async with app.state.db.session() as session:
        later = _published_run(date(2026, 7, 16), suffix="later")
        earlier = _published_run(date(2026, 7, 15), suffix="earlier")
        session.add_all([later, earlier])
        await session.flush()
        session.add_all([*_items(later.id), *_items(earlier.id)])
        await session.flush()
        await _seal(session, later)
        await _seal(session, earlier)

        cohort = await build_production_validation_source_cohort(
            session,
            source_runs=(later, earlier),
        )
        validation = EtfSignalValidationRun(
            status="running",
            as_of_date=date(2026, 7, 16),
            validation_mode="score_bucket_replay",
            rule_version="score_bucket_replay_v2",
        )
        session.add(validation)
        await session.flush()

        await attach_validation_source_manifest(session, validation, cohort)
        validation.status = "success"
        frozen = await validate_attached_validation_source_manifest(session, validation)
        await session.commit()

        rows = (
            await session.scalars(
                select(EtfSignalValidationSourceEvent)
                .where(EtfSignalValidationSourceEvent.validation_run_id == validation.id)
                .order_by(EtfSignalValidationSourceEvent.event_order)
            )
        ).all()

    assert frozen.cohort_hash == validation.source_manifest_hash
    assert validation.ranking_source_kind == "production_published"
    assert validation.source_signal_run_id is None
    assert validation.source_event_count == 2
    assert [row.source_date for row in rows] == [date(2026, 7, 15), date(2026, 7, 16)]
    assert [row.source_signal_run_id for row in rows] == [earlier.id, later.id]
    assert all(len(row.source_event_hash) == 64 for row in rows)
    assert all(len(row.immutable_hash) == 64 for row in rows)


@pytest.mark.asyncio
async def test_manifest_finalization_rejects_header_or_child_tampering(app) -> None:
    async with app.state.db.session() as session:
        source = _published_run(date(2026, 7, 15), suffix="tamper")
        session.add(source)
        await session.flush()
        session.add_all(_items(source.id))
        await session.flush()
        await _seal(session, source)
        validation = EtfSignalValidationRun(
            status="running",
            as_of_date=date(2026, 7, 15),
            validation_mode="score_bucket_replay",
            rule_version="score_bucket_replay_v2",
        )
        session.add(validation)
        await session.flush()
        cohort = await build_production_validation_source_cohort(
            session,
            source_runs=(source,),
        )
        await attach_validation_source_manifest(session, validation, cohort)
        await session.flush()

        validation.source_event_count = 2
        with pytest.raises(RankingValidationContractError, match="count"):
            await validate_attached_validation_source_manifest(session, validation)
        validation.source_event_count = 1

        row = await session.scalar(
            select(EtfSignalValidationSourceEvent).where(
                EtfSignalValidationSourceEvent.validation_run_id == validation.id
            )
        )
        assert row is not None
        row.source_event_hash = _hash("tampered")
        with pytest.raises(RankingValidationContractError, match="immutable"):
            await validate_attached_validation_source_manifest(session, validation)


@pytest.mark.asyncio
async def test_production_manifest_rejects_zero_sources(app) -> None:
    async with app.state.db.session() as session:
        with pytest.raises(RankingValidationContractError, match="cannot be empty"):
            await build_production_validation_source_cohort(session, source_runs=())
