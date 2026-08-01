from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_leader_tactics_observation import (
    LEADER_OBSERVATION_DATES_INSUFFICIENT,
    LEADER_OBSERVATION_FOLDS_INSUFFICIENT,
    LEADER_OBSERVATION_INDEPENDENT_DATES_INSUFFICIENT,
    LeaderObservationContractError,
    LeaderObservationManifest,
    LeaderObservationMatch,
    LeaderObservationPrimitive,
    LeaderObservationProgress,
    LeaderObservationReport,
    LeaderOutcomeMaturityManifest,
    LeaderPromotionGateProgress,
    build_leader_matured_outcome,
    build_leader_observation_primitive,
    finalize_leader_observation_primitives,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_BREAKOUT_CANDIDATE,
    LEADER_HYPOTHESIS_REGISTRY,
    LeaderAdjustedBar,
    LeaderPitAssetInput,
)
from app.services.strategy_lab.etf_ranking_candidates import (
    REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
)


def _hash(label: str) -> str:
    return stable_contract_hash({"label": label})


def _manifest() -> LeaderObservationManifest:
    return LeaderObservationManifest(
        source_id=1,
        source_signal_run_id=7,
        signal_date=date(2026, 7, 31),
        source_context_hash=_hash("context"),
        source_snapshot_hash=_hash("source"),
        universe_manifest_hash=_hash("universe"),
        input_snapshot_hash=_hash("input"),
        ranking_contract_hash=_hash("ranking"),
        research_contract_hash=_hash("research"),
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        candidate_registry_hash=FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        data_cutoff=datetime(2026, 7, 31, 15, 30, tzinfo=UTC),
        code_version="leader-observation-test-v1",
    )


def test_first_complete_source_can_accumulate_without_promotion() -> None:
    progress = LeaderPromotionGateProgress(
        eligible_pit_sessions=1,
        independent_primary_dates=0,
        completed_walk_forward_folds=0,
    )

    assert progress.accumulation_allowed is True
    assert progress.sample_gates_passed is False
    assert progress.failed_gates == (
        LEADER_OBSERVATION_DATES_INSUFFICIENT,
        LEADER_OBSERVATION_INDEPENDENT_DATES_INSUFFICIENT,
        LEADER_OBSERVATION_FOLDS_INSUFFICIENT,
    )
    assert progress.to_dict()["required_pit_sessions"] == 252


def test_promotion_sample_gate_requires_all_frozen_counts() -> None:
    progress = LeaderPromotionGateProgress(
        eligible_pit_sessions=252,
        independent_primary_dates=40,
        completed_walk_forward_folds=3,
    )

    assert progress.accumulation_allowed is True
    assert progress.sample_gates_passed is True
    assert progress.failed_gates == ()


def test_observation_and_maturity_manifests_are_deterministic_and_bound() -> None:
    observation = _manifest()
    same = _manifest()
    maturity = LeaderOutcomeMaturityManifest(
        observation_manifest_hash=observation.manifest_hash,
        observation_hash=_hash("observation"),
        signal_date=observation.signal_date,
        outcome_cutoff=datetime(2026, 8, 14, 15, 30, tzinfo=UTC),
        execution_cost_contract_hash=_hash("costs"),
        adjusted_price_contract_hash=_hash("prices"),
        code_version=observation.code_version,
    )

    assert observation.manifest_hash == same.manifest_hash
    assert maturity.manifest_hash == maturity.manifest_hash
    assert maturity.manifest_hash != observation.manifest_hash

    with pytest.raises(
        LeaderObservationContractError,
        match="registry identity",
    ):
        replace(
            observation,
            candidate_registry_hash=_hash("mutated-registry"),
        ).validate()


def test_complete_observation_report_is_research_only_and_zero_match_safe() -> None:
    manifest = _manifest()
    progress = LeaderObservationProgress(
        source_id=manifest.source_id,
        signal_date=manifest.signal_date,
        state="complete",
        processed_asset_count=1490,
        total_asset_count=1490,
        page_size=20,
        current_phase="final_evidence",
        checkpoint_hash=_hash("checkpoint"),
    )
    report = LeaderObservationReport(
        manifest_hash=manifest.manifest_hash,
        observation_hash=_hash("observation"),
        signal_date=manifest.signal_date,
        data_cutoff=manifest.data_cutoff,
        progress=progress,
        promotion_gates=LeaderPromotionGateProgress(1, 0, 0),
        exclusion_counts={"missing_historical_peer_mapping": 12},
        pending_outcome_count=0,
    )

    payload = report.to_dict()

    assert payload["current_matches"] == []
    assert payload["status"] == "insufficient_data"
    assert payload["research_only"] is True
    assert payload["production_mutation_allowed"] is False
    assert payload["progress"]["completion_ratio"] == 1.0


