from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.models.entities import (
    EtfIntradayCleanupCheckpoint,
    EtfIntradayDailySummary,
    EtfIntradayLatestQuote,
    EtfIntradayQuote,
    EtfIntradayQuoteEvidenceRef,
    EtfLabelOutcome,
    EtfPriceHistory,
    ShortResearchSignalItem,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
    TradableEtf,
    User,
    authorize_snapshot_publication,
    utcnow,
)
from app.services.intraday_etf import service as intraday_service
from app.services.intraday_etf.evidence import intraday_quote_evidence_hash
from app.services.intraday_etf.exchange_calendar import (
    is_trading_day,
    market_session,
    next_trading_day,
)
from app.services.intraday_etf.jobs import intraday_etf_watch_job
from app.services.intraday_etf.service import (
    ASIA_SHANGHAI,
    CONSENSUS_CONSISTENT,
    CONSENSUS_DIVERGED,
    CONSENSUS_SINGLE_PROVIDER,
    CONSENSUS_STALE,
    MarketState,
    ProviderQuoteResult,
    WatchItem,
    WatchlistResult,
    _fetch_eastmoney_provider,
    _postgres_quote_record,
    build_watchlist,
    current_market_state,
    is_quote_stale,
    latest_intraday_quote,
    latest_quotes_by_code,
    normalize_spot_record,
    persist_quotes,
    select_consensus_quotes,
    summarize_and_cleanup_intraday_quotes,
)
from app.services.short_research import service as short_research_service
from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.ranking_surfaces import (
    DUAL_RANKING_RULE_VERSION,
    actionable_rank_manifest,
)
from app.services.short_research.service import CONCLUSION_HIGH_WATCH, CONCLUSION_WATCH
from app.services.short_research.snapshot_selector import CanonicalSnapshotSelection
from app.services.tracked_positions.service import (
    create_alert_if_needed,
    create_position,
    position_analysis,
)
from app.services.workflows import etf_live_rankings as live_ranking_workflow

TEST_ADJUSTED_PROVIDER_VERSION = "eastmoney.push2his.kline.hfq_v1"


def _decision_adjusted_fields(close: float) -> dict[str, object]:
    return {
        "raw_price_basis": "unadjusted",
        "research_adjusted_value": close,
        "research_price_basis": "total_return_adjusted",
        "data_provider": "eastmoney",
        "provider_version": TEST_ADJUSTED_PROVIDER_VERSION,
        "source_timestamp": datetime.now() - timedelta(days=1),
        "adjustment_version": TEST_ADJUSTED_PROVIDER_VERSION,
        "decision_eligible": True,
        "decision_ineligibility_reason": None,
    }


def test_observation_only_research_row_cannot_be_used_as_intraday_action_base() -> None:
    snapshot = SimpleNamespace(
        status="success",
        publication_state="published",
        scope_kind="full",
        score_version="daily_reconstructable_v1",
        ranking_contract_hash="contract",
        price_basis="total_return_adjusted",
        score_field="research_score",
        coverage_ratio=1.0,
        as_of_trade_date=date(2026, 8, 10),
    )
    item = SimpleNamespace(
        score_eligible=True,
        metrics_json={
            "research_score": 88.0,
            "research_quality_eligible": False,
            "observation_only": True,
        },
    )

    score, reason = live_ranking_workflow._eligible_intraday_base(
        snapshot,
        item,
        date(2026, 8, 10),
    )

    assert score is None
    assert reason == "日线基座仅供研究观察，未通过行动质量门槛。"


def _etf(code: str, name: str | None = None) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=name or f"ETF{code}",
        exchange="SH",
        theme_tags_json=["test"],
        trading_rule_label="T+1",
        asset_class="equity",
        is_short_term_eligible=True,
        is_watchlist=True,
    )


def test_large_postgres_cutoff_query_uses_one_bounded_distinct_on_scan() -> None:
    codes = [f"{index:06d}" for index in range(1_500)]
    statement = intraday_service._postgres_distinct_on_historical_quotes_stmt(
        codes,
        trade_date=date(2026, 8, 10),
        decision_cutoff=datetime(2026, 8, 10, 15, 0),
        captured_cutoff=datetime(2026, 8, 10, 7, 0),
    )

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "DISTINCT ON (etf_intraday_quotes.etf_code)" in compiled
    assert "etf_intraday_quotes.trade_date =" in compiled
    assert "etf_intraday_quotes.quote_time <=" in compiled
    assert "etf_intraday_quotes.created_at <=" in compiled
    assert "JOIN LATERAL" not in compiled


async def _seed_signal_run(
    app,
    *,
    count: int = 25,
    conclusions: list[str] | None = None,
    total_scores: list[float] | None = None,
    canonical: bool = True,
    as_of_date: date | None = None,
    metrics_overrides: dict[str, dict[str, object]] | None = None,
) -> int:
    conclusions = conclusions or [CONCLUSION_WATCH] * count
    total_scores = total_scores or [100.0 - i for i in range(count)]
    signal_date = as_of_date or date(2026, 6, 12)
    research_manifest = daily_reconstructable_manifest()
    actionable_manifest = actionable_rank_manifest()
    async with app.state.db.session() as session:
        session.add_all([_etf(f"51{i:04d}") for i in range(count)])
        run = ShortResearchSignalRun(
            status="success",
            started_at=utcnow(),
            finished_at=utcnow(),
            as_of_date=signal_date,
            config_json={
                "asset_type": "etf",
                "theme": None,
                "codes": [],
                "language": "research_only",
            },
            summary_json={
                "item_count": count,
                "etf_count": count,
                **(
                    {
                        "ranking_surfaces": {
                            "research": {
                                "contract_id": research_manifest.contract_id,
                                "score_field": research_manifest.score_field,
                                "contract_hash": research_manifest.manifest_hash,
                                "eligible_count": count,
                            },
                            "actionable": {
                                "contract_id": actionable_manifest.contract_id,
                                "score_field": actionable_manifest.score_field,
                                "contract_hash": actionable_manifest.manifest_hash,
                                "eligible_count": count,
                            },
                        }
                    }
                    if canonical
                    else {}
                ),
            },
            scope_kind="full" if canonical else None,
            scope_hash="test-full-scope" if canonical else None,
            universe_snapshot_hash="test-universe" if canonical else None,
            input_snapshot_hash="test-input" if canonical else None,
            score_version=research_manifest.contract_id if canonical else None,
            rule_version=DUAL_RANKING_RULE_VERSION if canonical else None,
            ranking_contract_hash="test-ranking-contract" if canonical else None,
            score_field=research_manifest.score_field if canonical else None,
            data_cutoff=datetime.combine(signal_date, time(15, 0)) if canonical else None,
            as_of_trade_date=signal_date if canonical else None,
            price_basis="total_return_adjusted" if canonical else None,
            expected_item_count=count if canonical else None,
            decision_data_item_count=count if canonical else None,
            decision_data_coverage_ratio=1.0 if canonical else None,
            eligible_item_count=count if canonical else None,
            coverage_ratio=1.0 if canonical else None,
            idempotency_key=f"live-test-{signal_date}-{count}-{utcnow().isoformat()}"
            if canonical
            else None,
            publication_state=None,
            published_at=None,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        session.add_all(
            [
                ShortResearchSignalItem(
                    run_id=run.id,
                    asset_type="etf",
                    asset_code=f"51{i:04d}",
                    rank=i + 1,
                    global_rank=i + 1 if canonical else None,
                    total_score=total_scores[i] if i < len(total_scores) else 100.0 - i,
                    ranking_score=(total_scores[i] if i < len(total_scores) else 100.0 - i)
                    if canonical
                    else None,
                    score_eligible=True if canonical else None,
                    conclusion=conclusions[i] if i < len(conclusions) else CONCLUSION_WATCH,
                    score_breakdown_json={},
                    risk_flags_json=[],
                    rationale_json={
                        "key_reason": "test",
                        "entry_timing_label": "趋势延续",
                        "entry_timing_reason": "日线趋势仍在。",
                    },
                    metrics_json={
                        "default_display_eligible": True,
                        "entry_timing_label": "趋势延续",
                        "entry_timing_reason": "日线趋势仍在。",
                        **(
                            {
                                "score_version": research_manifest.contract_id,
                                "research_rank": i + 1,
                                "research_score": (
                                    total_scores[i] if i < len(total_scores) else 100.0 - i
                                ),
                                "actionable_rank": i + 1,
                                "actionable_score": (
                                    total_scores[i] if i < len(total_scores) else 100.0 - i
                                ),
                            }
                            if canonical
                            else {}
                        ),
                        **(
                            {"ranking_asset_bucket": "equity", "volatility_20d": 0.01}
                            if canonical
                            else {}
                        ),
                        **(metrics_overrides or {}).get(f"51{i:04d}", {}),
                    },
                )
                for i in range(count)
            ]
        )
        await session.commit()
        if canonical:
            with authorize_snapshot_publication(session.sync_session, run_id=run.id):
                run.publication_state = "published"
                run.published_at = utcnow()
                await session.flush()
            await session.commit()
        return run.id


async def _seed_price_history(app, code: str, *, start_price: float = 1.0) -> None:
    start = date(2026, 5, 11)
    async with app.state.db.session() as session:
        if await session.get(TradableEtf, code) is None:
            session.add(_etf(code))
            await session.flush()
        for offset in range(35):
            close = start_price * (1 + offset * 0.001)
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=offset),
                    open=close * 0.995,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=10_000_000,
                    turnover=100_000_000,
                    pct_change=0.1,
                    **_decision_adjusted_fields(close),
                )
            )
        await session.commit()


async def _seed_price_history_from_closes(
    app,
    code: str,
    closes: list[float],
    *,
    start: date,
    turnover: float = 100_000_000,
) -> None:
    async with app.state.db.session() as session:
        if await session.get(TradableEtf, code) is None:
            session.add(_etf(code))
            await session.flush()
        for offset in range(30):
            close = closes[0]
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start - timedelta(days=30 - offset),
                    open=close,
                    high=close * 1.004,
                    low=close * 0.996,
                    close=close,
                    volume=10_000_000,
                    turnover=turnover,
                    pct_change=0.0,
                    **_decision_adjusted_fields(close),
                )
            )
        for offset, close in enumerate(closes):
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=start + timedelta(days=offset),
                    open=close,
                    high=close * 1.004,
                    low=close * 0.996,
                    close=close,
                    volume=10_000_000,
                    turnover=turnover,
                    pct_change=0.0,
                    **_decision_adjusted_fields(close),
                )
            )
        await session.commit()


