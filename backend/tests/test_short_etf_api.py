from __future__ import annotations

from datetime import date

import pytest


@pytest.mark.asyncio
async def test_short_etf_universe_excludes_holding_period_funds(client) -> None:
    response = await client.get("/api/short-etf/universe")

    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert any(item["code"] == "159915" for item in items)
    assert all("一年持有" not in item["name"] for item in items)
    assert all(item["is_short_term_eligible"] for item in items)


@pytest.mark.asyncio
async def test_short_etf_data_sync_is_idempotent_and_reports_failures(client, monkeypatch) -> None:
    from app.services.short_etf import data as etf_data

    monkeypatch.setenv("SHORT_ETF_SYNC_DELAY_SECONDS", "0")

    async def fake_primary_fetch(code: str, from_date: date, to_date: date):
        if code == "512480":
            raise RuntimeError("测试数据源失败")
        return [
            {
                "date": "2026-01-01",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 1000,
                "turnover": 100_000_000,
                "pct_change": 5.0,
            }
        ]

    async def fake_backup_fetch(code: str, from_date: date, to_date: date):
        raise RuntimeError("备用数据源失败")

    async def fake_third_fetch(code: str, from_date: date, to_date: date):
        raise RuntimeError("第三数据源失败")

    monkeypatch.setattr(etf_data, "fetch_tickflow_etf_price_history", fake_primary_fetch)
    monkeypatch.setattr(etf_data, "fetch_eastmoney_etf_price_history", fake_backup_fetch)
    monkeypatch.setattr(etf_data, "fetch_efinance_etf_price_history", fake_backup_fetch)
    monkeypatch.setattr(etf_data, "fetch_sina_etf_price_history", fake_third_fetch)

    first = await client.post(
        "/api/short-etf/data/sync",
        json={"from_date": "2026-01-01", "to_date": "2026-01-01", "codes": ["159915", "512480"]},
    )
    second = await client.post(
        "/api/short-etf/data/sync",
        json={"from_date": "2026-01-01", "to_date": "2026-01-01", "codes": ["159915", "512480"]},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["inserted"] == 1
    assert second.json()["inserted"] == 0
    assert first.json()["failed"] == 1
    assert first.json()["failures"][0]["code"] == "512480"
