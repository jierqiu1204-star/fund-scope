from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import (
    Fund,
    FundNavHistory,
    HoldingsSnapshot,
    Index,
    IndexValuationHistory,
    NewsItem,
    NewsSummary,
    Transaction,
    User,
)
from app.services.dca_calculator import compute_dca_amount
from app.services.fund_data import fetch_fund_nav
from app.services.index_data import fetch_index_valuation
from app.services.llm import LLMClient
from app.services.news import fetch_news_for_fund
from app.services.news_summarizer import parse_summary_output
from app.services.notifier import Notifier
from app.services.recommendations.engine import generate_all_recommendations, recompute_all_metrics
from app.services.valuation import compute_percentile

logger = logging.getLogger(__name__)


def _aggregate_transactions(transactions: Sequence[Transaction]) -> dict[str, tuple[float, float]]:
    state: dict[str, tuple[float, float]] = {}
    for transaction in sorted(transactions, key=lambda item: (item.traded_at, item.id)):
        shares, cost = state.get(transaction.fund_code, (0.0, 0.0))
        if transaction.action == "buy":
            state[transaction.fund_code] = (
                shares + transaction.shares,
                cost + (transaction.amount or 0.0),
            )
            continue

        average_cost = (cost / shares) if shares else 0.0
        new_shares = shares - transaction.shares
        new_cost = max(cost - (average_cost * transaction.shares), 0.0)
        state[transaction.fund_code] = (new_shares, new_cost)
    return state


async def daily_fund_nav_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    return await sync_fund_nav_history(session, today - timedelta(days=7), today)


async def fund_nav_backfill_job(session: AsyncSession, days: int) -> dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be greater than 0")
    today = date.today()
    return await sync_fund_nav_history(session, today - timedelta(days=days), today)