def test_current_market_state_uses_half_open_trading_sessions() -> None:
    assert (
        current_market_state(datetime(2026, 6, 17, 11, 29, 59, tzinfo=ASIA_SHANGHAI)).status
        == "open"
    )
    lunch_state = current_market_state(datetime(2026, 6, 17, 11, 30, 0, tzinfo=ASIA_SHANGHAI))
    assert lunch_state.status == "lunch_break"
    assert lunch_state.session == "lunch"
    assert (
        current_market_state(datetime(2026, 6, 17, 12, 59, 59, tzinfo=ASIA_SHANGHAI)).status
        == "lunch_break"
    )
    assert (
        current_market_state(datetime(2026, 6, 17, 14, 59, 59, tzinfo=ASIA_SHANGHAI)).status
        == "open"
    )
    assert (
        current_market_state(datetime(2026, 6, 17, 15, 0, 0, tzinfo=ASIA_SHANGHAI)).status
        == "closed"
    )


def test_exchange_calendar_handles_holidays_boundaries_and_lunch_freshness() -> None:
    holiday = current_market_state(datetime(2026, 10, 1, 10, 0, tzinfo=ASIA_SHANGHAI))
    assert holiday.status == "closed"
    assert holiday.next_poll_seconds == 7 * 24 * 60 * 60 - 30 * 60

    before_open = current_market_state(datetime(2026, 6, 17, 9, 29, tzinfo=ASIA_SHANGHAI))
    assert before_open.status == "closed"
    assert before_open.next_poll_seconds == 60
    assert (
        current_market_state(datetime(2026, 6, 17, 9, 30, tzinfo=ASIA_SHANGHAI)).session
        == "morning"
    )
    lunch = current_market_state(datetime(2026, 6, 17, 11, 30, tzinfo=ASIA_SHANGHAI))
    assert lunch.session == "lunch"
    assert lunch.next_poll_seconds == 90 * 60
    assert (
        current_market_state(datetime(2026, 6, 17, 13, 0, tzinfo=ASIA_SHANGHAI)).session
        == "afternoon"
    )

    assert (
        is_quote_stale(
            datetime(2026, 6, 17, 11, 29, tzinfo=ASIA_SHANGHAI),
            datetime(2026, 6, 17, 12, 50, tzinfo=ASIA_SHANGHAI),
        )
        is False
    )
    assert (
        is_quote_stale(
            datetime(2026, 6, 17, 11, 29, tzinfo=ASIA_SHANGHAI),
            datetime(2026, 6, 17, 13, 5, tzinfo=ASIA_SHANGHAI),
        )
        is True
    )


def test_exchange_calendar_fails_closed_for_unknown_year() -> None:
    unknown_weekday = date(2027, 7, 15)

    assert is_trading_day(unknown_weekday) is False
    assert market_session(datetime(2027, 7, 15, 10, 0, tzinfo=ASIA_SHANGHAI)) == ("closed", None)


def test_unknown_calendar_year_rechecks_at_midnight_without_searching_forever() -> None:
    state = current_market_state(datetime(2027, 7, 15, 10, 0, tzinfo=ASIA_SHANGHAI))

    assert state.status == "closed"
    assert state.session is None
    assert state.next_poll_seconds == 14 * 60 * 60


def test_next_trading_day_rejects_crossing_into_unknown_calendar_year() -> None:
    with pytest.raises(ValueError, match="exchange calendar is unavailable for 2027"):
        next_trading_day(date(2026, 12, 31))


def test_intraday_adjustments_are_volatility_and_same_time_normalized() -> None:
    low_volatility = live_ranking_workflow._volatility_price_adjustment(
        change_percent=1.0,
        conclusion=CONCLUSION_WATCH,
        asset_bucket="bond",
        volatility_20d=0.005,
    )
    high_volatility = live_ranking_workflow._volatility_price_adjustment(
        change_percent=1.0,
        conclusion=CONCLUSION_WATCH,
        asset_bucket="commodity",
        volatility_20d=0.05,
    )
    morning = live_ranking_workflow._same_time_activity_adjustment(150.0, [100.0] * 5)
    afternoon = live_ranking_workflow._same_time_activity_adjustment(300.0, [200.0] * 5)
    insufficient = live_ranking_workflow._same_time_activity_adjustment(150.0, [100.0] * 4)

    assert low_volatility[0] == "冲高别追"
    assert low_volatility[2] < 0
    assert high_volatility[0] == "趋势延续"
    assert high_volatility[2] > 0
    assert morning[0] == afternoon[0] == 1.0
    assert insufficient[0] is None


@pytest.mark.asyncio
async def test_live_rankings_compare_turnover_with_same_exchange_minute_history(
    client, app, monkeypatch
) -> None:
    now = datetime(2026, 6, 17, 10, 0, 0)
    await _seed_signal_run(app, count=1, canonical=True, as_of_date=date(2026, 6, 16))
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add_all(
            [
                EtfIntradayQuote(
                    etf_code="510000",
                    quote_time=datetime.combine(trade_date, time(10, 0)),
                    trade_date=trade_date,
                    latest_price=1.0,
                    turnover=100.0,
                    source="test",
                    freshness_status="fresh",
                    raw_json={
                        "decision_eligible": True,
                        "consensus_status": CONSENSUS_CONSISTENT,
                    },
                )
                for trade_date in (
                    date(2026, 6, 16),
                    date(2026, 6, 15),
                    date(2026, 6, 12),
                    date(2026, 6, 11),
                    date(2026, 6, 10),
                )
            ]
        )
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.01,
                change_percent=0.5,
                turnover=150.0,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["score_source"] == "intraday"
    assert any("同刻历史中位数" in reason for reason in item["score_contribution_reasons"])
    assert item["intraday_adjustment_score"] == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("history_kind", ["stale", "mismatched", "incomplete"])
async def test_live_rankings_reject_unreliable_same_minute_history(
    client,
    app,
    monkeypatch,
    history_kind: str,
) -> None:
    now = datetime(2026, 6, 17, 10, 0, 0)
    await _seed_signal_run(app, count=1, canonical=True, as_of_date=date(2026, 6, 16))
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    trade_dates = [
        date(2026, 6, 16),
        date(2026, 6, 15),
        date(2026, 6, 12),
        date(2026, 6, 11),
        date(2026, 6, 10),
    ]
    if history_kind == "incomplete":
        trade_dates.pop()
    async with app.state.db.session() as session:
        for trade_date in trade_dates:
            quote_minute = 1 if history_kind == "mismatched" else 0
            session.add(
                EtfIntradayQuote(
                    etf_code="510000",
                    quote_time=datetime.combine(trade_date, time(10, quote_minute)),
                    trade_date=trade_date,
                    latest_price=1.0,
                    turnover=100.0,
                    source="test",
                    freshness_status="stale" if history_kind == "stale" else "fresh",
                    raw_json={
                        "decision_eligible": history_kind != "stale",
                        "consensus_status": (
                            CONSENSUS_STALE if history_kind == "stale" else CONSENSUS_CONSISTENT
                        ),
                    },
                )
            )
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.01,
                change_percent=0.5,
                turnover=150.0,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["score_source"] == "intraday"
    assert item["intraday_component_status"]["activity"]["status"] == "unavailable"
    assert item["intraday_adjustment_score"] == 1.0
    assert not any("同刻历史中位数" in reason for reason in item["score_contribution_reasons"])


@pytest.mark.asyncio
async def test_live_rankings_reports_unavailable_structure_components_without_weight_transfer(
    client, app, monkeypatch
) -> None:
    now = datetime(2026, 6, 17, 10, 0, 0)
    await _seed_signal_run(app, count=1, canonical=True, as_of_date=date(2026, 6, 16))
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.01,
                change_percent=0.5,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings")

    assert response.status_code == 200
    item = response.json()["items"][0]
    components = item["intraday_component_status"]
    assert components["price"]["status"] == "available"
    assert components["premium_discount"]["status"] == "unavailable"
    assert components["spread"]["status"] == "unavailable"
    assert components["activity"]["status"] == "unavailable"
    assert item["intraday_adjustment_score"] == 1.0


@pytest.mark.asyncio
async def test_scheduled_intraday_watch_skips_closed_market_without_fetching(
    app, monkeypatch
) -> None:
    called = False

    def fake_fetcher() -> pd.DataFrame:
        nonlocal called
        called = True
        return pd.DataFrame()

    monkeypatch.setattr(
        "app.services.intraday_etf.jobs.current_market_state",
        lambda: MarketState("closed", None, datetime(2026, 6, 17, 11, 30, tzinfo=ASIA_SHANGHAI)),
    )

    async with app.state.db.session() as session:
        result = await intraday_etf_watch_job(session, run_type="scheduled", fetcher=fake_fetcher)

    assert result["status"] == "skipped"
    assert called is False


@pytest.mark.asyncio
async def test_watchlist_uses_all_eligible_etfs_without_research_or_tracking_sources(app) -> None:
    conclusions = [CONCLUSION_WATCH] * 22
    conclusions[20] = CONCLUSION_HIGH_WATCH
    conclusions[21] = CONCLUSION_WATCH
    await _seed_signal_run(app, count=22, conclusions=conclusions)
    async with app.state.db.session() as session:
        session.add(_etf("159998", "Eligible ETF"))
        session.add(_etf("159999", "Tracked ETF"))
        session.add(
            TrackedPosition(
                user_id=1,
                asset_type="etf",
                asset_code="159999",
                asset_name="Tracked ETF",
                buy_date=date(2026, 6, 12),
                buy_amount=3000,
                entry_price=1.0,
                entry_price_date=date(2026, 6, 12),
                estimated_shares=3000,
                status="active",
            )
        )
        await session.commit()

        watchlist = await build_watchlist(session)

    assert watchlist.message == "使用全部可交易 ETF 盘中行情；研究筛选和用户持仓由上层服务编排。"
    assert len(watchlist.items) == 24
    assert [item.etf_code for item in watchlist.items[:3]] == ["159998", "159999", "510000"]
    assert watchlist.items[0].rank is None
    assert watchlist.items[0].sources == {"all_etf"}
    eligible = next(item for item in watchlist.items if item.etf_code == "159998")
    assert eligible.rank is None
    assert eligible.sources == {"all_etf"}
    tracked = next(item for item in watchlist.items if item.etf_code == "159999")
    assert tracked.rank is None
    assert tracked.sources == {"all_etf"}
    assert watchlist.signal_status == "all_etf"


