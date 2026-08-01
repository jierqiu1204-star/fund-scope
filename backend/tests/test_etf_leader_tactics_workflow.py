from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select

from app.core.config import Settings
from app.models.entities import (
    EtfFactorExperimentCheckpoint,
    EtfFactorExperimentEvidence,
    EtfPitCaptureSource,
    ShortResearchSignalRun,
)
from app.services import scheduler as scheduler_module
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_leader_tactics_continuation import (
    LeaderContinuationHandlers,
    LeaderContinuationManifest,
    LeaderContinuationPage,
    LeaderContinuationPhase,
    LeaderPageArtifact,
)
from app.services.strategy_lab.etf_leader_tactics_observation import (
    LEADER_OBSERVATION_EXPERIMENT_FAMILY,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_HYPOTHESIS_REGISTRY,
)
from app.services.workflows.etf_leader_tactics_shadow import (
    LEADER_CONTINUATION_DISABLED,
    LEADER_CONTINUATION_JOB_NAME,
    LEADER_PIT_SOURCE_UNAVAILABLE,
    continue_etf_leader_tactics_shadow_job,
)


def _hash(label: str) -> str:
    return stable_contract_hash({"label": label})


def _manifest(code_version: str) -> LeaderContinuationManifest:
    return LeaderContinuationManifest(
        run_key="leader-workflow-test",
        code_version=code_version,
        leader_manifest_hash=_hash("leader"),
        factor_manifest_hash=_hash("factor"),
        source_snapshot_hash=_hash("source"),
        universe_manifest_hash=_hash("universe"),
        input_snapshot_hash=_hash("input"),
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        candidate_registry_hash=FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        data_cutoff=datetime(2026, 7, 31, 15),
    )


async def _one_page(_manifest, checkpoint, _page_size, _timeout_seconds):
    return LeaderContinuationPage(
        phase_complete=True,
        next_cursor={},
        artifacts=(
            LeaderPageArtifact(
                item_key=f"{checkpoint.phase.value}:one",
                payload={"state": "fixture_research_only"},
            ),
        ),
        coverage={
            checkpoint.phase.value: {
                "numerator": 1,
                "denominator": 1,
                "rate": 1.0,
            }
        },
    )


def _handlers() -> LeaderContinuationHandlers:
    return LeaderContinuationHandlers(
        features=_one_page,
        outcomes=_one_page,
        diagnostics=_one_page,
        ma5_policy=_one_page,
        final_evidence=_one_page,
    )


async def test_disabled_workflow_is_read_only_and_never_calls_provider(app) -> None:
    settings = Settings(_env_file=None)
    async with app.state.db.session() as session:
        result = await continue_etf_leader_tactics_shadow_job(
            session,
            settings=settings,
        )
        checkpoint_count = await session.scalar(
            select(func.count()).select_from(EtfFactorExperimentCheckpoint)
        )

    assert result == {
        "job_name": LEADER_CONTINUATION_JOB_NAME,
        "status": "disabled",
        "unavailable_reason": LEADER_CONTINUATION_DISABLED,
        "live_provider_calls": 0,
        "advanced_pages": 0,
        "single_worker": True,
        "research_only": True,
        "production_mutation_allowed": False,
    }
    assert checkpoint_count == 0


async def test_enabled_workflow_advances_exactly_one_injected_page(
    app,
    tmp_path,
) -> None:
    code_version = "leader-shadow-test-v1"
    settings = Settings(
        _env_file=None,
        etf_leader_tactics_continuation_enabled=True,
        etf_leader_tactics_code_version=code_version,
    )
    manifest = _manifest(code_version)
    async with app.state.db.session() as session:
        result = await continue_etf_leader_tactics_shadow_job(
            session,
            settings=settings,
            manifest=manifest,
            handlers=_handlers(),
            artifact_store=ReplayArtifactStore(tmp_path / "leader.sqlite3"),
            timeout_seconds=1.0,
        )

    assert result["advanced_pages"] == 1
    assert result["generation"] == 1
    assert result["phase"] == LeaderContinuationPhase.OUTCOMES.value
    assert result["live_provider_calls"] == 0
    assert result["production_mutation_allowed"] is False

