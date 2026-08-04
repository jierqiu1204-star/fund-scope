from __future__ import annotations

from datetime import datetime

import pytest

from app.services.strategy_lab.dual_universe_leader_tactics_v2_availability import (
    V2_UNAVAILABLE_REASONS,
    v2_availability,
    validate_v2_unavailable_reason,
)


def test_availability_preserves_exact_counts_and_stable_reason() -> None:
    gate = v2_availability(9, 10, reason="insufficient_history")
    assert gate.to_dict() == {
        "numerator": 9,
        "denominator": 10,
        "coverage": 0.9,
        "unavailable_reason": "insufficient_history",
    }


def test_availability_rejects_unknown_reason_or_invalid_denominator() -> None:
    with pytest.raises(ValueError, match="unknown V2 unavailable reason"):
        validate_v2_unavailable_reason("made_up_reason")
    with pytest.raises(ValueError, match="availability counts"):
        v2_availability(2, 1)


def test_public_gate_reasons_are_explicit_and_not_datetime_objects() -> None:
    assert "future_known_input" in V2_UNAVAILABLE_REASONS
    assert "holdout_not_successfully_used_once" in V2_UNAVAILABLE_REASONS
    assert all(isinstance(reason, str) for reason in V2_UNAVAILABLE_REASONS)
    assert isinstance(datetime(2026, 8, 4).isoformat(), str)
