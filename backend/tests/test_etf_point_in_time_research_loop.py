from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, time

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    MAX_DRAWDOWN_DETERIORATION,
    MIN_PRIMARY_INDEPENDENT_DATES,
    MIN_PRODUCTION_COVERAGE,
    MIN_PROMOTION_SESSIONS,
    MIN_WALK_FORWARD_FOLDS,
    AdaptivePageProfile,
    PromotionGateEvidence,
    ResearchLoopContractError,
    ResearchLoopPhase,
    ResearchLoopPhaseHandlers,
    ResearchLoopPhaseResult,
    ResearchPromotionState,
    build_frozen_research_loop_manifest,
    continue_research_loop_once,
    evaluate_research_promotion,
    new_research_loop_checkpoint,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    freeze_ranking_candidate_registry,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    StageBCoverageDimension,
    StageBDateManifest,
    StageBRankedItem,
    StageBRankingEvent,
)
from app.services.strategy_lab.etf_validation_manifest import (
    build_research_replay_validation_source_cohort,
)
from app.services.strategy_lab.jobs import (
    continue_etf_point_in_time_research_job,
)


def _hash(label: str) -> str:
    return stable_contract_hash({"label": label})


def _manifest():
    return build_frozen_research_loop_manifest(
        replay_run_key="pit-research-2026",
        code_version="test-code-v1",
        source_snapshot_hash=_hash("source"),
        universe_manifest_hash=_hash("universe"),
        split_contract_hash=_hash("split"),
        holdout_identity_hash=_hash("holdout"),
        bootstrap_seed=20260725,
    )


def test_manifest_freezes_current_contracts_and_three_candidates() -> None:
    manifest = _manifest()
    registry = freeze_ranking_candidate_registry(FROZEN_RANKING_CANDIDATES)

    assert len(registry.candidates) == 3
    assert manifest.candidate_registry_hash == registry.registry_hash
    assert manifest.minimum_promotion_sessions == 252
    assert manifest.minimum_independent_dates == 40
    assert manifest.minimum_walk_forward_folds == 3
    assert manifest.minimum_production_coverage == 0.95
    assert manifest.maximum_drawdown_deterioration == 0.02
    assert len(manifest.manifest_hash) == 64

    with pytest.raises(
        ResearchLoopContractError,
        match="frozen production contracts",
    ):
        replace(manifest, candidate_registry_hash=_hash("grid-search")).validate()


def test_promotion_is_insufficient_before_all_real_sample_gates() -> None:
    decision = evaluate_research_promotion(
        PromotionGateEvidence(
            decision_data_coverage_ratio=MIN_PRODUCTION_COVERAGE,
            score_coverage_ratio=MIN_PRODUCTION_COVERAGE,
            eligible_point_in_time_sessions=MIN_PROMOTION_SESSIONS - 1,
            independent_primary_dates=MIN_PRIMARY_INDEPENDENT_DATES - 1,
            completed_walk_forward_folds=MIN_WALK_FORWARD_FOLDS - 1,
            adjusted_primary_interval_lower=None,
            fold_sign_stable=True,
            regime_sign_stable=True,
            candidate_maximum_drawdown=0.10,
            baseline_maximum_drawdown=0.10,
            holdout_consumed=False,
        )
    )

    assert decision.state is ResearchPromotionState.INSUFFICIENT_DATA
    assert {
        "eligible_point_in_time_sessions",
        "independent_primary_dates",
        "walk_forward_folds",
        "adjusted_primary_interval",
        "holdout_not_consumed",
    } <= set(decision.failed_gates)
    assert decision.production_mutation_allowed is False


