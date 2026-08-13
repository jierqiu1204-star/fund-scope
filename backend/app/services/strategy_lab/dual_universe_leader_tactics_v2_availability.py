"""Stable, machine-readable availability reasons for V2 research evidence.

The research surface must distinguish an empty result from evidence that is not
available yet.  Keep these identifiers stable so the API and UI can explain a
gate without inventing a fallback value or silently changing the denominator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

V2_UNAVAILABLE_REASONS = frozenset(
    {
        "leader_tactics_v2_api_disabled",
        "leader_tactics_v2_invalid_filter",
        "leader_tactics_v2_not_materialized",
        "leader_tactics_v2_empty_materialization",
        "insufficient_data",
        "ashare_research_tables_not_materialized",
        "ashare_universe_not_materialized",
        "missing_pit_universe",
        "missing_pit_theme_membership",
        "missing_pit_peer_group",
        "universe_listing_excluded",
        "membership_effective_after_signal",
        "membership_expired_before_signal",
        "missing_membership_fact_hash",
        "membership_fact_hash_mismatch",
        "membership_received_after_cutoff",
        "taxonomy_not_point_in_time",
        "missing_adjusted_history",
        "insufficient_adjusted_history",
        "adjusted_history_not_canonical",
        "adjusted_history_not_current_to_signal",
        "adjusted_bar_received_after_cutoff",
        "adjusted_history_received_after_cutoff",
        "wrong_price_basis",
        "invalid_adjusted_ohlcv",
        "missing_amount_for_core_rank",
        "adjusted_bar_not_decision_eligible",
        "non_finite_adjusted_input",
        "forbidden_raw_price_provider",
        "raw_or_audit_only_provider",
        "unsupported_adjusted_provider",
        "missing_adjusted_provenance",
        "future_known_input",
        "insufficient_history",
        "insufficient_volume_history",
        "insufficient_peer_count",
        "batch_breadth_gate_failed",
        "clone_not_representative",
        "universe_unsupported",
        "missing_asset_code",
        "hot_theme_gate_failed",
        "core_leader_gate_failed",
        "ma_alignment_failed",
        "volume_peak_gate_failed",
        "price_breakout_gate_failed",
        "ma5_ma10_cross_missing",
        "ma20_position_failed",
        "ma20_slope_failed",
        "base_atr_compression_failed",
        "base_overextension_gate_failed",
        "prior_leadership_unavailable",
        "prior_leadership_gate_failed",
        "drawdown_band_failed",
        "positive_stabilization_failed",
        "range_compression_failed",
        "overextension_gate_failed",
        "missing_transition_evidence",
        "future_window_pending",
        "insufficient_common_support_for_v2_etf_primary",
        "insufficient_factual_pit_sessions",
        "insufficient_non_overlapping_primary_dates",
        "insufficient_walk_forward_folds",
        "primary_bootstrap_lower_bound_not_above_zero",
        "holdout_not_successfully_used_once",
        "coverage_below_95_percent",
        "non_finite_input_violations",
        "raw_price_violations",
        "clone_policy_violations",
        "missing_drawdown_evidence",
        "missing_stability_diagnostics",
        "candidate_identity_not_frozen",
        "primary_endpoint_substituted",
        "purge_contract_incompatible",
        "embargo_contract_incompatible",
        "holm_adjustment_missing",
        "drawdown_degradation_exceeds_two_percent",
        "economic_validation_not_materialized",
        "v2_candidate_not_materialized",
        "locked_case_unavailable",
        "locked_case_wrong_cutoff",
        "sentiment_risk_contract_missing",
        "sentiment_risk_pit_input_invalid",
        "sentiment_risk_non_finite_input",
        "sentiment_risk_insufficient_hot_themes",
        "sentiment_risk_insufficient_leaders",
        "sentiment_risk_insufficient_middle_tier",
    }
)


@dataclass(frozen=True)
class V2Availability:
    """One gate's exact support count and stable unavailable reason."""

    numerator: int
    denominator: int
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.numerator < 0 or self.denominator < 0 or self.numerator > self.denominator:
            raise ValueError("availability counts must satisfy 0 <= numerator <= denominator")
        if (
            self.unavailable_reason is not None
            and self.unavailable_reason not in V2_UNAVAILABLE_REASONS
        ):
            raise ValueError(f"unknown V2 unavailable reason: {self.unavailable_reason}")

    @property
    def coverage(self) -> float:
        return self.numerator / self.denominator if self.denominator else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "coverage": self.coverage,
            "unavailable_reason": self.unavailable_reason,
        }


def validate_v2_unavailable_reason(reason: str | None) -> str | None:
    if reason is not None and reason not in V2_UNAVAILABLE_REASONS:
        raise ValueError(f"unknown V2 unavailable reason: {reason}")
    return reason


def v2_availability(
    numerator: int,
    denominator: int,
    *,
    reason: str | None = None,
) -> V2Availability:
    return V2Availability(numerator, denominator, validate_v2_unavailable_reason(reason))


__all__ = [
    "V2Availability",
    "V2_UNAVAILABLE_REASONS",
    "v2_availability",
    "validate_v2_unavailable_reason",
]
