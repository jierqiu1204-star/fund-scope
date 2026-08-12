from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest

from app.services.strategy_lab.etf_factor_deconfounding import (
    DECONFOUNDING_BASELINE,
    DECONFOUNDING_PIT_FLOW_BREADTH,
    DECONFOUNDING_RESIDUAL_MOMENTUM_BREADTH,
    FROZEN_DECONFOUNDING_CANDIDATES,
    FROZEN_DECONFOUNDING_REGISTRY,
    DeconfoundingContractError,
    build_deconfounding_evidence,
    evaluate_deconfounding_candidates,
    freeze_deconfounding_registry,
)


def _observations(*, flow_visible: bool = False):
    values = {
        "daily_reconstructable_score": 72.0,
        "residual_momentum_score": 80.0,
        "constituent_breadth_score": 60.0,
        "pit_share_flow_score": 90.0,
    }
    return evaluate_deconfounding_candidates(
        signal_date=date(2026, 8, 10),
        data_cutoff=datetime(2026, 8, 10, 15, 0),
        asset_code="510300",
        eligible_history_sessions=250,
        common_peer_count=20,
        input_values=values,
        input_decision_eligible={key: True for key in values},
        input_visible_at_cutoff={
            **{key: True for key in values},
            "pit_share_flow_score": flow_visible,
        },
    )


def test_deconfounding_registry_is_immutable_and_capped_at_three() -> None:
    registry = FROZEN_DECONFOUNDING_REGISTRY

    assert len(registry.candidates) == 3
    assert tuple(candidate.candidate_id for candidate in registry.candidates) == (
        DECONFOUNDING_BASELINE,
        DECONFOUNDING_RESIDUAL_MOMENTUM_BREADTH,
        DECONFOUNDING_PIT_FLOW_BREADTH,
    )
    assert all(len(candidate.manifest_hash) == 64 for candidate in registry.candidates)

    with pytest.raises(DeconfoundingContractError, match="at most three"):
        freeze_deconfounding_registry(
            (*FROZEN_DECONFOUNDING_CANDIDATES, FROZEN_DECONFOUNDING_CANDIDATES[0])
        )
    with pytest.raises(DeconfoundingContractError, match="frozen"):
        freeze_deconfounding_registry(
            (
                replace(
                    FROZEN_DECONFOUNDING_CANDIDATES[0],
                    formula="changed_after_outcomes",
                ),
            )
        )


def test_missing_pit_input_is_unavailable_without_baseline_fallback() -> None:
    observations = {item.candidate_id: item for item in _observations()}

    assert observations[DECONFOUNDING_BASELINE].score == 72.0
    assert observations[DECONFOUNDING_RESIDUAL_MOMENTUM_BREADTH].score == 70.0
    pit = observations[DECONFOUNDING_PIT_FLOW_BREADTH]
    assert pit.score is None
    assert pit.availability == "unavailable"
    assert pit.unavailable_reasons == ("pit_share_flow_score:not_visible_at_cutoff",)
    assert pit.score != observations[DECONFOUNDING_BASELINE].score


def test_deconfounding_evidence_is_compact_deterministic_and_research_only() -> None:
    observations = _observations(flow_visible=True)
    diagnostics = {
        "contract_version": "etf_factor_redundancy_clusters_v1",
        "effective_factor_count": 2,
        "research_only": True,
        "production_mutation_allowed": False,
    }
    first = build_deconfounding_evidence(
        observations,
        redundancy_diagnostics=diagnostics,
    )
    second = build_deconfounding_evidence(
        tuple(reversed(observations)),
        redundancy_diagnostics=diagnostics,
    )

    assert first == second
    assert first["candidate_count"] == 3
    assert first["state"] == "available"
    assert first["production_mutation_allowed"] is False
    assert "observations" not in first
    assert len(first["evidence_hash"]) == 64


def test_deconfounding_candidates_fail_closed_below_common_support() -> None:
    observations = evaluate_deconfounding_candidates(
        signal_date=date(2026, 8, 10),
        data_cutoff=datetime(2026, 8, 10, 15, 0),
        asset_code="510300",
        eligible_history_sessions=250,
        common_peer_count=19,
        input_values={
            "daily_reconstructable_score": 72.0,
            "residual_momentum_score": 80.0,
            "constituent_breadth_score": 60.0,
            "pit_share_flow_score": 90.0,
        },
        input_decision_eligible={
            "daily_reconstructable_score": True,
            "residual_momentum_score": True,
            "constituent_breadth_score": True,
            "pit_share_flow_score": True,
        },
        input_visible_at_cutoff={
            "daily_reconstructable_score": True,
            "residual_momentum_score": True,
            "constituent_breadth_score": True,
            "pit_share_flow_score": True,
        },
    )

    assert all(item.score is None for item in observations)
    assert all(
        "insufficient_common_peer_support" in item.unavailable_reasons for item in observations
    )
    evidence = build_deconfounding_evidence(observations)
    assert evidence["state"] == "insufficient_data"
