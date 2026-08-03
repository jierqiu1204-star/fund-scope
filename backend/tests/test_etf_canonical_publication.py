from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import (
    CanonicalPublicationImmutableError,
    EtfCanonicalPublicationRegistry,
    ShortResearchSignalRun,
)
from app.services.short_research.canonical_publication import (
    build_provider_health_seal,
    missing_provider_health_seal,
    register_canonical_publication,
    validate_provider_health_seal,
)
from app.services.short_research.ranking_surfaces import actionable_rank_manifest

TRADE_DATE = date(2026, 8, 3)
DECISION_CUTOFF = datetime(2026, 8, 3, 15, 0)


def _candidate_evidence(*, source_time: datetime = DECISION_CUTOFF) -> list[dict]:
    return [
        {
            "asset_code": "510300",
            "field_statuses": {
                "provider": "available",
                "provider_health": "available",
                "freshness": "available",
                "provider_consensus": "available",
            },
            "source_times": {
                "provider": source_time.isoformat(),
                "provider_health": source_time.isoformat(),
                "freshness": source_time.isoformat(),
                "provider_consensus": source_time.isoformat(),
            },
        }
    ]


def _provider_seal() -> dict:
    return build_provider_health_seal(
        candidate_evidence=_candidate_evidence(),
        manifest=actionable_rank_manifest(),
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )


def _run(
    *,
    suffix: str,
    research_contract_hash: str = "a" * 64,
    surface_group_hash: str = "f" * 64,
) -> ShortResearchSignalRun:
    return ShortResearchSignalRun(
        status="success",
        started_at=DECISION_CUTOFF,
        finished_at=DECISION_CUTOFF,
        as_of_date=TRADE_DATE,
        config_json={"market_decision_cutoff": DECISION_CUTOFF.isoformat()},
        summary_json={
            "readiness_policy": {
                "policy_version": "etf_readiness_policy_v3",
                "daily_coverage_threshold": 0.95,
                "warmup_coverage_threshold": 0.9,
            },
            "cutoff_provenance": {
                "market_decision_cutoff": DECISION_CUTOFF.isoformat(),
            },
            "surface_group_hash": surface_group_hash,
            "provider_health_identity": _provider_seal(),
            "ranking_surfaces": {
                "research": {"contract_hash": research_contract_hash},
                "actionable": {
                    "contract_hash": actionable_rank_manifest().manifest_hash
                },
            },
        },
        scope_kind="full",
        scope_hash="b" * 64,
        universe_snapshot_hash="c" * 64,
        input_snapshot_hash="d" * 63 + suffix,
        score_version="daily_reconstructable_v1",
        rule_version="final_score_v3_rule_v2",
        ranking_contract_hash="e" * 64,
        score_field="research_score",
        data_cutoff=DECISION_CUTOFF,
        as_of_trade_date=TRADE_DATE,
        price_basis="total_return_adjusted",
        expected_item_count=1,
        decision_data_item_count=1,
        decision_data_coverage_ratio=1.0,
        eligible_item_count=1,
        coverage_ratio=1.0,
        publication_state="unpublished",
        idempotency_key=f"registry-fixture-{suffix}",
    )


def test_provider_health_seal_fails_closed_for_missing_stale_and_tampered_evidence() -> None:
    manifest = actionable_rank_manifest()
    compatible = _provider_seal()
    stale = build_provider_health_seal(
        candidate_evidence=_candidate_evidence(
            source_time=DECISION_CUTOFF - timedelta(days=1)
        ),
        manifest=manifest,
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )
    tampered = {**compatible, "source_range": {"start": None, "end": None}}

    assert compatible["state"] == "compatible"
    assert stale["state"] == "stale"
    assert stale["unavailable_reason"] == "provider_health_seal_stale"
    assert missing_provider_health_seal(manifest=manifest)["state"] == "missing"
    assert validate_provider_health_seal(
        tampered,
        manifest=manifest,
        trade_date=TRADE_DATE,
        decision_cutoff=DECISION_CUTOFF,
    )["state"] == "incompatible"


@pytest.mark.asyncio
async def test_registry_returns_exact_winner_and_supersedes_new_identity(app) -> None:
    manifest = actionable_rank_manifest()
    first_run = _run(suffix="1")
    exact_duplicate = _run(suffix="1")
    exact_duplicate.idempotency_key = "registry-fixture-duplicate"
    superseding_run = _run(
        suffix="2",
        research_contract_hash="9" * 64,
        surface_group_hash="8" * 64,
    )

    async with app.state.db.session() as session:
        session.add_all([first_run, exact_duplicate, superseding_run])
        await session.flush()
        first = await register_canonical_publication(
            session,
            run=first_run,
            actionable_manifest=manifest,
        )
        await session.commit()

        duplicate = await register_canonical_publication(
            session,
            run=exact_duplicate,
            actionable_manifest=manifest,
        )
        await session.commit()

        superseding = await register_canonical_publication(
            session,
            run=superseding_run,
            actionable_manifest=manifest,
        )
        await session.commit()
        rows = (
            await session.scalars(
                select(EtfCanonicalPublicationRegistry).order_by(
                    EtfCanonicalPublicationRegistry.id.asc()
                )
            )
        ).all()

        assert first.created is True
        assert duplicate.created is False
        assert duplicate.winner_run_id == first_run.id
        assert superseding.created is True
        assert superseding.registry.supersedes_publication_id == first.registry.id
        assert [(row.source_signal_run_id, row.is_current) for row in rows] == [
            (first_run.id, False),
            (superseding_run.id, True),
        ]

        rows[0].provider_health_seal_hash = "0" * 64
        with pytest.raises(CanonicalPublicationImmutableError):
            await session.commit()
        await session.rollback()