def test_observation_match_rejects_non_finite_score() -> None:
    with pytest.raises(LeaderObservationContractError):
        LeaderObservationMatch(
            candidate_id=LEADER_BREAKOUT_CANDIDATE,
            asset_code="510300",
            asset_name="ETF",
            score=float("nan"),
            rank=1,
            matched_gates=("breakout",),
            feature_hash=_hash("feature"),
        )


def test_outcome_stays_pending_until_full_window_then_applies_costs() -> None:
    feature_hash = _hash("feature")
    pending = build_leader_matured_outcome(
        candidate_id=LEADER_BREAKOUT_CANDIDATE,
        asset_code="510300",
        signal_date=date(2026, 7, 31),
        horizon_sessions=5,
        feature_hash=feature_hash,
        adjusted_closes_after_signal=(
            (date(2026, 8, 3), 100.0),
            (date(2026, 8, 4), 101.0),
        ),
        round_trip_cost_bps=20.0,
    )
    matured = build_leader_matured_outcome(
        candidate_id=LEADER_BREAKOUT_CANDIDATE,
        asset_code="510300",
        signal_date=date(2026, 7, 31),
        horizon_sessions=5,
        feature_hash=feature_hash,
        adjusted_closes_after_signal=tuple(
            (date(2026, 8, 3 + index), 100.0 + index)
            for index in range(6)
        ),
        round_trip_cost_bps=20.0,
    )

    assert pending.status == "pending"
    assert pending.gross_return is None
    assert pending.net_return is None
    assert matured.status == "matured"
    assert matured.gross_return == pytest.approx(0.05)
    assert matured.net_return == pytest.approx(0.048)


def _primitive_input(code: str, *, peer_group: str, slope: float) -> LeaderPitAssetInput:
    start = date(2026, 1, 1)
    cutoff = datetime(2026, 6, 29, 16, tzinfo=UTC)
    bars = tuple(
        LeaderAdjustedBar(
            trade_date=start + timedelta(days=index),
            adjusted_open=100.0 + slope * index - 0.05,
            adjusted_high=100.0 + slope * index + 0.10,
            adjusted_low=100.0 + slope * index - 0.10,
            adjusted_close=100.0 + slope * index,
            volume=1_000.0 + index,
            turnover=1_000_000.0 * (1.0 + slope),
            observed_at=cutoff - timedelta(minutes=1),
        )
        for index in range(180)
    )
    return LeaderPitAssetInput(
        asset_code=code,
        signal_date=bars[-1].trade_date,
        source_cutoff=cutoff,
        baseline_score=70.0,
        bars=bars,
        historical_member=True,
        membership_effective_date=bars[0].trade_date,
        membership_observed_at=cutoff - timedelta(days=200),
        peer_group=peer_group,
        peer_mapping_effective_date=bars[0].trade_date,
        peer_mapping_observed_at=cutoff - timedelta(days=200),
        peer_mapping_kind="historical_pit",
        sector_trend_score=float(peer_group[-1]),
        sector_trend_as_of=bars[-1].trade_date,
        sector_trend_observed_at=cutoff - timedelta(minutes=1),
        sector_trend_contract_hash=_hash("sector-contract"),
        market_regime="risk_on",
        market_regime_as_of=bars[-1].trade_date,
        market_regime_observed_at=cutoff - timedelta(minutes=1),
        market_regime_contract_hash=REGIME_LIQUIDITY_GATE_CONTRACT_HASH,
        market_regime_status="available",
        clone_group=code,
        tracked_index=f"index-{peer_group}",
        theme=peer_group,
        sector=peer_group,
    )


def test_compact_primitives_are_resume_and_batch_order_invariant() -> None:
    inputs = tuple(
        _primitive_input(
            f"51{group}{index:02d}",
            peer_group=f"sector-{group}",
            slope=0.01 * (index + 1),
        )
        for group in range(1, 4)
        for index in range(5)
    )
    primitives = tuple(build_leader_observation_primitive(item) for item in inputs)
    restored = tuple(
        LeaderObservationPrimitive.from_dict(item.to_dict())
        for item in primitives
    )

    forward = finalize_leader_observation_primitives(restored)
    reversed_result = finalize_leader_observation_primitives(
        tuple(reversed(restored))
    )

    assert forward.observation_hash == reversed_result.observation_hash
    assert forward.all_observation_hashes == reversed_result.all_observation_hashes
    assert len(forward.all_observation_hashes) == 45
