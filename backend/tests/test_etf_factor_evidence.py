from datetime import date, datetime, timedelta
from typing import Literal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    NotificationLog,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
    ValidationEvidenceImmutableError,
)
from app.services.strategy_lab.etf_factor_evidence import (
    FactorEvidenceConflictError,
    FactorEvidenceContractError,
    FactorEvidencePayload,
    build_operational_factor_evidence,
    persist_factor_evidence,
)
from app.services.strategy_lab.etf_factor_validation import PromotionDecision
from app.services.strategy_lab.etf_point_in_time_research_loop import (
    PromotionGateEvidence,
    build_frozen_research_loop_manifest,
    evaluate_research_promotion,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    FROZEN_RANKING_CANDIDATES,
    RANKING_COST_CONTRACT_HASH,
)
from app.services.strategy_lab.etf_ranking_validation import RankingEndpointResult


def _payload(
    *,
    manifest_hash: str = "manifest-v1",
    validation_return: float = 0.02,
    promotion_state: Literal[
        "eligible_for_v4_proposal",
        "retain_current_ranking",
    ] = "retain_current_ranking",
) -> FactorEvidencePayload:
    passed = promotion_state == "eligible_for_v4_proposal"
    return FactorEvidencePayload(
        manifest_hash=manifest_hash,
        ranking_contract_hash="ranking-v3",
        code_version="git-sha-1",
        samples=({"asset_code": "510300", "split": "validation"},),
        aggregates={"primary_net_excess": validation_return},
        exclusions=({"asset_code": "159001", "reason": "missing_adjusted_price"},),
        intervals={
            "primary": {"lower": -0.001, "upper": 0.003},
            "multiplicity": {
                "method": "holm_bonferroni",
                "raw_primary_p_values": [0.01, 0.04],
                "adjusted_primary_p_values": [0.02, 0.04],
            },
        },
        split_reports={
            "development": {"sessions": 100},
            "validation": {"sessions": 40},
            "holdout": {"sessions": 20, "consumed": True},
        },
        costs={"fee_bps_per_side": 2.0, "slippage_bps_per_side": 3.0},
        limitations=("research evidence only", "no live ranking mutation"),
        report={"coverage": 0.91},
        promotion=PromotionDecision(
            state=promotion_state,
            passed=passed,
            failed_gates=() if passed else ("adjusted_primary_lower_bound",),
            endpoint="paired_top10_5_session_net_excess_common_support",
        ),
    )


def _hash(label: str) -> str:
    from app.services.etf_research_evidence import stable_contract_hash

    return stable_contract_hash({"label": label})


def _operational_manifest():
    return build_frozen_research_loop_manifest(
        replay_run_key="operational-factor-evidence",
        code_version="test-code",
        source_snapshot_hash=_hash("source"),
        universe_manifest_hash=_hash("universe"),
        split_contract_hash=_hash("split"),
        holdout_identity_hash=_hash("holdout"),
        bootstrap_seed=42,
    )


def _ranking_result(manifest) -> RankingEndpointResult:
    candidate = FROZEN_RANKING_CANDIDATES[1]
    independent_dates = tuple(
        date(2025, 1, 1) + timedelta(days=index * 6) for index in range(40)
    )
    return RankingEndpointResult(
        ranking_source_kind="research_replay",  # type: ignore[arg-type]
        source_cohort_hash=_hash("cohort"),
        candidate_registry_hash=manifest.candidate_registry_hash,
        candidate_id=candidate.candidate_id,
        candidate_manifest_hash=candidate.manifest_hash,
        endpoint_contract_hash=_hash("endpoint-contract"),
        top_n=10,
        horizon_sessions=5,
        endpoint_role="primary",
        endpoint_name="top10_five_session_paired_net_excess",
        coverage_numerator=40,
        coverage_denominator=42,
        coverage_ratio=40 / 42,
        completed_outcome_count=40,
        independent_dates=independent_dates,
        overlapping_excluded_dates=(),
        mean_candidate_gross_return=0.012,
        mean_candidate_net_return=0.010,
        mean_baseline_gross_return=0.009,
        mean_baseline_net_return=0.007,
        mean_paired_net_excess=0.003,
        bootstrap_confidence_interval=(0.0002, 0.0058),
        bootstrap_seed=42,
        bootstrap_resamples=2_000,
        bootstrap_block_length=5,
        bootstrap_input_hash=_hash("bootstrap-input"),
        average_turnover=0.12,
        average_rank_churn=0.08,
        mean_candidate_cost_drag=0.002,
        mean_baseline_cost_drag=0.002,
        fee_bps_per_side=5,
        slippage_bps_per_side=5,
        round_trip_cost_bps=20,
        cost_contract_hash=RANKING_COST_CONTRACT_HASH,
        candidate_maximum_drawdown=0.10,
        baseline_maximum_drawdown=0.09,
        maximum_drawdown_gate_passed=True,
        sample_gate_passed=True,
        accepted_sample_hashes=tuple(
            _hash(f"sample-{index}") for index in range(40)
        ),
        result_hash=_hash("ranking-result"),
    )


async def _production_counts(session: AsyncSession) -> tuple[int, ...]:
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


