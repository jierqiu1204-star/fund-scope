"""Research-only entry-quality diagnostics for ETF leader breakouts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

ENTRY_QUALITY_CONTRACT_VERSION = "etf_leader_entry_quality_shadow_v2"
MAX_PIVOT_EXTENSION_PCT = 0.05
MAX_OVEREXTENSION_ATR20 = 1.50

EntryQualityState = Literal[
    "unavailable",
    "pre_breakout",
    "weak_confirmation",
    "disciplined",
    "overextended",
]
NextSessionConfirmationState = Literal[
    "unavailable",
    "pending",
    "failed",
    "confirmed",
    "overextended",
]


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class LeaderEntryQualityDiagnostic:
    state: EntryQualityState
    pivot_extension_pct: float | None
    overextension_atr20: float | None
    volume_confirmed: bool | None
    reason_codes: tuple[str, ...]

    def component_payload(self) -> dict[str, str | float | bool | None]:
        entry_status = {
            "unavailable": "unavailable",
            "disciplined": "watch",
            "overextended": "overextended",
            "pre_breakout": "invalidated",
            "weak_confirmation": "invalidated",
        }[self.state]
        return {
            "entry_quality_contract_version": ENTRY_QUALITY_CONTRACT_VERSION,
            "entry_quality_state": self.state,
            "entry_quality_pivot_extension_pct": self.pivot_extension_pct,
            "entry_quality_overextension_atr20": self.overextension_atr20,
            "entry_quality_volume_confirmed": self.volume_confirmed,
            "entry_quality_reason_codes": ";".join(self.reason_codes),
            "entry_status": entry_status,
            "entry_status_reason": (
                "awaiting_next_session_confirmation"
                if entry_status == "watch"
                else self.reason_codes[0]
                if self.reason_codes
                else "entry_quality_unavailable"
            ),
        }


@dataclass(frozen=True)
class LeaderNextSessionConfirmation:
    state: NextSessionConfirmationState
    confirmation_return_pct: float | None
    confirmation_close_vs_signal_high_pct: float | None
    pivot_extension_pct: float | None
    overextension_atr20: float | None
    reason_codes: tuple[str, ...]

    def component_payload(self) -> dict[str, str | float | None]:
        return {
            "next_session_confirmation_state": self.state,
            "next_session_confirmation_granularity": "daily_close_proxy",
            "next_session_confirmation_return_pct": self.confirmation_return_pct,
            "next_session_confirmation_close_vs_signal_high_pct": (
                self.confirmation_close_vs_signal_high_pct
            ),
            "next_session_confirmation_pivot_extension_pct": self.pivot_extension_pct,
            "next_session_confirmation_overextension_atr20": (
                self.overextension_atr20
            ),
            "next_session_confirmation_reason_codes": ";".join(self.reason_codes),
        }


def assess_leader_breakout_entry_quality(
    *,
    adjusted_close: object,
    preceding_adjusted_high: object,
    adjusted_ma20: object,
    adjusted_atr20: object,
    volume_confirmed: bool | None,
) -> LeaderEntryQualityDiagnostic:
    """Classify breakout quality without changing candidate eligibility."""

    close = _finite(adjusted_close)
    pivot = _finite(preceding_adjusted_high)
    ma20 = _finite(adjusted_ma20)
    atr20 = _finite(adjusted_atr20)
    unavailable: list[str] = []
    if close is None or close <= 0:
        unavailable.append("adjusted_close_unavailable")
    if pivot is None or pivot <= 0:
        unavailable.append("breakout_pivot_unavailable")
    if ma20 is None or ma20 <= 0:
        unavailable.append("adjusted_ma20_unavailable")
    if atr20 is None or atr20 <= 0:
        unavailable.append("adjusted_atr20_unavailable")
    if volume_confirmed is None:
        unavailable.append("volume_confirmation_unavailable")
    if unavailable:
        return LeaderEntryQualityDiagnostic(
            state="unavailable",
            pivot_extension_pct=None,
            overextension_atr20=None,
            volume_confirmed=volume_confirmed,
            reason_codes=tuple(unavailable),
        )

    assert close is not None
    assert pivot is not None
    assert ma20 is not None
    assert atr20 is not None
    pivot_extension = close / pivot - 1.0
    overextension = abs(close - ma20) / atr20
    reasons: list[str] = []
    if pivot_extension <= 0:
        reasons.append("price_breakout_not_confirmed")
        state: EntryQualityState = "pre_breakout"
    else:
        if pivot_extension > MAX_PIVOT_EXTENSION_PCT:
            reasons.append("pivot_buy_zone_exceeded")
        if overextension > MAX_OVEREXTENSION_ATR20:
            reasons.append("atr_extension_excessive")
        if reasons:
            state = "overextended"
        elif not volume_confirmed:
            reasons.append("volume_confirmation_failed")
            state = "weak_confirmation"
        else:
            state = "disciplined"
    return LeaderEntryQualityDiagnostic(
        state=state,
        pivot_extension_pct=pivot_extension,
        overextension_atr20=overextension,
        volume_confirmed=volume_confirmed,
        reason_codes=tuple(reasons),
    )


def assess_leader_next_session_confirmation(
    *,
    signal_quality_state: object,
    signal_adjusted_close: object,
    signal_adjusted_high: object,
    preceding_adjusted_high: object,
    confirmation_adjusted_close: object | None,
    confirmation_adjusted_ma5: object | None,
    confirmation_adjusted_ma20: object | None,
    confirmation_adjusted_atr20: object | None,
) -> LeaderNextSessionConfirmation:
    """Confirm a signal at the next daily close without claiming intraday evidence."""

    if signal_quality_state == "overextended":
        return LeaderNextSessionConfirmation(
            state="overextended",
            confirmation_return_pct=None,
            confirmation_close_vs_signal_high_pct=None,
            pivot_extension_pct=None,
            overextension_atr20=None,
            reason_codes=("signal_day_overextended",),
        )
    if signal_quality_state in {"pre_breakout", "weak_confirmation"}:
        return LeaderNextSessionConfirmation(
            state="failed",
            confirmation_return_pct=None,
            confirmation_close_vs_signal_high_pct=None,
            pivot_extension_pct=None,
            overextension_atr20=None,
            reason_codes=("signal_day_not_confirmed",),
        )
    if signal_quality_state != "disciplined":
        return LeaderNextSessionConfirmation(
            state="unavailable",
            confirmation_return_pct=None,
            confirmation_close_vs_signal_high_pct=None,
            pivot_extension_pct=None,
            overextension_atr20=None,
            reason_codes=("signal_quality_unavailable",),
        )

    confirmation_values = (
        confirmation_adjusted_close,
        confirmation_adjusted_ma5,
        confirmation_adjusted_ma20,
        confirmation_adjusted_atr20,
    )
    if all(value is None for value in confirmation_values):
        return LeaderNextSessionConfirmation(
            state="pending",
            confirmation_return_pct=None,
            confirmation_close_vs_signal_high_pct=None,
            pivot_extension_pct=None,
            overextension_atr20=None,
            reason_codes=("next_session_not_observed",),
        )

    signal_close = _finite(signal_adjusted_close)
    signal_high = _finite(signal_adjusted_high)
    pivot = _finite(preceding_adjusted_high)
    confirmation_close = _finite(confirmation_adjusted_close)
    confirmation_ma5 = _finite(confirmation_adjusted_ma5)
    confirmation_ma20 = _finite(confirmation_adjusted_ma20)
    confirmation_atr20 = _finite(confirmation_adjusted_atr20)
    unavailable: list[str] = []
    for label, value in (
        ("signal_adjusted_close_unavailable", signal_close),
        ("signal_adjusted_high_unavailable", signal_high),
        ("breakout_pivot_unavailable", pivot),
        ("confirmation_adjusted_close_unavailable", confirmation_close),
        ("confirmation_adjusted_ma5_unavailable", confirmation_ma5),
        ("confirmation_adjusted_ma20_unavailable", confirmation_ma20),
        ("confirmation_adjusted_atr20_unavailable", confirmation_atr20),
    ):
        if value is None or value <= 0:
            unavailable.append(label)
    if unavailable:
        return LeaderNextSessionConfirmation(
            state="unavailable",
            confirmation_return_pct=None,
            confirmation_close_vs_signal_high_pct=None,
            pivot_extension_pct=None,
            overextension_atr20=None,
            reason_codes=tuple(unavailable),
        )

    assert signal_close is not None
    assert signal_high is not None
    assert pivot is not None
    assert confirmation_close is not None
    assert confirmation_ma5 is not None
    assert confirmation_ma20 is not None
    assert confirmation_atr20 is not None
    confirmation_return = confirmation_close / signal_close - 1.0
    close_vs_signal_high = confirmation_close / signal_high - 1.0
    pivot_extension = confirmation_close / pivot - 1.0
    overextension = abs(confirmation_close - confirmation_ma20) / confirmation_atr20
    reasons: list[str] = []
    if confirmation_return <= 0:
        reasons.append("next_session_not_higher")
    if close_vs_signal_high <= 0:
        reasons.append("signal_day_high_not_reclaimed")
    if confirmation_close <= confirmation_ma5:
        reasons.append("confirmation_close_below_ma5")
    if pivot_extension > MAX_PIVOT_EXTENSION_PCT:
        reasons.append("confirmation_pivot_buy_zone_exceeded")
    if overextension > MAX_OVEREXTENSION_ATR20:
        reasons.append("confirmation_atr_extension_excessive")
    if (
        "confirmation_pivot_buy_zone_exceeded" in reasons
        or "confirmation_atr_extension_excessive" in reasons
    ):
        state: NextSessionConfirmationState = "overextended"
    elif reasons:
        state = "failed"
    else:
        state = "confirmed"
    return LeaderNextSessionConfirmation(
        state=state,
        confirmation_return_pct=confirmation_return,
        confirmation_close_vs_signal_high_pct=close_vs_signal_high,
        pivot_extension_pct=pivot_extension,
        overextension_atr20=overextension,
        reason_codes=tuple(reasons),
    )
