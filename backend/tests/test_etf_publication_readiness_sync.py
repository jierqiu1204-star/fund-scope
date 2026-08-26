from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

import app.services.short_etf.bounded_history_sync as bounded_history_sync
from app.models.entities import (
    EtfAdjustedHistoryAvailability,
    EtfPriceHistory,
    EtfSyncCursor,
    TradableEtf,
)
from app.services.short_etf.bounded_history_sync import (
    BoundedHistorySyncRequest,
    PublicationReadinessCandidate,
    plan_publication_readiness_candidates,
)
from app.services.short_etf.data import ProviderFetchResult
from app.services.workflows.etf_publish_readiness import (
    CONSERVATIVE_PUBLICATION_PROFILE,
    MAXIMUM_PUBLICATION_PROFILE,
    build_publication_readiness_request,
    compact_readiness_payload,
)


def _request() -> BoundedHistorySyncRequest:
    return build_publication_readiness_request(
        profile=CONSERVATIVE_PUBLICATION_PROFILE,
        trade_date=date(2026, 7, 20),
        from_date=date(2026, 4, 1),
        contract_hash="a" * 64,
        universe_hash="b" * 64,
        eligible_codes=("510001", "510002"),
    )


def test_publication_candidate_planner_prioritizes_daily_then_warmup_and_rotates() -> None:
    candidates = (
        PublicationReadinessCandidate(
            code="510001",
            has_target_date=False,
            warmup_depth=60,
            priority=False,
        ),
        PublicationReadinessCandidate(
            code="510002",
            has_target_date=True,
            warmup_depth=1,
            priority=True,
        ),
        PublicationReadinessCandidate(
            code="510003",
            has_target_date=False,
            warmup_depth=0,
            priority=True,
        ),
        PublicationReadinessCandidate(
            code="510004",
            has_target_date=True,
            warmup_depth=5,
            priority=False,
        ),
    )

    ordered = plan_publication_readiness_candidates(candidates)
    rotated = plan_publication_readiness_candidates(candidates, rotation_anchor="510003")

    assert [item.code for item in ordered] == ["510001", "510003", "510004", "510002"]
    assert [item.code for item in rotated] == ["510004", "510002", "510001", "510003"]


def test_publication_request_freezes_identity_and_resource_profile() -> None:
    request = _request()

    assert request.selection_policy == "publication_readiness"
    assert request.target_trade_date == date(2026, 7, 20)
    assert request.required_sessions == 61
    assert request.max_codes == 10
    assert request.page_size == 500
    assert request.max_rows == 5_000
    assert request.rss_limit_bytes == 512 * 1024 * 1024
    assert request.admission_deadline_seconds == 45.0
    assert request.worker_deadline_seconds == 55.0
    assert request.process_deadline_seconds == 60.0
    assert request.provider_timeout_seconds == 6.0
    assert request.provider_policy_version
    assert request.adjustment_contract
    assert request.price_basis == "total_return_adjusted"
    assert request.identity_hash != replace(
        request,
        provider_policy_version=f"{request.provider_policy_version}-changed",
    ).identity_hash


def test_publication_profiles_allow_at_most_twenty_codes() -> None:
    conservative = _request()
    maximum = build_publication_readiness_request(
        profile=MAXIMUM_PUBLICATION_PROFILE,
        trade_date=date(2026, 7, 20),
        from_date=date(2026, 4, 1),
        contract_hash="a" * 64,
        universe_hash="b" * 64,
        eligible_codes=("510001", "510002"),
    )

    assert conservative.max_codes == 10
    assert maximum.max_codes == 20
    with pytest.raises(ValueError, match="publication readiness max_codes"):
        replace(maximum, max_codes=21)


def test_compact_readiness_payload_does_not_expand_full_universe() -> None:
    pending = [f"51{index:04d}" for index in range(1_500)]
    payload = compact_readiness_payload(
        {
            "target_date": "2026-07-20",
            "universe": {
                "snapshot_hash": "b" * 64,
                "expected_count": 1_500,
                "codes": pending,
            },
            "daily_freshness": {
                "expected_count": 1_500,
                "covered_count": 20,
                "excluded_count": 1_480,
                "coverage_ratio": 20 / 1_500,
                "pending_codes": pending,
            },
            "history_depth_61": {
                "expected_count": 1_500,
                "covered_count": 10,
                "excluded_count": 1_490,
                "coverage_ratio": 10 / 1_500,
                "pending_codes": pending,
            },
        }
    )

    assert payload["universe"] == {
        "snapshot_hash": "b" * 64,
        "expected_count": 1_500,
    }
    assert len(payload["daily_freshness"]["pending_samples"]) == 20
    assert len(payload["history_depth_61"]["pending_samples"]) == 20
    assert "pending_codes" not in payload["daily_freshness"]
    assert "codes" not in payload["universe"]
    assert len(str(payload)) < 2_500


