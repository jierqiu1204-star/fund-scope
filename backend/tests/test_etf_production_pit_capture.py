from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfFactorExperimentCheckpoint,
    EtfFactorExperimentEvidence,
    EtfOptimizedAllocationSnapshot,
    EtfPitCaptureSource,
    JobRun,
    NotificationLog,
    PitCaptureSourceImmutableError,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionActionExecution,
    TrackedPositionAlert,
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
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    ResearchLoopPhase,
    new_research_loop_checkpoint,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    StageBRankedItem,
    StageBRankingEvent,
    StageBReadPage,
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
    build_production_pit_phase_handlers,
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


def _stage_b_ranking_event(manifest) -> StageBRankingEvent:
    codes = tuple(f"510{index:03d}" for index in range(1, 21))
    ranked_items = tuple(
        StageBRankedItem(
            asset_code=code,
            rank=index,
            research_score=100.0 - index,
            feature_hash=_hash(f"feature:{code}"),
        )
        for index, code in enumerate(codes, start=1)
    )
    draft = StageBRankingEvent(
        replay_run_key=manifest.replay_run_key,
        replay_date=TRADE_DATE,
        ranking_source_kind="research_replay",
        score_contract_id="daily_reconstructable_v1",
        score_field="research_score",
        score_manifest_hash=manifest.research_contract_hash,
        stage_b_schema_version="etf-ranking-stage-b-v1",
        date_manifest_hash=_hash("stage-b-date-manifest"),
        source_date_manifest_hash=_hash("stage-b-source-manifest"),
        universe_hash=_hash("stage-b-universe"),
        input_hash=_hash("stage-b-input"),
        feature_manifest_hash=_hash("stage-b-features"),
        ranked_items=ranked_items,
        all_scored=codes,
        top5=codes[:5],
        top10=codes[:10],
        top20=codes[:20],
        event_hash="pending",
    )
    payload = asdict(draft)
    payload.pop("event_hash")
    return replace(draft, event_hash=stable_contract_hash(payload))


