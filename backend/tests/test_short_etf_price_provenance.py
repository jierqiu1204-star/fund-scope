from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest
from sqlalchemy import select

from app.models.entities import EtfDataHealth, EtfPriceHistory, TradableEtf
from app.services.short_etf import data


@pytest.mark.asyncio
async def test_eastmoney_history_uses_required_headers_and_total_return_provenance(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        close = "1.0" if request.url.params["fqt"] == "0" else "2.0"
        return httpx.Response(
            200,
            json={
                "data": {
                    "klines": [
                        f"2026-07-10,{close},{close},{close},{close},100,200,0,0,0,0"
                    ]
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(data.httpx, "AsyncClient", client_factory)

    rows = await data.fetch_eastmoney_etf_price_history(
        "159605", date(2026, 7, 10), date(2026, 7, 10)
    )

    assert [request.url.params["fqt"] for request in requests] == ["0", "2"]
    assert [request.url.params["invt"] for request in requests] == ["2", "2"]
    assert all(request.headers["referer"] == "https://quote.eastmoney.com/" for request in requests)
    assert all(request.headers["accept"] == "application/json,text/plain,*/*" for request in requests)
    assert all(request.headers["connection"] == "close" for request in requests)
    assert rows[0]["close"] == 1.0
    assert rows[0]["research_adjusted_value"] == 2.0
    assert rows[0]["research_price_basis"] == data.TOTAL_RETURN_PRICE_BASIS
    assert rows[0]["adjustment_version"] == "eastmoney.push2his.kline.hfq_v1"
    assert rows[0]["provider_version"] == "eastmoney.push2his.kline.hfq_v1"

@pytest.mark.asyncio
async def test_sync_records_primary_source_failure_for_raw_fallback(app, monkeypatch) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="159605",
                name="新能源ETF",
                exchange="SZ",
                theme_tags_json=["新能源"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        await session.commit()

        async def fallback_history(*_args: object, **_kwargs: object) -> data.ProviderFetchResult:
            return data.ProviderFetchResult(
                rows=[
                    {
                        "date": "2026-07-10",
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 100.0,
                        "turnover": 200.0,
                        "pct_change": 0.0,
                    }
                ],
                provider="sina",
                fallback_used=True,
                primary_error="东方财富: external_call_failed",
            )

        monkeypatch.setattr(data, "fetch_etf_price_history_with_provider", fallback_history)

        await data.sync_etf_price_history(
            session, date(2026, 7, 10), date(2026, 7, 10), ["159605"]
        )
        health = await session.scalar(select(EtfDataHealth).where(EtfDataHealth.etf_code == "159605"))
        price = await session.scalar(select(EtfPriceHistory).where(EtfPriceHistory.etf_code == "159605"))

    assert health is not None
    assert health.status == "success"
    assert health.last_error_message == "东方财富: external_call_failed"
    assert price is not None
    assert price.decision_eligible is False
    assert price.decision_ineligibility_reason == "missing_total_return_provenance"


@pytest.mark.asyncio
async def test_raw_fallback_does_not_downgrade_existing_decision_eligible_history(
    app, monkeypatch
) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="510050",
                name="上证50ETF",
                exchange="SH",
                theme_tags_json=["宽基"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="broad_market",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add(
            EtfPriceHistory(
                etf_code="510050",
                trade_date=date(2026, 7, 10),
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.0,
                volume=100.0,
                turnover=200.0,
                pct_change=0.0,
                raw_price_basis="raw_ohlc",
                research_adjusted_value=2.0,
                research_price_basis=data.TOTAL_RETURN_PRICE_BASIS,
                data_provider="eastmoney",
                provider_version=data.EASTMONEY_HFQ_ADJUSTMENT_VERSION,
                source_timestamp=datetime(2026, 7, 10, 15, 0),
                adjustment_version=data.EASTMONEY_HFQ_ADJUSTMENT_VERSION,
                decision_eligible=True,
                decision_ineligibility_reason=None,
            )
        )
        await session.commit()

        async def fallback_history(*_args: object, **_kwargs: object) -> data.ProviderFetchResult:
            return data.ProviderFetchResult(
                rows=[
                    {
                        "date": "2026-07-10",
                        "open": 9.0,
                        "high": 9.0,
                        "low": 9.0,
                        "close": 9.0,
                        "volume": 900.0,
                        "turnover": 900.0,
                        "pct_change": 9.0,
                    }
                ],
                provider="sina",
                fallback_used=True,
                primary_error="东方财富: external_call_failed",
            )

        monkeypatch.setattr(data, "fetch_etf_price_history_with_provider", fallback_history)

        result = await data.sync_etf_price_history(
            session, date(2026, 7, 10), date(2026, 7, 10), ["510050"]
        )
        price = await session.scalar(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code == "510050",
                EtfPriceHistory.trade_date == date(2026, 7, 10),
            )
        )

    assert result["updated"] == 0
    assert price is not None
    assert price.close == 1.0
    assert price.data_provider == "eastmoney"
    assert price.research_adjusted_value == 2.0
    assert price.research_price_basis == data.TOTAL_RETURN_PRICE_BASIS
    assert price.decision_eligible is True