def test_promotion_requires_positive_adjusted_interval_and_drawdown_gate() -> None:
    base = PromotionGateEvidence(
        decision_data_coverage_ratio=MIN_PRODUCTION_COVERAGE,
        score_coverage_ratio=MIN_PRODUCTION_COVERAGE,
        eligible_point_in_time_sessions=MIN_PROMOTION_SESSIONS,
        independent_primary_dates=MIN_PRIMARY_INDEPENDENT_DATES,
        completed_walk_forward_folds=MIN_WALK_FORWARD_FOLDS,
        adjusted_primary_interval_lower=0.001,
        fold_sign_stable=True,
        regime_sign_stable=True,
        candidate_maximum_drawdown=0.12,
        baseline_maximum_drawdown=0.10,
        holdout_consumed=True,
    )
    assert (
        evaluate_research_promotion(base).state
        is ResearchPromotionState.PROMOTION_ELIGIBLE
    )

    failed = evaluate_research_promotion(
        replace(
            base,
            adjusted_primary_interval_lower=0.0,
            candidate_maximum_drawdown=(
                base.baseline_maximum_drawdown
                + MAX_DRAWDOWN_DETERIORATION
                + 0.0001
            ),
        )
    )
    assert failed.state is ResearchPromotionState.PROMOTION_INELIGIBLE
    assert set(failed.failed_gates) == {
        "adjusted_primary_interval",
        "maximum_drawdown",
    }


def test_adaptive_page_profile_is_bounded_and_not_part_of_manifest() -> None:
    manifest_hash = _manifest().manifest_hash
    profile = AdaptivePageProfile()

    profile = profile.after("healthy")
    assert profile.page_size == 10
    profile = profile.after("healthy")
    assert profile.page_size == 15
    profile = profile.after("healthy").after("healthy")
    assert profile.page_size == 20
    profile = profile.after("timeout")
    assert profile.page_size == 10
    profile = profile.after("memory_pressure")
    assert profile.page_size == 5
    assert _manifest().manifest_hash == manifest_hash


def _with_page_size(checkpoint, page_size: int):
    changed = replace(
        checkpoint,
        page_profile=AdaptivePageProfile(page_size=page_size),
        checkpoint_hash="pending",
    )
    return replace(
        changed,
        checkpoint_hash=stable_contract_hash(changed.canonical_payload()),
    )


@pytest.mark.asyncio
async def test_phase_handlers_and_page_size_changes_preserve_final_artifacts() -> None:
    manifest = _manifest()
    total_units = 23
    calls: list[ResearchLoopPhase] = []

    async def handler(
        _manifest,
        checkpoint,
        page_size,
        _timeout_seconds,
    ):
        calls.append(checkpoint.phase)
        offset = int(checkpoint.phase_cursor.get("offset") or 0)
        next_offset = min(total_units, offset + page_size)
        complete = next_offset == total_units
        return ResearchLoopPhaseResult(
            phase_complete=complete,
            processed_count=next_offset - offset,
            artifact_hash=(
                _hash(f"{checkpoint.phase.value}-canonical-final")
                if complete
                else None
            ),
            cursor={"offset": next_offset},
            coverage={
                checkpoint.phase.value: {
                    "numerator": next_offset,
                    "denominator": total_units,
                    "rate": next_offset / total_units,
                }
            },
            exclusions={},
        )

    handlers = ResearchLoopPhaseHandlers(
        inputs=handler,
        stage_a=handler,
        stage_b=handler,
        candidates=handler,
        forward_outcomes=handler,
        ranking_validation=handler,
        factor_evidence=handler,
        policy_shadow=handler,
        final_evidence=handler,
    )

    async def finish(initial_page_size: int, *, resize_after: int | None = None):
        checkpoint = _with_page_size(
            new_research_loop_checkpoint(manifest),
            initial_page_size,
        )
        continuations = 0
        while checkpoint.phase is not ResearchLoopPhase.COMPLETE:
            checkpoint = await continue_research_loop_once(
                manifest=manifest,
                checkpoint=checkpoint,
                execute_phase=handlers.execute,
                timeout_seconds=1.0,
            )
            continuations += 1
            if resize_after is not None and continuations == resize_after:
                checkpoint = _with_page_size(checkpoint, 20)
        return checkpoint

    small_pages = await finish(5, resize_after=2)
    large_pages = await finish(20)

    assert small_pages.processed_count == large_pages.processed_count == (
        total_units * 9
    )
    assert small_pages.phase_artifact_hashes == large_pages.phase_artifact_hashes
    assert small_pages.coverage == large_pages.coverage
    assert small_pages.exclusions == large_pages.exclusions
    assert small_pages.result_hash == large_pages.result_hash
    assert set(calls) == set(ResearchLoopPhase) - {ResearchLoopPhase.COMPLETE}