async def test_evidence_is_idempotent_immutable_and_conflict_safe(app) -> None:
    async with app.state.db.session() as session:
        first = await persist_factor_evidence(session, _payload())
        second = await persist_factor_evidence(session, _payload())
        assert first.id == second.id

        try:
            await persist_factor_evidence(session, _payload(validation_return=0.5))
        except FactorEvidenceConflictError:
            pass
        else:
            raise AssertionError("conflicting evidence must be rejected")

        first.report_json = {"tampered": True}
        try:
            await session.commit()
        except ValidationEvidenceImmutableError:
            await session.rollback()
        else:
            raise AssertionError("persisted evidence must be immutable")


async def test_read_only_evidence_endpoint_separates_splits_and_costs(
    app,
    client,
) -> None:
    async with app.state.db.session() as session:
        evidence = await persist_factor_evidence(session, _payload())

    response = await client.get(
        f"/api/strategy-lab/etf-factor-evidence/{evidence.manifest_hash}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["development"]["sessions"] == 100
    assert body["validation"]["sessions"] == 40
    assert body["holdout"]["consumed"] is True
    assert body["costs"]["fee_bps_per_side"] == 2.0
    assert body["limitations"]
    assert body["research_only"] is True
    assert body["production_mutation_allowed"] is False


async def test_factor_evidence_never_mutates_production_decision_tables(app) -> None:
    async with app.state.db.session() as session:
        before = await _production_counts(session)
        weak = await persist_factor_evidence(session, _payload(manifest_hash="weak"))
        strong = await persist_factor_evidence(
            session,
            _payload(
                manifest_hash="strong",
                validation_return=0.5,
                promotion_state="eligible_for_v4_proposal",
            ),
        )
        after = await _production_counts(session)

    assert before == after
    assert weak.promotion_state == "retain_current_ranking"
    assert strong.promotion_state == "eligible_for_v4_proposal"


async def test_factor_evidence_rejects_uncomputed_holm_claim(app) -> None:
    payload = _payload()
    intervals = {
        **payload.intervals,
        "multiplicity": {
            "method": "holm_bonferroni",
            "raw_primary_p_values": [0.01, 0.04],
            "adjusted_primary_p_values": [0.01, 0.04],
        },
    }
    invalid = FactorEvidencePayload(
        **{
            **payload.__dict__,
            "intervals": intervals,
        }
    )

    async with app.state.db.session() as session:
        with pytest.raises(
            FactorEvidenceContractError,
            match="do not match",
        ):
            await persist_factor_evidence(session, invalid)


async def test_missing_factor_evidence_returns_404(client) -> None:
    response = await client.get(
        "/api/strategy-lab/etf-factor-evidence/does-not-exist"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "ETF factor evidence not found"


async def test_operational_factor_evidence_persists_explicit_promotion_state(
    app,
    client,
) -> None:
    manifest = _operational_manifest()
    ranking_result = _ranking_result(manifest)
    promotion = evaluate_research_promotion(
        PromotionGateEvidence(
            decision_data_coverage_ratio=0.96,
            score_coverage_ratio=0.96,
            eligible_point_in_time_sessions=252,
            independent_primary_dates=40,
            completed_walk_forward_folds=3,
            adjusted_primary_interval_lower=0.0002,
            fold_sign_stable=True,
            regime_sign_stable=True,
            candidate_maximum_drawdown=0.10,
            baseline_maximum_drawdown=0.09,
            holdout_consumed=True,
        )
    )
    payload = build_operational_factor_evidence(
        manifest=manifest,
        data_cutoff=datetime(2026, 7, 24, 15, 0),
        primary_result=ranking_result,
        exploratory_results=(),
        factor_diagnostics={
            "trend": {
                "incremental_ic": 0.02,
                "residualized": True,
            }
        },
        coverage={
            "decision_data_coverage_ratio": 0.96,
            "score_coverage_ratio": 0.96,
            "eligible_point_in_time_sessions": 252,
            "independent_primary_dates": 40,
        },
        exclusion_counts={"future_window_pending": 2},
        split_reports={
            "development": {"sessions": 160},
            "validation": {"sessions": 92},
            "holdout": {"consumed": True, "use_count": 1},
        },
        raw_primary_p_values=(0.01, 0.03),
        promotion=promotion,
        policy_shadow={
            "status": "insufficient_data",
            "unavailable_reason": "insufficient_independent_dates",
            "ranking_source_kind": "research_replay",
            "policy_mode": "policy_shadow",
        },
    )

    async with app.state.db.session() as session:
        evidence = await persist_factor_evidence(session, payload)

    assert evidence.promotion_state == "promotion_eligible"
    assert evidence.report_json["schema_version"] == (
        "etf_point_in_time_research_evidence_v1"
    )
    assert evidence.report_json["primary_metric"]["primary"] is True
    assert evidence.report_json["exploratory_metrics"] == []
    assert evidence.report_json["production_mutation_allowed"] is False
    response = await client.get(
        f"/api/strategy-lab/etf-factor-evidence/{manifest.manifest_hash}"
    )
    assert response.status_code == 200
    assert response.json()["promotion_state"] == "promotion_eligible"
