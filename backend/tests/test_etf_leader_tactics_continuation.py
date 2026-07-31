from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import EtfFactorExperimentCheckpoint, utcnow
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_action_replay.artifact_store import (
    ArtifactConflictError,
    ReplayArtifactStore,
)
from app.services.strategy_lab.etf_leader_tactics_continuation import (
    LeaderContinuationBusyError,
    LeaderContinuationCheckpoint,
    LeaderContinuationHandlers,
    LeaderContinuationManifest,
    LeaderContinuationPage,
    LeaderContinuationPhase,
    LeaderPageArtifact,
    continue_leader_tactics_once,
    new_leader_continuation_checkpoint,
    run_bounded_leader_tactics_continuation,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_HYPOTHESIS_REGISTRY,
)
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    AdaptivePageProfile,
)


def _hash(label: str) -> str:
    return stable_contract_hash({"label": label})


def _manifest(label: str = "base") -> LeaderContinuationManifest:
    return LeaderContinuationManifest(
        run_key=f"leader-test:{label}",
        code_version=f"test-{label}",
        leader_manifest_hash=_hash(f"leader-{label}"),
        factor_manifest_hash=_hash(f"factor-{label}"),
        source_snapshot_hash=_hash(f"source-{label}"),
        universe_manifest_hash=_hash(f"universe-{label}"),
        input_snapshot_hash=_hash(f"input-{label}"),
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        candidate_registry_hash=FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        data_cutoff=datetime(2026, 7, 31, 15),
    )


def _with_page_size(
    checkpoint: LeaderContinuationCheckpoint,
    page_size: int,
) -> LeaderContinuationCheckpoint:
    changed = replace(
        checkpoint,
        page_profile=AdaptivePageProfile(page_size=page_size),
        checkpoint_hash="pending",
    )
    return replace(
        changed,
        checkpoint_hash=stable_contract_hash(changed.canonical_payload()),
    )


def _paged_handler(total: int, *, fail_at: int | None = None):
    async def handler(_manifest, checkpoint, page_size, _timeout_seconds):
        offset = int(checkpoint.phase_cursor.get("offset") or 0)
        if fail_at is not None and offset == fail_at:
            raise OSError(" immutable   PIT page unavailable ")
        stop = min(total, offset + page_size)
        artifacts = tuple(
            LeaderPageArtifact(
                item_key=f"{checkpoint.phase.value}:{index:04d}",
                payload={
                    "phase": checkpoint.phase.value,
                    "index": index,
                    "feature_hash": _hash(
                        f"{checkpoint.phase.value}:{index}"
                    ),
                },
            )
            for index in range(offset, stop)
        )
        return LeaderContinuationPage(
            phase_complete=stop == total,
            next_cursor={"offset": stop},
            artifacts=artifacts,
            coverage={
                checkpoint.phase.value: {
                    "numerator": stop,
                    "denominator": total,
                    "rate": stop / total,
                }
            },
        )

    return handler


def _handlers(handler) -> LeaderContinuationHandlers:
    return LeaderContinuationHandlers(
        features=handler,
        outcomes=handler,
        diagnostics=handler,
        ma5_policy=handler,
        final_evidence=handler,
    )


def test_generic_research_artifacts_are_immutable_and_batch_invariant(
    tmp_path,
) -> None:
    run_id = _hash("same-run")
    rows = tuple((f"row:{index:03d}", {"value": index}) for index in range(23))
    small = ReplayArtifactStore(tmp_path / "small.sqlite3")
    large = ReplayArtifactStore(tmp_path / "large.sqlite3")

    for offset in range(0, len(rows), 5):
        small.write_research_artifacts(
            run_id=run_id,
            phase="features",
            artifacts=rows[offset : offset + 5],
        )
    for offset in range(0, len(rows), 20):
        large.write_research_artifacts(
            run_id=run_id,
            phase="features",
            artifacts=rows[offset : offset + 20],
        )

    assert small.research_phase_digest(
        run_id=run_id,
        phase="features",
    ) == large.research_phase_digest(run_id=run_id, phase="features")
    assert len(
        small.read_research_artifact_page(
            run_id=run_id,
            phase="features",
            max_rows=20,
        )
    ) == 20

    small.write_research_artifacts(
        run_id=run_id,
        phase="features",
        artifacts=(rows[0],),
    )
    with pytest.raises(ArtifactConflictError, match="different payload"):
        small.write_research_artifacts(
            run_id=run_id,
            phase="features",
            artifacts=((rows[0][0], {"value": -1}),),
        )


