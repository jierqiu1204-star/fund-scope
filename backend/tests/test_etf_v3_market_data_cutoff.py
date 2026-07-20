from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event, select

from app.models.entities import EtfDataHealth, EtfPriceHistory, TradableEtf
from app.services.short_research.service import (
    _available_assets,
    _with_final_score_v2,
    _with_final_score_v3_shadow,
    _with_opportunity_scores,
    _with_sector_trend_scores,
    compute_asset,
    compute_etf_snapshot_assets,
)


async def _seed_snapshot_batch(app, *, count: int = 5) -> tuple[list[str], date, datetime]:
    as_of_date = date(2026, 6, 18)
    decision_cutoff = datetime(2026, 6, 18, 15, 0)
    provider_version = "eastmoney.push2his.kline.hfq_v1"
    codes = [f"5111{index:02d}" for index in range(count)]
    async with app.state.db.session() as session:
        for code_index, code in enumerate(codes):
            session.add(
                TradableEtf(
                    code=code,
                    name=f"批量知识时点ETF{code}",
                    exchange="SH",
                    theme_tags_json=["批量测试"],
                    trading_rule_label="证券账户 T+1 ETF",
                    asset_class="broad",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                )
            )
            start = as_of_date - timedelta(days=181)
            for offset in range(181):
                trade_date = start + timedelta(days=offset)
                close = 1.0 + code_index * 0.01 + offset * 0.001
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=trade_date,
                        open=close,
                        high=close * 1.01,
                        low=close * 0.99,
                        close=close,
                        volume=1_000_000.0,
                        turnover=100_000_000.0 + code_index * 1_000_000.0,
                        pct_change=0.1,
                        research_adjusted_value=close,
                        research_price_basis="total_return_adjusted",
                        data_provider="eastmoney",
                        provider_version=provider_version,
                        source_timestamp=datetime(2026, 6, 18, 6, 30),
                        adjustment_version=provider_version,
                        decision_eligible=True,
                    )
                )
            session.add(
                EtfDataHealth(
                    etf_code=code,
                    status="success",
                    provider="eastmoney",
                    latest_price_date=as_of_date - timedelta(days=1),
                    successful_rows=181,
                    consecutive_failures=3 if code_index == count - 1 else 0,
                    last_attempted_at=datetime(2026, 6, 18, 6, 30),
                    last_success_at=datetime(2026, 6, 18, 6, 30),
                )
            )
        session.add(
            EtfPriceHistory(
                etf_code=codes[0],
                trade_date=as_of_date,
                open=1000.0,
                high=1000.0,
                low=1000.0,
                close=1000.0,
                volume=1_000_000.0,
                turnover=1_000_000_000.0,
                pct_change=1000.0,
                research_adjusted_value=1000.0,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version=provider_version,
                source_timestamp=datetime(2026, 6, 18, 8, 0),
                adjustment_version=provider_version,
                decision_eligible=True,
            )
        )
        await session.commit()
    return codes, as_of_date, decision_cutoff