@pytest.mark.asyncio
async def test_scheduler_job_advances_exactly_one_durable_page_per_trigger(
    app,
) -> None:
    manifest = _manifest()

    async def handler(
        _manifest,
        checkpoint,
        page_size,
        _timeout_seconds,
    ):
        return ResearchLoopPhaseResult(
            phase_complete=True,
            processed_count=page_size,
            artifact_hash=_hash(f"{checkpoint.phase.value}-complete"),
            cursor={},
            coverage={
                checkpoint.phase.value: {
                    "numerator": page_size,
                    "denominator": page_size,
                    "rate": 1.0,
                }
            },
            exclusions={},
        )

    handlers = ResearchLoopPhaseHandlers(
        inputs=handler,
        stage_a=handler,
        stage_b=handler,
        candidates=handler,
        forward_outcomes=handler,
        ranking_validation=handler,
        factor_evidence=handler,
        policy_shadow=handler,
        final_evidence=handler,
    )
    async with app.state.db.session() as session:
        first = await continue_etf_point_in_time_research_job(
            session,
            manifest=manifest,
            handlers=handlers,
            timeout_seconds=1.0,
        )
        second = await continue_etf_point_in_time_research_job(
            session,
            manifest=manifest,
            handlers=handlers,
            timeout_seconds=1.0,
        )

    assert first["phase"] == "stage_a"
    assert first["generation"] == 1
    assert second["phase"] == "stage_b"
    assert second["generation"] == 2


@pytest.mark.asyncio
async def test_one_continuation_advances_one_page_and_preserves_hashes() -> None:
    manifest = _manifest()
    checkpoint = new_research_loop_checkpoint(manifest)
    calls: list[tuple[ResearchLoopPhase, int]] = []

    async def execute_phase(
        phase,
        _manifest,
        _checkpoint,
        page_size,
        _timeout_seconds,
    ):
        calls.append((phase, page_size))
        return ResearchLoopPhaseResult(
            phase_complete=True,
            processed_count=page_size,
            artifact_hash=_hash(f"{phase.value}-artifact"),
            cursor={"after": page_size},
            coverage={
                "point_in_time_universe": {
                    "numerator": page_size,
                    "denominator": page_size,
                    "rate": 1.0,
                }
            },
            exclusions={},
            peak_rss_bytes=1024,
        )

    updated = await continue_research_loop_once(
        manifest=manifest,
        checkpoint=checkpoint,
        execute_phase=execute_phase,
        timeout_seconds=1.0,
    )

    assert calls == [(ResearchLoopPhase.INPUTS, 10)]
    assert updated.phase is ResearchLoopPhase.STAGE_A
    assert updated.generation == 1
    assert updated.processed_count == 10
    assert len(updated.phase_artifact_hashes["inputs"]) == 1
    assert updated.checkpoint_hash != checkpoint.checkpoint_hash


@pytest.mark.asyncio
async def test_timeout_keeps_phase_and_reduces_page_size() -> None:
    manifest = _manifest()
    checkpoint = new_research_loop_checkpoint(manifest)

    async def execute_phase(*_args):
        return ResearchLoopPhaseResult(
            phase_complete=False,
            processed_count=0,
            artifact_hash=None,
            cursor={},
            coverage={},
            exclusions={},
            outcome="timeout",
            error_summary="provider timeout",
        )

    updated = await continue_research_loop_once(
        manifest=manifest,
        checkpoint=checkpoint,
        execute_phase=execute_phase,
        timeout_seconds=1.0,
    )

    assert updated.phase is ResearchLoopPhase.INPUTS
    assert updated.page_profile.page_size == 5
    assert updated.stop_reason == "provider timeout"


