from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest

from app.services.strategy_lab import (
    dual_universe_leader_tactics_v2_eastmoney_provider as provider_module,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_eastmoney_provider import (
    EASTMONEY_A_SHARE_HISTORY_URL,
    EASTMONEY_A_SHARE_UNIVERSE_FALLBACK_URL,
    EASTMONEY_A_SHARE_UNIVERSE_URL,
    EASTMONEY_ADJUSTMENT_VERSION,
    AshareUniverseMember,
    EastmoneyAshareProviderConfig,
    EastmoneyAshareProviderError,
    EastmoneyAshareV2Provider,
)

RECEIVED_AT = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)


def _universe_payload(
    *, total: int = 2, rows: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "rc": 0,
        "data": {
            "total": total,
            "diff": rows
            if rows is not None
            else [
                {"f12": "000001", "f13": "0", "f14": "平安银行", "f100": "银行"},
                {"f12": "600000", "f13": "1", "f14": "浦发银行", "f100": "银行"},
            ],
        },
    }


def _history_payload(*, open_value: str = "10.5") -> dict[str, object]:
    return {
        "rc": 0,
        "data": {
            "code": "000001",
            "klines": [
                "2026-08-03,10,10.5,11,9.5,100,1000,0,0,0,1",
                f"2026-08-04,{open_value},10.8,11,10,110,1100,0,0,0,1.1",
            ],
        },
    }


@pytest.mark.asyncio
async def test_provider_returns_sorted_members_and_one_adjusted_history_request() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_URL).path:
            return httpx.Response(200, json=_universe_payload())
        assert request.url.path == httpx.URL(EASTMONEY_A_SHARE_HISTORY_URL).path
        assert request.url.params["fqt"] == "2"
        return httpx.Response(200, json=_history_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client) as provider:
            members = await provider.fetch_universe(received_at=RECEIVED_AT)
            facts = await provider.fetch_facts(
                members[0],
                signal_date=date(2026, 8, 4),
                received_at=RECEIVED_AT,
                history_sessions=2,
            )

    assert members == (
        AshareUniverseMember("000001", "平安银行", "SZ", "银行"),
        AshareUniverseMember("600000", "浦发银行", "SH", "银行"),
    )
    assert len(requests) == 2
    assert requests[0].url.path == httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_URL).path
    assert requests[1].url.params["fqt"] == "2"
    assert requests[1].url.params["secid"] == "0.000001"
    assert all(request.url.host.endswith("eastmoney.com") for request in requests)
    assert [fact.trade_date for fact in facts.adjusted_price_facts] == [
        date(2026, 8, 3),
        date(2026, 8, 4),
    ]
    assert all(fact.provider == "eastmoney" for fact in facts.adjusted_price_facts)
    assert all(fact.price_basis == "total_return_adjusted" for fact in facts.adjusted_price_facts)
    assert all(
        fact.adjustment_version == EASTMONEY_ADJUSTMENT_VERSION
        for fact in facts.adjusted_price_facts
    )
    assert facts.theme_facts[0].effective_from == date(2026, 8, 5)
    assert facts.theme_facts[0].received_at == RECEIVED_AT.replace(tzinfo=None)
    assert facts.theme_facts[0].effective_from > date(2026, 8, 4)


@pytest.mark.asyncio
async def test_universe_partial_payload_fails_closed_and_is_not_cached() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_universe_payload(
                total=2,
                rows=[{"f12": "000001", "f13": "0", "f14": "平安银行", "f100": "银行"}],
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client) as provider:
            with pytest.raises(EastmoneyAshareProviderError, match="universe_partial_or_invalid"):
                await provider.fetch_universe(received_at=RECEIVED_AT)
            with pytest.raises(EastmoneyAshareProviderError, match="universe_partial_or_invalid"):
                await provider.fetch_universe(received_at=RECEIVED_AT)

    assert calls == 2


