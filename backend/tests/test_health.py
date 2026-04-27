from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_endpoint_reports_app_and_db_status(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"app": "ok", "db": "ok"}
