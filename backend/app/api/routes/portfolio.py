from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import Fund, FundNavHistory, HoldingsSnapshot, Transaction
from app.schemas.portfolio import HoldingItem, HoldingsResponse, ValueHistoryPoint
from app.services.valuation import is_business_days_stale

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _aggregate_transactions(transactions: Sequence[Transaction]) -> dict[str, tuple[float, float]]:
    state: dict[str, tuple[float, float]] = {}
    for transaction in sorted(transactions, key=lambda item: (item.traded_at, item.id)):
        shares, cost = state.get(transaction.fund_code, (0.0, 0.0))
        if transaction.action == "buy":
            state[transaction.fund_code] = (shares + transaction.shares, cost + (transaction.amount or 0.0))
        else:
            average_cost = (cost / shares) if shares else 0.0
            new_shares = shares - transaction.shares
            new_cost = max(cost - (average_cost * transaction.shares), 0.0)
            state[transaction.fund_code] = (new_shares, new_cost)
    return state


@router.get("/holdings", response_model=HoldingsResponse)
async def get_holdings(session: AsyncSession = Depends(get_db_session)) -> HoldingsResponse:
    latest_snapshot_date = await session.scalar(select(func.max(HoldingsSnapshot.snapshot_date)))
    items: list[HoldingItem] = []

    if latest_snapshot_date is not None:
        snapshot_rows = (
            await session.execute(
                select(HoldingsSnapshot, Fund, FundNavHistory)
                .join(Fund, Fund.code == HoldingsSnapshot.fund_code)
                .join(
                    FundNavHistory,
                    (FundNavHistory.fund_code == HoldingsSnapshot.fund_code)
                    & (FundNavHistory.nav_date == HoldingsSnapshot.snapshot_date),
                    isouter=True,
                )
                .where(HoldingsSnapshot.snapshot_date == latest_snapshot_date)
            )
        ).all()

        for snapshot, fund, nav in snapshot_rows:
            market_value = snapshot.market_value
            pnl = market_value - snapshot.cost_basis if market_value is not None else None
            pnl_pct = (pnl / snapshot.cost_basis * 100) if pnl is not None and snapshot.cost_basis else None
            valuation_status = "ready" if market_value is not None and nav is not None else "missing_nav"
            items.append(
                HoldingItem(
                    fund_code=snapshot.fund_code,
                    fund_name=fund.name,
                    shares=snapshot.shares,
                    cost_basis=snapshot.cost_basis,
                    market_value=market_value,
                    pnl=pnl,
                    pnl_pct=round(pnl_pct, 2) if pnl_pct is not None else None,
                    is_stale=is_business_days_stale(nav.nav_date) if nav is not None else True,
                    as_of_date=nav.nav_date if nav is not None else None,
                    valuation_status=valuation_status,
                )
            )
        return HoldingsResponse(items=items)

    transactions = (await session.scalars(select(Transaction))).all()
    funds = {fund.code: fund for fund in (await session.scalars(select(Fund))).all()}
    for fund_code, (shares, cost_basis) in _aggregate_transactions(transactions).items():
        if shares <= 0:
            continue
        items.append(
            HoldingItem(
                fund_code=fund_code,
                fund_name=funds[fund_code].name,
                shares=shares,
                cost_basis=round(cost_basis, 2),
                market_value=None,
                pnl=None,
                pnl_pct=None,
                is_stale=True,
                as_of_date=None,
                valuation_status="missing_snapshot",
            )
        )
    return HoldingsResponse(items=items)


@router.get("/value-history", response_model=list[ValueHistoryPoint])
async def get_value_history(session: AsyncSession = Depends(get_db_session)) -> list[ValueHistoryPoint]:
    rows = (
        await session.execute(
            select(HoldingsSnapshot.snapshot_date, func.sum(HoldingsSnapshot.market_value))
            .group_by(HoldingsSnapshot.snapshot_date)
            .order_by(HoldingsSnapshot.snapshot_date.asc())
        )
    ).all()
    return [ValueHistoryPoint(date=snapshot_date, value=float(value or 0.0)) for snapshot_date, value in rows]
