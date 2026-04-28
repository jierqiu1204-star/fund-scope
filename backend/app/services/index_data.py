from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import akshare as ak
import httpx

from app.services.retry import retry_async


def _first_number(row: Any, keys: list[str], *, default: float | None = None) -> float:
    for key in keys:
        value = row.get(key) if hasattr(row, "get") else None
        if value is None or value == "":
            continue
        return float(value)
    if default is not None:
        return default
    raise KeyError(f"Missing valuation field; tried {keys}")


def _extract_csindex_payload(payload: Any, valuation_date: date) -> dict[str, float | date]:
    if isinstance(payload, dict):
        for key in ("data", "result", "records", "rows"):
            nested = payload.get(key)
            if isinstance(nested, list) and nested:
                return _extract_csindex_payload(nested[0], valuation_date)
            if isinstance(nested, dict):
                return _extract_csindex_payload(nested, valuation_date)
        return {
            "date": valuation_date,
            "pe": _first_number(payload, ["pe", "PE", "peRatio", "pe_ttm", "PE_TTM"]),
            "pb": _first_number(payload, ["pb", "PB", "pbRatio"]),
            "dividend_yield": _first_number(
                payload,
                ["dividend_yield", "dividendYield", "dyr", "yield"],
                default=0.0,
            ),
        }
    if isinstance(payload, list) and payload:
        return _extract_csindex_payload(payload[0], valuation_date)
    raise ValueError("CSIndex fallback response did not include valuation data")


async def fetch_primary_index_valuation(index_code: str, valuation_date: date) -> dict[str, float | date]:
    frame = await asyncio.to_thread(ak.stock_zh_index_value_csindex, symbol=index_code)
    row = frame.iloc[-1]
    return {
        "date": valuation_date,
        "pe": _first_number(row, ["\u5e02\u76c8\u73871", "\u5e02\u76c8\u7387", "pe", "PE", "PE_TTM"]),
        "pb": _first_number(row, ["\u5e02\u51c0\u7387", "pb", "PB"]),
        "dividend_yield": _first_number(row, ["\u80a1\u606f\u7387", "dividend_yield"], default=0.0),
    }


async def fetch_index_valuation_fallback(index_code: str, valuation_date: date) -> dict[str, float | date]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://www.csindex.com.cn/csindex-home/index-list/query-valuation-data",
            params={"indexCode": index_code},
        )
        response.raise_for_status()
        return _extract_csindex_payload(response.json(), valuation_date)


async def fetch_index_valuation(index_code: str, valuation_date: date) -> dict[str, float | date]:
    try:
        return await retry_async(
            "fetch_index_valuation_primary",
            lambda: fetch_primary_index_valuation(index_code, valuation_date),
        )
    except Exception:
        return await retry_async(
            "fetch_index_valuation_fallback",
            lambda: fetch_index_valuation_fallback(index_code, valuation_date),
        )
