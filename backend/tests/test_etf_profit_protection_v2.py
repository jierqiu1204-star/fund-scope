from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.models.entities import EtfPriceHistory, TrackedPosition, TradableEtf
from app.services.risk_alerts import (
    ETF_PROFIT_PROTECTION_STATE_KEY,
    derive_etf_profit_thresholds,
    evaluate_long_profit_protection,
)
from app.services.tracked_positions.service import merge_exit_state, position_analysis

PROVIDER_VERSION = "eastmoney.push2his.kline.hfq_v1"


def test_profit_thresholds_distinguish_low_and_high_volatility_etfs() -> None:
    low = derive_etf_profit_thresholds(
        asset_bucket="bond",
        atr_pct=0.35,
        realized_volatility_pct=0.25,
        median_abs_return_pct=0.20,
    )
    high = derive_etf_profit_thresholds(
        asset_bucket="equity",
        atr_pct=3.20,
        realized_volatility_pct=2.60,
        median_abs_return_pct=2.20,
    )

    assert low is not None
    assert high is not None
    assert low.profit_start_pct < high.profit_start_pct
    assert low.trailing_giveback_pct < high.trailing_giveback_pct
    assert low.entry_risk_unit_pct == pytest.approx(0.25)
    assert high.entry_risk_unit_pct == pytest.approx(2.60)


def test_profit_thresholds_freeze_entry_risk_instead_of_widening_after_volatility_spike() -> None:
    thresholds = derive_etf_profit_thresholds(
        asset_bucket="equity",
        atr_pct=4.00,
        realized_volatility_pct=3.60,
        median_abs_return_pct=3.20,
        persisted_entry_risk_unit_pct=1.10,
    )

    assert thresholds is not None
    assert thresholds.current_risk_unit_pct == pytest.approx(3.50)
    assert thresholds.entry_risk_unit_pct == pytest.approx(1.10)
    assert thresholds.profit_start_pct == pytest.approx(2.00)
    assert thresholds.trailing_giveback_pct == pytest.approx(1.21)


def test_profit_protection_high_water_and_stop_only_ratchet_up() -> None:
    armed = evaluate_long_profit_protection(
        current_profit_pct=8.0,
        observed_high_water_profit_pct=8.0,
        profit_start_pct=3.0,
        trailing_giveback_pct=2.0,
        data_eligible=True,
    )
    after_volatility_spike = evaluate_long_profit_protection(
        current_profit_pct=5.5,
        observed_high_water_profit_pct=5.5,
        profit_start_pct=5.0,
        trailing_giveback_pct=4.0,
        data_eligible=True,
        persisted_high_water_profit_pct=armed.high_water_profit_pct,
        persisted_trailing_stop_pnl_pct=armed.trailing_stop_pnl_pct,
        persisted_armed=True,
    )

    assert armed.state == "armed"
    assert armed.trailing_stop_pnl_pct == pytest.approx(6.0)
    assert after_volatility_spike.high_water_profit_pct == pytest.approx(8.0)
    assert after_volatility_spike.trailing_stop_pnl_pct == pytest.approx(6.0)
    assert after_volatility_spike.state == "triggered"


def test_profit_protection_freezes_existing_line_when_adjusted_data_is_unavailable() -> None:
    waiting = evaluate_long_profit_protection(
        current_profit_pct=7.0,
        observed_high_water_profit_pct=7.0,
        profit_start_pct=None,
        trailing_giveback_pct=None,
        data_eligible=False,
        persisted_high_water_profit_pct=10.0,
        persisted_trailing_stop_pnl_pct=8.0,
        persisted_armed=True,
    )

    assert waiting.state == "data_waiting"
    assert waiting.high_water_profit_pct == pytest.approx(10.0)
    assert waiting.trailing_stop_pnl_pct == pytest.approx(8.0)
    assert waiting.triggered is False


def _tradable_etf(code: str) -> TradableEtf:
    return TradableEtf(
        code=code,
        name=f"利润保护测试ETF{code}",
        exchange="SH",
        theme_tags_json=["宽基"],
        trading_rule_label="证券账户 T+1 ETF",
        asset_class="broad_base",
        is_short_term_eligible=True,
        is_watchlist=True,
    )


async def _seed_history(
    session,
    *,
    code: str,
    closes: list[float],
    adjusted_eligible: bool,
) -> None:
    start = date(2026, 1, 5)
    session.add(_tradable_etf(code))
    for index, close in enumerate(closes):
        session.add(
            EtfPriceHistory(
                etf_code=code,
                trade_date=start + timedelta(days=index),
                open=close * 0.997,
                high=close * 1.006,
                low=close * 0.994,
                close=close,
                volume=10_000_000,
                turnover=100_000_000,
                pct_change=0.0,
                raw_price_basis="unadjusted",
                research_adjusted_value=close if adjusted_eligible else None,
                research_price_basis="total_return_adjusted" if adjusted_eligible else None,
                data_provider="eastmoney" if adjusted_eligible else None,
                provider_version=PROVIDER_VERSION if adjusted_eligible else None,
                source_timestamp=datetime(2026, 2, 28, 8, 0) if adjusted_eligible else None,
                adjustment_version=PROVIDER_VERSION if adjusted_eligible else None,
                decision_eligible=True if adjusted_eligible else False,
                decision_ineligibility_reason=None
                if adjusted_eligible
                else "raw_price_only",
            )
        )
    await session.commit()


