from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.core.auth import hash_password
from app.models.entities import (
    EtfPriceHistory,
    Stock,
    TrackedPosition,
    TrackedPositionAlert,
    TradableEtf,
    User,
)
from app.services.leader_tactics_exit_policy import (
    LEADER_TACTICS_TAKE_PROFIT,
    evaluate_leader_exit_thresholds,
)
from app.services.risk_alerts import (
    ALERT_LEADER_TACTICS_EXIT,
    LEADER_TACTICS_BREAKEVEN_EXIT,
    LEADER_TACTICS_DATA_WAITING,
    LEADER_TACTICS_HARD_STOP,
    LEADER_TACTICS_MA5_EXIT,
    LeaderTacticsDailyBar,
    LeaderTacticsExitInput,
    calculate_position_sizing,
    evaluate_leader_tactics_exit,
    map_exit_signal_to_position_action,
)
from app.services.tracked_positions.alert_policy import (
    resolve_alert_policy_selection,
)
from app.services.tracked_positions.service import (
    AlertDecision,
    PreparedAlertEvaluation,
    _leader_entry_anchor_date,
    _mark_leader_exit_notification_sent,
    create_alert_if_needed,
    prepare_alert_evaluation,
    resolve_asset_name,
)


def _bar(
    day: date,
    close: float,
    *,
    version: str = "adjustment-v1",
    received_at: datetime | None = None,
) -> LeaderTacticsDailyBar:
    return LeaderTacticsDailyBar(
        trade_date=day,
        adjusted_open=close,
        adjusted_high=close + 0.5,
        adjusted_low=close - 0.5,
        adjusted_close=close,
        provider="eastmoney",
        adjustment_version=version,
        revision_id=f"revision-{day.isoformat()}",
        received_at=received_at or datetime.combine(day, datetime.min.time()),
    )


def _bars(closes: list[float], *, version: str = "adjustment-v1") -> list[LeaderTacticsDailyBar]:
    start = date(2026, 1, 1)
    return [
        _bar(start + timedelta(days=index), close, version=version)
        for index, close in enumerate(closes)
    ]


def _evaluation(bars: list[LeaderTacticsDailyBar]) -> LeaderTacticsExitInput:
    return LeaderTacticsExitInput(
        entry_anchor_date=bars[20].trade_date,
        evaluation_cutoff=datetime(2026, 1, 25, 8, 0),
        bars=bars,
    )


def test_invalid_dates_fail_closed_without_calling_isoformat() -> None:
    bars = _bars([10.0] * 25)
    invalid_cutoff = replace(_evaluation(bars), evaluation_cutoff="not-a-date")
    invalid_entry = replace(_evaluation(bars), entry_anchor_date="not-a-date")

    cutoff_result = evaluate_leader_tactics_exit(invalid_cutoff)  # type: ignore[arg-type]
    entry_result = evaluate_leader_tactics_exit(invalid_entry)  # type: ignore[arg-type]

    assert cutoff_result.reason_code == LEADER_TACTICS_DATA_WAITING
    assert entry_result.reason_code == LEADER_TACTICS_DATA_WAITING


def test_after_close_tracking_anchors_to_the_next_eligible_session() -> None:
    position = TrackedPosition(
        buy_date=date(2026, 8, 14),
        confirmed_nav_date=date(2026, 8, 14),
        order_time_bucket="after_15",
    )

    assert _leader_entry_anchor_date(position) == date(2026, 8, 15)


@pytest.mark.parametrize("future_kind", ["received_at", "trade_date"])
def test_future_bars_are_not_decision_eligible(future_kind: str) -> None:
    bars = _bars([10.0] * 25)
    if future_kind == "received_at":
        bars[-1] = replace(bars[-1], received_at=datetime(2026, 1, 25, 9, 0))
    else:
        bars[-1] = replace(
            bars[-1],
            trade_date=date(2026, 1, 26),
            received_at=datetime(2026, 1, 26, 0, 0),
        )

    result = evaluate_leader_tactics_exit(_evaluation(bars))

    assert result.reason_code == LEADER_TACTICS_DATA_WAITING


def test_aware_and_naive_cutoffs_use_the_same_visibility_rule() -> None:
    bars = _bars([10.0] * 25)
    aware = replace(
        _evaluation(bars),
        evaluation_cutoff=datetime(2026, 1, 25, 16, 0, tzinfo=UTC),
    )
    naive = replace(_evaluation(bars), evaluation_cutoff=datetime(2026, 1, 25, 16, 0))

    aware_result = evaluate_leader_tactics_exit(aware)
    naive_result = evaluate_leader_tactics_exit(naive)

    assert aware_result.data_eligible is True
    assert naive_result.data_eligible is True


