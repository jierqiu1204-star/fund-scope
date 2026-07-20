from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from app.services.etf_catalyst_shadow.ingestion import ReceiptInput
from app.services.etf_catalyst_shadow.runner import SourceFetchBatch

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_NDRC_QUERY_URL = "https://fwfx.ndrc.gov.cn/api/query"
_MAX_INDEX_BYTES = 2_000_000
_MAX_QUERY_BYTES = 5_000_000


class OfficialSourceFetchError(RuntimeError):
    pass


def _local_now() -> datetime:
    return datetime.now(_SHANGHAI).replace(tzinfo=None)


def _ndrc_query_credentials(index_html: str) -> tuple[str, str]:
    site_code = re.search(r"siteCode:\s*['\"]([^'\"]+)['\"]", index_html)
    key = re.search(r"key:\s*['\"]([^'\"]+)['\"]", index_html)
    if site_code is None or key is None:
        raise OfficialSourceFetchError("NDRC query credentials missing from index")
    return site_code.group(1), key.group(1)


def _ndrc_cursor(cursor: str | None) -> tuple[int, int]:
    if cursor is None:
        return 1, 0
    parts = cursor.split(":", maxsplit=1)
    try:
        page = int(parts[0])
        offset = int(parts[1]) if len(parts) == 2 else 0
    except ValueError as exc:
        raise OfficialSourceFetchError("NDRC cursor is not page[:offset]") from exc
    if not 1 <= page <= 500 or not 0 <= offset < 20:
        raise OfficialSourceFetchError("NDRC cursor is outside the bounded range")
    return page, offset


def _published_date(value: object) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError as exc:
        raise OfficialSourceFetchError("NDRC item has no valid docDate") from exc


def _validate_ndrc_item(item: object) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise OfficialSourceFetchError("NDRC result item is not an object")
    url = str(item.get("url") or "")
    host = (urlsplit(url).hostname or "").lower()
    if host != "ndrc.gov.cn" and not host.endswith(".ndrc.gov.cn"):
        raise OfficialSourceFetchError("NDRC result URL is outside ndrc.gov.cn")
    if not item.get("reference"):
        raise OfficialSourceFetchError("NDRC result item has no reference")
    return item


def parse_ndrc_query_response(
    payload: object,
    *,
    source_id: str,
    page: int,
    offset: int,
    source_page_size: int,
    maximum_items: int,
    fetched_at: datetime,
) -> SourceFetchBatch:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise OfficialSourceFetchError("NDRC query did not return ok=true")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise OfficialSourceFetchError("NDRC query data is missing")
    result_list = data.get("resultList")
    if not isinstance(result_list, list):
        raise OfficialSourceFetchError("NDRC query resultList is missing")
    try:
        total_hits = max(0, int(data.get("totalHits", 0)))
    except (TypeError, ValueError) as exc:
        raise OfficialSourceFetchError("NDRC totalHits is invalid") from exc

    if total_hits == 0 and not result_list:
        return SourceFetchBatch(
            receipts=(
                ReceiptInput(
                    source_id=source_id,
                    fetch_state="successful_empty",
                    fetched_at=fetched_at,
                    first_received_at=fetched_at,
                    observation_key=f"{source_id}:page:{page}:empty",
                    parser_version="ndrc_query_v1",
                ),
            ),
            next_cursor=None,
            complete=True,
        )
    if not result_list or offset >= len(result_list):
        raise OfficialSourceFetchError("NDRC query page does not satisfy its cursor")
    bounded = result_list[offset : offset + maximum_items]

    receipts: list[ReceiptInput] = []
    for raw_item in bounded:
        item = _validate_ndrc_item(raw_item)
        receipts.append(
            ReceiptInput(
                source_id=source_id,
                fetch_state="item",
                fetched_at=fetched_at,
                first_received_at=fetched_at,
                external_id=str(item["reference"]),
                canonical_url=str(item["url"]),
                source_published_at=_published_date(item.get("docDate")),
                published_time_precision="day",
                raw_content=json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                raw_content_ref=f"ndrc-query://page/{page}/{item['reference']}",
                parser_version="ndrc_query_v1",
                metadata={
                    "title": str(item.get("title") or item.get("dreTitle") or ""),
                    "index_date": item.get("indexDate"),
                    "source_api": _NDRC_QUERY_URL,
                },
            )
        )

    next_offset = offset + len(bounded)
    capped_total = min(total_hits, 500)
    if next_offset < len(result_list):
        next_cursor = f"{page}:{next_offset}"
        complete = False
    else:
        consumed = (page - 1) * source_page_size + len(result_list)
        complete = consumed >= capped_total
        next_cursor = None if complete else f"{page + 1}:0"
    return SourceFetchBatch(
        receipts=tuple(receipts),
        next_cursor=next_cursor,
        complete=complete,
    )


class OfficialCatalystFetcher:
    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def __call__(
        self,
        source_id: str,
        endpoint: str,
        cursor: str | None,
        maximum_items: int,
    ) -> SourceFetchBatch:
        if source_id != "ndrc_policy":
            raise OfficialSourceFetchError(
                f"no production parser is registered for {source_id}"
            )
        if self._client is not None:
            return await self._fetch_ndrc(
                self._client,
                endpoint=endpoint,
                cursor=cursor,
                maximum_items=maximum_items,
            )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
            headers={"User-Agent": "FundScope-Catalyst-Shadow/1.0"},
        ) as client:
            return await self._fetch_ndrc(
                client,
                endpoint=endpoint,
                cursor=cursor,
                maximum_items=maximum_items,
            )

    async def _fetch_ndrc(
        self,
        client: httpx.AsyncClient,
        *,
        endpoint: str,
        cursor: str | None,
        maximum_items: int,
    ) -> SourceFetchBatch:
        page, offset = _ndrc_cursor(cursor)
        source_page_size = max(10, maximum_items)
        index_response = await client.get(endpoint)
        index_response.raise_for_status()
        if len(index_response.content) > _MAX_INDEX_BYTES:
            raise OfficialSourceFetchError("NDRC index response exceeds size limit")
        site_code, key = _ndrc_query_credentials(index_response.text)

        query_response = await client.get(
            _NDRC_QUERY_URL,
            params={
                "qt": "",
                "tab": "all",
                "page": page,
                "pageSize": source_page_size,
                "siteCode": site_code,
                "key": key,
                "startDateStr": "",
                "endDateStr": "",
                "timeOption": 0,
                "sort": "dateDesc",
            },
        )
        query_response.raise_for_status()
        if len(query_response.content) > _MAX_QUERY_BYTES:
            raise OfficialSourceFetchError("NDRC query response exceeds size limit")
        try:
            payload = query_response.json()
        except ValueError as exc:
            raise OfficialSourceFetchError("NDRC query response is not JSON") from exc
        return parse_ndrc_query_response(
            payload,
            source_id="ndrc_policy",
            page=page,
            offset=offset,
            source_page_size=source_page_size,
            maximum_items=maximum_items,
            fetched_at=_local_now(),
        )
