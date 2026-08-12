from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

CANONICAL_RESEARCH_ELIGIBILITY_POLICY_VERSION = "etf_canonical_research_eligibility_v2"
OBSERVATION_ONLY_STATE_CONTRACT_VERSION = "etf_observation_only_state_v2"
MIN_CANONICAL_AVERAGE_TURNOVER_20D = 50_000_000.0
MIN_CANONICAL_ELIGIBLE_SESSIONS = 61
CANONICAL_PRICE_BASIS = "total_return_adjusted"
COMPATIBLE_TAXONOMY_BUCKETS = frozenset(
    {
        "broad-equity",
        "sector/theme-equity",
        "fixed-income",
        "commodity",
        "cross-border",
    }
)


@dataclass(frozen=True)
class CanonicalResearchEligibility:
    policy_version: str
    score_eligible: bool
    research_eligible: bool
    action_quality_eligible: bool
    score_reasons: tuple[str, ...]
    quality_reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        """Compatibility alias for callers that still mean action-quality eligible."""
        return self.action_quality_eligible

    @property
    def reasons(self) -> tuple[str, ...]:
        """Compatibility view of all fail-closed reasons."""
        return tuple(sorted({*self.score_reasons, *self.quality_reasons}))


def canonical_research_eligibility_policy() -> dict[str, Any]:
    return {
        "policy_version": CANONICAL_RESEARCH_ELIGIBILITY_POLICY_VERSION,
        "minimum_eligible_adjusted_sessions": MIN_CANONICAL_ELIGIBLE_SESSIONS,
        "price_basis": CANONICAL_PRICE_BASIS,
        "minimum_average_turnover_20d": MIN_CANONICAL_AVERAGE_TURNOVER_20D,
        "compatible_taxonomy_buckets": sorted(COMPATIBLE_TAXONOMY_BUCKETS),
        "unknown_taxonomy_behavior": "observation_only",
        "failed_quality_behavior": "observation_only",
        "eligibility_layers": {
            "score": "finite_contract_compatible_adjusted_daily_inputs",
            "research": "same_as_score_eligibility",
            "action_quality": "research_eligible_and_quality_gates_pass",
        },
        "observation_only_state_contract_version": (OBSERVATION_ONLY_STATE_CONTRACT_VERSION),
        "stable_exclusion_reason_semantics": "etf_quality_exclusion_reasons_v2",
        "raw_stale_estimated_or_display_only_fallback": "forbidden",
    }


def evaluate_canonical_research_eligibility(
    *,
    eligible_sessions: int,
    price_basis: str,
    decision_data_eligible: bool,
    finite_adjusted_inputs: bool,
    average_turnover_20d: object,
    taxonomy_bucket: object,
    taxonomy_evidence_valid: bool,
    default_display_eligible: bool,
) -> CanonicalResearchEligibility:
    score_reasons: list[str] = []
    quality_reasons: list[str] = []
    if eligible_sessions < MIN_CANONICAL_ELIGIBLE_SESSIONS:
        score_reasons.append("history:insufficient_61_eligible_adjusted_sessions")
    if price_basis != CANONICAL_PRICE_BASIS:
        score_reasons.append("price_basis:decision_ineligible")
    if not decision_data_eligible:
        score_reasons.append("decision_data:ineligible")
    if not finite_adjusted_inputs:
        score_reasons.append("adjusted_inputs:missing_or_non_finite")
    turnover = (
        float(average_turnover_20d)
        if isinstance(average_turnover_20d, int | float)
        and not isinstance(average_turnover_20d, bool)
        and math.isfinite(float(average_turnover_20d))
        else None
    )
    if turnover is None or turnover < MIN_CANONICAL_AVERAGE_TURNOVER_20D:
        quality_reasons.append("absolute_tradability_below_threshold")
    bucket = str(taxonomy_bucket or "")
    if not taxonomy_evidence_valid or bucket not in COMPATIBLE_TAXONOMY_BUCKETS:
        quality_reasons.append("taxonomy_bucket_unresolved")
    if (
        not default_display_eligible
        and "absolute_tradability_below_threshold" not in quality_reasons
    ):
        quality_reasons.append("canonical_quality_gate_failed")
    score_unique = tuple(sorted(set(score_reasons)))
    quality_unique = tuple(sorted(set(quality_reasons)))
    return CanonicalResearchEligibility(
        policy_version=CANONICAL_RESEARCH_ELIGIBILITY_POLICY_VERSION,
        score_eligible=not score_unique,
        research_eligible=not score_unique,
        action_quality_eligible=not score_unique and not quality_unique,
        score_reasons=score_unique,
        quality_reasons=quality_unique,
    )
