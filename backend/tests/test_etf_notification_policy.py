from __future__ import annotations

from datetime import date

import pytest

from app.services.workflows.tracked_position_notifications import (
    NotificationPolicyContext,
    decide_notification,
    next_trading_repeat_slot,
    trading_repeat_slot,
)


def _context(**changes) -> NotificationPolicyContext:
    values = {
        "trade_session": date(2026, 7, 14),
        "transition": "firing",
        "notification_repeat": False,
        "hard_stop": False,
        "data_state": "eligible",
        "existing_same_slot": False,
        "user_silenced": False,
        "higher_severity_active": False,
    }
    values.update(changes)
    return NotificationPolicyContext(**values)


def test_notification_policy_suppresses_same_slot_silence_and_lower_severity() -> None:
    same_slot = decide_notification(_context(existing_same_slot=True))
    silenced = decide_notification(_context(user_silenced=True))
    inhibited = decide_notification(_context(higher_severity_active=True))

    assert (same_slot.emit, same_slot.reason_code) == (False, "same_repeat_slot")
    assert (silenced.emit, silenced.reason_code) == (False, "user_silenced")
    assert (inhibited.emit, inhibited.reason_code) == (False, "higher_severity_active")


def test_hard_stop_bypasses_digest_but_not_action_identity() -> None:
    decision = decide_notification(_context(hard_stop=True))

    assert decision.emit is True
    assert decision.route == "urgent_hard_stop"
    assert decision.severity == "critical"
    assert decision.action_oriented is True


@pytest.mark.parametrize("data_state", ["data_waiting", "no_data", "error"])
def test_invalid_data_repeat_becomes_current_data_unverifiable(data_state: str) -> None:
    decision = decide_notification(
        _context(
            transition="repeat",
            notification_repeat=True,
            hard_stop=True,
            data_state=data_state,
        )
    )

    assert decision.emit is True
    assert decision.transition == "current_data_unverifiable"
    assert decision.notification_kind == "data_status"
    assert decision.action_oriented is False
    assert decision.route == "data_status"


def test_repeat_slots_follow_exchange_trading_days() -> None:
    friday = date(2026, 7, 17)
    assert trading_repeat_slot(friday) == "2026-07-17:close"
    assert next_trading_repeat_slot(friday) == "2026-07-20:close"
    with pytest.raises(ValueError, match="trading day"):
        trading_repeat_slot(date(2026, 7, 18))
