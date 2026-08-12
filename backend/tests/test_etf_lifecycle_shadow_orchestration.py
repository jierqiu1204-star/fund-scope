from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    TrackedPosition,
    TrackedPositionActionDecision,
    TrackedPositionAlertAudit,
    TrackedPositionLifecycleShadowEvidence,
    TrackedPositionNotificationItem,
)
from app.schemas.etf_quotes import TrackedEtfIntradaySnapshotOut
from app.schemas.tracked_positions import (
    TrackedPositionChartPoint,
    TrackedPositionExitSignal,
)
from app.services.risk_alerts import (
    ALERT_HARD_STOP,
    AlertDecision,
    evaluate_position_risk_rule_set,
)
from app.services.tracked_positions.jobs import daily_tracked_position_alerts_job
from app.services.tracked_positions.service import (
    PositionAnalysis,
    PreparedAlertEvaluation,
)
from app.services.workflows.tracked_position_lifecycle import (
    LifecycleEvaluationResult,
)
from app.services.workflows.tracked_position_lifecycle_shadow import (
    TrackedPositionLifecycleShadowObserver,
)


async def _position(
    app,
    *,
    asset_type: str = "etf",
    asset_code: str = "513520",
    with_baseline: bool = True,
) -> TrackedPosition:
    state = {
        "position_episode_id": f"episode-{asset_code}",
        "exposure_version": 1,
        "alert_rule_states": {},
    }
    if with_baseline:
        state["exposure_baseline"] = {
            "normalized_quantity": 1000.0,
            "account_weight": 0.3,
            "source": "confirmed_shares",
            "adjustment_factor": 1.0,
        }
    async with app.state.db.session() as session:
        position = TrackedPosition(
            user_id=1,
            asset_type=asset_type,
            asset_code=asset_code,
            asset_name=f"asset-{asset_code}",
            buy_date=date(2026, 8, 1),
            confirmed_shares=1000.0,
            estimated_shares=1000.0,
            buy_amount=1000.0,
            entry_price=1.0,
            exit_state_json=state,
            exit_state_version=0,
            status="active",
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)
        session.expunge(position)
        return position


def _prepared(
    *,
    quote_time: datetime = datetime(2026, 8, 10, 14, 50),
    price: float = 0.95,
    decision_eligible: bool = True,
) -> PreparedAlertEvaluation:
    reliability = "fresh_consensus" if decision_eligible else "stale"
    snapshot = TrackedEtfIntradaySnapshotOut(
        current_price=price,
        quote_time=quote_time,
        trade_date=quote_time.date(),
        price_source="intraday_quote",
        reliability_level=reliability,
        email_eligible=decision_eligible,
        is_stale=not decision_eligible,
        freshness_status="fresh" if decision_eligible else "display_only",
        bid_price=price - 0.001,
        ask_price=price + 0.001,
        source="eastmoney+akshare",
        decision_eligible=decision_eligible,
        provider_count=2,
        fresh_provider_count=2 if decision_eligible else 0,
        limitation_reason=None if decision_eligible else "stale quote",
    )
    decision = AlertDecision(
        alert_type=ALERT_HARD_STOP,
        trigger_label="硬止损提醒",
        reasons=["test"],
        risk_flags=[],
        advisor_summary=None,
        signal_item=None,
        advisor_report=None,
        alert_source="intraday_quote",
        quote_time=quote_time,
    )
    return PreparedAlertEvaluation(
        decision=decision,
        signal_date=quote_time.date(),
        analysis=PositionAnalysis(
            chart=[
                TrackedPositionChartPoint(
                    date=quote_time.date(),
                    price=price,
                    estimated_pnl_pct=-5.0,
                )
            ],
            exit_signal=TrackedPositionExitSignal(
                alert_type=ALERT_HARD_STOP,
                label="硬止损提醒",
                level="urgent",
                action_class="actionable_exit",
            ),
            max_profit_pct=0.0,
            profit_giveback_pct=5.0,
            holding_days=9,
            technical_metrics={
                "current_pnl_pct": -5.0,
                "hard_stop_pct": -4.0,
                "profit_giveback_pct": 5.0,
                "trailing_threshold_pct": 2.0,
                "profit_start_pct": 3.0,
                "trend_weakening": True,
                "confirmed_trend_weakening": True,
            },
            intraday_snapshot=snapshot,
        ),
        data_reason_code="prepared_analysis",
    )


