"""Root checks on persisted, currently received historical research inputs."""

from dataclasses import asdict, replace
from datetime import UTC, date, timedelta

import pytest
from test_etf_history_readiness_projection import _etf, _listing
from test_etf_identity_facts import _taxonomy_record, _underlying_record
from test_etf_leader_exit_historical_acceptance import (
    END,
    FROZEN,
    RECEIVED,
    START,
    _calendar,
    _dataset,
)
from test_etf_pit_replay_input_loader import _adjusted_history

from app.models.entities import EtfAdjustedPriceRevision
from app.services.short_research.etf_identity_facts import persist_etf_identity_facts
from app.services.strategy_lab.etf_leader_exit_historical_loader import (
    dataset_from_json,
    dataset_to_json,
    load_historical_v2_dataset,
)

CODE = "512710"


async def _seed(session, *, listing_received=RECEIVED, underlying=True, code=CODE,
                underlying_id="CSI-MIL", daily_step=0.005):
    session.add(_etf(code))
    listing = _listing(code, date(2025, 1, 1))
    listing.observed_at = listing_received.astimezone(UTC).replace(tzinfo=None)
    session.add(listing)
    rows = _adjusted_history(code, end_date=END, count=160, daily_step=daily_step,
                             source_timestamp=RECEIVED.replace(tzinfo=None))
    selected = [row for row in rows if isinstance(row, EtfAdjustedPriceRevision)
                and row.trade_date in set(_calendar())]
    session.add_all(selected)
    await session.commit()
    await persist_etf_identity_facts(
        session,
        taxonomy_records=[replace(_taxonomy_record(observed_at=RECEIVED, primary_theme="军工"),
                                  etf_code=code, external_source_id=f"rules:{code}",
                                  raw_payload={"code": code, "primary_theme": "军工"})],
        underlying_records=[replace(_underlying_record(observed_at=RECEIVED, underlying_id=underlying_id),
                                    etf_code=code, external_source_id=f"registry:{code}",
                                    raw_payload={"fund_code": code, "underlying_id": underlying_id})]
        if underlying else [],
    )
    await session.commit()
    return selected


@pytest.mark.asyncio
async def test_loader_accepts_backfilled_history_with_real_receipts_and_warmup(app):
    async with app.state.db.session() as session:
        rows = await _seed(session)
        data = await load_historical_v2_dataset(
            session, frozen_at=FROZEN, start_date=START, end_date=END,
        )
    assert len(rows) < 180
    assert len(data.assets) == 1
    asset = data.assets[0]
    assert asset.bars[0].trade_date < START
    assert data.trading_sessions[0] == date(2026, 1, 5)
    assert asset.bars[-1].observed_at == RECEIVED
    assert asset.bars[-1].adjusted_close == rows[-1].research_adjusted_value
    assert asset.bars[-1].adjusted_open == pytest.approx(rows[-1].open * 2)
    assert asset.membership.observed_at == RECEIVED
    assert asset.membership.mapping_kind == "current_vintage_proxy"
    assert asset.membership.clone_group == "CSI-MIL"
    assert data.source_manifest
    assert "taxonomy" in str(data.source_manifest)


@pytest.mark.asyncio
async def test_listing_cutoff_uses_utc_instead_of_accepting_next_eight_hours(app):
    async with app.state.db.session() as session:
        await _seed(session, listing_received=FROZEN + timedelta(hours=1))
        data = await load_historical_v2_dataset(
            session, frozen_at=FROZEN, start_date=START, end_date=END,
        )
    assert not data.assets
    assert any(code == CODE and "listing" in reason for code, reason in data.exclusions)


@pytest.mark.asyncio
async def test_missing_formal_underlying_is_not_replaced_by_name_or_asset_code(app):
    async with app.state.db.session() as session:
        await _seed(session, underlying=False)
        data = await load_historical_v2_dataset(
            session, frozen_at=FROZEN, start_date=START, end_date=END,
        )
    assert not data.assets
    assert any(code == CODE and "underlying" in reason for code, reason in data.exclusions)


def test_frozen_dataset_json_roundtrip_preserves_timestamps_hash_and_facts():
    data = _dataset()
    recovered = dataset_from_json(dataset_to_json(data))
    assert asdict(recovered) == asdict(data)
    assert recovered.source_hash == data.source_hash
    corrupted = dataset_to_json(data).replace('"adjusted_close": 110.0', '"adjusted_close": 999.0', 1)
    with pytest.raises(ValueError):
        dataset_from_json(corrupted)


def test_offline_receipt_after_dataset_freeze_is_rejected():
    data = _dataset()
    changed = replace(data.assets[0], membership=replace(
        data.assets[0].membership, observed_at=FROZEN + timedelta(days=1),
    ))
    with pytest.raises(ValueError):
        replace(data, assets=(changed, *data.assets[1:]), source_hash="")


async def test_multiple_assets_keep_their_own_prices_identity_and_missing_facts(app):
    async with app.state.db.session() as session:
        first = await _seed(session)
        second = await _seed(session, code="512720", underlying_id="CSI-OTHER", daily_step=0.01)
        await _seed(session, code="512730", underlying=False)
        data = await load_historical_v2_dataset(
            session, frozen_at=FROZEN, start_date=START, end_date=END,
        )
    assert [asset.asset_code for asset in data.assets] == [CODE, "512720"]
    assert [asset.bars[-1].adjusted_close for asset in data.assets] == [
        first[-1].research_adjusted_value, second[-1].research_adjusted_value,
    ]
    assert [asset.membership.clone_group for asset in data.assets] == ["CSI-MIL", "CSI-OTHER"]
    assert ("512730", "underlying_fact_missing") in data.exclusions
