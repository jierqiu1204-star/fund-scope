from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from statistics import pstdev
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    Fund,
    FundMetric,
    FundNavHistory,
    HoldingsSnapshot,
    IndexValuationHistory,
    NewsItem,
    NewsSummary,
    RecommendationItem,
    RecommendationProfile,
    RecommendationRun,
    Stock,
    StockFundamental,
    StockMetric,
    utcnow,
)
from app.services.recommendations.constants import (
    ASSET_TYPE_FUND,
    ASSET_TYPE_STOCK,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    SAFE_LABELS,
)
from app.services.recommendations.scoring import (
    FundCandidateInput,
    StockCandidateInput,
    score_fund_candidate,
    score_stock_candidate,
)
from app.services.recommendations.stock_adjusted_prices import (
    StockAdjustedPricePoint,
    compatible_adjusted_prices,
)
from app.services.recommendations.stock_data import (
    default_stock_fundamentals,
    default_stock_universe,
)


def _returns(values: Sequence[float]) -> list[float]:
    return [
        (values[index] / values[index - 1] - 1.0)
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]


def _annualized_volatility(nav_values: Sequence[float]) -> float | None:
    daily_returns = _returns(nav_values)
    if len(daily_returns) < 2:
        return None
    return round(pstdev(daily_returns) * math.sqrt(252) * 100, 2)


def _max_drawdown(nav_values: Sequence[float]) -> float | None:
    if len(nav_values) < 2:
        return None
    peak = nav_values[0]
    max_drop = 0.0
    for value in nav_values:
        peak = max(peak, value)
        if peak > 0:
            max_drop = min(max_drop, (value / peak) - 1.0)
    return round(abs(max_drop) * 100, 2)


def _return_1y(nav_rows: Sequence[FundNavHistory]) -> float | None:
    if len(nav_rows) < 2:
        return None
    first = nav_rows[0].nav
    last = nav_rows[-1].nav
    if first <= 0:
        return None
    return round((last / first - 1.0) * 100, 2)


def _fee_rate_for_category(category: str) -> float:
    if category == "money_market":
        return 0.2
    if category == "bond":
        return 0.35
    return 0.6


async def ensure_default_recommendation_profiles(session: AsyncSession) -> None:
    for asset_type, name in [
        (ASSET_TYPE_FUND, "Default fund screening"),
        (ASSET_TYPE_STOCK, "Default stock watchlist screening"),
    ]:
        existing = await session.scalar(
            select(RecommendationProfile).where(
                RecommendationProfile.asset_type == asset_type,
                RecommendationProfile.is_default.is_(True),
            )
        )
        if existing is None:
            session.add(
                RecommendationProfile(
                    name=name,
                    asset_type=asset_type,
                    risk_level="balanced",
                    is_default=True,
                    config_json={},
                )
            )


async def ensure_default_stock_seed_data(session: AsyncSession, as_of_date: date) -> None:
    for row in default_stock_universe():
        existing = await session.scalar(select(Stock).where(Stock.code == row["code"]))
        if existing is None:
            session.add(
                Stock(
                    code=row["code"],
                    exchange=row["exchange"],
                    name=row["name"],
                    industry=row["industry"],
                    is_candidate=True,
                )
            )
    await session.flush()


    for fundamental_row in default_stock_fundamentals(as_of_date):
        existing = await session.scalar(
            select(StockFundamental).where(
                StockFundamental.stock_code == fundamental_row["stock_code"],
                StockFundamental.report_date == fundamental_row["report_date"],
            )
        )
        if existing is None:
            session.add(StockFundamental(**fundamental_row))