def test_mixed_adjustment_versions_fail_closed() -> None:
    bars = _bars([10.0] * 25)
    bars[-1] = replace(bars[-1], adjustment_version="adjustment-v2")

    result = evaluate_leader_tactics_exit(_evaluation(bars))

    assert result.reason_code == LEADER_TACTICS_DATA_WAITING
    assert result.threshold_context["data_reason"] == "adjustment_version_mismatch"


def test_high_water_and_armed_scan_all_visible_closes_not_only_current_close() -> None:
    closes = [10.0] * 21 + [13.0, 10.5, 10.6, 10.7]
    result = evaluate_leader_tactics_exit(_evaluation(_bars(closes)))

    assert result.data_eligible is True
    assert result.threshold_context["adjustment_version"] == "adjustment-v1"
    assert result.threshold_context["high_water_adjusted_close"] == pytest.approx(13.0)
    assert result.threshold_context["armed"] is True
    assert result.reason_code == LEADER_TACTICS_MA5_EXIT


def test_hard_stop_reason_wins_when_ma5_is_higher_than_the_hard_stop() -> None:
    closes = [10.0] * 21 + [12.0, 12.0, 12.0, 7.0]

    result = evaluate_leader_tactics_exit(_evaluation(_bars(closes)))

    assert result.threshold_context["adjusted_ma5"] > result.threshold_context["initial_stop"]
    assert result.threshold_context["current_adjusted_close"] < result.threshold_context["initial_stop"]
    assert result.reason_code == LEADER_TACTICS_HARD_STOP


def test_armed_breakeven_reason_wins_over_ma5_without_hard_stop() -> None:
    closes = [10.0] * 21 + [12.0, 10.4, 10.3, 9.99]

    result = evaluate_leader_tactics_exit(_evaluation(_bars(closes)))

    assert result.threshold_context["armed"] is True
    assert result.threshold_context["current_adjusted_close"] > result.threshold_context[
        "initial_stop"
    ]
    assert result.reason_code == LEADER_TACTICS_BREAKEVEN_EXIT


def test_optional_take_profit_triggers_on_close_at_or_above_target() -> None:
    result = evaluate_leader_exit_thresholds(
        entry_close=10.0,
        initial_stop=9.0,
        risk_unit=1.0,
        previous_high=None,
        visible_closes=(10.0, 10.5),
        ma5=9.8,
        take_profit_line=10.5,
    )

    assert result.reason_code == LEADER_TACTICS_TAKE_PROFIT
    assert result.take_profit_line == 10.5


def test_frozen_long_position_can_evaluate_after_entry_leaves_bounded_window() -> None:
    bars = _bars([10.0, 12.0, 11.0, 10.5, 10.4])
    result = evaluate_leader_tactics_exit(
        LeaderTacticsExitInput(
            entry_anchor_date=date(2025, 1, 1),
            evaluation_cutoff=datetime(2026, 1, 5, 8, 0),
            bars=bars,
            persisted_state={
                "entry_adjusted_close": 10.0,
                "entry_atr20": 0.5,
                "initial_stop": 9.0,
                "risk_unit": 1.0,
                "adjustment_version": "adjustment-v1",
                "high_water_adjusted_close": 12.0,
                "armed": True,
            },
        )
    )

    assert result.data_eligible is True
    assert result.threshold_context["bar_date"] == "2026-01-05"
    assert result.threshold_context["high_water_adjusted_close"] == pytest.approx(12.0)


def test_truncated_window_preserves_persisted_high_water_arming() -> None:
    bars = _bars([10.2, 10.1, 10.0, 9.9, 9.8])
    result = evaluate_leader_tactics_exit(
        LeaderTacticsExitInput(
            entry_anchor_date=date(2025, 1, 1),
            evaluation_cutoff=datetime(2026, 1, 5, 8, 0),
            bars=bars,
            persisted_state={
                "entry_adjusted_close": 10.0,
                "entry_atr20": 0.5,
                "initial_stop": 9.0,
                "risk_unit": 1.0,
                "adjustment_version": "adjustment-v1",
                "high_water_adjusted_close": 11.25,
                "armed": False,
            },
        )
    )

    assert result.data_eligible is True
    assert result.threshold_context["high_water_adjusted_close"] == pytest.approx(11.25)
    assert result.threshold_context["armed"] is True
    assert result.threshold_context["breakeven_line"] == pytest.approx(10.0)
    assert result.threshold_context["fee_bps_per_side"] == 0.0
    assert result.threshold_context["slippage_bps_per_side"] == 0.0
    assert result.threshold_context["round_trip_cost_bps"] == 0.0


