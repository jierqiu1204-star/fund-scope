from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfAdjustedPriceRevision,
    TrackedEtfSleeveDailySnapshot,
    TrackedEtfSleeveImmutableError,
    TrackedEtfSleeveLedgerEvent,
    TrackedPosition,
    TradableEtf,
    User,
    utcnow,
)
from app.services.risk_alerts import ACTION_CLASS_GUARD_ONLY, PositionSizingRecommendation
from app.services.tracked_positions.owner_risk import (
    EtfOwnerRiskContext,
    apply_owner_risk_guard,
    build_owner_etf_risk_context,
    materialize_owner_etf_risk_snapshot,
    reconstruct_sleeve_ledger,
)
from app.services.tracked_positions.sleeve_repository import (
    AppendSleeveDailySnapshotCommand,
    AppendSleeveLedgerEventCommand,
    SleeveLedgerConflictError,
    append_owner_daily_snapshot,
    append_owner_ledger_event,
)


def _ledger_event(
    *,
    sequence: int,
    event_hash: str,
    predecessor_hash: str | None,
    event_type: str,
    cash_delta: float | None = None,
    quantity_delta: float | None = None,
    quantity_after: float | None = None,
    execution_price: float | None = None,
    fees: float = 0.0,
    holdings: list[dict[str, object]] | None = None,
) -> TrackedEtfSleeveLedgerEvent:
    return TrackedEtfSleeveLedgerEvent(
        user_id=1,
        tracked_position_id=(None if event_type == "opening_reconciliation" else 10),
        sequence_no=sequence,
        idempotency_key=f"event-{sequence}",
        request_hash=str(sequence) * 64,
        event_type=event_type,
        asset_code=(None if event_type == "opening_reconciliation" else "510300"),
        effective_date=date(2026, 8, 10),
        occurred_at=utcnow(),
        cash_delta=cash_delta,
        cash_balance_after=(1_000.0 if event_type == "opening_reconciliation" else None),
        quantity_delta=quantity_delta,
        quantity_after=quantity_after,
        execution_price=execution_price,
        fees=fees,
        adjustment_factor=1.0,
        holdings_after_json=holdings or [],
        provenance="owner_confirmed",
        evidence_ref=None,
        reason_code="test",
        contract_version="tracked_etf_sleeve_ledger_v1",
        predecessor_event_hash=predecessor_hash,
        event_hash=event_hash,
    )


def test_reconstruct_sleeve_ledger_uses_reconciled_cost_and_real_sell_proceeds() -> None:
    opening = _ledger_event(
        sequence=1,
        event_hash="a" * 64,
        predecessor_hash=None,
        event_type="opening_reconciliation",
        holdings=[
            {
                "tracked_position_id": 10,
                "asset_code": "510300",
                "quantity": 500.0,
                "remaining_cost_basis": 5_000.0,
                "adjustment_factor": 1.0,
            }
        ],
    )
    sell = _ledger_event(
        sequence=2,
        event_hash="b" * 64,
        predecessor_hash="a" * 64,
        event_type="sell",
        cash_delta=1_190.0,
        quantity_delta=-100.0,
        quantity_after=400.0,
        execution_price=12.0,
        fees=10.0,
    )

    result = reconstruct_sleeve_ledger((opening, sell))

    assert result.status == "ready"
    assert result.cash_balance == 2_190.0
    assert result.realized_pnl == 190.0
    assert result.holdings[0].quantity == 400.0
    assert result.holdings[0].remaining_cost_basis == 4_000.0


