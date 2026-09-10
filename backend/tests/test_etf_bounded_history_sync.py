from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

import app.services.short_etf.bounded_history_sync as bounded_history_sync
from app.models.entities import (
    EtfAdjustedHistoryAvailability,
    EtfAdjustedPriceRevision,
    EtfPriceHistory,
    EtfSyncCursor,
    JobRun,
    TradableEtf,
)
from app.services.short_etf.bounded_history_sync import (
    BoundedHistorySyncRequest,
    read_process_rss_bytes,
    run_bounded_history_sync_slice,
)
from app.services.short_etf.data import ProviderFetchResult


def _etf(
    code: str,
    *,
    eligible: bool = True,
    watchlist: bool = False,
) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=f"ETF-{code}",
        exchange="SH",
        theme_tags_json=[],
        trading_rule_label="证券账户 T+1 ETF",
        asset_class="sector",
        is_short_term_eligible=eligible,
        is_watchlist=watchlist,
    )


def _rows(start: date, count: int, *, adjusted: bool = True) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for offset in range(count):
        current = start + timedelta(days=offset)
        close = 1.0 + offset * 0.01
        row: dict[str, float | str] = {
            "date": current.isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1_000_000.0,
            "turnover": 100_000_000.0,
            "pct_change": 0.0,
        }
        if adjusted:
            row.update(
                {
                    "research_adjusted_value": close,
                    "research_price_basis": "total_return_adjusted",
                    "adjustment_version": "eastmoney.push2his.kline.hfq_v1",
                    "provider_version": "eastmoney.push2his.kline.hfq_v1",
                }
            )
        rows.append(row)
    return rows


def _request(
    *,
    max_codes: int = 2,
    max_rows: int = 5_000,
    eligible_codes: tuple[str, ...] = ("510000",),
) -> BoundedHistorySyncRequest:
    return BoundedHistorySyncRequest(
        scope="history_depth_required:" + "a" * 64,
        contract_hash="a" * 64,
        universe_hash="b" * 64,
        eligible_codes=eligible_codes,
        from_date=date(2026, 7, 1),
        to_date=date(2026, 7, 3),
        required_sessions=3,
        max_codes=max_codes,
        page_size=2,
        max_rows=max_rows,
        admission_deadline_seconds=45.0,
        worker_deadline_seconds=55.0,
        process_deadline_seconds=60.0,
        rss_limit_bytes=768 * 1024 * 1024,
        provider_timeout_seconds=8.0,
    )


def _research_request(
    *,
    max_codes: int = 5,
    eligible_codes: tuple[str, ...] = ("510000",),
    required_sessions: int = 3,
) -> BoundedHistorySyncRequest:
    required_trade_dates = tuple(
        date(2026, 7, 1) + timedelta(days=offset) for offset in range(required_sessions)
    )
    return replace(
        _request(max_codes=1, eligible_codes=eligible_codes),
        scope="research_depth:" + "c" * 64,
        from_date=required_trade_dates[0],
        to_date=required_trade_dates[-1],
        required_sessions=required_sessions,
        required_trade_dates=required_trade_dates,
        max_codes=max_codes,
        rss_limit_bytes=512 * 1024 * 1024,
        provider_timeout_seconds=6.0,
        selection_policy="research_depth",
        provider_policy_version="adjusted-provider-policy-v1",
    )


def _revision(
    code: str,
    trade_date: date,
    *,
    provider: str,
    seen_at: datetime,
) -> EtfAdjustedPriceRevision:
    version = {
        "eastmoney": "eastmoney.push2his.kline.hfq_v1",
        "tickflow": "tickflow.free.klines.backward_v1",
    }[provider]
    marker = f"{code}:{trade_date.isoformat()}:{provider}"
    revision_hash = hashlib.sha256(f"revision:{marker}".encode()).hexdigest()
    payload_hash = hashlib.sha256(f"payload:{marker}".encode()).hexdigest()
    return EtfAdjustedPriceRevision(
        etf_code=code,
        trade_date=trade_date,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1_000_000.0,
        turnover=100_000_000.0,
        pct_change=0.0,
        raw_price_basis="raw_ohlc",
        research_adjusted_value=1.0,
        research_price_basis="total_return_adjusted",
        data_provider=provider,
        provider_version=version,
        source_timestamp=seen_at,
        adjustment_version=version,
        decision_eligible=True,
        decision_ineligibility_reason=None,
        first_seen_at=seen_at,
        observed_at=seen_at,
        payload_hash=payload_hash,
        revision_hash=revision_hash,
        created_at=seen_at,
    )


def test_process_rss_reader_returns_positive_value() -> None:
    assert read_process_rss_bytes() > 0


def test_research_depth_profile_enforces_resource_bounds() -> None:
    assert _research_request(max_codes=5).max_codes == 5
    assert _research_request(max_codes=20).max_codes == 20
    with pytest.raises(ValueError, match="between 5 and 20"):
        _research_request(max_codes=4)
    with pytest.raises(ValueError, match="between 5 and 20"):
        _research_request(max_codes=21)
    with pytest.raises(ValueError, match="512 MiB"):
        replace(
            _research_request(),
            rss_limit_bytes=513 * 1024 * 1024,
        )
    with pytest.raises(ValueError, match="6 seconds"):
        replace(_research_request(), provider_timeout_seconds=6.1)


