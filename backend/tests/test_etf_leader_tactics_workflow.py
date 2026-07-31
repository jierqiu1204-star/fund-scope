from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import Settings
from app.models.entities import EtfFactorExperimentCheckpoint
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
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_HYPOTHESIS_REGISTRY,
)
from app.services.workflows.etf_leader_tactics_shadow import (
    LEADER_CONTINUATION_DISABLED,
    LEADER_CONTINUATION_JOB_NAME,
    LEADER_PIT_SESSIONS_INSUFFICIENT,
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
    assert result["unavailable_reason"] == LEADER_PIT_SESSIONS_INSUFFICIENT
    assert result["eligible_pit_sessions"] == 0
    assert result["live_provider_calls"] == 0
    assert checkpoint_count == 0


async def test_admin_entry_is_disabled_by_default(client) -> None:
    response = await client.post(
        "/api/admin/jobs/etf-leader-tactics-shadow/continue"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unavailable_reason"] == LEADER_CONTINUATION_DISABLED
    assert payload["advanced_pages"] == 0
    assert payload["live_provider_calls"] == 0


def test_leader_continuation_is_not_registered_with_scheduler() -> None:
    scheduler_source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "scheduler.py"
    ).read_text(encoding="utf-8")

    assert LEADER_CONTINUATION_JOB_NAME not in scheduler_source
