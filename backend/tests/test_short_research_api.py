from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest

from app.models.entities import EtfPriceHistory, FundNavHistory
from app.services.short_research.service import allowed_conclusions, ensure_short_research_universe


async def _seed_short_research_history(app) -> None:
    start = date(2026, 1, 27)
    async with app.state.db.session() as session:
        await ensure_short_research_universe(session)
        for offset in range(130):
            current = start + timedelta(days=offset)
            fund_nav = 1.0 + offset * 0.002
            etf_close = 1.0 + offset * 0.012
            session.add(
                FundNavHistory(
                    fund_code="110020",
                    nav_date=current,
                    nav=fund_nav,
                    accumulated_nav=fund_nav,
                )
            )
            session.add(
                EtfPriceHistory(
                    etf_code="512480",
                    trade_date=current,
                    open=etf_close * 0.99,
                    high=etf_close * 1.02,
                    low=etf_close * 0.98,
                    close=etf_close,
                    volume=2_000_000 + offset * 5000,
                    turnover=220_000_000 + offset * 1_000_000,
                    pct_change=0.0 if offset == 0 else 0.012 / (1.0 + (offset - 1) * 0.012) * 100,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_short_research_status_seeds_about_200_assets(client) -> None:
    response = await client.get("/api/short-research/status")

    assert response.status_code == 200
    body = response.json()
    assert body["asset_count"] == 200
    assert body["fund_count"] == 117
    assert body["etf_count"] == 83
    assert len(body["data_health"]) == 200
    assert all("一年持有" not in item["name"] for item in body["data_health"])


@pytest.mark.asyncio
async def test_short_research_signal_generation_is_deterministic_and_research_only(client, app) -> None:
    await _seed_short_research_history(app)

    response = await client.post("/api/short-research/signals/run", json={"as_of_date": "2026-06-05"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["summary"]["item_count"] == 200
    assert body["summary"]["research_only"] is True
    assert body["items"]
    assert {item["conclusion"] for item in body["items"]}.issubset(allowed_conclusions())
    assert any(item["code"] == "110020" and item["asset_type"] == "fund" for item in body["items"])
    hot_etf = next(item for item in body["items"] if item["code"] == "512480")
    assert hot_etf["conclusion"] in {"高位观察", "谨慎观察", "短线观察"}
    assert "key_reason" in hot_etf["rationale"]
    assert hot_etf["rationale"]["research_only"] is True
    assert hot_etf["rationale"]["no_trade_instruction"] is True

    payload_text = json.dumps(body, ensure_ascii=False).lower()
    for forbidden in ["buy", "sell", "stop_loss", "take_profit", "target_price", "expected_return", "guaranteed_profit"]:
        assert forbidden not in payload_text

    latest = await client.get("/api/short-research/signals/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]

    fund_only = await client.post(
        "/api/short-research/signals/run",
        json={"as_of_date": "2026-06-05", "asset_type": "fund"},
    )
    assert fund_only.status_code == 200
    fund_body = fund_only.json()
    assert fund_body["summary"]["fund_count"] == fund_body["summary"]["item_count"]
    assert fund_body["summary"]["etf_count"] == 0
    assert all(item["asset_type"] == "fund" for item in fund_body["items"])

    latest_fund = await client.get("/api/short-research/signals/latest?asset_type=fund")
    assert latest_fund.status_code == 200
    assert latest_fund.json()["id"] == fund_body["id"]


@pytest.mark.asyncio
async def test_short_research_asset_detail_returns_charts_and_beginner_explanations(client, app) -> None:
    await _seed_short_research_history(app)

    response = await client.get("/api/short-research/assets/fund/110020")

    assert response.status_code == 200
    body = response.json()
    assert body["asset"]["code"] == "110020"
    assert body["asset"]["asset_type"] == "fund"
    assert body["chart"]
    assert body["return_windows"]["return_20d"] is not None
    assert set(body["explanation_sections"]) == {"投资方向", "为什么上榜", "主要风险", "反方提醒", "数据说明"}
    assert "公开基金净值数据" in body["explanation_sections"]["数据说明"]


@pytest.mark.asyncio
async def test_short_research_filters_sort_and_data_sync_endpoint(client, app, monkeypatch) -> None:
    await _seed_short_research_history(app)

    filtered = await client.get("/api/short-research/assets?asset_type=etf&theme=半导体&sort=return_20d")

    assert filtered.status_code == 200
    items = filtered.json()["items"]
    assert items
    assert all(item["asset_type"] == "etf" for item in items)
    assert any(item["code"] == "512480" for item in items)

    from app.services.short_research import service as short_research_service

    async def fake_fund_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"funds": 0, "rows_inserted": 0, "rows_updated": 0, "failed": 0, "failures": []}

    async def fake_etf_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"etfs": 1, "inserted": 3, "updated": 0, "failed": 0, "failures": []}

    monkeypatch.setattr(short_research_service, "sync_fund_nav_history", fake_fund_sync)
    monkeypatch.setattr(short_research_service, "sync_etf_price_history", fake_etf_sync)

    sync = await client.post(
        "/api/short-research/data/sync",
        json={"from_date": "2026-06-01", "to_date": "2026-06-05", "asset_type": "etf", "codes": ["512480"]},
    )

    assert sync.status_code == 200
    body = sync.json()
    assert body["asset_count"] == 1
    assert body["etfs"]["inserted"] == 3
    assert body["failed"] == 0
