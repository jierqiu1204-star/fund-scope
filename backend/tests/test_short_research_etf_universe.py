from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models.entities import EtfPriceHistory, TrackedPosition, TradableEtf
from app.services.short_research import service as short_research_service
from app.services.short_research.universe import EtfUniverseRecord, refresh_etf_universe


async def _seed_etf_history(
    app: Any,
    *,
    code: str,
    name: str,
    latest: date = date(2026, 6, 5),
    days: int = 90,
    turnover: float = 120_000_000,
    daily_return: float = 0.002,
    theme_tags: list[str] | None = None,
) -> None:
    async with app.state.db.session() as session:
        existing = await session.scalar(select(TradableEtf).where(TradableEtf.code == code))
        if existing is None:
            session.add(
                TradableEtf(
                    code=code,
                    name=name,
                    exchange="SH" if code.startswith("5") else "SZ",
                    theme_tags_json=theme_tags or ["测试主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
        start = latest - timedelta(days=days - 1)
        for offset in range(days):
            current = start + timedelta(days=offset)
            close = 1.0 * (1 + daily_return) ** offset
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=current,
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=turnover / close,
                    turnover=turnover,
                    pct_change=0.0 if offset == 0 else daily_return * 100,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_etf_universe_refresh_is_idempotent_excludes_unsuitable_and_preserves_manual_theme(app) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="588000",
                name="旧名称科创ETF",
                exchange="SH",
                theme_tags_json=["人工维护主题"],
                trading_rule_label="旧规则",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        await session.commit()

        records = [
            EtfUniverseRecord(
                code="588000",
                name="科创50ETF",
                exchange="SH",
                category="broad",
                theme_tags=["科创"],
                trading_rule_label="证券账户 T+1 ETF",
                source="pytest",
            ),
            EtfUniverseRecord(
                code="511990",
                name="华宝添益货币ETF",
                exchange="SH",
                category="money",
                theme_tags=["货币"],
                trading_rule_label="货币 ETF",
                source="pytest",
            ),
        ]

        first = await refresh_etf_universe(session, records=records)
        second = await refresh_etf_universe(session, records=records)

        assert first["discovered"] == 2
        assert first["excluded"] == 1
        assert second["inserted"] == 0
        assert await session.scalar(select(func.count()).select_from(TradableEtf).where(TradableEtf.code == "588000")) == 1

        kept = await session.scalar(select(TradableEtf).where(TradableEtf.code == "588000"))
        assert kept is not None
        assert kept.name == "科创50ETF"
        assert kept.theme_tags_json == ["人工维护主题"]
        assert kept.is_short_term_eligible is True

        money = await session.scalar(select(TradableEtf).where(TradableEtf.code == "511990"))
        assert money is not None
        assert money.is_short_term_eligible is False


@pytest.mark.asyncio
async def test_short_research_etf_status_universe_filter_and_dynamic_detail(client, app) -> None:
    await _seed_etf_history(app, code="560001", name="动态科技ETF", turnover=150_000_000)
    await _seed_etf_history(app, code="560002", name="低流动ETF", turnover=3_000_000)

    status = await client.get("/api/short-research/status")
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["etf_total_count"] >= 2
    assert status_body["etf_eligible_count"] >= 2
    assert status_body["etf_default_display_count"] >= 1
    assert "etf_data_stale_count" in status_body
    assert "etf_failed_count" in status_body

    default_response = await client.get("/api/short-research/assets?asset_type=etf&universe=default")
    assert default_response.status_code == 200
    default_items = default_response.json()["items"]
    assert any(item["code"] == "560001" for item in default_items)
    assert all(item["code"] != "560002" for item in default_items)
    included = next(item for item in default_items if item["code"] == "560001")
    assert included["metrics"]["default_display_eligible"] is True
    assert included["metrics"]["data_quality_score"] > 0

    all_response = await client.get("/api/short-research/assets?asset_type=etf&universe=all")
    assert all_response.status_code == 200
    all_items = all_response.json()["items"]
    low_liquidity = next(item for item in all_items if item["code"] == "560002")
    assert low_liquidity["metrics"]["default_display_eligible"] is False
    assert any("成交额" in reason or "流动性" in reason for reason in low_liquidity["metrics"]["default_exclusion_reasons"])

    detail = await client.get("/api/short-research/assets/etf/560001")
    assert detail.status_code == 200
    assert detail.json()["asset"]["name"] == "动态科技ETF"


@pytest.mark.asyncio
async def test_etf_observation_portfolio_is_research_only_and_balances_cash(client, app) -> None:
    await _seed_etf_history(app, code="560101", name="观察科技ETF", turnover=200_000_000, daily_return=0.003)
    await _seed_etf_history(app, code="560102", name="观察红利ETF", turnover=180_000_000, daily_return=0.0015)

    response = await client.get("/api/short-research/observation-portfolio?asset_type=etf&limit=3")

    assert response.status_code == 200
    body = response.json()
    assert body["research_only"] is True
    assert body["no_trade_instruction"] is True
    assert body["items"]
    assert 0 <= body["cash_weight"] <= 1
    assert abs(sum(item["target_weight"] for item in body["items"]) + body["cash_weight"] - 1) < 0.01
    assert all(item["evidence"] for item in body["items"])

    payload = json.dumps(body, ensure_ascii=False).lower()
    for forbidden in ["buy", "sell", "target_price", "expected_return", "guaranteed_profit"]:
        assert forbidden not in payload


@pytest.mark.asyncio
async def test_dynamic_etf_sync_batches_and_prioritizes_tracked_etfs(app, monkeypatch) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="560201",
                    name="普通主题ETF",
                    exchange="SH",
                    theme_tags_json=["主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                ),
                TradableEtf(
                    code="560202",
                    name="默认关注ETF",
                    exchange="SH",
                    theme_tags_json=["主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
                TradableEtf(
                    code="560203",
                    name="已追踪ETF",
                    exchange="SH",
                    theme_tags_json=["主题"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                ),
                TrackedPosition(
                    asset_type="etf",
                    asset_code="560203",
                    asset_name="已追踪ETF",
                    buy_date=date(2026, 6, 1),
                    buy_amount=3000,
                    status="active",
                ),
            ]
        )
        await session.commit()

        calls: list[list[str]] = []

        async def fake_etf_sync(_session: Any, _from_date: date, _to_date: date, codes: list[str] | None = None) -> dict[str, Any]:
            batch = list(codes or [])
            calls.append(batch)
            return {"etfs": len(batch), "inserted": 0, "updated": 0, "failed": 0, "failures": []}

        monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", "2")
        monkeypatch.setattr(short_research_service, "sync_etf_price_history", fake_etf_sync)

        result = await short_research_service.sync_short_research_data(
            session,
            from_date=date(2026, 6, 1),
            to_date=date(2026, 6, 5),
            asset_type="etf",
            codes=["560201", "560202", "560203"],
        )

    assert calls[0][0] == "560203"
    assert calls[0][1] == "560202"
    assert calls[1] == ["560201"]
    assert result["etfs"]["batches"] == 2
    assert result["asset_count"] == 3