@pytest.mark.asyncio
async def test_live_rankings_order_and_rank_change(client, app, monkeypatch) -> None:
    run_id = await _seed_signal_run(
        app,
        count=3,
        conclusions=[CONCLUSION_WATCH, CONCLUSION_WATCH, CONCLUSION_HIGH_WATCH],
        total_scores=[60.0, 70.0, 80.0],
        canonical=True,
        as_of_date=date.today(),
    )
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI), 30),
    )
    async with app.state.db.session() as session:
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.0,
                change_percent=1.0,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        session.add(
            EtfIntradayQuote(
                etf_code="510001",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.2,
                change_percent=3.0,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        session.add(
            EtfIntradayQuote(
                etf_code="510002",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.4,
                change_percent=-1.0,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings?limit=2&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["watched_count"] == 3
    assert body["next_poll_seconds"] == 30
    assert body["snapshot"]["snapshot_id"] == run_id
    assert body["snapshot"]["freshness_status"] == "ready"
    assert body["live_scope_hash"]
    items = body["items"]
    assert [item["etf_code"] for item in items] == ["510002", "510001"]
    assert items[0]["live_rank"] == 1
    assert items[0]["base_rank"] == 3
    assert items[0]["base_global_rank"] == 3
    assert items[0]["live_scope_rank"] == 1
    assert items[0]["filtered_position"] == 1
    assert items[0]["rank_scope"] == "live_scope"
    assert items[0]["rank_change"] == 2
    assert items[0]["score_source"] == "intraday"
    assert items[0]["intraday_adjustment_score"] is not None
    assert any("盘中涨跌" in reason for reason in items[0]["score_contribution_reasons"])
    assert items[0]["live_entry_timing_label"] == "健康回踩"
    assert items[1]["live_rank"] == 2
    assert items[1]["base_rank"] == 2
    assert items[1]["base_global_rank"] == 2
    assert items[1]["live_scope_rank"] == 2
    assert items[1]["filtered_position"] == 2
    assert items[1]["rank_change"] == 0
    assert items[1]["live_entry_timing_label"] == "冲高别追"


@pytest.mark.asyncio
async def test_live_rankings_exposes_fresh_quote_without_score_from_stale_daily_base(
    client, app, monkeypatch
) -> None:
    await _seed_signal_run(
        app,
        count=1,
        total_scores=[80.0],
        canonical=True,
        as_of_date=date(2026, 6, 12),
    )
    now = datetime(2026, 6, 17, 10, 0, 0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.02,
                change_percent=1.0,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["quote"]["decision_eligible"] is True
    assert item["base_score"] is None
    assert item["score_source"] == "unavailable"
    assert item["live_total_score"] is None
    assert item["live_scope_rank"] is None
    assert any("基座" in reason for reason in item["score_contribution_reasons"])


@pytest.mark.asyncio
async def test_live_rankings_keep_base_rank_for_equal_scores(client, app, monkeypatch) -> None:
    await _seed_signal_run(app, count=2, total_scores=[80.0, 80.0], canonical=True)
    now = datetime(2026, 6, 12, 16, 0, 0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("closed", "after_close", now.replace(tzinfo=ASIA_SHANGHAI)),
    )

    response = await client.get("/api/etf-quotes/live-rankings")

    assert response.status_code == 200
    assert [item["etf_code"] for item in response.json()["items"]] == ["510000", "510001"]
    assert [
        (item["base_global_rank"], item["live_scope_rank"], item["rank_change"])
        for item in response.json()["items"]
    ] == [(1, 1, 0), (2, 2, 0)]


@pytest.mark.asyncio
async def test_live_rankings_pins_the_signal_run_selected_for_its_watchlist(
    client, app, monkeypatch
) -> None:
    run_id = await _seed_signal_run(app, count=1)
    calls = 0

    async def one_source_run(session, **_kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("live ranking must not resolve a second signal run")
        run = await session.get(ShortResearchSignalRun, run_id)
        assert run is not None
        return CanonicalSnapshotSelection(state="ready", run=run)

    monkeypatch.setattr(
        live_ranking_workflow,
        "current_etf_ranking_surface_selection",
        one_source_run,
    )

    response = await client.get("/api/etf-quotes/live-rankings?limit=10")

    assert response.status_code == 200
    assert response.json()["items"][0]["base_rank"] == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_detail_and_portfolio_reuse_the_provided_signal_run(app, monkeypatch) -> None:
    run_id = await _seed_signal_run(app, count=1, canonical=True)
    series_cutoffs: list[datetime | None] = []

    async def unexpected_latest_signal_run(*_args, **_kwargs):
        raise AssertionError("a pinned signal run must not be resolved again")

    async def cutoff_pinned_etf_series(
        _session, _code, _as_of_date=None, *, data_cutoff: datetime | None = None
    ):
        series_cutoffs.append(data_cutoff)
        return []

    monkeypatch.setattr(short_research_service, "latest_signal_run", unexpected_latest_signal_run)
    monkeypatch.setattr(short_research_service, "_etf_series", cutoff_pinned_etf_series)

    async with app.state.db.session() as session:
        run = await session.get(ShortResearchSignalRun, run_id)
        assert run is not None
        asset, _chart, _sections = await short_research_service.get_asset_detail(
            session,
            "etf",
            "510000",
            source_run=run,
        )
        portfolio = await short_research_service.etf_observation_portfolio(
            session,
            source_run=run,
            use_snapshot=False,
            include_optimized=False,
        )

    assert asset.metadata.code == "510000"
    assert portfolio["daily_signal_date"] == run.as_of_date
    assert series_cutoffs
    assert set(series_cutoffs) == {run.data_cutoff}


@pytest.mark.asyncio
async def test_validation_summary_uses_only_the_pinned_source_run(app) -> None:
    async with app.state.db.session() as session:
        source_run = ShortResearchSignalRun(status="success", as_of_date=date(2026, 6, 12))
        other_run = ShortResearchSignalRun(status="success", as_of_date=date(2026, 6, 12))
        session.add_all([source_run, other_run])
        await session.flush()
        source_item = ShortResearchSignalItem(
            run_id=source_run.id,
            asset_type="etf",
            asset_code="510100",
            rank=1,
            total_score=70.0,
            conclusion=CONCLUSION_WATCH,
        )
        other_item = ShortResearchSignalItem(
            run_id=other_run.id,
            asset_type="etf",
            asset_code="510101",
            rank=1,
            total_score=60.0,
            conclusion=CONCLUSION_HIGH_WATCH,
        )
        session.add_all([source_item, other_item])
        await session.flush()
        session.add_all(
            [
                EtfLabelOutcome(
                    signal_item_id=source_item.id,
                    signal_run_id=source_run.id,
                    asset_code=source_item.asset_code,
                    label=CONCLUSION_WATCH,
                    entry_timing_label="趋势延续",
                    signal_date=source_run.as_of_date,
                    horizon_days=5,
                    forward_return=0.03,
                    status="completed",
                ),
                EtfLabelOutcome(
                    signal_item_id=other_item.id,
                    signal_run_id=other_run.id,
                    asset_code=other_item.asset_code,
                    label=CONCLUSION_HIGH_WATCH,
                    entry_timing_label="冲高别追",
                    signal_date=other_run.as_of_date,
                    horizon_days=5,
                    forward_return=-0.02,
                    status="completed",
                ),
            ]
        )
        await session.commit()
        summary = await short_research_service._label_outcome_summary(
            session,
            source_run.as_of_date,
            source_signal_run_id=source_run.id,
        )

    assert summary["asset_count"] == 1
    assert [group["label"] for group in summary["groups"]] == [CONCLUSION_WATCH]


@pytest.mark.asyncio
async def test_live_rankings_search_keeps_global_rank_and_does_not_rank_incomparable_item(
    client, app, monkeypatch
) -> None:
    await _seed_signal_run(
        app, count=3, total_scores=[60.0, 70.0, 80.0], canonical=True, as_of_date=date.today()
    )
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI), 30),
    )
    async with app.state.db.session() as session:
        session.add_all(
            [
                EtfIntradayQuote(
                    etf_code="510001",
                    quote_time=now,
                    trade_date=now.date(),
                    latest_price=1.2,
                    change_percent=3.0,
                    source="test",
                    freshness_status="fresh",
                    raw_json={"decision_eligible": True},
                ),
                EtfIntradayQuote(
                    etf_code="510002",
                    quote_time=now,
                    trade_date=now.date(),
                    latest_price=1.4,
                    change_percent=-1.0,
                    source="test",
                    freshness_status="fresh",
                    raw_json={"decision_eligible": True},
                ),
            ]
        )
        await session.commit()

    ranked_response = await client.get("/api/etf-quotes/live-rankings?q=510001")
    assert ranked_response.status_code == 200
    ranked_item = ranked_response.json()["items"][0]
    assert ranked_item["etf_code"] == "510001"
    assert ranked_item["live_rank"] == 2
    assert ranked_item["base_global_rank"] == 2
    assert ranked_item["live_scope_rank"] == 2
    assert ranked_item["filtered_position"] == 1
    assert ranked_item["rank_scope"] == "live_scope"
    assert ranked_item["rank_change"] == 0

    incomparable_response = await client.get("/api/etf-quotes/live-rankings?q=510000")
    assert incomparable_response.status_code == 200
    incomparable_item = incomparable_response.json()["items"][0]
    assert incomparable_item["etf_code"] == "510000"
    assert incomparable_item["live_rank"] is None
    assert incomparable_item["live_scope_rank"] is None
    assert incomparable_item["filtered_position"] == 1
    assert incomparable_item["rank_scope"] == "unranked"
    assert incomparable_item["rank_change"] is None


@pytest.mark.asyncio
async def test_live_rank_change_requires_matching_scope_and_score_version(
    client, app, monkeypatch
) -> None:
    await _seed_signal_run(app, count=2, total_scores=[80.0, 70.0])
    now = datetime(2026, 6, 12, 16, 0, 0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("closed", "after_close", now.replace(tzinfo=ASIA_SHANGHAI)),
    )

    comparable = await client.get("/api/etf-quotes/live-rankings?q=510000")
    assert comparable.status_code == 200
    assert comparable.json()["items"][0]["rank_change"] == 0


@pytest.mark.asyncio
async def test_live_rank_change_rejects_mismatched_item_score_version(
    client,
    app,
    monkeypatch,
) -> None:
    await _seed_signal_run(
        app,
        count=2,
        total_scores=[80.0, 70.0],
        metrics_overrides={"510000": {"score_version": "other_score_version"}},
    )
    now = datetime(2026, 6, 12, 16, 0, 0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("closed", "after_close", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    mismatched_version = await client.get("/api/etf-quotes/live-rankings?q=510000")
    assert mismatched_version.status_code == 200
    assert mismatched_version.json()["items"][0]["rank_change"] is None


@pytest.mark.asyncio
async def test_live_rank_change_rejects_mismatched_scope(client, app, monkeypatch) -> None:
    await _seed_signal_run(app, count=2, total_scores=[80.0, 70.0])
    now = datetime(2026, 6, 12, 16, 0, 0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("closed", "after_close", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(_etf("159999", "Scope-only ETF"))
        await session.commit()

    mismatched_scope = await client.get("/api/etf-quotes/live-rankings?q=510000")
    assert mismatched_scope.status_code == 200
    assert mismatched_scope.json()["items"][0]["rank_change"] is None


@pytest.mark.asyncio
async def test_live_tracking_filter_is_limited_to_current_user(client, app, monkeypatch) -> None:
    await _seed_signal_run(app, count=2, total_scores=[80.0, 70.0])
    now = datetime(2026, 6, 12, 16, 0, 0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("closed", "after_close", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        owner = await session.get(User, 1)
        assert owner is not None
        other = User(
            email="live-tracking-other@example.com",
            recipient_email="live-tracking-other@example.com",
            is_approved=True,
        )
        session.add(other)
        await session.flush()
        session.add_all(
            [
                TrackedPosition(
                    user_id=owner.id,
                    asset_type="etf",
                    asset_code="510001",
                    asset_name="ETF510001",
                    buy_date=date(2026, 6, 12),
                    buy_amount=1000,
                    status="active",
                ),
                TrackedPosition(
                    user_id=other.id,
                    asset_type="etf",
                    asset_code="510000",
                    asset_name="ETF510000",
                    buy_date=date(2026, 6, 12),
                    buy_amount=1000,
                    status="active",
                ),
            ]
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings?tracking_states=我已持仓")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["etf_code"] == "510001"
    assert body["items"][0]["filtered_position"] == 1
    assert "tracked_position" not in body["items"][0]["sources"]

    unauthenticated = await client.get(
        "/api/etf-quotes/live-rankings?tracking_states=我已持仓",
        headers={"Authorization": ""},
    )
    assert unauthenticated.status_code == 401
    unsupported = await client.get("/api/etf-quotes/live-rankings?tracking_states=未知状态")
    assert unsupported.status_code == 400


@pytest.mark.asyncio
async def test_live_rankings_keeps_intraday_entry_timing_when_daily_cache_is_high_chase(
    client, app, monkeypatch
) -> None:
    daily_timing = {
        "entry_timing_label": "冲高别追",
        "entry_timing_reason": "今天 3.09%，且近20日 22.23%、近60日 44.86% 已经不低，追高风险上升。",
    }
    run_id = await _seed_signal_run(
        app,
        count=1,
        conclusions=[CONCLUSION_HIGH_WATCH],
        total_scores=[94.6],
        canonical=True,
        as_of_date=date.today(),
        metrics_overrides={"510000": daily_timing},
    )
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "afternoon", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        signal_item = await session.scalar(
            select(ShortResearchSignalItem).where(
                ShortResearchSignalItem.run_id == run_id,
                ShortResearchSignalItem.asset_code == "510000",
            )
        )
        assert signal_item is not None
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=2.447,
                change_percent=-6.1,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        await session.commit()

    response = await client.get("/api/etf-quotes/live-rankings?limit=1")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["etf_code"] == "510000"
    assert item["score_source"] == "intraday"
    assert item["quote"]["change_percent"] == -6.1
    assert item["live_entry_timing_label"] == "跌破等待"
    assert "跌破" in item["live_entry_timing_reason"]
    assert item["daily_entry_timing_label"] == "冲高别追"
    assert "今天 3.09%" in item["daily_entry_timing_reason"]


@pytest.mark.asyncio
async def test_live_rankings_filters_labels_before_pagination_and_keeps_daily_entry_when_closed(
    client, app, monkeypatch
) -> None:
    await _seed_signal_run(
        app,
        count=3,
        conclusions=[CONCLUSION_WATCH, CONCLUSION_HIGH_WATCH, CONCLUSION_WATCH],
        total_scores=[100.0, 99.0, 98.0],
        canonical=True,
        metrics_overrides={
            "510002": {
                "entry_timing_label": "健康回踩",
                "entry_timing_reason": "休市后仍按日线健康回踩筛选。",
            }
        },
    )

    now = datetime(2026, 6, 12, 16, 0, 0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("closed", "after_close", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    high_response = await client.get(
        "/api/etf-quotes/live-rankings?limit=1&observation_labels=高位观察"
    )
    assert high_response.status_code == 200
    high_body = high_response.json()
    assert high_body["total"] == 1
    assert high_body["items"][0]["etf_code"] == "510001"
    assert high_body["items"][0]["live_rank"] == 2
    assert high_body["items"][0]["live_scope_rank"] == 2
    assert high_body["items"][0]["filtered_position"] == 1
    assert high_body["items"][0]["rank_change"] == 0

    entry_response = await client.get("/api/etf-quotes/live-rankings?limit=1&entry_labels=健康回踩")
    assert entry_response.status_code == 200
    entry_body = entry_response.json()
    assert entry_body["total"] == 1
    assert entry_body["items"][0]["etf_code"] == "510002"
    assert entry_body["items"][0]["live_rank"] == 3
    assert entry_body["items"][0]["live_scope_rank"] == 3
    assert entry_body["items"][0]["filtered_position"] == 1
    assert entry_body["items"][0]["rank_change"] == 0
    assert entry_body["items"][0]["live_entry_timing_label"] == "数据不足"
    assert entry_body["items"][0]["daily_entry_timing_label"] == "健康回踩"


def test_postgres_quote_record_serializes_raw_json_as_valid_json_text() -> None:
    record = {
        "etf_code": "510001",
        "quote_time": datetime(2026, 6, 30, 14, 59),
        "raw_json": {
            "consensus_status": CONSENSUS_SINGLE_PROVIDER,
            "provider_quotes": [{"provider": "akshare", "latest_price": 1.23}],
            "decision_eligible": True,
        },
    }

    serialized = _postgres_quote_record(record)

    assert isinstance(serialized["raw_json"], str)
    assert json.loads(serialized["raw_json"]) == record["raw_json"]
    assert "'consensus_status'" not in serialized["raw_json"]


def test_quote_raw_accepts_json_text_from_postgresql_bulk_insert() -> None:
    quote = EtfIntradayLatestQuote(
        etf_code="510001",
        quote_time=datetime(2026, 6, 30, 14, 59),
        trade_date=date(2026, 6, 30),
        latest_price=1.23,
        source="akshare",
        raw_json=json.dumps({"consensus_status": CONSENSUS_SINGLE_PROVIDER}),
    )

    assert intraday_service.quote_consensus_status(quote) == CONSENSUS_SINGLE_PROVIDER


@pytest.mark.asyncio
async def test_live_rankings_marks_data_insufficient_without_faking_quote(
    client, app, monkeypatch
) -> None:
    now = datetime(2026, 6, 17, 10, 0, 0)
    await _seed_signal_run(app, count=1, canonical=True, as_of_date=date(2026, 6, 16))
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    response = await client.get("/api/etf-quotes/live-rankings")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    items = body["items"]
    assert len(items) == 1
    assert items[0]["etf_code"] == "510000"
    assert items[0]["base_score"] == 100
    assert items[0]["live_total_score"] == 100
    assert items[0]["score_source"] == "daily"
    assert items[0]["intraday_adjustment_score"] is None
    assert any("日线基础分" in reason for reason in items[0]["score_contribution_reasons"])
    assert items[0]["quote"] is None
    assert items[0]["live_entry_timing_label"] == "数据不足"
    assert items[0]["live_entry_timing_reason"] == "暂无新鲜盘中行情，暂不做盘中加分。"
    assert items[0]["daily_entry_timing_label"] == "趋势延续"
    assert items[0]["daily_entry_timing_reason"] == "日线趋势仍在。"


@pytest.mark.asyncio
async def test_quote_normalization_stale_handling_and_persist_all_eligible_etfs(app) -> None:
    await _seed_signal_run(app, count=1)
    quote_time = datetime.now().replace(microsecond=0)
    quote = normalize_spot_record(
        {
            "code": "510000",
            "latest_price": "1.234",
            "change_percent": "1.20",
            "volume": "10000",
            "turnover": "20000000",
            "bid_price": "1.233",
            "ask_price": "1.235",
            "iopv": "1.230",
            "premium_discount_pct": "0.33",
            "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    assert quote is not None
    assert quote.etf_code == "510000"
    assert quote.latest_price == 1.234
    assert quote.raw["quote_time_is_fallback"] is False
    assert is_quote_stale(datetime.now() - timedelta(minutes=5)) is True

    def fake_fetcher() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "code": "510000",
                    "latest_price": 1.25,
                    "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "provider_timestamp": pd.Timestamp(quote_time),
                },
                {
                    "code": "510999",
                    "latest_price": 1.55,
                    "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                {
                    "code": "999999",
                    "latest_price": 9.99,
                    "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            ]
        )

    async with app.state.db.session() as session:
        session.add(_etf("510999", "Non Signal ETF"))
        await session.commit()

        result = await intraday_etf_watch_job(
            session, run_type="manual", force=True, fetcher=fake_fetcher
        )
        rows = (
            await session.scalars(select(EtfIntradayQuote).order_by(EtfIntradayQuote.etf_code))
        ).all()

    assert result["details"]["signal_run_id"] is None
    assert result["details"]["signal_status"] == "all_etf"
    assert result["updated_quote_count"] == 2
    assert [row.etf_code for row in rows] == ["510000", "510999"]
    assert rows[0].raw_json["provider_timestamp"] == quote_time.isoformat()


def test_quote_normalization_marks_missing_time_as_display_only() -> None:
    fallback_time = datetime(2026, 6, 17, 10, 1, 0, tzinfo=ASIA_SHANGHAI)
    quote = normalize_spot_record(
        {
            "code": "510000",
            "latest_price": "1.234",
            "change_percent": "0.8",
        },
        fallback_time=fallback_time,
    )

    assert quote is not None
    assert quote.quote_time == fallback_time.replace(tzinfo=None)
    assert quote.raw["quote_time_is_fallback"] is True


def test_quote_normalization_maps_akshare_etf_spot_fields() -> None:
    quote = normalize_spot_record(
        {
            "代码": "510300",
            "最新价": "4.123",
            "买一": "4.122",
            "卖一": "4.124",
            "IOPV实时估值": "4.120",
            "基金折价率": "0.07",
            "更新时间": "2026-07-13 14:59:00+08:00",
        }
    )

    assert quote is not None
    assert quote.bid_price == 4.122
    assert quote.ask_price == 4.124
    assert quote.iopv == 4.12
    assert quote.premium_discount_pct == 0.07


def test_quote_normalization_parses_timezone_update_time() -> None:
    quote = normalize_spot_record(
        {
            "代码": "513520",
            "最新价": "2.585",
            "涨跌幅": "0.12",
            "更新时间": "2026-06-22 12:47:26+08:00",
        }
    )

    assert quote is not None
    assert quote.etf_code == "513520"
    assert quote.quote_time == datetime(2026, 6, 22, 12, 47, 26)
    assert quote.trade_date == date(2026, 6, 22)
    assert quote.raw["quote_time_is_fallback"] is False


@pytest.mark.asyncio
async def test_eastmoney_spot_row_fetch_preserves_complete_universe_rows(monkeypatch) -> None:
    rows = [
        {"f12": "510300", "f14": "沪深300ETF", "f2": 4.1},
        {"f12": "511010", "f14": "国债ETF", "f2": None},
    ]

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"data": {"total": len(rows), "diff": rows}}

    class FakeAsyncClient:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, _url: str, *, params: dict[str, str]) -> FakeResponse:
            assert params["pn"] == "1"
            assert "b:MK0827" in params["fs"]
            return FakeResponse()

    monkeypatch.setattr("app.services.intraday_etf.service.httpx.AsyncClient", FakeAsyncClient)

    result = await intraday_service.fetch_eastmoney_etf_spot_rows()

    assert result.error is None
    assert result.expected_total == 2
    assert result.complete is True
    assert list(result.rows) == rows


@pytest.mark.asyncio
async def test_eastmoney_spot_row_fetch_has_total_wall_clock_timeout(monkeypatch) -> None:
    class FakeAsyncClient:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, _url: str, *, params: dict[str, str]) -> None:
            await asyncio.Event().wait()

    monkeypatch.setattr("app.services.intraday_etf.service.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        intraday_service, "EASTMONEY_PROVIDER_TOTAL_TIMEOUT_SECONDS", 0.01, raising=False
    )

    result = await intraday_service.fetch_eastmoney_etf_spot_rows()

    assert result.complete is False
    assert result.error is not None
    assert "timeout" in result.error.lower()


@pytest.mark.asyncio
async def test_eastmoney_provider_fetches_all_pages(monkeypatch) -> None:
    quote_timestamp = int(datetime(2026, 6, 25, 14, 57, 0, tzinfo=ASIA_SHANGHAI).timestamp())
    calls: list[dict[str, str]] = []

    def row(code: str, price: float) -> dict[str, object]:
        return {
            "f12": code,
            "f14": f"ETF{code}",
            "f2": price,
            "f3": 0.5,
            "f5": 1000,
            "f6": 1_000_000,
            "f124": quote_timestamp,
        }

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, _url: str, *, params: dict[str, str]) -> FakeResponse:
            calls.append(dict(params))
            page = params["pn"]
            if page == "1":
                rows = [row("510000", 1.0), row("510001", 1.1)]
            elif page == "2":
                rows = [row("510002", 1.2)]
            else:
                rows = []
            return FakeResponse({"data": {"total": 3, "diff": rows}})

    monkeypatch.setattr("app.services.intraday_etf.service.httpx.AsyncClient", FakeAsyncClient)

    result = await _fetch_eastmoney_provider()

    assert set(result.quotes) == {"510000", "510001", "510002"}
    assert [call["pn"] for call in calls] == ["1", "2"]


@pytest.mark.asyncio
async def test_eastmoney_provider_retries_transient_disconnect(monkeypatch) -> None:
    quote_timestamp = int(datetime(2026, 6, 25, 14, 57, 0, tzinfo=ASIA_SHANGHAI).timestamp())
    calls: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": {
                    "total": 1,
                    "diff": [
                        {
                            "f12": "510000",
                            "f14": "ETF510000",
                            "f2": 1.0,
                            "f3": 0.5,
                            "f5": 1000,
                            "f6": 1_000_000,
                            "f124": quote_timestamp,
                        }
                    ],
                }
            }

    class FakeAsyncClient:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, _url: str, *, params: dict[str, str]) -> FakeResponse:
            calls.append(params["pn"])
            if len(calls) == 1:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return FakeResponse()

    monkeypatch.setattr("app.services.intraday_etf.service.httpx.AsyncClient", FakeAsyncClient)

    result = await _fetch_eastmoney_provider()

    assert result.error is None
    assert set(result.quotes) == {"510000"}
    assert calls == ["1", "1"]


@pytest.mark.asyncio
async def test_akshare_provider_clears_expired_backoff(monkeypatch) -> None:
    monkeypatch.setattr(
        intraday_service,
        "_PROVIDER_BACKOFF_UNTIL",
        datetime.now(ASIA_SHANGHAI) - timedelta(seconds=1),
    )
    monkeypatch.setattr(intraday_service, "_PROVIDER_FAILURE_COUNT", 2)

    def fake_fetcher() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "code": "510000",
                    "latest_price": 1.0,
                    "quote_time": "2026-06-25 14:57:00",
                }
            ]
        )

    monkeypatch.setattr(intraday_service.ak, "fund_etf_spot_em", fake_fetcher)

    result = await intraday_service._fetch_akshare_provider()

    assert result.error is None
    assert set(result.quotes) == {"510000"}
    assert intraday_service._PROVIDER_BACKOFF_UNTIL is None


@pytest.mark.asyncio
async def test_akshare_provider_reports_blank_exception_type() -> None:
    def broken_fetcher() -> pd.DataFrame:
        raise TimeoutError()

    result = await intraday_service._fetch_akshare_provider(broken_fetcher)

    assert result.error is not None
    assert "TimeoutError" in result.error


@pytest.mark.asyncio
async def test_intraday_watch_reports_watch_codes_missing_from_provider(app) -> None:
    await _seed_signal_run(app, count=2)
    quote_time = datetime.now().replace(microsecond=0)

    def fake_fetcher() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "code": "510000",
                    "latest_price": 1.25,
                    "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            ]
        )

    async with app.state.db.session() as session:
        result = await intraday_etf_watch_job(
            session, run_type="manual", force=True, fetcher=fake_fetcher
        )

    assert result["updated_quote_count"] == 1
    assert result["details"]["missing_watch_count"] == 1
    assert result["details"]["missing_watch_codes"] == ["510001"]
    assert result["details"]["quote_audit"]["510000"]["decision_eligible"] is True
    assert result["details"]["quote_audit"]["510000"]["quote_time_is_fallback"] is False
    assert result["details"]["quote_audit"]["510001"]["decision_eligible"] is False
    assert result["details"]["quote_audit"]["510001"]["quote_freshness"] == "unavailable"


@pytest.mark.asyncio
async def test_etf_entry_uses_manual_price_then_fresh_intraday_fallback(app, monkeypatch) -> None:
    await _seed_price_history(app, "512800")
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            EtfIntradayQuote(
                etf_code="512800",
                quote_time=now,
                trade_date=now.date(),
                latest_price=0.814,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        await session.commit()

        manual = await create_position(
            session,
            asset_type="etf",
            asset_code="512800",
            user_id=1,
            buy_amount=3000,
            buy_date=now.date(),
            order_time_bucket="before_15",
            confirmed_nav_date=None,
            confirmed_nav=0.8,
        )
        fallback = await create_position(
            session,
            asset_type="etf",
            asset_code="512800",
            user_id=1,
            buy_amount=3000,
            buy_date=now.date(),
            order_time_bucket="before_15",
            confirmed_nav_date=None,
            confirmed_nav=None,
        )

    assert manual.entry_price == 0.8
    assert fallback.entry_price == 0.814
    assert fallback.entry_price_date == now.date()


@pytest.mark.asyncio
async def test_etf_entry_requires_manual_price_without_fresh_intraday_quote(app) -> None:
    await _seed_price_history(app, "512800", start_price=0.8)
    buy_date = date(2026, 6, 17)

    async with app.state.db.session() as session:
        position = await create_position(
            session,
            asset_type="etf",
            asset_code="512800",
            user_id=1,
            buy_amount=3000,
            buy_date=buy_date,
            order_time_bucket="before_15",
            confirmed_nav_date=None,
            confirmed_nav=None,
        )

    assert position.entry_price is None
    assert position.entry_price_date is None
    assert position.estimated_shares is None


@pytest.mark.asyncio
async def test_dynamic_hard_stop_and_intraday_cooldown(app, settings, monkeypatch) -> None:
    await _seed_signal_run(app, count=1)
    await _seed_price_history(app, "510000")
    now = datetime.now().replace(microsecond=0)
    sent: list[dict] = []

    async def fake_send_template(
        self, session, *, recipient: str, template_name: str, payload: dict
    ) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="ETF510000",
            buy_date=date(2026, 6, 1),
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=date(2026, 6, 1),
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=0.95,
                bid_price=0.949,
                ask_price=0.951,
                turnover=100_000_000,
                source="test",
                freshness_status="fresh",
                raw_json={"decision_eligible": True},
            )
        )
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        await session.refresh(position)

        first_alert, first_status = await create_alert_if_needed(
            session, position, settings, evaluation_mode="intraday"
        )
        second_alert, second_status = await create_alert_if_needed(
            session, position, settings, evaluation_mode="intraday"
        )
        alerts = (await session.scalars(select(TrackedPositionAlert))).all()

    assert first_status == "email_sent"
    assert first_alert is not None
    assert first_alert.alert_type == "hard_stop"
    assert first_alert.alert_level == "urgent"
    assert first_alert.alert_source == "intraday_quote"
    assert first_alert.threshold_context_json["alert_type"] == "hard_stop"
    assert first_alert.threshold_context_json["hard_stop_pct"] is not None
    assert first_alert.threshold_context_json["threshold_mode"] in {
        "rule_dynamic_v2",
        "fixed_fallback",
    }
    assert sent[0]["threshold_context"]["alert_type"] == "hard_stop"
    assert (
        sent[0]["threshold_context"]["hard_stop_pct"]
        == first_alert.threshold_context_json["hard_stop_pct"]
    )
    assert second_status == "suppressed"
    assert second_alert is not None
    assert second_alert.id == first_alert.id
    assert len(alerts) == 1
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_intraday_email_requires_fresh_quote_and_skips_daily_close_fallback(
    app, settings, monkeypatch
) -> None:
    await _seed_signal_run(app, count=1)
    await _seed_price_history_from_closes(
        app,
        "510000",
        [1.00, 0.99, 0.98, 0.97, 0.96, 0.95],
        start=datetime.now().date() - timedelta(days=8),
    )
    sent: list[dict] = []

    async def fake_send_template(
        self, session, *, recipient: str, template_name: str, payload: dict
    ) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="ETF510000",
            buy_date=date(2026, 6, 1),
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=date(2026, 6, 1),
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        await session.refresh(position)

        alert, status = await create_alert_if_needed(
            session, position, settings, evaluation_mode="intraday"
        )
        alerts = (await session.scalars(select(TrackedPositionAlert))).all()
        analysis = await position_analysis(session, position)

    assert alert is None
    assert status == "data_ineligible"
    assert alerts == []
    assert sent == []
    assert analysis.intraday_snapshot is not None
    assert analysis.intraday_snapshot.price_source == "daily_close"
    assert analysis.intraday_snapshot.email_eligible is False
    assert analysis.exit_signal.email_eligible is False


@pytest.mark.asyncio
async def test_intraday_structure_warning_is_web_only(app, settings, monkeypatch) -> None:
    await _seed_signal_run(app, count=1)
    await _seed_price_history_from_closes(
        app,
        "510000",
        [1.00, 1.002, 1.001, 1.003, 1.002, 1.001, 1.000, 1.001, 1.000, 1.001],
        start=datetime.now().date() - timedelta(days=14),
    )
    now = datetime.now().replace(microsecond=0)
    sent: list[dict] = []

    async def fake_send_template(
        self, session, *, recipient: str, template_name: str, payload: dict
    ) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="Structure ETF",
            buy_date=now.date() - timedelta(days=10),
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=now.date() - timedelta(days=10),
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.001,
                bid_price=0.995,
                ask_price=1.007,
                turnover=5_000_000,
                iopv=None,
                premium_discount_pct=1.2,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        await session.commit()
        await session.refresh(position)

        alert, status = await create_alert_if_needed(session, position, settings)

    assert alert is not None
    assert alert.alert_type == "risk_warning"
    assert alert.alert_level == "watch"
    assert alert.email_status == "skipped"
    assert status == "web_only"
    assert sent == []


@pytest.mark.asyncio
async def test_dynamic_trailing_profit_trend_and_structure_warnings(app) -> None:
    today = datetime.now().date()
    base = today - timedelta(days=14)

    await _seed_price_history_from_closes(
        app,
        "510880",
        [1.00, 1.01, 1.02, 1.04, 1.06, 1.08, 1.075, 1.07, 1.065, 1.06],
        start=base,
    )
    await _seed_price_history_from_closes(
        app,
        "510881",
        [1.005, 1.004, 1.003, 1.002, 1.001, 1.000, 0.998, 0.996, 0.994, 0.992],
        start=base,
    )
    await _seed_price_history_from_closes(
        app,
        "510882",
        [1.00, 1.002, 1.001, 1.003, 1.002, 1.001, 1.000, 1.001, 1.000, 1.001],
        start=base,
    )
    now = datetime.now().replace(microsecond=0)

    async with app.state.db.session() as session:
        trailing_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510880",
            asset_name="Trailing ETF",
            buy_date=base,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=base,
            estimated_shares=3000,
            status="active",
        )
        trend_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510881",
            asset_name="Trend ETF",
            buy_date=base,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=base,
            estimated_shares=3000,
            status="active",
        )
        structure_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510882",
            asset_name="Structure ETF",
            buy_date=base,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=base,
            estimated_shares=3000,
            status="active",
        )
        session.add_all([trailing_position, trend_position, structure_position])
        session.add_all(
            [
                EtfIntradayQuote(
                    etf_code="510880",
                    quote_time=now,
                    trade_date=today,
                    latest_price=1.055,
                    bid_price=1.054,
                    ask_price=1.056,
                    turnover=100_000_000,
                    iopv=1.055,
                    source="test",
                    freshness_status="fresh",
                    raw_json={"decision_eligible": True},
                ),
                EtfIntradayQuote(
                    etf_code="510881",
                    quote_time=now,
                    trade_date=today,
                    latest_price=0.99,
                    bid_price=0.989,
                    ask_price=0.991,
                    turnover=100_000_000,
                    iopv=0.99,
                    source="test",
                    freshness_status="fresh",
                    raw_json={"decision_eligible": True},
                ),
                EtfIntradayQuote(
                    etf_code="510882",
                    quote_time=now,
                    trade_date=today,
                    latest_price=1.001,
                    bid_price=0.995,
                    ask_price=1.007,
                    turnover=5_000_000,
                    iopv=None,
                    premium_discount_pct=1.2,
                    source="test",
                    freshness_status="fresh",
                    raw_json={"decision_eligible": True},
                ),
            ]
        )
        await session.commit()
        await session.refresh(trailing_position)
        await session.refresh(trend_position)
        await session.refresh(structure_position)

        trailing = await position_analysis(session, trailing_position)
        trend = await position_analysis(session, trend_position)
        structure = await position_analysis(session, structure_position)

    assert trailing.exit_signal.alert_type == "trailing_take_profit"
    assert trailing.exit_signal.level == "warning"
    assert trailing.dynamic_thresholds is not None
    assert trailing.dynamic_thresholds.trailing_giveback_pct is not None
    assert trend.exit_signal.alert_type == "confirmed_trend_weakening"
    assert trend.dynamic_thresholds is not None
    assert trend.dynamic_thresholds.trend_weakening is True
    assert structure.exit_signal.alert_type == "risk_warning"
    assert structure.exit_signal.level == "watch"
    assert structure.dynamic_thresholds is not None
    assert structure.dynamic_thresholds.liquidity_warnings
    assert structure.dynamic_thresholds.structure_warnings


