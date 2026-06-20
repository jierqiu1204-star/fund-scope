from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest

from app.models.entities import (
    EtfPriceHistory,
    FundNavHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
)
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


async def _seed_entry_timing_etf(
    app,
    *,
    code: str,
    closes: list[float],
    turnovers: list[float] | None = None,
    latest_date: date = date(2026, 6, 5),
) -> None:
    start = latest_date - timedelta(days=len(closes) - 1)
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name=f"买点测试ETF{code}",
                exchange="SH",
                theme_tags_json=["买点测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        for offset, close in enumerate(closes):
            previous = closes[offset - 1] if offset > 0 else close
            turnover = turnovers[offset] if turnovers is not None else 120_000_000
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=offset),
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=2_000_000,
                    turnover=turnover,
                    pct_change=0.0 if offset == 0 else (close / previous - 1.0) * 100,
                )
            )
        await session.commit()


def _steady_uptrend(days: int = 80, *, start: float = 1.0, step: float = 0.01) -> list[float]:
    return [round(start + offset * step, 6) for offset in range(days)]


async def _seed_observation_portfolio_signal_run(
    app,
    *,
    run_as_of_date: date = date(2026, 6, 15),
    items: list[dict[str, Any]],
) -> None:
    async with app.state.db.session() as session:
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=run_as_of_date,
            config_json={"asset_type": "etf", "language": "research_only"},
            summary_json={"item_count": len(items)},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)

        seen_codes: set[str] = set()
        for item in items:
            code = item["code"]
            if code in seen_codes:
                continue
            seen_codes.add(code)
            session.add(
                TradableEtf(
                    code=code,
                    name=f"观察组合测试{code}",
                    exchange="SH",
                    theme_tags_json=item.get("theme_tags", ["观察组合测试"]),
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )

        await session.commit()

        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=item["code"],
                    rank=index + 1,
                    total_score=float(item["total_score"]),
                    conclusion=item["conclusion"],
                    score_breakdown_json={},
                    risk_flags_json=item.get("risk_flags", []),
                    rationale_json={"entry_timing_reason": item["entry_timing_reason"]},
                    metrics_json={
                        "entry_timing_label": item["entry_timing_label"],
                        "entry_timing_reason": item["entry_timing_reason"],
                        "return_20d": 0.09,
                        "average_turnover_20d": 150_000_000,
                        "max_drawdown_60d": item.get("max_drawdown_60d", -0.04),
                        "volatility_20d": item.get("volatility_20d", 0.015),
                        "default_display_eligible": item.get("default_display_eligible", True),
                    },
                )
                for index, item in enumerate(items)
            ]
        )
        await session.commit()


async def _seed_observation_price_series(
    app,
    *,
    code: str,
    start_price: float = 1.0,
    daily_return: float = 0.002,
    days: int = 70,
    latest_date: date = date(2026, 6, 15),
) -> None:
    start = latest_date - timedelta(days=days - 1)
    async with app.state.db.session() as session:
        close = start_price
        for offset in range(days):
            period_return = 0.0 if offset == 0 else daily_return + (0.001 if offset % 2 else -0.001)
            close = close if offset == 0 else close * (1 + period_return)
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=offset),
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=2_000_000,
                    turnover=160_000_000,
                    pct_change=period_return * 100,
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
    assert body["data_health"] == []

    detailed = await client.get("/api/short-research/status?include_health=true")
    assert detailed.status_code == 200
    detailed_body = detailed.json()
    assert len(detailed_body["data_health"]) == 200
    assert all("一年持有" not in item["name"] for item in detailed_body["data_health"])


