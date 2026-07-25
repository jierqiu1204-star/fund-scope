from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.entities import (
    EtfPriceHistory,
    EtfUniverseMembership,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TradableEtf,
)
from app.services.short_research import snapshot_publication
from app.services.short_research.snapshot_publication import (
    EtfCoverageBarrier,
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
    decision_data_item_count: int = 2,
    decision_data_coverage_ratio: float = 1.0,
    score_item_codes: tuple[str, ...] = ("159915", "510300"),
) -> int:
    expected_codes = ["159915", "510300"]
    included_codes = [code for code in expected_codes if code != missing_price_code]
    exclusions = (
        [{"asset_code": missing_price_code, "reason": "missing_trade_date_price"}]
        if missing_price_code is not None
        else []
    )
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
                        data_provider="eastmoney",
                        provider_version="eastmoney.push2his.kline.hfq_v1",
                        source_timestamp=datetime(2026, 1, 2, 7, 0),
                        adjustment_version="eastmoney.push2his.kline.hfq_v1",
                        decision_eligible=True,
                    )
                )
        run = ShortResearchSignalRun(
            status="success",
            as_of_date=date(2026, 1, 2),
            config_json={"scope": "fixture"},
            summary_json={
                "item_count": len(score_item_codes),
                "coverage": {
                    "decision_data": {
                        "expected_codes": expected_codes,
                        "included_codes": included_codes,
                        "excluded": exclusions,
                        "expected_count": len(expected_codes),
                        "included_count": len(included_codes),
                        "coverage_ratio": round(len(included_codes) / len(expected_codes), 6),
                    },
                    "score": {
                        "expected_count": len(expected_codes),
                        "eligible_count": len(score_item_codes),
                        "eligible_codes": list(score_item_codes),
                        "coverage_ratio": round(coverage_ratio, 6),
                        "excluded": [],
                    },
                },
            },
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
            decision_data_item_count=decision_data_item_count,
            decision_data_coverage_ratio=decision_data_coverage_ratio,
            eligible_item_count=len(score_item_codes),
            coverage_ratio=coverage_ratio,
            idempotency_key="fixture-inputs",
        )
        session.add(run)
        await session.flush()
        items = {
            "159915":
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
            "510300":
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
        }
        session.add_all([items[code] for code in score_item_codes])
        await session.flush()
        run.summary_json = {
            **run.summary_json,
            "draft_seal": snapshot_publication.build_snapshot_draft_seal(
                run,
                [items[code] for code in score_item_codes],
            ),
        }
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
            "decision_data": {
                "expected_codes": ["159915", "510300"],
                "included_codes": ["159915", "510300"],
                "excluded": [],
                "expected_count": 2,
                "included_count": 2,
                "coverage_ratio": 1.0,
            },
            "score": {
                "expected_count": 2,
                "eligible_count": 2,
                "eligible_codes": ["159915", "510300"],
                "coverage_ratio": 1.0,
                "excluded": [],
            },
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper_target", ["ranking_score", "metrics", "summary"])
async def test_snapshot_publisher_rejects_draft_content_changed_after_seal(app, tamper_target: str) -> None:
    run_id = await _seed_publishable_run(app)
    assert hasattr(snapshot_publication, "build_snapshot_draft_seal")

    async with app.state.db.session() as session:
        run = await session.get(ShortResearchSignalRun, run_id)
        items = (
            await session.scalars(
                select(ShortResearchSignalItem)
                .where(ShortResearchSignalItem.run_id == run_id)
                .order_by(ShortResearchSignalItem.global_rank.asc())
            )
        ).all()
        assert run is not None
        run.summary_json = {
            **run.summary_json,
            "draft_seal": snapshot_publication.build_snapshot_draft_seal(run, items),
        }
        await session.commit()

        if tamper_target == "ranking_score":
            items[0].ranking_score = 79.0
        elif tamper_target == "metrics":
            items[0].metrics_json = {"tampered": True}
        else:
            run.summary_json = {**run.summary_json, "tampered": True}
        await session.commit()

    async with app.state.db.session() as session:
        with pytest.raises(SnapshotPublicationError, match="draft content seal mismatch"):
            await publish_full_snapshot(session, run_id=run_id)


