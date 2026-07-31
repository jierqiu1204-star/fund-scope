from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.models.entities import EtfFactorExperimentEvidence
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_leader_tactics_evidence import (
    LEADER_EVIDENCE_SCHEMA_VERSION,
)
from app.services.strategy_lab.etf_leader_tactics_evidence_view import (
    LEADER_COHORT_SPARSE,
    LEADER_DATES_INSUFFICIENT,
    LEADER_EVIDENCE_API_DISABLED,
    LEADER_EVIDENCE_INCOMPATIBLE,
    LEADER_EVIDENCE_NOT_MATERIALIZED,
    LEADER_OUTCOMES_PENDING,
    LEADER_PIT_INPUT_MISSING,
    LEADER_REGISTRY_MISSING,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_EXPERIMENT_FAMILY,
    LEADER_HYPOTHESIS_REGISTRY,
)

ENDPOINT = "/api/short-research/evidence/etf/leader-tactics/latest"


def _hash(label: str) -> str:
    return stable_contract_hash({"label": label})


def _valid_report(status: str = "insufficient_data") -> dict:
    return {
        "schema_version": LEADER_EVIDENCE_SCHEMA_VERSION,
        "experiment_family": LEADER_EXPERIMENT_FAMILY,
        "status": status,
        "unavailable_reason": None,
        "ranking_source_kind": "research_replay",
        "policy_mode": "policy_shadow",
        "notification_provenance": "none",
        "execution_provenance": "simulated_execution",
        "data_cutoff": "2026-07-31T15:00:00",
        "manifest_hash": _hash("manifest"),
        "factor_manifest_hash": _hash("factor"),
        "source_snapshot_hash": _hash("source"),
        "universe_manifest_hash": _hash("universe"),
        "input_snapshot_hash": _hash("input"),
        "feature_panel_hashes": [_hash("feature")],
        "hypothesis_registry": {
            "registry_hash": LEADER_HYPOTHESIS_REGISTRY.registry_hash,
        },
        "candidate_registry": {
            "registry_hash": FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
        },
        "coverage": {"eligible_point_in_time_sessions": 61},
        "exclusion_counts": {},
        "primary_metrics": [
            {
                "candidate_id": "leader_breakout_proxy_v1",
                "mean_paired_net_excess": 0.001,
                "independent_date_count": 40,
                "primary": True,
            }
        ],
        "exploratory_metrics": [],
        "diagnostics": {"residual_overlap": {"momentum": 0.2}},
        "ma5_policy_shadow": [{"status": "available"}],
        "holdout": {"consumed": False},
        "candidate_decisions": [
            {
                "candidate_id": "leader_breakout_proxy_v1",
                "status": status,
                "failed_gates": [],
                "production_mutation_allowed": False,
            }
        ],
        "research_only": True,
        "production_mutation_allowed": False,
    }


async def _insert_evidence(
    app,
    *,
    report: dict,
    registry_hash: str | None = None,
) -> None:
    async with app.state.db.session() as session:
        session.add(
            EtfFactorExperimentEvidence(
                manifest_hash=str(report.get("manifest_hash") or _hash("manifest")),
                ranking_contract_hash=_hash("ranking"),
                code_version="leader-api-test-v1",
                experiment_family=LEADER_EXPERIMENT_FAMILY,
                hypothesis_registry_hash=(
                    LEADER_HYPOTHESIS_REGISTRY.registry_hash
                    if registry_hash is None
                    else registry_hash
                ),
                evidence_hash=_hash("evidence"),
                promotion_state=str(report.get("status") or "insufficient_data"),
                report_json=report,
                costs_json={
                    "fee_bps_per_side": 5.0,
                    "slippage_bps_per_side": 5.0,
                },
                limitations_json=[
                    LEADER_HYPOTHESIS_REGISTRY.non_equivalence_notice
                ],
            )
        )
        await session.commit()


