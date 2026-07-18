from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import event

from app.models.entities import EtfPriceHistory, TradableEtf
from app.services.short_etf.data import compute_etf_metric


@pytest.mark.asyncio
async def test_metric_reads_only_latest_61_decision_eligible_sessions(app) -> None:
    code = "510888"
    start = date(2026, 1, 1)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "etf_price_history" in statement.lower():
            statements.append(statement.lower())

    event.listen(app.state.db.engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with app.state.db.session() as session:
            session.add(
                TradableEtf(
                    code=code,
                    name="ETF-510888",
                    exchange="SH",
                    theme_tags_json=[],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=False,
                )
            )
            for offset in range(90):
                current = start + timedelta(days=offset)
                close = 1.0 + offset * 0.01
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=current,
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        volume=1.0,
                        turnover=100_000_000.0,
                        pct_change=0.1,
                        research_adjusted_value=close,
                        research_price_basis="total_return_adjusted",
                        data_provider="eastmoney",
                        provider_version="fixture-hfq-v1",
                        adjustment_version="fixture-hfq-v1",
                        decision_eligible=True,
                    )
                )
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=90),
                    open=99.0,
                    high=99.0,
                    low=99.0,
                    close=99.0,
                    volume=1.0,
                    turnover=1.0,
                    pct_change=9.9,
                    data_provider="sina",
                    decision_eligible=False,
                )
            )
            await session.commit()
            statements.clear()

            metric = await compute_etf_metric(
                session,
                code,
                start + timedelta(days=90),
                commit=False,
            )

            assert metric is not None
            assert metric.return_60d is not None
            assert metric.return_60d < 1.0
            assert any("decision_eligible" in statement for statement in statements)
            assert any(" limit " in statement for statement in statements)
    finally:
        event.remove(app.state.db.engine.sync_engine, "before_cursor_execute", capture)
