from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.models.entities import Stock, StockFundamental, StockMetric
from app.services.recommendations.engine import recompute_stock_metrics
from app.services.recommendations.stock_adjusted_prices import (
    canonical_ashare_code,
    compatible_adjusted_prices,
)


async def _create_fact_table(session) -> None:
    await session.execute(
        text(
            """
            CREATE TABLE ashare_adjusted_price_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_code VARCHAR(32) NOT NULL,
                trade_date DATE NOT NULL,
                adjusted_close FLOAT NOT NULL,
                amount FLOAT NOT NULL,
                price_basis VARCHAR(64) NOT NULL,
                provider VARCHAR(64) NOT NULL,
                adjustment_version VARCHAR(128) NOT NULL,
                revision_id VARCHAR(128) NOT NULL,
                received_at DATETIME,
                historical_research_only BOOLEAN NOT NULL,
                decision_eligible BOOLEAN NOT NULL
            )
            """
        )
    )


@pytest.mark.asyncio
async def test_recommendation_metrics_use_authoritative_adjusted_facts(app) -> None:
    metric_date = date.today()
    async with app.state.db.session() as session:
        await _create_fact_table(session)
        session.add(
            Stock(
                code="600519.SH",
                exchange="SH",
                name="Kweichow Moutai",
                industry="consumer",
                is_candidate=True,
            )
        )
        session.add(
            StockFundamental(
                stock_code="600519.SH",
                report_date=metric_date,
                pe=24,
                pb=6,
                roe=28,
                gross_margin=90,
                debt_to_asset=18,
                operating_cashflow=1000,
                dividend_yield=2,
            )
        )
        for index, close in enumerate((100.0, 110.0, 120.0)):
            trade_date = metric_date - timedelta(days=2 - index)
            await session.execute(
                text(
                    """
                    INSERT INTO ashare_adjusted_price_facts
                        (asset_code, trade_date, adjusted_close, amount, price_basis,
                         provider, adjustment_version, revision_id, received_at,
                         historical_research_only, decision_eligible)
                    VALUES
                        (:asset_code, :trade_date, :close, :amount, :price_basis,
                         :provider, :adjustment_version, :revision_id, :received_at,
                         :historical_only, :eligible)
                    """
                ),
                {
                    "asset_code": "600519",
                    "trade_date": trade_date,
                    "close": close,
                    "amount": 1_000_000_000.0,
                    "price_basis": "total_return_adjusted",
                    "provider": "eastmoney",
                    "adjustment_version": "hfq-v1",
                    "revision_id": f"r{index}",
                    "received_at": datetime.combine(metric_date, datetime.max.time()),
                    "historical_only": False,
                    "eligible": True,
                },
            )
        await session.commit()

        assert canonical_ashare_code("600519.SH") == "600519"
        points = await compatible_adjusted_prices(
            session,
            stock_code="600519.SH",
            as_of_date=metric_date,
        )
        assert [point.close for point in points] == [100.0, 110.0, 120.0]

        assert await recompute_stock_metrics(session, metric_date) >= 1
        metric = await session.scalar(
            select(StockMetric).where(
                StockMetric.stock_code == "600519.SH",
                StockMetric.metric_date == metric_date,
            )
        )
        assert metric is not None
        assert metric.data_quality_json["source"] == "ashare_adjusted_price_facts"
        assert metric.data_quality_json["price_points"] == 3


@pytest.mark.asyncio
async def test_adjusted_price_reader_rejects_ineligible_and_raw_rows(app) -> None:
    metric_date = date.today()
    async with app.state.db.session() as session:
        await _create_fact_table(session)
        await session.execute(
            text(
                """
                INSERT INTO ashare_adjusted_price_facts
                    (asset_code, trade_date, adjusted_close, amount, price_basis,
                     provider, adjustment_version, revision_id, received_at,
                     historical_research_only, decision_eligible)
                VALUES ('600519', :trade_date, 100, 10, 'raw', 'sina', 'raw',
                        'bad', :received_at, :historical_only, :eligible)
                """
            ),
            {
                "trade_date": metric_date,
                "received_at": datetime.combine(metric_date, datetime.max.time()),
                "historical_only": False,
                "eligible": False,
            },
        )
        assert not await compatible_adjusted_prices(
            session,
            stock_code="600519.SH",
            as_of_date=metric_date,
        )