@pytest.mark.asyncio
async def test_research_depth_preflight_requires_immutable_revisions(
    app,
) -> None:
    codes = ("510091", "510092", "510093", "510094")
    async with app.state.db.session() as session:
        session.add_all(
            [
                _etf("510091"),
                _etf("510092", watchlist=True),
                _etf("510093", watchlist=True),
                _etf("510094"),
            ]
        )
        for code in ("510092", "510094"):
            session.add(
                EtfPriceHistory(
                    etf_code=code,
                    trade_date=date(2026, 7, 1),
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
                    source_timestamp=datetime(2026, 7, 1, 15, 0),
                    adjustment_version="eastmoney.push2his.kline.hfq_v1",
                    decision_eligible=True,
                )
            )
        await session.commit()
        depths = await bounded_history_sync._eligible_depths(
            session,
            request=_research_request(
                eligible_codes=codes,
                required_sessions=3,
            ),
        )

    assert list(depths) == ["510092", "510093", "510091", "510094"]
    assert depths == {
        "510092": 0,
        "510093": 0,
        "510091": 0,
        "510094": 0,
    }


@pytest.mark.asyncio
async def test_research_depth_does_not_count_non_overlapping_providers_as_one_history(
    app,
) -> None:
    code = "510093"
    request = _research_request(eligible_codes=(code,), required_sessions=3)
    eastmoney_seen = datetime(2026, 7, 3, 6, 0)
    tickflow_seen = datetime(2026, 7, 3, 8, 0)

    async with app.state.db.session() as session:
        session.add(_etf(code))
        session.add_all(
            [
                _revision(
                    code,
                    request.required_trade_dates[index],
                    provider="eastmoney",
                    seen_at=eastmoney_seen,
                )
                for index in (0, 1)
            ]
            + [
                _revision(
                    code,
                    request.required_trade_dates[2],
                    provider="tickflow",
                    seen_at=tickflow_seen,
                )
            ]
        )
        await session.commit()

        depths = await bounded_history_sync._eligible_depths(
            session,
            request=request,
        )
        missing = await bounded_history_sync._missing_required_trade_dates(
            session,
            code=code,
            request=request,
        )
        complete = await bounded_history_sync._depth_is_complete(
            session,
            code=code,
            request=request,
        )

    assert depths == {code: 2}
    assert missing == (request.required_trade_dates[2],)
    assert complete is False


@pytest.mark.asyncio
async def test_research_depth_prioritizes_projection_only_migration(app) -> None:
    codes = ("510096", "510097")
    required_dates = (date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3))
    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        for trade_date in required_dates:
            session.add(
                EtfPriceHistory(
                    etf_code="510097",
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
                    source_timestamp=datetime(2026, 7, 3, 15, 0),
                    adjustment_version="eastmoney.push2his.kline.hfq_v1",
                    decision_eligible=True,
                )
            )
        await session.commit()

        depths = await bounded_history_sync._eligible_depths(
            session,
            request=_research_request(
                eligible_codes=codes,
                required_sessions=3,
            ),
        )

    assert list(depths) == ["510097", "510096"]


@pytest.mark.asyncio
async def test_projection_migration_fills_the_bounded_required_window(app) -> None:
    code = "510098"
    required_dates = tuple(
        date(2026, 5, 1) + timedelta(days=offset) for offset in range(45)
    )
    request = replace(
        _research_request(eligible_codes=(code,), required_sessions=len(required_dates)),
        required_trade_dates=required_dates,
        from_date=required_dates[0],
        to_date=required_dates[-1],
    )
    async with app.state.db.session() as session:
        session.add(_etf(code))
        session.add_all(
            EtfPriceHistory(
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
                source_timestamp=datetime(2026, 7, 1, 15, 0),
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                decision_eligible=True,
            )
            for trade_date in required_dates
        )
        await session.commit()

        migrated = await bounded_history_sync._backfill_revisions_from_projection(
            session,
            code=code,
            request=request,
        )
        await session.commit()
        revision_count = await session.scalar(
            select(func.count()).select_from(EtfAdjustedPriceRevision)
        )

    assert migrated == len(required_dates)
    assert revision_count == len(required_dates)


