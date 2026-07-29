from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfFactorExperimentCheckpoint,
    EtfPitCaptureSource,
    JobRun,
    PitCaptureSourceImmutableError,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    authorize_snapshot_publication,
    utcnow,
)
from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.coverage_policy import evaluate_etf_readiness
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_surfaces import (
    DUAL_RANKING_RULE_VERSION,
    actionable_rank_manifest,
)
from app.services.workflows.etf_point_in_time_capture import (
    PIT_UNAVAILABLE_CADENCE,
    PIT_UNAVAILABLE_COMPLETE_PUBLICATION,
    PIT_UNAVAILABLE_CUTOFF_INCOMPATIBLE,
    PIT_UNAVAILABLE_CUTOFF_MISSING,
    PIT_UNAVAILABLE_DISABLED,
    PIT_UNAVAILABLE_MANIFEST_LEASE,
    PIT_UNAVAILABLE_PROVIDER_HEALTH_CONTEXT,
    advance_production_pit_once,
    build_production_pit_manifest,
    capture_complete_pit_source,
    preflight_production_pit_capture,
    run_scheduled_production_pit_capture,
)

TRADE_DATE = date(2026, 7, 29)
MARKET_CUTOFF = datetime(2026, 7, 29, 15, 0)
RECEIPT_CUTOFF = datetime(2026, 7, 29, 15, 8)
PROVIDER_HEALTH = {
    "job_run_id": 1,
    "provider_health": {"providers": {"eastmoney": {"circuit_state": "closed"}}},
}


def _hash(label: str) -> str:
    return stable_contract_hash({"label": label})


async def _seed_complete_snapshot(
    session,
    *,
    daily_coverage: float = 1.0,
    warmup_coverage: float = 1.0,
) -> ShortResearchSignalRun:
    research = daily_reconstructable_manifest()
    actionable = actionable_rank_manifest()
    readiness = evaluate_etf_readiness(
        daily_coverage_ratio=daily_coverage,
        warmup_coverage_ratio=warmup_coverage,
    )
    run = ShortResearchSignalRun(
        status="success",
        started_at=utcnow(),
        finished_at=utcnow(),
        as_of_date=TRADE_DATE,
        scope_kind="full",
        scope_hash=_hash("scope"),
        universe_snapshot_hash=_hash("universe"),
        input_snapshot_hash=_hash("inputs"),
        score_version=research.contract_id,
        rule_version=DUAL_RANKING_RULE_VERSION,
        ranking_contract_hash=_hash("ranking"),
        score_field=research.score_field,
        data_cutoff=RECEIPT_CUTOFF,
        as_of_trade_date=TRADE_DATE,
        price_basis=research.price_basis,
        expected_item_count=1,
        decision_data_item_count=1,
        decision_data_coverage_ratio=daily_coverage,
        eligible_item_count=1,
        coverage_ratio=warmup_coverage,
        publication_state="unpublished",
        idempotency_key=f"pit-source-{daily_coverage}-{warmup_coverage}",
        config_json={
            "asset_type": "etf",
            "market_decision_cutoff": MARKET_CUTOFF.isoformat(),
            "source_availability_cutoff": RECEIPT_CUTOFF.isoformat(),
            "replay_visibility_cutoff": None,
            "readiness_state": readiness.state,
        },
        summary_json={
            "item_count": 1,
            "readiness_state": readiness.state,
            "readiness_policy_version": readiness.policy_version,
            "readiness_policy": readiness.to_dict(),
            "cutoff_provenance": {
                "market_decision_cutoff": MARKET_CUTOFF.isoformat(),
                "data_receipt_cutoff": RECEIPT_CUTOFF.isoformat(),
                "replay_visibility_cutoff": None,
            },
            "ranking_surfaces": {
                "research": {
                    "contract_id": research.contract_id,
                    "score_field": research.score_field,
                    "contract_hash": research.manifest_hash,
                    "eligible_count": 1,
                },
                "actionable": {
                    "contract_id": actionable.contract_id,
                    "score_field": actionable.score_field,
                    "contract_hash": actionable.manifest_hash,
                    "eligible_count": 1,
                },
            },
        },
    )
    session.add(run)
    await session.flush()
    session.add(
        ShortResearchSignalItem(
            run_id=run.id,
            asset_type="etf",
            asset_code="510001",
            rank=1,
            global_rank=1,
            total_score=80.0,
            ranking_score=80.0,
            score_eligible=True,
            conclusion="观察",
            score_breakdown_json={},
            risk_flags_json=[],
            rationale_json={},
            metrics_json={},
        )
    )
    await session.flush()
    with authorize_snapshot_publication(session.sync_session, run_id=run.id):
        run.publication_state = "published"
        run.published_at = utcnow()
        await session.flush()
    await session.commit()
    await session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_pit_capture_requires_complete_dual_readiness_and_all_cutoffs(app) -> None:
    async with app.state.db.session() as session:
        degraded = await _seed_complete_snapshot(
            session,
            daily_coverage=1.0,
            warmup_coverage=0.94,
        )
        rejected = await capture_complete_pit_source(
            session,
            source_signal_run_id=degraded.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=RECEIPT_CUTOFF,
        )
        missing = await capture_complete_pit_source(
            session,
            source_signal_run_id=degraded.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=None,
        )

        assert rejected.state == "unavailable"
        assert rejected.unavailable_reason == PIT_UNAVAILABLE_COMPLETE_PUBLICATION
        assert missing.state == "unavailable"
        # Readiness is checked before cutoff provenance and remains the stable reason.
        assert missing.unavailable_reason == PIT_UNAVAILABLE_COMPLETE_PUBLICATION
        assert await session.scalar(select(func.count(EtfPitCaptureSource.id))) == 0

        complete = await _seed_complete_snapshot(session)
        missing = await capture_complete_pit_source(
            session,
            source_signal_run_id=complete.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=None,
        )
        incompatible = await capture_complete_pit_source(
            session,
            source_signal_run_id=complete.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=RECEIPT_CUTOFF + timedelta(minutes=1),
        )

    assert missing.unavailable_reason == PIT_UNAVAILABLE_CUTOFF_MISSING
    assert incompatible.unavailable_reason == PIT_UNAVAILABLE_CUTOFF_INCOMPATIBLE