def _etf(code: str, *, watchlist: bool = False) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=f"ETF-{code}",
        exchange="SH",
        theme_tags_json=[],
        trading_rule_label="证券账户 T+1 ETF",
        asset_class="sector",
        is_short_term_eligible=True,
        is_watchlist=watchlist,
    )


def _history(code: str, trade_date: date) -> EtfPriceHistory:
    return EtfPriceHistory(
        etf_code=code,
        trade_date=trade_date,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1_000_000.0,
        turnover=100_000_000.0,
        pct_change=0.0,
        research_adjusted_value=1.0,
        research_price_basis="total_return_adjusted",
        data_provider="eastmoney",
        provider_version="eastmoney.push2his.kline.hfq_v1",
        adjustment_version="eastmoney.push2his.kline.hfq_v1",
        decision_eligible=True,
    )


@pytest.mark.asyncio
async def test_publication_database_candidates_keep_daily_and_warmup_independent(app) -> None:
    daily_gap = "510011"
    warmup_gap = "510012"
    request = replace(
        _request(),
        eligible_codes=(daily_gap, warmup_gap),
        max_codes=1,
    )
    prior_dates = request.required_trade_dates[:-1]

    async with app.state.db.session() as session:
        session.add_all([_etf(daily_gap), _etf(warmup_gap, watchlist=True)])
        session.add_all(_history(daily_gap, item) for item in prior_dates)
        session.add(_history(warmup_gap, request.target_trade_date))
        await session.commit()

        candidates = await bounded_history_sync._publication_readiness_candidates(
            session,
            request=request,
        )

    by_code = {candidate.code: candidate for candidate in candidates}
    assert by_code[daily_gap].has_target_date is False
    assert by_code[daily_gap].warmup_depth == 60
    assert by_code[warmup_gap].has_target_date is True
    assert by_code[warmup_gap].warmup_depth == 1
    assert [
        candidate.code
        for candidate in plan_publication_readiness_candidates(candidates)
    ] == [daily_gap, warmup_gap]


@pytest.mark.asyncio
async def test_publication_slice_fetches_full_bounded_window_and_advances_factual_state(
    app,
) -> None:
    code = "510013"
    request = replace(_request(), eligible_codes=(code,), max_codes=1)
    calls: list[tuple[date, date]] = []

    async def fetch(_code: str, from_date: date, to_date: date) -> ProviderFetchResult:
        calls.append((from_date, to_date))
        rows = [
            {
                "date": item.isoformat(),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1_000_000.0,
                "turnover": 100_000_000.0,
                "pct_change": 0.0,
                "research_adjusted_value": 1.0,
                "research_price_basis": "total_return_adjusted",
                "provider_version": "eastmoney.push2his.kline.hfq_v1",
                "adjustment_version": "eastmoney.push2his.kline.hfq_v1",
            }
            for item in request.required_trade_dates
        ]
        return ProviderFetchResult(
            rows=rows,
            provider="eastmoney",
            fallback_used=False,
        )

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        result = await bounded_history_sync.run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )

    assert calls == [(request.from_date, request.to_date)]
    assert result.status == "complete"
    assert result.completed_codes == (code,)
    assert result.persisted_rows == 61


@pytest.mark.asyncio
async def test_publication_short_history_is_cooled_down_without_losing_denominator(
    app,
) -> None:
    code = "510016"
    request = replace(_request(), eligible_codes=(code,), max_codes=1)
    calls: list[str] = []
    returned_dates = (
        request.from_date,
        *(
            item
            for index, item in enumerate(request.required_trade_dates)
            if index != 10
        ),
    )
    assert len(returned_dates) == request.required_sessions
    assert request.from_date not in request.required_trade_dates

    async def fetch(
        current_code: str,
        _from: date,
        _to: date,
    ) -> ProviderFetchResult:
        calls.append(current_code)
        return ProviderFetchResult(
            rows=[
                {
                    "date": item.isoformat(),
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1_000_000.0,
                    "turnover": 100_000_000.0,
                    "pct_change": 0.0,
                    "research_adjusted_value": 1.0,
                    "research_price_basis": "total_return_adjusted",
                    "provider_version": "eastmoney.push2his.kline.hfq_v1",
                    "adjustment_version": "eastmoney.push2his.kline.hfq_v1",
                }
                for item in returned_dates
            ],
            provider="tencent",
            fallback_used=True,
        )

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        first = await bounded_history_sync.run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        observation = await session.get(
            EtfAdjustedHistoryAvailability,
            (code, request.provider_policy_version, request.scope, request.required_calendar_hash),
        )
        cursor = await session.get(EtfSyncCursor, request.scope)
        second = await bounded_history_sync.run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        assert observation is not None
        observation.observed_at = datetime.utcnow() - timedelta(minutes=31)
        await session.commit()
        third = await bounded_history_sync.run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )

    assert first.status == "partial"
    assert first.completed_codes == ()
    assert observation is not None
    assert observation.status == "source_history_shortfall"
    assert observation.eligible_session_count == request.required_sessions
    assert observation.retry_after is not None
    assert observation.evidence_json["inferred_listing_date"] is False
    assert observation.evidence_json["covered_required_sessions"] == 60
    assert cursor is not None
    assert cursor.last_priority_code == code
    assert cursor.last_lane == "history_attempt"
    assert second.status == "partial"
    assert second.stop_reason == "history_availability_cooldown"
    assert second.attempted_codes == ()
    assert third.attempted_codes == (code,)
    assert calls == [code, code]