@pytest.mark.asyncio
async def test_research_depth_fetches_full_source_span_with_gap_minimum(app) -> None:
    code = "510095"
    calls: list[tuple[date, date, int, tuple[date, ...]]] = []

    class GapFetcher:
        async def __call__(
            self,
            _code: str,
            _from: date,
            _to: date,
        ) -> ProviderFetchResult:
            raise AssertionError("research sync must use the dynamic gap minimum")

        async def fetch_with_minimum(
            self,
            current_code: str,
            from_date: date,
            to_date: date,
            *,
            minimum_eligible_rows: int,
            required_trade_dates: tuple[date, ...],
        ) -> ProviderFetchResult:
            assert current_code == code
            calls.append(
                (
                    from_date,
                    to_date,
                    minimum_eligible_rows,
                    required_trade_dates,
                )
            )
            return ProviderFetchResult(
                rows=_rows(date(2026, 7, 1), 3),
                provider="eastmoney",
                fallback_used=False,
            )

    async with app.state.db.session() as session:
        session.add(_etf(code))
        for trade_date in (date(2026, 7, 2), date(2026, 7, 3)):
            session.add(
                EtfPriceHistory(
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
                    source_timestamp=datetime(2026, 7, 3, 15, 0),
                    adjustment_version="eastmoney.push2his.kline.hfq_v1",
                    decision_eligible=True,
                )
            )
        await session.commit()
        result = await run_bounded_history_sync_slice(
            session,
            request=_research_request(eligible_codes=(code,)),
            fetcher=GapFetcher(),
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        row_count = await session.scalar(select(func.count()).select_from(EtfPriceHistory))
        revision_count = await session.scalar(
            select(func.count()).select_from(EtfAdjustedPriceRevision)
        )

    assert calls == [(date(2026, 7, 1), date(2026, 7, 3), 1, (date(2026, 7, 1),))]
    assert result.status == "complete"
    assert result.completed_codes == (code,)
    assert result.fetched_rows == 3
    assert result.excluded_rows == 2
    assert row_count == 3
    assert revision_count == 3


@pytest.mark.asyncio
async def test_research_depth_persists_full_new_provider_window(app) -> None:
    code = "510094"
    required_dates = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(3))

    class FullWindowFetcher:
        async def fetch_with_minimum(
            self,
            _code: str,
            _from: date,
            _to: date,
            *,
            minimum_eligible_rows: int,
            required_trade_dates: tuple[date, ...],
        ) -> ProviderFetchResult:
            assert minimum_eligible_rows == 1
            assert required_trade_dates == (required_dates[0],)
            return ProviderFetchResult(
                rows=_rows(required_dates[0], len(required_dates)),
                provider="eastmoney",
                fallback_used=False,
            )

        async def __call__(self, _code: str, _from: date, _to: date) -> ProviderFetchResult:
            raise AssertionError("research sync must use fetch_with_minimum")

    async with app.state.db.session() as session:
        session.add(_etf(code))
        session.add_all(
            EtfPriceHistory(
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
                data_provider="tickflow",
                provider_version="tickflow.free.klines.backward_v1",
                source_timestamp=datetime(2026, 7, 3, 15, 0),
                adjustment_version="tickflow.free.klines.backward_v1",
                decision_eligible=True,
            )
            for trade_date in required_dates[1:]
        )
        await session.commit()
        request = replace(
            _research_request(eligible_codes=(code,)),
            required_trade_dates=required_dates,
            from_date=required_dates[0],
            to_date=required_dates[-1],
        )
        result = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=FullWindowFetcher(),
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        rows = (
            await session.scalars(
                select(EtfPriceHistory)
                .where(EtfPriceHistory.etf_code == code)
                .order_by(EtfPriceHistory.trade_date.asc())
            )
        ).all()

    assert result.status == "complete"
    assert result.completed_codes == (code,)
    assert [row.trade_date for row in rows] == list(required_dates)
    assert {row.data_provider for row in rows} == {"eastmoney"}


def test_research_rotation_never_moves_a_shallower_bucket_ahead() -> None:
    codes = ["510101", "510102", "510103"]
    depths = {"510101": 2, "510102": 2, "510103": 1}

    rotated = bounded_history_sync._rotate_within_depth_bucket(
        codes,
        depths=depths,
        last_code="510101",
    )

    assert rotated == ["510102", "510101", "510103"]


@pytest.mark.asyncio
async def test_research_depth_short_history_is_cooled_down_without_losing_denominator(
    app,
) -> None:
    code = "510098"
    calls: list[str] = []

    async def fetch(
        current_code: str,
        _from: date,
        _to: date,
    ) -> ProviderFetchResult:
        calls.append(current_code)
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 2),
            provider="eastmoney",
            fallback_used=False,
        )

    request = _research_request(eligible_codes=(code,))
    async with app.state.db.session() as session:
        session.add(_etf(code))
        for trade_date in request.required_trade_dates[:2]:
            session.add(
                EtfPriceHistory(
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
                    source_timestamp=datetime(2026, 7, 2, 15, 0),
                    adjustment_version="eastmoney.push2his.kline.hfq_v1",
                    decision_eligible=True,
                )
            )
        await session.commit()
        first = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        observation = await session.get(
            EtfAdjustedHistoryAvailability,
            (
                code,
                request.provider_policy_version,
                request.scope,
                request.required_calendar_hash,
            ),
        )
        second = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        observation = await session.get(
            EtfAdjustedHistoryAvailability,
            (code, request.provider_policy_version, request.scope, request.required_calendar_hash),
        )
        assert observation is not None
        observation.retry_after = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()
        third = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )

    assert first.status == "partial"
    assert first.completed_codes == ()
    assert observation is not None
    assert observation.status == "source_history_shortfall"
    assert observation.eligible_session_count == 2
    assert observation.retry_after is not None
    assert observation.evidence_json["inferred_listing_date"] is False
    assert second.status == "partial"
    assert second.stop_reason == "history_availability_cooldown"
    assert second.attempted_codes == ()
    assert calls == [code, code]
    assert third.attempted_codes == (code,)


