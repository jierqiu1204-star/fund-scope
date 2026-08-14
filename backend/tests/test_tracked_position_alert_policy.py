"""API contract tests for selecting tracked-position email policies."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, select

from app.models.entities import (
    EtfPriceHistory,
    LateDayTurnaroundObservation,
    LateDayTurnaroundRun,
    ShortResearchSignalRun,
    TrackedPosition,
    TrackedPositionAlert,
    TrackedPositionAlertAudit,
    TradableEtf,
)
from app.services.strategy_lab.late_day_turnaround_shadow import CONTRACT_HASH
from app.services.tracked_positions.alert_policy import (
    apply_alert_policy_selection,
)


@pytest.mark.asyncio
async def test_default_tracking_policy_is_backward_compatible(client) -> None:
    response = await client.post(
        "/api/tracked-positions",
        json={"asset_type": "fund", "asset_code": "270042"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["alert_policy_id"] == "standard_dynamic_v2"
    assert body["alert_policy_version"] == "standard_dynamic_v2"
    assert body["alert_policy_provenance"] == "default"
    assert body["source_strategy"] is None


@pytest.mark.asyncio
async def test_tail_policy_is_etf_only(client) -> None:
    response = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "fund",
            "asset_code": "270042",
            "alert_policy_id": "late_day_turnaround_t1_v1",
        },
    )

    assert response.status_code == 422
    assert "只支持 ETF" in response.text


@pytest.mark.asyncio
async def test_etf_tracking_can_select_and_reset_tail_policy(client, app) -> None:
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="513521",
                name="测试尾盘ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="T+1",
                asset_class="ETF",
            )
        )
        session.add(
            EtfPriceHistory(
                etf_code="513521",
                trade_date=date(2026, 8, 13),
                open=1.00,
                high=1.02,
                low=0.99,
                close=1.01,
                volume=1_000_000,
                turnover=1_010_000,
                pct_change=1.0,
            )
        )
        await session.commit()

    created = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "etf",
            "asset_code": "513521",
            "buy_date": "2026-08-13",
            "confirmed_nav": 1.01,
            "buy_amount": 3000,
            "alert_policy_id": "late_day_turnaround_t1_v1",
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["alert_policy_id"] == "late_day_turnaround_t1_v1"
    assert body["alert_policy_version"] == "late_day_turnaround_t1_v1"
    assert body["alert_policy_provenance"] == "manual_selection"
    assert body["source_strategy"] == "late_day_turnaround"
    assert body["source_manifest_hash"] is None

    reset = await client.patch(
        f"/api/tracked-positions/{body['id']}",
        json={
            "alert_policy_id": "standard_dynamic_v2",
            "expected_exit_state_version": body["exit_state_version"],
        },
    )

    assert reset.status_code == 200
    reset_body = reset.json()
    assert reset_body["alert_policy_id"] == "standard_dynamic_v2"
    assert reset_body["alert_policy_provenance"] == "default"
    assert reset_body["source_strategy"] is None
    assert reset_body["exit_state_version"] == body["exit_state_version"] + 1
    async with app.state.db.session() as session:
        audit = await session.scalar(
            select(TrackedPositionAlertAudit)
            .where(
                TrackedPositionAlertAudit.tracked_position_id == body["id"],
                TrackedPositionAlertAudit.outcome == "policy_changed",
            )
            .order_by(TrackedPositionAlertAudit.id.desc())
        )

    assert audit is not None
    assert audit.trigger_label == "standard_dynamic_v2"

    async with app.state.db.session() as session:
        position = await session.get(TrackedPosition, body["id"])
        assert position is not None
        with pytest.raises(LookupError, match="owner"):
            await apply_alert_policy_selection(
                session,
                position=position,
                owner_id=999,
                policy_id="standard_dynamic_v2",
                source_manifest_hash=None,
                source_decision_at=None,
            )

@pytest.mark.asyncio
async def test_candidate_provenance_must_match_persisted_etf_evidence(
    client, app
) -> None:
    manifest_hash = "m" * 64
    decision_at = "2026-08-13T14:30:00+08:00"
    async with app.state.db.session() as session:
        session.add(
            TradableEtf(
                code="513522",
                name="候选证据ETF",
                exchange="SH",
                theme_tags_json=["测试"],
                trading_rule_label="T+1",
                asset_class="ETF",
            )
        )
        session.add(
            EtfPriceHistory(
                etf_code="513522",
                trade_date=date(2026, 8, 13),
                open=1.00,
                high=1.03,
                low=0.99,
                close=1.02,
                volume=1_000_000,
                turnover=1_020_000,
                pct_change=2.0,
            )
        )
        run = LateDayTurnaroundRun(
            manifest_hash=manifest_hash,
            universe="etf",
            signal_date=date(2026, 8, 13),
            decision_at=datetime(2026, 8, 13, 14, 30),
            status="complete",
            policy_mode="shadow_only",
            strategy_version="late_day_turnaround_v2",
            contract_hash=CONTRACT_HASH,
            universe_hash="u" * 64,
            input_hash="i" * 64,
            expected_count=1,
            evaluated_count=1,
            available_count=1,
            qualifying_count=1,
            provider_health_json={"status": "healthy"},
            exclusion_counts_json={},
        )
        session.add(run)
        await session.flush()
        session.add(
            LateDayTurnaroundObservation(
                run_id=run.id,
                asset_code="513522",
                asset_name="候选证据ETF",
                observation_kind="formal_candidate",
                available=True,
                reason="ok",
                score=88.0,
                ma5=1.0,
                gain_pct=2.0,
                ma_deviation_pct=2.0,
                amount_ratio=1.2,
                signal_date=date(2026, 8, 13),
                decision_at=datetime(2026, 8, 13, 14, 30),
                input_hash="o" * 64,
                provenance_json={"contract_hash": CONTRACT_HASH},
            )
        )
        await session.commit()

    created = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "etf",
            "asset_code": "513522",
            "buy_date": "2026-08-13",
            "confirmed_nav": 1.02,
            "alert_policy_id": "late_day_turnaround_t1_v1",
            "source_manifest_hash": manifest_hash,
            "source_decision_at": decision_at,
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["alert_policy_provenance"] == "candidate_backed"
    assert body["source_manifest_hash"] == manifest_hash
    assert body["source_decision_at"] == "2026-08-13T14:30:00"

    mismatch = await client.post(
        "/api/tracked-positions",
        json={
            "asset_type": "etf",
            "asset_code": "513522",
            "buy_date": "2026-08-13",
            "confirmed_nav": 1.02,
            "alert_policy_id": "late_day_turnaround_t1_v1",
            "source_manifest_hash": manifest_hash,
            "source_decision_at": "2026-08-13T14:40:00+08:00",
        },
    )
    assert mismatch.status_code == 422

    async with app.state.db.session() as session:
        persisted_run = await session.scalar(
            select(LateDayTurnaroundRun).where(
                LateDayTurnaroundRun.manifest_hash == manifest_hash
            )
        )
        ranking_count = await session.scalar(select(func.count(ShortResearchSignalRun.id)))
        alert_count = await session.scalar(select(func.count(TrackedPositionAlert.id)))
    assert persisted_run is not None
    assert persisted_run.status == "complete"
    assert persisted_run.qualifying_count == 1
    assert ranking_count == 0
    assert alert_count == 0