async def _count(app) -> int:
    async with app.state.db.session() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(EtfFactorExperimentEvidence)
            )
            or 0
        )


@pytest.mark.anyio
async def test_leader_evidence_api_is_disabled_and_read_only_by_default(
    app,
    client,
) -> None:
    before = await _count(app)

    response = await client.get(ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "insufficient_data"
    assert payload["unavailable_reason"] == LEADER_EVIDENCE_API_DISABLED
    assert payload["ranking_source_kind"] == "research_replay"
    assert payload["notification_provenance"] == "none"
    assert payload["execution_provenance"] == "none"
    assert payload["production_mutation_allowed"] is False
    assert await _count(app) == before


@pytest.mark.anyio
async def test_enabled_api_reports_missing_materialized_evidence(app, client) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True

    response = await client.get(ENDPOINT)

    assert response.status_code == 200
    assert response.json()["unavailable_reason"] == (
        LEADER_EVIDENCE_NOT_MATERIALIZED
    )


@pytest.mark.anyio
async def test_api_rejects_missing_hypothesis_registry(app, client) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    await _insert_evidence(
        app,
        report=_valid_report(),
        registry_hash=_hash("wrong-registry"),
    )

    payload = (await client.get(ENDPOINT)).json()

    assert payload["unavailable_reason"] == LEADER_REGISTRY_MISSING
    assert payload["primary_metrics"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("exclusions", "coverage", "expected_reason"),
    [
        (
            {"missing_historical_peer_mapping": 3},
            {"eligible_point_in_time_sessions": 300},
            LEADER_PIT_INPUT_MISSING,
        ),
        (
            {"insufficient_candidate_cohort": 2},
            {"eligible_point_in_time_sessions": 300},
            LEADER_COHORT_SPARSE,
        ),
        (
            {"future_window_pending": 4},
            {"eligible_point_in_time_sessions": 300},
            LEADER_OUTCOMES_PENDING,
        ),
        ({}, {"eligible_point_in_time_sessions": 61}, LEADER_DATES_INSUFFICIENT),
    ],
)
async def test_api_exposes_stable_insufficient_data_reasons(
    app,
    client,
    exclusions,
    coverage,
    expected_reason,
) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    report = _valid_report()
    report["exclusion_counts"] = exclusions
    report["coverage"] = coverage
    await _insert_evidence(app, report=report)

    payload = (await client.get(ENDPOINT)).json()

    assert payload["status"] == "insufficient_data"
    assert payload["unavailable_reason"] == expected_reason


@pytest.mark.anyio
async def test_api_fails_closed_for_incompatible_evidence(app, client) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    report = _valid_report()
    report["schema_version"] = "legacy-or-mutated"
    await _insert_evidence(app, report=report)

    payload = (await client.get(ENDPOINT)).json()

    assert payload["unavailable_reason"] == LEADER_EVIDENCE_INCOMPATIBLE
    assert payload["primary_metrics"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "failed_gates"),
    [
        ("rejected", ["adjusted_primary_interval", "residual_incremental_alpha"]),
        ("eligible_for_v4_proposal", []),
    ],
)
async def test_api_keeps_failed_gates_and_proposal_eligibility_research_only(
    app,
    client,
    status,
    failed_gates,
) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    report = deepcopy(_valid_report(status))
    report["coverage"] = {"eligible_point_in_time_sessions": 300}
    report["candidate_decisions"][0]["failed_gates"] = failed_gates
    await _insert_evidence(app, report=report)

    payload = (await client.get(ENDPOINT)).json()

    assert payload["status"] == status
    assert payload["unavailable_reason"] is None
    assert payload["candidate_decisions"][0]["failed_gates"] == failed_gates
    assert payload["primary_metrics"]
    assert payload["research_only"] is True
    assert payload["production_mutation_allowed"] is False
    assert payload["notification_provenance"] == "none"
    assert payload["execution_provenance"] == "simulated_execution"