@pytest.mark.parametrize(
    "state_patch",
    [
        {"adjustment_version": "adjustment-v2"},
        {"adjustment_version": "adjustment-v1", "risk_unit": 0.75},
        {"adjustment_version": "adjustment-v1", "initial_stop": 10.5},
    ],
)
def test_corrupt_or_mixed_frozen_risk_basis_fails_closed(
    state_patch: dict[str, object],
) -> None:
    state: dict[str, object] = {
        "entry_adjusted_close": 10.0,
        "entry_atr20": 0.5,
        "initial_stop": 9.0,
        "risk_unit": 1.0,
        "adjustment_version": "adjustment-v1",
        "high_water_adjusted_close": 11.25,
        "armed": True,
    }
    state.update(state_patch)

    result = evaluate_leader_tactics_exit(
        LeaderTacticsExitInput(
            entry_anchor_date=date(2025, 1, 1),
            evaluation_cutoff=datetime(2026, 1, 5, 8, 0),
            bars=_bars([10.2, 10.1, 10.0, 9.9, 9.8]),
            persisted_state=state,
        )
    )

    assert result.data_eligible is False
    assert result.reason_code == LEADER_TACTICS_DATA_WAITING
    assert result.threshold_context["data_reason"] == "frozen_state_basis_mismatch"


def test_failed_notification_does_not_permanently_suppress_same_exit_signal() -> None:
    closes = [10.0] * 21 + [13.0, 10.5, 10.6, 10.7]
    first = evaluate_leader_tactics_exit(_evaluation(_bars(closes)))
    retry = evaluate_leader_tactics_exit(
        replace(
            _evaluation(_bars(closes)),
            persisted_state=dict(first.state_update),
        )
    )
    sent_state = dict(first.state_update)
    sent_state["exit_notification_sent"] = True
    after_success = evaluate_leader_tactics_exit(
        replace(_evaluation(_bars(closes)), persisted_state=sent_state)
    )

    assert first.actionable is True
    assert retry.actionable is True
    assert after_success.actionable is False
    assert after_success.reason_code == "leader_tactics_exit_already_triggered"


def test_notification_success_is_the_only_state_that_suppresses_future_retry() -> None:
    position = TrackedPosition(
        exit_state_json={
            "leader_tactics_exit_v1": {
                "exit_triggered": True,
                "exit_notification_sent": False,
            }
        }
    )
    alert = TrackedPositionAlert(id=42)

    _mark_leader_exit_notification_sent(position, alert)

    state = position.exit_state_json["leader_tactics_exit_v1"]
    assert state["exit_triggered"] is True
    assert state["exit_notification_sent"] is True
    assert state["exit_notification_alert_id"] == 42


async def test_leader_policy_is_daily_close_only_for_intraday_evaluation() -> None:
    position = TrackedPosition(
        asset_type="etf",
        asset_code="510300",
        alert_policy_id="leader_tactics_exit_v1",
    )

    prepared = await prepare_alert_evaluation(
        None,  # type: ignore[arg-type]
        position,
        evaluation_mode="intraday",
    )

    assert prepared.decision is None
    assert prepared.analysis is None
    assert prepared.data_reason_code == "leader_tactics_daily_close_only"


def test_leader_exit_maps_to_full_exit_for_etf_and_stock() -> None:
    mapped = map_exit_signal_to_position_action(
        alert_type="leader_tactics_exit",
        allow_full_exit=True,
    )
    etf = calculate_position_sizing(
        asset_type="etf",
        alert_type="leader_tactics_exit",
        current_market_value=1_000.0,
        current_price=10.0,
        etf_trading_capital=10_000.0,
        capital_confirmed=True,
        allow_full_exit=True,
        exposure_baseline_quantity=100.0,
    )
    stock = calculate_position_sizing(
        asset_type="stock",
        alert_type="leader_tactics_exit",
        current_market_value=None,
        current_price=None,
        etf_trading_capital=None,
        capital_confirmed=False,
        allow_full_exit=True,
    )

    assert mapped.action == "exit"
    assert mapped.target_remaining_fraction == 0.0
    assert etf.action == "exit"
    assert etf.target_account_weight == pytest.approx(0.0)
    assert stock.action == "exit"
    assert stock.recommended_trade_amount is None
    assert "券商实际持仓" in (stock.reason or "")


