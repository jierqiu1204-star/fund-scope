from __future__ import annotations

import pytest

from app.api.routes.dual_universe_leader_tactics_v2 import (
    _candidate_classification_projection,
    _classification_readiness_projection,
)


@pytest.mark.asyncio
async def test_v2_api_is_disabled_without_reading_or_mutating_research_state(client) -> None:
    response = await client.get("/api/short-research/leader-tactics-v2/candidates")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["unavailable_reason"] == "leader_tactics_v2_api_disabled"
    assert payload["research_only"] is True
    assert payload["production_mutation_allowed"] is False


@pytest.mark.asyncio
async def test_v2_contract_is_readable_when_candidate_materialization_is_disabled(client) -> None:
    response = await client.get("/api/short-research/leader-tactics-v2/contract")
    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate_ids"] == [
        "leader_breakout_proxy_v2",
        "base_launch_proxy_v2",
        "former_leader_repair_proxy_v2",
        "low_base_catchup_proxy_v1",
    ]
    assert payload["research_only"] is True


@pytest.mark.asyncio
async def test_enabled_v2_api_reports_disabled_etf_materialization(app, client) -> None:
    app.state.settings.etf_leader_tactics_v2_api_enabled = True
    app.state.settings.etf_leader_tactics_v2_etf_materialize_enabled = False

    response = await client.get(
        "/api/short-research/leader-tactics-v2/candidates",
        params={"universe": "etf"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["unavailable_reason"] == (
        "leader_tactics_v2_etf_materialization_disabled"
    )
    assert payload["research_only"] is True
    assert payload["production_mutation_allowed"] is False


@pytest.mark.asyncio
async def test_v2_readiness_is_disabled_without_materialization(client) -> None:
    response = await client.get("/api/short-research/leader-tactics-v2/readiness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["research_only"] is True
    assert payload["unavailable_reason"] == "leader_tactics_v2_api_disabled"


@pytest.mark.asyncio
async def test_v2_summary_is_disabled_without_materialization(client) -> None:
    response = await client.get("/api/short-research/leader-tactics-v2/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["availability"] == "unavailable"
    assert payload["unavailable_reason"] == "leader_tactics_v2_api_disabled"
    assert payload["economic_evidence"]["unavailable_reason"] == (
        "economic_validation_not_materialized"
    )
    assert payload["notification_provenance"] == "none"
    assert payload["execution_provenance"] == "none"
    assert payload["classification_readiness"] == {
        "status": "not_applicable",
        "unavailable_reasons": [],
    }


@pytest.mark.asyncio
async def test_ashare_unavailable_payload_exposes_stable_classification_reason(
    client,
) -> None:
    response = await client.get(
        "/api/short-research/leader-tactics-v2/candidates",
        params={"universe": "ashare"},
    )

    assert response.status_code == 200
    readiness = response.json()["summary"]["classification_readiness"]
    assert readiness["status"] == "unavailable"
    assert readiness["unavailable_reasons"] == ["classification_graph_not_materialized"]


def test_candidate_classification_projection_keeps_selected_and_alternative_contexts() -> None:
    projection = _candidate_classification_projection(
        {
            "universe": "ashare",
            "gate_facts": {
                "classification_graph": {
                    "status": "available",
                    "industry_path": {
                        "taxonomy": "sw_2021",
                        "level_1": {"code": "270000", "label": "电子"},
                        "level_3": {"code": "270500", "label": "被动元件"},
                        "fact_hash": "industry-fact",
                    },
                    "selected_context": {
                        "context_key": "passive_components",
                        "display_label": "被动元件/MLCC",
                        "relation_kind": "industry_union_proxy",
                        "peer_count": 8,
                    },
                    "alternative_memberships": [
                        {
                            "context_key": "sw3:270500",
                            "display_label": "被动元件",
                            "relation_kind": "industry_l3",
                            "peer_count": 8,
                        }
                    ],
                    "theme_state": {
                        "status": "available",
                        "state_hash": "state-hash",
                        "eligible_member_count": 8,
                    },
                    "provider_health": {"tickflow": "healthy"},
                }
            },
            "provenance": {},
            "exclusion_reasons": [],
        }
    )

    assert projection["classification_status"] == "available"
    assert projection["industry_path"]["taxonomy"] == "sw_2021"
    assert projection["selected_context"]["context_key"] == "passive_components"
    assert projection["alternative_contexts"][0]["relation_kind"] == "industry_l3"
    assert projection["theme_state"]["state_hash"] == "state-hash"
    assert projection["classification_provider_health"] == {"tickflow": "healthy"}
    assert projection["classification_unavailable_reasons"] == []


def test_classification_projection_normalizes_stable_unavailable_reasons() -> None:
    projection = _candidate_classification_projection(
        {
            "universe": "ashare",
            "classification_graph": {
                "status": "unavailable",
                "unavailable_reasons": [
                    "partial_capture",
                    "stale_snapshot",
                    "insufficient_peers",
                    "missing_state",
                    "taxonomy_incompatible",
                    "provider_specific_noise",
                ],
            },
            "gate_facts": {},
            "provenance": {},
            "exclusion_reasons": [],
        }
    )

    assert projection["classification_unavailable_reasons"] == [
        "incompatible_theme_taxonomy",
        "theme_capture_partial",
        "theme_peer_count_insufficient",
        "theme_snapshot_stale",
        "theme_state_unavailable",
    ]
    assert _classification_readiness_projection(None, universe="etf") == {
        "status": "not_applicable",
        "unavailable_reasons": [],
    }
    readiness = _classification_readiness_projection(
        {
            "status": "available",
            "authoritative_universe_count": 5540,
            "industry_level_3_count": 4225,
            "coverage": {"industry_level_3": 0.7626},
            "provider_health": {"tickflow": "healthy"},
            "unavailable_reasons": [],
        },
        universe="ashare",
    )
    assert readiness["coverage"] == {"industry_level_3": 0.7626}
    assert readiness["provider_health"] == {"tickflow": "healthy"}