@pytest.mark.asyncio
async def test_new_run_cannot_be_inserted_as_published(app) -> None:
    async with app.state.db.session() as session:
        session.add(
            ShortResearchSignalRun(
                status="success",
                as_of_date=date(2026, 1, 2),
                publication_state="published",
            )
        )

        with pytest.raises(ValueError, match="cannot be created as published"):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_draft_run_cannot_be_published_outside_snapshot_publisher(app) -> None:
    run_id = await _seed_publishable_run(app)

    async with app.state.db.session() as session:
        run = await session.get(ShortResearchSignalRun, run_id)
        assert run is not None
        run.publication_state = "published"
        run.published_at = datetime(2026, 1, 2, 16, 0)

        with pytest.raises(ValueError, match="authorized snapshot publisher"):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper_target", ["run_identity", "item_content"])
async def test_snapshot_publisher_rejects_content_tampered_after_validation(
    app,
    monkeypatch,
    tamper_target: str,
) -> None:
    run_id = await _seed_publishable_run(app)
    original_validate = snapshot_publication._validate_publishable

    def validate_then_tamper(run, items, *, decision_data_codes) -> None:
        original_validate(run, items, decision_data_codes=decision_data_codes)
        if tamper_target == "run_identity":
            run.ranking_contract_hash = "tampered-contract"
        else:
            items[0].conclusion = "tampered-after-validation"

    monkeypatch.setattr(snapshot_publication, "_validate_publishable", validate_then_tamper)

    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="draft content seal mismatch"):
            await publish_full_snapshot(session, run_id=run_id)

    async with app.state.db.session() as session:
        run = await session.get(ShortResearchSignalRun, run_id)
        item = await session.scalar(
            select(ShortResearchSignalItem)
            .where(ShortResearchSignalItem.run_id == run_id)
            .order_by(ShortResearchSignalItem.global_rank.asc())
        )

    assert run is not None
    assert run.publication_state is None
    assert run.ranking_contract_hash == "contract-hash"
    assert item is not None
    assert item.conclusion == "观察"


@pytest.mark.asyncio
async def test_publication_rejects_mismatched_as_of_and_trade_dates(app) -> None:
    run_id = await _seed_publishable_run(app)
    async with app.state.db.session() as session:
        run = await session.get(ShortResearchSignalRun, run_id)
        assert run is not None
        run.as_of_date = date(2026, 1, 3)
        await session.commit()

    async with app.state.db.session() as session:
        with pytest.raises(SnapshotPublicationError, match="as-of date must match trade date"):
            await publish_full_snapshot(session, run_id=run_id)


@pytest.mark.asyncio
async def test_publication_and_item_mutation_use_matching_parent_first_row_locks(app) -> None:
    run_id = await _seed_publishable_run(app)
    phase = "item_mutation"
    locked_entities: dict[str, set[type]] = {
        "item_mutation": set(),
        "publication": set(),
    }

    def capture_lock(orm_execute_state) -> None:
        if not orm_execute_state.is_select:
            return
        statement = orm_execute_state.statement
        if getattr(statement, "_for_update_arg", None) is None:
            return
        for description in statement.column_descriptions:
            entity = description.get("entity")
            if isinstance(entity, type):
                locked_entities[phase].add(entity)

    event.listen(Session, "do_orm_execute", capture_lock)
    try:
        async with app.state.db.session() as session:
            item = await session.scalar(
                select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run_id)
            )
            assert item is not None
            item.conclusion = "draft mutation"
            await session.flush()
            await session.rollback()

        phase = "publication"
        async with app.state.db.session() as session:
            await publish_full_snapshot(session, run_id=run_id)
    finally:
        event.remove(Session, "do_orm_execute", capture_lock)

    assert ShortResearchSignalRun in locked_entities["item_mutation"]
    assert ShortResearchSignalItem in locked_entities["publication"]


@pytest.mark.asyncio
async def test_partial_or_undercovered_snapshot_cannot_publish(app) -> None:
    run_id = await _seed_publishable_run(app, coverage_ratio=0.5, score_item_codes=("159915",))

    async with app.state.db.session() as session:
        with pytest.raises(SnapshotPublicationError, match="coverage"):
            await publish_full_snapshot(session, run_id=run_id)
        run = await session.scalar(select(ShortResearchSignalRun).where(ShortResearchSignalRun.id == run_id))

    assert run is not None
    assert run.publication_state is None
    assert run.published_at is None