async def recompute_fund_metrics(session: AsyncSession, as_of_date: date | None = None) -> int:
    metric_date = as_of_date or date.today()
    funds = (
        await session.scalars(select(Fund).where(Fund.is_watchlist.is_(True)).order_by(Fund.code.asc()))
    ).all()
    rows_upserted = 0
    for fund in funds:
        nav_rows = (
            await session.scalars(
                select(FundNavHistory)
                .where(
                    FundNavHistory.fund_code == fund.code,
                    FundNavHistory.nav_date >= metric_date - timedelta(days=370),
                    FundNavHistory.nav_date <= metric_date,
                )
                .order_by(FundNavHistory.nav_date.asc())
            )
        ).all()
        nav_values = [row.nav for row in nav_rows]
        missing_metrics: list[str] = []
        return_1y = _return_1y(nav_rows)
        volatility_1y = _annualized_volatility(nav_values)
        max_drawdown_1y = _max_drawdown(nav_values)
        if return_1y is None:
            missing_metrics.append("return_1y")
        if volatility_1y is None:
            missing_metrics.append("volatility_1y")
        if max_drawdown_1y is None:
            missing_metrics.append("max_drawdown_1y")

        critical_news_count = await session.scalar(
            select(func.count())
            .select_from(NewsItem)
            .join(NewsSummary, NewsSummary.news_item_id == NewsItem.id)
            .where(
                NewsItem.fund_code == fund.code,
                NewsItem.published_at >= utcnow() - timedelta(days=90),
                NewsSummary.event_type.in_(["manager_change", "strategy_change"]),
            )
        )
        data_quality = {
            "missing_metrics": missing_metrics,
            "nav_points": len(nav_rows),
            "source": "fund_nav_history",
        }
        payload = {
            "return_1y": return_1y,
            "volatility_1y": volatility_1y,
            "max_drawdown_1y": max_drawdown_1y,
            "tracking_error": None,
            "fee_rate": _fee_rate_for_category(fund.category),
            "fund_size": round(50.0 + fund.target_allocation * 250.0, 2),
            "news_risk_score": min(float(critical_news_count or 0) * 40.0, 100.0),
            "data_quality_json": data_quality,
        }
        existing = await session.scalar(
            select(FundMetric).where(FundMetric.fund_code == fund.code, FundMetric.metric_date == metric_date)
        )
        if existing is None:
            session.add(FundMetric(fund_code=fund.code, metric_date=metric_date, **payload))
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
        rows_upserted += 1
    return rows_upserted


def _stock_metric_scores(
    fundamental: StockFundamental,
    price_rows: Sequence[StockAdjustedPricePoint],
) -> dict[str, Any]:
    closes = [row.close for row in price_rows]
    returns = _returns(closes)
    volatility = round(pstdev(returns) * math.sqrt(252) * 100, 2) if len(returns) >= 2 else None
    momentum = round((closes[-1] / closes[0] - 1.0) * 100, 2) if len(closes) >= 2 and closes[0] else None
    latest_turnover = price_rows[-1].turnover if price_rows else None
    return {
        "momentum_6m": momentum,
        "volatility_1y": volatility,
        "turnover": latest_turnover,
        "quality_score": round(
            ((fundamental.roe or 0) * 2.0)
            + ((fundamental.gross_margin or 0) * 0.3)
            + max(0.0, 80.0 - (fundamental.debt_to_asset or 80.0)) * 0.35,
            2,
        ),
        "valuation_score": round(
            max(0.0, 100.0 - (fundamental.pe or 60.0) * 1.2) * 0.65
            + max(0.0, 100.0 - (fundamental.pb or 10.0) * 8.0) * 0.35,
            2,
        ),
        "momentum_score": None if momentum is None else max(0.0, min(100.0, 50.0 + momentum)),
        "risk_score": None if volatility is None else max(0.0, min(100.0, 100.0 - volatility)),
        "liquidity_score": None
        if latest_turnover is None
        else max(0.0, min(100.0, latest_turnover / 1_000_000_000.0 * 100.0)),
    }


async def recompute_stock_metrics(session: AsyncSession, as_of_date: date | None = None) -> int:
    metric_date = as_of_date or date.today()
    await ensure_default_stock_seed_data(session, metric_date)
    stocks = (
        await session.scalars(select(Stock).where(Stock.is_candidate.is_(True)).order_by(Stock.code.asc()))
    ).all()
    rows_upserted = 0
    for stock in stocks:
        fundamental = await session.scalar(
            select(StockFundamental)
            .where(
                StockFundamental.stock_code == stock.code,
                StockFundamental.report_date <= metric_date,
            )
            .order_by(StockFundamental.report_date.desc())
        )
        price_rows = await compatible_adjusted_prices(
            session,
            stock_code=stock.code,
            as_of_date=metric_date,
        )
        missing_metrics: list[str] = []
        if fundamental is None:
            missing_metrics.extend(["pe", "pb", "roe"])
            payload: dict[str, Any] = {
                "quality_score": None,
                "valuation_score": None,
                "momentum_score": None,
                "risk_score": None,
                "liquidity_score": None,
                "data_quality_json": {"missing_metrics": missing_metrics, "source": "stock_fundamentals"},
            }
        else:
            scores = _stock_metric_scores(fundamental, price_rows)
            for key in ["momentum_6m", "volatility_1y", "turnover"]:
                if scores[key] is None:
                    missing_metrics.append(key)
            payload = {
                "quality_score": scores["quality_score"],
                "valuation_score": scores["valuation_score"],
                "momentum_score": scores["momentum_score"],
                "risk_score": scores["risk_score"],
                "liquidity_score": scores["liquidity_score"],
                "data_quality_json": {
                    "missing_metrics": missing_metrics,
                    "momentum_6m": scores["momentum_6m"],
                    "volatility_1y": scores["volatility_1y"],
                    "turnover": scores["turnover"],
                    "price_points": len(price_rows),
                    "source": "ashare_adjusted_price_facts",
                },
            }
        existing = await session.scalar(
            select(StockMetric).where(StockMetric.stock_code == stock.code, StockMetric.metric_date == metric_date)
        )
        if existing is None:
            session.add(StockMetric(stock_code=stock.code, metric_date=metric_date, **payload))
        else:
            for key, value in payload.items():
                setattr(existing, key, value)
        rows_upserted += 1
    return rows_upserted