async def _seed_complete_snapshot(
    session,
    *,
    daily_coverage: float = 1.0,
    warmup_coverage: float = 1.0,
    readiness_policy_version: str | None = None,
    readiness_state: str | None = None,
) -> ShortResearchSignalRun:
    research = daily_reconstructable_manifest()
    actionable = actionable_rank_manifest()
    readiness = evaluate_etf_readiness(
        daily_coverage_ratio=daily_coverage,
        warmup_coverage_ratio=warmup_coverage,
    )
    persisted_readiness = readiness.to_dict()
    if readiness_policy_version is not None:
        persisted_readiness["policy_version"] = readiness_policy_version
    if readiness_state is not None:
        persisted_readiness["state"] = readiness_state
    persisted_state = str(persisted_readiness["state"])
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
            "readiness_state": persisted_state,
        },
        summary_json={
            "item_count": 1,
            "readiness_state": persisted_state,
            "readiness_policy_version": persisted_readiness["policy_version"],
            "readiness_policy": persisted_readiness,
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
            warmup_coverage=0.8999,
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

        legacy = await _seed_complete_snapshot(
            session,
            daily_coverage=0.95,
            warmup_coverage=0.94,
            readiness_policy_version="etf_readiness_policy_v1",
            readiness_state="degraded",
        )
        legacy_rejected = await capture_complete_pit_source(
            session,
            source_signal_run_id=legacy.id,
            provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
            provider_health_identity=PROVIDER_HEALTH,
            replay_visibility_cutoff=RECEIPT_CUTOFF,
        )
        assert legacy_rejected.unavailable_reason == (
            PIT_UNAVAILABLE_COMPLETE_PUBLICATION
        )

        complete = await _seed_complete_snapshot(
            session,
            daily_coverage=0.95,
            warmup_coverage=0.90,
        )
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
        assert first.checkpoint["ranking_ready"] is True
        assert first.checkpoint["research_ready"] is False
        assert first.checkpoint["readiness"]["factual_pit_session_count"] == 1
        assert first.checkpoint["readiness"]["non_overlapping_primary_date_count"] == 0
        assert first.checkpoint["readiness"]["completed_walk_forward_fold_count"] == 0

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
async def test_pit_candidate_adapter_records_retryable_stage_b_pending_artifact(
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
        source = await session.get(EtfPitCaptureSource, captured.source.id)
        assert source is not None
        manifest = build_production_pit_manifest(
            source,
            code_version="pit-pending-test-v1",
            split_contract_hash=_hash("split"),
            holdout_identity_hash=_hash("holdout"),
            bootstrap_seed=20260729,
        )
        store = ReplayArtifactStore(tmp_path / "pit-pending-artifacts.sqlite3")
        handlers = build_production_pit_phase_handlers(
            session=session,
            source=source,
            artifact_store=store,
        )
        result = await handlers.candidates(
            manifest,
            replace(
                new_research_loop_checkpoint(manifest),
                phase=ResearchLoopPhase.CANDIDATES,
            ),
            5,
            5.0,
        )
        retry = await handlers.candidates(
            manifest,
            replace(
                new_research_loop_checkpoint(manifest),
                phase=ResearchLoopPhase.CANDIDATES,
            ),
            5,
            5.0,
        )

    assert result.phase_complete is False
    assert result.outcome == "healthy"
    assert result.cursor["pending_reason"] == "pit_stage_b_ranking_event_pending"
    assert retry.cursor == result.cursor
    assert retry.exclusions == {}
    rows = store.read_research_artifact_page(
        run_id=manifest.replay_run_key,
        phase="candidates",
        max_rows=20,
    )
    assert rows[0].payload["status"] == "pending"
    assert rows[0].payload["details"]["unavailable_inputs"] == [
        "stage_b_ranking_event"
    ]


@pytest.mark.asyncio
async def test_pit_research_adapters_persist_pending_evidence_without_side_effects(
    app,
    monkeypatch,
    tmp_path,
) -> None:
    """Later PIT phases write only replay artifacts when exact outcomes are absent."""

    import app.services.workflows.etf_point_in_time_capture as pit_capture
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
        source = await session.get(EtfPitCaptureSource, captured.source.id)
        assert source is not None
        manifest = build_production_pit_manifest(
            source,
            code_version="pit-adapter-test-v1",
            split_contract_hash=_hash("split"),
            holdout_identity_hash=_hash("holdout"),
            bootstrap_seed=20260729,
        )
        event = _stage_b_ranking_event(manifest)
        monkeypatch.setattr(
            pit_capture,
            "read_stage_b_ranking_event_page",
            lambda **_kwargs: StageBReadPage(
                rows=(event,),
                has_more=False,
                next_after_date=event.replay_date,
            ),
        )
        store = ReplayArtifactStore(tmp_path / "pit-phase-artifacts.sqlite3")
        handlers = build_production_pit_phase_handlers(
            session=session,
            source=source,
            artifact_store=store,
        )
        checkpoint = new_research_loop_checkpoint(manifest)
        before = {
            "ranking_runs": await session.scalar(
                select(func.count(ShortResearchSignalRun.id))
            ),
            "ranking_items": await session.scalar(
                select(func.count(ShortResearchSignalItem.id))
            ),
            "allocation": await session.scalar(
                select(func.count(EtfOptimizedAllocationSnapshot.id))
            ),
            "positions": await session.scalar(select(func.count(TrackedPosition.id))),
            "alerts": await session.scalar(
                select(func.count(TrackedPositionAlert.id))
            ),
            "notifications": await session.scalar(
                select(func.count(NotificationLog.id))
            ),
            "executions": await session.scalar(
                select(func.count(TrackedPositionActionExecution.id))
            ),
            "factor_evidence": await session.scalar(
                select(func.count(EtfFactorExperimentEvidence.id))
            ),
            "research_checkpoints": await session.scalar(
                select(func.count(EtfFactorExperimentCheckpoint.id))
            ),
        }

        inputs = await handlers.inputs(manifest, checkpoint, 5, 5.0)
        candidates = await handlers.candidates(
            manifest,
            replace(
                checkpoint,
                phase=ResearchLoopPhase.CANDIDATES,
                phase_artifact_hashes={"inputs": (inputs.artifact_hash,)},
            ),
            5,
            5.0,
        )
        assert inputs.phase_complete is True
        assert candidates.phase_complete is True
        assert candidates.artifact_hash is not None

        forward = await handlers.forward_outcomes(
            manifest,
            replace(
                checkpoint,
                phase=ResearchLoopPhase.FORWARD_OUTCOMES,
                phase_artifact_hashes={"candidates": (candidates.artifact_hash,)},
                exclusions=dict(candidates.exclusions),
            ),
            5,
            5.0,
        )
        forward_retry = await handlers.forward_outcomes(
            manifest,
            replace(
                checkpoint,
                phase=ResearchLoopPhase.FORWARD_OUTCOMES,
                phase_artifact_hashes={"candidates": (candidates.artifact_hash,)},
                exclusions=dict(candidates.exclusions),
            ),
            5,
            5.0,
        )
        assert forward_retry.artifact_hash == forward.artifact_hash
        validation = await handlers.ranking_validation(
            manifest,
            replace(
                checkpoint,
                phase=ResearchLoopPhase.RANKING_VALIDATION,
                phase_artifact_hashes={
                    "candidates": (candidates.artifact_hash,),
                    "forward_outcomes": (forward.artifact_hash,),
                },
                exclusions={**candidates.exclusions, **forward.exclusions},
            ),
            5,
            5.0,
        )
        factor = await handlers.factor_evidence(
            manifest,
            replace(
                checkpoint,
                phase=ResearchLoopPhase.FACTOR_EVIDENCE,
                phase_artifact_hashes={
                    "ranking_validation": (validation.artifact_hash,),
                },
                exclusions={
                    **candidates.exclusions,
                    **forward.exclusions,
                    **validation.exclusions,
                },
            ),
            5,
            5.0,
        )
        policy = await handlers.policy_shadow(
            manifest,
            replace(
                checkpoint,
                phase=ResearchLoopPhase.POLICY_SHADOW,
                phase_artifact_hashes={"factor_evidence": (factor.artifact_hash,)},
                exclusions={
                    **candidates.exclusions,
                    **forward.exclusions,
                    **validation.exclusions,
                    **factor.exclusions,
                },
            ),
            5,
            5.0,
        )
        final = await handlers.final_evidence(
            manifest,
            replace(
                checkpoint,
                phase=ResearchLoopPhase.FINAL_EVIDENCE,
                phase_artifact_hashes={
                    "factor_evidence": (factor.artifact_hash,),
                    "policy_shadow": (policy.artifact_hash,),
                },
                exclusions={
                    **candidates.exclusions,
                    **forward.exclusions,
                    **validation.exclusions,
                    **factor.exclusions,
                    **policy.exclusions,
                },
            ),
            5,
            5.0,
        )
        assert all(
            result.phase_complete
            and result.artifact_hash is not None
            for result in (forward, validation, factor, policy, final)
        )

        phase_rows = {
            phase: store.read_research_artifact_page(
                run_id=manifest.replay_run_key,
                phase=phase,
                max_rows=20,
            )
            for phase in (
                "inputs",
                "candidates",
                "forward_outcomes",
                "ranking_validation",
                "factor_evidence",
                "policy_shadow",
                "final_evidence",
            )
        }
        for rows in phase_rows.values():
            assert rows
            for row in rows:
                payload = row.payload
                assert payload["manifest_hash"] == manifest.manifest_hash
                assert payload["research_only"] is True
                assert payload["production_mutation_allowed"] is False
                for key in (
                    "code_hash",
                    "input_hash",
                    "cutoff_hash",
                    "predecessor_hash",
                    "candidate_hash",
                    "outcome_hash",
                    "cost_hash",
                ):
                    assert len(payload[key]) == 64

        forward_payloads = [row.payload for row in phase_rows["forward_outcomes"]]
        pending_outcomes = sum(
            payload["details"]["outcome_bundle"]["pending_outcome_count"]
            for payload in forward_payloads
            if "outcome_bundle" in payload["details"]
        )
        assert pending_outcomes == 80
        assert all(
            outcome["entry_session"] is None
            and outcome["exit_session"] is None
            for payload in forward_payloads
            for outcome in payload["details"].get("outcome_bundle", {}).get(
                "outcomes", []
            )
            if outcome["status"] == "pending"
        )
        final_payload = phase_rows["final_evidence"][0].payload
        readiness = final_payload["details"]["readiness"]
        assert readiness["ranking_ready"] is True
        assert readiness["research_ready"] is False
        assert readiness["factual_pit_session_count"] == 1
        assert readiness["non_overlapping_primary_date_count"] == 0
        assert readiness["completed_walk_forward_fold_count"] == 0
        assert readiness["pending_window_count"] == 80
        assert "future_window_pending" in readiness["promotion_blockers"]

        after = {
            "ranking_runs": await session.scalar(
                select(func.count(ShortResearchSignalRun.id))
            ),
            "ranking_items": await session.scalar(
                select(func.count(ShortResearchSignalItem.id))
            ),
            "allocation": await session.scalar(
                select(func.count(EtfOptimizedAllocationSnapshot.id))
            ),
            "positions": await session.scalar(select(func.count(TrackedPosition.id))),
            "alerts": await session.scalar(
                select(func.count(TrackedPositionAlert.id))
            ),
            "notifications": await session.scalar(
                select(func.count(NotificationLog.id))
            ),
            "executions": await session.scalar(
                select(func.count(TrackedPositionActionExecution.id))
            ),
            "factor_evidence": await session.scalar(
                select(func.count(EtfFactorExperimentEvidence.id))
            ),
            "research_checkpoints": await session.scalar(
                select(func.count(EtfFactorExperimentCheckpoint.id))
            ),
        }

    assert after == before


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