@pytest.mark.asyncio
async def test_full_score_coverage_does_not_hide_low_decision_data_coverage(app) -> None:
    run_id = await _seed_publishable_run(
        app,
        missing_price_code="510300",
        decision_data_item_count=1,
        decision_data_coverage_ratio=0.5,
    )

    async with app.state.db.session() as session:
        with pytest.raises(SnapshotPublicationError, match="decision-data coverage"):
            await publish_full_snapshot(session, run_id=run_id)


@pytest.mark.asyncio
async def test_publication_rejects_same_count_changed_universe_code_set(app, monkeypatch) -> None:
    run_id = await _seed_publishable_run(app)

    async def changed_barrier(_session, *, as_of_trade_date, data_cutoff):
        assert as_of_trade_date == date(2026, 1, 2)
        assert data_cutoff == datetime(2026, 1, 2, 15, 30)
        return EtfCoverageBarrier(
            expected_codes=["159915", "512000"],
            included_codes=["159915", "512000"],
            excluded=[],
        )

    monkeypatch.setattr(snapshot_publication, "build_etf_coverage_barrier", changed_barrier)

    async with app.state.db.session() as session:
        with pytest.raises(SnapshotPublicationError, match="universe code set changed"):
            await publish_full_snapshot(session, run_id=run_id)


@pytest.mark.asyncio
async def test_publication_rejects_same_count_changed_decision_code_set(app, monkeypatch) -> None:
    run_id = await _seed_publishable_run(app)

    async def changed_barrier(_session, *, as_of_trade_date, data_cutoff):
        assert as_of_trade_date == date(2026, 1, 2)
        assert data_cutoff == datetime(2026, 1, 2, 15, 30)
        return EtfCoverageBarrier(
            expected_codes=["159915", "510300"],
            included_codes=["159915", "512000"],
            excluded=[],
        )

    monkeypatch.setattr(snapshot_publication, "build_etf_coverage_barrier", changed_barrier)

    async with app.state.db.session() as session:
        with pytest.raises(SnapshotPublicationError, match="decision-data code set changed"):
            await publish_full_snapshot(session, run_id=run_id)


@pytest.mark.asyncio
async def test_full_decision_data_coverage_does_not_hide_low_score_coverage(app) -> None:
    run_id = await _seed_publishable_run(
        app,
        coverage_ratio=0.5,
        score_item_codes=("159915",),
    )

    async with app.state.db.session() as session:
        with pytest.raises(SnapshotPublicationError, match="score coverage"):
            await publish_full_snapshot(session, run_id=run_id)


def test_publication_accepts_ninety_percent_score_coverage_as_degraded() -> None:
    codes = [f"5100{index:02d}" for index in range(9)]
    trade_date = date(2026, 7, 24)
    items = [
        SimpleNamespace(
            asset_type="etf",
            asset_code=code,
            global_rank=index,
            score_eligible=True,
            ranking_score=80.0 - index,
        )
        for index, code in enumerate(codes, start=1)
    ]
    run = SimpleNamespace(
        status="success",
        scope_kind="full",
        scope_hash="scope",
        universe_snapshot_hash="universe",
        input_snapshot_hash="input",
        score_version="final_score_v3",
        rule_version="final_score_v3_rule_v2",
        ranking_contract_hash="contract",
        score_field="ranking_score",
        data_cutoff=datetime(2026, 7, 24, 15, 0),
        as_of_date=trade_date,
        as_of_trade_date=trade_date,
        price_basis="total_return_adjusted",
        expected_item_count=10,
        decision_data_item_count=10,
        decision_data_coverage_ratio=1.0,
        eligible_item_count=9,
        coverage_ratio=0.90,
        idempotency_key="degraded-coverage-fixture",
        summary_json={
            "item_count": 9,
            "coverage": {
                "score": {
                    "expected_count": 10,
                    "eligible_count": 9,
                    "eligible_codes": codes,
                    "coverage_ratio": 0.90,
                }
            },
        },
    )

    snapshot_publication._validate_publishable(
        run,
        items,
        decision_data_codes={*codes, "510099"},
    )