@pytest.mark.asyncio
async def test_different_page_sizes_produce_identical_final_artifacts(
    tmp_path,
) -> None:
    manifest = _manifest("batch-invariance")

    async def finish(page_size: int, store_path):
        checkpoint = _with_page_size(
            new_leader_continuation_checkpoint(manifest),
            page_size,
        )
        store = ReplayArtifactStore(store_path)
        handlers = _handlers(_paged_handler(23))
        while checkpoint.phase is not LeaderContinuationPhase.COMPLETE:
            checkpoint = await continue_leader_tactics_once(
                artifact_store=store,
                manifest=manifest,
                checkpoint=checkpoint,
                execute_phase=handlers.execute,
                timeout_seconds=1.0,
            )
        return checkpoint

    small = await finish(5, tmp_path / "small-pages.sqlite3")
    large = await finish(20, tmp_path / "large-pages.sqlite3")

    assert small.phase_artifact_hashes == large.phase_artifact_hashes
    assert small.phase_item_counts == large.phase_item_counts
    assert small.coverage == large.coverage
    assert small.exclusions == large.exclusions
    assert small.result_hash == large.result_hash


@pytest.mark.asyncio
async def test_crash_resume_is_compact_idempotent_and_keeps_exact_cursor(
    app,
    tmp_path,
) -> None:
    manifest = _manifest("resume")
    store = ReplayArtifactStore(tmp_path / "resume.sqlite3")
    healthy = _handlers(_paged_handler(23))

    async with app.state.db.session() as session:
        first = await run_bounded_leader_tactics_continuation(
            session,
            artifact_store=store,
            manifest=manifest,
            handlers=healthy,
            timeout_seconds=1.0,
        )
        assert first.phase_cursor == {"offset": 10}

        failed = await run_bounded_leader_tactics_continuation(
            session,
            artifact_store=store,
            manifest=manifest,
            handlers=_handlers(_paged_handler(23, fail_at=10)),
            timeout_seconds=1.0,
        )
        assert failed.phase_cursor == {"offset": 10}
        assert failed.stop_reason == "OSError: immutable PIT page unavailable"

        resumed = await run_bounded_leader_tactics_continuation(
            session,
            artifact_store=store,
            manifest=manifest,
            handlers=healthy,
            timeout_seconds=1.0,
        )
        assert resumed.phase_cursor == {"offset": 20}
        completed_features = await run_bounded_leader_tactics_continuation(
            session,
            artifact_store=store,
            manifest=manifest,
            handlers=healthy,
            timeout_seconds=1.0,
        )

        row = await session.scalar(
            select(EtfFactorExperimentCheckpoint).where(
                EtfFactorExperimentCheckpoint.manifest_hash
                == manifest.manifest_hash
            )
        )

    assert completed_features.phase is LeaderContinuationPhase.OUTCOMES
    assert completed_features.phase_item_counts["features"] == 23
    assert store.artifact_counts(manifest.manifest_hash)["research"] == 23
    assert row is not None
    assert row.lease_token is None
    assert row.lease_expires_at is None
    compact_state = str(row.cached_factor_rows_json)
    assert len(compact_state) < 16_000
    assert "adjusted_close" not in compact_state
    assert "bars" not in compact_state


@pytest.mark.asyncio
async def test_timeout_reduces_page_and_active_database_lease_is_rejected(
    app,
    tmp_path,
) -> None:
    manifest = _manifest("timeout-lease")
    store = ReplayArtifactStore(tmp_path / "timeout.sqlite3")

    async def blocked(*_args):
        await asyncio.sleep(0.2)
        return LeaderContinuationPage(False, {})

    async with app.state.db.session() as session:
        timed_out = await run_bounded_leader_tactics_continuation(
            session,
            artifact_store=store,
            manifest=manifest,
            handlers=_handlers(blocked),
            timeout_seconds=0.05,
        )
        assert timed_out.phase is LeaderContinuationPhase.FEATURES
        assert timed_out.page_profile.page_size == 5
        assert timed_out.stop_reason == "leader continuation phase timed out"

        row = await session.scalar(
            select(EtfFactorExperimentCheckpoint).where(
                EtfFactorExperimentCheckpoint.manifest_hash
                == manifest.manifest_hash
            )
        )
        assert row is not None
        row.status = "running"
        row.lease_token = "another-worker"
        row.lease_expires_at = utcnow() + timedelta(seconds=30)
        await session.commit()

        with pytest.raises(LeaderContinuationBusyError, match="lease"):
            await run_bounded_leader_tactics_continuation(
                session,
                artifact_store=store,
                manifest=manifest,
                handlers=_handlers(_paged_handler(1)),
                timeout_seconds=1.0,
            )