def _stage_b_pair(manifest, *, signal_date):
    decision_cutoff = datetime.combine(
        signal_date,
        time(15, 0),
    )
    date_manifest = StageBDateManifest(
        replay_run_key=manifest.replay_run_key,
        replay_date=signal_date,
        decision_cutoff=decision_cutoff,
        score_contract_id=manifest.research_contract_id,
        score_manifest_hash=manifest.research_contract_hash,
        feature_schema_version="etf-ranking-stage-a-v1",
        stage_b_schema_version="etf-ranking-stage-b-v1",
        source_snapshot_hash=manifest.source_snapshot_hash,
        source_date_manifest_hash=_hash(f"source-date-{signal_date}"),
        universe_manifest_hash=manifest.universe_manifest_hash,
        universe_hash=_hash(f"universe-{signal_date}"),
        input_hash=_hash(f"input-{signal_date}"),
        feature_manifest_hash=_hash(f"features-{signal_date}"),
        authoritative_asset_codes=("510300",),
        expected_universe_count=1,
        score_eligible_asset_codes=("510300",),
        exclusions=(),
        coverage=(
            StageBCoverageDimension(
                dimension="score_eligible",
                numerator=1,
                denominator=1,
                rate=1.0,
                exclusions=(),
            ),
        ),
        manifest_hash="pending",
    )
    date_manifest = replace(
        date_manifest,
        manifest_hash=stable_contract_hash(
            {
                key: value
                for key, value in asdict(date_manifest).items()
                if key != "manifest_hash"
            }
        ),
    )
    ranked = StageBRankedItem(
        asset_code="510300",
        rank=1,
        research_score=70.0,
        feature_hash=_hash(f"feature-{signal_date}"),
    )
    event = StageBRankingEvent(
        replay_run_key=manifest.replay_run_key,
        replay_date=signal_date,
        ranking_source_kind="research_replay",
        score_contract_id=manifest.research_contract_id,
        score_field=manifest.research_score_field,
        score_manifest_hash=manifest.research_contract_hash,
        stage_b_schema_version="etf-ranking-stage-b-v1",
        date_manifest_hash=date_manifest.manifest_hash,
        source_date_manifest_hash=date_manifest.source_date_manifest_hash,
        universe_hash=date_manifest.universe_hash,
        input_hash=date_manifest.input_hash,
        feature_manifest_hash=date_manifest.feature_manifest_hash,
        ranked_items=(ranked,),
        all_scored=("510300",),
        top5=("510300",),
        top10=("510300",),
        top20=("510300",),
        event_hash="pending",
    )
    event = replace(
        event,
        event_hash=stable_contract_hash(
            {
                key: value
                for key, value in asdict(event).items()
                if key != "event_hash"
            }
        ),
    )
    return date_manifest, event


def test_stage_b_research_events_build_a_research_only_validation_cohort() -> None:
    manifest = _manifest()
    date_manifest, event = _stage_b_pair(
        manifest,
        signal_date=date(2026, 7, 24),
    )
    cohort = build_research_replay_validation_source_cohort(
        ranking_events=(event,),
        date_manifests=(date_manifest,),
        replay_contract_hash=manifest.manifest_hash,
    )

    assert cohort.ranking_source_kind.value == "research_replay"
    assert cohort.source_replay_run_key == manifest.replay_run_key
    assert cohort.events[0].publication_state is None
    assert cohort.events[0].source_signal_run_id is None
    assert cohort.events[0].availability_cutoff == date_manifest.decision_cutoff