@pytest.mark.asyncio
async def test_v3_snapshot_ignores_adjusted_price_learned_after_data_cutoff(app) -> None:
    code = "510088"
    as_of_date = date(2026, 6, 18)
    decision_cutoff = datetime(2026, 6, 18, 15, 0)
    provider_version = "eastmoney.push2his.kline.hfq_v1"
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name="知识时点测试ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="broad",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        start = as_of_date - timedelta(days=61)
        for offset in range(61):
            trade_date = start + timedelta(days=offset)
            close = 1.0 + offset * 0.002
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=trade_date,
                    open=close,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=1_000_000.0,
                    turnover=100_000_000.0,
                    pct_change=0.2,
                    research_adjusted_value=close,
                    research_price_basis="total_return_adjusted",
                    data_provider="eastmoney",
                    provider_version=provider_version,
                    source_timestamp=datetime(2026, 6, 18, 6, 30),
                    adjustment_version=provider_version,
                    decision_eligible=True,
                )
            )
        await session.commit()

        before = (
            await compute_etf_snapshot_assets(
                session,
                codes=[code],
                as_of_date=as_of_date,
                decision_cutoff=decision_cutoff,
            )
        )[0]
        session.add(
            EtfPriceHistory(
                etf_code=code,
                trade_date=as_of_date,
                open=1000.0,
                high=1000.0,
                low=1000.0,
                close=1000.0,
                volume=1_000_000.0,
                turnover=1_000_000_000.0,
                pct_change=1000.0,
                research_adjusted_value=1000.0,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version=provider_version,
                # 08:00 UTC is 16:00 Asia/Shanghai, after the decision cutoff.
                source_timestamp=datetime(2026, 6, 18, 8, 0),
                adjustment_version=provider_version,
                decision_eligible=True,
            )
        )
        await session.commit()

        after = (
            await compute_etf_snapshot_assets(
                session,
                codes=[code],
                as_of_date=as_of_date,
                decision_cutoff=decision_cutoff,
            )
        )[0]

    assert after.latest_date == before.latest_date
    assert after.latest_value == before.latest_value
    assert after.metrics["return_20d"] == before.metrics["return_20d"]
    assert after.score_breakdown["final_score_v3_shadow"] == before.score_breakdown["final_score_v3_shadow"]


@pytest.mark.asyncio
async def test_v3_snapshot_uses_data_cutoff_for_prices_and_market_cutoff_for_quotes(app) -> None:
    code = "510089"
    as_of_date = date(2026, 6, 18)
    market_cutoff = datetime(2026, 6, 18, 15, 0)
    data_cutoff = datetime(2026, 6, 18, 21, 0)
    provider_version = "eastmoney.push2his.kline.hfq_v1"
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name="双截止测试ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="broad",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        for offset in range(61):
            trade_date = as_of_date - timedelta(days=60 - offset)
            close = 1.0 + offset * 0.002
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=trade_date,
                    open=close,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=1_000_000.0,
                    turnover=100_000_000.0,
                    pct_change=0.2,
                    research_adjusted_value=close,
                    research_price_basis="total_return_adjusted",
                    data_provider="eastmoney",
                    provider_version=provider_version,
                    # 12:00 UTC is 20:00 Asia/Shanghai: after market cutoff, before data cutoff.
                    source_timestamp=datetime(2026, 6, 18, 12, 0),
                    adjustment_version=provider_version,
                    decision_eligible=True,
                )
            )
        await session.commit()

        assets = await compute_etf_snapshot_assets(
            session,
            codes=[code],
            as_of_date=as_of_date,
            decision_cutoff=market_cutoff,
            data_cutoff=data_cutoff,
        )

    assert assets[0].latest_date == as_of_date
    assert assets[0].usable_days == 61


@pytest.mark.asyncio
async def test_v3_snapshot_batch_prefetch_keeps_sequential_output_and_cutoff_semantics(app) -> None:
    codes, as_of_date, decision_cutoff = await _seed_snapshot_batch(app)
    async with app.state.db.session() as session:
        candidates = await _available_assets(session, asset_type="etf", codes=codes)
        expected = [
            await compute_asset(
                session,
                candidate,
                as_of_date=as_of_date,
                data_cutoff=decision_cutoff,
            )
            for candidate in candidates
        ]
        expected = _with_final_score_v2(expected)
        expected = _with_sector_trend_scores(expected)
        expected = await _with_opportunity_scores(session, expected, as_of_date)
        expected = await _with_final_score_v3_shadow(
            session,
            expected,
            as_of_date,
            decision_cutoff=decision_cutoff,
        )

        actual = await compute_etf_snapshot_assets(
            session,
            codes=codes,
            as_of_date=as_of_date,
            decision_cutoff=decision_cutoff,
        )

    actual_without_history_digest = [
        replace(
            asset,
            metrics={
                key: value
                for key, value in asset.metrics.items()
                if key != "v3_adjusted_price_history_digest"
                and not key.startswith("research_")
                and key != "history_confidence_tier"
            },
            score_breakdown={
                key: value
                for key, value in asset.score_breakdown.items()
                if key != "daily_reconstructable_v1"
            },
        )
        for asset in actual
    ]
    expected_without_research_metadata = [
        replace(
            asset,
            metrics={
                key: value
                for key, value in asset.metrics.items()
                if not key.startswith("research_")
                and key != "history_confidence_tier"
            },
        )
        for asset in expected
    ]
    assert actual_without_history_digest == expected_without_research_metadata
    assert [asset.metadata.code for asset in actual] == sorted(codes)
    assert actual[0].latest_date == as_of_date - timedelta(days=1)


