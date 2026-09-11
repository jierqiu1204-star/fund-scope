"""Root acceptance against persisted history, independent of provider adapters."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from test_etf_history_readiness_projection import _etf, _listing, _membership
from test_etf_pit_replay_input_loader import T, _adjusted_history
from test_etf_pit_replay_input_loader import _membership as _pit_membership

from app.models.entities import EtfAdjustedPriceRevision, EtfPriceHistory
from app.services.strategy_lab.dual_universe_leader_tactics_v2_etf_inputs import (
    read_etf_v2_asset_inputs,
)
from app.services.workflows.etf_history_readiness import read_etf_history_readiness

CODE = "510300"
CUTOFF = datetime(2022, 6, 30, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
EARLY = datetime(2022, 6, 30, 6, 30)
RECEIVED = datetime(2022, 6, 30, 6, 45)


def _tickflow(row):
    row.data_provider = "tickflow"
    row.provider_version = "tickflow.free.klines.backward_v1"
    row.adjustment_version = row.provider_version


async def _seed(session, rows):
    listing = _listing(CODE, T.replace(year=2020, month=1, day=1))
    listing.observed_at = datetime(2021, 1, 1)
    session.add_all(
        [
            _etf(CODE),
            listing,
            _membership(CODE, effective_from=T.replace(year=2020, month=1, day=1)),
            _pit_membership(CODE),
            *rows,
        ]
    )
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("window,lane", [(180, "telemetry_depth_180"), (300, "contract_depth"), (500, "telemetry_depth_500")])
async def test_readiness_rejects_disjoint_sources_even_when_projection_is_full(app, window, lane):
    rows = _adjusted_history(CODE, count=window)
    split_date = rows[len(rows) // 2].trade_date
    for row in rows:
        if row.trade_date >= split_date:
            _tickflow(row)
    async with app.state.db.session() as session:
        await _seed(session, rows)
        report = await read_etf_history_readiness(session, target_date=T, data_cutoff=CUTOFF)
    assert report[lane]["expected_count"] == 1
    assert report[lane]["covered_count"] == 0
    assert report["daily_freshness"]["covered_count"] == 1
    assert report["history_depth_61"]["covered_count"] == 1


@pytest.mark.asyncio
async def test_readiness_counts_complete_revision_source_despite_mixed_projection(app):
    rows = _adjusted_history(CODE, count=180)
    for offset, row in enumerate(r for r in rows if isinstance(r, EtfPriceHistory)):
        if offset % 2:
            _tickflow(row)
    async with app.state.db.session() as session:
        await _seed(session, rows)
        report = await read_etf_history_readiness(session, target_date=T, data_cutoff=CUTOFF)
        bundle = await read_etf_v2_asset_inputs(session, replay_date=T, decision_cutoff=CUTOFF)
    assert report["telemetry_depth_180"]["covered_count"] == 1
    assert bundle.adjusted_180_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("received_late", [False, True])
async def test_readiness_respects_revision_withdrawal_and_historical_cutoff(app, received_late):
    received = datetime(2022, 6, 30, 7, 30) if received_late else RECEIVED
    withdrawn = next(
        r for r in _adjusted_history(CODE, count=1, source_timestamp=received)
        if isinstance(r, EtfAdjustedPriceRevision)
    )
    withdrawn.decision_eligible = False
    withdrawn.decision_ineligibility_reason = "provider_withdrew_price"
    async with app.state.db.session() as session:
        await _seed(session, [*_adjusted_history(CODE, count=180), withdrawn])
        report = await read_etf_history_readiness(session, target_date=T, data_cutoff=CUTOFF)
        bundle = await read_etf_v2_asset_inputs(session, replay_date=T, decision_cutoff=CUTOFF)
    assert report["daily_freshness"]["covered_count"] == int(received_late)
    assert report["history_depth_61"]["covered_count"] == int(received_late)
    assert report["telemetry_depth_180"]["covered_count"] == bundle.adjusted_180_count == int(received_late)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("close", 50.0), ("revision_hash", "bad"), ("revision_hash", "z" * 64), ("research_adjusted_value", -1.0), ("provider_version", "unknown-version")])
async def test_readiness_rejects_corrupt_latest_evidence_behind_valid_projection(app, field, value):
    corrupt = next(
        r for r in _adjusted_history(CODE, count=1, source_timestamp=RECEIVED)
        if isinstance(r, EtfAdjustedPriceRevision)
    )
    setattr(corrupt, field, value)
    async with app.state.db.session() as session:
        await _seed(session, [*_adjusted_history(CODE, count=180), corrupt])
        report = await read_etf_history_readiness(session, target_date=T, data_cutoff=CUTOFF)
    assert report["daily_freshness"]["covered_count"] == 0
    assert report["history_depth_61"]["covered_count"] == 0
    assert report["telemetry_depth_180"]["covered_count"] == 0


@pytest.mark.asyncio
async def test_projection_cannot_make_late_received_history_visible_in_the_past(app):
    rows = _adjusted_history(CODE, count=180)
    for row in rows:
        if isinstance(row, EtfAdjustedPriceRevision):
            row.first_seen_at = datetime(2022, 7, 1)
            row.observed_at = datetime(2022, 7, 1)
    async with app.state.db.session() as session:
        await _seed(session, rows)
        report = await read_etf_history_readiness(session, target_date=T, data_cutoff=CUTOFF)
        bundle = await read_etf_v2_asset_inputs(session, replay_date=T, decision_cutoff=CUTOFF)
    assert report["telemetry_depth_180"]["covered_count"] == bundle.adjusted_180_count == 0
    assert report["daily_freshness"]["covered_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("close", 50.0), ("revision_hash", "bad"), ("revision_hash", "z" * 64), ("research_adjusted_value", -1.0)])
async def test_reader_and_readiness_both_select_valid_source_over_newer_corrupt_source(app, field, value):
    newer_source = [
        row for row in _adjusted_history(CODE, count=180, source_timestamp=RECEIVED)
        if isinstance(row, EtfAdjustedPriceRevision)
    ]
    for row in newer_source:
        _tickflow(row)
    setattr(newer_source[-1], field, value)
    async with app.state.db.session() as session:
        await _seed(session, [*_adjusted_history(CODE, count=180), *newer_source])
        report = await read_etf_history_readiness(session, target_date=T, data_cutoff=CUTOFF)
        bundle = await read_etf_v2_asset_inputs(session, replay_date=T, decision_cutoff=CUTOFF)
    assert report["telemetry_depth_180"]["covered_count"] == bundle.adjusted_180_count == 1
    assert bundle.provider_health == (("eastmoney", "healthy"),)