@pytest.mark.asyncio
async def test_high_volatility_etf_receives_wider_dynamic_thresholds(app) -> None:
    today = datetime.now().date()
    start = today - timedelta(days=10)
    await _seed_price_history_from_closes(
        app,
        "510890",
        [1.000, 1.002, 1.001, 1.003, 1.004, 1.003, 1.005, 1.006, 1.005, 1.007],
        start=start,
    )
    await _seed_price_history_from_closes(
        app,
        "510891",
        [1.00, 1.06, 1.01, 1.09, 1.02, 1.11, 1.04, 1.13, 1.05, 1.12],
        start=start,
    )

    async with app.state.db.session() as session:
        low_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510890",
            asset_name="Low Vol ETF",
            buy_date=start,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=start,
            estimated_shares=3000,
            status="active",
        )
        high_position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510891",
            asset_name="High Vol ETF",
            buy_date=start,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=start,
            estimated_shares=3000,
            status="active",
        )
        session.add_all([low_position, high_position])
        await session.commit()
        await session.refresh(low_position)
        await session.refresh(high_position)

        low = await position_analysis(session, low_position)
        high = await position_analysis(session, high_position)

    assert low.dynamic_thresholds is not None
    assert high.dynamic_thresholds is not None
    assert high.dynamic_thresholds.hard_stop_pct is not None
    assert low.dynamic_thresholds.hard_stop_pct is not None
    assert high.dynamic_thresholds.trailing_giveback_pct is not None
    assert low.dynamic_thresholds.trailing_giveback_pct is not None
    assert high.dynamic_thresholds.hard_stop_pct < low.dynamic_thresholds.hard_stop_pct
    assert (
        high.dynamic_thresholds.trailing_giveback_pct > low.dynamic_thresholds.trailing_giveback_pct
    )