@pytest.mark.asyncio
async def test_leader_policy_accepts_etf_and_stock_but_rejects_fund() -> None:
    etf = await resolve_alert_policy_selection(
        None,  # type: ignore[arg-type]
        asset_type="etf",
        asset_code="512710",
        policy_id="leader_tactics_exit_v1",
    )
    stock = await resolve_alert_policy_selection(
        None,  # type: ignore[arg-type]
        asset_type="stock",
        asset_code="600000",
        policy_id="leader_tactics_exit_v1",
    )

    assert etf.provenance == "manual_selection"
    assert stock.provenance == "manual_selection"
    with pytest.raises(ValueError, match="只支持 ETF 或股票"):
        await resolve_alert_policy_selection(
            None,  # type: ignore[arg-type]
            asset_type="fund",
            asset_code="270042",
            policy_id="leader_tactics_exit_v1",
        )
    with pytest.raises(ValueError, match="股票追踪只支持"):
        await resolve_alert_policy_selection(
            None,  # type: ignore[arg-type]
            asset_type="stock",
            asset_code="600000",
            policy_id="standard_dynamic_v2",
        )


@pytest.mark.asyncio
async def test_candidate_backed_leader_policy_requires_confirmed_manifest_evidence(
    monkeypatch,
) -> None:
    manifest_hash = "leader-manifest-" + "m" * 48

    async def fake_manifest(session, *, universe, as_of):
        assert universe == "ashare"
        return {"manifest_hash": manifest_hash}

    class FakeResult:
        def mappings(self):
            return self

        def first(self):
            return {
                "formula_id": "leader_breakout_proxy_v2",
                "signal_date": "2026-08-14",
                "asset_name": "候选股票",
                "gate_facts_json": (
                    '{"adjusted_low":9.8,"adjusted_atr20":0.35}'
                ),
                "effective_state": "confirmed",
            }

    class FakeSession:
        async def execute(self, statement, params):
            assert params["manifest_hash"] == manifest_hash
            assert params["asset_code"] == "600000"
            assert params["qualifies"] is True
            return FakeResult()

    monkeypatch.setattr(
        "app.services.tracked_positions.alert_policy.get_v2_materialized_manifest",
        fake_manifest,
    )
    selection = await resolve_alert_policy_selection(
        FakeSession(),  # type: ignore[arg-type]
        asset_type="stock",
        asset_code="600000.SH",
        policy_id="leader_tactics_exit_v1",
        source_manifest_hash=manifest_hash,
        source_decision_at=datetime(2026, 8, 14, 6, 30, tzinfo=UTC),
    )

    assert selection.provenance == "candidate_backed"
    assert selection.source_strategy == "leader_tactics_v2"
    assert selection.source_context is not None
    assert selection.source_context["signal_adjusted_low"] == pytest.approx(9.8)