@pytest.mark.asyncio
async def test_short_research_signal_generation_is_deterministic_and_research_only(client, app) -> None:
    await _seed_short_research_history(app)

    response = await client.post("/api/short-research/signals/run", json={"as_of_date": "2026-06-05"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["summary"]["item_count"] == 200
    assert body["summary"]["research_only"] is True
    assert body["summary"]["experiment"]["portfolio_single_weight_cap"] == 0.3
    assert body["summary"]["label_validation"]["rule_version"] == "label_validation_v1"
    assert body["summary"]["label_validation"]["groups"]
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
async def test_short_research_entry_timing_labels_are_explained(client, app) -> None:
    healthy = _steady_uptrend()
    healthy[-1] = round(healthy[-2] * 0.992, 6)

    chasing = _steady_uptrend(step=0.012)
    chasing[-1] = round(chasing[-2] * 1.05, 6)

    broken = _steady_uptrend()
    broken[-1] = round(broken[-10] * 1.005, 6)

    weak_volume = _steady_uptrend()
    weak_volume[-1] = round(weak_volume[-20] * 0.97, 6)
    weak_turnovers = [100_000_000 for _item in weak_volume]
    weak_turnovers[-1] = 260_000_000

    stale = _steady_uptrend()

    await _seed_entry_timing_etf(app, code="560810", closes=healthy)
    await _seed_entry_timing_etf(app, code="560811", closes=chasing)
    await _seed_entry_timing_etf(app, code="560812", closes=broken)
    await _seed_entry_timing_etf(app, code="560813", closes=weak_volume, turnovers=weak_turnovers)
    await _seed_entry_timing_etf(app, code="560814", closes=stale, latest_date=date(2026, 5, 20))

    response = await client.post(
        "/api/short-research/signals/run",
        json={
            "as_of_date": "2026-06-05",
            "asset_type": "etf",
            "codes": ["560810", "560811", "560812", "560813", "560814"],
        },
    )

    assert response.status_code == 200
    items = {item["code"]: item for item in response.json()["items"]}
    assert items["560810"]["entry_timing_label"] == "健康回踩"
    assert items["560811"]["entry_timing_label"] == "冲高别追"
    assert items["560812"]["entry_timing_label"] == "跌破等待"
    assert items["560813"]["entry_timing_label"] == "放量转弱"
    assert items["560814"]["entry_timing_label"] == "数据不足"

    healthy_item = items["560810"]
    assert "今天" in healthy_item["entry_timing_reason"]
    assert "10日线" in healthy_item["entry_timing_reason"]
    assert healthy_item["metrics"]["entry_timing_label"] == "健康回踩"
    assert healthy_item["rationale"]["entry_timing_reason"] == healthy_item["entry_timing_reason"]
    assert healthy_item["metrics"]["today_return_pct"] < 0
    assert healthy_item["metrics"]["ma5"] is not None
    assert healthy_item["metrics"]["ma10"] is not None
    assert healthy_item["metrics"]["ma20"] is not None
    assert healthy_item["metrics"]["pullback_from_5d_high_pct"] <= 0


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
    assert set(body["explanation_sections"]) == {"投资方向", "为什么上榜", "今日买点", "主要风险", "反方提醒", "数据说明"}
    assert "公开基金净值数据" in body["explanation_sections"]["数据说明"]


@pytest.mark.asyncio
async def test_short_research_filters_sort_and_data_sync_endpoint(client, app, monkeypatch) -> None:
    await _seed_short_research_history(app)
    signal = await client.post(
        "/api/short-research/signals/run",
        json={"as_of_date": "2026-06-05", "asset_type": "etf"},
    )
    assert signal.status_code == 200

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


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_filters_out_high_watch_and_bad_timing(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "560901",
                "total_score": 94.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "今日回踩 5 日线附近，短线结构可再观察。",
            },
            {
                "code": "560902",
                "total_score": 99.0,
                "conclusion": "高位观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "今日买点标签示例。",
            },
            {
                "code": "560903",
                "total_score": 98.0,
                "conclusion": "短线观察",
                "entry_timing_label": "冲高别追",
                "entry_timing_reason": "今日短线已转弱，等待修复。",
            },
            {
                "code": "560904",
                "total_score": 97.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "趋势延续，仍可观察。",
            },
            {
                "code": "560905",
                "total_score": 96.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "短线指标偏强。",
                "risk_flags": ["追高风险", "流动性不足"],
            },
            {
                "code": "560906",
                "total_score": 95.0,
                "conclusion": "短线观察",
                "entry_timing_label": "数据不足",
                "entry_timing_reason": "今日数据缺口。",
            },
        ],
    )

    response = await client.get("/api/short-research/observation-portfolio?limit=3")
    assert response.status_code == 200

    body = response.json()
    assert body["asset_type"] == "etf"
    assert body["cash_weight"] < 1.0
    assert body["single_weight_cap"] == 0.3
    assert body["total_exposure_cap"] == 0.6
    assert body["constraint_summary"]["single_weight_cap"] == 0.3
    assert body["methodology"]
    assert len(body["items"]) == 2
    assert [item["code"] for item in body["items"]] == ["560904", "560901"]
    assert all(item["code"] not in {"560902", "560903", "560905", "560906"} for item in body["items"])
    assert {item["code"] for item in body["watch_only_items"]} >= {"560902", "560903"}
    assert {item["code"] for item in body["excluded_items"]} >= {"560905", "560906"}
    assert "今日买点：趋势延续" in body["items"][0]["risk_reasons"]
    assert any("买点原因：" in item for item in body["items"][0]["risk_reasons"])
    assert (
        "高位/追高/数据不足资产会分到观察或等待分组，不进入主组合权重。"
        in (body["note"] or "")
    )


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_no_match_returns_full_cash(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "560907",
                "total_score": 99.0,
                "conclusion": "高位观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "高位观察示例。",
            },
            {
                "code": "560908",
                "total_score": 98.0,
                "conclusion": "短线观察",
                "entry_timing_label": "冲高别追",
                "entry_timing_reason": "短线偏热，不适合再买。",
            },
            {
                "code": "560909",
                "total_score": 97.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "偏强。",
                "risk_flags": ["追高风险"],
            },
        ],
    )

    response = await client.get("/api/short-research/observation-portfolio?limit=3")
    assert response.status_code == 200

    body = response.json()
    assert body["items"] == []
    assert body["watch_only_items"]
    assert body["excluded_items"] == []
    assert body["cash_weight"] == 1.0
    assert (
        body["note"]
        == "当前没有同时满足短线观察和健康买点的 ETF，强势但高位的只适合继续观察，不给组合权重。"
    )