def test_complete_rule_set_includes_false_recovery_and_freezes_ineligible_data() -> None:
    metrics = {
        "current_pnl_pct": 1.0,
        "hard_stop_pct": -4.0,
        "profit_giveback_pct": 0.5,
        "trailing_threshold_pct": 2.0,
        "profit_start_pct": 3.0,
        "trend_weakening": False,
        "confirmed_trend_weakening": False,
    }
    eligible = evaluate_position_risk_rule_set(
        technical_metrics=metrics,
        legacy_alert_type=None,
        data_eligible=True,
        data_reason_code="fresh_quote",
    )
    assert {rule.rule_id for rule in eligible} == {
        "hard_stop",
        "trailing_take_profit",
        "confirmed_trend_weakening",
        "exit_watch",
        "trend_weakening",
        "take_profit_watch",
    }
    assert all(rule.condition_met is False for rule in eligible)
    assert all(rule.recovery_met is True for rule in eligible)

    ineligible = evaluate_position_risk_rule_set(
        technical_metrics=metrics,
        legacy_alert_type=ALERT_HARD_STOP,
        data_eligible=False,
        data_reason_code="quote_not_decision_eligible",
    )
    assert all(rule.data_state == "data_waiting" for rule in ineligible)
    assert all(not rule.condition_met and not rule.recovery_met for rule in ineligible)

    missing_threshold = evaluate_position_risk_rule_set(
        technical_metrics={
            key: value
            for key, value in metrics.items()
            if key != "trailing_threshold_pct"
        },
        legacy_alert_type=None,
        data_eligible=True,
        data_reason_code="fresh_quote",
    )
    trailing_rule = next(
        rule
        for rule in missing_threshold
        if rule.rule_id == "trailing_take_profit"
    )
    assert trailing_rule.data_state == "no_data"
    assert trailing_rule.recovery_met is False


@pytest.mark.asyncio
async def test_shadow_is_etf_only_disabled_safe_idempotent_and_has_no_production_side_effects(
    app,
) -> None:
    etf = await _position(app)
    fund = await _position(app, asset_type="fund", asset_code="270042")
    prepared = _prepared()

    disabled = TrackedPositionLifecycleShadowObserver(
        enabled=False,
        session_factory=app.state.db.session,
    )
    assert (
        await disabled.observe(position=etf, prepared=prepared, evaluation_mode="daily")
    ).reason_code == "shadow_disabled"

    observer = TrackedPositionLifecycleShadowObserver(
        enabled=True,
        session_factory=app.state.db.session,
    )
    fund_result = await observer.observe(
        position=fund,
        prepared=prepared,
        evaluation_mode="daily",
    )
    first = await observer.observe(
        position=etf,
        prepared=prepared,
        evaluation_mode="daily",
    )
    second = await observer.observe(
        position=etf,
        prepared=prepared,
        evaluation_mode="daily",
    )

    assert fund_result.status == "not_applicable"
    assert first.status == "evaluated"
    assert second.status == "duplicate"
    async with app.state.db.session() as session:
        shadow_rows = (await session.scalars(select(TrackedPositionLifecycleShadowEvidence))).all()
        assert len(shadow_rows) == 1
        assert shadow_rows[0].action_evidence_json["legacy_comparison_status"] == "compared"
        assert (
            shadow_rows[0].action_evidence_json["execution_provenance"] == "simulated_not_observed"
        )
        assert (
            await session.scalar(select(func.count()).select_from(TrackedPositionActionDecision))
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(TrackedPositionNotificationItem))
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(TrackedPositionAlertAudit)) == 0
        )