@pytest.mark.asyncio
async def test_research_depth_empty_missing_session_is_cooled_down(app) -> None:
    code = "510099"
    calls: list[str] = []

    async def fetch(
        current_code: str,
        _from: date,
        _to: date,
    ) -> ProviderFetchResult:
        calls.append(current_code)
        return ProviderFetchResult(
            rows=[],
            provider="eastmoney",
            fallback_used=False,
        )

    request = _research_request(eligible_codes=(code,))
    async with app.state.db.session() as session:
        session.add(_etf(code))
        for trade_date in request.required_trade_dates[:2]:
            session.add(
                EtfPriceHistory(
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
                    source_timestamp=datetime(2026, 7, 2, 15, 0),
                    adjustment_version="eastmoney.push2his.kline.hfq_v1",
                    decision_eligible=True,
                )
            )
        await session.commit()
        first = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        observation = await session.get(
            EtfAdjustedHistoryAvailability,
            (code, request.provider_policy_version, request.scope, request.required_calendar_hash),
        )
        second = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )

    assert first.status == "partial"
    assert observation is not None
    assert observation.status == "source_history_shortfall"
    assert observation.eligible_session_count == 2
    assert observation.retry_after is not None
    assert observation.evidence_json["returned_eligible_sessions"] == 0
    assert observation.evidence_json["requested_sessions"] == 3
    assert observation.evidence_json["covered_required_sessions"] == 2
    assert second.stop_reason == "history_availability_cooldown"
    assert second.attempted_codes == ()
    assert calls == [code]


@pytest.mark.asyncio
async def test_history_cooldown_is_scoped_to_lane_and_calendar(app) -> None:
    code = "510096"
    calls: list[str] = []

    async def fetch(
        current_code: str,
        from_date: date,
        _to: date,
    ) -> ProviderFetchResult:
        calls.append(current_code)
        return ProviderFetchResult(
            rows=_rows(from_date, 1),
            provider="eastmoney",
            fallback_used=False,
        )

    first_request = _research_request(eligible_codes=(code,))
    second_request = replace(
        first_request,
        scope="research_depth:" + "d" * 64,
    )
    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        await run_bounded_history_sync_slice(
            session,
            request=first_request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        second = await run_bounded_history_sync_slice(
            session,
            request=second_request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        observation_count = await session.scalar(
            select(func.count()).select_from(EtfAdjustedHistoryAvailability)
        )

    assert second.attempted_codes == (code,)
    assert calls == [code, code]
    assert observation_count == 2


@pytest.mark.asyncio
async def test_raw_history_cannot_create_research_depth_availability(app) -> None:
    code = "510097"

    async def fetch(
        _code: str,
        _from: date,
        _to: date,
    ) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3, adjusted=False),
            provider="sina",
            fallback_used=False,
        )

    request = _research_request(eligible_codes=(code,))
    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        result = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        observation = await session.get(
            EtfAdjustedHistoryAvailability,
            (code, request.provider_policy_version, request.scope, request.required_calendar_hash),
        )

    assert result.status == "partial"
    assert result.completed_codes == ()
    assert observation is None


@pytest.mark.asyncio
async def test_research_cursor_survives_adaptive_profile_identity_change(app) -> None:
    codes = tuple(f"5107{index:02d}" for index in range(7))
    first_calls: list[str] = []
    second_calls: list[str] = []

    async def failing_fetch(
        code: str,
        _from: date,
        _to: date,
    ) -> ProviderFetchResult:
        first_calls.append(code)
        raise TimeoutError

    async def healthy_fetch(
        code: str,
        _from: date,
        _to: date,
    ) -> ProviderFetchResult:
        second_calls.append(code)
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3),
            provider="eastmoney",
            fallback_used=False,
        )

    first_request = _research_request(
        max_codes=5,
        eligible_codes=codes,
    )
    second_request = _research_request(
        max_codes=10,
        eligible_codes=codes,
    )
    assert first_request.identity_hash != second_request.identity_hash

    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        await session.commit()
        first = await run_bounded_history_sync_slice(
            session,
            request=first_request,
            fetcher=failing_fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        second = await run_bounded_history_sync_slice(
            session,
            request=second_request,
            fetcher=healthy_fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )

    assert first.stop_reason == "provider_circuit_open"
    assert first_calls == list(codes[:3])
    assert second_calls == list(codes[3:] + codes[:3])
    assert second.status == "complete"


@pytest.mark.asyncio
async def test_history_lease_is_global_across_lane_scopes(app) -> None:
    request = _research_request(eligible_codes=("510096",))
    called = False

    async def fetch(
        _code: str,
        _from: date,
        _to: date,
    ) -> ProviderFetchResult:
        nonlocal called
        called = True
        raise AssertionError("global history lease must block provider work")

    async with app.state.db.session() as session:
        session.add(_etf("510096"))
        session.add(
            JobRun(
                job_name="etf_history_continuation:history_depth_61",
                status="running",
                started_at=datetime.utcnow(),
                details_json={"identity_hash": "d" * 64},
            )
        )
        await session.commit()
        result = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
        )

    assert result.status == "skipped"
    assert result.stop_reason == "overlapping_worker_lease"
    assert called is False


