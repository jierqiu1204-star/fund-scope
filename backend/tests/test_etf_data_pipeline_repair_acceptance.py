"""Independent acceptance: persisted evidence, real readers, no provider mocks."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from test_etf_pit_replay_input_loader import (
    CUTOFF,
    T,
    _adjusted_history,
    _etf,
    _membership,
)

from app.models.entities import EtfAdjustedPriceRevision
from app.services.short_etf import bounded_history_sync
from app.services.short_etf.data import ProviderFetchResult
from app.services.short_research.etf_tracked_underlying import (
    EtfTrackedUnderlyingProviderError,
    _record_from_response,
)
from app.services.strategy_lab.dual_universe_leader_tactics_v2_etf_inputs import (
    read_etf_v2_asset_inputs,
)


def _new_revision_rows(*, count: int, observed: datetime, provider: str = "tickflow"):
    rows = [
        row
        for row in _adjusted_history("510300", count=count, source_timestamp=observed)
        if isinstance(row, EtfAdjustedPriceRevision)
    ]
    if provider == "tickflow":
        for row in rows:
            row.data_provider = provider
            row.provider_version = "tickflow.free.klines.backward_v1"
            row.adjustment_version = row.provider_version
    return rows


async def _seed(session, rows) -> None:
    session.add(_etf("510300"))
    session.add(_membership("510300"))
    session.add_all(rows)
    await session.commit()


@pytest.mark.asyncio
async def test_v2_selects_complete_coherent_source_instead_of_newer_partial_source(app):
    async with app.state.db.session() as session:
        await _seed(
            session,
            [
                *_adjusted_history("510300", count=180),
                *_new_revision_rows(count=40, observed=datetime(2022, 6, 30, 6, 40)),
            ],
        )
        bundle = await read_etf_v2_asset_inputs(
            session, replay_date=T, decision_cutoff=CUTOFF
        )
    assert bundle.universe_count == 1
    assert bundle.adjusted_120_count == 1
    assert bundle.adjusted_180_count == 1
    assert len(bundle.inputs[0].bars) == 180
    assert bundle.provider_health == (("eastmoney", "healthy"),)


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh_count", [127, 180])
async def test_v2_prefers_current_complete_source_over_longer_stale_history(app, fresh_count):
    async with app.state.db.session() as session:
        await _seed(
            session,
            [
                *_adjusted_history("510300", count=220, end_date=T-timedelta(days=14)),
                *_new_revision_rows(count=fresh_count, observed=datetime(2022, 6, 30, 6, 40)),
            ],
        )
        bundle = await read_etf_v2_asset_inputs(
            session, replay_date=T, decision_cutoff=CUTOFF
        )
    assert bundle.adjusted_120_count == 1
    assert bundle.adjusted_180_count == int(fresh_count == 180)
    assert bundle.provider_health == (("tickflow", "healthy"),)


@pytest.mark.asyncio
async def test_revision_outside_requested_window_does_not_disqualify_current_history(app):
    old_invalid = _new_revision_rows(
        count=181, observed=datetime(2022, 6, 30, 6, 45), provider="eastmoney"
    )[0]
    old_invalid.decision_eligible = False
    old_invalid.decision_ineligibility_reason = "withdrawn_old_evidence_outside_window"
    async with app.state.db.session() as session:
        await _seed(
            session,
            [
                *_adjusted_history("510300", count=181),
                old_invalid,
                *_new_revision_rows(count=127, observed=datetime(2022, 6, 30, 6, 40)),
            ],
        )
        bundle = await read_etf_v2_asset_inputs(
            session, replay_date=T, decision_cutoff=CUTOFF
        )
    assert bundle.adjusted_180_count == 1
    assert bundle.provider_health == (("eastmoney", "healthy"),)


@pytest.mark.asyncio
async def test_v2_does_not_join_disjoint_sources_to_reach_history_threshold(app):
    old_rows = _adjusted_history("510300", count=180)
    # This reproduces the production shape: the older source stops before the
    # new source starts. Total rows are sufficient, neither source is complete.
    old_revisions = [row for row in old_rows if isinstance(row, EtfAdjustedPriceRevision)]
    old_cutoff = old_revisions[-91].trade_date
    rows = [row for row in old_rows if row.trade_date < old_cutoff]
    async with app.state.db.session() as session:
        await _seed(
            session,
            [*rows, *_new_revision_rows(count=91, observed=datetime(2022, 6, 30, 6, 40))],
        )
        bundle = await read_etf_v2_asset_inputs(
            session, replay_date=T, decision_cutoff=CUTOFF
        )
    assert bundle.universe_count == 1
    assert bundle.adjusted_120_count == 0
    assert bundle.inputs[0].bars == ()
    assert sum(count for _, count in bundle.exclusions) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("received_before_cutoff", [True, False])
async def test_new_invalid_revision_cannot_resurrect_old_price_or_rewrite_past(
    app, received_before_cutoff
):
    observed = datetime(2022, 6, 30, 6, 40) if received_before_cutoff else datetime(2022, 7, 1)
    invalid = _new_revision_rows(count=1, observed=observed, provider="eastmoney")[0]
    invalid.decision_eligible = False
    invalid.decision_ineligibility_reason = "provider_withdrew_adjustment_evidence"
    async with app.state.db.session() as session:
        await _seed(session, [*_adjusted_history("510300", count=180), invalid])
        bundle = await read_etf_v2_asset_inputs(
            session, replay_date=T, decision_cutoff=CUTOFF
        )
    assert bundle.adjusted_120_count == (0 if received_before_cutoff else 1)
    assert bool(bundle.inputs[0].bars) is not received_before_cutoff


@pytest.mark.parametrize("label", ["--", "暂无资料", "待更新"])
def test_unavailable_provider_text_is_not_a_resolved_index(label):
    html = (
        "<table><tr><th>基金代码</th><td>510300（主代码）</td></tr>"
        f"<tr><th>跟踪标的</th><td>{label}</td></tr></table>"
    )
    with pytest.raises(EtfTrackedUnderlyingProviderError):
        _record_from_response(
            code="510300",
            source_url="https://fundf10.eastmoney.com/jbgk_510300.html",
            body=html,
            observed_at=datetime(2026, 9, 10, 12),
        )


def test_conflicting_explicit_provider_fields_fail_closed():
    html = (
        "<table><tr><th>基金代码</th><td>510300（主代码）</td></tr>"
        "<tr><th>跟踪标的</th><td>沪深300指数</td></tr>"
        "<tr><th>跟踪标的</th><td>中证1000指数</td></tr></table>"
    )
    with pytest.raises(EtfTrackedUnderlyingProviderError):
        _record_from_response(
            code="510300",
            source_url="https://fundf10.eastmoney.com/jbgk_510300.html",
            body=html,
            observed_at=datetime(2026, 9, 10, 12),
        )


def _input_repair_request(dates):
    return bounded_history_sync.BoundedHistorySyncRequest(
        scope="root-acceptance-input-repair",
        contract_hash="a" * 64,
        universe_hash="b" * 64,
        eligible_codes=("510300",),
        from_date=dates[0],
        to_date=dates[-1],
        required_sessions=len(dates),
        required_trade_dates=tuple(dates),
        max_codes=5,
        page_size=500,
        admission_deadline_seconds=40,
        worker_deadline_seconds=45,
        process_deadline_seconds=50,
        rss_limit_bytes=512 * 1024 * 1024,
        provider_timeout_seconds=6,
        selection_policy="research_depth",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_field,bad_value", [
    ("research_adjusted_value", -1.0),
    ("research_adjusted_value", float("inf")),
    ("revision_hash", "bad"),
])
async def test_history_worker_does_not_stop_on_corrupt_complete_window(app, bad_field, bad_value):
    rows = _new_revision_rows(count=180, observed=datetime(2022, 6, 30, 6, 40))
    setattr(rows[-1], bad_field, bad_value)
    request = _input_repair_request([row.trade_date for row in rows])
    async with app.state.db.session() as session:
        await _seed(session, rows)
        assert not await bounded_history_sync._depth_is_complete(
            session, code="510300", request=request
        )


@pytest.mark.asyncio
async def test_partial_new_source_preserves_127_rows_without_claiming_180_complete(app):
    full = _new_revision_rows(
        count=180, observed=datetime(2022, 6, 30, 6, 30), provider="eastmoney"
    )
    request = _input_repair_request([row.trade_date for row in full])

    class Fetcher:
        async def fetch_with_minimum(self, code, from_date, to_date, **kwargs):
            assert (from_date, to_date) == (request.from_date, request.to_date)
            assert kwargs["minimum_eligible_rows"] == 14
            return ProviderFetchResult(
                provider="tickflow", fallback_used=False,
                rows=[{
                    "date": row.trade_date.isoformat(),
                    "open": row.open, "high": row.high, "low": row.low,
                    "close": row.close, "volume": row.volume, "turnover": row.turnover,
                    "pct_change": 0.0,
                    "research_adjusted_value": row.research_adjusted_value,
                    "research_price_basis": "total_return_adjusted",
                    "adjustment_version": "tickflow.free.klines.backward_v1",
                    "provider_version": "tickflow.free.klines.backward_v1",
                } for row in full[-127:]],
            )

    async with app.state.db.session() as session:
        await _seed(session, full[:166])
        result = await bounded_history_sync.run_bounded_history_sync_slice(
            session, request=request, fetcher=Fetcher(), rss_reader=lambda: 32 * 1024 * 1024,
        )
        by_provider = await bounded_history_sync._coherent_revision_dates(
            session, codes=("510300",), trade_dates=request.required_trade_dates,
        )
    assert len(by_provider["510300"]["tickflow"]) == 127
    assert result.completed_codes == ()
    assert result.status != "complete"
