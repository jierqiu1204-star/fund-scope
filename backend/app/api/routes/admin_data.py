from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import (
    EtfIntradayLatestQuote,
    EtfIntradayQuote,
    EtfPriceHistory,
    Fund,
    FundNavHistory,
    PaperPortfolio,
    StrategyDefinition,
)
from app.services.intraday_etf.service import (
    is_fresh_decision_quote,
    quote_decision_limitation_reason,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/data-status")
async def get_data_status(session: AsyncSession = Depends(get_db_session)) -> dict[str, int | str | None]:
    fund_count = await session.scalar(select(func.count(Fund.code)).where(Fund.is_watchlist.is_(True)))
    nav_rows, earliest_nav_date, latest_nav_date = (
        await session.execute(
            select(
                func.count(FundNavHistory.id),
                func.min(FundNavHistory.nav_date),
                func.max(FundNavHistory.nav_date),
            )
        )
    ).one()
    strategy_count = await session.scalar(select(func.count(StrategyDefinition.id)))
    paper_portfolio_count = await session.scalar(select(func.count(PaperPortfolio.id)))

    return {
        "fund_count": int(fund_count or 0),
        "nav_rows": int(nav_rows or 0),
        "earliest_nav_date": earliest_nav_date.isoformat() if earliest_nav_date else None,
        "latest_nav_date": latest_nav_date.isoformat() if latest_nav_date else None,
        "strategy_count": int(strategy_count or 0),
        "paper_portfolio_count": int(paper_portfolio_count or 0),
    }


def _quote_summary(quote: EtfIntradayLatestQuote | EtfIntradayQuote | None) -> dict[str, Any] | None:
    if quote is None:
        return None
    return {
        "price": quote.latest_price,
        "quote_time": quote.quote_time.isoformat(),
        "trade_date": quote.trade_date.isoformat(),
        "change_percent": quote.change_percent,
        "source": quote.source,
        "freshness_status": quote.freshness_status,
    }


def _daily_summary(row: EtfPriceHistory | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "price": row.close,
        "trade_date": row.trade_date.isoformat(),
        "pct_change": row.pct_change,
    }


@router.get("/etf-quote-diagnostics")
async def get_etf_quote_diagnostics(
    codes: list[str] = Query(default_factory=list),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    normalized_codes = [code.strip() for code in codes if code.strip()][:20]
    items: list[dict[str, Any]] = []

    for code in normalized_codes:
        latest_snapshot = await session.get(EtfIntradayLatestQuote, code)
        latest_history = await session.scalar(
            select(EtfIntradayQuote)
            .where(EtfIntradayQuote.etf_code == code)
            .order_by(EtfIntradayQuote.quote_time.desc(), EtfIntradayQuote.id.desc())
            .limit(1)
        )
        latest_daily = await session.scalar(
            select(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code == code)
            .order_by(EtfPriceHistory.trade_date.desc(), EtfPriceHistory.id.desc())
            .limit(1)
        )

        display_quote: EtfIntradayLatestQuote | EtfIntradayQuote | None = latest_snapshot or latest_history
        if display_quote is not None:
            display_price = display_quote.latest_price
            display_time = display_quote.quote_time.isoformat()
            display_source = "latest_snapshot" if latest_snapshot is not None else "intraday_history"
            email_eligible = is_fresh_decision_quote(display_quote)
            email_reason = (
                "新鲜盘中行情，可用于邮件判断。"
                if email_eligible
                else quote_decision_limitation_reason(display_quote) or "盘中行情不可用于邮件判断。"
            )
        elif latest_daily is not None:
            display_price = latest_daily.close
            display_time = latest_daily.trade_date.isoformat()
            display_source = "daily_reference"
            email_eligible = False
            email_reason = "日线参考不能触发盘中邮件。"
        else:
            display_price = None
            display_time = None
            display_source = "unavailable"
            email_eligible = False
            email_reason = "暂无可用行情。"

        items.append(
            {
                "code": code,
                "latest_snapshot": _quote_summary(latest_snapshot),
                "latest_history": _quote_summary(latest_history),
                "daily_reference": _daily_summary(latest_daily),
                "display_price": display_price,
                "display_time": display_time,
                "display_price_source": display_source,
                "email_eligible": email_eligible,
                "email_eligibility_reason": email_reason,
            }
        )

    return {"items": items}