@pytest.mark.asyncio
async def test_missing_iopv_warning_is_web_only(app, settings, monkeypatch) -> None:
    today = datetime.now().date()
    start = today - timedelta(days=10)
    await _seed_signal_run(app, count=1)
    await _seed_price_history_from_closes(
        app,
        "510000",
        [1.00, 1.01, 1.00, 1.01, 1.00, 1.01, 1.00, 1.01, 1.00, 1.01],
        start=start,
    )
    sent: list[dict[str, object]] = []

    async def fake_send_template(
        self, session, *, recipient: str, template_name: str, payload: dict
    ) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)

    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.smtp_host = "smtp.163.com"
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="ETF510000",
            buy_date=start,
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=start,
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=datetime.now().replace(microsecond=0),
                trade_date=today,
                latest_price=1.01,
                bid_price=1.009,
                ask_price=1.011,
                turnover=100_000_000,
                iopv=None,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()
        await session.refresh(position)
        alert, status = await create_alert_if_needed(session, position, settings)

    assert alert is not None
    assert status == "web_only"
    assert alert.alert_type == "risk_warning"
    assert alert.email_status == "skipped"
    assert sent == []


@pytest.mark.asyncio
async def test_intraday_watch_status_api_and_tracked_position_fields(
    client, app, monkeypatch
) -> None:
    await _seed_signal_run(app, count=1)
    await _seed_price_history(app, "510000")
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            TrackedPosition(
                user_id=1,
                asset_type="etf",
                asset_code="510000",
                asset_name="ETF510000",
                buy_date=date(2026, 6, 1),
                buy_amount=3000,
                entry_price=1.0,
                entry_price_date=date(2026, 6, 1),
                estimated_shares=3000,
                status="active",
            )
        )
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.02,
                bid_price=1.019,
                ask_price=1.021,
                iopv=1.018,
                premium_discount_pct=0.2,
                turnover=80_000_000,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

    status_response = await client.get("/api/etf-quotes/tracked")
    tracked_response = await client.get("/api/tracked-positions")

    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["watched_count"] == 1
    assert status_body["items"][0]["quote"]["latest_price"] == 1.02
    assert tracked_response.status_code == 200
    tracked = tracked_response.json()["items"][0]
    assert tracked["intraday_snapshot"]["price_source"] == "intraday_quote"
    assert tracked["intraday_snapshot"]["current_price"] == 1.02
    assert tracked["dynamic_thresholds"]["hard_stop_pct"] is not None
    assert tracked["recent_intraday_alerts"] == []