@pytest.mark.asyncio
async def test_coverage_barrier_reports_each_same_date_data_exclusion(app) -> None:
    await _seed_publishable_run(app, missing_price_code="510300")

    async with app.state.db.session() as session:
        barrier = await build_etf_coverage_barrier(
            session,
            as_of_trade_date=date(2026, 1, 2),
            data_cutoff=datetime(2026, 1, 2, 15, 30),
        )

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
        barrier = await build_etf_coverage_barrier(
            session,
            as_of_trade_date=date(2026, 1, 2),
            data_cutoff=datetime(2026, 1, 2, 15, 30),
        )

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
        barrier = await build_etf_coverage_barrier(
            session,
            as_of_trade_date=date(2026, 1, 2),
            data_cutoff=datetime(2026, 1, 2, 15, 30),
        )

    assert barrier.excluded == [{"asset_code": "510300", "reason": "incompatible_research_price_basis"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutations", "reason"),
    [
        ({"source_timestamp": datetime(2026, 1, 2, 7, 31)}, "source_after_data_cutoff"),
        (
            {
                "data_provider": "sina",
                "provider_version": "sina.raw_v1",
                "adjustment_version": "sina.raw_v1",
            },
            "unsupported_adjusted_provider",
        ),
        ({"research_adjusted_value": 0.0}, "invalid_research_adjusted_value"),
    ],
)
async def test_coverage_barrier_rejects_unavailable_or_untrusted_adjusted_price_provenance(
    app,
    mutations,
    reason,
) -> None:
    await _seed_publishable_run(app)
    async with app.state.db.session() as session:
        row = await session.scalar(
            select(EtfPriceHistory).where(
                EtfPriceHistory.etf_code == "510300",
                EtfPriceHistory.trade_date == date(2026, 1, 2),
            )
        )
        assert row is not None
        for field, value in mutations.items():
            setattr(row, field, value)
        await session.commit()
        barrier = await build_etf_coverage_barrier(
            session,
            as_of_trade_date=date(2026, 1, 2),
            data_cutoff=datetime(2026, 1, 2, 15, 30),
        )

    assert barrier.excluded == [{"asset_code": "510300", "reason": reason}]


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


@pytest.mark.asyncio
async def test_published_snapshot_run_content_is_immutable(app) -> None:
    run_id = await _seed_publishable_run(app)

    async with app.state.db.session() as session:
        await publish_full_snapshot(session, run_id=run_id)

    async with app.state.db.session() as session:
        run = await session.get(ShortResearchSignalRun, run_id)
        assert run is not None
        run.summary_json = {"coverage": {"tampered": True}}
        run.config_json = {"tampered": True}
        with pytest.raises(ValueError, match="published.*immutable"):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_published_snapshot_item_content_is_immutable(app) -> None:
    run_id = await _seed_publishable_run(app)

    async with app.state.db.session() as session:
        await publish_full_snapshot(session, run_id=run_id)

    async with app.state.db.session() as session:
        item = await session.scalar(
            select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run_id)
        )
        assert item is not None
        item.conclusion = "已篡改"
        item.score_breakdown_json = {"tampered": True}
        item.risk_flags_json = ["tampered"]
        item.rationale_json = {"tampered": True}
        item.metrics_json = {"tampered": True}
        with pytest.raises(ValueError, match="published.*immutable"):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_published_snapshot_items_cannot_be_deleted(app) -> None:
    run_id = await _seed_publishable_run(app)

    async with app.state.db.session() as session:
        await publish_full_snapshot(session, run_id=run_id)

    async with app.state.db.session() as session:
        item = await session.scalar(
            select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run_id)
        )
        assert item is not None
        await session.delete(item)
        with pytest.raises(ValueError, match="published ranking snapshot items are immutable"):
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


@pytest.mark.asyncio
async def test_published_snapshot_item_cannot_move_to_a_draft_run(app) -> None:
    run_id = await _seed_publishable_run(app)
    async with app.state.db.session() as session:
        await publish_full_snapshot(session, run_id=run_id)

    async with app.state.db.session() as session:
        draft = ShortResearchSignalRun(status="success", as_of_date=date(2026, 1, 3))
        session.add(draft)
        await session.flush()
        item = await session.scalar(
            select(ShortResearchSignalItem).where(ShortResearchSignalItem.run_id == run_id)
        )
        assert item is not None
        item.run_id = draft.id

        with pytest.raises(ValueError, match="published ranking snapshot items are immutable"):
            await session.commit()
        await session.rollback()
