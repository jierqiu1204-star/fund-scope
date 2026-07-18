from __future__ import annotations

import hashlib
import smtplib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event

from app.models.entities import (
    EtfPointInTimeMembershipFact,
    EtfPriceHistory,
    TradableEtf,
)
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
    score_daily_reconstructable,
)
from app.services.strategy_lab.etf_action_replay.artifact_store import ReplayArtifactStore
from app.services.strategy_lab.etf_ranking_replay_inputs import (
    ReplayInputExclusionReason,
    load_point_in_time_ranking_inputs,
)
from app.services.strategy_lab.etf_ranking_stage_a import (
    STAGE_A_SCHEMA_VERSION,
    StageABatchRequest,
    StageAReplayContract,
    run_stage_a_loader_job,
)
from app.services.strategy_lab.etf_ranking_stage_b import (
    StageBBatchRequest,
    read_stage_b_source_date_page_from_stage_a,
    run_stage_b_continuation,
    stage_b_contract_from_stage_a,
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
    membership_state: str = "included",
    receipt_suffix: str = "primary",
) -> EtfPointInTimeMembershipFact:
    receipt = f"{code}:{receipt_suffix}:{membership_state}"

    def digest(label: str) -> str:
        return hashlib.sha256(f"{receipt}:{label}".encode()).hexdigest()

    return EtfPointInTimeMembershipFact(
        etf_code=code,
        external_source_id=f"exchange-notice:{receipt}",
        provider="fixture-exchange",
        provider_version="notice-v1",
        observed_at=updated_at or known_at,
        effective_from=effective_from,
        effective_to=effective_to,
        membership_state=membership_state,
        evidence_hash=digest("evidence"),
        raw_payload_hash=digest("raw"),
        fact_hash=digest("fact"),
        created_at=datetime(2026, 7, 15),
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
    assert first.authoritative_universe[0].membership_source == "fixture-exchange"
    assert first.authoritative_universe[0].tracked_underlying_id is None
    assert first.eligible_inputs[0].bars[-1].session_date == T
    assert first.eligible_inputs[0].bars[-1].adjusted_close == pytest.approx(2.6)
    assert first == second
    assert len(first.universe_hash) == 64
    assert len(first.input_hash) == 64


@pytest.mark.asyncio
async def test_research_replay_cannot_write_production_decision_or_notification_state(
    app,
    tmp_path,
    monkeypatch,
) -> None:
    forbidden_tables = (
        "short_research_signal_runs",
        "short_research_signal_items",
        "etf_observation_portfolio_snapshots",
        "etf_observation_portfolio_items",
        "etf_optimized_allocation_snapshots",
        "etf_optimized_allocation_items",
        "tracked_positions",
        "tracked_position_alerts",
        "tracked_position_alert_audits",
        "tracked_position_action_decisions",
        "tracked_position_action_executions",
        "tracked_position_action_transition_receipts",
        "tracked_position_lifecycle_shadow_evidence",
        "tracked_position_notification_envelopes",
        "tracked_position_notification_items",
        "notification_log",
    )
    writes: list[str] = []
    smtp_calls: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = " ".join(str(statement).lower().split())
        if normalized.startswith(("insert ", "update ", "delete ")) and any(
            table in normalized for table in forbidden_tables
        ):
            writes.append(normalized)

    def reject_smtp(*_args, **_kwargs):
        smtp_calls.append("called")
        raise AssertionError("research replay must not open SMTP connections")

    monkeypatch.setattr(smtplib, "SMTP", reject_smtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", reject_smtp)

    async with app.state.db.session() as session:
        session.add(_etf("510900"))
        session.add(_membership("510900"))
        session.add_all(_adjusted_history("510900"))
        await session.commit()

        sync_engine = session.sync_session.bind
        assert sync_engine is not None
        event.listen(sync_engine, "before_cursor_execute", capture_statement)
        try:
            def digest(label: str) -> str:
                return hashlib.sha256(label.encode()).hexdigest()

            stage_a_contract = StageAReplayContract(
                replay_run_key="no-production-side-effects",
                score_manifest_hash=daily_reconstructable_manifest().manifest_hash,
                source_snapshot_hash=digest("source-registry"),
                universe_manifest_hash=digest("universe-registry"),
                decision_cutoff_semantics="asia_shanghai_post_close_v1",
                schema_version=STAGE_A_SCHEMA_VERSION,
                candidate_registry_hash=digest("candidate-registry"),
            )
            store = ReplayArtifactStore(tmp_path / "replay-artifacts.sqlite3")
            stage_a = await run_stage_a_loader_job(
                session=session,
                store=store,
                contract=stage_a_contract,
                request=StageABatchRequest(
                    max_source_rows=61,
                    max_items=1,
                    max_pages=1,
                    max_seconds=10.0,
                    worker_count=1,
                    peak_rss_limit_bytes=256 * 1024**2,
                ),
                replay_dates=(T,),
                decision_cutoffs=((T, CUTOFF),),
                max_codes_per_page=1,
                peak_rss_reader=lambda: 1024,
            )
            assert stage_a.complete is True

            stage_b_contract = stage_b_contract_from_stage_a(stage_a_contract)
            source_page = read_stage_b_source_date_page_from_stage_a(
                store=store,
                stage_a_contract=stage_a_contract,
                stage_b_contract=stage_b_contract,
                after_cursor=None,
                max_dates=1,
                max_feature_rows=2,
                stage_a_page_rows=1,
                max_seconds=10.0,
            )
            stage_b = run_stage_b_continuation(
                store=store,
                contract=stage_b_contract,
                request=StageBBatchRequest(
                    max_dates=1,
                    max_feature_rows=2,
                    max_seconds=10.0,
                    worker_count=1,
                ),
                source_dates=source_page.source_dates,
                source_has_more=source_page.has_more,
            )
            assert stage_b.complete is True
        finally:
            event.remove(sync_engine, "before_cursor_execute", capture_statement)

    assert writes == []
    assert smtp_calls == []


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
        (
            "510011",
            ReplayInputExclusionReason.MEMBERSHIP_OBSERVED_AFTER_CUTOFF,
        ),
        (
            "510012",
            ReplayInputExclusionReason.MEMBERSHIP_OBSERVED_AFTER_CUTOFF,
        ),
        (None, ReplayInputExclusionReason.INSUFFICIENT_POINT_IN_TIME_UNIVERSE),
    }


@pytest.mark.asyncio
async def test_loader_rejects_conflicting_membership_receipts(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510013"))
        session.add_all(
            [
                _membership("510013", receipt_suffix="included"),
                _membership(
                    "510013",
                    membership_state="excluded",
                    receipt_suffix="excluded",
                ),
            ]
        )
        session.add_all(_adjusted_history("510013"))
        await session.commit()

        snapshot = await load_point_in_time_ranking_inputs(
            session,
            replay_date=T,
            decision_cutoff=CUTOFF,
            max_source_rows=1_000,
        )

    assert snapshot.authoritative_universe == ()
    assert {
        (item.asset_code, item.reason)
        for item in snapshot.exclusions
    } == {
        ("510013", ReplayInputExclusionReason.MEMBERSHIP_FACT_CONFLICT),
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