@pytest.mark.asyncio
async def test_stock_tracking_name_requires_authoritative_universe_snapshot(app) -> None:
    async with app.state.db.session() as session:
        await session.execute(
            text(
                """
                CREATE TABLE ashare_research_universe_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date DATE NOT NULL,
                    asset_code VARCHAR(32) NOT NULL,
                    asset_name VARCHAR(256) NOT NULL,
                    listing_state VARCHAR(32) NOT NULL,
                    effective_at DATETIME NOT NULL,
                    received_at DATETIME NOT NULL,
                    provider VARCHAR(64) NOT NULL,
                    source_cutoff DATETIME NOT NULL,
                    exclusion_reason VARCHAR(256),
                    fact_hash VARCHAR(128) NOT NULL UNIQUE
                )
                """
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO ashare_research_universe_snapshots
                    (snapshot_date, asset_code, asset_name, listing_state,
                     effective_at, received_at, provider, source_cutoff,
                     exclusion_reason, fact_hash)
                VALUES
                    ('2026-08-14', '600000', '权威名称', 'listed',
                     '2026-08-14 08:00:00', '2026-08-14 08:01:00',
                     'eastmoney', '2026-08-14 08:01:00', NULL, 'snapshot-600000')
                """
            )
        )
        session.add(
            Stock(code="600000", exchange="SH", name="旧表名称", industry="银行")
        )
        session.add(
            Stock(code="600001", exchange="SH", name="只有旧表", industry="银行")
        )
        await session.commit()

        authoritative = await resolve_asset_name(session, "stock", "600000")
        with pytest.raises(ValueError, match="已登记股票"):
            await resolve_asset_name(session, "stock", "600001")

    assert authoritative == "权威名称"


@pytest.mark.asyncio
async def test_failed_smtp_retries_same_leader_alert_and_success_suppresses(
    app, monkeypatch
) -> None:
    attempts = 0

    async def fake_send_template(
        self, session, *, recipient: str, template_name: str, payload: dict
    ) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary smtp failure")
        return "sent"

    monkeypatch.setattr(
        "app.services.tracked_positions.service.Notifier.send_template",
        fake_send_template,
    )
    signal_date = date(2026, 8, 14)
    async with app.state.db.session() as session:
        user = User(
            email="leader-holder@example.com",
            password_hash=hash_password("password-123"),
            recipient_email="leader-alert@example.com",
            is_approved=True,
            is_super_admin=False,
            smtp_host="smtp.saved.local",
            smtp_port=2525,
            smtp_username="saved-user",
            smtp_password_ref="env:SMTP_PASSWORD",
            smtp_from="FundScope <saved@example.com>",
        )
        session.add(user)
        await session.flush()
        session.add(
            TradableEtf(
                code="513599",
                name="龙头策略测试ETF",
                exchange="SH",
                trading_rule_label="T+1",
                asset_class="ETF",
            )
        )
        session.add(
            EtfPriceHistory(
                etf_code="513599",
                trade_date=signal_date,
                open=1.0,
                high=1.0,
                low=0.98,
                close=0.99,
                volume=1_000,
                turnover=100_000,
                pct_change=-1.0,
            )
        )
        position = TrackedPosition(
            user_id=user.id,
            asset_type="etf",
            asset_code="513599",
            asset_name="龙头策略测试ETF",
            buy_date=date(2026, 8, 1),
            buy_amount=1_000,
            entry_price=1.0,
            entry_price_date=date(2026, 8, 1),
            estimated_shares=1_000,
            alert_policy_id="leader_tactics_exit_v1",
            alert_policy_version="leader_tactics_exit_v1",
            alert_policy_provenance="manual_selection",
            exit_state_json={
                "leader_tactics_exit_v1": {
                    "exit_triggered": True,
                    "exit_notification_sent": False,
                },
                "exit_signal_threshold_context": {
                    "leader_tactics": {
                        "data_eligible": True,
                        "price_basis": "total_return_adjusted",
                        "bar_date": signal_date.isoformat(),
                        "bar_cutoff": "2026-08-14T15:30:00+08:00",
                        "provider": "eastmoney",
                        "adjustment_version": "adjustment-v1",
                        "revision": "revision-1",
                    }
                },
            },
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)

        prepared = PreparedAlertEvaluation(
            decision=AlertDecision(
                alert_type=ALERT_LEADER_TACTICS_EXIT,
                trigger_label="龙头策略全额退出提醒",
                reasons=["复权收盘跌破 MA5。"],
                risk_flags=[],
                advisor_summary=None,
                signal_item=None,
                advisor_report=None,
                alert_level="warning",
                alert_source="leader_tactics_daily_close",
            ),
            signal_date=signal_date,
            analysis=None,
            data_reason_code="leader_tactics_prepared",
        )

        first, first_status = await create_alert_if_needed(
            session,
            position,
            app.state.settings,
            prepared_evaluation=prepared,
        )
        second, second_status = await create_alert_if_needed(
            session,
            position,
            app.state.settings,
            prepared_evaluation=prepared,
        )
        third, third_status = await create_alert_if_needed(
            session,
            position,
            app.state.settings,
            prepared_evaluation=prepared,
        )

        alert_count = await session.scalar(
            select(func.count(TrackedPositionAlert.id)).where(
                TrackedPositionAlert.tracked_position_id == position.id
            )
        )

    assert first is not None
    assert first_status == "email_failed"
    assert second is not None
    assert second.id == first.id
    assert second_status == "email_sent"
    assert third is not None
    assert third.id == first.id
    assert third_status == "deduplicated"
    assert attempts == 2
    assert alert_count == 1
    assert position.exit_state_json["leader_tactics_exit_v1"][
        "exit_notification_sent"
    ] is True
