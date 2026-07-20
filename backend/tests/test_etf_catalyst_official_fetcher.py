from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from app.services.etf_catalyst_shadow.official_fetcher import (
    OfficialCatalystFetcher,
    OfficialSourceFetchError,
    parse_ndrc_query_response,
)

FETCHED = datetime(2026, 7, 20, 10, 30)


def _payload(*, total_hits: int = 2) -> dict:
    return {
        "ok": True,
        "data": {
            "totalHits": total_hits,
            "resultList": [
                {
                    "reference": "official-1",
                    "url": "https://www.ndrc.gov.cn/xxgk/zcfb/tz/one.html",
                    "docDate": "2026-07-17",
                    "title": "政策一",
                },
                {
                    "reference": "official-2",
                    "url": "https://www.ndrc.gov.cn/xxgk/zcfb/tz/two.html",
                    "docDate": "2026-07-16",
                    "title": "政策二",
                },
            ],
        },
    }


def test_ndrc_response_becomes_bounded_dated_receipts() -> None:
    batch = parse_ndrc_query_response(
        _payload(total_hits=40),
        source_id="ndrc_policy",
        page=1,
        offset=0,
        source_page_size=2,
        maximum_items=2,
        fetched_at=FETCHED,
    )

    assert len(batch.receipts) == 2
    assert batch.next_cursor == "2:0"
    assert batch.complete is False
    assert batch.receipts[0].source_published_at == datetime(2026, 7, 17)
    assert batch.receipts[0].published_time_precision == "day"
    assert batch.receipts[0].parser_version == "ndrc_query_v1"


def test_ndrc_empty_response_is_explicit_successful_empty() -> None:
    payload = {"ok": True, "data": {"totalHits": 0, "resultList": []}}
    batch = parse_ndrc_query_response(
        payload,
        source_id="ndrc_policy",
        page=1,
        offset=0,
        source_page_size=20,
        maximum_items=20,
        fetched_at=FETCHED,
    )

    assert batch.complete is True
    assert batch.next_cursor is None
    assert batch.receipts[0].fetch_state == "successful_empty"


@pytest.mark.asyncio
async def test_fetcher_reads_public_ndrc_query_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.ndrc.gov.cn":
            return httpx.Response(
                200,
                text="siteCode: 'public-site', key: 'public-key'",
            )
        assert request.url.host == "fwfx.ndrc.gov.cn"
        assert request.url.params["pageSize"] == "10"
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        batch = await OfficialCatalystFetcher(client=client)(
            "ndrc_policy",
            "https://www.ndrc.gov.cn/xxgk/wjk/",
            None,
            2,
        )

    assert [item.external_id for item in batch.receipts] == [
        "official-1",
        "official-2",
    ]


def test_ndrc_small_batches_resume_within_the_same_source_page() -> None:
    payload = _payload(total_hits=40)
    payload["data"]["resultList"] = [
        {
            "reference": f"official-{index}",
            "url": f"https://www.ndrc.gov.cn/xxgk/zcfb/tz/{index}.html",
            "docDate": "2026-07-17",
            "title": f"政策{index}",
        }
        for index in range(1, 11)
    ]

    first = parse_ndrc_query_response(
        payload,
        source_id="ndrc_policy",
        page=1,
        offset=0,
        source_page_size=10,
        maximum_items=3,
        fetched_at=FETCHED,
    )
    second = parse_ndrc_query_response(
        payload,
        source_id="ndrc_policy",
        page=1,
        offset=3,
        source_page_size=10,
        maximum_items=3,
        fetched_at=FETCHED,
    )

    assert first.next_cursor == "1:3"
    assert second.next_cursor == "1:6"
    assert [item.external_id for item in second.receipts] == [
        "official-4",
        "official-5",
        "official-6",
    ]


@pytest.mark.asyncio
async def test_fetcher_fails_closed_for_unimplemented_official_source() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as client:
        with pytest.raises(OfficialSourceFetchError):
            await OfficialCatalystFetcher(client=client)(
                "csrc_announcement",
                "https://www.csrc.gov.cn/csrc/c101954/common_list.shtml",
                None,
                20,
            )
