from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest
from sqlalchemy import select

from app.models.entities import (
    EtfAdjustedPriceRevision,
    EtfDataHealth,
    EtfPriceHistory,
    TradableEtf,
)
from app.services.market_data import (
    etf_adjusted_price_provenance_issue,
    etf_decision_adjusted_provider_versions,
)
from app.services.short_etf import data
from app.services.short_etf.publication_providers import (
    ACCEPTED_ADJUSTED_PROVIDER_VERSIONS,
)


class _Frame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def iterrows(self):  # type: ignore[no-untyped-def]
        return enumerate(self.rows)


@pytest.mark.asyncio
async def test_eastmoney_history_uses_akshare_raw_and_hfq_provenance(
    monkeypatch,
) -> None:
    calls: list[str] = []

    async def fake_records(
        _code: str,
        _from_date: date,
        _to_date: date,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        calls.append("raw_then_hfq")
        return (
            [
                {
                    "日期": "2026-07-10",
                    "开盘": 1.0,
                    "最高": 1.0,
                    "最低": 1.0,
                    "收盘": 1.0,
                    "成交量": 100.0,
                    "成交额": 200.0,
                    "涨跌幅": 0.0,
                }
            ],
            [
                {
                    "日期": "2026-07-10",
                    "开盘": 2.0,
                    "最高": 2.0,
                    "最低": 2.0,
                    "收盘": 2.0,
                    "成交量": 100.0,
                    "成交额": 200.0,
                    "涨跌幅": 0.0,
                }
            ],
        )

    monkeypatch.setattr(data, "_fetch_akshare_history_records", fake_records)

    rows = await data.fetch_eastmoney_etf_price_history(
        "159605", date(2026, 7, 10), date(2026, 7, 10)
    )

    assert calls == ["raw_then_hfq"]
    assert rows[0]["close"] == 1.0
    assert rows[0]["research_adjusted_value"] == 2.0
    assert rows[0]["research_price_basis"] == data.TOTAL_RETURN_PRICE_BASIS
    assert rows[0]["adjustment_version"] == "eastmoney.push2his.kline.hfq_v1"
    assert rows[0]["provider_version"] == "eastmoney.push2his.kline.hfq_v1"


@pytest.mark.asyncio
async def test_efinance_history_requests_raw_and_hfq_with_total_return_provenance(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_to_thread(_func: object, *_args: object, **kwargs: object) -> _Frame:
        calls.append(kwargs)
        close = 1.0 if kwargs["fqt"] == 0 else 2.0
        return _Frame(
            [
                {
                    "日期": "2026-07-10",
                    "开盘": close,
                    "最高": close,
                    "最低": close,
                    "收盘": close,
                    "成交量": 100.0,
                    "成交额": 200.0,
                    "涨跌幅": 0.0,
                }
            ]
        )

    monkeypatch.setattr(data.asyncio, "to_thread", fake_to_thread)

    rows = await data.fetch_efinance_etf_price_history(
        "510050", date(2026, 7, 10), date(2026, 7, 10)
    )

    assert [call["fqt"] for call in calls] == [0, 2]
    assert all(call["beg"] == "20260710" for call in calls)
    assert all(call["end"] == "20260710" for call in calls)
    assert rows[0]["close"] == 1.0
    assert rows[0]["research_adjusted_value"] == 2.0
    assert rows[0]["research_price_basis"] == data.TOTAL_RETURN_PRICE_BASIS
    assert rows[0]["adjustment_version"] == "efinance.stock.get_quote_history.fqt2_v1"
    assert rows[0]["provider_version"] == "efinance.stock.get_quote_history.fqt2_v1"


def test_parse_tickflow_history_payload_filters_dates_and_calculates_change() -> None:
    rows = data.parse_tickflow_history_payload(
        {
            "timestamp": [1783526400000, 1783612800000],
            "open": [1.0, 1.1],
            "high": [1.1, 1.2],
            "low": [0.9, 1.0],
            "close": [1.0, 1.1],
            "volume": [100.0, 200.0],
            "amount": [1000.0, 2200.0],
        },
        date(2026, 7, 10),
        date(2026, 7, 10),
    )

    assert rows == [
        {
            "date": "2026-07-10",
            "open": 1.1,
            "high": 1.2,
            "low": 1.0,
            "close": 1.1,
            "volume": 200.0,
            "turnover": 2200.0,
            "pct_change": pytest.approx(10.0),
        }
    ]


@pytest.mark.asyncio
async def test_tickflow_history_requests_raw_and_backward_with_total_return_provenance(
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        close = 1.0 if request.url.params["adjust"] == "none" else 2.0
        return httpx.Response(
            200,
            json={
                "data": {
                    "timestamp": [1783612800000],
                    "open": [close],
                    "high": [close],
                    "low": [close],
                    "close": [close],
                    "volume": [100.0],
                    "amount": [200.0],
                }
            },
        )

    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(data.httpx, "AsyncClient", client_factory)

    rows = await data.fetch_tickflow_etf_price_history(
        "510050", date(2026, 7, 10), date(2026, 7, 10)
    )

    assert [request.url.params["adjust"] for request in requests] == ["none", "backward"]
    assert all(request.url.params["symbol"] == "510050.SH" for request in requests)
    assert all(request.url.params["period"] == "1d" for request in requests)
    assert all(request.url.params["count"] == "10000" for request in requests)
    assert rows[0]["close"] == 1.0
    assert rows[0]["research_adjusted_value"] == 2.0
    assert rows[0]["research_price_basis"] == data.TOTAL_RETURN_PRICE_BASIS
    assert rows[0]["adjustment_version"] == "tickflow.free.klines.backward_v1"
    assert rows[0]["provider_version"] == "tickflow.free.klines.backward_v1"


@pytest.mark.asyncio
async def test_tencent_history_pairs_raw_and_hfq_with_total_return_provenance() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        symbol = "sh510050"
        if request.url.path.endswith("/fqkline/get"):
            instrument = {
                "hfqday": [
                    ["2026-07-10", "2.0", "2.0", "2.1", "1.9", "100"]
                ]
            }
        else:
            instrument = {
                "day": [
                    ["2026-07-10", "1.0", "1.0", "1.1", "0.9", "100"]
                ]
            }
        return httpx.Response(200, json={"data": {symbol: instrument}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await data.fetch_tencent_etf_price_history_once(
            client,
            "510050",
            date(2026, 7, 10),
            date(2026, 7, 10),
        )

    assert len(requests) == 2
    assert requests[0].url.path.endswith("/kline/kline")
    assert requests[0].url.params["param"] == (
        "sh510050,day,2026-07-10,2026-07-10,1"
    )
    assert requests[1].url.path.endswith("/fqkline/get")
    assert requests[1].url.params["param"] == (
        "sh510050,day,2026-07-10,2026-07-10,1,hfq"
    )
    assert all(request.headers["referer"] == "https://gu.qq.com/" for request in requests)
    assert rows[0]["close"] == 1.0
    assert rows[0]["turnover"] == 10_000.0
    assert rows[0]["research_adjusted_value"] == 2.0
    assert rows[0]["research_price_basis"] == data.TOTAL_RETURN_PRICE_BASIS
    assert rows[0]["adjustment_version"] == data.TENCENT_HFQ_ADJUSTMENT_VERSION
    assert rows[0]["provider_version"] == data.TENCENT_HFQ_ADJUSTMENT_VERSION


def test_publication_provider_contract_matches_central_decision_registry() -> None:
    assert dict(etf_decision_adjusted_provider_versions()) == (
        ACCEPTED_ADJUSTED_PROVIDER_VERSIONS
    )


def test_tencent_hfq_is_accepted_by_the_central_decision_registry() -> None:
    issue = etf_adjusted_price_provenance_issue(
        adjusted_value=2.0,
        price_basis=data.TOTAL_RETURN_PRICE_BASIS,
        data_provider="tencent",
        provider_version=data.TENCENT_HFQ_ADJUSTMENT_VERSION,
        source_timestamp=datetime(2026, 7, 10, 6, 0),
        adjustment_version=data.TENCENT_HFQ_ADJUSTMENT_VERSION,
        data_cutoff=datetime(2026, 7, 10, 15, 0),
    )
    forged = etf_adjusted_price_provenance_issue(
        adjusted_value=2.0,
        price_basis=data.TOTAL_RETURN_PRICE_BASIS,
        data_provider="tencent",
        provider_version="tencent.forged_v9",
        source_timestamp=datetime(2026, 7, 10, 6, 0),
        adjustment_version="tencent.forged_v9",
        data_cutoff=datetime(2026, 7, 10, 15, 0),
    )
    legacy_unit_bug = etf_adjusted_price_provenance_issue(
        adjusted_value=2.0,
        price_basis=data.TOTAL_RETURN_PRICE_BASIS,
        data_provider="tencent",
        provider_version="tencent.ifzq.fqkline.hfq_v1",
        source_timestamp=datetime(2026, 7, 10, 6, 0),
        adjustment_version="tencent.ifzq.fqkline.hfq_v1",
        data_cutoff=datetime(2026, 7, 10, 15, 0),
    )

    assert issue is None
    assert forged == "unsupported_adjusted_provider"
    assert legacy_unit_bug == "unsupported_adjusted_provider"


def test_tencent_parser_rejects_incomplete_rows() -> None:
    with pytest.raises(ValueError, match="腾讯返回了不完整"):
        data.parse_tencent_history_payload(
            {"data": {"sh510050": {"day": [["2026-07-10", "1.0"]]}}},
            symbol="sh510050",
            series_names=("day",),
            from_date=date(2026, 7, 10),
            to_date=date(2026, 7, 10),
        )


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
        revision = await session.scalar(
            select(EtfAdjustedPriceRevision).where(
                EtfAdjustedPriceRevision.etf_code == "159605"
            )
        )

    assert health is not None
    assert health.status == "success"
    assert health.last_error_message == "东方财富: external_call_failed"
    assert price is not None
    assert price.decision_eligible is False
    assert price.decision_ineligibility_reason == "missing_total_return_provenance"
    assert revision is not None
    assert revision.decision_eligible is False
    assert revision.first_seen_at == revision.observed_at


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
        revisions = (
            await session.scalars(
                select(EtfAdjustedPriceRevision)
                .where(EtfAdjustedPriceRevision.etf_code == "510050")
                .order_by(EtfAdjustedPriceRevision.id.asc())
            )
        ).all()

    assert result["updated"] == 0
    assert price is not None
    assert price.close == 1.0
    assert price.data_provider == "eastmoney"
    assert price.research_adjusted_value == 2.0
    assert price.research_price_basis == data.TOTAL_RETURN_PRICE_BASIS
    assert price.decision_eligible is True
    assert len(revisions) == 1
    assert revisions[0].decision_eligible is False
