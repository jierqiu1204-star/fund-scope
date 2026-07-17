from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.entities import EtfPriceHistory, EtfUniverseMembership, TradableEtf
from app.services.short_research.daily_reconstructable import score_daily_reconstructable
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    ReplayInputExclusionReason,
    load_point_in_time_ranking_inputs,
)

T = date(2022, 6, 30)
CUTOFF = datetime(2022, 6, 30, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _etf(code: str, *, currently_eligible: bool = True) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=f"ETF-{code}",
        exchange="SH" if code.startswith("5") else "SZ",
        theme_tags_json=["current-only-classification"],
        trading_rule_label="证券账户 T+1 ETF",
        asset_class="current-only-asset-class",
        is_short_term_eligible=currently_eligible,
        is_watchlist=currently_eligible,
        created_at=datetime(2021, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )


def _membership(
    code: str,
    *,
    effective_from: date = date(2020, 1, 1),
    effective_to: date | None = None,
    known_at: datetime = datetime(2021, 1, 1),
    updated_at: datetime | None = None,
) -> EtfUniverseMembership:
    return EtfUniverseMembership(
        etf_code=code,
        effective_from=effective_from,
        effective_to=effective_to,
        source="factual-membership-fixture",
        tracked_underlying_id=f"UNDERLYING-{code}",
        created_at=known_at,
        updated_at=updated_at or known_at,
    )


def _adjusted_history(
    code: str,
    *,
    end_date: date = T,
    count: int = 61,
    source_timestamp: datetime = datetime(2022, 6, 30, 6, 30),
    daily_step: float = 0.005,
) -> list[EtfPriceHistory]:
    rows: list[EtfPriceHistory] = []
    trade_dates: list[date] = []
    cursor = end_date
    while len(trade_dates) < count:
        if cursor.weekday() < 5:
            trade_dates.append(cursor)
        cursor -= timedelta(days=1)
    for offset, trade_date in enumerate(reversed(trade_dates)):
        raw_close = 1.0 + offset * daily_step
        rows.append(
            EtfPriceHistory(
                etf_code=code,
                trade_date=trade_date,
                open=raw_close * 0.995,
                high=raw_close * 1.01,
                low=raw_close * 0.99,
                close=raw_close,
                volume=1_000_000 + offset,
                turnover=raw_close * (1_000_000 + offset),
                pct_change=0.0,
                raw_price_basis="raw_ohlc",
                research_adjusted_value=raw_close * 2.0,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version="eastmoney.push2his.kline.hfq_v1",
                source_timestamp=source_timestamp,
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                decision_eligible=True,
            )
        )
    return rows


@pytest.mark.asyncio
async def test_loader_uses_effective_membership_and_keeps_later_delisted_member(app) -> None:
    async with app.state.db.session() as session:
        session.add_all(
            [
                _etf("510003"),
                _etf("510001", currently_eligible=False),
                _etf("510002"),
            ]
        )
        session.add_all(
            [
                _membership("510003"),
                _membership("510001", effective_to=T + timedelta(days=5)),
                _membership("510002", effective_from=T + timedelta(days=1)),
            ]
        )
        session.add_all(
            [
                *_adjusted_history("510003"),
                *_adjusted_history("510001"),
                *_adjusted_history("510002"),
            ]
        )
        await session.commit()

        first = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=1_000,
        )
        second = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=1_000,
        )

    assert [item.asset_code for item in first.authoritative_universe] == [
        "510001",
        "510003",
    ]
    assert [item.asset_code for item in first.eligible_inputs] == ["510001", "510003"]
    assert first.authoritative_universe[0].membership_source == "factual-membership-fixture"
    assert first.authoritative_universe[0].tracked_underlying_id == "UNDERLYING-510001"
    assert first.eligible_inputs[0].bars[-1].session_date == T
    assert first.eligible_inputs[0].bars[-1].adjusted_close == pytest.approx(2.6)
    assert first == second
    assert len(first.universe_hash) == 64
    assert len(first.input_hash) == 64


@pytest.mark.asyncio
async def test_loader_rejects_current_survivor_and_future_known_membership(app) -> None:
    async with app.state.db.session() as session:
        session.add_all([_etf("510010"), _etf("510011"), _etf("510012")])
        session.add_all(
            [
                _membership(
                    "510011",
                    effective_from=date(2020, 1, 1),
                    known_at=datetime(2022, 7, 1),
                ),
                _membership(
                    "510012",
                    known_at=datetime(2021, 1, 1),
                    updated_at=datetime(2026, 1, 1),
                ),
            ]
        )
        session.add_all(
            [
                *_adjusted_history("510010"),
                *_adjusted_history("510011"),
                *_adjusted_history("510012"),
            ]
        )
        await session.commit()

        snapshot = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=1_000,
        )

    assert snapshot.authoritative_universe == ()
    assert snapshot.eligible_inputs == ()
    assert {
        (item.asset_code, item.reason)
        for item in snapshot.exclusions
    } == {
        ("510011", ReplayInputExclusionReason.FUTURE_KNOWN_INPUT),
        ("510012", ReplayInputExclusionReason.FUTURE_KNOWN_INPUT),
        (None, ReplayInputExclusionReason.INSUFFICIENT_POINT_IN_TIME_UNIVERSE),
    }


