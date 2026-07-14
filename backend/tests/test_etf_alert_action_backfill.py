from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from app.cli import build_parser
from app.models.entities import (
    EtfPriceHistory,
    TrackedPosition,
    TrackedPositionActionDecision,
    TradableEtf,
)
from app.services.tracked_positions.lifecycle_backfill import backfill_position_lifecycle_batch


def test_backfill_cli_requires_explicit_ids_and_one_bounded_batch() -> None:
    args = build_parser().parse_args(
        [
            "backfill-etf-alert-lifecycle",
            "--position-id",
            "10",
            "--position-id",
            "11",
            "--cutoff",
            "2026-07-14",
            "--max-items",
            "2",
        ]
    )

    assert args.command == "backfill-etf-alert-lifecycle"
    assert args.position_ids == [10, 11]
    assert args.cutoff == date(2026, 7, 14)
    assert args.max_items == 2


async def _seed_position(
    session,
    *,
    code: str,
    confirmed_shares: float | None,
    estimated_shares: float | None,
    status: str = "active",
    exit_state: dict | None = None,
) -> TrackedPosition:
    session.add(
        TradableEtf(
            code=code,
            name=f"回填ETF{code}",
            exchange="SH",
            theme_tags_json=["回填"],
            trading_rule_label="T+1",
            asset_class="sector",
        )
    )
    position = TrackedPosition(
        user_id=1,
        asset_type="etf",
        asset_code=code,
        asset_name=f"回填ETF{code}",
        buy_date=date(2026, 7, 1),
        confirmed_shares=confirmed_shares,
        buy_amount=1000.0,
        entry_price=1.0,
        estimated_shares=estimated_shares,
        exit_state_json=exit_state or {},
        exit_state_version=0,
        status=status,
    )
    session.add(position)
    await session.flush()
    return position


def _eligible_price(code: str, trade_date: date, adjusted_value: float) -> EtfPriceHistory:
    return EtfPriceHistory(
        etf_code=code,
        trade_date=trade_date,
        open=adjusted_value,
        high=adjusted_value,
        low=adjusted_value,
        close=adjusted_value,
        volume=1_000_000,
        turnover=adjusted_value * 1_000_000,
        pct_change=0.0,
        research_adjusted_value=adjusted_value,
        research_price_basis="total_return_adjusted",
        data_provider="fixture",
        provider_version="fixture-v1",
        source_timestamp=datetime.combine(trade_date, datetime.min.time()),
        adjustment_version="fixture-adjusted-v1",
        decision_eligible=True,
    )