async def recompute_all_metrics(session: AsyncSession, as_of_date: date | None = None) -> dict[str, int]:
    await ensure_default_recommendation_profiles(session)
    metric_date = as_of_date or date.today()
    fund_metrics = await recompute_fund_metrics(session, metric_date)
    stock_metrics = await recompute_stock_metrics(session, metric_date)
    await session.commit()
    return {"fund_metrics": fund_metrics, "stock_metrics": stock_metrics}


async def _default_profile(session: AsyncSession, asset_type: str) -> RecommendationProfile:
    await ensure_default_recommendation_profiles(session)
    profile = await session.scalar(
        select(RecommendationProfile).where(
            RecommendationProfile.asset_type == asset_type,
            RecommendationProfile.is_default.is_(True),
        )
    )
    assert profile is not None
    return profile


async def _portfolio_allocations(session: AsyncSession) -> dict[str, float]:
    latest_date = await session.scalar(select(func.max(HoldingsSnapshot.snapshot_date)))
    if latest_date is None:
        return {}
    rows = (
        await session.execute(
            select(HoldingsSnapshot.fund_code, HoldingsSnapshot.market_value).where(
                HoldingsSnapshot.snapshot_date == latest_date
            )
        )
    ).all()
    total = sum(float(value or 0.0) for _, value in rows)
    if total <= 0:
        return {}
    return {fund_code: float(value or 0.0) / total for fund_code, value in rows}


async def _latest_fund_metric(session: AsyncSession, fund_code: str, as_of_date: date) -> FundMetric | None:
    metric = await session.scalar(
        select(FundMetric)
        .where(FundMetric.fund_code == fund_code, FundMetric.metric_date <= as_of_date)
        .order_by(FundMetric.metric_date.desc())
    )
    return metric


async def _latest_index_percentile(session: AsyncSession, index_code: str | None, as_of_date: date) -> float | None:
    if index_code is None:
        return None
    row = await session.scalar(
        select(IndexValuationHistory)
        .where(
            IndexValuationHistory.index_code == index_code,
            IndexValuationHistory.valuation_date <= as_of_date,
        )
        .order_by(IndexValuationHistory.valuation_date.desc())
    )
    return row.pe_percentile if row is not None else None


async def _fund_scores(session: AsyncSession, as_of_date: date) -> list[dict[str, Any]]:
    allocations = await _portfolio_allocations(session)
    funds = (
        await session.scalars(select(Fund).where(Fund.is_watchlist.is_(True)).order_by(Fund.code.asc()))
    ).all()
    results: list[dict[str, Any]] = []
    for fund in funds:
        metric = await _latest_fund_metric(session, fund.code, as_of_date)
        if metric is None:
            await recompute_fund_metrics(session, as_of_date)
            metric = await _latest_fund_metric(session, fund.code, as_of_date)
        missing = list(metric.data_quality_json.get("missing_metrics", [])) if metric is not None else []
        pe_percentile = await _latest_index_percentile(session, fund.tracking_index_code, as_of_date)
        if fund.tracking_index_code and pe_percentile is None:
            missing.append("pe_percentile")
        scored = score_fund_candidate(
            FundCandidateInput(
                code=fund.code,
                name=fund.name,
                tracking_index_code=fund.tracking_index_code,
                category=fund.category,
                target_allocation=fund.target_allocation,
                current_allocation=allocations.get(fund.code, 0.0),
                pe_percentile=pe_percentile,
                fee_rate=metric.fee_rate if metric is not None else None,
                fund_size=metric.fund_size if metric is not None else None,
                volatility_1y=metric.volatility_1y if metric is not None else None,
                max_drawdown_1y=metric.max_drawdown_1y if metric is not None else None,
                news_risk_score=metric.news_risk_score if metric is not None else 0.0,
                missing_metrics=sorted(set(missing)),
            )
        )
        results.append(scored.model_dump())
    return results


async def _industry_allocations(session: AsyncSession) -> dict[str, float]:
    stocks = (await session.scalars(select(Stock).where(Stock.is_candidate.is_(True)))).all()
    if not stocks:
        return {}
    counts = Counter(stock.industry for stock in stocks)
    return {industry: count / len(stocks) for industry, count in counts.items()}