@pytest.mark.asyncio
async def test_bounded_sync_caps_codes_pages_and_resumes_without_duplicates(app) -> None:
    codes = [f"5100{index:02d}" for index in range(4)]
    calls: list[str] = []

    async def fetch(code: str, _from: date, _to: date) -> ProviderFetchResult:
        calls.append(code)
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3),
            provider="eastmoney",
            fallback_used=False,
        )

    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        await session.commit()

        first = await run_bounded_history_sync_slice(
            session,
            request=_request(max_codes=2, eligible_codes=tuple(codes)),
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        second = await run_bounded_history_sync_slice(
            session,
            request=_request(max_codes=2, eligible_codes=tuple(codes)),
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        row_count = await session.scalar(
            __import__("sqlalchemy").select(__import__("sqlalchemy").func.count()).select_from(
                EtfPriceHistory
            )
        )
        cursor = await session.get(EtfSyncCursor, _request().scope)

    assert first.status == "partial"
    assert second.status == "complete"
    assert first.attempted_codes == tuple(codes[:2])
    assert second.attempted_codes == tuple(codes[2:])
    assert first.max_page_rows <= 2
    assert first.fetched_rows == 6
    assert first.persisted_rows == 6
    assert first.inserted_rows == 6
    assert first.updated_rows == 0
    assert row_count == 12
    assert len(calls) == 4
    assert cursor is not None
    assert cursor.last_regular_code == codes[-1]


@pytest.mark.asyncio
async def test_depth_cursor_does_not_advance_for_same_day_raw_or_shallow_data(app) -> None:
    code = "510099"

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3, adjusted=False),
            provider="sina",
            fallback_used=True,
        )

    async with app.state.db.session() as session:
        session.add(_etf(code))
        session.add(
            EtfPriceHistory(
                etf_code=code,
                trade_date=date(2026, 7, 3),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
                turnover=1.0,
                pct_change=0.0,
                research_adjusted_value=1.0,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version="eastmoney.push2his.kline.hfq_v1",
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                decision_eligible=True,
            )
        )
        await session.commit()

        result = await run_bounded_history_sync_slice(
            session,
            request=_request(max_codes=1, eligible_codes=(code,)),
            fetcher=fetch,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        cursor = await session.get(EtfSyncCursor, _request().scope)

    assert result.status == "partial"
    assert result.completed_codes == ()
    assert result.exclusions == ((code, "insufficient_contiguous_adjusted_sessions"),)
    assert cursor is None


@pytest.mark.asyncio
async def test_sync_stops_admitting_provider_work_after_deadline(app) -> None:
    codes = ["510201", "510202", "510203"]

    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()
    calls: list[str] = []

    async def fetch(code: str, _from: date, _to: date) -> ProviderFetchResult:
        calls.append(code)
        clock.value = 46.0
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3),
            provider="eastmoney",
            fallback_used=False,
        )

    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        await session.commit()
        result = await run_bounded_history_sync_slice(
            session,
            request=_request(max_codes=3, eligible_codes=tuple(codes)),
            fetcher=fetch,
            clock=clock,
            rss_reader=lambda: 32 * 1024 * 1024,
        )

    assert calls == [codes[0]]
    assert result.status == "partial"
    assert result.stop_reason == "admission_deadline"
    assert result.elapsed_seconds == 46.0


@pytest.mark.asyncio
async def test_sync_keeps_checkpoint_reserve_after_slow_provider(app) -> None:
    code = "510205"

    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        clock.value = 52.9
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3),
            provider="eastmoney",
            fallback_used=False,
        )

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        result = await run_bounded_history_sync_slice(
            session,
            request=_request(max_codes=1, eligible_codes=(code,)),
            fetcher=fetch,
            clock=clock,
            rss_reader=lambda: 32 * 1024 * 1024,
        )
        stored = await session.scalar(
            select(func.count())
            .select_from(EtfPriceHistory)
            .where(EtfPriceHistory.etf_code == code)
        )

    assert result.status == "partial"
    assert result.stop_reason == "worker_deadline"
    assert stored == 0