@pytest.mark.asyncio
async def test_publication_page_interruption_resumes_idempotently(app) -> None:
    code = "510014"
    request = replace(_request(), eligible_codes=(code,), max_codes=1)

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=[
                {
                    "date": item.isoformat(),
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1_000_000.0,
                    "turnover": 100_000_000.0,
                    "pct_change": 0.0,
                    "research_adjusted_value": 1.0,
                    "research_price_basis": "total_return_adjusted",
                    "provider_version": "eastmoney.push2his.kline.hfq_v1",
                    "adjustment_version": "eastmoney.push2his.kline.hfq_v1",
                }
                for item in request.required_trade_dates
            ],
            provider="eastmoney",
            fallback_used=False,
        )

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        with pytest.raises(RuntimeError, match="publication page interruption"):
            await bounded_history_sync.run_bounded_history_sync_slice(
                session,
                request=request,
                fetcher=fetch,
                rss_reader=lambda: 32 * 1024 * 1024,
                before_page_commit=lambda: (_ for _ in ()).throw(
                    RuntimeError("publication page interruption")
                ),
            )
        count_after_failure = await session.scalar(
            select(func.count())
            .select_from(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code == code)
        )
        cursor_after_failure = await session.get(EtfSyncCursor, request.scope)
        resumed = await bounded_history_sync.run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        count_after_resume = await session.scalar(
            select(func.count())
            .select_from(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code == code)
        )

    assert count_after_failure == 0
    assert cursor_after_failure is not None
    assert cursor_after_failure.last_priority_code == code
    assert resumed.status == "complete"
    assert count_after_resume == 61


@pytest.mark.asyncio
async def test_publication_profile_stops_before_fetch_on_rss_limit(app) -> None:
    code = "510015"
    called = False
    request = replace(_request(), eligible_codes=(code,), max_codes=1)

    async def fetch(*_args: object) -> ProviderFetchResult:
        nonlocal called
        called = True
        raise AssertionError("RSS admission guard must stop provider work")

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        result = await bounded_history_sync.run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: request.rss_limit_bytes + 1,
        )

    assert result.status == "partial"
    assert result.stop_reason == "rss_limit"
    assert called is False


@pytest.mark.asyncio
async def test_lifetime_peak_above_limit_does_not_block_recovered_current_rss(app) -> None:
    code = "510017"
    called = False
    request = replace(_request(), eligible_codes=(code,), max_codes=1)

    async def fetch(*_args: object) -> ProviderFetchResult:
        nonlocal called
        called = True
        return ProviderFetchResult(
            rows=[],
            provider="eastmoney",
            fallback_used=False,
        )

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        result = await bounded_history_sync.run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 64 * 1024 * 1024,
            lifetime_peak_rss_reader=lambda: 768 * 1024 * 1024,
        )

    assert called is True
    assert result.stop_reason != "rss_limit"
    assert result.current_rss_bytes == 64 * 1024 * 1024
    assert result.slice_peak_current_rss_bytes == 64 * 1024 * 1024
    assert result.lifetime_peak_rss_bytes == 768 * 1024 * 1024
    assert result.rss_limit_bytes == 512 * 1024 * 1024


@pytest.mark.asyncio
async def test_publication_profile_fails_closed_when_current_rss_is_unavailable(
    app,
) -> None:
    code = "510018"
    called = False
    request = replace(_request(), eligible_codes=(code,), max_codes=1)

    async def fetch(*_args: object) -> ProviderFetchResult:
        nonlocal called
        called = True
        raise AssertionError("unavailable current RSS must stop provider work")

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        result = await bounded_history_sync.run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: None,
            lifetime_peak_rss_reader=lambda: 768 * 1024 * 1024,
        )

    assert called is False
    assert result.status == "partial"
    assert result.stop_reason == "current_rss_unavailable"
    assert result.current_rss_bytes is None
    assert result.lifetime_peak_rss_bytes == 768 * 1024 * 1024
