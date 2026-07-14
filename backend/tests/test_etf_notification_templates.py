from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.notifier import NotificationContractError, Notifier


def _action_payload() -> dict[str, object]:
    return {
        "contract_version": "tracked_position_action_notification_v1",
        "notification_kind": "action",
        "asset_name": "测试ETF",
        "asset_code": "513520",
        "alert_title": "减仓建议待确认",
        "action_id": 42,
        "action_status": "proposed",
        "policy_version": "policy-v1",
        "eligible_data_time": "2026-07-14T15:00:00",
        "cutoff_time": "2026-07-14T15:00:00",
        "data_source": "eastmoney_adjusted",
        "quote_freshness": "close_final",
        "evidence_hash": "a" * 64,
        "target_semantics": "absolute_exposure_baseline",
        "target_stage": "remaining_5000bp",
        "target_remaining_fraction": 0.5,
        "baseline_normalized_quantity": 1000.0,
        "baseline_adjustment_factor": 1.0,
        "baseline_source": "confirmed_shares",
        "target_normalized_quantity": 500.0,
        "automatic_execution": False,
    }


def test_action_template_exposes_cutoff_evidence_absolute_target_and_no_auto_execution() -> None:
    template_name, template_version, subject, body = Notifier().render_tracked_position_envelope(
        [_action_payload()],
        route="urgent_hard_stop",
    )

    assert template_name == "tracked_position_action_v2.html.j2"
    assert template_version == "tracked-position-notification-v2"
    assert subject.startswith("[紧急]")
    for expected in (
        "2026-07-14T15:00:00",
        "eastmoney_adjusted",
        "close_final",
        "policy-v1",
        "a" * 64,
        "动作 #42",
        "proposed",
        "exposure baseline",
        "1000.0",
        "0.5",
        "500.0",
        "等待用户执行/确认",
        "未自动执行",
    ):
        assert expected in body


@pytest.mark.parametrize(
    "missing_field",
    [
        "action_id",
        "policy_version",
        "eligible_data_time",
        "evidence_hash",
        "target_semantics",
    ],
)
def test_action_template_fails_closed_when_contract_field_is_missing(
    missing_field: str,
) -> None:
    payload = deepcopy(_action_payload())
    payload.pop(missing_field)

    with pytest.raises(NotificationContractError, match=missing_field):
        Notifier().render_tracked_position_envelope([payload], route="ordinary_digest")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_semantics", "relative_current_position"),
        ("automatic_execution", True),
        ("data_source", "unknown"),
        ("quote_freshness", "unknown"),
        ("target_normalized_quantity", 499.0),
    ],
)
def test_action_template_rejects_unsafe_or_inconsistent_contract(
    field: str,
    value: object,
) -> None:
    payload = deepcopy(_action_payload())
    payload[field] = value

    with pytest.raises(NotificationContractError):
        Notifier().render_tracked_position_envelope([payload], route="ordinary_digest")


def test_watch_template_is_explicitly_hold_only_and_has_no_action_target() -> None:
    payload = {
        "contract_version": "tracked_position_action_notification_v1",
        "notification_kind": "watch",
        "asset_name": "测试ETF",
        "asset_code": "513520",
        "alert_title": "止盈观察提醒",
        "position_action": "hold",
        "action_class": "soft_watch",
    }

    _, _, subject, body = Notifier().render_tracked_position_envelope(
        [payload], route="ordinary_digest"
    )

    assert "止盈观察提醒" in subject
    assert "hold/仅观察/未生成减仓动作" in body
    assert "目标标准化数量" not in body


def test_data_status_template_never_quotes_old_action_or_price() -> None:
    payload = {
        "contract_version": "tracked_position_action_notification_v1",
        "notification_kind": "data_status",
        "asset_name": "测试ETF",
        "asset_code": "513520",
        "data_state": "data_waiting",
        "reason_code": "adjusted_close_not_final",
        "checked_at": "2026-07-14T15:00:00",
    }

    _, _, _, body = Notifier().render_tracked_position_envelope(
        [payload], route="data_status"
    )

    assert "当前无法复核" in body
    assert "历史动作建议" in body
    assert "当前价格" not in body
