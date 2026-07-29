from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    NotificationLog,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
)
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_action_policy_validation import (
    CandidateEndpointResult,
    CandidateName,
    DevelopmentGateArtifact,
)
from app.services.strategy_lab.etf_policy_shadow import (
    PolicyDirectionalDiagnostics,
    PolicyShadowContractError,
    build_policy_shadow_surface,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    StageBCoverageDimension,
    StageBDateManifest,
    StageBRankedItem,
    StageBRankingEvent,
)


def _hash(label: str) -> str:
    return stable_contract_hash({"label": label})


@dataclass(frozen=True)
class _RawEvidence:
    evidence_hash: str


def _stage_b_top20() -> tuple[StageBDateManifest, StageBRankingEvent]:
    signal_date = date(2026, 7, 24)
    asset_codes = tuple(f"51{index:04d}" for index in range(20))
    ranked_items = tuple(
        StageBRankedItem(
            asset_code=asset_code,
            rank=index,
            research_score=100.0 - index,
            feature_hash=_hash(f"feature-{asset_code}"),
        )
        for index, asset_code in enumerate(asset_codes, start=1)
    )
    manifest = StageBDateManifest(
        replay_run_key="policy-shadow-run",
        replay_date=signal_date,
        decision_cutoff=datetime(2026, 7, 24, 15, 0),
        score_contract_id="daily_reconstructable_v1",
        score_manifest_hash=_hash("score"),
        feature_schema_version="etf-ranking-stage-a-v1",
        stage_b_schema_version="etf-ranking-stage-b-v1",
        source_snapshot_hash=_hash("source"),
        source_date_manifest_hash=_hash("source-date"),
        universe_manifest_hash=_hash("universe-manifest"),
        universe_hash=_hash("universe"),
        input_hash=_hash("input"),
        feature_manifest_hash=_hash("feature-manifest"),
        authoritative_asset_codes=asset_codes,
        expected_universe_count=20,
        score_eligible_asset_codes=asset_codes,
        exclusions=(),
        coverage=(
            StageBCoverageDimension(
                dimension="score_eligible",
                numerator=20,
                denominator=20,
                rate=1.0,
                exclusions=(),
            ),
        ),
        manifest_hash="pending",
    )
    manifest = replace(
        manifest,
        manifest_hash=stable_contract_hash(
            {
                key: value
                for key, value in asdict(manifest).items()
                if key != "manifest_hash"
            }
        ),
    )
    event = StageBRankingEvent(
        replay_run_key="policy-shadow-run",
        replay_date=signal_date,
        ranking_source_kind="research_replay",
        score_contract_id="daily_reconstructable_v1",
        score_field="research_score",
        score_manifest_hash=manifest.score_manifest_hash,
        stage_b_schema_version=manifest.stage_b_schema_version,
        date_manifest_hash=manifest.manifest_hash,
        source_date_manifest_hash=manifest.source_date_manifest_hash,
        universe_hash=manifest.universe_hash,
        input_hash=manifest.input_hash,
        feature_manifest_hash=manifest.feature_manifest_hash,
        ranked_items=ranked_items,
        all_scored=asset_codes,
        top5=asset_codes[:5],
        top10=asset_codes[:10],
        top20=asset_codes,
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
    return manifest, event


def _endpoint() -> CandidateEndpointResult:
    return CandidateEndpointResult(
        candidate=CandidateName.IDEMPOTENT_ABSOLUTE_TARGETS,
        registry_hash=_hash("registry"),
        candidate_parameter_hash=_hash("candidate"),
        top_n=20,
        horizon_trading_days=10,
        tax_fee_adjusted=True,
        endpoint="mean_action_cycle_benefit",
        development_mean_benefit=0.003,
        validation_mean_benefit=0.002,
        maximum_drawdown=0.08,
        confidence_interval=(-0.001, 0.005),
        action_cycle_count=20,
        independent_trading_day_count=15,
        coverage_numerator=20,
        coverage_denominator=24,
        input_snapshot_hash=_hash("policy-input"),
        trading_calendar_hash=_hash("calendar"),
        opportunity_cost=0.0004,
        derivation_contract_hash=_hash("action-contract"),
        raw_evidence=_RawEvidence(_hash("raw-evidence")),  # type: ignore[arg-type]
    )


def _gate(*, sufficient: bool) -> DevelopmentGateArtifact:
    endpoint = _endpoint()
    return DevelopmentGateArtifact(
        registry_hash=endpoint.registry_hash,
        contract_hash=endpoint.derivation_contract_hash,
        input_snapshot_hash=endpoint.input_snapshot_hash,
        trading_calendar_hash=endpoint.trading_calendar_hash,
        coverage_numerator=endpoint.coverage_numerator,
        coverage_denominator=endpoint.coverage_denominator,
        complete_action_cycle_count=endpoint.action_cycle_count,
        independent_trading_day_count=endpoint.independent_trading_day_count,
        review_candidate=endpoint.candidate,
        sample_gate_passed=sufficient,
        maximum_drawdown_gate_passed=True,
        minimum_practical_benefit_gate_passed=True,
        holdout_ready=sufficient,
        bootstrap_result_hash=_hash("bootstrap"),
        paired_samples_hash=_hash("paired"),
        candidate2_endpoint_evidence_hash=_hash("candidate2"),
        candidate3_endpoint_evidence_hash=_hash("candidate3"),
        walk_forward_boundaries_hash=_hash("walk-forward"),
        purged_samples_hash=_hash("purged"),
        purged_action_cycle_ids=(),
    )


async def _production_counts(session) -> tuple[int, ...]:
    models = (
        ShortResearchSignalRun,
        ShortResearchSignalItem,
        TrackedPosition,
        TrackedPositionAlert,
        NotificationLog,
    )
    counts: list[int] = []
    for model in models:
        counts.append(
            int(await session.scalar(select(func.count()).select_from(model)) or 0)
        )
    return tuple(counts)


@pytest.mark.anyio
async def test_policy_shadow_binds_top20_net_benefit_without_side_effects(
    app,
) -> None:
    manifest, event = _stage_b_top20()
    async with app.state.db.session() as session:
        before = await _production_counts(session)
        surface = build_policy_shadow_surface(
            ranking_events=(event,),
            date_manifests=(manifest,),
            endpoint=_endpoint(),
            development_gate=_gate(sufficient=False),
            action_policy_contract_hash=_hash("policy-contract"),
            directional_diagnostics=PolicyDirectionalDiagnostics(
                stop_loss_sample_count=8,
                stop_loss_continued_down_count=5,
                take_profit_sample_count=7,
                take_profit_near_local_high_count=4,
            ),
        )
        after = await _production_counts(session)

    assert before == after
    assert surface["status"] == "insufficient_data"
    assert surface["primary_metric"]["label"] == (
        "top20_10_trading_day_tax_fee_adjusted_mean_action_cycle_benefit"
    )
    assert surface["primary_metric"]["value"] == 0.002
    assert surface["notification_provenance"] == "shadow_eligible"
    assert surface["execution_provenance"] == "simulated_execution"
    assert surface["provider_receipt"] is None
    assert surface["exploratory_metrics"][0]["primary"] is False
    assert surface["production_mutation_allowed"] is False


def test_policy_shadow_rejects_incomplete_top20() -> None:
    manifest, event = _stage_b_top20()
    incomplete = replace(event, top20=event.top20[:19])

    with pytest.raises(
        PolicyShadowContractError,
        match="complete point-in-time Top20",
    ):
        build_policy_shadow_surface(
            ranking_events=(incomplete,),
            date_manifests=(manifest,),
            endpoint=_endpoint(),
            development_gate=_gate(sufficient=True),
            action_policy_contract_hash=_hash("policy-contract"),
            directional_diagnostics=PolicyDirectionalDiagnostics(0, 0, 0, 0),
        )
