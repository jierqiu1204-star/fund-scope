from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

CANONICAL_RESEARCH_ELIGIBILITY_POLICY_VERSION = "etf_canonical_research_eligibility_v1"
OBSERVATION_ONLY_STATE_CONTRACT_VERSION = "etf_observation_only_state_v1"
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
    eligible: bool
    policy_version: str
    reasons: tuple[str, ...]


def canonical_research_eligibility_policy() -> dict[str, Any]:
    return {
        "policy_version": CANONICAL_RESEARCH_ELIGIBILITY_POLICY_VERSION,
        "minimum_eligible_adjusted_sessions": MIN_CANONICAL_ELIGIBLE_SESSIONS,
        "price_basis": CANONICAL_PRICE_BASIS,
        "minimum_average_turnover_20d": MIN_CANONICAL_AVERAGE_TURNOVER_20D,
        "compatible_taxonomy_buckets": sorted(COMPATIBLE_TAXONOMY_BUCKETS),
        "unknown_taxonomy_behavior": "observation_only",
        "failed_quality_behavior": "observation_only",
        "observation_only_state_contract_version": (
            OBSERVATION_ONLY_STATE_CONTRACT_VERSION
        ),
        "stable_exclusion_reason_semantics": "etf_quality_exclusion_reasons_v1",
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
    reasons: list[str] = []
    if eligible_sessions < MIN_CANONICAL_ELIGIBLE_SESSIONS:
        reasons.append("history:insufficient_61_eligible_adjusted_sessions")
    if price_basis != CANONICAL_PRICE_BASIS:
        reasons.append("price_basis:decision_ineligible")
    if not decision_data_eligible:
        reasons.append("decision_data:ineligible")
    if not finite_adjusted_inputs:
        reasons.append("adjusted_inputs:missing_or_non_finite")
    turnover = (
        float(average_turnover_20d)
        if isinstance(average_turnover_20d, int | float)
        and not isinstance(average_turnover_20d, bool)
        and math.isfinite(float(average_turnover_20d))
        else None
    )
    if turnover is None or turnover < MIN_CANONICAL_AVERAGE_TURNOVER_20D:
        reasons.append("absolute_tradability_below_threshold")
    bucket = str(taxonomy_bucket or "")
    if not taxonomy_evidence_valid or bucket not in COMPATIBLE_TAXONOMY_BUCKETS:
        reasons.append("taxonomy_bucket_unresolved")
    if not default_display_eligible and "absolute_tradability_below_threshold" not in reasons:
        reasons.append("canonical_quality_gate_failed")
    unique = tuple(sorted(set(reasons)))
    return CanonicalResearchEligibility(
        eligible=not unique,
        policy_version=CANONICAL_RESEARCH_ELIGIBILITY_POLICY_VERSION,
        reasons=unique,
    )
