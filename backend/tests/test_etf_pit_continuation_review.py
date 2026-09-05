from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from test_etf_production_pit_capture import (
    PROVIDER_HEALTH,
    RECEIPT_CUTOFF,
    TRADE_DATE,
    _seed_complete_snapshot,
)

from app.models.entities import EtfFactorExperimentCheckpoint
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    ResearchLoopPhase,
    new_research_loop_checkpoint,
)
from app.services.workflows import etf_point_in_time_capture as pit


async def _capture(session, trade_date):
    run = await _seed_complete_snapshot(session, trade_date=trade_date)
    result = await pit.capture_complete_pit_source(
        session,
        source_signal_run_id=run.id,
        provider_health_hash=stable_contract_hash(PROVIDER_HEALTH),
        provider_health_identity=PROVIDER_HEALTH,
        replay_visibility_cutoff=datetime.combine(trade_date, RECEIPT_CUTOFF.time()),
    )
    assert result.source is not None
    return result.source


def _manifest(source, code_version="review-v2"):
    return pit.build_production_pit_manifest(
        source,
        code_version=code_version,
        split_contract_hash=pit.PIT_SPLIT_CONTRACT_HASH,
        holdout_identity_hash=pit.PIT_HOLDOUT_IDENTITY_HASH,
        bootstrap_seed=pit.PIT_BOOTSTRAP_SEED,
    )


@pytest.mark.asyncio
async def test_disabled_capture_does_not_advance_existing_due_source(app, tmp_path):
    async with app.state.db.session() as session:
        await _capture(session, TRADE_DATE)
        await session.commit()
        result = await pit.run_scheduled_production_pit_capture(
            session,
            trade_date=TRADE_DATE,
            enabled=False,
            code_version="review-v2",
            artifact_dir=str(tmp_path / "disabled"),
        )
    assert result["status"] == "skipped"
    assert result["reason"] == pit.PIT_UNAVAILABLE_DISABLED
    assert not (tmp_path / "disabled").exists()


@pytest.mark.asyncio
async def test_manifest_versions_have_separate_artifacts_and_retry_payload_is_stable(
    app, tmp_path,
):
    async with app.state.db.session() as session:
        source = await _capture(session, TRADE_DATE)
        old = _manifest(source, "before-review")
        new = _manifest(source)
        assert old.source_snapshot_hash == new.source_snapshot_hash
        assert old.replay_run_key != new.replay_run_key
        checkpoint = replace(
            new_research_loop_checkpoint(new), phase=ResearchLoopPhase.CANDIDATES
        )
        first = pit._phase_artifact_payload(
            phase="candidates", status="pending", manifest=new,
            checkpoint=checkpoint, source=source, page_size=5,
            details={"reason": "waiting_for_predecessor"},
        )
        retry = pit._phase_artifact_payload(
            phase="candidates", status="pending", manifest=new,
            checkpoint=replace(checkpoint, phase_cursor={"pending_reason": "missing"}),
            source=source, page_size=20,
            details={"reason": "waiting_for_predecessor"},
        )
        assert first == retry
        store = ReplayArtifactStore(tmp_path / "retry.sqlite3")
        one = store.write_research_artifacts(
            run_id=new.replay_run_key, phase="candidates", artifacts=(("pending", first),)
        )
        two = store.write_research_artifacts(
            run_id=new.replay_run_key, phase="candidates", artifacts=(("pending", retry),)
        )
        assert one == two


@pytest.mark.asyncio
async def test_scheduler_seals_later_selections_before_retrying_old_pending_outcomes(app):
    now = datetime(2026, 8, 3, 8)
    async with app.state.db.session() as session:
        first = await _capture(session, TRADE_DATE)
        second = await _capture(session, TRADE_DATE + timedelta(days=1))
        first_manifest = _manifest(first)
        state = replace(
            new_research_loop_checkpoint(first_manifest),
            phase=ResearchLoopPhase.FORWARD_OUTCOMES,
            phase_cursor={"pending_outcome_count": 80},
        )
        session.add(EtfFactorExperimentCheckpoint(
            manifest_hash=first_manifest.manifest_hash,
            code_version=first_manifest.code_version,
            status="partial",
            updated_at=now - timedelta(hours=1),
            cached_factor_rows_json={
                "__etf_point_in_time_research_loop_v1__": state.canonical_payload()
            },
        ))
        await session.commit()
        due = await pit._oldest_due_pit_source(session, code_version="review-v2", now=now)
        assert due.id == second.id