@pytest.mark.asyncio
async def test_shadow_ineligible_data_freezes_missing_state_defers_and_older_snapshot_skips(
    app,
) -> None:
    etf = await _position(app)
    missing_state = await _position(app, asset_code="159001", with_baseline=False)
    observer = TrackedPositionLifecycleShadowObserver(
        enabled=True,
        session_factory=app.state.db.session,
    )

    ineligible = await observer.observe(
        position=etf,
        prepared=_prepared(decision_eligible=False),
        evaluation_mode="intraday",
    )
    missing = await observer.observe(
        position=missing_state,
        prepared=_prepared(),
        evaluation_mode="intraday",
    )
    assert ineligible.status == "data_ineligible"
    assert missing.reason_code == "missing_exposure_baseline"

    newer_etf = await _position(app, asset_code="513521")
    first = await observer.observe(
        position=newer_etf,
        prepared=_prepared(quote_time=datetime(2026, 8, 10, 14, 51), price=1.01),
        evaluation_mode="intraday",
    )
    older = await observer.observe(
        position=newer_etf,
        prepared=_prepared(quote_time=datetime(2026, 8, 10, 14, 50), price=1.0),
        evaluation_mode="intraday",
    )
    assert first.status == "evaluated"
    assert older.reason_code == "out_of_order_snapshot"

    async with app.state.db.session() as session:
        row = await session.scalar(
            select(TrackedPositionLifecycleShadowEvidence).where(
                TrackedPositionLifecycleShadowEvidence.tracked_position_id == etf.id
            )
        )
        assert row is not None
        assert row.data_state == "data_waiting"
        assert all(
            transition["from_state"] == transition["to_state"]
            for transition in row.transitions_json
        )


@pytest.mark.asyncio
async def test_shadow_failure_is_isolated_and_daily_job_reuses_one_prepared_analysis(
    app,
    settings,
    monkeypatch,
) -> None:
    first_position = await _position(app, asset_code="513522")
    second_position = await _position(app, asset_code="513523")
    observer = TrackedPositionLifecycleShadowObserver(
        enabled=True,
        session_factory=app.state.db.session,
    )
    calls = 0

    async def flaky_execute(*args, **kwargs) -> LifecycleEvaluationResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("bounded test failure")
        return LifecycleEvaluationResult("shadow_recorded", 0, None, "event", None)

    monkeypatch.setattr(
        "app.services.workflows.tracked_position_lifecycle_shadow.execute_position_lifecycle_evaluation",
        flaky_execute,
    )
    failed = await observer.observe(
        position=first_position,
        prepared=_prepared(),
        evaluation_mode="daily",
    )
    continued = await observer.observe(
        position=second_position,
        prepared=_prepared(price=1.01),
        evaluation_mode="daily",
    )
    assert failed.status == "failed"
    assert continued.status == "evaluated"

    prepared = _prepared()
    prepared_calls = 0
    legacy_calls = 0
    observed_ids: list[int] = []

    async def fake_refresh(*args, **kwargs) -> None:
        return None

    async def fake_prepare(*args, **kwargs) -> PreparedAlertEvaluation:
        nonlocal prepared_calls
        prepared_calls += 1
        return prepared

    async def fake_legacy(*args, prepared_evaluation=None, **kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        assert prepared_evaluation is prepared
        return None, "no_signal"

    async def fake_observer(position, received, mode):
        assert received is prepared
        assert mode == "daily"
        observed_ids.append(position.id)
        return {"shadow_evaluated": 1}

    monkeypatch.setattr(
        "app.services.tracked_positions.jobs.refresh_entry_if_waiting",
        fake_refresh,
    )
    monkeypatch.setattr(
        "app.services.tracked_positions.jobs.prepare_alert_evaluation",
        fake_prepare,
    )
    monkeypatch.setattr(
        "app.services.tracked_positions.jobs.create_alert_if_needed",
        fake_legacy,
    )
    async with app.state.db.session() as session:
        result = await daily_tracked_position_alerts_job(
            session,
            settings,
            post_evaluation_observer=fake_observer,
        )
    assert prepared_calls == result["positions_checked"]
    assert legacy_calls == result["positions_checked"]
    assert len(observed_ids) == result["positions_checked"]
    assert result["shadow_evaluated"] == result["positions_checked"]
    assert result["owner_risk_blocked_add_positions"] >= 0
    assert result["owner_risk_blocked_adds"] == 0
    assert result["liquidity_capacity_unavailable"] == 0
    assert result["liquidity_stressed_exits"] == 0