def test_owner_risk_guard_blocks_only_new_risk_and_keeps_exit() -> None:
    context = EtfOwnerRiskContext(
        status="unavailable",
        state="data_halt",
        trade_session=None,
        equity=None,
        flow_adjusted_nav=None,
        high_water_nav=None,
        drawdown=None,
        valuation_coverage=0.0,
        execution_coverage=0.0,
        unavailable_reasons=("sleeve_snapshot_missing",),
        contract_version="tracked_etf_sleeve_risk_v1",
        contract_hash="a" * 64,
        source_hash=None,
    )
    add = apply_owner_risk_guard(
        PositionSizingRecommendation(
            action="add",
            label="加仓",
            recommended_trade_amount=2_000,
            recommended_trade_shares=2_000,
        ),
        context,
    )
    exit_action = apply_owner_risk_guard(
        PositionSizingRecommendation(
            action="exit",
            label="退出",
            recommended_trade_amount=5_000,
            recommended_trade_shares=5_000,
        ),
        context,
    )

    assert add.action == "no_add"
    assert add.action_class == ACTION_CLASS_GUARD_ONLY
    assert add.recommended_trade_amount is None
    assert exit_action.action == "exit"
    assert exit_action.recommended_trade_amount == 5_000
    assert exit_action.owner_risk_control is not None


@pytest.mark.asyncio
async def test_sleeve_repository_is_idempotent_append_only_and_allows_initial_data_halt(
    app,
) -> None:
    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.etf_trading_capital = 10_000
        user.etf_trading_capital_confirmed_at = utcnow()
        position = TrackedPosition(
            user_id=user.id,
            asset_type="etf",
            asset_code="510300",
            asset_name="沪深300ETF",
            buy_date=date(2026, 8, 1),
            confirmed_shares=100,
            buy_amount=500,
            entry_price=5,
            exit_state_json={},
            status="active",
        )
        session.add(position)
        await session.flush()
        command = AppendSleeveLedgerEventCommand(
            owner_id=user.id,
            idempotency_key="opening-1",
            event_type="opening_reconciliation",
            effective_date=date(2026, 8, 10),
            occurred_at=utcnow(),
            provenance="owner_confirmed",
            expected_predecessor_event_hash=None,
            cash_balance_after=9_500,
            holdings_after=(
                {
                    "tracked_position_id": position.id,
                    "asset_code": position.asset_code,
                    "quantity": 100.0,
                    "remaining_cost_basis": 500.0,
                    "adjustment_factor": 1.0,
                },
            ),
        )
        first = await append_owner_ledger_event(session, command)
        replay = await append_owner_ledger_event(session, command)
        snapshot = await append_owner_daily_snapshot(
            session,
            AppendSleeveDailySnapshotCommand(
                owner_id=user.id,
                idempotency_key="snapshot-1",
                snapshot_date=date(2026, 8, 10),
                cutoff_at=utcnow(),
                coverage_state="eligible",
                risk_state="data_halt",
                expected_predecessor_snapshot_hash=None,
                ledger_head_event_hash=first.event.event_hash,
                input_contract_hash="b" * 64,
                nav_contract_hash="c" * 64,
                risk_policy_hash="d" * 64,
                cash_balance=9_500,
                market_value=500,
                equity=10_000,
                flow_adjusted_nav=10_000,
                high_water_nav=10_000,
                drawdown_pct=None,
                holding_count=1,
                valued_holding_count=1,
                ledger_coverage_ratio=1,
                valuation_coverage_ratio=1,
                reasons=("drawdown_history_insufficient",),
            ),
        )
        await session.commit()

        assert first.created is True
        assert replay.created is False
        assert snapshot.snapshot.risk_state == "data_halt"
        assert snapshot.snapshot.equity == 10_000
        current = await build_owner_etf_risk_context(
            session,
            user,
            now=datetime(2026, 8, 10, 20),
        )
        stale = await build_owner_etf_risk_context(
            session,
            user,
            now=datetime(2026, 8, 11, 20),
        )
        assert current.status == "ready"
        assert stale.status == "unavailable"
        assert stale.unavailable_reasons == ("sleeve_snapshot_stale_by_trade_session",)

        first.event.reason_code = "mutated"
        with pytest.raises(TrackedEtfSleeveImmutableError):
            await session.commit()
        await session.rollback()

    async with app.state.db.session() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(TrackedEtfSleeveLedgerEvent)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(TrackedEtfSleeveDailySnapshot)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_reconciliation_api_requires_full_confirmed_holdings_and_replays(app, client) -> None:
    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.etf_trading_capital = 10_000
        user.etf_trading_capital_confirmed_at = utcnow()
        position = TrackedPosition(
            user_id=user.id,
            asset_type="etf",
            asset_code="510300",
            asset_name="沪深300ETF",
            buy_date=date(2026, 8, 1),
            confirmed_shares=100,
            buy_amount=500,
            entry_price=5,
            exit_state_json={"position_episode_id": "episode-1"},
            status="active",
        )
        session.add(position)
        await session.commit()
        position_id = position.id

    payload = {
        "trade_session": date.today().isoformat(),
        "occurred_at": utcnow().isoformat(),
        "cash_balance": 9_500,
        "holdings": [
            {
                "position_id": position_id,
                "quantity": 100,
                "remaining_cost_basis": 500,
            }
        ],
    }
    first = await client.post(
        "/api/tracked-positions/etf-sleeve/reconcile",
        headers={"Idempotency-Key": "owner-reconcile-1"},
        json=payload,
    )
    replay = await client.post(
        "/api/tracked-positions/etf-sleeve/reconcile",
        headers={"Idempotency-Key": "owner-reconcile-1"},
        json=payload,
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "accepted"
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "replayed"

    invalid = await client.post(
        "/api/tracked-positions/etf-sleeve/reconcile",
        headers={"Idempotency-Key": "owner-reconcile-2"},
        json={**payload, "holdings": []},
    )
    assert invalid.status_code == 422

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, position_id)
        assert position is not None
        position.exit_state_json = {"current_adjustment_factor": "not-a-number"}
        await session.commit()
    invalid_factor = await client.post(
        "/api/tracked-positions/etf-sleeve/reconcile",
        headers={"Idempotency-Key": "owner-reconcile-invalid-factor"},
        json=payload,
    )
    assert invalid_factor.status_code == 422
    assert "复权因子无效" in invalid_factor.text

    with pytest.raises(SleeveLedgerConflictError):
        async with app.state.db.session() as session:
            await append_owner_ledger_event(
                session,
                AppendSleeveLedgerEventCommand(
                    owner_id=1,
                    idempotency_key="owner-reconcile-1",
                    event_type="reconciliation",
                    effective_date=date.today(),
                    occurred_at=utcnow(),
                    provenance="owner_confirmed",
                    expected_predecessor_event_hash="0" * 64,
                    cash_balance_after=9_400,
                    holdings_after=(),
                ),
            )