@pytest.mark.asyncio
async def test_loader_reports_stable_adjustment_and_eligibility_exclusions(app) -> None:
    valid = _adjusted_history("510100")
    raw_fallback = _adjusted_history("510101")
    for row in raw_fallback:
        row.data_provider = "sina-fallback"
        row.provider_version = None
        row.adjustment_version = None
        row.research_adjusted_value = None
        row.research_price_basis = None
        row.decision_eligible = False
        row.decision_ineligibility_reason = "missing_total_return_provenance"
    unproven = _adjusted_history("510102")
    for row in unproven:
        row.provider_version = "eastmoney.future-hfq-v9"
        row.adjustment_version = "eastmoney.future-hfq-v9"
    ineligible = _adjusted_history("510103")
    ineligible[-1].decision_eligible = False
    ineligible[-1].decision_ineligibility_reason = "provider_health_failed"
    stale = _adjusted_history("510104", end_date=T - timedelta(days=1))
    invalid_ohlc = _adjusted_history("510105")
    invalid_ohlc[-1].high = invalid_ohlc[-1].close * 0.5
    fallback_basis = _adjusted_history("510106")
    for row in fallback_basis:
        row.raw_price_basis = "fallback_raw"
    future_revision = _adjusted_history(
        "510107",
        source_timestamp=datetime(2022, 7, 1),
    )

    async with app.state.db.session() as session:
        codes = (
            "510100",
            "510101",
            "510102",
            "510103",
            "510104",
            "510105",
            "510106",
            "510107",
        )
        session.add_all([_etf(code) for code in codes])
        session.add_all([_membership(code) for code in codes])
        session.add_all(
            [
                *valid,
                *raw_fallback,
                *unproven,
                *ineligible,
                *stale,
                *invalid_ohlc,
                *fallback_basis,
                *future_revision,
            ]
        )
        await session.commit()

        snapshot = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=1_000,
        )

    assert [item.asset_code for item in snapshot.eligible_inputs] == ["510100"]
    assert snapshot.eligible_inputs[0].synchronized_after_cutoff is False
    assert snapshot.eligible_inputs[0].provenance.scale_invariance_proven is True
    assert {
        item.asset_code: item.reason
        for item in snapshot.exclusions
    } == {
        "510101": ReplayInputExclusionReason.RAW_OR_FALLBACK_PROVIDER_DATA,
        "510102": ReplayInputExclusionReason.UNPROVEN_ADJUSTMENT_POINT_IN_TIME,
        "510103": ReplayInputExclusionReason.STALE_OR_INELIGIBLE_ADJUSTED_INPUT,
        "510104": ReplayInputExclusionReason.STALE_OR_INELIGIBLE_ADJUSTED_INPUT,
        "510105": ReplayInputExclusionReason.STALE_OR_INELIGIBLE_ADJUSTED_INPUT,
        "510106": ReplayInputExclusionReason.RAW_OR_FALLBACK_PROVIDER_DATA,
        "510107": ReplayInputExclusionReason.FUTURE_KNOWN_INPUT,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_cutoff",
    [
        datetime(2022, 6, 30, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
        datetime(2022, 6, 30, 23, 0, tzinfo=ZoneInfo("UTC")),
    ],
)
async def test_loader_rejects_preclose_or_wrong_shanghai_session_cutoff(
    app,
    invalid_cutoff: datetime,
) -> None:
    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="completed Shanghai trading session"):
            await load_point_in_time_ranking_inputs(
                session,
                replay_date=T,
                decision_cutoff=invalid_cutoff,
                max_source_rows=1_000,
            )


def _features_and_ranks(snapshot) -> tuple[tuple[tuple[str, float, float, float, float], ...], tuple[str, ...]]:
    features = tuple(
        (
            item.asset_code,
            score.research_score,
            score.trend_score,
            score.risk_score,
            score.liquidity_score,
        )
        for item in snapshot.eligible_inputs
        for score in [score_daily_reconstructable(item.bars, provenance=item.provenance)]
    )
    ranks = tuple(code for code, *_ in sorted(features, key=lambda item: (-item[1], item[0])))
    return features, ranks