@pytest.mark.asyncio
async def test_persistence_timeout_uses_bounded_cleanup_and_returns_partial(
    app,
    monkeypatch,
) -> None:
    code = "510206"
    persist_called = False

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3),
            provider="eastmoney",
            fallback_used=False,
        )

    async def timeout_persist(*_args, **_kwargs):
        nonlocal persist_called
        persist_called = True
        raise TimeoutError

    request = replace(
        _request(max_codes=1, eligible_codes=(code,)),
        admission_deadline_seconds=0.1,
        worker_deadline_seconds=0.2,
        process_deadline_seconds=0.25,
        provider_timeout_seconds=0.05,
    )
    monkeypatch.setattr(bounded_history_sync, "_persist_pages", timeout_persist)

    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()
        original_rollback = session.rollback

        async def hanging_rollback() -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(session, "rollback", hanging_rollback)
        started = asyncio.get_running_loop().time()
        result = await asyncio.wait_for(
            run_bounded_history_sync_slice(
                session,
                request=request,
                fetcher=fetch,
                clock=lambda: 0.0,
            ),
            timeout=0.5,
        )
        elapsed = asyncio.get_running_loop().time() - started
        monkeypatch.setattr(session, "rollback", original_rollback)

    assert result.status == "partial"
    assert result.stop_reason == "worker_deadline"
    assert persist_called is True
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_process_deadline_includes_initial_history_database_work(
    app,
    monkeypatch,
) -> None:
    cancelled = False

    async def blocked_lease_check(*_args, **_kwargs):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    request = replace(
        _request(max_codes=1, eligible_codes=("510207",)),
        admission_deadline_seconds=0.05,
        worker_deadline_seconds=0.1,
        process_deadline_seconds=0.15,
        provider_timeout_seconds=0.04,
    )
    monkeypatch.setattr(bounded_history_sync, "_active_lease", blocked_lease_check)

    async with app.state.db.session() as session:
        started = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                run_bounded_history_sync_slice(
                    session,
                    request=request,
                ),
                timeout=0.5,
            )
        elapsed = asyncio.get_running_loop().time() - started

    assert cancelled is True
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_external_cancellation_cleans_worker_lease_and_provider_task(app) -> None:
    code = "510204"
    fetch_started = asyncio.Event()
    provider_cancelled = False

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        nonlocal provider_cancelled
        fetch_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            provider_cancelled = True

    request = _request(max_codes=1, eligible_codes=(code,))
    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()

        task = asyncio.create_task(
            run_bounded_history_sync_slice(
                session,
                request=request,
                fetcher=fetch,
            )
        )
        await asyncio.wait_for(fetch_started.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)

        job = await session.scalar(
            select(JobRun)
            .where(JobRun.job_name == f"etf_history_continuation:{request.scope}")
            .order_by(JobRun.id.desc())
            .limit(1)
        )

    assert provider_cancelled is True
    assert job is not None
    assert job.status == "partial"
    assert job.finished_at is not None
    assert job.details_json["stop_reason"] == "worker_cancelled"


@pytest.mark.asyncio
async def test_sync_rejects_overlapping_worker_lease(app) -> None:
    called = False

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        nonlocal called
        called = True
        raise AssertionError("overlapping worker must not fetch")

    request = _request(max_codes=1, eligible_codes=("510301",))
    async with app.state.db.session() as session:
        session.add(_etf("510301"))
        session.add(
            JobRun(
                job_name=f"etf_history_continuation:{request.scope}",
                status="running",
                started_at=datetime.utcnow(),
                details_json={"identity_hash": request.identity_hash},
            )
        )
        await session.commit()

        result = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
        )

    assert result.status == "skipped"
    assert result.stop_reason == "overlapping_worker_lease"
    assert called is False


@pytest.mark.asyncio
async def test_provider_timeout_is_bounded_and_next_slice_rotates_past_failure(app) -> None:
    codes = ["510401", "510402"]
    calls: list[str] = []
    cancelled = False

    async def fetch(code: str, _from: date, _to: date) -> ProviderFetchResult:
        nonlocal cancelled
        calls.append(code)
        if code == codes[0]:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3),
            provider="eastmoney",
            fallback_used=False,
        )

    request = replace(
        _request(max_codes=1, eligible_codes=tuple(codes)),
        provider_timeout_seconds=0.01,
    )
    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        await session.commit()

        first = await run_bounded_history_sync_slice(session, request=request, fetcher=fetch)
        second = await run_bounded_history_sync_slice(session, request=request, fetcher=fetch)

    assert first.exclusions == ((codes[0], "provider_timeout"),)
    assert second.attempted_codes == (codes[1],)
    assert calls == codes
    assert cancelled is True


@pytest.mark.asyncio
async def test_provider_circuit_opens_after_three_reproducible_failures(app) -> None:
    codes = [f"5105{index:02d}" for index in range(5)]
    calls: list[str] = []

    async def fetch(code: str, _from: date, _to: date) -> ProviderFetchResult:
        calls.append(code)
        raise RuntimeError("eastmoney proxy refused")

    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        await session.commit()
        result = await run_bounded_history_sync_slice(
            session,
            request=_request(max_codes=5, eligible_codes=tuple(codes)),
            fetcher=fetch,
        )

    assert calls == codes[:3]
    assert result.status == "partial"
    assert result.stop_reason == "provider_circuit_open"
    assert all("RuntimeError:eastmoney proxy refused" in reason for _, reason in result.exclusions)