@pytest.mark.asyncio
async def test_bounded_backfill_handles_confirmed_estimated_missing_legacy_firing_and_closed(app) -> None:
    async with app.state.db.session() as session:
        confirmed = await _seed_position(
            session,
            code="510001",
            confirmed_shares=1000.0,
            estimated_shares=1000.0,
        )
        estimated = await _seed_position(
            session,
            code="510002",
            confirmed_shares=None,
            estimated_shares=800.0,
        )
        missing = await _seed_position(
            session,
            code="510003",
            confirmed_shares=None,
            estimated_shares=None,
        )
        legacy = await _seed_position(
            session,
            code="510004",
            confirmed_shares=600.0,
            estimated_shares=600.0,
            exit_state={
                "latest_position_action": {
                    "position_action": "reduce",
                    "cooldown_end": "2026-07-20",
                }
            },
        )
        firing = await _seed_position(
            session,
            code="510005",
            confirmed_shares=500.0,
            estimated_shares=500.0,
            exit_state={"rules": {"hard_stop": {"state": "firing"}}},
        )
        closed = await _seed_position(
            session,
            code="510006",
            confirmed_shares=0.0,
            estimated_shares=0.0,
            status="closed",
        )
        for code in ("510001", "510002", "510004", "510005"):
            session.add_all(
                [
                    _eligible_price(code, date(2026, 7, 1), 1.0),
                    _eligible_price(code, date(2026, 7, 10), 1.2),
                ]
            )
        await session.commit()
        position_ids = [confirmed.id, estimated.id, missing.id, legacy.id, firing.id, closed.id]

    async with app.state.db.session() as session:
        result = await backfill_position_lifecycle_batch(
            session,
            position_ids=position_ids,
            cutoff=date(2026, 7, 14),
            max_items=10,
        )
        await session.commit()

    async with app.state.db.session() as session:
        rows = {
            row.asset_code: row
            for row in (
                await session.scalars(select(TrackedPosition).where(TrackedPosition.id.in_(position_ids)))
            ).all()
        }
        action_count = await session.scalar(select(func.count()).select_from(TrackedPositionActionDecision))

    assert result.processed == 6
    assert result.confirmed_baseline == 3
    assert result.estimated_baseline == 1
    assert result.data_waiting == 1
    assert result.closed == 1
    assert result.legacy_unverified == 1
    assert result.rejected == 0
    assert action_count == 0

    confirmed_state = rows["510001"].exit_state_json
    assert confirmed_state["position_episode_id"]
    assert confirmed_state["exposure_version"] == 1
    assert confirmed_state["exposure_baseline"]["source"] == "confirmed_shares"
    assert confirmed_state["exposure_baseline"]["normalized_quantity"] == 1000.0
    assert confirmed_state["high_water_adjusted_price"] == 1.2

    estimated_state = rows["510002"].exit_state_json
    assert estimated_state["exposure_baseline"]["source"] == "verified_estimated_shares"
    assert estimated_state["exposure_baseline"]["normalized_quantity"] == 800.0

    missing_state = rows["510003"].exit_state_json
    assert missing_state["needs_user_confirmation"] is True
    assert missing_state["evaluation_data_outcome"] == {
        "state": "data_waiting",
        "reason_code": "position_quantity_unavailable",
    }

    legacy_state = rows["510004"].exit_state_json
    assert legacy_state["latest_position_action"]["position_action"] == "reduce"
    assert legacy_state["execution_provenance"] == "legacy_unverified"
    assert "latest_executed_position_action" not in legacy_state
    assert legacy_state.get("reentry_cooldown_origin") is None

    firing_state = rows["510005"].exit_state_json
    assert firing_state["rules"]["hard_stop"]["state"] == "firing"
    assert firing_state["alert_rule_states"] == {}
    assert firing_state.get("current_action_id") is None

    closed_state = rows["510006"].exit_state_json
    assert closed_state["position_episode_status"] == "closed"
    assert closed_state.get("position_episode_id") is None


@pytest.mark.asyncio
async def test_backfill_processes_only_one_explicit_bounded_batch_and_is_idempotent(app) -> None:
    async with app.state.db.session() as session:
        first = await _seed_position(
            session,
            code="520001",
            confirmed_shares=100.0,
            estimated_shares=100.0,
        )
        second = await _seed_position(
            session,
            code="520002",
            confirmed_shares=100.0,
            estimated_shares=100.0,
        )
        session.add_all(
            [
                _eligible_price("520001", date(2026, 7, 10), 1.0),
                _eligible_price("520002", date(2026, 7, 10), 1.0),
            ]
        )
        await session.commit()

    async with app.state.db.session() as session:
        first_result = await backfill_position_lifecycle_batch(
            session,
            position_ids=[first.id, second.id],
            cutoff=date(2026, 7, 14),
            max_items=1,
        )
        await session.commit()
    async with app.state.db.session() as session:
        retry = await backfill_position_lifecycle_batch(
            session,
            position_ids=[first.id],
            cutoff=date(2026, 7, 14),
            max_items=1,
        )
        await session.commit()
        first_row = await session.get(TrackedPosition, first.id)
        second_row = await session.get(TrackedPosition, second.id)

    assert first_result.processed == 1
    assert retry.processed == 0
    assert retry.skipped_existing == 1
    assert first_row is not None and first_row.exit_state_version == 1
    assert second_row is not None and second_row.exit_state_version == 0