@pytest.mark.asyncio
async def test_v3_snapshot_query_count_is_constant_as_etf_count_grows(app) -> None:
    codes, as_of_date, decision_cutoff = await _seed_snapshot_batch(app)
    async with app.state.db.session() as session:
        bind = session.get_bind()
        statements: list[str] = []

        def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(bind, "before_cursor_execute", count_selects)
        try:
            await compute_etf_snapshot_assets(
                session,
                codes=codes[:1],
                as_of_date=as_of_date,
                decision_cutoff=decision_cutoff,
            )
            one_etf_queries = len(statements)
            statements.clear()
            await compute_etf_snapshot_assets(
                session,
                codes=codes,
                as_of_date=as_of_date,
                decision_cutoff=decision_cutoff,
            )
            many_etf_queries = len(statements)
        finally:
            event.remove(bind, "before_cursor_execute", count_selects)

    assert many_etf_queries <= one_etf_queries + 1
    assert many_etf_queries <= 10


@pytest.mark.asyncio
async def test_v3_snapshot_history_digest_only_tracks_consumed_rows(app) -> None:
    codes, as_of_date, decision_cutoff = await _seed_snapshot_batch(app, count=1)
    code = codes[0]
    async with app.state.db.session() as session:
        before = (
            await compute_etf_snapshot_assets(
                session,
                codes=[code],
                as_of_date=as_of_date,
                decision_cutoff=decision_cutoff,
            )
        )[0].metrics["v3_adjusted_price_history_digest"]
        assert isinstance(before, str)
        assert re.fullmatch(r"[0-9a-f]{64}", before)

        future_row = await session.scalar(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code == code,
                EtfPriceHistory.trade_date == as_of_date,
            )
        )
        unused_row = await session.scalar(
            select(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code == code)
            .order_by(EtfPriceHistory.trade_date.asc())
            .limit(1)
        )
        assert future_row is not None
        assert unused_row is not None
        future_row.research_adjusted_value = 2_000.0
        unused_row.research_adjusted_value = float(unused_row.research_adjusted_value or 0) + 10.0
        await session.commit()

        after_unconsumed_changes = (
            await compute_etf_snapshot_assets(
                session,
                codes=[code],
                as_of_date=as_of_date,
                decision_cutoff=decision_cutoff,
            )
        )[0].metrics["v3_adjusted_price_history_digest"]
        assert after_unconsumed_changes == before

        consumed_row = await session.scalar(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code == code,
                EtfPriceHistory.trade_date == as_of_date - timedelta(days=1),
            )
        )
        assert consumed_row is not None
        consumed_row.source_timestamp = datetime(2026, 6, 18, 6, 31)
        await session.commit()

        after_provenance_change = (
            await compute_etf_snapshot_assets(
                session,
                codes=[code],
                as_of_date=as_of_date,
                decision_cutoff=decision_cutoff,
            )
        )[0].metrics["v3_adjusted_price_history_digest"]
        assert after_provenance_change != before

        consumed_row.research_adjusted_value = float(consumed_row.research_adjusted_value or 0) + 0.25
        await session.commit()

        after_consumed_change = (
            await compute_etf_snapshot_assets(
                session,
                codes=[code],
                as_of_date=as_of_date,
                decision_cutoff=decision_cutoff,
            )
        )[0].metrics["v3_adjusted_price_history_digest"]

    assert after_consumed_change != after_provenance_change