async def test_enabled_discovery_fails_closed_before_creating_checkpoint(app) -> None:
    settings = Settings(
        _env_file=None,
        etf_leader_tactics_continuation_enabled=True,
        etf_leader_tactics_code_version="leader-shadow-test-v1",
    )
    async with app.state.db.session() as session:
        result = await continue_etf_leader_tactics_shadow_job(
            session,
            settings=settings,
        )
        checkpoint_count = await session.scalar(
            select(func.count()).select_from(EtfFactorExperimentCheckpoint)
        )

    assert result["status"] == "insufficient_data"
    assert result["unavailable_reason"] == LEADER_PIT_SOURCE_UNAVAILABLE
    assert result["eligible_pit_sessions"] == 0
    assert result["live_provider_calls"] == 0
    assert checkpoint_count == 0


async def test_first_complete_source_advances_collection_before_252_sessions(
    app,
    tmp_path,
) -> None:
    context = {"kind": "leader-first-source-test", "version": 1}
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 7, 31),
            as_of_trade_date=date(2026, 7, 31),
            scope_kind="all",
        )
        session.add(run)
        await session.flush()
        session.add(
            EtfPitCaptureSource(
                source_signal_run_id=run.id,
                as_of_trade_date=date(2026, 7, 31),
                source_snapshot_hash=_hash("source-first"),
                source_context_hash=stable_contract_hash(context),
                universe_manifest_hash=_hash("universe-first"),
                input_snapshot_hash=_hash("input-first"),
                ranking_contract_hash=_hash("ranking-first"),
                research_contract_hash=_hash("research-first"),
                actionable_contract_hash=_hash("actionable-first"),
                readiness_policy_version="dual_95_v1",
                readiness_state="complete",
                target_date_coverage_ratio=0.96,
                warmup_coverage_ratio=0.96,
                market_decision_cutoff=datetime(2026, 7, 31, 15, 0),
                replay_visibility_cutoff=datetime(2026, 7, 31, 15, 30),
                data_receipt_cutoff=datetime(2026, 7, 31, 15, 30),
                cutoff_timezone="Asia/Shanghai",
                provider_health_hash=_hash("provider-first"),
                source_context_json=context,
            )
        )
        await session.commit()

    settings = Settings(
        _env_file=None,
        etf_leader_tactics_continuation_enabled=True,
        etf_leader_tactics_code_version="leader-first-source-v1",
        etf_leader_tactics_artifact_dir=str(tmp_path),
    )
    async with app.state.db.session() as session:
        result = await continue_etf_leader_tactics_shadow_job(
            session,
            settings=settings,
            timeout_seconds=1.0,
        )

    assert result["advanced_pages"] == 1
    assert result["phase"] == LeaderContinuationPhase.OUTCOMES.value
    assert result["live_provider_calls"] == 0
    assert result["production_mutation_allowed"] is False

    for _ in range(4):
        async with app.state.db.session() as session:
            result = await continue_etf_leader_tactics_shadow_job(
                session,
                settings=settings,
                timeout_seconds=5.0,
            )
    assert result["phase"] == LeaderContinuationPhase.COMPLETE.value, result.get(
        "stop_reason"
    )
    async with app.state.db.session() as session:
        evidence_count = int(
            await session.scalar(
                select(func.count())
                .select_from(EtfFactorExperimentEvidence)
                .where(
                    EtfFactorExperimentEvidence.experiment_family
                    == LEADER_OBSERVATION_EXPERIMENT_FAMILY
                )
            )
            or 0
        )
        skipped = await continue_etf_leader_tactics_shadow_job(
            session,
            settings=settings,
            timeout_seconds=1.0,
        )
    assert evidence_count == 1
    assert skipped["advanced_pages"] == 0
    assert result["production_mutation_allowed"] is False


async def test_admin_entry_is_disabled_by_default(client) -> None:
    response = await client.post(
        "/api/admin/jobs/etf-leader-tactics-shadow/continue"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unavailable_reason"] == LEADER_CONTINUATION_DISABLED
    assert payload["advanced_pages"] == 0
    assert payload["live_provider_calls"] == 0


def test_leader_continuation_scheduler_is_separately_gated(app) -> None:
    disabled = scheduler_module.build_scheduler("Asia/Shanghai")
    scheduler_module.register_default_jobs(
        disabled,
        app.state.db,
        Settings(_env_file=None),
    )
    assert disabled.get_job(LEADER_CONTINUATION_JOB_NAME) is None

    enabled = scheduler_module.build_scheduler("Asia/Shanghai")
    scheduler_module.register_default_jobs(
        enabled,
        app.state.db,
        Settings(
            _env_file=None,
            etf_leader_tactics_continuation_enabled=True,
            etf_leader_tactics_code_version="leader-shadow-test-v1",
        ),
    )
    job = enabled.get_job(LEADER_CONTINUATION_JOB_NAME)
    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