@pytest.mark.asyncio
async def test_candidate_chain_rejects_missing_exchange_session(app, tmp_path):
    async with app.state.db.session() as session:
        await _capture(session, TRADE_DATE)
        third = await _capture(session, TRADE_DATE + timedelta(days=2))
        await session.commit()
        with pytest.raises(ValueError, match="predecessor_exchange_session_missing"):
            await pit._previous_candidate_states(
                session=session, source=third, manifest=_manifest(third),
                artifact_store=ReplayArtifactStore(tmp_path / "gap.sqlite3"),
                timeout_seconds=5,
            )


@pytest.mark.asyncio
async def test_candidate_gap_cannot_starve_older_unattempted_maturity_work(app):
    now = datetime(2026, 8, 3, 8)
    async with app.state.db.session() as session:
        first = await _capture(session, TRADE_DATE)
        second = await _capture(session, TRADE_DATE + timedelta(days=1))
        for source, phase, minutes_ago in (
            (first, ResearchLoopPhase.CANDIDATES, 3),
            (second, ResearchLoopPhase.FORWARD_OUTCOMES, 60),
        ):
            manifest = _manifest(source)
            state = replace(new_research_loop_checkpoint(manifest), phase=phase)
            session.add(EtfFactorExperimentCheckpoint(
                manifest_hash=manifest.manifest_hash,
                code_version=manifest.code_version,
                status="partial",
                updated_at=now - timedelta(minutes=minutes_ago),
                cached_factor_rows_json={
                    "__etf_point_in_time_research_loop_v1__": state.canonical_payload()
                },
            ))
        await session.commit()
        due = await pit._oldest_due_pit_source(session, code_version="review-v2", now=now)
        assert due.id == second.id


@pytest.mark.asyncio
async def test_final_evidence_projects_sealed_statistics_instead_of_capture_counts(app, tmp_path):
    async with app.state.db.session() as session:
        source = await _capture(session, TRADE_DATE)
        manifest = _manifest(source)
        checkpoint = replace(
            new_research_loop_checkpoint(manifest), phase=ResearchLoopPhase.FINAL_EVIDENCE
        )
        store = ReplayArtifactStore(tmp_path / "final.sqlite3")
        for phase, status, details in (
            ("ranking_validation", "completed", {
                "candidate_results": [{"candidate_id": "daily_core_top10_hysteresis"}],
                "validation_source_cohort": {"events": [{}, {}, {}]},
            }),
            ("factor_evidence", "completed", {
                "promotion": {"state": "insufficient_data", "failed_gates": ["folds"]},
                "evidence": {
                    "report": {"coverage": {"independent_primary_date_count": 2}},
                    "split_reports": {"validation": {"walk_forward_fold_count": 1}},
                },
            }),
            ("policy_shadow", "pending", {"reason": pit.PIT_PENDING_POLICY_INPUTS}),
        ):
            store.write_research_artifacts(
                run_id=manifest.replay_run_key, phase=phase,
                artifacts=((phase, pit._phase_artifact_payload(
                    phase=phase, status=status, details=details,
                    manifest=manifest, checkpoint=checkpoint, source=source, page_size=5,
                )),),
            )
        result = await pit.build_production_pit_phase_handlers(
            session=session, source=source, artifact_store=store,
        ).final_evidence(manifest, checkpoint, 5, 5)
        assert result.phase_complete
        row = store.read_research_artifact_page(
            run_id=manifest.replay_run_key, phase="final_evidence", max_rows=5,
        )[0]
        readiness = row.payload["details"]["readiness"]
        assert readiness["ranking_diagnostics_available"] is True
        assert readiness["factual_pit_session_count"] == 3
        assert readiness["non_overlapping_primary_date_count"] == 2
        assert readiness["completed_walk_forward_fold_count"] == 1
        assert readiness["promotion_blockers"] == ["folds", pit.PIT_PENDING_POLICY_INPUTS]
        assert readiness["research_ready"] is False
