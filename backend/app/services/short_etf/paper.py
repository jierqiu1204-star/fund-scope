from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    ShortEtfPaperEquityCurve,
    ShortEtfPaperOrder,
    ShortEtfPaperPortfolio,
    ShortEtfSignalItem,
    TradableEtf,
    utcnow,
)
from app.services.short_etf.data import latest_etf_price
from app.services.short_etf.signals import latest_signal_run, list_signal_items

PAPER_STATUS_ACTIVE = "active"
DEFAULT_INITIAL_CASH = 100000.0
DEFAULT_FEE_RATE = 0.0005


@dataclass(frozen=True)
class PaperPosition:
    etf_code: str
    shares: float
    market_value: float
    weight: float


async def start_paper_portfolio(
    session: AsyncSession,
    *,
    name: str,
    started_at: date,
    initial_cash: float = DEFAULT_INITIAL_CASH,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> ShortEtfPaperPortfolio:
    paper = ShortEtfPaperPortfolio(
        name=name,
        status=PAPER_STATUS_ACTIVE,
        started_at=started_at,
        cash=initial_cash,
        latest_equity=initial_cash,
        config_json={"initial_cash": initial_cash, "fee_rate": fee_rate, "target_count": 1},
    )
    session.add(paper)
    await session.commit()
    await session.refresh(paper)
    return paper


async def get_paper_portfolio(session: AsyncSession, paper_id: int) -> ShortEtfPaperPortfolio | None:
    return cast(
        ShortEtfPaperPortfolio | None,
        await session.scalar(select(ShortEtfPaperPortfolio).where(ShortEtfPaperPortfolio.id == paper_id)),
    )


async def list_paper_orders(session: AsyncSession, paper_id: int) -> list[ShortEtfPaperOrder]:
    rows = await session.scalars(
        select(ShortEtfPaperOrder)
        .where(ShortEtfPaperOrder.paper_id == paper_id)
        .order_by(ShortEtfPaperOrder.trade_date.asc(), ShortEtfPaperOrder.id.asc())
    )
    return list(rows.all())


async def list_equity_curve(session: AsyncSession, paper_id: int) -> list[ShortEtfPaperEquityCurve]:
    rows = await session.scalars(
        select(ShortEtfPaperEquityCurve)
        .where(ShortEtfPaperEquityCurve.paper_id == paper_id)
        .order_by(ShortEtfPaperEquityCurve.curve_date.asc(), ShortEtfPaperEquityCurve.id.asc())
    )
    return list(rows.all())


def _holdings_from_orders(orders: list[ShortEtfPaperOrder]) -> dict[str, float]:
    holdings: dict[str, float] = {}
    for order in orders:
        sign = 1 if order.side == "buy" else -1
        holdings[order.etf_code] = holdings.get(order.etf_code, 0.0) + sign * order.shares
    return {code: shares for code, shares in holdings.items() if shares > 0.000001}


def _last_buy_dates(orders: list[ShortEtfPaperOrder]) -> dict[str, date]:
    dates: dict[str, date] = {}
    for order in orders:
        if order.side == "buy":
            dates[order.etf_code] = order.trade_date
    return dates


async def _positions(
    session: AsyncSession,
    holdings: dict[str, float],
    cash: float,
    as_of_date: date,
) -> tuple[list[PaperPosition], float]:
    values: list[tuple[str, float, float]] = []
    for code, shares in holdings.items():
        price = await latest_etf_price(session, code, as_of_date)
        if price is None:
            continue
        values.append((code, shares, shares * price.close))
    total_value = cash + sum(value for _, _, value in values)
    positions = [
        PaperPosition(
            etf_code=code,
            shares=round(shares, 6),
            market_value=round(market_value, 2),
            weight=round(market_value / total_value, 4) if total_value else 0.0,
        )
        for code, shares, market_value in values
    ]
    return positions, round(total_value, 2)


async def _target_items(session: AsyncSession, as_of_date: date) -> tuple[int | None, list[ShortEtfSignalItem]]:
    run = await latest_signal_run(session)
    if run is None or run.as_of_date > as_of_date:
        return None, []
    items = await list_signal_items(session, run.id)
    eligible = [item for item in items if item.conclusion == "可观察"]
    return run.id, eligible[:1]


async def run_paper_update(
    session: AsyncSession,
    paper: ShortEtfPaperPortfolio,
    *,
    as_of_date: date,
) -> ShortEtfPaperPortfolio:
    existing_curve = await session.scalar(
        select(ShortEtfPaperEquityCurve).where(
            ShortEtfPaperEquityCurve.paper_id == paper.id,
            ShortEtfPaperEquityCurve.curve_date == as_of_date,
        )
    )
    if existing_curve is not None:
        return paper

    orders = await list_paper_orders(session, paper.id)
    holdings = _holdings_from_orders(orders)
    last_buy_dates = _last_buy_dates(orders)
    signal_run_id, targets = await _target_items(session, as_of_date)
    target_codes = {item.etf_code for item in targets}
    fee_rate = float(paper.config_json.get("fee_rate", DEFAULT_FEE_RATE))

    for code, shares in list(holdings.items()):
        if code in target_codes or last_buy_dates.get(code) == as_of_date:
            continue
        price = await latest_etf_price(session, code, as_of_date)
        if price is None:
            continue
        amount = shares * price.close
        fee = amount * fee_rate
        paper.cash += amount - fee
        session.add(
            ShortEtfPaperOrder(
                paper_id=paper.id,
                signal_run_id=signal_run_id,
                trade_date=as_of_date,
                etf_code=code,
                side="sell",
                amount=round(amount, 2),
                shares=round(shares, 6),
                price=price.close,
                fee=round(fee, 2),
                status="confirmed",
            )
        )
        holdings.pop(code, None)

    for item in targets:
        if item.etf_code in holdings or paper.cash <= 0:
            continue
        price = await latest_etf_price(session, item.etf_code, as_of_date)
        if price is None:
            continue
        amount = paper.cash * 0.95
        fee = amount * fee_rate
        shares = max(0.0, (amount - fee) / price.close)
        if shares <= 0:
            continue
        paper.cash -= amount
        session.add(
            ShortEtfPaperOrder(
                paper_id=paper.id,
                signal_run_id=signal_run_id,
                trade_date=as_of_date,
                etf_code=item.etf_code,
                side="buy",
                amount=round(amount, 2),
                shares=round(shares, 6),
                price=price.close,
                fee=round(fee, 2),
                status="confirmed",
            )
        )
        holdings[item.etf_code] = holdings.get(item.etf_code, 0.0) + shares

    positions, equity = await _positions(session, holdings, paper.cash, as_of_date)
    previous_curve = await list_equity_curve(session, paper.id)
    previous_peak = max([point.equity for point in previous_curve], default=equity)
    peak = max(previous_peak, equity)
    drawdown = equity / peak - 1 if peak else 0.0
    paper.latest_equity = equity
    paper.updated_at = utcnow()
    session.add(
        ShortEtfPaperEquityCurve(
            paper_id=paper.id,
            curve_date=as_of_date,
            equity=equity,
            cash=round(paper.cash, 2),
            drawdown=round(drawdown, 6),
        )
    )
    paper.config_json = {
        **paper.config_json,
        "latest_positions": [position.__dict__ for position in positions],
        "trading_rule_note": "股票类 ETF 按 T+1 保守模拟；第一版不做盘中成交。",
    }
    await session.commit()
    await session.refresh(paper)
    return paper


async def latest_etf_names(session: AsyncSession) -> dict[str, str]:
    rows = await session.scalars(select(TradableEtf))
    return {row.code: row.name for row in rows.all()}


def paper_summary(paper: ShortEtfPaperPortfolio) -> dict[str, Any]:
    return {
        "trading_rule_note": paper.config_json.get("trading_rule_note", "股票类 ETF 按 T+1 保守模拟；第一版不做盘中成交。"),
        "initial_cash": paper.config_json.get("initial_cash", DEFAULT_INITIAL_CASH),
        "fee_rate": paper.config_json.get("fee_rate", DEFAULT_FEE_RATE),
    }
