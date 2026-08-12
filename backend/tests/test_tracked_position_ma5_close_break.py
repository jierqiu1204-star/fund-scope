from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app.models.entities import EtfPriceHistory, TrackedPosition, TradableEtf
from app.schemas.tracked_positions import TrackedPositionChartPoint
from app.services.market_data import ASIA_SHANGHAI
from app.services.tracked_positions.ma5_close_break import (
    MA5_CLOSE_BREAK_STATE_KEY,
    AdjustedMa5CloseBreakEvidence,
    load_adjusted_ma5_close_break_evidence,
)
from app.services.tracked_positions.service import (
    _performance_analysis,
    merge_exit_state,
    position_analysis,
)

_PROVIDER_VERSION = "eastmoney.push2his.kline.hfq_v1"


def _adjusted_row(
    *,
    code: str,
    trade_date: date,
    adjusted_close: float,
    raw_close: float,
    decision_eligible: bool = True,
) -> EtfPriceHistory:
    return EtfPriceHistory(
        etf_code=code,
        trade_date=trade_date,
        open=raw_close,
        high=raw_close,
        low=raw_close,
        close=raw_close,
        volume=1_000_000,
        turnover=100_000_000,
        pct_change=0.0,
        raw_price_basis="exchange_raw_close",
        research_adjusted_value=adjusted_close if decision_eligible else None,
        research_price_basis="total_return_adjusted" if decision_eligible else None,
        data_provider="eastmoney",
        provider_version=_PROVIDER_VERSION,
        source_timestamp=datetime.combine(trade_date, time(7, 30)),
        adjustment_version=_PROVIDER_VERSION,
        decision_eligible=decision_eligible,
        decision_ineligibility_reason=(
            None if decision_eligible else "missing_total_return_provenance"
        ),
    )


@pytest.mark.asyncio
async def test_adjusted_ma5_break_is_close_only_deduplicated_and_rearms_after_two_sessions(
    app,
) -> None:
    code = "588899"
    now = datetime(2026, 8, 10, 16, 0, tzinfo=ASIA_SHANGHAI)
    initial = [
        (date(2026, 8, 4), 1.00),
        (date(2026, 8, 5), 1.00),
        (date(2026, 8, 6), 1.00),
        (date(2026, 8, 7), 1.00),
        (date(2026, 8, 10), 0.90),
    ]

    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name="复权五日线测试ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add_all(
            [
                _adjusted_row(
                    code=code,
                    trade_date=trade_date,
                    adjusted_close=adjusted,
                    raw_close=0.90,
                )
                for trade_date, adjusted in initial
            ]
        )
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code=code,
            asset_name="复权五日线测试ETF",
            buy_date=date(2026, 8, 10),
            buy_amount=900.0,
            entry_price=0.90,
            entry_price_date=date(2026, 8, 10),
            estimated_shares=1000.0,
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)

        analysis = await position_analysis(session, position, now=now)
        assert analysis.ma5_close_break is not None
        assert analysis.ma5_close_break.adjusted_close == 0.90
        assert analysis.ma5_close_break.adjusted_ma5 == 0.98
        assert analysis.ma5_close_break.should_alert is True
        assert analysis.ma5_close_break.earliest_execution_date == date(2026, 8, 11)
        assert analysis.exit_signal.alert_type == "ma5_close_break_exit"
        assert analysis.exit_signal.email_eligible is True
        assert analysis.technical_metrics["ma5_close_break_condition_met"] is True

        merge_exit_state(position, analysis)
        repeated = await load_adjusted_ma5_close_break_evidence(
            session,
            position,
            now=now,
        )
        assert repeated.condition_met is True
        assert repeated.observation_is_new is False
        assert repeated.should_alert is False

        session.add(
            _adjusted_row(
                code=code,
                trade_date=date(2026, 8, 11),
                adjusted_close=1.00,
                raw_close=1.00,
            )
        )
        await session.flush()
        first_recovery = await load_adjusted_ma5_close_break_evidence(
            session,
            position,
            now=datetime(2026, 8, 11, 16, 0, tzinfo=ASIA_SHANGHAI),
        )
        assert first_recovery.condition_met is False
        assert first_recovery.state_update is not None
        assert first_recovery.state_update["phase"] == "recovering"
        assert first_recovery.state_update["recovery_count"] == 1
        position.exit_state_json = {
            **dict(position.exit_state_json or {}),
            MA5_CLOSE_BREAK_STATE_KEY: first_recovery.state_update,
        }

        session.add(
            _adjusted_row(
                code=code,
                trade_date=date(2026, 8, 12),
                adjusted_close=1.02,
                raw_close=1.02,
            )
        )
        await session.flush()
        second_recovery = await load_adjusted_ma5_close_break_evidence(
            session,
            position,
            now=datetime(2026, 8, 12, 16, 0, tzinfo=ASIA_SHANGHAI),
        )
        assert second_recovery.state_update is not None
        assert second_recovery.state_update["phase"] == "armed"
        position.exit_state_json = {
            **dict(position.exit_state_json or {}),
            MA5_CLOSE_BREAK_STATE_KEY: second_recovery.state_update,
        }

        session.add(
            _adjusted_row(
                code=code,
                trade_date=date(2026, 8, 13),
                adjusted_close=0.80,
                raw_close=0.80,
            )
        )
        await session.flush()
        next_break = await load_adjusted_ma5_close_break_evidence(
            session,
            position,
            now=datetime(2026, 8, 13, 16, 0, tzinfo=ASIA_SHANGHAI),
        )
        assert next_break.condition_met is True
        assert next_break.should_alert is True