@pytest.mark.asyncio
async def test_cutoff_at_t_is_invariant_to_larger_database_history(app) -> None:
    async with app.state.db.session() as session:
        session.add_all([_etf("510200"), _etf("510201")])
        session.add_all([_membership("510200"), _membership("510201")])
        session.add_all(
            [
                *_adjusted_history("510200", daily_step=0.005),
                *_adjusted_history("510201", daily_step=-0.002),
            ]
        )
        await session.commit()

        through_t = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=1_000,
        )
        features_through_t, ranks_through_t = _features_and_ranks(through_t)

        session.add_all(
            [
                *_adjusted_history(
                    "510200",
                    end_date=T - timedelta(days=100),
                    count=3,
                    source_timestamp=datetime(2026, 1, 1),
                ),
                *_adjusted_history(
                    "510201",
                    end_date=T - timedelta(days=100),
                    count=3,
                    source_timestamp=datetime(2026, 1, 1),
                ),
                *_adjusted_history(
                    "510200",
                    end_date=T + timedelta(days=1),
                    count=1,
                    source_timestamp=datetime(2026, 1, 1),
                ),
                *_adjusted_history(
                    "510201",
                    end_date=T + timedelta(days=1),
                    count=1,
                    source_timestamp=datetime(2026, 1, 1),
                ),
                _etf("510299"),
                _membership("510299", known_at=datetime(2022, 7, 1)),
                *_adjusted_history("510299"),
            ]
        )
        await session.commit()

        larger_database = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=1_000,
        )
        features_larger, ranks_larger = _features_and_ranks(larger_database)

    assert through_t.authoritative_universe == larger_database.authoritative_universe
    assert through_t.eligible_inputs == larger_database.eligible_inputs
    assert through_t.universe_hash == larger_database.universe_hash
    assert through_t.input_hash == larger_database.input_hash
    assert through_t.source_snapshot_hash != larger_database.source_snapshot_hash
    assert through_t.coverage_manifest_hash != larger_database.coverage_manifest_hash
    assert features_through_t == features_larger
    assert ranks_through_t == ranks_larger
    assert [item.asset_code for item in larger_database.exclusions] == ["510299"]


@pytest.mark.asyncio
async def test_loader_reads_only_the_required_tail_window_per_code(app) -> None:
    codes = ("510400", "510401")
    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        session.add_all([_membership(code) for code in codes])
        for code in codes:
            session.add_all(_adjusted_history(code))
            session.add_all(
                _adjusted_history(
                    code,
                    end_date=T - timedelta(days=100),
                    count=80,
                    source_timestamp=datetime(2026, 1, 1),
                )
            )
        await session.commit()

        snapshot = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=122,
        )

    assert [item.asset_code for item in snapshot.eligible_inputs] == list(codes)
    assert all(len(item.bars) == 61 for item in snapshot.eligible_inputs)


@pytest.mark.asyncio
async def test_loader_pages_codes_without_losing_full_universe_identity(app) -> None:
    codes = ("510410", "510411", "510412")
    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        session.add_all([_membership(code) for code in codes])
        for code in codes:
            session.add_all(_adjusted_history(code))
        await session.commit()

        first = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=122,
            max_codes=2,
        )
        second = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=122,
            max_codes=2,
            code_after=first.next_code_after,
        )

    assert first.page_asset_codes == ("510410", "510411")
    assert first.next_code_after == "510411"
    assert first.has_more is True
    assert second.page_asset_codes == ("510412",)
    assert second.next_code_after is None
    assert second.has_more is False
    assert first.authoritative_universe == second.authoritative_universe
    assert first.universe_hash == second.universe_hash
    assert first.source_snapshot_hash == second.source_snapshot_hash
    assert {
        item.asset_code
        for page in (first, second)
        for item in page.eligible_inputs
    } == set(codes)


@pytest.mark.asyncio
async def test_loader_rejects_unbounded_code_or_row_pages_before_price_scan(app) -> None:
    async with app.state.db.session() as session:
        with pytest.raises(ValueError, match="max_codes"):
            await load_point_in_time_ranking_inputs(
                session,
                replay_date=T,
                decision_cutoff=CUTOFF,
                max_source_rows=10_000,
                max_codes=17,
            )

        session.add_all([_etf("510420"), _etf("510421")])
        session.add_all([_membership("510420"), _membership("510421")])
        await session.commit()
        with pytest.raises(ValueError, match="bounded source-row budget"):
            await load_point_in_time_ranking_inputs(
                session,
                replay_date=T,
                decision_cutoff=CUTOFF,
                max_source_rows=61,
                max_codes=2,
            )
