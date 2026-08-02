"""Fail-closed evidence adapter for current-vintage historical leader proxies.

This module accepts an already materialized, bounded replay artifact.  It does
not load market data, reconstruct historical membership, or grant any credit to
the factual point-in-time promotion gates.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_evidence import (
    FactorEvidenceContractError,
    FactorEvidencePayload,
    FactorEvidencePromotion,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    LEADER_HYPOTHESIS_REGISTRY,
)

LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY = (
    "leader_tactics_historical_proxy_v1"
)
LEADER_HISTORICAL_PROXY_SCHEMA_VERSION = (
    "etf_leader_tactics_historical_proxy_evidence_v1"
)
LEADER_HISTORICAL_PROXY_SOURCE_SCHEMA_VERSION = (
    "leader_source_snapshot_historical_proxy_v1"
)
LEADER_HISTORICAL_PROXY_REPORT_KIND = "historical_proxy"
LEADER_HISTORICAL_PROXY_EVIDENCE_MODE = "source_snapshot_historical_proxy"
LEADER_HISTORICAL_PROXY_UNAVAILABLE = "leader_historical_proxy_not_materialized"
LEADER_HISTORICAL_PROXY_INCOMPATIBLE = "leader_historical_proxy_incompatible"
LEADER_HISTORICAL_PROXY_NOT_PIT = "historical_proxy_not_point_in_time"

_ALLOWED_CANDIDATE_IDS = {
    "leader_breakout_proxy_v1",
    "former_leader_repair_proxy_v1",
}
_UNCLASSIFIED_PEER_GROUPS = {
    "unknown",
    "other",
    "unclassified",
    "未知",
    "其他",
}
_REQUIRED_LIMITATIONS = {
    "membership and peer taxonomy use the sealed source snapshot, not factual historical receipts",
    "historical proxy dates and candidates cannot count toward PIT promotion gates",
    "research only; no ranking, position, alert, email, or execution mutation",
}


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FactorEvidenceContractError(f"{field} must be an integer")
    if value < 0:
        raise FactorEvidenceContractError(f"{field} must be non-negative")
    return value


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FactorEvidenceContractError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise FactorEvidenceContractError(f"{field} must be finite")
    return parsed


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise FactorEvidenceContractError(f"{field} must be a sha256 hash")
    try:
        int(value, 16)
    except ValueError as exc:
        raise FactorEvidenceContractError(
            f"{field} must be a sha256 hash"
        ) from exc
    return value


def _validate_candidate(raw: object) -> dict[str, Any]:
    candidate = _mapping(raw)
    candidate_id = candidate.get("candidate_id")
    if candidate_id not in _ALLOWED_CANDIDATE_IDS:
        raise FactorEvidenceContractError(
            "historical proxy candidate identity is incompatible"
        )
    asset_code = str(candidate.get("asset_code") or "").strip()
    if not asset_code:
        raise FactorEvidenceContractError(
            "historical proxy candidate asset code is required"
        )
    peer_group = str(candidate.get("peer_group") or "").strip()
    if not peer_group or peer_group.lower() in _UNCLASSIFIED_PEER_GROUPS:
        raise FactorEvidenceContractError(
            "historical proxy candidates require a classified peer group"
        )
    score = _finite_number(candidate.get("score"), field="candidate.score")
    if not 0 <= score <= 1:
        raise FactorEvidenceContractError(
            "historical proxy candidate score must be between zero and one"
        )
    baseline_score = candidate.get("baseline_score")
    if baseline_score is not None:
        _finite_number(baseline_score, field="candidate.baseline_score")
    for key, value in _mapping(candidate.get("components")).items():
        if value is not None:
            _finite_number(value, field=f"candidate.components.{key}")
    feature_hash = _require_sha256(
        candidate.get("feature_hash"), field="candidate.feature_hash"
    )
    canonical_candidate = {
        key: value for key, value in candidate.items() if key != "feature_hash"
    }
    if feature_hash != stable_contract_hash(canonical_candidate):
        raise FactorEvidenceContractError(
            "historical proxy candidate feature hash does not match payload"
        )
    return candidate


def build_leader_historical_proxy_evidence(
    source: Mapping[str, Any],
    *,
    code_version: str,
) -> FactorEvidencePayload:
    """Validate a bounded replay artifact and wrap it as immutable evidence."""

    payload = dict(source)
    if payload.get("schema_version") != LEADER_HISTORICAL_PROXY_SOURCE_SCHEMA_VERSION:
        raise FactorEvidenceContractError(
            "historical proxy source schema is incompatible"
        )
    if payload.get("status") != "complete":
        raise FactorEvidenceContractError(
            "historical proxy source must be complete"
        )
    if (
        payload.get("ranking_source_kind") != "research_replay"
        or payload.get("evidence_mode") != LEADER_HISTORICAL_PROXY_EVIDENCE_MODE
        or payload.get("production_mutation_allowed") is not False
    ):
        raise FactorEvidenceContractError(
            "historical proxy provenance is incompatible"
        )
    if payload.get("membership_mode") != (
        "sealed_source_snapshot_current_vintage_proxy"
    ):
        raise FactorEvidenceContractError(
            "historical proxy membership mode is incompatible"
        )
    if payload.get("price_basis") != "total_return_adjusted":
        raise FactorEvidenceContractError(
            "historical proxy requires total-return-adjusted prices"
        )
    forbidden = {str(value).lower() for value in _sequence(payload.get("forbidden_providers"))}
    if not {"sina", "efinance"}.issubset(forbidden):
        raise FactorEvidenceContractError(
            "historical proxy must explicitly forbid raw fallback providers"
        )
    signal_date = str(payload.get("signal_date") or "").strip()
    signal_run_id = _non_negative_int(
        payload.get("signal_run_id"), field="signal_run_id"
    )
    history_sessions = _non_negative_int(
        payload.get("history_sessions"), field="history_sessions"
    )
    if not signal_date or signal_run_id == 0 or history_sessions < 120:
        raise FactorEvidenceContractError(
            "historical proxy source identity or history depth is invalid"
        )
    source_contract = {
        "schema_version": payload["schema_version"],
        "signal_run_id": signal_run_id,
        "signal_date": signal_date,
        "history_sessions": history_sessions,
        "membership_mode": payload["membership_mode"],
        "price_basis": payload["price_basis"],
        "forbidden_providers": _sequence(payload.get("forbidden_providers")),
        "production_mutation_allowed": False,
    }
    contract_hash = _require_sha256(
        payload.get("contract_hash"), field="contract_hash"
    )
    if contract_hash != stable_contract_hash(source_contract):
        raise FactorEvidenceContractError(
            "historical proxy contract hash does not match source contract"
        )
    ranking_contract_hash = _require_sha256(
        payload.get("source_ranking_contract_hash"),
        field="source_ranking_contract_hash",
    )
    input_snapshot_hash = _require_sha256(
        payload.get("source_input_snapshot_hash"),
        field="source_input_snapshot_hash",
    )
    candidates = [_validate_candidate(item) for item in _sequence(payload.get("candidates"))]
    if len(candidates) > 40:
        raise FactorEvidenceContractError(
            "historical proxy candidate display is bounded to 40 rows"
        )
    identities = {
        (str(item["candidate_id"]), str(item["asset_code"]))
        for item in candidates
    }
    if len(identities) != len(candidates):
        raise FactorEvidenceContractError(
            "historical proxy candidate identities must be unique"
        )
    expected_counts = Counter(str(item["candidate_id"]) for item in candidates)
    candidate_counts = _mapping(payload.get("candidate_counts"))
    for candidate_id in _ALLOWED_CANDIDATE_IDS:
        declared = _non_negative_int(
            candidate_counts.get(candidate_id, 0),
            field=f"candidate_counts.{candidate_id}",
        )
        if declared != expected_counts.get(candidate_id, 0):
            raise FactorEvidenceContractError(
                "historical proxy candidate counts do not match candidates"
            )
    input_asset_count = _non_negative_int(
        payload.get("input_asset_count"), field="input_asset_count"
    )
    excluded_asset_count = _non_negative_int(
        payload.get("excluded_asset_count"), field="excluded_asset_count"
    )
    exclusion_counts = {
        str(reason): _non_negative_int(count, field=f"exclusion_counts.{reason}")
        for reason, count in _mapping(payload.get("exclusion_counts")).items()
    }
    limitations = tuple(str(item) for item in _sequence(payload.get("limitations")))
    if not _REQUIRED_LIMITATIONS.issubset(set(limitations)):
        raise FactorEvidenceContractError(
            "historical proxy limitations are incomplete"
        )
    if not code_version.strip():
        raise FactorEvidenceContractError(
            "historical proxy code version is required"
        )
    manifest_hash = stable_contract_hash(
        {
            "experiment_family": LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
            "schema_version": LEADER_HISTORICAL_PROXY_SCHEMA_VERSION,
            "contract_hash": contract_hash,
            "source_ranking_contract_hash": ranking_contract_hash,
            "source_input_snapshot_hash": input_snapshot_hash,
            "signal_date": signal_date,
            "code_version": code_version,
        }
    )
    total_source_assets = input_asset_count + excluded_asset_count
    report = {
        "schema_version": LEADER_HISTORICAL_PROXY_SCHEMA_VERSION,
        "report_kind": LEADER_HISTORICAL_PROXY_REPORT_KIND,
        "experiment_family": LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
        "manifest_hash": manifest_hash,
        "status": "insufficient_data",
        "unavailable_reason": LEADER_HISTORICAL_PROXY_NOT_PIT,
        "ranking_source_kind": "research_replay",
        "evidence_mode": LEADER_HISTORICAL_PROXY_EVIDENCE_MODE,
        "policy_mode": "none",
        "notification_provenance": "none",
        "execution_provenance": "none",
        "signal_date": signal_date,
        "signal_run_id": signal_run_id,
        "history_sessions": history_sessions,
        "membership_mode": payload["membership_mode"],
        "price_basis": payload["price_basis"],
        "forbidden_providers": sorted(forbidden),
        "contract_hash": contract_hash,
        "source_ranking_contract_hash": ranking_contract_hash,
        "source_input_snapshot_hash": input_snapshot_hash,
        "coverage": {
            "source_ranked_asset_count": total_source_assets,
            "eligible_history_asset_count": input_asset_count,
            "eligible_history_ratio": (
                input_asset_count / total_source_assets
                if total_source_assets
                else 0.0
            ),
            "history_sessions": history_sessions,
        },
        "exclusion_counts": exclusion_counts,
        "candidate_counts": {
            candidate_id: expected_counts.get(candidate_id, 0)
            for candidate_id in sorted(_ALLOWED_CANDIDATE_IDS)
        },
        "candidates": candidates,
        "promotion_gate_credit": {
            "eligible_pit_sessions": 0,
            "independent_primary_dates": 0,
            "walk_forward_folds": 0,
        },
        "limitations": list(limitations),
        "research_only": True,
        "production_mutation_allowed": False,
    }
    return FactorEvidencePayload(
        manifest_hash=manifest_hash,
        ranking_contract_hash=ranking_contract_hash,
        code_version=code_version,
        samples=tuple(candidates),
        aggregates={
            "status": "insufficient_data",
            "coverage": report["coverage"],
            "candidate_counts": report["candidate_counts"],
            "promotion_gate_credit": report["promotion_gate_credit"],
        },
        exclusions=tuple(
            {"reason": reason, "count": count}
            for reason, count in sorted(exclusion_counts.items())
        ),
        intervals={
            "multiplicity": {
                "method": "holm_bonferroni",
                "raw_primary_p_values": [1.0],
                "adjusted_primary_p_values": [1.0],
            },
            "interpretation": "sentinel_no_primary_inference",
        },
        split_reports={
            "historical_proxy_only": True,
            "holdout_consumed": False,
        },
        costs={},
        limitations=limitations,
        report=report,
        promotion=FactorEvidencePromotion(
            state="insufficient_data",
            passed=False,
            failed_gates=(
                "historical_membership_not_point_in_time",
                "forward_outcomes_not_evaluated",
            ),
            endpoint="none_historical_proxy_screen",
        ),
        experiment_family=LEADER_HISTORICAL_PROXY_EXPERIMENT_FAMILY,
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
    )
