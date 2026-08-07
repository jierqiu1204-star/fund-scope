from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.services.strategy_lab import (
    dual_universe_leader_tactics_v2_tickflow_provider as provider_module,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_tickflow_provider import (
    BAOSTOCK_TAXONOMY_VERSION,
    CAPCO_TAXONOMY_VERSION,
    CAPCO_THEME_SOURCE,
    TICKFLOW_ADJUSTMENT_VERSION,
    TICKFLOW_BASE_URL,
    TICKFLOW_PROVIDER,
    TICKFLOW_TAXONOMY_VERSION,
    TICKFLOW_THEME_SOURCE,
    AshareIndustryClassification,
    BaoStockIndustryBatchResult,
    TickflowAshareProviderError,
    TickflowAshareV2Provider,
    load_capco_bse_industries,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
RECEIVED_AT = datetime(2026, 8, 7, 20, 0, tzinfo=SHANGHAI)


def test_capco_snapshot_only_supplements_missing_bse_members_at_real_receipt() -> None:
    members = (
        provider_module.TickflowAshareMember(
            symbol="920169.BJ",
            code="920169",
            name="七丰精工",
            board="BSE",
            current_industry=None,
            float_shares=1_000_000,
        ),
        provider_module.TickflowAshareMember(
            symbol="000001.SZ",
            code="000001",
            name="平安银行",
            board="SZ",
            current_industry=None,
            float_shares=1_000_000,
        ),
    )

    result = load_capco_bse_industries(
        members,
        signal_date=date(2026, 8, 7),
        received_at=RECEIVED_AT,
    )

    assert set(result) == {"920169.BJ"}
    classification = result["920169.BJ"]
    assert classification.name == "通用设备制造业"
    assert classification.group_id == "capco_division:34"
    assert classification.source == CAPCO_THEME_SOURCE
    assert classification.taxonomy_version == CAPCO_TAXONOMY_VERSION
    assert classification.effective_from == date(2026, 8, 7)
    assert classification.received_at == RECEIVED_AT
    assert classification.confidence == "official_frozen_snapshot"


def test_baostock_parser_preserves_completed_rows_when_later_symbol_times_out() -> None:
    result = provider_module._parse_baostock_industry_output(
        stdout=(
            b'{"industry": "bank", "symbol": "600001.SH"}\n'
            b'{"industry": null, "symbol": "000001.SZ"}\n'
        ),
        stderr=b"",
        returncode=-14,
        requested_symbols=("600001.SH", "000001.SZ", "000002.SZ"),
    )

    assert isinstance(result, BaoStockIndustryBatchResult)
    assert result.industries == {"600001.SH": "bank"}
    assert result.completed_symbols == ("600001.SH", "000001.SZ")
    assert result.failures == (
        ("000002.SZ", "baostock_industry_failed:returncode=-14"),
    )


def _instrument(symbol: str, name: str, *, float_shares: float | None = 1_000_000) -> dict:
    code, exchange = symbol.split(".")
    return {
        "symbol": symbol,
        "exchange": exchange,
        "code": code,
        "name": name,
        "region": "CN",
        "type": "stock",
        "ext": {"type": "cn_equity", "float_shares": float_shares},
    }


def _history_payload() -> dict:
    timestamps = [
        int(datetime(2026, 8, day, tzinfo=SHANGHAI).timestamp() * 1000)
        for day in (6, 7)
    ]
    return {
        "data": {
            "timestamp": timestamps,
            "open": [10.0, 10.5],
            "high": [10.8, 11.2],
            "low": [9.8, 10.2],
            "close": [10.5, 11.0],
            "volume": [1_000.0, 2_000.0],
            "amount": [1_050_000.0, 2_200_000.0],
        }
    }


@pytest.mark.asyncio
async def test_provider_combines_complete_tickflow_universe_baostock_theme_and_adjusted_bars(
    monkeypatch,
) -> None:
    monkeypatch.setattr(provider_module, "_CURRENT_UNIVERSE_CACHE", None)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/universes/CN_Equity_A"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "CN_Equity_A",
                        "symbol_count": 2,
                        "symbols": ["600001.SH", "000001.SZ"],
                    }
                },
            )
        if request.url.path.endswith("/instruments"):
            payload = json.loads(request.content)
            rows = {
                "600001.SH": _instrument("600001.SH", "浦发测试"),
                "000001.SZ": _instrument("000001.SZ", "平安测试", float_shares=None),
            }
            return httpx.Response(200, json={"data": [rows[s] for s in payload["symbols"]]})
        if request.url.path.endswith("/klines"):
            assert request.url.params["adjust"] == "backward"
            assert request.url.params["symbol"] == "000001.SZ"
            return httpx.Response(200, json=_history_payload())
        raise AssertionError(request.url)

    async def industries():
        return {"000001.SZ": "银行"}

    async with httpx.AsyncClient(
        base_url=TICKFLOW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        async with TickflowAshareV2Provider(
            client=client,
            industry_loader=industries,
        ) as provider:
            members = await provider.fetch_universe(received_at=RECEIVED_AT)
            facts = await provider.fetch_facts(
                members[0],
                signal_date=date(2026, 8, 7),
                received_at=RECEIVED_AT,
                history_sessions=2,
            )

    assert [member.code for member in members] == ["000001", "600001"]
    assert members[0].current_industry == "银行"
    assert facts.universe_fact.provider == TICKFLOW_PROVIDER
    assert facts.theme_facts[0].taxonomy_version == BAOSTOCK_TAXONOMY_VERSION
    assert facts.theme_facts[0].group_id == "baostock_industry:银行"
    assert len(facts.adjusted_price_facts) == 2
    assert all(fact.provider == TICKFLOW_PROVIDER for fact in facts.adjusted_price_facts)
    assert all(
        fact.adjustment_version == TICKFLOW_ADJUSTMENT_VERSION
        for fact in facts.adjusted_price_facts
    )
    assert facts.adjusted_price_facts[-1].decision_eligible is True
    assert facts.adjusted_price_facts[-1].turnover == 0.0
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_provider_batches_tickflow_sw1_as_primary_current_classification(
    monkeypatch,
) -> None:
    monkeypatch.setattr(provider_module, "_CURRENT_UNIVERSE_CACHE", None)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/universes/CN_Equity_A"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "CN_Equity_A",
                        "symbol_count": 2,
                        "symbols": ["600001.SH", "000001.SZ"],
                    }
                },
            )
        if request.url.path.endswith("/universes/batch"):
            assert json.loads(request.content) == {"ids": ["CN_Equity_SW1_1"]}
            return httpx.Response(
                200,
                json={
                    "data": {
                        "CN_Equity_SW1_1": {
                            "id": "CN_Equity_SW1_1",
                            "name": "SW1银行",
                            "symbol_count": 1,
                            "symbols": ["000001.SZ"],
                        }
                    }
                },
            )
        if request.url.path.endswith("/universes"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "CN_Equity_SW1_1",
                            "name": "SW1银行",
                            "symbol_count": 1,
                        }
                    ]
                },
            )
        if request.url.path.endswith("/instruments"):
            payload = json.loads(request.content)
            rows = {
                "600001.SH": _instrument("600001.SH", "浦发测试"),
                "000001.SZ": _instrument("000001.SZ", "平安测试"),
            }
            return httpx.Response(200, json={"data": [rows[s] for s in payload["symbols"]]})
        if request.url.path.endswith("/klines"):
            return httpx.Response(200, json=_history_payload())
        raise AssertionError(request.url)

    async with httpx.AsyncClient(
        base_url=TICKFLOW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        async with TickflowAshareV2Provider(client=client) as provider:
            members = await provider.fetch_universe(received_at=RECEIVED_AT)
            facts = await provider.fetch_facts(
                members[0],
                signal_date=date(2026, 8, 7),
                received_at=RECEIVED_AT,
                history_sessions=2,
            )

    assert members[0].code == "000001"
    assert members[0].current_industry == "银行"
    assert members[1].current_industry is None
    assert facts.theme_facts[0].source == TICKFLOW_THEME_SOURCE
    assert facts.theme_facts[0].taxonomy_version == TICKFLOW_TAXONOMY_VERSION
    assert facts.theme_facts[0].group_id == "tickflow_sw1:银行"
    assert len(requests) == 5


@pytest.mark.asyncio
async def test_provider_fails_closed_on_partial_instrument_metadata(monkeypatch) -> None:
    monkeypatch.setattr(provider_module, "_CURRENT_UNIVERSE_CACHE", None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/universes/CN_Equity_A"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "CN_Equity_A",
                        "symbol_count": 2,
                        "symbols": ["600001.SH", "000001.SZ"],
                    }
                },
            )
        if request.url.path.endswith("/instruments"):
            return httpx.Response(200, json={"data": [_instrument("600001.SH", "only-one")]})
        raise AssertionError(request.url)

    async def industries():
        return {}

    async with httpx.AsyncClient(
        base_url=TICKFLOW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        async with TickflowAshareV2Provider(
            client=client,
            industry_loader=industries,
        ) as provider:
            with pytest.raises(TickflowAshareProviderError, match="instrument_batch_partial"):
                await provider.fetch_universe(received_at=RECEIVED_AT)


@pytest.mark.asyncio
async def test_provider_applies_persisted_baostock_supplement_without_replacing_tickflow(
    monkeypatch,
) -> None:
    monkeypatch.setattr(provider_module, "_CURRENT_UNIVERSE_CACHE", None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/universes/CN_Equity_A"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "CN_Equity_A",
                        "symbol_count": 1,
                        "symbols": ["600001.SH"],
                    }
                },
            )
        if request.url.path.endswith("/instruments"):
            return httpx.Response(
                200,
                json={"data": [_instrument("600001.SH", "浦发测试")]},
            )
        if request.url.path.endswith("/klines"):
            return httpx.Response(200, json=_history_payload())
        raise AssertionError(request.url)

    async def industries():
        return {}

    observed_at = datetime(2026, 8, 7, 12)
    supplement = AshareIndustryClassification(
        name="银行",
        group_id="baostock_industry:银行",
        source="baostock.query_stock_industry.current",
        taxonomy_version=BAOSTOCK_TAXONOMY_VERSION,
        effective_from=date(2026, 8, 7),
        received_at=observed_at,
    )
    async with httpx.AsyncClient(
        base_url=TICKFLOW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        async with TickflowAshareV2Provider(
            client=client,
            industry_loader=industries,
        ) as provider:
            await provider.fetch_universe(received_at=RECEIVED_AT)
            members = provider.apply_industry_supplements({"600001.SH": supplement})
            facts = await provider.fetch_facts(
                members[0],
                signal_date=date(2026, 8, 7),
                received_at=RECEIVED_AT,
                history_sessions=2,
            )

    assert facts.theme_facts[0].received_at == observed_at
    assert facts.theme_facts[0].group_id == "baostock_industry:银行"


@pytest.mark.asyncio
async def test_provider_wraps_blocking_industry_failure(monkeypatch) -> None:
    monkeypatch.setattr(provider_module, "_CURRENT_UNIVERSE_CACHE", None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "CN_Equity_A",
                    "symbol_count": 1,
                    "symbols": ["600001.SH"],
                }
            },
        )

    async def industries():
        raise OSError("provider down")

    async with httpx.AsyncClient(
        base_url=TICKFLOW_BASE_URL,
        transport=httpx.MockTransport(handler),
    ) as client:
        async with TickflowAshareV2Provider(
            client=client,
            industry_loader=industries,
        ) as provider:
            with pytest.raises(
                TickflowAshareProviderError,
                match="industry_loader_failed:OSError",
            ):
                await provider.fetch_universe(received_at=RECEIVED_AT)
