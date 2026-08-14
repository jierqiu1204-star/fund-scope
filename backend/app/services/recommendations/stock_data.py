from __future__ import annotations

from datetime import date
from typing import TypedDict


class StockUniverseRow(TypedDict):
    code: str
    exchange: str
    name: str
    industry: str



class StockFundamentalRow(TypedDict):
    stock_code: str
    report_date: date
    pe: float
    pb: float
    roe: float
    gross_margin: float
    debt_to_asset: float
    operating_cashflow: float
    dividend_yield: float


def default_stock_universe() -> list[StockUniverseRow]:
    return [
        {"code": "600519.SH", "exchange": "SH", "name": "Kweichow Moutai", "industry": "consumer"},
        {"code": "000333.SZ", "exchange": "SZ", "name": "Midea Group", "industry": "consumer"},
        {"code": "600036.SH", "exchange": "SH", "name": "China Merchants Bank", "industry": "financials"},
    ]




def default_stock_fundamentals(as_of_date: date) -> list[StockFundamentalRow]:
    return [
        {
            "stock_code": "600519.SH",
            "report_date": as_of_date,
            "pe": 24.0,
            "pb": 6.0,
            "roe": 28.0,
            "gross_margin": 90.0,
            "debt_to_asset": 18.0,
            "operating_cashflow": 1000.0,
            "dividend_yield": 2.0,
        },
        {
            "stock_code": "000333.SZ",
            "report_date": as_of_date,
            "pe": 14.0,
            "pb": 2.8,
            "roe": 21.0,
            "gross_margin": 27.0,
            "debt_to_asset": 58.0,
            "operating_cashflow": 800.0,
            "dividend_yield": 3.0,
        },
        {
            "stock_code": "600036.SH",
            "report_date": as_of_date,
            "pe": 6.5,
            "pb": 0.9,
            "roe": 15.0,
            "gross_margin": 42.0,
            "debt_to_asset": 88.0,
            "operating_cashflow": 1200.0,
            "dividend_yield": 5.0,
        },
    ]
