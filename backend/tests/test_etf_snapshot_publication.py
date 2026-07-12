from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models.entities import (
    EtfPriceHistory,
    EtfUniverseMembership,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
)
from app.services.short_research.snapshot_publication import (
    SnapshotPublicationError,
    build_etf_coverage_barrier,
    publish_full_snapshot,
)


async def _seed_publishable_run(
    app,
    *,
    scope_kind: str = "full",
    coverage_ratio: float = 1.0,
    missing_price_code: str | None = None,
) -> int:
    async with app.state.db.session() as session:
        session.add_all(
            [
                TradableEtf(
                    code="159915",
                    name="创业板ETF",
                    exchange="SZ",
                    theme_tags_json=["科技"],
                    trading_rule_label="T+1",
                    asset_class="sector",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
                TradableEtf(
                    code="510300",
                    name="沪深300ETF",
                    exchange="SH",
                    theme_tags_json=["宽基"],
                    trading_rule_label="T+1",
                    asset_class="broad_index",
                    is_short_term_eligible=True,
                    is_watchlist=True,
                ),
            ]
        )
        await session.flush()
        for code in ("159915", "510300"):
            session.add(EtfUniverseMembership(etf_code=code, effective_from=date(2025, 1, 1), source="fixture"))
            if code != missing_price_code:
                session.add(
                    EtfPriceHistory(
                        etf_code=code,
                        trade_date=date(2026, 1, 2),
                        open=1.0,
                        high=1.1,
                        low=0.9,
                        close=1.0,
                        volume=1_000_000,
                        turnover=100_000_000,
                        pct_change=0.0,
                        research_adjusted_value=1.0,
                        research_price_basis="total_return_adjusted",
                        decision_eligible=True,
                    )
                )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 1, 2),
            config_json={"scope": "fixture"},
            summary_json={"item_count": 2},
            scope_kind=scope_kind,
            scope_hash="scope-hash",
            universe_snapshot_hash="universe-hash",
            input_snapshot_hash="input-hash",
            score_version="final_score_v3",
            rule_version="ranking_rule_v3",
            ranking_contract_hash="contract-hash",
            score_field="ranking_score",
            data_cutoff=datetime(2026, 1, 2, 15, 30),
            as_of_trade_date=date(2026, 1, 2),
            price_basis="total_return_adjusted",
            expected_item_count=2,
            eligible_item_count=2,
            coverage_ratio=coverage_ratio,
            idempotency_key="fixture-inputs",
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="159915",
                    rank=1,
                    global_rank=1,
                    total_score=80.0,
                    ranking_score=80.0,
                    score_eligible=True,
                    conclusion="观察",
                ),
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code="510300",
                    rank=2,
                    global_rank=2,
                    total_score=70.0,
                    ranking_score=70.0,
                    score_eligible=True,
                    conclusion="观察",
                ),
            ]
        )
        await session.commit()
        return run.id


@pytest.mark.asyncio
async def test_full_snapshot_publication_is_idempotent_and_marks_run_published(app) -> None:
    run_id = await _seed_publishable_run(app)

    async with app.state.db.session() as session:
        first = await publish_full_snapshot(session, run_id=run_id)
        second = await publish_full_snapshot(session, run_id=run_id)
        assert first.id == second.id
        assert first.publication_state == "published"
        assert first.published_at is not None
        assert first.summary_json["coverage"] == {
            "expected_codes": ["159915", "510300"],
            "included_codes": ["159915", "510300"],
            "excluded": [],
            "expected_count": 2,
            "included_count": 2,
            "coverage_ratio": 1.0,
        }


@pytest.mark.asyncio
async def test_partial_or_undercovered_snapshot_cannot_publish(app) -> None:
    run_id = await _seed_publishable_run(app, coverage_ratio=0.5)

    async with app.state.db.session() as session:
        with pytest.raises(SnapshotPublicationError, match="coverage"):
            await publish_full_snapshot(session, run_id=run_id)
        run = await session.scalar(select(ShortResearchSignalRun).where(ShortResearchSignalRun.id == run_id))

    assert run is not None
    assert run.publication_state is None
    assert run.published_at is None


@pytest.mark.asyncio
async def test_coverage_barrier_reports_each_same_date_data_exclusion(app) -> None:
    await _seed_publishable_run(app, missing_price_code="510300")

    async with app.state.db.session() as session:
        barrier = await build_etf_coverage_barrier(session, as_of_trade_date=date(2026, 1, 2))

    assert barrier.to_dict() == {
        "expected_codes": ["159915", "510300"],
        "included_codes": ["159915"],
        "excluded": [{"asset_code": "510300", "reason": "missing_trade_date_price"}],
        "expected_count": 2,
        "included_count": 1,
        "coverage_ratio": 0.5,
    }


@pytest.mark.asyncio
async def test_coverage_barrier_rejects_previous_trade_date_price(app) -> None:
    await _seed_publishable_run(app, missing_price_code="510300")
    async with app.state.db.session() as session:
        session.add(
            EtfPriceHistory(
                etf_code="510300",
                trade_date=date(2026, 1, 1),
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.0,
                volume=1_000_000,
                turnover=100_000_000,
                pct_change=0.0,
                research_adjusted_value=1.0,
                research_price_basis="total_return_adjusted",
                decision_eligible=True,
            )
        )
        await session.commit()
        barrier = await build_etf_coverage_barrier(session, as_of_trade_date=date(2026, 1, 2))

    assert barrier.excluded == [{"asset_code": "510300", "reason": "missing_trade_date_price"}]


@pytest.mark.asyncio
async def test_coverage_barrier_rejects_incompatible_provider_price_basis(app) -> None:
    await _seed_publishable_run(app)
    async with app.state.db.session() as session:
        row = await session.scalar(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code == "510300",
                EtfPriceHistory.trade_date == date(2026, 1, 2),
            )
        )
        assert row is not None
        row.research_price_basis = "raw_ohlc"
        await session.commit()
        barrier = await build_etf_coverage_barrier(session, as_of_trade_date=date(2026, 1, 2))

    assert barrier.excluded == [{"asset_code": "510300", "reason": "incompatible_research_price_basis"}]


@pytest.mark.asyncio
async def test_published_snapshot_identity_and_item_ranks_are_immutable(app) -> None:
    run_id = await _seed_publishable_run(app)

    async with app.state.db.session() as session:
        await publish_full_snapshot(session, run_id=run_id)
        run = await session.get(ShortResearchSignalRun, run_id)
        assert run is not None
        run.ranking_contract_hash = "changed-contract"
        with pytest.raises(ValueError, match="published.*immutable"):
            await session.commit()
        await session.rollback()

    async with app.state.db.session() as session:
        item = await session.scalar(
            select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run_id)
        )
        assert item is not None
        item.global_rank = 99
        item.ranking_score = 99.0
        with pytest.raises(ValueError, match="published.*immutable"):
            await session.commit()
        await session.rollback()