@pytest.mark.asyncio
async def test_transport_eof_rotates_only_between_official_https_universe_hosts() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.RemoteProtocolError("peer closed connection", request=request)
        return httpx.Response(200, json=_universe_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client) as provider:
            members = await provider.fetch_universe(received_at=RECEIVED_AT)
            diagnostics = provider.transport_diagnostics

    assert len(members) == 2
    assert [request.url.host for request in requests] == [
        httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_URL).host,
        httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_FALLBACK_URL).host,
    ]
    assert all(request.url.scheme == "https" for request in requests)
    assert diagnostics == {
        "request_attempt_count": 2,
        "transport_retry_count": 1,
        "last_endpoint_host": httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_FALLBACK_URL).host,
        "last_transport_error": "RemoteProtocolError",
        "universe_cache_hit": False,
    }


@pytest.mark.asyncio
async def test_owned_provider_reuses_one_bounded_daily_universe_cache(monkeypatch) -> None:
    calls = 0

    async def request_json(_self, kind, _params, *, budget_seconds=None):
        nonlocal calls
        assert kind == "universe"
        assert budget_seconds is not None
        calls += 1
        return _universe_payload()

    monkeypatch.setattr(provider_module, "_CURRENT_UNIVERSE_CACHE", None)
    monkeypatch.setattr(EastmoneyAshareV2Provider, "_request_json", request_json)

    async with EastmoneyAshareV2Provider() as first_provider:
        first_members = await first_provider.fetch_universe(received_at=RECEIVED_AT)
        assert first_provider.transport_diagnostics["universe_cache_hit"] is False
    async with EastmoneyAshareV2Provider() as second_provider:
        second_members = await second_provider.fetch_universe(received_at=RECEIVED_AT)
        assert second_provider.transport_diagnostics["universe_cache_hit"] is True

    assert first_members == second_members
    assert calls == 1


@pytest.mark.asyncio
async def test_universe_is_fetched_in_stable_bounded_pages_and_includes_bse() -> None:
    requests: list[httpx.Request] = []
    rows = [
        {
            "f12": f"{index:06d}",
            "f13": "0",
            "f14": f"asset-{index}",
            "f100": "测试行业",
        }
        for index in range(1, 205)
    ]
    rows.append(
        {
            "f12": "920001",
            "f13": "2",
            "f14": "北交测试",
            "f100": "测试行业",
        }
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page_number = int(request.url.params["pn"])
        start = (page_number - 1) * 100
        return httpx.Response(
            200,
            json=_universe_payload(total=len(rows), rows=rows[start : start + 100]),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client) as provider:
            members = await provider.fetch_universe(received_at=RECEIVED_AT)

    assert len(members) == 205
    assert [request.url.params["pn"] for request in requests] == ["1", "2", "3"]
    assert all(request.url.params["pz"] == "100" for request in requests)
    assert all(request.url.params["fid"] == "f12" for request in requests)
    assert all("t:81" in request.url.params["fs"] for request in requests)
    assert members[-1] == AshareUniverseMember("920001", "北交测试", "BSE", "测试行业")


@pytest.mark.asyncio
async def test_incomplete_later_universe_page_fails_closed() -> None:
    calls = 0
    first_page = [
        {
            "f12": f"{index:06d}",
            "f13": "0",
            "f14": f"asset-{index}",
            "f100": "测试行业",
        }
        for index in range(1, 101)
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.params["pn"] == "1":
            return httpx.Response(200, json=_universe_payload(total=101, rows=first_page))
        return httpx.Response(200, json=_universe_payload(total=101, rows=[]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client) as provider:
            with pytest.raises(EastmoneyAshareProviderError, match="universe_partial_or_invalid"):
                await provider.fetch_universe(received_at=RECEIVED_AT)

    assert calls == 2


@pytest.mark.asyncio
async def test_invalid_adjusted_ohlc_is_rejected_without_raw_or_fallback_request() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_URL).path:
            return httpx.Response(
                200,
                json=_universe_payload(
                    total=1, rows=[{"f12": "000001", "f13": "0", "f14": "平安银行", "f100": "银行"}]
                ),
            )
        return httpx.Response(200, json=_history_payload(open_value="nan"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client) as provider:
            members = await provider.fetch_universe(received_at=RECEIVED_AT)
            with pytest.raises(EastmoneyAshareProviderError, match="adjusted_open_non_finite"):
                await provider.fetch_facts(
                    members[0],
                    signal_date=date(2026, 8, 4),
                    received_at=RECEIVED_AT,
                    history_sessions=2,
                )

    assert [request.url.path for request in requests] == [
        httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_URL).path,
        httpx.URL(EASTMONEY_A_SHARE_HISTORY_URL).path,
    ]
    assert requests[1].url.params["fqt"] == "2"
    assert requests[1].url.params["fields2"] == "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"


@pytest.mark.asyncio
async def test_http_status_failure_is_not_retried_or_downgraded() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, json={"rc": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client) as provider:
            with pytest.raises(EastmoneyAshareProviderError, match="universe_http_status:503"):
                await provider.fetch_universe(received_at=RECEIVED_AT)

    assert len(requests) == 1
    assert requests[0].url.host == httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_URL).host
    assert requests[0].url.scheme == "https"


@pytest.mark.asyncio
async def test_request_timeout_is_bounded_to_one_retry() -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=_universe_payload())

    config = EastmoneyAshareProviderConfig(
        request_timeout_seconds=0.01,
        code_budget_seconds=0.05,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client, config=config) as provider:
            with pytest.raises(
                EastmoneyAshareProviderError,
                match=r"universe_transport_unavailable:attempts=2",
            ):
                await provider.fetch_universe(received_at=RECEIVED_AT)

    assert calls == 2