@pytest.mark.asyncio
async def test_short_research_observation_portfolio_reduces_theme_and_correlation_overlap(client, app) -> None:
    await _seed_observation_portfolio_signal_run(
        app,
        items=[
            {
                "code": "561001",
                "total_score": 99.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "科技方向健康回踩。",
                "theme_tags": ["科技"],
            },
            {
                "code": "561002",
                "total_score": 98.0,
                "conclusion": "短线观察",
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "同方向健康回踩。",
                "theme_tags": ["科技"],
            },
            {
                "code": "561003",
                "total_score": 97.0,
                "conclusion": "短线观察",
                "entry_timing_label": "趋势延续",
                "entry_timing_reason": "红利方向趋势延续。",
                "theme_tags": ["红利"],
            },
        ],
    )
    await _seed_observation_price_series(app, code="561001", daily_return=0.002)
    await _seed_observation_price_series(app, code="561002", daily_return=0.002)
    await _seed_observation_price_series(app, code="561003", daily_return=0.001)

    response = await client.get("/api/short-research/observation-portfolio?limit=3")
    assert response.status_code == 200

    body = response.json()
    item_codes = {item["code"] for item in body["items"]}
    watch_codes = {item["code"] for item in body["watch_only_items"]}
    assert "561001" in item_codes
    assert "561002" not in item_codes
    assert "561002" in watch_codes
    assert any("相关性" in "；".join(item["risk_reasons"]) for item in body["watch_only_items"])
    assert all(item["target_weight"] <= 0.3 for item in body["items"])
    assert body["cash_weight"] >= 0.4
