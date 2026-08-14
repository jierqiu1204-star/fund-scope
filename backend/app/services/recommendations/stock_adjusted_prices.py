"""Bounded reads for recommendation metrics from authoritative A-share facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from math import isfinite

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class StockAdjustedPricePoint:
    trade_date: date
    close: float
    turnover: float


@dataclass(frozen=True, slots=True)
class StockAdjustedPriceBar:
    trade_date: date
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    provider: str
    adjustment_version: str
    revision_id: str
    received_at: datetime
    decision_eligible: bool
    price_basis: str


def canonical_ashare_code(stock_code: str) -> str:
    """Map recommendation codes such as ``600519.SH`` to six-digit fact codes."""

    parts = [part for part in stock_code.strip().upper().replace("/", ".").split(".") if part]
    numeric = next((part for part in parts if part.isdigit()), "")
    if not numeric:
        numeric = "".join(character for character in stock_code if character.isdigit())
    return numeric.zfill(6)[-6:] if numeric else ""


async def compatible_adjusted_prices(
    session: AsyncSession,
    *,
    stock_code: str,
    as_of_date: date,
    lookback_days: int = 370,
    max_rows: int = 370,
) -> tuple[StockAdjustedPricePoint, ...]:
    """Read latest eligible total-return-adjusted revisions, bounded and fail closed."""

    if lookback_days < 1 or not 1 <= max_rows <= 512:
        raise ValueError("invalid adjusted-price read bound")
    cutoff = datetime.combine(as_of_date, time.max)
    try:
        result = await session.execute(
            text(
                """
                SELECT trade_date, adjusted_close, amount
                FROM (
                    SELECT trade_date, adjusted_close, amount,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code, trade_date
                               ORDER BY received_at DESC, revision_id DESC, id DESC
                           ) AS revision_rank
                    FROM ashare_adjusted_price_facts
                    WHERE asset_code = :asset_code
                      AND trade_date BETWEEN :start_date AND :as_of_date
                      AND received_at IS NOT NULL
                      AND received_at <= :cutoff
                      AND decision_eligible = :eligible
                      AND historical_research_only = :historical_only
                      AND price_basis = :price_basis
                      AND LOWER(provider) IN ('akshare', 'eastmoney', 'tickflow')
                      AND adjustment_version IS NOT NULL
                      AND TRIM(adjustment_version) <> ''
                      AND adjusted_close > 0
                      AND adjusted_close < :finite_max
                      AND amount >= 0
                      AND amount < :finite_max
                ) ranked
                WHERE revision_rank = 1
                ORDER BY trade_date DESC
                LIMIT :max_rows
                """
            ),
            {
                "asset_code": canonical_ashare_code(stock_code),
                "start_date": as_of_date.fromordinal(
                    max(1, as_of_date.toordinal() - lookback_days)
                ),
                "as_of_date": as_of_date,
                "cutoff": cutoff,
                "eligible": True,
                "historical_only": False,
                "price_basis": "total_return_adjusted",
                "finite_max": 1.0e100,
                "max_rows": max_rows,
            },
        )
    except SQLAlchemyError:
        return ()

    points: list[StockAdjustedPricePoint] = []
    for row in reversed(result.mappings().all()):
        trade_date = row["trade_date"]
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date[:10])
        close = float(row["adjusted_close"])
        turnover = float(row["amount"])
        if not (isfinite(close) and close > 0 and isfinite(turnover) and turnover >= 0):
            continue
        points.append(
            StockAdjustedPricePoint(
                trade_date=trade_date,
                close=close,
                turnover=turnover,
            )
        )
    return tuple(points)


async def compatible_adjusted_bars(
    session: AsyncSession,
    *,
    stock_code: str,
    as_of_date: date,
    lookback_days: int = 180,
    max_rows: int = 180,
) -> tuple[StockAdjustedPriceBar, ...]:
    """Read bounded, PIT-visible adjusted OHLC bars for position risk rules."""

    if lookback_days < 1 or not 1 <= max_rows <= 300:
        raise ValueError("invalid adjusted-bar read bound")
    cutoff = datetime.combine(as_of_date, time.max)
    try:
        result = await session.execute(
            text(
                """
                SELECT trade_date, adjusted_open, adjusted_high, adjusted_low,
                       adjusted_close, provider, adjustment_version, revision_id,
                       received_at, decision_eligible, price_basis
                FROM (
                    SELECT trade_date, adjusted_open, adjusted_high, adjusted_low,
                           adjusted_close, provider, adjustment_version, revision_id,
                           received_at, decision_eligible, price_basis,
                           ROW_NUMBER() OVER (
                               PARTITION BY asset_code, trade_date
                               ORDER BY received_at DESC, revision_id DESC, id DESC
                           ) AS revision_rank
                    FROM ashare_adjusted_price_facts
                    WHERE asset_code = :asset_code
                      AND trade_date BETWEEN :start_date AND :as_of_date
                      AND received_at IS NOT NULL
                      AND received_at <= :cutoff
                      AND decision_eligible = :eligible
                      AND historical_research_only = :historical_only
                      AND price_basis = :price_basis
                      AND LOWER(provider) IN ('akshare', 'eastmoney', 'tickflow')
                      AND adjustment_version IS NOT NULL
                      AND TRIM(adjustment_version) <> ''
                      AND adjusted_open > 0
                      AND adjusted_high >= adjusted_open
                      AND adjusted_low > 0
                      AND adjusted_low <= adjusted_high
                      AND adjusted_close > 0
                ) ranked
                WHERE revision_rank = 1
                ORDER BY trade_date ASC
                LIMIT :max_rows
                """
            ),
            {
                "asset_code": canonical_ashare_code(stock_code),
                "start_date": as_of_date.fromordinal(
                    max(1, as_of_date.toordinal() - lookback_days)
                ),
                "as_of_date": as_of_date,
                "cutoff": cutoff,
                "eligible": True,
                "historical_only": False,
                "price_basis": "total_return_adjusted",
                "max_rows": max_rows,
            },
        )
    except SQLAlchemyError:
        return ()

    bars: list[StockAdjustedPriceBar] = []
    for row in result.mappings().all():
        trade_date = row["trade_date"]
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date[:10])
        received_at = row["received_at"]
        if isinstance(received_at, str):
            received_at = datetime.fromisoformat(received_at)
        try:
            values = tuple(
                float(row[key])
                for key in ("adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close")
            )
        except (TypeError, ValueError):
            continue
        if (
            not isinstance(received_at, datetime)
            or not all(isfinite(value) and value > 0 for value in values)
            or values[2] > values[1]
            or values[1] < max(values[0], values[3])
            or values[2] > min(values[0], values[3])
        ):
            continue
        bars.append(
            StockAdjustedPriceBar(
                trade_date=trade_date,
                adjusted_open=values[0],
                adjusted_high=values[1],
                adjusted_low=values[2],
                adjusted_close=values[3],
                provider=str(row["provider"]).lower(),
                adjustment_version=str(row["adjustment_version"]),
                revision_id=str(row["revision_id"]),
                received_at=received_at,
                decision_eligible=bool(row["decision_eligible"]),
                price_basis=str(row["price_basis"]),
            )
        )
    return tuple(bars)


__all__ = [
    "StockAdjustedPriceBar",
    "StockAdjustedPricePoint",
    "canonical_ashare_code",
    "compatible_adjusted_bars",
    "compatible_adjusted_prices",
]