@pytest.mark.asyncio
async def test_adjusted_history_transport_error_retries_same_approved_https_endpoint() -> None:
    requests: list[httpx.Request] = []
    history_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_calls
        requests.append(request)
        if request.url.path == httpx.URL(EASTMONEY_A_SHARE_UNIVERSE_URL).path:
            return httpx.Response(
                200,
                json=_universe_payload(
                    total=1,
                    rows=[
                        {
                            "f12": "000001",
                            "f13": "0",
                            "f14": "平安银行",
                            "f100": "银行",
                        }
                    ],
                ),
            )
        history_calls += 1
        if history_calls == 1:
            raise httpx.RemoteProtocolError("peer closed connection", request=request)
        return httpx.Response(200, json=_history_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with EastmoneyAshareV2Provider(client=client) as provider:
            member = (await provider.fetch_universe(received_at=RECEIVED_AT))[0]
            facts = await provider.fetch_facts(
                member,
                signal_date=date(2026, 8, 4),
                received_at=RECEIVED_AT,
                history_sessions=2,
            )

    history_requests = [
        request
        for request in requests
        if request.url.path == httpx.URL(EASTMONEY_A_SHARE_HISTORY_URL).path
    ]
    assert len(facts.adjusted_price_facts) == 2
    assert len(history_requests) == 2
    assert all(request.url == history_requests[0].url for request in history_requests)
    assert all(request.url.scheme == "https" for request in history_requests)


def test_provider_transport_attempt_limit_is_hard_bounded() -> None:
    with pytest.raises(ValueError, match="transport attempts"):
        EastmoneyAshareProviderConfig(transport_attempts=3)
    with pytest.raises(ValueError, match="universe budget"):
        EastmoneyAshareProviderConfig(universe_budget_seconds=41)


def test_provider_rejects_naive_or_future_cutoff_before_provider_work() -> None:
    provider = EastmoneyAshareV2Provider()
    with pytest.raises(EastmoneyAshareProviderError, match="received_at_must_be_timezone_aware"):
        asyncio.run(provider.fetch_universe(received_at=datetime(2026, 8, 5, 8)))
