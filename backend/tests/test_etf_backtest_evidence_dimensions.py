from __future__ import annotations

from datetime import date, datetime

from app.services.etf_research_evidence import stable_contract_hash
from app.services.short_research.backtest import backtest_summary_payload


def _run(*, execution_model: str, config: dict, coverage: dict, metrics: dict):
    replay_contract = {
        "replay_contract_version": "etf_replay_contract_v2",
        "execution_model": execution_model,
        "action_lifecycle_version": config.get("action_lifecycle_version"),
        "target_semantics": config.get("target_semantics"),
        "action_event_source": config.get("action_event_source"),
    }
    replay_contract["contract_hash"] = stable_contract_hash(replay_contract)
    return type(
        "Run",
        (),
        {
            "id": 1,
            "status": "success",
            "started_at": datetime(2026, 7, 14, 15, 0),
            "finished_at": datetime(2026, 7, 14, 15, 1),
            "start_date": date(2026, 1, 1),
            "end_date": date(2026, 7, 14),
            "initial_cash": 100_000.0,
            "fee_rate": 0.001,
            "metrics_json": metrics,
            "benchmark_json": {},
            "data_coverage_json": coverage,
            "caveats_json": [],
            "config_json": {**config, "replay_contract": replay_contract},
            "error_message": None,
        },
    )()


def test_daily_result_keeps_action_notification_and_time_limits_separate() -> None:
    payload = backtest_summary_payload(
        _run(
            execution_model="daily_close_v1",
            config={"execution": "daily_close", "no_intraday_fill": True},
            coverage={
                "asset_count": 120,
                "priced_asset_count": 118,
                "trading_days": 240,
                "start_date": "2025-07-14",
                "end_date": "2026-07-14",
            },
            metrics={},
        )
    )

    assert payload["action_evidence"] == {
        "scenario_label": "动作建议完全执行情景",
        "status": "legacy_diagnostic",
        "sample_count": None,
        "execution_provenance": "simulated_not_observed",
        "observed_user_execution": False,
        "policy_semantics": "legacy_current_position",
        "research_only": True,
        "promotion_eligible": False,
    }
    assert payload["evidence_status"] == "旧口径结果"
    assert payload["evidence_summary"]["sample_count"] == 0
    assert payload["research_only"] is True
    assert payload["promotion_eligible"] is False
    assert payload["notification_evidence"]["scenario_label"] == "仅 SMTP 已接受邮件被执行敏感性"
    assert payload["notification_evidence"]["smtp_semantics"] == "accepted_not_delivered"
    assert payload["notification_evidence"]["observed_user_execution"] is False
    assert payload["execution_evidence"]["model"] == "daily_close_v1"
    assert payload["execution_evidence"]["base_fill_field"] == "legacy_daily_close"
    assert payload["coverage_evidence"]["priced_asset_count"] == 118
    assert payload["time_resolution_limitations"]["resolution"] == "daily_bars"
    assert payload["time_resolution_limitations"]["intraday_trigger_replayed"] is False
    assert payload["time_resolution_limitations"]["bid_ask_iopv_verified"] is False
    assert any(
        "旧相对仓位动作语义" in note
        for note in payload["time_resolution_limitations"]["notes"]
    )


def test_intraday_result_reports_stored_quote_coverage_without_claiming_delivery() -> None:
    payload = backtest_summary_payload(
        _run(
            execution_model="intraday_alert_v1",
            config={"execution": "intraday_alert", "execution_delay_minutes": 3},
            coverage={
                "asset_count": 100,
                "priced_asset_count": 100,
                "intraday_asset_count": 70,
                "intraday_trade_days": 15,
                "intraday_quote_count": 600,
                "start_date": "2026-06-20",
                "end_date": "2026-07-14",
                "data_limitation_notes": ["只覆盖已保存的盘中快照。"],
            },
            metrics={
                "action_evidence": {"sample_count": 12},
                "notification_evidence": {"sample_count": 8},
            },
        )
    )

    assert payload["action_evidence"]["status"] == "legacy_diagnostic"
    assert payload["action_evidence"]["sample_count"] == 12
    assert payload["notification_evidence"]["status"] == "legacy_diagnostic"
    assert payload["notification_evidence"]["sample_count"] == 8
    assert payload["evidence_status"] == "旧口径结果"
    assert payload["execution_evidence"] == {
        "model": "intraday_alert_v1",
        "label": "盘中历史快照模拟",
        "base_fill_field": "stored_intraday_quote_after_fixed_delay",
        "execution_delay_minutes": 3,
        "execution_provenance": "simulated_not_observed",
        "observed_user_execution": False,
    }
    assert payload["coverage_evidence"]["intraday_quote_count"] == 600
    assert payload["time_resolution_limitations"]["resolution"] == "stored_intraday_snapshots"
    assert payload["time_resolution_limitations"]["intraday_trigger_replayed"] is True
    assert payload["time_resolution_limitations"]["smtp_delivery_verified"] is False
    assert payload["time_resolution_limitations"]["notes"] == [
        "只覆盖已保存的盘中快照。",
        "旧相对仓位动作语义仅供诊断，不可作为当前 v2 动作策略证据。",
    ]


def test_current_action_contract_uses_real_sample_count_without_synthetic_minimum() -> None:
    payload = backtest_summary_payload(
        _run(
            execution_model="daily_close_v1",
            config={
                "execution": "daily_close",
                "action_lifecycle_version": "etf_exit_action_v2",
                "target_semantics": "absolute_exposure_baseline",
                "action_event_source": "unique_action_decision",
            },
            coverage={},
            metrics={"action_evidence": {"sample_count": 3}},
        )
    )

    assert payload["evidence_status"] == "同源已验证"
    assert payload["evidence_summary"]["sample_count"] == 3
    assert payload["evidence_summary"]["evidence_status"] == "样本不足"
    assert payload["action_evidence"]["status"] == "available"
    assert payload["action_evidence"]["policy_semantics"] == "absolute_exposure_baseline"


def test_current_action_contract_rejects_a_tampered_contract_hash() -> None:
    run = _run(
        execution_model="daily_close_v1",
        config={
            "execution": "daily_close",
            "action_lifecycle_version": "etf_exit_action_v2",
            "target_semantics": "absolute_exposure_baseline",
            "action_event_source": "unique_action_decision",
        },
        coverage={},
        metrics={"action_evidence": {"sample_count": 20}},
    )
    run.config_json["replay_contract"]["contract_hash"] = "tampered"

    payload = backtest_summary_payload(run)

    assert payload["evidence_status"] == "旧口径结果"
    assert payload["action_evidence"]["status"] == "legacy_diagnostic"
