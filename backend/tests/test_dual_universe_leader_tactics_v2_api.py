from __future__ import annotations

import pytest


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
