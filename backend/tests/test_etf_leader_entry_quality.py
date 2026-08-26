from __future__ import annotations

import pytest

from app.services.strategy_lab.etf_leader_entry_quality import (
    ATR_EXTENSION_DIAGNOSTIC_LEVEL,
    MAX_PIVOT_EXTENSION_PCT,
    assess_leader_breakout_entry_quality,
    assess_leader_next_session_confirmation,
)


def test_disciplined_breakout_stays_inside_frozen_buy_zone() -> None:
    result = assess_leader_breakout_entry_quality(
        adjusted_close=103.0,
        preceding_adjusted_high=100.0,
        adjusted_ma20=100.0,
        adjusted_atr20=4.0,
        volume_confirmed=True,
    )

    assert result.state == "disciplined"
    assert result.pivot_extension_pct == pytest.approx(0.03)
    assert result.overextension_atr20 == pytest.approx(0.75)
    assert result.reason_codes == ()


def test_pivot_extension_is_hard_rejection_but_atr_extension_is_diagnostic() -> None:
    above_pivot = assess_leader_breakout_entry_quality(
        adjusted_close=100.0 * (1.0 + MAX_PIVOT_EXTENSION_PCT + 0.001),
        preceding_adjusted_high=100.0,
        adjusted_ma20=100.0,
        adjusted_atr20=10.0,
        volume_confirmed=True,
    )
    extended_from_ma20 = assess_leader_breakout_entry_quality(
        adjusted_close=103.0,
        preceding_adjusted_high=100.0,
        adjusted_ma20=110.0,
        adjusted_atr20=7.0 / (ATR_EXTENSION_DIAGNOSTIC_LEVEL + 0.1),
        volume_confirmed=True,
    )

    assert above_pivot.state == "overextended"
    assert "pivot_buy_zone_exceeded" in above_pivot.reason_codes
    assert extended_from_ma20.state == "disciplined"
    assert extended_from_ma20.overextension_atr20 > ATR_EXTENSION_DIAGNOSTIC_LEVEL
    assert extended_from_ma20.component_payload()[
        "entry_quality_atr_extension_elevated"
    ] is True
    assert extended_from_ma20.reason_codes == ()


def test_missing_atr_fails_closed_and_weak_volume_is_not_actionable_quality() -> None:
    unavailable = assess_leader_breakout_entry_quality(
        adjusted_close=103.0,
        preceding_adjusted_high=100.0,
        adjusted_ma20=100.0,
        adjusted_atr20=0.0,
        volume_confirmed=True,
    )
    weak = assess_leader_breakout_entry_quality(
        adjusted_close=103.0,
        preceding_adjusted_high=100.0,
        adjusted_ma20=100.0,
        adjusted_atr20=4.0,
        volume_confirmed=False,
    )

    assert unavailable.state == "unavailable"
    assert "adjusted_atr20_unavailable" in unavailable.reason_codes
    assert weak.state == "weak_confirmation"
    assert weak.reason_codes == ("volume_confirmation_failed",)


def test_disciplined_signal_waits_for_next_session_confirmation() -> None:
    signal = assess_leader_breakout_entry_quality(
        adjusted_close=102.0,
        preceding_adjusted_high=100.0,
        adjusted_ma20=100.0,
        adjusted_atr20=3.0,
        volume_confirmed=True,
    )

    assert signal.component_payload()["entry_status"] == "watch"
    assert signal.component_payload()["entry_status_reason"] == (
        "awaiting_next_session_confirmation"
    )

    confirmed = assess_leader_next_session_confirmation(
        signal_quality_state=signal.state,
        signal_adjusted_close=102.0,
        signal_adjusted_high=102.4,
        preceding_adjusted_high=100.0,
        confirmation_adjusted_close=103.0,
        confirmation_adjusted_ma5=101.0,
        confirmation_adjusted_ma20=100.5,
        confirmation_adjusted_atr20=3.0,
    )

    assert confirmed.state == "confirmed"
    assert confirmed.reason_codes == ()


def test_next_session_weakness_fails_and_late_confirmation_is_not_chased() -> None:
    failed = assess_leader_next_session_confirmation(
        signal_quality_state="disciplined",
        signal_adjusted_close=102.0,
        signal_adjusted_high=102.4,
        preceding_adjusted_high=100.0,
        confirmation_adjusted_close=101.5,
        confirmation_adjusted_ma5=101.0,
        confirmation_adjusted_ma20=100.5,
        confirmation_adjusted_atr20=3.0,
    )
    overextended = assess_leader_next_session_confirmation(
        signal_quality_state="disciplined",
        signal_adjusted_close=102.0,
        signal_adjusted_high=102.4,
        preceding_adjusted_high=100.0,
        confirmation_adjusted_close=106.0,
        confirmation_adjusted_ma5=102.0,
        confirmation_adjusted_ma20=101.0,
        confirmation_adjusted_atr20=4.0,
    )

    assert failed.state == "failed"
    assert "next_session_not_higher" in failed.reason_codes
    assert "signal_day_high_not_reclaimed" in failed.reason_codes
    assert overextended.state == "overextended"
    assert "confirmation_pivot_buy_zone_exceeded" in overextended.reason_codes


def test_high_atr_extension_does_not_override_strict_next_session_confirmation() -> None:
    confirmed = assess_leader_next_session_confirmation(
        signal_quality_state="disciplined",
        signal_adjusted_close=102.0,
        signal_adjusted_high=102.4,
        preceding_adjusted_high=100.0,
        confirmation_adjusted_close=104.0,
        confirmation_adjusted_ma5=101.0,
        confirmation_adjusted_ma20=100.0,
        confirmation_adjusted_atr20=1.0,
    )

    assert confirmed.overextension_atr20 == pytest.approx(4.0)
    assert confirmed.component_payload()[
        "next_session_confirmation_atr_extension_elevated"
    ] is True
    assert confirmed.state == "confirmed"
    assert confirmed.reason_codes == ()


def test_missing_next_session_is_pending_not_actionable() -> None:
    pending = assess_leader_next_session_confirmation(
        signal_quality_state="disciplined",
        signal_adjusted_close=102.0,
        signal_adjusted_high=102.4,
        preceding_adjusted_high=100.0,
        confirmation_adjusted_close=None,
        confirmation_adjusted_ma5=None,
        confirmation_adjusted_ma20=None,
        confirmation_adjusted_atr20=None,
    )

    assert pending.state == "pending"
    assert pending.reason_codes == ("next_session_not_observed",)