@pytest.mark.asyncio
async def test_intraday_provider_failure_falls_back_to_cached_quote(app, monkeypatch) -> None:
    await _seed_signal_run(app, count=1)
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )
    async with app.state.db.session() as session:
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=1.01,
                source="test",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

        def broken_fetcher() -> pd.DataFrame:
            raise RuntimeError("provider down")

        result = await intraday_etf_watch_job(
            session, run_type="manual", force=True, fetcher=broken_fetcher
        )
        quote_count = await session.scalar(select(func.count()).select_from(EtfIntradayQuote))

    assert result["status"] == "degraded"
    assert result["updated_quote_count"] == 0
    assert result["details"]["provider_error"]
    assert quote_count == 1


@pytest.mark.asyncio
async def test_persist_quotes_writes_latest_snapshot_and_dedupes_history(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510001"))
        await session.flush()
        watchlist = WatchlistResult(
            items=[WatchItem(etf_code="510001")],
            signal_run_id=None,
            signal_as_of_date=None,
            signal_status="ready",
            message="test",
        )
        first = _normalized_provider_quote("510001", 1.0, "akshare", datetime(2026, 6, 18, 9, 31))
        second = _normalized_provider_quote("510001", 1.2, "akshare", datetime(2026, 6, 18, 9, 32))
        conflicting_second = _normalized_provider_quote(
            "510001", 9.9, "eastmoney", datetime(2026, 6, 18, 9, 32)
        )

        assert await persist_quotes(session, watchlist, {"510001": first}) == 1
        assert await persist_quotes(session, watchlist, {"510001": second}) == 1
        assert await persist_quotes(session, watchlist, {"510001": conflicting_second}) == 1

        latest_count = await session.scalar(
            select(func.count()).select_from(EtfIntradayLatestQuote)
        )
        history_count = await session.scalar(select(func.count()).select_from(EtfIntradayQuote))
        latest = await session.get(EtfIntradayLatestQuote, "510001")
        frozen_history = await session.scalar(
            select(EtfIntradayQuote).where(
                EtfIntradayQuote.etf_code == "510001",
                EtfIntradayQuote.quote_time == datetime(2026, 6, 18, 9, 32),
            )
        )

    assert latest_count == 1
    assert history_count == 2
    assert latest is not None
    assert frozen_history is not None
    assert latest.latest_price == 1.2
    assert frozen_history.latest_price == 1.2
    assert latest.quote_time == datetime(2026, 6, 18, 9, 32)


@pytest.mark.asyncio
async def test_latest_quotes_by_code_prefers_latest_snapshot(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510001"))
        await session.flush()
        session.add(
            EtfIntradayQuote(
                etf_code="510001",
                quote_time=datetime(2026, 6, 18, 9, 35),
                trade_date=date(2026, 6, 18),
                latest_price=1.1,
            )
        )
        session.add(
            EtfIntradayLatestQuote(
                etf_code="510001",
                quote_time=datetime(2026, 6, 18, 9, 34),
                trade_date=date(2026, 6, 18),
                latest_price=1.3,
                source="snapshot",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

        quotes = await latest_quotes_by_code(session, ["510001"])

    assert quotes["510001"].latest_price == 1.3
    assert quotes["510001"].source == "snapshot"


@pytest.mark.asyncio
async def test_latest_intraday_quote_prefers_latest_snapshot_for_tracking_consistency(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510001"))
        await session.flush()
        session.add(
            EtfIntradayQuote(
                etf_code="510001",
                quote_time=datetime(2026, 6, 18, 9, 35),
                trade_date=date(2026, 6, 18),
                latest_price=1.1,
                source="history",
                freshness_status="fresh",
                raw_json={},
            )
        )
        session.add(
            EtfIntradayLatestQuote(
                etf_code="510001",
                quote_time=datetime(2026, 6, 18, 9, 34),
                trade_date=date(2026, 6, 18),
                latest_price=1.3,
                source="snapshot",
                freshness_status="fresh",
                raw_json={},
            )
        )
        await session.commit()

        quote = await latest_intraday_quote(session, "510001")

    assert quote is not None
    assert quote.latest_price == 1.3
    assert quote.source == "snapshot"


@pytest.mark.asyncio
async def test_latest_quotes_by_code_returns_one_latest_quote_per_etf(app) -> None:
    async with app.state.db.session() as session:
        session.add_all([_etf("510001"), _etf("510002")])
        await session.flush()
        session.add_all(
            [
                EtfIntradayQuote(
                    etf_code="510001",
                    quote_time=datetime(2026, 6, 18, 9, 31),
                    trade_date=date(2026, 6, 18),
                    latest_price=1.0,
                ),
                EtfIntradayQuote(
                    etf_code="510001",
                    quote_time=datetime(2026, 6, 18, 9, 35),
                    trade_date=date(2026, 6, 18),
                    latest_price=1.1,
                ),
                EtfIntradayQuote(
                    etf_code="510002",
                    quote_time=datetime(2026, 6, 18, 9, 33),
                    trade_date=date(2026, 6, 18),
                    latest_price=2.0,
                ),
            ]
        )
        await session.commit()

        quotes = await latest_quotes_by_code(session, ["510001", "510002", "510001", "599999"])

    assert set(quotes) == {"510001", "510002"}
    assert quotes["510001"].latest_price == 1.1
    assert quotes["510002"].latest_price == 2.0


@pytest.mark.asyncio
async def test_quotes_at_cutoff_selects_latest_history_row_and_ignores_later_quote(app) -> None:
    trade_date = date(2026, 6, 18)
    cutoff = datetime(2026, 6, 18, 15, 0)
    async with app.state.db.session() as session:
        session.add_all([_etf("510011"), _etf("510012"), _etf("510013")])
        await session.flush()
        session.add_all(
            [
                EtfIntradayQuote(
                    etf_code="510011",
                    quote_time=datetime(2026, 6, 18, 14, 45),
                    trade_date=trade_date,
                    latest_price=1.0,
                    created_at=datetime(2026, 6, 18, 6, 45),
                ),
                EtfIntradayQuote(
                    etf_code="510011",
                    quote_time=datetime(2026, 6, 18, 14, 55),
                    trade_date=trade_date,
                    latest_price=1.1,
                    created_at=datetime(2026, 6, 18, 6, 55),
                ),
                EtfIntradayQuote(
                    etf_code="510011",
                    quote_time=datetime(2026, 6, 18, 15, 1),
                    trade_date=trade_date,
                    latest_price=9.9,
                    created_at=datetime(2026, 6, 18, 7, 1),
                ),
                EtfIntradayQuote(
                    etf_code="510012",
                    quote_time=datetime(2026, 6, 18, 14, 50),
                    trade_date=trade_date,
                    latest_price=2.0,
                    created_at=datetime(2026, 6, 18, 6, 50),
                ),
                EtfIntradayQuote(
                    etf_code="510012",
                    quote_time=datetime(2026, 6, 17, 14, 59),
                    trade_date=date(2026, 6, 17),
                    latest_price=8.8,
                    created_at=datetime(2026, 6, 17, 6, 59),
                ),
                EtfIntradayQuote(
                    etf_code="510013",
                    quote_time=datetime(2026, 6, 18, 14, 50),
                    trade_date=trade_date,
                    latest_price=3.0,
                    # captured at 15:05 Asia/Shanghai, after the 15:00 decision cutoff
                    created_at=datetime(2026, 6, 18, 7, 5),
                ),
            ]
        )
        await session.commit()

        quotes = await intraday_service.quotes_at_or_before_cutoff(
            session,
            ["510011", "510012", "510013", "510011", "599999"],
            trade_date=trade_date,
            decision_cutoff=cutoff,
        )

    assert set(quotes) == {"510011", "510012"}
    assert quotes["510011"].quote_time == datetime(2026, 6, 18, 14, 55)
    assert quotes["510011"].latest_price == 1.1
    assert quotes["510012"].latest_price == 2.0


@pytest.mark.asyncio
async def test_intraday_cleanup_summarizes_and_deletes_old_raw_quotes(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510003"))
        await session.flush()
        for offset in range(4):
            trade_date = date(2026, 6, 10 + offset)
            session.add_all(
                [
                    EtfIntradayQuote(
                        etf_code="510003",
                        quote_time=datetime(2026, 6, 10 + offset, 9, 31),
                        trade_date=trade_date,
                        latest_price=1.0 + offset,
                        volume=1000 + offset,
                        turnover=10_000 + offset,
                    ),
                    EtfIntradayQuote(
                        etf_code="510003",
                        quote_time=datetime(2026, 6, 10 + offset, 14, 59),
                        trade_date=trade_date,
                        latest_price=1.1 + offset,
                        volume=2000 + offset,
                        turnover=20_000 + offset,
                    ),
                ]
            )
        await session.commit()

        result = await summarize_and_cleanup_intraday_quotes(
            session,
            retention_trading_days=2,
            evidence_seal_complete=True,
        )
        raw_count = await session.scalar(select(func.count()).select_from(EtfIntradayQuote))
        summary_count = await session.scalar(
            select(func.count()).select_from(EtfIntradayDailySummary)
        )
        first_summary = await session.scalar(
            select(EtfIntradayDailySummary).where(
                EtfIntradayDailySummary.trade_date == date(2026, 6, 10)
            )
        )

    assert result["cutoff_date"] == "2026-06-12"
    assert result["summarized_groups"] == 2
    assert result["deleted_rows"] == 4
    assert raw_count == 4
    assert summary_count == 2
    assert first_summary is not None
    assert first_summary.total_volume == 2000
    assert first_summary.total_turnover == 20_000
    assert first_summary.summary_json["volume_semantics"] == "max_cumulative_snapshot"


@pytest.mark.asyncio
async def test_intraday_cleanup_fails_closed_until_evidence_seal_is_complete(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510004"))
        await session.flush()
        for offset in range(3):
            trade_date = date(2026, 6, 10 + offset)
            session.add(
                EtfIntradayQuote(
                    etf_code="510004",
                    quote_time=datetime(2026, 6, 10 + offset, 14, 50),
                    trade_date=trade_date,
                    latest_price=1.0 + offset,
                )
            )
        await session.commit()

        result = await summarize_and_cleanup_intraday_quotes(
            session,
            retention_trading_days=1,
        )
        raw_count = await session.scalar(select(func.count()).select_from(EtfIntradayQuote))

    assert result["job_status"] == "partial"
    assert result["unavailable_reason"] == "intraday_quote_evidence_seal_incomplete"
    assert result["deleted_rows"] == 0
    assert raw_count == 3


@pytest.mark.asyncio
async def test_intraday_cleanup_never_deletes_protected_quote_and_persists_checkpoint(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510005"))
        await session.flush()
        protected_quote: EtfIntradayQuote | None = None
        for offset in range(4):
            trade_date = date(2026, 6, 10 + offset)
            first = EtfIntradayQuote(
                etf_code="510005",
                quote_time=datetime(2026, 6, 10 + offset, 9, 31),
                trade_date=trade_date,
                latest_price=1.0 + offset,
            )
            session.add_all(
                [
                    first,
                    EtfIntradayQuote(
                        etf_code="510005",
                        quote_time=datetime(2026, 6, 10 + offset, 14, 59),
                        trade_date=trade_date,
                        latest_price=1.1 + offset,
                    ),
                ]
            )
            if offset == 0:
                protected_quote = first
        await session.flush()
        assert protected_quote is not None
        session.add(
            EtfIntradayQuoteEvidenceRef(
                quote_id=protected_quote.id,
                owner_kind="published_snapshot",
                owner_id=1,
                asset_code="510005",
                evidence_purpose="actionable_ranking_input",
                evidence_state="protected",
                quote_hash=intraday_quote_evidence_hash(protected_quote),
                decision_cutoff=datetime(2026, 6, 10, 15, 0),
                receipt_cutoff=datetime(2026, 6, 10, 15, 1),
            )
        )
        await session.commit()

        result = await summarize_and_cleanup_intraday_quotes(
            session,
            retention_trading_days=2,
            batch_size=4,
            evidence_seal_complete=True,
        )
        remaining_ids = set(await session.scalars(select(EtfIntradayQuote.id)))
        checkpoint = await session.get(EtfIntradayCleanupCheckpoint, 1)

    assert result["deleted_rows"] == 3
    assert protected_quote.id in remaining_ids
    assert checkpoint is not None
    assert checkpoint.status == "advanced"
    assert checkpoint.last_trade_date == date(2026, 6, 11)
    assert checkpoint.last_etf_code == "510005"
    assert checkpoint.deleted_rows_total == 3


@pytest.mark.asyncio
async def test_intraday_cleanup_rejects_non_positive_price_without_deleting(app) -> None:
    async with app.state.db.session() as session:
        session.add(_etf("510006"))
        await session.flush()
        for offset, price in enumerate((-1.0, 1.1, 1.2)):
            session.add(
                EtfIntradayQuote(
                    etf_code="510006",
                    quote_time=datetime(2026, 6, 10 + offset, 14, 50),
                    trade_date=date(2026, 6, 10 + offset),
                    latest_price=price,
                )
            )
        await session.commit()

        result = await summarize_and_cleanup_intraday_quotes(
            session,
            retention_trading_days=1,
            evidence_seal_complete=True,
        )
        raw_count = await session.scalar(select(func.count()).select_from(EtfIntradayQuote))

    assert result["job_status"] == "partial"
    assert result["unavailable_reason"] == "intraday_cleanup_invalid_price"
    assert result["deleted_rows"] == 0
    assert raw_count == 3


def _normalized_provider_quote(
    code: str, price: float, source: str, quote_time: datetime
) -> object:
    quote = normalize_spot_record(
        {
            "code": code,
            "latest_price": price,
            "change_percent": 0.5,
            "turnover": 100_000_000,
            "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        fallback_time=quote_time.replace(tzinfo=ASIA_SHANGHAI),
        source=source,
    )
    assert quote is not None
    return quote


def _normalized_provider_premium_quote(
    code: str,
    price: float,
    iopv: float,
    source: str,
    quote_time: datetime,
) -> object:
    quote = normalize_spot_record(
        {
            "code": code,
            "latest_price": price,
            "iopv": iopv,
            "bid_price": price - 0.0001,
            "ask_price": price + 0.0001,
            "quote_time": quote_time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        fallback_time=quote_time.replace(tzinfo=ASIA_SHANGHAI),
        source=source,
    )
    assert quote is not None
    return quote


def test_select_consensus_quotes_marks_consistent_and_single_provider() -> None:
    now = datetime(2026, 6, 24, 10, 0, 0)
    ak_quote = _normalized_provider_quote("510000", 1.0000, "akshare", now)
    eastmoney_quote = _normalized_provider_quote("510000", 1.0005, "eastmoney", now)

    consistent = select_consensus_quotes(
        [
            ProviderQuoteResult("akshare", {"510000": ak_quote}),
            ProviderQuoteResult("eastmoney", {"510000": eastmoney_quote}),
        ],
        now=now,
    )

    selected = consistent.quotes["510000"]
    assert selected.raw["consensus_status"] == CONSENSUS_CONSISTENT
    assert selected.raw["decision_eligible"] is True
    assert selected.raw["provider_count"] == 2
    assert consistent.consensus_counts[CONSENSUS_CONSISTENT] == 1

    single = select_consensus_quotes(
        [
            ProviderQuoteResult("akshare", {"510001": ak_quote}),
            ProviderQuoteResult("eastmoney", {}, error="provider down"),
        ],
        now=now,
    )

    assert single.quotes["510001"].raw["consensus_status"] == CONSENSUS_SINGLE_PROVIDER
    assert single.quotes["510001"].raw["decision_eligible"] is True
    assert single.consensus_counts[CONSENSUS_SINGLE_PROVIDER] == 1


def test_select_consensus_quotes_persists_premium_consensus_and_provider_provenance() -> None:
    now = datetime(2026, 6, 24, 10, 0, 0)
    ak_quote = _normalized_provider_premium_quote("510020", 1.0010, 1.0, "akshare", now)
    eastmoney_quote = _normalized_provider_premium_quote("510020", 1.0015, 1.0, "eastmoney", now)

    result = select_consensus_quotes(
        [
            ProviderQuoteResult("akshare", {"510020": ak_quote}),
            ProviderQuoteResult("eastmoney", {"510020": eastmoney_quote}),
        ],
        now=now,
    )

    raw = result.quotes["510020"].raw
    assert raw["premium_consensus_status"] == CONSENSUS_CONSISTENT
    assert raw["premium_provider_consensus"] == 100
    assert raw["premium_provider_count"] == 2
    assert raw["premium_dispersion_bps"] == pytest.approx(5.0)
    ak_provenance, eastmoney_provenance = raw["provider_quotes"]
    assert ak_provenance["provider"] == "akshare"
    assert ak_provenance["latest_price"] == 1.001
    assert ak_provenance["iopv"] == 1.0
    assert ak_provenance["premium_discount_bps"] == pytest.approx(10.0)
    assert ak_provenance["bid_price"] == pytest.approx(1.0009)
    assert ak_provenance["ask_price"] == pytest.approx(1.0011)
    assert ak_provenance["quote_time"] == now.isoformat()
    assert eastmoney_provenance["provider"] == "eastmoney"
    assert eastmoney_provenance["latest_price"] == 1.0015
    assert eastmoney_provenance["iopv"] == 1.0
    assert eastmoney_provenance["premium_discount_bps"] == pytest.approx(15.0)
    assert eastmoney_provenance["bid_price"] == pytest.approx(1.0014)
    assert eastmoney_provenance["ask_price"] == pytest.approx(1.0016)
    assert eastmoney_provenance["quote_time"] == now.isoformat()


def test_select_consensus_quotes_uses_contract_single_provider_score() -> None:
    now = datetime(2026, 6, 24, 10, 0, 0)
    quote = _normalized_provider_premium_quote("510021", 1.0010, 1.0, "akshare", now)

    result = select_consensus_quotes([ProviderQuoteResult("akshare", {"510021": quote})], now=now)

    raw = result.quotes["510021"].raw
    assert raw["premium_consensus_status"] == CONSENSUS_SINGLE_PROVIDER
    assert raw["premium_provider_consensus"] == 60
    assert raw["premium_provider_count"] == 1


def test_premium_divergence_is_independent_of_latest_price_agreement() -> None:
    now = datetime(2026, 6, 24, 10, 0, 0)
    ak_quote = _normalized_provider_premium_quote("510022", 1.001, 1.0, "akshare", now)
    eastmoney_quote = _normalized_provider_premium_quote("510022", 1.001, 0.99, "eastmoney", now)

    result = select_consensus_quotes(
        [
            ProviderQuoteResult("akshare", {"510022": ak_quote}),
            ProviderQuoteResult("eastmoney", {"510022": eastmoney_quote}),
        ],
        now=now,
    )

    raw = result.quotes["510022"].raw
    assert raw["consensus_status"] == CONSENSUS_CONSISTENT
    assert raw["premium_consensus_status"] == CONSENSUS_DIVERGED
    assert raw["premium_provider_consensus"] is None
    assert raw["premium_dispersion_bps"] > 30


def test_select_consensus_quotes_blocks_diverged_and_missing_time_quotes() -> None:
    now = datetime(2026, 6, 24, 10, 0, 0)
    ak_quote = _normalized_provider_quote("510000", 1.0000, "akshare", now)
    eastmoney_quote = _normalized_provider_quote("510000", 1.0200, "eastmoney", now)

    diverged = select_consensus_quotes(
        [
            ProviderQuoteResult("akshare", {"510000": ak_quote}),
            ProviderQuoteResult("eastmoney", {"510000": eastmoney_quote}),
        ],
        now=now,
    )

    selected = diverged.quotes["510000"]
    assert selected.raw["consensus_status"] == CONSENSUS_DIVERGED
    assert selected.raw["decision_eligible"] is False
    assert selected.raw["decision_ineligible_reason"]

    fallback_quote = normalize_spot_record(
        {"code": "510002", "latest_price": 1.0, "change_percent": 0.1},
        fallback_time=now.replace(tzinfo=ASIA_SHANGHAI),
        source="akshare",
    )
    assert fallback_quote is not None
    stale = select_consensus_quotes(
        [ProviderQuoteResult("akshare", {"510002": fallback_quote})], now=now
    )

    assert stale.quotes["510002"].raw["consensus_status"] == CONSENSUS_STALE
    assert stale.quotes["510002"].raw["decision_eligible"] is False
    assert stale.consensus_counts[CONSENSUS_STALE] == 1


@pytest.mark.asyncio
async def test_diverged_quote_does_not_drive_live_ranking_or_tracked_email(
    client, app, settings, monkeypatch
) -> None:
    now = datetime(2026, 6, 17, 10, 0, 0)
    await _seed_signal_run(
        app,
        count=1,
        total_scores=[80.0],
        canonical=True,
        as_of_date=date(2026, 6, 16),
    )
    await _seed_price_history(app, "510000")
    sent: list[dict] = []

    async def fake_send_template(
        self, session, *, recipient: str, template_name: str, payload: dict
    ) -> str:
        sent.append(payload)
        return "sent"

    monkeypatch.setattr("app.services.notifier.Notifier.send_template", fake_send_template)
    monkeypatch.setattr(
        "app.services.intraday_etf.service.current_market_state",
        lambda: MarketState("open", "morning", now.replace(tzinfo=ASIA_SHANGHAI)),
    )

    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code="510000",
            asset_name="ETF510000",
            buy_date=date(2026, 6, 1),
            buy_amount=3000,
            entry_price=1.0,
            entry_price_date=date(2026, 6, 1),
            estimated_shares=3000,
            status="active",
        )
        session.add(position)
        session.add(
            EtfIntradayQuote(
                etf_code="510000",
                quote_time=now,
                trade_date=now.date(),
                latest_price=0.95,
                change_percent=-5.0,
                source="akshare",
                freshness_status="display_only",
                raw_json={
                    "consensus_status": CONSENSUS_DIVERGED,
                    "decision_eligible": False,
                    "provider_count": 2,
                    "fresh_provider_count": 2,
                    "price_diff_pct": 1.2,
                    "decision_ineligible_reason": "多行情源价格分歧，只能网页参考。",
                },
            )
        )
        await session.commit()
        await session.refresh(position)

        alert, status = await create_alert_if_needed(
            session, position, settings, evaluation_mode="intraday"
        )

    ranking_response = await client.get("/api/etf-quotes/live-rankings")
    item = ranking_response.json()["items"][0]

    assert item["score_source"] == "daily"
    assert item["quote"]["decision_eligible"] is False
    assert item["quote"]["consensus_status"] == CONSENSUS_DIVERGED
    assert item["intraday_component_status"]["consensus"]["status"] == "unavailable"
    assert status in {"data_ineligible", "web_only"}
    if alert is not None:
        assert alert.email_status == "skipped"
    assert sent == []
