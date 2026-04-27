from __future__ import annotations

import asyncio
from datetime import date

import akshare as ak

from app.services.retry import retry_async


async def fetch_index_valuation(index_code: str, valuation_date: date) -> dict[str, float | date]:
    async def fetch_primary() -> dict[str, float | date]:
        frame = await asyncio.to_thread(ak.stock_zh_index_value_csindex, symbol=index_code)
        row = frame.iloc[-1]
        return {
            "date": valuation_date,
            "pe": float(row.get("市盈率1")),
            "pb": float(row.get("市净率")),
            "dividend_yield": float(row.get("股息率")) if row.get("股息率") is not None else 0.0,
        }

    return await retry_async("fetch_index_valuation_primary", fetch_primary)
