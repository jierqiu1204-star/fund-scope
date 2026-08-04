from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_dual_universe_leader_tactics_v2_storage import _seed_read_db


@pytest.mark.asyncio
async def test_enabled_v2_api_paginates_one_manifest_and_keeps_summary_stable(app, client) -> None:
    await _seed_read_db(app.state.db.engine)
    app.state.settings.etf_leader_tactics_v2_api_enabled = True

    first_response = await client.get(
        "/api/short-research/leader-tactics-v2/candidates",
        params={"limit": 2},
    )
    assert first_response.status_code == 200
    first = first_response.json()
    assert [row["asset_code"] for row in first["candidates"]] == ["000003", "000005"]

    second_response = await client.get(
        "/api/short-research/leader-tactics-v2/candidates",
        params={"limit": 2, "cursor": first["next_cursor"]},
    )
    assert second_response.status_code == 200
    second = second_response.json()
    assert [row["asset_code"] for row in second["candidates"]] == ["000006", "000001"]
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["summary"] == second["summary"]

    summary_response = await client.get(
        "/api/short-research/leader-tactics-v2/summary",
    )
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["manifest"]["manifest_hash"] == first["manifest_hash"]
    assert summary["candidate_counts"] == {
        "total": 6,
        "available": 5,
        "qualifying": 4,
        "by_formula": {
            "base_launch_proxy_v2": 2,
            "former_leader_repair_proxy_v2": 2,
            "leader_breakout_proxy_v2": 2,
        },
        "by_state": {"preparing": 6},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["provider_health", "status"])
async def test_enabled_v2_api_hides_corrupt_latest_manifest(app, client, corruption: str) -> None:
    manifest_ids = await _seed_read_db(app.state.db.engine)
    app.state.settings.etf_leader_tactics_v2_api_enabled = True
    async with app.state.db.engine.begin() as connection:
        await connection.execute(
            text(
                """
                UPDATE leader_tactics_v2_run_manifests
                SET provider_health_json = CASE
                        WHEN :corruption = :provider_kind THEN :provider_health
                        ELSE provider_health_json
                    END,
                    status = CASE
                        WHEN :corruption = :status_kind THEN :status
                        ELSE status
                    END
                WHERE manifest_hash = :manifest_hash
                """
            ),
            {
                "manifest_hash": manifest_ids["new"],
                "corruption": corruption,
                "provider_kind": "provider_health",
                "status_kind": "status",
                "status": "tampered",
                "provider_health": '{"eastmoney":"tampered"}',
            },
        )

    candidates_response = await client.get("/api/short-research/leader-tactics-v2/candidates")
    summary_response = await client.get("/api/short-research/leader-tactics-v2/summary")

    assert candidates_response.status_code == 200
    assert candidates_response.json()["summary"]["unavailable_reason"] == (
        "leader_tactics_v2_not_materialized"
    )
    assert summary_response.status_code == 200
    assert summary_response.json()["unavailable_reason"] == ("leader_tactics_v2_not_materialized")