@pytest.mark.asyncio
async def test_depth_requires_exact_recent_eligible_session_coverage(app) -> None:
    code = "510601"
    anchor = "510699"
    session_dates = [date(2026, 6, 30) + timedelta(days=offset) for offset in range(4)]

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=_rows(session_dates[0], 3, adjusted=False),
            provider="sina",
            fallback_used=True,
        )

    async with app.state.db.session() as session:
        session.add_all([_etf(code), _etf(anchor, eligible=False)])
        session.add_all(
            EtfPriceHistory(
                etf_code=anchor,
                trade_date=trade_date,
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
                turnover=1.0,
                pct_change=0.0,
                research_adjusted_value=1.0,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version="eastmoney.push2his.kline.hfq_v1",
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                decision_eligible=True,
            )
            for trade_date in session_dates
        )
        session.add_all(
            EtfPriceHistory(
                etf_code=code,
                trade_date=trade_date,
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
                turnover=1.0,
                pct_change=0.0,
                research_adjusted_value=1.0,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version="eastmoney.push2his.kline.hfq_v1",
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                decision_eligible=True,
            )
            for trade_date in session_dates[:3]
        )
        await session.commit()

        result = await run_bounded_history_sync_slice(
            session,
            request=replace(
                _request(max_codes=1, eligible_codes=(code,)),
                from_date=session_dates[0],
                to_date=session_dates[-1],
            ),
            fetcher=fetch,
        )

    assert result.completed_codes == ()
    assert result.exclusions == ((code, "insufficient_contiguous_adjusted_sessions"),)


@pytest.mark.asyncio
async def test_selection_prioritizes_watchlist_then_shallowest_history(app) -> None:
    codes = ["510701", "510702", "510703"]
    calls: list[str] = []

    async def fetch(code: str, _from: date, _to: date) -> ProviderFetchResult:
        calls.append(code)
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3, adjusted=False),
            provider="sina",
            fallback_used=True,
        )

    async with app.state.db.session() as session:
        session.add_all(
            [
                _etf(codes[0]),
                _etf(codes[1], watchlist=True),
                _etf(codes[2]),
            ]
        )
        session.add(
            EtfPriceHistory(
                etf_code=codes[2],
                trade_date=date(2026, 7, 3),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
                turnover=1.0,
                pct_change=0.0,
                research_adjusted_value=1.0,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version="eastmoney.push2his.kline.hfq_v1",
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                decision_eligible=True,
            )
        )
        await session.commit()

        result = await run_bounded_history_sync_slice(
            session,
            request=_request(max_codes=3, eligible_codes=tuple(codes)),
            fetcher=fetch,
        )

    assert result.attempted_codes == (codes[1], codes[0], codes[2])
    assert calls == list(result.attempted_codes)


@pytest.mark.asyncio
async def test_selection_uses_frozen_request_universe_not_current_flags(app) -> None:
    historical_member = "510750"
    current_survivor = "510751"
    calls: list[str] = []

    async def fetch(code: str, _from: date, _to: date) -> ProviderFetchResult:
        calls.append(code)
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3, adjusted=False),
            provider="sina",
            fallback_used=True,
        )

    async with app.state.db.session() as session:
        session.add_all(
            [
                _etf(historical_member, eligible=False),
                _etf(current_survivor, eligible=True),
            ]
        )
        await session.commit()

        result = await run_bounded_history_sync_slice(
            session,
            request=_request(
                max_codes=1,
                eligible_codes=(historical_member,),
            ),
            fetcher=fetch,
        )

    assert result.attempted_codes == (historical_member,)
    assert calls == [historical_member]


@pytest.mark.asyncio
async def test_universe_identity_change_does_not_reuse_old_cursor_rotation(app) -> None:
    codes = ["510801", "510802"]
    request = replace(
        _request(max_codes=1, eligible_codes=tuple(codes)),
        universe_hash="c" * 64,
    )

    async def fetch(code: str, _from: date, _to: date) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3, adjusted=False),
            provider="sina",
            fallback_used=True,
        )

    async with app.state.db.session() as session:
        session.add_all([_etf(code) for code in codes])
        session.add(
            EtfSyncCursor(
                scope=request.scope,
                last_regular_code=codes[0],
                last_lane="regular",
            )
        )
        await session.commit()

        result = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
        )

    assert result.attempted_codes == (codes[0],)


@pytest.mark.asyncio
async def test_page_batch_records_insert_update_unchanged_and_excluded_counts(app) -> None:
    code = "510901"

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        rows = _rows(date(2026, 7, 1), 3)
        rows.append(_rows(date(2026, 7, 1), 1, adjusted=False)[0])
        return ProviderFetchResult(
            rows=rows,
            provider="eastmoney",
            fallback_used=True,
        )

    async with app.state.db.session() as session:
        session.add(_etf(code))
        session.add(
            EtfPriceHistory(
                etf_code=code,
                trade_date=date(2026, 7, 1),
                open=1.0,
                high=1.0,
                low=1.0,
                close=0.9,
                volume=1_000_000.0,
                turnover=100_000_000.0,
                pct_change=0.0,
                research_adjusted_value=0.9,
                research_price_basis="total_return_adjusted",
                data_provider="eastmoney",
                provider_version="eastmoney.push2his.kline.hfq_v1",
                adjustment_version="eastmoney.push2his.kline.hfq_v1",
                decision_eligible=True,
            )
        )
        await session.commit()

        result = await run_bounded_history_sync_slice(
            session,
            request=_request(max_codes=1, eligible_codes=(code,)),
            fetcher=fetch,
        )

    assert result.inserted_rows == 2
    assert result.updated_rows == 1
    assert result.unchanged_rows == 0
    assert result.excluded_rows == 1
    assert result.persisted_rows == 3


