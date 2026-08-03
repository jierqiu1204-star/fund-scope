from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import cached_property, lru_cache
from typing import Any, Literal

from app.services.short_research.daily_reconstructable import (
    daily_reconstructable_manifest,
)
from app.services.short_research.final_score_v3 import FinalScoreV3Result
from app.services.short_research.ranking_contract import (
    canonical_hash,
    final_score_v3_contract,
)

ACTIONABLE_CONTRACT_ID = "actionable_rank_v1"
ACTIONABLE_SCORE_FIELD = "actionable_score"
ACTIONABLE_POLICY_VERSION = "actionable_eligibility_policy_v1"
PRODUCT_FIELD_POLICY_ID = "etf_action_fields_v1"
DUAL_RANKING_RULE_VERSION = "dual_ranking_surfaces_v1"
MIN_RESEARCH_SESSIONS = 61
MIN_ACTIONABLE_SESSIONS = 120

ActionableFieldStatus = Literal[
    "available",
    "missing",
    "stale",
    "not_applicable",
    "unhealthy",
    "inconsistent",
]
HistoryConfidenceTier = Literal[
    "provisional_short_history",
    "standard_history",
    "full_history_context",
]

MANDATORY_ACTIONABLE_FIELDS = (
    "bid",
    "ask",
    "iopv",
    "premium_discount",
    "provider",
    "provider_health",
    "freshness",
    "provider_consensus",
)


@dataclass(frozen=True)
class ActionableFieldEvidence:
    status: ActionableFieldStatus
    source_time: str | None = None


@dataclass(frozen=True)
class ProductFieldPolicy:
    policy_id: str
    mandatory_fields: tuple[str, ...]
    not_applicable_fields_by_product_type: Mapping[str, tuple[str, ...]]

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "mandatory_fields": list(self.mandatory_fields),
            "not_applicable_fields_by_product_type": {
                key: sorted(value)
                for key, value in sorted(self.not_applicable_fields_by_product_type.items())
            },
            "missing_behavior": "actionable_rank_unavailable",
            "stale_behavior": "actionable_rank_unavailable",
            "unclassified_behavior": "actionable_rank_unavailable",
            "not_applicable_behavior": "accepted_only_when_explicitly_declared",
        }

    @cached_property
    def policy_hash(self) -> str:
        payload = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def permits_not_applicable(self, *, product_type: str, field: str) -> bool:
        return field in self.not_applicable_fields_by_product_type.get(product_type, ())


@lru_cache(maxsize=1)
def actionable_product_field_policy() -> ProductFieldPolicy:
    return ProductFieldPolicy(
        policy_id=PRODUCT_FIELD_POLICY_ID,
        mandatory_fields=MANDATORY_ACTIONABLE_FIELDS,
        not_applicable_fields_by_product_type={},
    )


@dataclass(frozen=True)
class ActionableRankManifest:
    contract_id: str = ACTIONABLE_CONTRACT_ID
    score_field: str = ACTIONABLE_SCORE_FIELD
    eligibility_policy_version: str = ACTIONABLE_POLICY_VERSION

    def canonical_payload(self) -> dict[str, Any]:
        product_policy = actionable_product_field_policy()
        return {
            "contract_id": self.contract_id,
            "score_field": self.score_field,
            "eligibility_policy_version": self.eligibility_policy_version,
            "minimum_eligible_adjusted_sessions": MIN_ACTIONABLE_SESSIONS,
            "score_source": {
                "contract_id": "final_score_v3",
                "score_field": "ranking_score",
                "contract_hash": canonical_hash(final_score_v3_contract()),
                "transform": "identity_no_recalculation",
            },
            "product_field_policy": product_policy.canonical_payload(),
            "product_field_policy_hash": product_policy.policy_hash,
            "required_gates": [
                "finite_complete_final_score_v3",
                "fresh_same_session_mandatory_fields",
                "healthy_provider",
                "consistent_provider_consensus",
                "no_cap_violation",
                "no_non_finite_reject",
                "no_unclassified_missing_field",
            ],
            "forbidden_input_domains": [
                "theme",
                "catalyst",
                "future_outcome",
                "backtest_outcome",
                "position",
                "alert",
                "notification",
                "email",
            ],
            "missing_data_behavior": "actionable_rank_unavailable_no_fallback",
            "distinct_from_contracts": [
                daily_reconstructable_manifest().contract_id,
                "final_score_v3",
            ],
        }

    @cached_property
    def manifest_hash(self) -> str:
        payload = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def matches_identity(
        self,
        *,
        contract_id: str,
        score_field: str,
        manifest_hash: str,
    ) -> bool:
        return (
            contract_id == self.contract_id
            and score_field == self.score_field
            and manifest_hash == self.manifest_hash
        )


@lru_cache(maxsize=1)
def actionable_rank_manifest() -> ActionableRankManifest:
    return ActionableRankManifest()


def history_confidence_tier(eligible_sessions: int) -> HistoryConfidenceTier | None:
    if eligible_sessions < MIN_RESEARCH_SESSIONS:
        return None
    if eligible_sessions < MIN_ACTIONABLE_SESSIONS:
        return "provisional_short_history"
    if eligible_sessions < 250:
        return "standard_history"
    return "full_history_context"