@pytest.mark.asyncio
async def test_adjusted_ma5_break_never_uses_raw_close_as_fallback(app) -> None:
    code = "588898"
    sessions = [
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
        date(2026, 8, 7),
        date(2026, 8, 10),
    ]
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code=code,
                name="原始价禁用测试ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="证券账户 T+1 ETF",
                asset_class="sector",
                is_short_term_eligible=True,
                is_watchlist=True,
            )
        )
        session.add_all(
            [
                _adjusted_row(
                    code=code,
                    trade_date=trade_date,
                    adjusted_close=1.0,
                    raw_close=0.8 if trade_date == sessions[-1] else 1.0,
                    decision_eligible=trade_date != sessions[-1],
                )
                for trade_date in sessions
            ]
        )
        position = TrackedPosition(
            user_id=1,
            asset_type="etf",
            asset_code=code,
            asset_name="原始价禁用测试ETF",
            buy_date=date(2026, 8, 10),
            buy_amount=800.0,
            entry_price=0.8,
            entry_price_date=date(2026, 8, 10),
            estimated_shares=1000.0,
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)

        evidence = await load_adjusted_ma5_close_break_evidence(
            session,
            position,
            now=datetime(2026, 8, 10, 16, 0, tzinfo=ASIA_SHANGHAI),
        )

    assert evidence.status == "unavailable"
    assert evidence.reason_code == "missing_total_return_provenance"
    assert evidence.should_alert is False


def test_pnl_hard_stop_keeps_priority_and_merges_initial_ma5_reason() -> None:
    position = TrackedPosition(
        user_id=1,
        asset_type="etf",
        asset_code="588897",
        asset_name="双止损测试ETF",
        buy_date=date(2026, 8, 10),
        buy_amount=1_000.0,
        entry_price=1.0,
        entry_price_date=date(2026, 8, 10),
        estimated_shares=1_000.0,
        status="active",
    )
    evidence = AdjustedMa5CloseBreakEvidence(
        status="eligible",
        reason_code="eligible_total_return_adjusted_close",
        trade_date=date(2026, 8, 10),
        adjusted_close=0.9,
        adjusted_ma5=0.98,
        condition_met=True,
        observation_is_new=True,
        should_alert=True,
        earliest_execution_date=date(2026, 8, 11),
        data_provider="eastmoney",
        provider_version=_PROVIDER_VERSION,
        adjustment_version=_PROVIDER_VERSION,
        source_timestamp=datetime(2026, 8, 10, 7, 30),
    )

    analysis = _performance_analysis(
        position,
        [
            TrackedPositionChartPoint(
                date=date(2026, 8, 10),
                price=0.9,
                estimated_pnl_pct=-10.0,
            )
        ],
        None,
        ma5_close_break=evidence,
    )

    assert analysis.exit_signal.alert_type == "hard_stop"
    assert analysis.exit_signal.email_eligible is True
    assert sum("复权收盘破位" in reason for reason in analysis.exit_signal.reasons) == 1