@pytest.mark.asyncio
async def test_daily_materialization_recovers_only_after_two_distinct_eligible_sessions(
    app,
) -> None:
    sessions = (date(2026, 8, 7), date(2026, 8, 10), date(2026, 8, 11))
    cutoff = datetime(2026, 8, 11, 20)
    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.etf_trading_capital = 1_000
        user.etf_trading_capital_confirmed_at = utcnow()
        session.add(
            TradableEtf(
                code="510300",
                name="沪深300ETF",
                exchange="SH",
                trading_rule_label="T+1股票ETF",
                asset_class="broad_index",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        position = TrackedPosition(
            user_id=user.id,
            asset_type="etf",
            asset_code="510300",
            asset_name="沪深300ETF",
            buy_date=date(2026, 8, 1),
            confirmed_shares=100,
            buy_amount=500,
            entry_price=5,
            exit_state_json={},
            status="active",
        )
        session.add(position)
        await session.flush()
        await append_owner_ledger_event(
            session,
            AppendSleeveLedgerEventCommand(
                owner_id=user.id,
                idempotency_key="materialize-opening",
                event_type="opening_reconciliation",
                effective_date=sessions[0],
                occurred_at=datetime(2026, 8, 7, 16),
                provenance="owner_confirmed",
                expected_predecessor_event_hash=None,
                cash_balance_after=500,
                holdings_after=(
                    {
                        "tracked_position_id": position.id,
                        "asset_code": position.asset_code,
                        "quantity": 100.0,
                        "remaining_cost_basis": 500.0,
                        "adjustment_factor": 1.0,
                    },
                ),
            ),
        )
        for index, trade_session in enumerate(sessions):
            observed_at = datetime.combine(trade_session, datetime.min.time()) + timedelta(
                hours=16
            )
            raw_close = 5.0 + index * 0.1
            session.add(
                EtfAdjustedPriceRevision(
                    etf_code="510300",
                    trade_date=trade_session,
                    open=raw_close,
                    high=raw_close,
                    low=raw_close,
                    close=raw_close,
                    volume=1_000_000,
                    turnover=raw_close * 1_000_000,
                    pct_change=0,
                    raw_price_basis="raw_ohlc",
                    research_adjusted_value=raw_close * 2,
                    research_price_basis="total_return_adjusted",
                    data_provider="eastmoney",
                    provider_version="eastmoney.push2his.kline.hfq_v1",
                    source_timestamp=observed_at,
                    adjustment_version="eastmoney.push2his.kline.hfq_v1",
                    decision_eligible=True,
                    decision_ineligibility_reason=None,
                    first_seen_at=observed_at,
                    observed_at=observed_at,
                    payload_hash=f"{index + 1}" * 64,
                    revision_hash=f"{index + 4}" * 64,
                    created_at=observed_at,
                )
            )
        await session.flush()

        results = []
        for trade_session in sessions:
            results.append(
                await materialize_owner_etf_risk_snapshot(
                    session,
                    user,
                    trade_session=trade_session,
                    cutoff_at=cutoff,
                )
            )
        await session.commit()

    assert [result.context.state for result in results] == [
        "data_halt",
        "data_halt",
        "normal",
    ]
    assert results[0].context.equity == 1_000
    assert results[1].context.unavailable_reasons == ("risk_recovery_pending",)
    assert results[2].context.drawdown == 0.0


@pytest.mark.asyncio
async def test_bounded_ledger_read_persists_data_halt_against_actual_owner_head(
    app,
    monkeypatch,
) -> None:
    from app.services.tracked_positions import owner_risk

    trade_session = date(2026, 8, 10)
    async with app.state.db.session() as session:
        user = await session.get(User, 1)
        assert user is not None
        user.etf_trading_capital = 1_000
        user.etf_trading_capital_confirmed_at = utcnow()
        opening = await append_owner_ledger_event(
            session,
            AppendSleeveLedgerEventCommand(
                owner_id=user.id,
                idempotency_key="bounded-opening",
                event_type="opening_reconciliation",
                effective_date=trade_session,
                occurred_at=datetime(2026, 8, 10, 15, 30),
                provenance="owner_confirmed",
                expected_predecessor_event_hash=None,
                cash_balance_after=900,
                holdings_after=(),
            ),
        )
        deposit = await append_owner_ledger_event(
            session,
            AppendSleeveLedgerEventCommand(
                owner_id=user.id,
                idempotency_key="bounded-deposit",
                event_type="cash_deposit",
                effective_date=trade_session,
                occurred_at=datetime(2026, 8, 10, 15, 40),
                provenance="owner_confirmed",
                expected_predecessor_event_hash=opening.event.event_hash,
                cash_delta=100,
            ),
        )
        deposit_hash = deposit.event.event_hash
        monkeypatch.setattr(owner_risk, "MAX_OWNER_LEDGER_READ", 1)

        result = await materialize_owner_etf_risk_snapshot(
            session,
            user,
            trade_session=trade_session,
            cutoff_at=datetime(2026, 8, 10, 20),
        )
        await session.commit()

    assert result.context.status == "unavailable"
    assert "sleeve_ledger_read_bound_reached" in result.context.unavailable_reasons
    async with app.state.db.session() as session:
        snapshot = await session.scalar(
            select(TrackedEtfSleeveDailySnapshot).order_by(
                TrackedEtfSleeveDailySnapshot.sequence_no.desc()
            )
        )
        assert snapshot is not None
        assert snapshot.ledger_head_event_hash == deposit_hash
