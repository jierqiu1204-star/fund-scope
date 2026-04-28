from __future__ import annotations

from datetime import date, timedelta
from typing import TypedDict


class StockUniverseRow(TypedDict):
    code: str
    exchange: str
    name: str
    industry: str


class StockPriceRow(TypedDict):
    stock_code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


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


def default_stock_price_history(as_of_date: date) -> list[StockPriceRow]:
    rows: list[StockPriceRow] = []
    seeds = {
        "600519.SH": (100.0, 122.0, 1_800_000_000.0),
        "000333.SZ": (50.0, 58.0, 900_000_000.0),
        "600036.SH": (30.0, 31.5, 700_000_000.0),
    }
    for stock_code, (start, end, turnover) in seeds.items():
        rows.append(
            {
                "stock_code": stock_code,
                "trade_date": as_of_date - timedelta(days=180),
                "open": start,
                "high": start,
                "low": start,
                "close": start,
                "volume": turnover / start,
                "turnover": turnover,
            }
        )
        rows.append(
            {
                "stock_code": stock_code,
                "trade_date": as_of_date,
                "open": end,
                "high": end * 1.02,
                "low": end * 0.98,
                "close": end,
                "volume": turnover / end,
                "turnover": turnover,
            }
        )
    return rows


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
