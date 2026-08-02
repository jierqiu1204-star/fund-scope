from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfFactorExperimentCheckpoint,
    EtfFactorExperimentEvidence,
)
from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_leader_tactics_continuation import (
    LEADER_CONTINUATION_STATE_KEY,
)
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
from app.services.strategy_lab.etf_leader_tactics_historical_backtest import (
    HISTORICAL_BACKTEST_CONTRACT_HASH,
    LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
    LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
    LEADER_HISTORICAL_BACKTEST_NOT_PIT,
    LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
    LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
)
from app.services.strategy_lab.etf_leader_tactics_historical_proxy import (
    LEADER_HISTORICAL_PROXY_EVIDENCE_MODE,
    LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
    LEADER_HISTORICAL_PROXY_NOT_PIT,
    LEADER_HISTORICAL_PROXY_REPORT_KIND,
    LEADER_HISTORICAL_PROXY_SCHEMA_VERSION,
)
from app.services.strategy_lab.etf_leader_tactics_observation import (
    LEADER_MATURITY_EXPERIMENT_FAMILY,
    LEADER_MATURITY_REPORT_KIND,
    LEADER_MATURITY_SCHEMA_VERSION,
    LEADER_OBSERVATION_EXPERIMENT_FAMILY,
    LEADER_OBSERVATION_REPORT_KIND,
    LEADER_OBSERVATION_SCHEMA_VERSION,
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
    experiment_family: str = LEADER_EXPERIMENT_FAMILY,
) -> None:
    async with app.state.db.session() as session:
        session.add(
            EtfFactorExperimentEvidence(
                manifest_hash=str(report.get("manifest_hash") or _hash("manifest")),
                ranking_contract_hash=_hash("ranking"),
                code_version="leader-api-test-v1",
                experiment_family=experiment_family,
                hypothesis_registry_hash=(
                    LEADER_HYPOTHESIS_REGISTRY.registry_hash
                    if registry_hash is None
                    else registry_hash
                ),
                evidence_hash=stable_contract_hash(
                    {
                        "experiment_family": experiment_family,
                        "report": report,
                    }
                ),
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


def _observation_report(*, match: bool = True) -> dict:
    manifest_hash = _hash("observation-manifest")
    observations = (
        [
            {
                "candidate_id": "leader_breakout_proxy_v1",
                "asset_code": "510300",
                "asset_name": None,
                "signal_date": "2026-07-31",
                "source_cutoff": "2026-07-31T16:00:00+08:00",
                "availability": "available",
                "qualifies": True,
                "score": 0.88,
                "rank": 1,
                "peer_group": "broad-market",
                "theme": "宽基",
                "sector": None,
                "matched_gates": [],
                "gate_reasons": [],
                "unavailable_reasons": [],
                "components": {},
                "feature_hash": _hash("observation-feature"),
            }
        ]
        if match
        else []
    )
    return {
        "schema_version": LEADER_OBSERVATION_SCHEMA_VERSION,
        "report_kind": LEADER_OBSERVATION_REPORT_KIND,
        "experiment_family": LEADER_OBSERVATION_EXPERIMENT_FAMILY,
        "manifest_hash": manifest_hash,
        "observation_manifest_hash": manifest_hash,
        "status": "insufficient_data",
        "ranking_source_kind": "research_replay",
        "policy_mode": "none",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "research_only": True,
        "production_mutation_allowed": False,
        "observation_state": "observing",
        "observation_unavailable_reason": "leader_observation_dates_below_252",
        "observation_data_cutoff": "2026-07-31T16:00:00+08:00",
        "observation_counts": {
            "eligible_pit_sessions": 1,
            "materialized_pit_sessions": 1,
            "required_pit_sessions": 252,
            "current_input_asset_count": 1490,
            "current_available_observation_count": 3 if match else 0,
            "current_qualifying_observation_count": len(observations),
            "returned_current_observation_count": len(observations),
            "current_observations_truncated": False,
            "pending_outcome_count": len(observations),
            "matured_outcome_count": 0,
            "outcomes_by_horizon": [],
            "independent_primary_date_count": 0,
            "required_primary_date_count": 40,
            "completed_walk_forward_fold_count": 0,
            "required_walk_forward_fold_count": 3,
        },
        "current_observations": observations,
        "pending_outcomes": [
            {
                "candidate_id": item["candidate_id"],
                "asset_code": item["asset_code"],
                "signal_date": item["signal_date"],
                "pending_horizons": [5, 10],
                "feature_hash": item["feature_hash"],
            }
            for item in observations
        ],
        "partial_checkpoint": {"state": "complete"},
    }


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
async def test_historical_proxy_is_visible_without_pit_gate_credit(
    app,
    client,
) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    manifest_hash = _hash("historical-proxy-manifest")
    report = {
        "schema_version": LEADER_HISTORICAL_PROXY_SCHEMA_VERSION,
        "report_kind": LEADER_HISTORICAL_PROXY_REPORT_KIND,
        "experiment_family": LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
        "manifest_hash": manifest_hash,
        "status": "insufficient_data",
        "unavailable_reason": LEADER_HISTORICAL_PROXY_NOT_PIT,
        "ranking_source_kind": "research_replay",
        "evidence_mode": LEADER_HISTORICAL_PROXY_EVIDENCE_MODE,
        "policy_mode": "none",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "signal_date": "2026-07-31",
        "signal_run_id": 121,
        "history_sessions": 180,
        "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
        "price_basis": "total_return_adjusted",
        "contract_hash": _hash("historical-proxy-contract"),
        "source_ranking_contract_hash": _hash("historical-ranking"),
        "source_input_snapshot_hash": _hash("historical-input"),
        "coverage": {
            "source_ranked_asset_count": 1376,
            "eligible_history_asset_count": 750,
            "eligible_history_ratio": 750 / 1376,
            "history_sessions": 180,
        },
        "exclusion_counts": {"unclassified_peer_group": 299},
        "candidate_counts": {
            "leader_breakout_proxy_v1": 0,
            "former_leader_repair_proxy_v1": 1,
        },
        "candidates": [
            {
                "candidate_id": "former_leader_repair_proxy_v1",
                "asset_code": "512710",
                "name": "军工龙头ETF富国",
                "score": 0.6481481481481481,
                "baseline_score": 41.212968016154,
                "peer_group": "industrial",
                "components": {"peer_count": 19},
                "feature_hash": _hash("historical-candidate"),
            }
        ],
        "promotion_gate_credit": {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        },
        "limitations": ["research only"],
        "research_only": True,
        "production_mutation_allowed": False,
    }
    await _insert_evidence(
        app,
        report=report,
        experiment_family=LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
    )

    payload = (await client.get(ENDPOINT)).json()

    historical = payload["historical_proxy"]
    assert historical["status"] == "complete"
    assert historical["candidates"][0]["asset_code"] == "512710"
    assert historical["promotion_gate_credit"] == {
        "eligible_pit_sessions": 0,
        "independent_primary_dates": 0,
        "walk_forward_folds": 0,
    }
    assert payload["observation_counts"]["eligible_pit_sessions"] == 0
    assert payload["current_observations"] == []
    assert payload["notification_provenance"] == "none"
    assert payload["execution_provenance"] == "none"


@pytest.mark.anyio
async def test_historical_backtest_is_visible_without_pit_gate_credit(
    app,
    client,
) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    manifest_hash = _hash("historical-backtest-manifest")
    report = {
        "schema_version": LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
        "report_kind": LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
        "experiment_family": LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
        "manifest_hash": manifest_hash,
        "status": "insufficient_data",
        "unavailable_reason": LEADER_HISTORICAL_BACKTEST_NOT_PIT,
        "contract_hash": HISTORICAL_BACKTEST_CONTRACT_HASH,
        "ranking_source_kind": "research_replay",
        "evidence_mode": LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
        "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
        "price_basis": "total_return_adjusted",
        "source_signal_run_id": 121,
        "source_signal_date": "2026-07-31",
        "first_signal_date": "2025-10-01",
        "last_signal_date": "2026-07-01",
        "coverage": {
            "classified_asset_count": 750,
            "history_180_asset_count": 750,
            "eligible_signal_date_count": 180,
            "unique_candidate_signal_count": 42,
        },
        "exclusion_counts": {},
        "aggregates": [
            {
                "candidate_id": "all_leader_candidates",
                "horizon_sessions": 5,
                "event_count": 42,
                "signal_date_count": 30,
                "mean_net_return": 0.01,
                "median_net_return": 0.008,
                "win_rate": 0.57,
                "mean_peer_net_return": 0.004,
                "mean_net_excess_return": 0.006,
                "mean_net_return_ci95_lower": -0.002,
                "mean_net_return_ci95_upper": 0.02,
                "event_series_max_drawdown": -0.08,
            }
        ],
        "promotion_gate_credit": {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        },
        "limitations": ["current-vintage membership bias"],
        "research_only": True,
        "production_mutation_allowed": False,
    }
    await _insert_evidence(
        app,
        report=report,
        experiment_family=LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
    )

    payload = (await client.get(ENDPOINT)).json()

    backtest = payload["historical_backtest"]
    assert backtest["status"] == "complete"
    assert backtest["aggregates"][0]["mean_net_return"] == pytest.approx(0.01)
    assert backtest["promotion_gate_credit"] == {
        "eligible_pit_sessions": 0,
        "independent_primary_dates": 0,
        "walk_forward_folds": 0,
    }
    assert payload["notification_provenance"] == "none"
    assert payload["execution_provenance"] == "none"


@pytest.mark.anyio
async def test_partial_checkpoint_is_visible_without_partial_ranks(
    app,
    client,
) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    async with app.state.db.session() as session:
        session.add(
            EtfFactorExperimentCheckpoint(
                manifest_hash=_hash("partial-checkpoint"),
                code_version="leader-api-test-v1",
                status="partial",
                cached_factor_rows_json={
                    LEADER_CONTINUATION_STATE_KEY: {
                        "phase": "features",
                        "generation": 3,
                        "phase_item_counts": {"features": 48},
                        "coverage": {
                            "observation_input": {"expected": 1490}
                        },
                        "page_profile": {"page_size": 16},
                        "phase_cursor": {"code_after": "510300"},
                        "checkpoint_hash": _hash("partial-state"),
                    }
                },
            )
        )
        await session.commit()

    payload = (await client.get(ENDPOINT)).json()

    assert payload["observation_state"] == "partial"
    assert payload["partial_checkpoint"]["processed_asset_count"] == 48
    assert payload["partial_checkpoint"]["total_asset_count"] == 1490
    assert payload["current_observations"] == []
    assert payload["primary_metrics"] == []


@pytest.mark.anyio
async def test_first_observation_is_visible_without_fabricated_returns(
    app,
    client,
) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    report = _observation_report()
    await _insert_evidence(
        app,
        report=report,
        experiment_family=LEADER_OBSERVATION_EXPERIMENT_FAMILY,
    )

    payload = (await client.get(ENDPOINT)).json()

    assert payload["status"] == "insufficient_data"
    assert payload["observation_state"] == "observing"
    assert payload["observation_counts"]["materialized_pit_sessions"] == 1
    assert payload["current_observations"][0]["asset_code"] == "510300"
    assert payload["primary_metrics"] == []
    assert payload["exploratory_metrics"] == []
    assert payload["notification_provenance"] == "none"
    assert payload["execution_provenance"] == "none"
    assert payload["production_mutation_allowed"] is False


@pytest.mark.anyio
async def test_daily_observation_does_not_mask_final_report(app, client) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    final_report = _valid_report("rejected")
    final_report["coverage"] = {"eligible_point_in_time_sessions": 300}
    await _insert_evidence(app, report=final_report)
    await _insert_evidence(
        app,
        report=_observation_report(match=False),
        experiment_family=LEADER_OBSERVATION_EXPERIMENT_FAMILY,
    )

    payload = (await client.get(ENDPOINT)).json()

    assert payload["status"] == "rejected"
    assert payload["manifest_hash"] == final_report["manifest_hash"]
    assert payload["observation_state"] == "observing"
    assert payload["current_observations"] == []


@pytest.mark.anyio
async def test_maturity_updates_counts_without_exposing_a_primary_metric(
    app,
    client,
) -> None:
    app.state.settings.etf_leader_tactics_evidence_api_enabled = True
    observation = _observation_report()
    await _insert_evidence(
        app,
        report=observation,
        experiment_family=LEADER_OBSERVATION_EXPERIMENT_FAMILY,
    )
    maturity = {
        "schema_version": LEADER_MATURITY_SCHEMA_VERSION,
        "report_kind": LEADER_MATURITY_REPORT_KIND,
        "experiment_family": LEADER_MATURITY_EXPERIMENT_FAMILY,
        "manifest_hash": _hash("maturity-manifest"),
        "observation_manifest_hash": observation["manifest_hash"],
        "observation_hash": _hash("observation-result"),
        "signal_date": "2026-07-31",
        "outcome_cutoff": "2026-08-14T16:00:00+08:00",
        "outcomes": [
            {
                "candidate_id": "leader_breakout_proxy_v1",
                "asset_code": "510300",
                "signal_date": "2026-07-31",
                "horizon_sessions": 5,
                "status": "matured",
            },
            {
                "candidate_id": "leader_breakout_proxy_v1",
                "asset_code": "510300",
                "signal_date": "2026-07-31",
                "horizon_sessions": 10,
                "status": "pending",
            },
        ],
        "matured_outcome_count": 1,
        "pending_outcome_count": 1,
        "unavailable_outcome_count": 0,
        "ranking_source_kind": "research_replay",
        "policy_mode": "policy_shadow",
        "notification_provenance": "none",
        "execution_provenance": "simulated_execution",
        "holdout_consumed": False,
        "research_only": True,
        "production_mutation_allowed": False,
    }
    await _insert_evidence(
        app,
        report=maturity,
        experiment_family=LEADER_MATURITY_EXPERIMENT_FAMILY,
    )

    payload = (await client.get(ENDPOINT)).json()

    assert payload["observation_counts"]["matured_outcome_count"] == 1
    assert payload["observation_counts"]["pending_outcome_count"] == 1
    assert payload["observation_counts"]["outcomes_by_horizon"] == [
        {"horizon_sessions": 5, "pending": 0, "matured": 1, "unavailable": 0},
        {"horizon_sessions": 10, "pending": 1, "matured": 0, "unavailable": 0},
    ]
    assert payload["primary_metrics"] == []


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