async def sync_fund_nav_history(
    session: AsyncSession,
    from_date: date,
    to_date: date,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    if from_date > to_date:
        raise ValueError("from_date must be before to_date")

    query = select(Fund.code).where(Fund.is_watchlist.is_(True)).order_by(Fund.code.asc())
    if codes:
        query = query.where(Fund.code.in_(codes))
    fund_codes = (await session.scalars(query)).all()
    inserted = 0
    updated = 0
    failures: list[dict[str, str]] = []

    for fund_code in fund_codes:
        try:
            rows = await fetch_fund_nav(fund_code, from_date, to_date)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fund_nav_fetch_failed", extra={"fund_code": fund_code})
            failures.append({"fund_code": fund_code, "error": str(exc)})
            continue

        for row in rows:
            nav_date = date.fromisoformat(str(row["date"]))
            existing = await session.scalar(
                select(FundNavHistory).where(
                    FundNavHistory.fund_code == fund_code,
                    FundNavHistory.nav_date == nav_date,
                )
            )
            if existing is None:
                session.add(
                    FundNavHistory(
                        fund_code=fund_code,
                        nav_date=nav_date,
                        nav=float(row["nav"]),
                        accumulated_nav=float(row["accumulated_nav"]),
                    )
                )
                inserted += 1
                continue

            existing.nav = float(row["nav"])
            existing.accumulated_nav = float(row["accumulated_nav"])
            updated += 1
    await session.commit()
    return {
        "funds": len(fund_codes),
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "rows_inserted": inserted,
        "rows_updated": updated,
        "failed": len(failures),
        "failures": failures,
    }


async def daily_valuation_job(session: AsyncSession) -> dict[str, Any]:
    today = date.today()
    index_codes = (
        await session.scalars(
            select(Index.code).where(Index.is_watchlist.is_(True)).order_by(Index.code.asc())
        )
    ).all()
    inserted = 0
    failures: list[dict[str, str]] = []

    for index_code in index_codes:
        try:
            payload = await fetch_index_valuation(index_code, today)
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily_valuation_index_failed", extra={"index_code": index_code})
            failures.append({"index_code": index_code, "error": str(exc)})
            continue
        pe = float(cast(float, payload["pe"]))
        pb = float(cast(float, payload["pb"]))
        historical_rows = (
            await session.scalars(
                select(IndexValuationHistory)
                .where(IndexValuationHistory.index_code == index_code)
                .order_by(IndexValuationHistory.valuation_date.asc())
            )
        ).all()
        pe_result = compute_percentile([row.pe for row in historical_rows] + [pe], pe)
        pb_result = compute_percentile([row.pb for row in historical_rows] + [pb], pb)
        existing = await session.scalar(
            select(IndexValuationHistory).where(
                IndexValuationHistory.index_code == index_code,
                IndexValuationHistory.valuation_date == today,
            )
        )
        if existing is None:
            session.add(
                IndexValuationHistory(
                    index_code=index_code,
                    valuation_date=today,
                    pe=pe,
                    pb=pb,
                    dividend_yield=float(cast(float, payload["dividend_yield"])),
                    pe_percentile=pe_result.percentile,
                    pb_percentile=pb_result.percentile,
                    effective_window=pe_result.effective_window,
                )
            )
            inserted += 1
            continue

        existing.pe = pe
        existing.pb = pb
        existing.dividend_yield = float(cast(float, payload["dividend_yield"]))
        existing.pe_percentile = pe_result.percentile
        existing.pb_percentile = pb_result.percentile
        existing.effective_window = pe_result.effective_window
    await session.commit()
    return {
        "indices": len(index_codes),
        "rows_inserted": inserted,
        "failed": len(failures),
        "failures": failures,
    }


async def daily_holdings_snapshot_job(session: AsyncSession) -> dict[str, Any]:
    snapshot_date = await session.scalar(select(func.max(FundNavHistory.nav_date)))
    if snapshot_date is None:
        return {"snapshot_date": None, "rows_upserted": 0}

    transactions = (await session.scalars(select(Transaction))).all()
    holdings = _aggregate_transactions(transactions)
    rows_upserted = 0

    for fund_code, (shares, cost_basis) in holdings.items():
        if shares <= 0:
            continue

        nav_row = await session.scalar(
            select(FundNavHistory)
            .where(
                FundNavHistory.fund_code == fund_code,
                FundNavHistory.nav_date <= snapshot_date,
            )
            .order_by(FundNavHistory.nav_date.desc())
        )
        if nav_row is None:
            continue

        existing = await session.scalar(
            select(HoldingsSnapshot).where(
                HoldingsSnapshot.portfolio_id == 1,
                HoldingsSnapshot.snapshot_date == snapshot_date,
                HoldingsSnapshot.fund_code == fund_code,
            )
        )
        market_value = round(shares * nav_row.nav, 2)
        if existing is None:
            session.add(
                HoldingsSnapshot(
                    portfolio_id=1,
                    snapshot_date=snapshot_date,
                    fund_code=fund_code,
                    shares=shares,
                    cost_basis=round(cost_basis, 2),
                    market_value=market_value,
                )
            )
        else:
            existing.shares = shares
            existing.cost_basis = round(cost_basis, 2)
            existing.market_value = market_value
        rows_upserted += 1
    await session.commit()
    return {"snapshot_date": snapshot_date.isoformat(), "rows_upserted": rows_upserted}


async def daily_news_fetch_job(session: AsyncSession, llm_client: LLMClient) -> dict[str, Any]:
    fund_codes = (
        await session.scalars(select(Fund.code).where(Fund.is_watchlist.is_(True)).order_by(Fund.code.asc()))
    ).all()
    inserted = 0
    summarized = 0
    duplicate_skipped = 0
    empty_funds = 0
    summary_failed = 0
    summary_errors: list[dict[str, str]] = []

    for fund_code in fund_codes:
        items = await fetch_news_for_fund(fund_code)
        if not items:
            empty_funds += 1
        for item in items:
            existing = await session.scalar(select(NewsItem).where(NewsItem.url == str(item["url"])))
            if existing is not None:
                duplicate_skipped += 1
                continue

            news_item = NewsItem(
                fund_code=fund_code,
                published_at=item["published_at"],
                title=str(item["title"]),
                url=str(item["url"]),
                raw_content=str(item["raw_content"]),
            )
            session.add(news_item)
            await session.flush()
            inserted += 1

            try:
                parsed = parse_summary_output(
                    await llm_client.summarize_news(news_item.title, news_item.raw_content)
                )
                session.add(
                    NewsSummary(
                        news_item_id=news_item.id,
                        summary=parsed.summary,
                        event_type=parsed.event_type,
                        model_name=llm_client.model_name,
                    )
                )
                summarized += 1
            except Exception as exc:  # noqa: BLE001
                summary_failed += 1
                summary_errors.append(
                    {
                        "fund_code": fund_code,
                        "url": news_item.url,
                        "error": str(exc),
                    }
                )
                continue

    await session.commit()
    return {
        "funds": len(fund_codes),
        "inserted": inserted,
        "summarized": summarized,
        "duplicate_skipped": duplicate_skipped,
        "empty_funds": empty_funds,
        "summary_failed": summary_failed,
        "summary_errors": summary_errors,
    }


def _build_notifier_for_user(user: User, settings: Settings) -> Notifier:
    smtp_password = settings.smtp_password if user.smtp_password_ref == "env:SMTP_PASSWORD" else ""
    return Notifier(
        smtp_host=user.smtp_host or settings.smtp_host,
        smtp_port=user.smtp_port or settings.smtp_port,
        smtp_username=user.smtp_username or settings.smtp_username,
        smtp_password=smtp_password or settings.smtp_password,
        smtp_from=user.smtp_from or settings.smtp_from,
    )


async def _latest_valuation_statuses(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(IndexValuationHistory).order_by(
                IndexValuationHistory.index_code.asc(),
                IndexValuationHistory.valuation_date.desc(),
            )
        )
    ).all()
    latest_by_index: dict[str, IndexValuationHistory] = {}
    for row in rows:
        latest_by_index.setdefault(row.index_code, row)
    return [
        {
            "index_code": row.index_code,
            "pe": row.pe,
            "pb": row.pb,
            "pe_percentile": row.pe_percentile,
            "pb_percentile": row.pb_percentile,
            "as_of_date": row.valuation_date.isoformat(),
        }
        for row in latest_by_index.values()
    ]


