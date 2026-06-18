from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfPriceHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TradableEtf,
)
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


async def _seed_cached_etf_signals(app: Any, count: int = 3) -> int:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 6, 12),
            config_json={"asset_type": "etf"},
            summary_json={"item_count": count, "fund_count": 0, "etf_count": count},
        )
        session.add(run)
        await session.flush()
        for index in range(count):
            code = f"5620{index:02d}"
            session.add(
                TradableEtf(
                    code=code,
                    name=f"Cached ETF {index}",
                    exchange="SH",
                    theme_tags_json=["cached"],
                    trading_rule_label="T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            session.add(
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=code,
                    rank=index + 1,
                    total_score=90 - index,
                    conclusion="短线观察",
                    score_breakdown_json={
                        "trend": {"score": 80 - index, "weight": 0.55},
                        "risk": {"score": 70, "weight": 0.30},
                        "liquidity": {"score": 90, "weight": 0.15},
                        "metrics": {"return_20d": 0.02 + index / 100},
                    },
                    risk_flags_json=[],
                    rationale_json={"key_reason": "cached", "risk_explanation": "cached"},
                    metrics_json={
                        "return_5d": 0.01,
                        "return_20d": 0.02 + index / 100,
                        "return_60d": 0.03,
                        "max_drawdown_60d": -0.04,
                        "average_turnover_20d": 100_000_000,
                        "latest_date": "2026-06-12",
                        "latest_value": 1.23 + index,
                        "usable_days": 120,
                        "sample_level": "样本充足",
                        "source_note": "cached signal",
                        "default_display_eligible": True,
                    },
                )
            )
        await session.commit()
        return run.id


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

    async with app.state.db.session() as session:
        await short_research_service.run_signal_generation(session, asset_type="etf")

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

    async with app.state.db.session() as session:
        await short_research_service.run_signal_generation(session, asset_type="etf")

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
async def test_assets_endpoint_uses_cached_signal_items_and_paginates(client, app, monkeypatch) -> None:
    await _seed_cached_etf_signals(app, count=4)

    async def fail_full_recompute(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("assets endpoint should not recompute the full ETF universe")

    monkeypatch.setattr(short_research_service, "list_computed_assets", fail_full_recompute)

    response = await client.get(
        "/api/short-research/assets?asset_type=etf&universe=default&limit=2&offset=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert [item["code"] for item in body["items"]] == ["562001", "562002"]
    assert body["items"][0]["latest_date"] == "2026-06-12"
    assert body["items"][0]["latest_value"] == 2.23
    assert body["items"][0]["usable_days"] == 120


@pytest.mark.asyncio
async def test_observation_portfolio_uses_cached_signals(client, app, monkeypatch) -> None:
    await _seed_cached_etf_signals(app, count=3)

    async def fail_full_recompute(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("observation portfolio should not recompute the full ETF universe")

    monkeypatch.setattr(short_research_service, "list_computed_assets", fail_full_recompute)

    response = await client.get("/api/short-research/observation-portfolio?asset_type=etf&limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["research_only"] is True
    assert len(body["items"]) == 2
    assert [item["code"] for item in body["items"]] == ["562000", "562001"]
    assert abs(sum(item["target_weight"] for item in body["items"]) + body["cash_weight"] - 1) < 0.01


@pytest.mark.asyncio
async def test_status_endpoint_is_lightweight_by_default(client, app, monkeypatch) -> None:
    await _seed_cached_etf_signals(app, count=2)

    async def fail_health_scan(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("status endpoint should not scan full data health by default")

    async def fail_default_recompute(*_args: Any, **_kwargs: Any) -> list[Any]:
        raise AssertionError("status endpoint should not recompute default ETF assets")

    monkeypatch.setattr(short_research_service, "data_health", fail_health_scan)
    monkeypatch.setattr(short_research_service, "list_computed_assets", fail_default_recompute)

    response = await client.get("/api/short-research/status")

    assert response.status_code == 200
    body = response.json()
    assert body["etf_default_display_count"] == 2
    assert body["data_health"] == []


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
                    user_id=1,
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
    assert result["etfs"]["skipped"] == 0
    assert result["asset_count"] == 3


@pytest.mark.asyncio
async def test_dynamic_etf_sync_limits_daily_batches_when_codes_are_not_explicit(app, monkeypatch) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code=f"56100{index}",
                    name=f"批量测试ETF{index}",
                    exchange="SH",
                    theme_tags_json=["批量"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                )
                for index in range(5)
            ]
        )
        await session.commit()

        calls: list[list[str]] = []

        async def fake_etf_sync(
            _session: Any, _from_date: date, _to_date: date, codes: list[str] | None = None
        ) -> dict[str, Any]:
            batch = list(codes or [])
            calls.append(batch)
            return {"etfs": len(batch), "inserted": 0, "updated": 0, "failed": 0, "failures": []}

        monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_BATCH_SIZE", "2")
        monkeypatch.setenv("SHORT_RESEARCH_ETF_SYNC_MAX_BATCHES", "1")
        monkeypatch.setattr(short_research_service, "sync_etf_price_history", fake_etf_sync)

        result = await short_research_service.sync_short_research_data(
            session,
            from_date=date(2026, 6, 1),
            to_date=date(2026, 6, 5),
            asset_type="etf",
        )

    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert result["etfs"]["batches"] == 1
    assert result["etfs"]["batches_total"] >= 3
    assert result["etfs"]["processed"] == 2
    assert result["etfs"]["skipped"] >= 3