async def _stock_scores(session: AsyncSession, as_of_date: date) -> list[dict[str, Any]]:
    await ensure_default_stock_seed_data(session, as_of_date)
    industry_allocations = await _industry_allocations(session)
    stocks = (
        await session.scalars(select(Stock).where(Stock.is_candidate.is_(True)).order_by(Stock.code.asc()))
    ).all()
    results: list[dict[str, Any]] = []
    for stock in stocks:
        metric = await session.scalar(
            select(StockMetric)
            .where(StockMetric.stock_code == stock.code, StockMetric.metric_date <= as_of_date)
            .order_by(StockMetric.metric_date.desc())
        )
        if metric is None:
            await recompute_stock_metrics(session, as_of_date)
            metric = await session.scalar(
                select(StockMetric)
                .where(StockMetric.stock_code == stock.code, StockMetric.metric_date <= as_of_date)
                .order_by(StockMetric.metric_date.desc())
            )
        fundamental = await session.scalar(
            select(StockFundamental)
            .where(
                StockFundamental.stock_code == stock.code,
                StockFundamental.report_date <= as_of_date,
            )
            .order_by(StockFundamental.report_date.desc())
        )
        missing = list(metric.data_quality_json.get("missing_metrics", [])) if metric is not None else []
        scored = score_stock_candidate(
            StockCandidateInput(
                code=stock.code,
                name=stock.name,
                industry=stock.industry,
                pe=fundamental.pe if fundamental is not None else None,
                pb=fundamental.pb if fundamental is not None else None,
                roe=fundamental.roe if fundamental is not None else None,
                gross_margin=fundamental.gross_margin if fundamental is not None else None,
                debt_to_asset=fundamental.debt_to_asset if fundamental is not None else None,
                momentum_6m=metric.data_quality_json.get("momentum_6m") if metric is not None else None,
                volatility_1y=metric.data_quality_json.get("volatility_1y") if metric is not None else None,
                turnover=metric.data_quality_json.get("turnover") if metric is not None else None,
                industry_allocation=industry_allocations.get(stock.industry, 0.0),
                missing_metrics=sorted(set(missing)),
            )
        )
        if scored is not None:
            results.append(scored.model_dump())
    return results


async def generate_recommendations(
    session: AsyncSession,
    asset_type: str,
    as_of_date: date | None = None,
) -> RecommendationRun:
    if asset_type not in {ASSET_TYPE_FUND, ASSET_TYPE_STOCK}:
        raise ValueError(f"Unsupported asset type: {asset_type}")

    run_date = as_of_date or date.today()
    profile = await _default_profile(session, asset_type)
    run = RecommendationRun(
        profile_id=profile.id,
        asset_type=asset_type,
        status=RUN_STATUS_RUNNING,
        as_of_date=run_date,
        started_at=utcnow(),
        data_cutoff_json={"as_of_date": run_date.isoformat()},
        details_json={},
    )
    session.add(run)
    await session.flush()
    try:
        scored_items = (
            await _fund_scores(session, run_date)
            if asset_type == ASSET_TYPE_FUND
            else await _stock_scores(session, run_date)
        )
        scored_items.sort(key=lambda item: (-float(item["total_score"]), str(item["asset_code"])))
        for rank, item in enumerate(scored_items, start=1):
            session.add(
                RecommendationItem(
                    run_id=run.id,
                    asset_code=str(item["asset_code"]),
                    asset_name=str(item["asset_name"]),
                    asset_type=asset_type,
                    rank=rank,
                    total_score=float(item["total_score"]),
                    score_breakdown_json=item["score_breakdown"],
                    rationale_json={**item["rationale"], "safe_label": SAFE_LABELS[asset_type]},
                    risk_flags_json=item["risk_flags"],
                    data_freshness_json=item["data_freshness"],
                )
            )
        run.status = RUN_STATUS_SUCCESS
        run.finished_at = utcnow()
        run.details_json = {"items": len(scored_items)}
        await session.commit()
        return run
    except Exception as exc:
        run.status = RUN_STATUS_FAILED
        run.finished_at = utcnow()
        run.error_message = str(exc)
        await session.commit()
        raise


async def generate_all_recommendations(session: AsyncSession, as_of_date: date | None = None) -> dict[str, int]:
    await ensure_default_recommendation_profiles(session)
    run_date = as_of_date or date.today()
    await recompute_all_metrics(session, run_date)
    fund_run = await generate_recommendations(session, ASSET_TYPE_FUND, run_date)
    stock_run = await generate_recommendations(session, ASSET_TYPE_STOCK, run_date)
    fund_items = await session.scalar(
        select(func.count()).select_from(RecommendationItem).where(RecommendationItem.run_id == fund_run.id)
    )
    stock_items = await session.scalar(
        select(func.count()).select_from(RecommendationItem).where(RecommendationItem.run_id == stock_run.id)
    )
    return {"fund_items": int(fund_items or 0), "stock_items": int(stock_items or 0)}
