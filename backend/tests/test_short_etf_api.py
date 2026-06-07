from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import EtfPriceHistory, TradableEtf


async def _seed_etf_prices(app, code: str, *, start: date, days: int, base: float, daily_step: float) -> None:
    async with app.state.db.session() as session:
        etf = await session.scalar(select(TradableEtf).where(TradableEtf.code == code))
        if etf is None:
            session.add(
                TradableEtf(
                    code=code,
                    name=f"测试ETF{code}",
                    exchange="SZ",
                    theme_tags_json=["科技"],
                    trading_rule_label="T+1股票ETF",
                    asset_class="equity_etf",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
        for offset in range(days):
            trade_date = start + timedelta(days=offset)
            close = base + offset * daily_step
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=trade_date,
                    open=close * 0.99,
                    high=close * 1.01,
                    low=close * 0.98,
                    close=close,
                    volume=1_000_000 + offset * 1000,
                    turnover=100_000_000 + offset * 1_000_000,
                    pct_change=0.0 if offset == 0 else daily_step / (base + (offset - 1) * daily_step) * 100,
                )
            )
        await session.commit()


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
async def test_short_etf_api_empty_and_not_found_states_are_readable(client) -> None:
    latest = await client.get("/api/short-etf/signals/latest")
    missing_paper = await client.get("/api/short-etf/paper/999")

    assert latest.status_code == 200
    assert latest.json() is None
    assert missing_paper.status_code == 404
    assert "短线 ETF 模拟盘不存在" in missing_paper.json()["detail"]


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

    monkeypatch.setattr(etf_data, "fetch_akshare_etf_price_history", fake_primary_fetch)
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


@pytest.mark.asyncio
async def test_short_etf_signals_use_research_language_and_chase_risk(client, app) -> None:
    await _seed_etf_prices(app, "159915", start=date(2026, 1, 1), days=70, base=1.0, daily_step=0.01)

    response = await client.post("/api/short-etf/signals/run", json={"as_of_date": "2026-03-11"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["items"]
    item = body["items"][0]
    assert item["etf_code"] == "159915"
    assert "追高风险" in item["risk_flags"]
    assert item["conclusion"] in {"高位观察", "谨慎", "不适合短线"}
    assert "追高风险" in item["rationale"]["reason"]
    assert "近60日" in item["rationale"]["reason"]
    assert item["rationale"]["metric_summary"]["return_60d"] > 0
    assert item["rationale"]["metric_summary_text"]
    assert "buy" not in item
    assert "sell" not in item
    assert "target_price" not in item
    assert "expected_return" not in item

    latest = await client.get("/api/short-etf/signals/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_short_etf_review_preserves_signal_rank(client, app) -> None:
    await _seed_etf_prices(app, "159915", start=date(2026, 1, 1), days=70, base=1.0, daily_step=0.003)
    signal = (await client.post("/api/short-etf/signals/run", json={"as_of_date": "2026-03-11"})).json()

    response = await client.post(f"/api/short-etf/signals/{signal['id']}/review")

    assert response.status_code == 200
    review = response.json()
    assert review["status"] == "success"
    assert review["items"][0]["rank"] == signal["items"][0]["rank"]
    assert review["items"][0]["total_score"] == signal["items"][0]["total_score"]
    assert set(review["items"][0]["agent_notes"]) == {"数据员", "趋势员", "风控员", "反方", "总结员"}
    assert "近20日" in review["items"][0]["agent_notes"]["趋势员"]


@pytest.mark.asyncio
async def test_daily_short_etf_signals_job_also_generates_review(client, app) -> None:
    await _seed_etf_prices(app, "159915", start=date(2026, 1, 1), days=70, base=1.0, daily_step=0.003)

    job = await client.post("/api/admin/jobs/daily_short_etf_signals/run")

    assert job.status_code == 200
    details = job.json()
    assert details["status"] == "success"
    assert details["review_id"] > 0
    review = await client.get(f"/api/short-etf/signals/{details['run_id']}/review")
    assert review.status_code == 200
    assert review.json()["run_id"] == details["run_id"]


@pytest.mark.asyncio
async def test_short_etf_paper_portfolio_creates_daily_orders_and_equity_curve(client, app) -> None:
    await _seed_etf_prices(app, "159915", start=date(2026, 1, 1), days=70, base=1.0, daily_step=0.003)
    await client.post("/api/short-etf/signals/run", json={"as_of_date": "2026-03-11"})

    paper_response = await client.post(
        "/api/short-etf/paper/start",
        json={"name": "短线ETF模拟盘", "started_at": "2026-03-10", "initial_cash": 100000},
    )
    assert paper_response.status_code == 200
    paper = paper_response.json()

    run_response = await client.post(f"/api/short-etf/paper/{paper['id']}/run", json={"as_of_date": "2026-03-11"})

    assert run_response.status_code == 200
    updated = run_response.json()
    assert updated["latest_equity"] > 0
    assert updated["orders"]
    assert updated["orders"][0]["side"] == "buy"
    assert updated["equity_curve"]
    assert updated["summary"]["trading_rule_note"]

    listing = await client.get("/api/short-etf/paper")
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == paper["id"]


@pytest.mark.asyncio
async def test_short_etf_paper_only_buys_observable_signals(client, app) -> None:
    await _seed_etf_prices(app, "159915", start=date(2026, 1, 1), days=70, base=1.0, daily_step=0.01)
    await _seed_etf_prices(app, "511010", start=date(2026, 1, 1), days=70, base=1.0, daily_step=0.0001)
    signal = (await client.post("/api/short-etf/signals/run", json={"as_of_date": "2026-03-11"})).json()

    assert signal["items"][0]["etf_code"] == "159915"
    assert signal["items"][0]["conclusion"] == "高位观察"
    assert any(item["etf_code"] == "511010" and item["conclusion"] == "可观察" for item in signal["items"])

    paper_response = await client.post(
        "/api/short-etf/paper/start",
        json={"name": "短线ETF模拟盘", "started_at": "2026-03-11", "initial_cash": 100000},
    )
    run_response = await client.post(f"/api/short-etf/paper/{paper_response.json()['id']}/run", json={"as_of_date": "2026-03-11"})

    assert run_response.status_code == 200
    updated = run_response.json()
    assert updated["orders"]
    assert updated["orders"][0]["etf_code"] == "511010"
    assert all(order["etf_code"] != "159915" for order in updated["orders"])