@pytest.mark.asyncio
async def test_precommit_failure_rolls_back_page_and_resumes_same_page(app) -> None:
    code = "510902"

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 3),
            provider="eastmoney",
            fallback_used=False,
        )

    request = _request(max_codes=1, eligible_codes=(code,))
    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()

        with pytest.raises(RuntimeError, match="injected page interruption"):
            await run_bounded_history_sync_slice(
                session,
                request=request,
                fetcher=fetch,
                before_page_commit=lambda: (_ for _ in ()).throw(
                    RuntimeError("injected page interruption")
                ),
            )

        row_count_after_failure = await session.scalar(
            select(func.count()).select_from(EtfPriceHistory)
        )
        revision_count_after_failure = await session.scalar(
            select(func.count()).select_from(EtfAdjustedPriceRevision)
        )
        failed_job = await session.scalar(
            select(JobRun)
            .where(JobRun.job_name == f"etf_history_continuation:{request.scope}")
            .order_by(JobRun.id.desc())
            .limit(1)
        )
        failed_job_status = failed_job.status if failed_job is not None else None
        failed_job_details = (
            dict(failed_job.details_json) if failed_job is not None else {}
        )
        cursor_after_failure = await session.get(EtfSyncCursor, request.scope)

        resumed = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
        )
        row_count_after_resume = await session.scalar(
            select(func.count()).select_from(EtfPriceHistory)
        )
        revision_count_after_resume = await session.scalar(
            select(func.count()).select_from(EtfAdjustedPriceRevision)
        )

    assert row_count_after_failure == 0
    assert revision_count_after_failure == 0
    assert failed_job is not None
    assert failed_job_status == "failed"
    assert failed_job_details["stop_reason"] == "page_persistence_error:RuntimeError"
    assert "last_trade_date" not in failed_job_details
    assert cursor_after_failure is None
    assert resumed.attempted_codes == (code,)
    assert resumed.status == "complete"
    assert row_count_after_resume == 3
    assert revision_count_after_resume == 3


@pytest.mark.asyncio
async def test_worker_deadline_keeps_last_complete_page_and_exits_before_60(app) -> None:
    code = "510904"

    class Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = Clock()

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=_rows(date(2026, 7, 1), 4),
            provider="eastmoney",
            fallback_used=False,
        )

    request = replace(
        _request(max_codes=1, eligible_codes=(code,)),
        to_date=date(2026, 7, 4),
        required_sessions=4,
    )
    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()

        partial = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
            clock=clock,
            before_page_commit=lambda: setattr(clock, "value", 56.0),
        )
        count_after_partial = await session.scalar(
            select(func.count()).select_from(EtfPriceHistory)
        )

        resumed = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
        )
        count_after_resume = await session.scalar(
            select(func.count()).select_from(EtfPriceHistory)
        )

    assert partial.status == "partial"
    assert partial.stop_reason == "worker_deadline"
    assert partial.elapsed_seconds == 56.0
    assert partial.elapsed_seconds < request.process_deadline_seconds
    assert partial.last_durable_checkpoint is not None
    assert partial.last_durable_checkpoint["last_trade_date"] == "2026-07-02"
    assert count_after_partial == 2
    assert resumed.status == "complete"
    assert count_after_resume == 4


@pytest.mark.asyncio
async def test_500_row_page_uses_bounded_batch_sql(app) -> None:
    code = "510903"
    start = date(2025, 1, 1)
    rows = _rows(start, 500)
    end = date.fromisoformat(str(rows[-1]["date"]))

    async def fetch(_code: str, _from: date, _to: date) -> ProviderFetchResult:
        return ProviderFetchResult(
            rows=rows,
            provider="eastmoney",
            fallback_used=False,
        )

    request = replace(
        _request(max_codes=1, max_rows=500, eligible_codes=(code,)),
        from_date=start,
        to_date=end,
        required_sessions=300,
        page_size=500,
    )
    async with app.state.db.session() as session:
        session.add(_etf(code))
        await session.commit()

        result = await run_bounded_history_sync_slice(
            session,
            request=request,
            fetcher=fetch,
        )
        row_count = await session.scalar(
            select(func.count()).select_from(EtfPriceHistory)
        )
        revision_count = await session.scalar(
            select(func.count()).select_from(EtfAdjustedPriceRevision)
        )

    assert result.status == "complete"
    assert result.max_page_rows == 20
    assert result.max_page_sql_statements <= 8
    assert result.persisted_rows == 500
    assert row_count == 500
    assert revision_count == 500