def _position(*, code: str, state: dict | None = None) -> TrackedPosition:
    return TrackedPosition(
        user_id=1,
        asset_type="etf",
        asset_code=code,
        asset_name=f"利润保护测试ETF{code}",
        buy_date=date(2026, 1, 5),
        buy_amount=3_000,
        entry_price=1.0,
        entry_price_date=date(2026, 1, 5),
        estimated_shares=3_000,
        confirmed_shares=3_000,
        status="active",
        exit_state_json=state or {},
    )


@pytest.mark.asyncio
async def test_raw_only_history_cannot_arm_etf_profit_protection(app) -> None:
    closes = [1.0 + index * 0.003 for index in range(39)] + [1.08]
    async with app.state.db.session() as session:
        await _seed_history(
            session,
            code="589910",
            closes=closes,
            adjusted_eligible=False,
        )
        position = _position(code="589910")
        session.add(position)
        await session.commit()
        await session.refresh(position)

        analysis = await position_analysis(session, position)

    assert analysis.dynamic_thresholds is not None
    assert analysis.dynamic_thresholds.risk_data_eligible is False
    assert analysis.dynamic_thresholds.risk_data_reason_code == "insufficient_adjusted_history"
    assert analysis.dynamic_thresholds.profit_start_pct is None
    assert analysis.profit_protection is not None
    assert analysis.profit_protection.state == "data_waiting"
    assert all(point.trailing_stop_pnl_pct is None for point in analysis.chart)
    assert analysis.exit_signal.alert_type not in {
        "take_profit_watch",
        "trailing_take_profit",
    }


@pytest.mark.asyncio
async def test_data_outage_freezes_a_triggered_line_without_retriggering(app) -> None:
    closes = [1.0 + index * 0.002 for index in range(39)] + [1.07]
    persisted = {
        ETF_PROFIT_PROTECTION_STATE_KEY: {
            "state": "triggered",
            "triggered": True,
            "data_eligible": True,
            "risk_data_eligible": True,
            "risk_price_basis": "total_return_adjusted",
            "entry_risk_unit_pct": 1.10,
            "high_water_profit_pct": 10.0,
            "trailing_stop_pnl_pct": 8.0,
        }
    }
    async with app.state.db.session() as session:
        await _seed_history(
            session,
            code="589912",
            closes=closes,
            adjusted_eligible=False,
        )
        position = _position(code="589912", state=persisted)
        session.add(position)
        await session.commit()
        await session.refresh(position)

        analysis = await position_analysis(session, position)
        merge_exit_state(position, analysis)
        saved = dict(position.exit_state_json[ETF_PROFIT_PROTECTION_STATE_KEY])

    assert analysis.profit_protection is not None
    assert analysis.profit_protection.state == "data_waiting"
    assert analysis.profit_protection.triggered is False
    assert analysis.exit_signal.alert_type != "trailing_take_profit"
    assert saved["state"] == "armed"
    assert saved["triggered"] is False
    assert saved["trailing_stop_pnl_pct"] == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_persisted_high_water_survives_lower_bounded_chart_and_stop_is_not_backfilled(
    app,
) -> None:
    closes = [1.0 + index * 0.0025 for index in range(39)] + [1.07]
    persisted = {
        ETF_PROFIT_PROTECTION_STATE_KEY: {
            "state": "armed",
            "data_eligible": True,
            "risk_data_eligible": True,
            "risk_price_basis": "total_return_adjusted",
            "entry_risk_unit_pct": 1.10,
            "high_water_profit_pct": 12.0,
            "trailing_stop_pnl_pct": 9.0,
        }
    }
    async with app.state.db.session() as session:
        await _seed_history(
            session,
            code="589911",
            closes=closes,
            adjusted_eligible=True,
        )
        position = _position(code="589911", state=persisted)
        session.add(position)
        await session.commit()
        await session.refresh(position)

        analysis = await position_analysis(session, position)
        merge_exit_state(position, analysis)
        saved = dict(position.exit_state_json[ETF_PROFIT_PROTECTION_STATE_KEY])

    assert analysis.dynamic_thresholds is not None
    assert analysis.dynamic_thresholds.risk_data_eligible is True
    assert analysis.dynamic_thresholds.entry_risk_unit_pct == pytest.approx(1.10)
    assert analysis.max_profit_pct == pytest.approx(12.0)
    assert analysis.profit_protection is not None
    assert analysis.profit_protection.trailing_stop_pnl_pct >= 9.0
    assert saved["high_water_profit_pct"] == pytest.approx(12.0)
    assert saved["trailing_stop_pnl_pct"] >= 9.0
    assert analysis.chart[0].trailing_stop_pnl_pct is None
    assert analysis.chart[-1].trailing_stop_pnl_pct >= 9.0