@pytest.mark.asyncio
async def test_pit_source_capture_is_idempotent_append_only_and_keeps_cutoffs_distinct(
    app,
) -> None:
    async with app.state.db.session() as session:
        run = await _seed_complete_snapshot(session)
        first = await capture_complete_pit_source(
            session,
            source_signal_run_id=run.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=RECEIPT_CUTOFF,
        )
        await session.commit()
        second = await capture_complete_pit_source(
            session,
            source_signal_run_id=run.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=RECEIPT_CUTOFF,
        )

        assert first.created is True
        assert second.created is False
        assert first.source is not None
        assert second.source is not None
        assert first.source.id == second.source.id
        assert first.source.market_decision_cutoff == MARKET_CUTOFF
        assert first.source.data_receipt_cutoff == RECEIPT_CUTOFF
        assert first.source.replay_visibility_cutoff == RECEIPT_CUTOFF
        prospective = first.source.source_context_json["prospective_evidence"]
        assert prospective["source_trade_dates"] == [TRADE_DATE.isoformat()]
        assert prospective["eligible_primary_dates"] == []
        assert prospective["independent_primary_date_count"] == 0
        assert prospective["completed_walk_forward_fold_count"] == 0
        assert await session.scalar(select(func.count(EtfPitCaptureSource.id))) == 1

        source_id = first.source.id
        first.source.provider_health_hash = _hash("rewritten-provider")
        with pytest.raises(PitCaptureSourceImmutableError):
            await session.commit()
        await session.rollback()

        persisted = await session.get(EtfPitCaptureSource, source_id)
        assert persisted is not None
        await session.delete(persisted)
        with pytest.raises(PitCaptureSourceImmutableError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_pit_continuation_advances_one_page_then_honors_database_lease(
    app,
    tmp_path,
) -> None:
    from app.services.strategy_lab.etf_action_replay.artifact_store import (
        ReplayArtifactStore,
    )

    async with app.state.db.session() as session:
        run = await _seed_complete_snapshot(session)
        captured = await capture_complete_pit_source(
            session,
            source_signal_run_id=run.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=RECEIPT_CUTOFF,
        )
        await session.commit()
        assert captured.source is not None
        source_id = captured.source.id
        manifest = build_production_pit_manifest(
            captured.source,
            code_version="test-artifact-v1",
            split_contract_hash=_hash("split"),
            holdout_identity_hash=_hash("holdout"),
            bootstrap_seed=20260729,
        )

        first = await advance_production_pit_once(
            session,
            source_id=source_id,
            artifact_store=ReplayArtifactStore(tmp_path / "pit.sqlite3"),
            code_version="test-artifact-v1",
            split_contract_hash=_hash("split"),
            holdout_identity_hash=_hash("holdout"),
            bootstrap_seed=20260729,
            timeout_seconds=5.0,
        )
        checkpoint = await session.scalar(
            select(EtfFactorExperimentCheckpoint).where(
                EtfFactorExperimentCheckpoint.manifest_hash == manifest.manifest_hash
            )
        )
        assert checkpoint is not None
        assert first.state == "advanced"
        assert first.checkpoint is not None
        assert first.checkpoint["generation"] == 1
        assert first.checkpoint["phase"] == "stage_a"

        checkpoint.status = "running"
        checkpoint.lease_token = "active-test-lease"
        checkpoint.lease_expires_at = utcnow() + timedelta(minutes=5)
        await session.commit()
        second = await advance_production_pit_once(
            session,
            source_id=source_id,
            artifact_store=ReplayArtifactStore(tmp_path / "pit.sqlite3"),
            code_version="test-artifact-v1",
            split_contract_hash=_hash("split"),
            holdout_identity_hash=_hash("holdout"),
            bootstrap_seed=20260729,
            timeout_seconds=5.0,
        )

    assert second.state == "unavailable"
    assert second.unavailable_reason == PIT_UNAVAILABLE_MANIFEST_LEASE


@pytest.mark.asyncio
async def test_pit_preflight_is_due_aware_and_requires_factual_provider_health(
    app,
    tmp_path,
) -> None:
    async with app.state.db.session() as session:
        run = await _seed_complete_snapshot(session)
        disabled = await preflight_production_pit_capture(
            session,
            trade_date=TRADE_DATE,
            enabled=False,
            code_version="test-artifact-v1",
        )
        missing_health = await preflight_production_pit_capture(
            session,
            trade_date=TRADE_DATE,
            enabled=True,
            code_version="test-artifact-v1",
        )
        assert disabled.due is False
        assert disabled.reason == PIT_UNAVAILABLE_DISABLED
        assert missing_health.due is False
        assert missing_health.reason == PIT_UNAVAILABLE_PROVIDER_HEALTH_CONTEXT

        session.add(
            JobRun(
                job_name=(
                    "etf_history_continuation:"
                    f"publication_readiness:{TRADE_DATE.isoformat()}"
                ),
                status="success",
                started_at=RECEIPT_CUTOFF,
                finished_at=RECEIPT_CUTOFF,
                details_json={
                    "provider_health": {
                        "policy_version": "publication_provider_policy_v1",
                        "providers": {
                            "eastmoney": {
                                "circuit_state": "closed",
                                "last_error": None,
                            }
                        },
                    }
                },
            )
        )
        await session.commit()
        due = await preflight_production_pit_capture(
            session,
            trade_date=TRADE_DATE,
            enabled=True,
            code_version="test-artifact-v1",
        )
        assert due.due is True
        assert due.source_signal_run_id == run.id
        assert due.provider_health_hash is not None
        assert due.replay_visibility_cutoff == RECEIPT_CUTOFF

        result = await run_scheduled_production_pit_capture(
            session,
            trade_date=TRADE_DATE,
            enabled=True,
            code_version="test-artifact-v1",
            artifact_dir=str(tmp_path / "pit-artifacts"),
            timeout_seconds=5.0,
        )
        not_due = await preflight_production_pit_capture(
            session,
            trade_date=TRADE_DATE,
            enabled=True,
            code_version="test-artifact-v1",
        )

    assert result["status"] == "success"
    assert result["checkpoint"]["generation"] == 1
    assert result["checkpoint"]["phase"] == "stage_a"
    assert not_due.due is False
    assert not_due.reason == PIT_UNAVAILABLE_CADENCE
