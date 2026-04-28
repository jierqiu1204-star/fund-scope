from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime
from typing import Any

import akshare as ak
import httpx

from app.services.retry import retry_async


def _extract_js_array(text: str, variable_name: str) -> list[object]:
    match = re.search(rf"var\s+{re.escape(variable_name)}\s*=\s*(\[.*?\]);", text, re.DOTALL)
    if match is None:
        return []
    parsed: object = json.loads(match.group(1))
    if not isinstance(parsed, list):
        return []
    return parsed


def _date_from_epoch_millis(value: int | float) -> date:
    return datetime.fromtimestamp(float(value) / 1000, tz=UTC).date()


def _as_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "date"):
        parsed = value.date()
        if isinstance(parsed, date):
            return parsed
    return date.fromisoformat(str(value))


def parse_akshare_nav_frame(
    frame: Any,
    from_date: date,
    to_date: date,
) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for _, record in frame.iterrows():
        nav_date = _as_date(record.get("\u51c0\u503c\u65e5\u671f"))
        if from_date <= nav_date <= to_date:
            rows.append(
                {
                    "date": nav_date.isoformat(),
                    "nav": float(record.get("\u5355\u4f4d\u51c0\u503c")),
                    "accumulated_nav": float(record.get("\u7d2f\u8ba1\u51c0\u503c")),
                }
            )
    return rows


def parse_eastmoney_nav_response(
    text: str,
    from_date: date,
    to_date: date,
) -> list[dict[str, float | str]]:
    nav_rows = _extract_js_array(text, "Data_netWorthTrend")
    accumulated_rows = _extract_js_array(text, "Data_ACWorthTrend")
    accumulated_by_date = {
        _date_from_epoch_millis(row[0]): float(row[1])
        for row in accumulated_rows
        if isinstance(row, list) and len(row) >= 2
    }

    rows: list[dict[str, float | str]] = []
    for record in nav_rows:
        if not isinstance(record, dict):
            continue
        raw_date = record.get("x")
        raw_nav = record.get("y")
        if raw_date is None or raw_nav is None:
            continue
        nav_date = _date_from_epoch_millis(float(raw_date))
        if not from_date <= nav_date <= to_date:
            continue
        nav = float(raw_nav)
        rows.append(
            {
                "date": nav_date.isoformat(),
                "nav": nav,
                "accumulated_nav": accumulated_by_date.get(nav_date, nav),
            }
        )
    return rows


async def fetch_fund_nav(code: str, from_date: date, to_date: date) -> list[dict[str, float | str]]:
    async def fetch_primary() -> list[dict[str, float | str]]:
        frame = await asyncio.to_thread(
            ak.fund_open_fund_info_em,
            symbol=code,
            indicator="\u5355\u4f4d\u51c0\u503c\u8d70\u52bf",
        )
        return parse_akshare_nav_frame(frame, from_date, to_date)

    async def fetch_fallback() -> list[dict[str, float | str]]:
        url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
        return parse_eastmoney_nav_response(response.text, from_date, to_date)

    try:
        return await retry_async("fetch_fund_nav_primary", fetch_primary)
    except Exception:  # noqa: BLE001
        return await retry_async("fetch_fund_nav_fallback", fetch_fallback)
