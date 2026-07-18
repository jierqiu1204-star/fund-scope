from __future__ import annotations

from datetime import date, datetime

import pytest

from app.models.entities import (
    EtfSignalValidationRun,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    authorize_snapshot_publication,
)
from app.services.strategy_lab.etf_validation_manifest_repair import (
    repair_legacy_validation_manifests,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash


def _hash(label: str) -> str:
    return stable_contract_hash({"fixture": label})


async def _published_source(session) -> ShortResearchSignalRun:
    signal_date = date(2026, 7, 15)
    source = ShortResearchSignalRun(
        status="success",
        as_of_date=signal_date,
        as_of_trade_date=signal_date,
        scope_kind="full",
        scope_hash=_hash("scope"),
        universe_snapshot_hash=_hash("universe"),
        input_snapshot_hash=_hash("input"),
        score_version="final_score_v3",
        score_field="ranking_score",
        rule_version="final_score_v3_rule_v2",
        ranking_contract_hash=_hash("contract"),
        data_cutoff=datetime(2026, 7, 15, 15, 0),
        price_basis="total_return_adjusted",
        expected_item_count=1,
        decision_data_item_count=1,
        decision_data_coverage_ratio=1.0,
        eligible_item_count=1,
        coverage_ratio=1.0,
        idempotency_key="repair-source-1",
        summary_json={"item_count": 1},
    )
    session.add(source)
    await session.flush()
    session.add(
        ShortResearchSignalItem(
            run_id=source.id,
            asset_type="etf",
            asset_code="510001",
            rank=1,
            global_rank=1,
            total_score=88.0,
            ranking_score=88.0,
            score_eligible=True,
            conclusion="短线观察",
        )
    )
    await session.flush()
    with authorize_snapshot_publication(session.sync_session, run_id=source.id):
        source.publication_state = "published"
        source.published_at = datetime(2026, 7, 15, 15, 35)
        await session.flush()
    return source


def _identity(source: ShortResearchSignalRun) -> dict[str, object]:
    return {
        "source_signal_run_id": source.id,
        "source_date": source.as_of_trade_date.isoformat(),
        "ranking_contract_hash": source.ranking_contract_hash,
        "scope_kind": source.scope_kind,
        "scope_hash": source.scope_hash,
        "universe_snapshot_hash": source.universe_snapshot_hash,
        "input_snapshot_hash": source.input_snapshot_hash,
        "score_field": source.score_field,
        "score_version": source.score_version,
        "rule_version": source.rule_version,
        "price_basis": source.price_basis,
    }


@pytest.mark.asyncio
async def test_manifest_repair_dry_run_then_applies_only_complete_factual_links(app) -> None:
    async with app.state.db.session() as session:
        source = await _published_source(session)
        repairable = EtfSignalValidationRun(
            status="success",
            as_of_date=date(2026, 7, 15),
            source_signal_run_id=source.id,
            validation_mode="score_bucket_replay",
            rule_version="score_bucket_replay_v2",
            summary_json={
                "ranking_source_kind": "production_published",
                "source_signal_run_ids": [source.id],
                "source_snapshot_identities": [_identity(source)],
            },
        )
        ambiguous = EtfSignalValidationRun(
            status="success",
            as_of_date=date(2026, 7, 15),
            source_signal_run_id=source.id,
            validation_mode="score_bucket_replay",
            rule_version="score_bucket_replay_v2",
            summary_json={"source_signal_run_ids": [source.id]},
        )
        session.add_all([repairable, ambiguous])
        await session.flush()

        dry_run = await repair_legacy_validation_manifests(
            session,
            apply=False,
            limit=10,
        )
        assert [(item.validation_run_id, item.disposition) for item in dry_run.items] == [
            (repairable.id, "ready"),
            (ambiguous.id, "legacy_untrusted"),
        ]
        assert dry_run.items[1].reason == "missing_explicit_source_kind"
        assert repairable.ranking_source_kind is None
        assert repairable.source_manifest_hash is None

        applied = await repair_legacy_validation_manifests(
            session,
            apply=True,
            limit=10,
        )
        await session.commit()

    assert applied.repaired_count == 1
    assert repairable.ranking_source_kind == "production_published"
    assert repairable.source_signal_run_id is None
    assert repairable.source_event_count == 1
    assert len(repairable.source_manifest_hash) == 64
    assert ambiguous.ranking_source_kind is None
    assert ambiguous.source_manifest_hash is None


@pytest.mark.asyncio
async def test_manifest_repair_rejects_mismatched_or_partial_identity(app) -> None:
    async with app.state.db.session() as session:
        source = await _published_source(session)
        validation = EtfSignalValidationRun(
            status="success",
            as_of_date=date(2026, 7, 15),
            source_signal_run_id=source.id,
            validation_mode="score_bucket_replay",
            rule_version="score_bucket_replay_v2",
            summary_json={
                "ranking_source_kind": "production_published",
                "source_signal_run_ids": [source.id],
                "source_snapshot_identities": [
                    {**_identity(source), "input_snapshot_hash": _hash("wrong")}
                ],
            },
        )
        session.add(validation)
        await session.flush()

        result = await repair_legacy_validation_manifests(
            session,
            apply=True,
            limit=10,
        )

    assert result.repaired_count == 0
    assert result.items[0].disposition == "legacy_untrusted"
    assert result.items[0].reason == "stored_identity_mismatch"
    assert validation.ranking_source_kind is None
    assert validation.source_manifest_hash is None
