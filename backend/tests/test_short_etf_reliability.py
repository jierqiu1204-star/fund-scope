from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfPriceHistory,
    ShortEtfPaperOrder,
    ShortEtfReliabilityEvaluation,
    ShortEtfSignalRun,
    TradableEtf,
)


async def _seed_etf(
    app,
    code: str,
    *,
    name: str | None = None,
    start: date = date(2026, 1, 1),
    days: int = 90,
    base: float = 1.0,
    daily_step: float = 0.002,
    turnover: float = 120_000_000,
    final_surge: bool = False,
) -> None:
    async with app.state.db.session() as session:
        if await session.scalar(select(TradableEtf).where(TradableEtf.code == code)) is None:
            session.add(
                TradableEtf(
                    code=code,
                    name=name or f"测试ETF{code}",
                    exchange="SZ",
                    theme_tags_json=["科技"],
                    trading_rule_label="T+1股票ETF",
                    asset_class="equity_etf",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
        close = base
        for offset in range(days):
            trade_date = start + timedelta(days=offset)
            step = daily_step
            if final_surge and offset >= days - 5:
                step = daily_step * 10
            previous = close
            close = close + step
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=trade_date,
                    open=previous,
                    high=close * 1.02,
                    low=previous * 0.98,
                    close=close,
                    volume=1_000_000,
                    turnover=turnover,
                    pct_change=(close / previous - 1) * 100 if previous else 0.0,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_short_etf_sync_uses_backup_provider_and_records_health(client, monkeypatch) -> None:
    from app.services.short_etf import data as etf_data

    monkeypatch.setenv("SHORT_ETF_SYNC_DELAY_SECONDS", "0")

    async def primary_fails(code: str, from_date: date, to_date: date):
        raise RuntimeError("AKShare 临时失败")

    async def backup_succeeds(code: str, from_date: date, to_date: date):
        return [
            {
                "date": "2026-01-02",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 1_000_000,
                "turnover": 120_000_000,
                "pct_change": 5.0,
            }
        ]

    monkeypatch.setattr(etf_data, "fetch_akshare_etf_price_history", primary_fails)
    monkeypatch.setattr(etf_data, "fetch_efinance_etf_price_history", backup_succeeds)

    response = await client.post(
        "/api/short-etf/data/sync",
        json={"from_date": "2026-01-01", "to_date": "2026-01-03", "codes": ["159915"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 1
    assert body["failed"] == 0
    assert body["fallback_used"] == 1
    assert body["provider_counts"]["efinance"] == 1

    status = await client.get("/api/short-etf/data-status")
    assert status.status_code == 200
    item = next(row for row in status.json()["items"] if row["code"] == "159915")
    assert item["status"] == "success"
    assert item["provider"] == "efinance"
    assert item["latest_price_date"] == "2026-01-02"
    assert item["consecutive_failures"] == 0
    assert item["last_error_message"] is None


@pytest.mark.asyncio
async def test_short_etf_sync_uses_sina_third_provider_when_primary_and_backup_fail(client, monkeypatch) -> None:
    from app.services.short_etf import data as etf_data

    monkeypatch.setenv("SHORT_ETF_SYNC_DELAY_SECONDS", "0")

    async def primary_fails(code: str, from_date: date, to_date: date):
        raise RuntimeError("AKShare 被断开")

    async def backup_fails(code: str, from_date: date, to_date: date):
        raise RuntimeError("efinance 被断开")

    async def third_provider_succeeds(code: str, from_date: date, to_date: date):
        return [
            {
                "date": "2026-01-02",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.04,
                "volume": 1_000_000,
                "turnover": 120_000_000,
                "pct_change": 4.0,
            }
        ]

    monkeypatch.setattr(etf_data, "fetch_akshare_etf_price_history", primary_fails)
    monkeypatch.setattr(etf_data, "fetch_efinance_etf_price_history", backup_fails)
    monkeypatch.setattr(etf_data, "fetch_sina_etf_price_history", third_provider_succeeds)

    response = await client.post(
        "/api/short-etf/data/sync",
        json={"from_date": "2026-01-01", "to_date": "2026-01-03", "codes": ["159915"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 1
    assert body["failed"] == 0
    assert body["fallback_used"] == 1
    assert body["provider_counts"]["sina"] == 1

    status = await client.get("/api/short-etf/data-status")
    item = next(row for row in status.json()["items"] if row["code"] == "159915")
    assert item["provider"] == "sina"
    assert item["status"] == "success"


@pytest.mark.asyncio
async def test_short_etf_sync_waits_between_etfs_to_reduce_public_source_pressure(client, monkeypatch) -> None:
    from app.services.short_etf import data as etf_data

    delays: list[float] = []
    monkeypatch.setenv("SHORT_ETF_SYNC_DELAY_SECONDS", "0.25")

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    async def primary_succeeds(code: str, from_date: date, to_date: date):
        return [
            {
                "date": "2026-01-02",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.02,
                "volume": 1_000_000,
                "turnover": 120_000_000,
                "pct_change": 2.0,
            }
        ]

    monkeypatch.setattr(etf_data.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(etf_data, "fetch_akshare_etf_price_history", primary_succeeds)

    response = await client.post(
        "/api/short-etf/data/sync",
        json={"from_date": "2026-01-01", "to_date": "2026-01-03", "codes": ["159915", "512480"]},
    )

    assert response.status_code == 200
    assert response.json()["failed"] == 0
    assert delays == [0.25]


@pytest.mark.asyncio
async def test_short_etf_retry_failed_targets_only_failed_or_stale_etfs(client, monkeypatch) -> None:
    from app.services.short_etf import data as etf_data

    monkeypatch.setenv("SHORT_ETF_SYNC_DELAY_SECONDS", "0")
    attempts: list[str] = []

    async def primary_fails(code: str, from_date: date, to_date: date):
        raise RuntimeError(f"{code} 主源失败")

    async def backup_changes_after_first_failure(code: str, from_date: date, to_date: date):
        attempts.append(code)
        if len(attempts) == 1:
            raise RuntimeError(f"{code} 备用源失败")
        return [
            {
                "date": "2026-01-04",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.03,
                "volume": 1_000_000,
                "turnover": 120_000_000,
                "pct_change": 3.0,
            }
        ]

    monkeypatch.setattr(etf_data, "fetch_akshare_etf_price_history", primary_fails)
    monkeypatch.setattr(etf_data, "fetch_efinance_etf_price_history", backup_changes_after_first_failure)

    failed = await client.post(
        "/api/short-etf/data/sync",
        json={"from_date": "2026-01-01", "to_date": "2026-01-04", "codes": ["159915"]},
    )
    retry = await client.post("/api/short-etf/data/retry-failed")

    assert failed.status_code == 200
    assert failed.json()["failed"] == 1
    assert retry.status_code == 200
    assert retry.json()["retried"] == 1
    assert retry.json()["inserted"] == 1
    assert attempts == ["159915", "159915"]


@pytest.mark.asyncio
async def test_short_etf_signals_expand_risk_labels_and_review_language(client, app) -> None:
    await _seed_etf(app, "159915", days=90, daily_step=0.004, final_surge=True)

    signal = await client.post("/api/short-etf/signals/run", json={"as_of_date": "2026-03-31"})

    assert signal.status_code == 200
    item = signal.json()["items"][0]
    assert "连续大涨" in item["risk_flags"]
    assert item["conclusion"] in {"高位观察", "谨慎", "不适合短线"}
    assert not {"buy", "sell", "target_price", "expected_return"} & set(item)

    review = await client.post(f"/api/short-etf/signals/{signal.json()['id']}/review")
    assert review.status_code == 200
    notes = review.json()["items"][0]["agent_notes"]
    assert set(notes) == {"数据员", "趋势员", "风控员", "反方", "总结员"}
    assert "不是购买建议" in notes["总结员"]
    assert "目标价" not in str(review.json())


@pytest.mark.asyncio
async def test_short_etf_reliability_evaluation_is_separate_and_sample_aware(client, app) -> None:
    await _seed_etf(app, "159915", days=90, daily_step=0.004)
    await _seed_etf(app, "512480", days=90, daily_step=0.002)

    async with app.state.db.session() as session:
        signal_runs_before = await session.scalar(select(func.count(ShortEtfSignalRun.id)))
        orders_before = await session.scalar(select(func.count(ShortEtfPaperOrder.id)))

    response = await client.post(
        "/api/short-etf/evaluations",
        json={"start_date": "2026-01-01", "end_date": "2026-03-31", "fee_rate": 0.0005},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["conclusion"] == "样本不足，继续观察"
    assert "样本不足" in body["risk_flags"]
    assert body["items"]
    assert body["summary"]["fee_rate"] == 0.0005
    assert body["summary"]["parameter_stability"] in {"稳定", "不稳定", "样本不足"}

    async with app.state.db.session() as session:
        signal_runs_after = await session.scalar(select(func.count(ShortEtfSignalRun.id)))
        orders_after = await session.scalar(select(func.count(ShortEtfPaperOrder.id)))
        evaluations = await session.scalar(select(func.count(ShortEtfReliabilityEvaluation.id)))

    assert signal_runs_after == signal_runs_before
    assert orders_after == orders_before
    assert evaluations == 1

    listing = await client.get("/api/short-etf/evaluations")
    detail = await client.get(f"/api/short-etf/evaluations/{body['id']}")
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == body["id"]
    assert detail.status_code == 200
    assert detail.json()["id"] == body["id"]
