"""Frozen, research-only ETF factor deconfounding candidates."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Any

from app.services.etf_research_evidence import stable_contract_hash

DECONFOUNDING_BASELINE = "daily_reconstructable_baseline"
DECONFOUNDING_RESIDUAL_MOMENTUM_BREADTH = "residual_momentum_breadth_v1"
DECONFOUNDING_PIT_FLOW_BREADTH = "pit_flow_constituent_breadth_v1"
DECONFOUNDING_REGISTRY_ID = "etf_factor_deconfounding_candidates_v1"
DECONFOUNDING_EVIDENCE_SCHEMA = "etf_factor_deconfounding_evidence_v1"
MAX_DECONFOUNDING_CANDIDATES = 3
MINIMUM_DECONFOUNDING_COMMON_PEERS = 20
MINIMUM_DECONFOUNDING_HISTORY_SESSIONS = 61


class DeconfoundingContractError(ValueError):
    pass


@dataclass(frozen=True)
class FrozenDeconfoundingCandidate:
    candidate_id: str
    role: str
    formula: str
    direction: str
    transform: str
    required_inputs: tuple[str, ...]
    input_weights: tuple[tuple[str, float], ...]
    minimum_common_peer_count: int
    required_history_sessions: int
    missing_value_rule: str
    manifest_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("manifest_hash")
        return payload


@dataclass(frozen=True)
class FrozenDeconfoundingRegistry:
    candidates: tuple[FrozenDeconfoundingCandidate, ...]
    registry_hash: str

    @property
    def by_id(self) -> dict[str, FrozenDeconfoundingCandidate]:
        return {candidate.candidate_id: candidate for candidate in self.candidates}


@dataclass(frozen=True)
class DeconfoundingCandidateObservation:
    signal_date: date
    data_cutoff: datetime
    asset_code: str
    candidate_id: str
    candidate_manifest_hash: str
    registry_hash: str
    score: float | None
    availability: str
    unavailable_reasons: tuple[str, ...]
    input_hash: str
    observation_hash: str
    research_only: bool = True
    production_mutation_allowed: bool = False

    def canonical_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("observation_hash")
        return payload


def _candidate(
    candidate_id: str,
    *,
    role: str,
    formula: str,
    required_inputs: tuple[str, ...],
    input_weights: tuple[tuple[str, float], ...],
) -> FrozenDeconfoundingCandidate:
    draft = FrozenDeconfoundingCandidate(
        candidate_id=candidate_id,
        role=role,
        formula=formula,
        direction="higher_is_better",
        transform="identity_bounded_0_100",
        required_inputs=required_inputs,
        input_weights=input_weights,
        minimum_common_peer_count=MINIMUM_DECONFOUNDING_COMMON_PEERS,
        required_history_sessions=MINIMUM_DECONFOUNDING_HISTORY_SESSIONS,
        missing_value_rule="unavailable_without_fallback",
        manifest_hash="pending",
    )
    return replace(
        draft,
        manifest_hash=stable_contract_hash(draft.canonical_payload()),
    )


FROZEN_DECONFOUNDING_CANDIDATES = (
    _candidate(
        DECONFOUNDING_BASELINE,
        role="control",
        formula="daily_reconstructable_score",
        required_inputs=("daily_reconstructable_score",),
        input_weights=(("daily_reconstructable_score", 1.0),),
    ),
    _candidate(
        DECONFOUNDING_RESIDUAL_MOMENTUM_BREADTH,
        role="candidate",
        formula="0.50*residual_momentum_score+0.50*constituent_breadth_score",
        required_inputs=(
            "residual_momentum_score",
            "constituent_breadth_score",
        ),
        input_weights=(
            ("residual_momentum_score", 0.5),
            ("constituent_breadth_score", 0.5),
        ),
    ),
    _candidate(
        DECONFOUNDING_PIT_FLOW_BREADTH,
        role="candidate",
        formula="0.50*pit_share_flow_score+0.50*constituent_breadth_score",
        required_inputs=("pit_share_flow_score", "constituent_breadth_score"),
        input_weights=(
            ("pit_share_flow_score", 0.5),
            ("constituent_breadth_score", 0.5),
        ),
    ),
)
_FROZEN_BY_ID = {candidate.candidate_id: candidate for candidate in FROZEN_DECONFOUNDING_CANDIDATES}
_FROZEN_ORDER = {
    candidate.candidate_id: index for index, candidate in enumerate(FROZEN_DECONFOUNDING_CANDIDATES)
}


def freeze_deconfounding_registry(
    candidates: Iterable[FrozenDeconfoundingCandidate],
) -> FrozenDeconfoundingRegistry:
    values = tuple(candidates)
    if not values:
        raise DeconfoundingContractError("deconfounding registry cannot be empty")
    if len(values) > MAX_DECONFOUNDING_CANDIDATES:
        raise DeconfoundingContractError("deconfounding registry allows at most three candidates")
    ids = tuple(candidate.candidate_id for candidate in values)
    if len(ids) != len(set(ids)):
        raise DeconfoundingContractError("deconfounding registry contains duplicate candidates")
    for candidate in values:
        if _FROZEN_BY_ID.get(candidate.candidate_id) != candidate:
            raise DeconfoundingContractError(
                "candidate does not match its frozen deconfounding definition"
            )
    ordered = tuple(sorted(values, key=lambda item: _FROZEN_ORDER[item.candidate_id]))
    registry_hash = stable_contract_hash(
        {
            "registry_id": DECONFOUNDING_REGISTRY_ID,
            "max_candidates": MAX_DECONFOUNDING_CANDIDATES,
            "research_only": True,
            "production_mutation_allowed": False,
            "candidates": tuple(
                (candidate.candidate_id, candidate.manifest_hash) for candidate in ordered
            ),
        }
    )
    return FrozenDeconfoundingRegistry(
        candidates=ordered,
        registry_hash=registry_hash,
    )


FROZEN_DECONFOUNDING_REGISTRY = freeze_deconfounding_registry(FROZEN_DECONFOUNDING_CANDIDATES)


def _finite_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 100.0:
        return None
    return numeric


def evaluate_deconfounding_candidates(
    *,
    signal_date: date,
    data_cutoff: datetime,
    asset_code: str,
    eligible_history_sessions: int,
    common_peer_count: int,
    input_values: Mapping[str, object],
    input_decision_eligible: Mapping[str, bool],
    input_visible_at_cutoff: Mapping[str, bool],
    registry: FrozenDeconfoundingRegistry = FROZEN_DECONFOUNDING_REGISTRY,
) -> tuple[DeconfoundingCandidateObservation, ...]:
    if not asset_code.strip():
        raise DeconfoundingContractError("asset code is required")
    if common_peer_count < 0 or eligible_history_sessions < 0:
        raise DeconfoundingContractError("history and peer counts must be non-negative")
    frozen = freeze_deconfounding_registry(registry.candidates)
    if frozen.registry_hash != registry.registry_hash:
        raise DeconfoundingContractError("deconfounding registry hash is invalid")
    observations: list[DeconfoundingCandidateObservation] = []
    for candidate in registry.candidates:
        reasons: list[str] = []
        if eligible_history_sessions < candidate.required_history_sessions:
            reasons.append("insufficient_61_eligible_adjusted_sessions")
        if common_peer_count < candidate.minimum_common_peer_count:
            reasons.append("insufficient_common_peer_support")
        finite_inputs: dict[str, float] = {}
        for input_id in candidate.required_inputs:
            if input_visible_at_cutoff.get(input_id) is not True:
                reasons.append(f"{input_id}:not_visible_at_cutoff")
                continue
            if input_decision_eligible.get(input_id) is not True:
                reasons.append(f"{input_id}:decision_ineligible")
                continue
            value = _finite_score(input_values.get(input_id))
            if value is None:
                reasons.append(f"{input_id}:missing_non_finite_or_out_of_range")
                continue
            finite_inputs[input_id] = value
        unavailable_reasons = tuple(sorted(set(reasons)))
        score = None
        if not unavailable_reasons:
            score = round(
                sum(
                    finite_inputs[input_id] * weight for input_id, weight in candidate.input_weights
                ),
                8,
            )
        input_hash = stable_contract_hash(
            {
                "signal_date": signal_date,
                "data_cutoff": data_cutoff,
                "asset_code": asset_code,
                "candidate_id": candidate.candidate_id,
                "eligible_history_sessions": eligible_history_sessions,
                "common_peer_count": common_peer_count,
                "inputs": {
                    input_id: {
                        "value": input_values.get(input_id),
                        "decision_eligible": input_decision_eligible.get(input_id),
                        "visible_at_cutoff": input_visible_at_cutoff.get(input_id),
                    }
                    for input_id in candidate.required_inputs
                },
            }
        )
        draft = DeconfoundingCandidateObservation(
            signal_date=signal_date,
            data_cutoff=data_cutoff,
            asset_code=asset_code,
            candidate_id=candidate.candidate_id,
            candidate_manifest_hash=candidate.manifest_hash,
            registry_hash=registry.registry_hash,
            score=score,
            availability="available" if score is not None else "unavailable",
            unavailable_reasons=unavailable_reasons,
            input_hash=input_hash,
            observation_hash="pending",
        )
        observations.append(
            replace(
                draft,
                observation_hash=stable_contract_hash(draft.canonical_payload()),
            )
        )
    return tuple(observations)


def build_deconfounding_evidence(
    observations: Sequence[DeconfoundingCandidateObservation] = (),
    *,
    redundancy_diagnostics: Mapping[str, Any] | None = None,
    registry: FrozenDeconfoundingRegistry = FROZEN_DECONFOUNDING_REGISTRY,
) -> dict[str, Any]:
    frozen = freeze_deconfounding_registry(registry.candidates)
    if frozen.registry_hash != registry.registry_hash:
        raise DeconfoundingContractError("deconfounding registry hash is invalid")
    seen: set[tuple[date, str, str]] = set()
    by_candidate: dict[str, list[DeconfoundingCandidateObservation]] = {
        candidate.candidate_id: [] for candidate in registry.candidates
    }
    for observation in observations:
        candidate = registry.by_id.get(observation.candidate_id)
        key = (
            observation.signal_date,
            observation.asset_code,
            observation.candidate_id,
        )
        if (
            candidate is None
            or observation.candidate_manifest_hash != candidate.manifest_hash
            or observation.registry_hash != registry.registry_hash
            or observation.observation_hash != stable_contract_hash(observation.canonical_payload())
            or observation.production_mutation_allowed
            or not observation.research_only
        ):
            raise DeconfoundingContractError("deconfounding observation identity is invalid")
        if key in seen:
            raise DeconfoundingContractError("duplicate deconfounding candidate observation")
        seen.add(key)
        by_candidate[observation.candidate_id].append(observation)
    if (
        redundancy_diagnostics is not None
        and redundancy_diagnostics.get("production_mutation_allowed") is not False
    ):
        raise DeconfoundingContractError("deconfounding diagnostics must remain research-only")
    candidate_summaries: list[dict[str, Any]] = []
    available_observation_count = 0
    for candidate in registry.candidates:
        rows = sorted(
            by_candidate[candidate.candidate_id],
            key=lambda item: (item.signal_date, item.asset_code),
        )
        reasons = Counter(reason for row in rows for reason in row.unavailable_reasons)
        available_count = sum(row.availability == "available" for row in rows)
        available_observation_count += available_count
        candidate_summaries.append(
            {
                "candidate_id": candidate.candidate_id,
                "candidate_manifest_hash": candidate.manifest_hash,
                "role": candidate.role,
                "observation_count": len(rows),
                "available_count": available_count,
                "unavailable_count": sum(row.availability != "available" for row in rows),
                "unavailable_reason_counts": dict(sorted(reasons.items())),
                "observation_chain_hash": stable_contract_hash(
                    tuple(row.observation_hash for row in rows)
                ),
            }
        )
    payload = {
        "schema_version": DECONFOUNDING_EVIDENCE_SCHEMA,
        "registry_id": DECONFOUNDING_REGISTRY_ID,
        "registry_hash": registry.registry_hash,
        "candidate_count": len(registry.candidates),
        "candidate_ids": [candidate.candidate_id for candidate in registry.candidates],
        "candidate_summaries": candidate_summaries,
        "redundancy_diagnostics": dict(redundancy_diagnostics or {}),
        "state": "available" if available_observation_count else "insufficient_data",
        "research_only": True,
        "production_mutation_allowed": False,
    }
    return {**payload, "evidence_hash": stable_contract_hash(payload)}


def validate_deconfounding_evidence(
    value: Mapping[str, Any],
    *,
    registry: FrozenDeconfoundingRegistry = FROZEN_DECONFOUNDING_REGISTRY,
) -> dict[str, Any]:
    payload = dict(value)
    evidence_hash = payload.pop("evidence_hash", None)
    candidate_ids = payload.get("candidate_ids")
    expected_ids = [candidate.candidate_id for candidate in registry.candidates]
    if (
        value.get("schema_version") != DECONFOUNDING_EVIDENCE_SCHEMA
        or value.get("registry_hash") != registry.registry_hash
        or value.get("candidate_count") != len(registry.candidates)
        or candidate_ids != expected_ids
        or len(expected_ids) > MAX_DECONFOUNDING_CANDIDATES
        or value.get("research_only") is not True
        or value.get("production_mutation_allowed") is not False
        or evidence_hash != stable_contract_hash(payload)
    ):
        raise DeconfoundingContractError("deconfounding evidence contract or hash is invalid")
    return dict(value)