async def _portfolio_return(session: AsyncSession) -> dict[str, float] | None:
    snapshot_date = await session.scalar(select(func.max(HoldingsSnapshot.snapshot_date)))
    if snapshot_date is None:
        return None
    total_cost, total_value = (
        await session.execute(
            select(
                func.sum(HoldingsSnapshot.cost_basis),
                func.sum(HoldingsSnapshot.market_value),
            ).where(HoldingsSnapshot.snapshot_date == snapshot_date)
        )
    ).one()
    cost = float(total_cost or 0.0)
    value = float(total_value or 0.0)
    pnl = round(value - cost, 2)
    return {
        "cost_basis": round(cost, 2),
        "market_value": round(value, 2),
        "pnl": pnl,
        "pnl_pct": round((pnl / cost * 100) if cost else 0.0, 2),
    }


async def monthly_dca_reminder_job(
    session: AsyncSession,
    notifier_or_settings: Notifier | Settings,
) -> dict[str, Any]:
    users = (
        await session.scalars(
            select(User)
            .where(User.is_approved.is_(True))
            .order_by(User.is_super_admin.desc(), User.id.asc())
        )
    ).all()
    if not users:
        return {"amount": 0.0, "reason": "没有已批准用户", "send_status": "skipped"}

    results = []
    for user in users:
        notifier = (
            notifier_or_settings
            if isinstance(notifier_or_settings, Notifier)
            else _build_notifier_for_user(user, notifier_or_settings)
        )
        try:
            result = await _send_monthly_dca_reminder_for_user(session, user, notifier)
        except Exception as exc:  # noqa: BLE001
            logger.warning("monthly_dca_reminder_user_failed", extra={"user_id": user.id})
            result = {
                "user_id": user.id,
                "recipient": user.recipient_email,
                "amount": 0.0,
                "reason": "用户提醒发送失败",
                "send_status": "failed",
                "error": str(exc),
            }
        results.append(result)
    first_result = results[0]
    return {**first_result, "user_results": results}


async def _send_monthly_dca_reminder_for_user(
    session: AsyncSession,
    user: User,
    notifier: Notifier,
) -> dict[str, Any]:
    valuation = await session.scalar(
        select(IndexValuationHistory)
        .where(IndexValuationHistory.index_code == user.reference_index_code)
        .order_by(IndexValuationHistory.valuation_date.desc())
    )
    dca = compute_dca_amount(
        base_amount=user.base_monthly_amount,
        percentile=valuation.pe_percentile if valuation is not None else None,
    )
    funds = (
        await session.scalars(select(Fund).where(Fund.is_watchlist.is_(True)).order_by(Fund.code.asc()))
    ).all()
    breakdown = [
        {"fund_code": fund.code, "amount": round(dca.amount * fund.target_allocation, 2)}
        for fund in funds
    ]
    critical_events = (
        await session.execute(
            select(NewsItem.title, NewsSummary.summary, NewsSummary.event_type)
            .join(NewsSummary, NewsSummary.news_item_id == NewsItem.id)
            .where(NewsSummary.event_type.in_(["manager_change", "strategy_change"]))
        )
    ).all()
    send_status = await notifier.send_template(
        session,
        recipient=user.recipient_email,
        template_name="dca_monthly.html.j2",
        payload={
            "title": "Monthly DCA Reminder",
            "amount": dca.amount,
            "reason": dca.reason,
            "breakdown": breakdown,
            "valuation_statuses": await _latest_valuation_statuses(session),
            "portfolio_return": await _portfolio_return(session),
            "critical_events": [dict(row._mapping) for row in critical_events],
        },
    )
    return {
        "user_id": user.id,
        "recipient": user.recipient_email,
        "amount": dca.amount,
        "reason": dca.reason,
        "send_status": send_status,
    }


async def daily_recommendation_metrics_job(session: AsyncSession) -> dict[str, int]:
    return await recompute_all_metrics(session)


async def daily_asset_recommendations_job(session: AsyncSession) -> dict[str, int]:
    return await generate_all_recommendations(session)