@dataclass(frozen=True)
class ActionableRankResult:
    contract_id: str
    score_field: str
    manifest_hash: str
    eligibility_policy_version: str
    product_field_policy_id: str
    product_field_policy_hash: str
    referenced_score_contract_id: str
    referenced_score_contract_hash: str
    actionable_score: float | None
    actionable_eligible: bool
    history_tier: HistoryConfidenceTier | None
    field_statuses: Mapping[str, str]
    source_times: Mapping[str, str]
    exclusion_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RankDerivedActionDecision:
    allowed: bool
    suppression_reasons: tuple[str, ...]


def validate_rank_derived_action_context(
    metrics: Mapping[str, Any],
    *,
    required_as_of_date: date,
    required_contract_hash: str | None = None,
) -> RankDerivedActionDecision:
    """Fail closed before rank membership can drive allocation or email selection."""

    manifest = actionable_rank_manifest()
    reasons: list[str] = []
    if metrics.get("actionable_contract_id") != manifest.contract_id:
        reasons.append("actionable_contract_id_mismatch")
    expected_hash = required_contract_hash or manifest.manifest_hash
    if metrics.get("actionable_contract_hash") != expected_hash:
        reasons.append("actionable_contract_hash_mismatch")
    if metrics.get("actionable_as_of_date") != required_as_of_date.isoformat():
        reasons.append("actionable_as_of_date_mismatch")
    if metrics.get("actionable_eligible") is not True:
        reasons.append("actionable_rank_ineligible")
    rank = metrics.get("actionable_rank")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank <= 0:
        reasons.append("actionable_rank_missing")
    score = metrics.get("actionable_score")
    if (
        isinstance(score, bool)
        or not isinstance(score, int | float)
        or not math.isfinite(float(score))
    ):
        reasons.append("actionable_score_missing_or_non_finite")
    suppression_reasons = tuple(sorted(set(reasons)))
    return RankDerivedActionDecision(
        allowed=not suppression_reasons,
        suppression_reasons=suppression_reasons,
    )


def evaluate_actionable_rank(
    final_score: FinalScoreV3Result,
    *,
    eligible_sessions: int,
    field_evidence: Mapping[str, ActionableFieldEvidence],
    product_type: str = "etf",
    product_policy: ProductFieldPolicy | None = None,
    cap_violation: bool = False,
    non_finite_reject: bool = False,
) -> ActionableRankResult:
    """Apply action gates while preserving the exact final_score_v3 score."""

    policy = product_policy or actionable_product_field_policy()
    reasons: list[str] = []
    if eligible_sessions < MIN_ACTIONABLE_SESSIONS:
        reasons.append("history:insufficient_120_eligible_adjusted_sessions")

    score = final_score.ranking_score
    if (
        not final_score.score_eligible
        or score is None
        or isinstance(score, bool)
        or not math.isfinite(float(score))
    ):
        reasons.append("final_score_v3:unavailable_or_non_finite")
    for component, value in sorted(final_score.component_scores.items()):
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
            reasons.append(f"final_score_v3.{component}:non_finite")
    for component, missing in sorted(final_score.missing_by_component.items()):
        for reason in sorted(missing):
            reasons.append(f"final_score_v3.{component}:{reason}")

    if cap_violation or final_score.cap_violation:
        reasons.append("final_score_v3:cap_violation")
    if non_finite_reject or final_score.non_finite_reject:
        reasons.append("final_score_v3:non_finite_reject")

    field_statuses: dict[str, str] = {}
    source_times: dict[str, str] = {}
    for field in policy.mandatory_fields:
        evidence = field_evidence.get(field)
        status = evidence.status if evidence is not None else "missing"
        field_statuses[field] = status
        if evidence is not None and evidence.source_time:
            source_times[field] = evidence.source_time
        if status == "available":
            continue
        if status == "not_applicable" and policy.permits_not_applicable(
            product_type=product_type,
            field=field,
        ):
            continue
        if status == "not_applicable":
            reasons.append(f"{field}:not_applicable_not_permitted")
        else:
            reasons.append(f"{field}:{status}")

    exclusion_reasons = tuple(sorted(set(reasons)))
    manifest = actionable_rank_manifest()
    referenced_contract_hash = canonical_hash(final_score_v3_contract())
    eligible = not exclusion_reasons
    return ActionableRankResult(
        contract_id=manifest.contract_id,
        score_field=manifest.score_field,
        manifest_hash=manifest.manifest_hash,
        eligibility_policy_version=manifest.eligibility_policy_version,
        product_field_policy_id=policy.policy_id,
        product_field_policy_hash=policy.policy_hash,
        referenced_score_contract_id="final_score_v3",
        referenced_score_contract_hash=referenced_contract_hash,
        actionable_score=float(score) if eligible and score is not None else None,
        actionable_eligible=eligible,
        history_tier=history_confidence_tier(eligible_sessions),
        field_statuses=field_statuses,
        source_times=source_times,
        exclusion_reasons=exclusion_reasons,
    )
